"""Catch stale evidence, accidental writes and invented platform outcomes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import imageio_ffmpeg
import pytest

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def publication(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    video = source / "video.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=red:s=180x320:r=10:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    creative = source / "creative.json"
    write(creative, {"lines": [{"zh":"上脸试试。", "visible_zh":"Color Bloom 液体腮红"},
                               {"zh":"你选哪一边？", "visible_zh":"你选哪一边？"}]})
    approval = source / "zh-approval.json"
    write(approval, {"status":"approved", "version_sha256":digest(creative), "user_reply":"可以"})
    loc = source / "localization.json"
    write(loc, {"market":"BR", "locale":"pt-BR", "creative_sha256":digest(creative),
                "segments":[{"line":1, "text":"Vou testar.", "visible_text":"Blush líquido Color Bloom"},
                            {"line":2, "text":"Qual lado você prefere?", "visible_text":"Qual lado você prefere?"}]})
    audit = source / "audit.json"
    write(audit, {"status":"passed_independent_internal_text_review", "market":"BR", "locale":"pt-BR",
                  "creative_sha256":digest(creative), "localization_sha256":digest(loc),
                  "zh_approval_sha256":digest(approval), "material_changes_to_approved_meaning":[]})
    srt = source / "video.srt"; srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nVou testar.\n", encoding="utf-8")
    execution = source / "execution.json"
    write(execution, {"job_id":"test-job", "output":str(video), "srt":str(srt), "duration":1,
                      "localization_sha256":digest(loc), "subtitle_cues":[[0,1,"Vou testar."]],
                      "bound_files":[{"path":str(p),"sha256":digest(p)} for p in (creative,approval,loc,audit)]})
    qa = source / "qa.json"
    write(qa, {"status":"technical_checks_passed_pending_visual_review", "video_sha256":digest(video),
               "execution_sha256":digest(execution), "video":{"duration_seconds":1,"width":180,"height":320,"full_decode":"passed"},
               "scope":{"commercial_license":"unverified", "final_user_acceptance":"pending"}})
    request = tmp_path / "request.json"
    write(request, {"schema_version":1, "market":"BR", "locale":"pt-BR", "product_name_zh":"Color Bloom 液体腮红",
                    "execution":str(execution), "creative":str(creative), "zh_approval":str(approval),
                    "localization":str(loc), "localization_audit":str(audit), "qa":str(qa),
                    "title_refs":[{"line":1,"field":"visible_text"},{"line":2,"field":"visible_text"}],
                    "hashtag_refs":[{"line":1,"field":"visible_text","tokens":[2,3]},
                                    {"line":1,"field":"visible_text","tokens":[0,1]}],
                    "cover_seconds":0.5, "delivery_method":"local_package", "account":None, "product_id":None})
    return request, tmp_path / "package", source


def test_real_package_preserves_video_and_keeps_platform_states_unexecuted(publication):
    from workflow.publishing import prepare_publish
    request, output, source = publication
    result = prepare_publish(request, output, workspace=ROOT)
    manifest = json.loads((output / "manifest.json").read_text())
    assert digest(output / "video.mp4") == digest(source / "video.mp4")
    assert (output / "cover.jpg").stat().st_size > 100
    assert manifest["state"] == "LOCAL_PREPARED"
    assert manifest["platform"] == {"upload":"not_attempted", "inbox":"not_verified", "draft":"not_verified",
                                    "publication":"not_attempted", "publish_id":None, "content_id":None, "url":None}
    assert manifest["product"]["product_id"] is None
    assert manifest["eligibility"]["ready_for_upload"] is False
    assert result["reused"] is False
    assert all(Path(p).is_absolute() for p in result["artifacts"].values())
    copy = json.loads((output / "internal" / "publish-copy.json").read_text())
    assert copy["title"] == "Blush líquido Color Bloom | Qual lado você prefere?"
    assert copy["hashtags"] == ["#ColorBloom", "#BlushLiquido"]
    preview = (output / "中文发布预览.md").read_text()
    assert "Color Bloom 液体腮红｜你选哪一边？" in preview
    assert "Qual lado" not in preview
    assert "Blush líquido" not in preview
    assert "成片最终验收待完成" in preview
    assert "员工后续手工操作" in preview
    assert "明确单条上传授权" not in preview
    assert "验证账号的待编辑入口及草稿保存能力" not in preview
    assert "账号及其所在市场、执行设备和已登录的可用入口" not in preview
    assert "App收件箱待编辑" not in preview
    assert "平台草稿" not in preview


@pytest.mark.parametrize("change, expected", [
    ("approval", "中文批准"), ("localization", "本地化"), ("audit", "审核"),
    ("video", "成片"), ("execution", "执行清单"), ("market", "市场"),
    ("copy", "文案"), ("cover", "封面"), ("subtitle", "字幕")])
def test_invalid_inputs_fail_before_creating_package(publication, change, expected):
    from workflow.publishing import prepare_publish
    request, output, source = publication
    data = json.loads(request.read_text())
    if change == "approval":
        write(source / "zh-approval.json", {"status":"approved", "version_sha256":"stale"})
    elif change == "localization":
        (source / "localization.json").write_text("{}")
    elif change == "audit":
        audit=json.loads((source / "audit.json").read_text()); audit["material_changes_to_approved_meaning"]=["new promise"]
        write(source / "audit.json",audit)
    elif change == "video":
        (source / "video.mp4").write_bytes(b"changed")
    elif change == "execution":
        execution=json.loads((source / "execution.json").read_text()); execution["duration"]=2
        write(source / "execution.json", execution)
    elif change == "market": data["market"]="MX"; data["locale"]="es-MX"
    elif change == "copy": data["title"]="去皱一整天"
    elif change == "cover": data["cover_seconds"]=1.5
    elif change == "subtitle": (source / "video.srt").write_text("wrong words")
    write(request,data)
    with pytest.raises(ValueError, match=expected): prepare_publish(request,output,workspace=ROOT)
    assert not output.exists()


def test_retry_reuses_and_changed_inputs_or_corrupt_outputs_require_new_directory(publication):
    from workflow.publishing import prepare_publish
    request,output,_=publication
    prepare_publish(request,output,workspace=ROOT)
    before=(output/"video.mp4").stat().st_mtime_ns
    assert prepare_publish(request,output,workspace=ROOT)["reused"]
    assert (output/"video.mp4").stat().st_mtime_ns == before
    data=json.loads(request.read_text()); data["cover_seconds"]=0.6; write(request,data)
    with pytest.raises(ValueError,match="新编号"):prepare_publish(request,output,workspace=ROOT)
    data["cover_seconds"]=0.5;write(request,data)
    (output/"internal"/"caption.txt").write_text("changed")
    with pytest.raises(ValueError,match="损坏"):prepare_publish(request,output,workspace=ROOT)


@pytest.mark.parametrize("method", ["tiktok_inbox", "platform_draft", "direct_post"])
def test_external_method_is_rejected_without_platform_result(publication, method):
    from workflow.publishing import prepare_publish
    request,output,_=publication
    data=json.loads(request.read_text());data["delivery_method"]=method;write(request,data)
    with pytest.raises(ValueError,match="仅支持本地"):prepare_publish(request,output,workspace=ROOT)
    assert not output.exists()


def test_output_cannot_cover_source_directory(publication):
    from workflow.publishing import prepare_publish
    request,_,source=publication
    old=digest(source/"video.mp4")
    with pytest.raises(ValueError):prepare_publish(request,source,workspace=ROOT)
    assert digest(source/"video.mp4")==old


def test_cli_runs_from_other_directory_and_only_returns_chinese_preview(publication,tmp_path):
    request,output,_=publication
    completed=subprocess.run([sys.executable,str(ROOT/"scripts/prepare_publish.py"),str(request),"--output",str(output)],
                             cwd=tmp_path,capture_output=True,text=True)
    assert completed.returncode==0, completed.stderr
    result=json.loads(completed.stdout)
    assert result["state"]=="LOCAL_PREPARED"
    assert "Qual lado" not in completed.stdout
    assert Path(result["artifacts"]["preview"]).exists()


def test_lock_prevents_second_writer_without_removing_first_writers_lock(publication):
    from workflow.publishing import prepare_publish
    request,output,_=publication
    lock=output.with_name(output.name+".prepare.lock");lock.write_text("existing process")
    with pytest.raises(ValueError,match="已有锁"):prepare_publish(request,output,workspace=ROOT)
    assert lock.read_text()=="existing process"
    assert not output.exists()


def test_cover_failure_leaves_retryable_record_and_no_partial_package(publication,monkeypatch):
    import workflow.publishing.prepare as module
    request,output,source=publication
    original=digest(source/"video.mp4")
    real_cover=module._cover
    def fail(*args):raise ValueError("封面提取临时失败")
    monkeypatch.setattr(module,"_cover",fail)
    with pytest.raises(ValueError,match="封面提取临时失败"):module.prepare_publish(request,output,workspace=ROOT)
    assert not output.exists()
    assert not list(output.parent.glob(output.name+".partial-*"))
    assert not output.with_name(output.name+".prepare.lock").exists()
    failure=json.loads(output.with_name(output.name+".prepare-failure.json").read_text())
    assert failure["state"]=="LOCAL_PREPARE_FAILED"
    assert failure["platform_action"] is False
    assert digest(source/"video.mp4")==original
    monkeypatch.setattr(module,"_cover",real_cover)
    assert module.prepare_publish(request,output,workspace=ROOT)["state"]=="LOCAL_PREPARED"


def test_changed_request_during_preparation_cannot_bind_old_preview_to_new_request(publication,monkeypatch):
    import workflow.publishing.prepare as module
    request,output,_=publication
    real_validate=module.validate_request
    def change_after_validation(*args):
        result=real_validate(*args)
        data=json.loads(request.read_text());data["cover_seconds"]=0.75;write(request,data)
        return result
    monkeypatch.setattr(module,"validate_request",change_after_validation)
    with pytest.raises(ValueError,match="请求输入变化"):module.prepare_publish(request,output,workspace=ROOT)
    assert not output.exists()


def test_manifest_tampering_is_detected_on_resume(publication):
    from workflow.publishing import prepare_publish
    request,output,_=publication
    prepare_publish(request,output,workspace=ROOT)
    manifest=json.loads((output/"manifest.json").read_text());manifest["platform"]["draft"]="saved"
    write(output/"manifest.json",manifest)
    with pytest.raises(ValueError,match="损坏"):prepare_publish(request,output,workspace=ROOT)
