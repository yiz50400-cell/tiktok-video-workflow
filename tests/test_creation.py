import sys
"""Creation plans bind real media evidence to immutable draft versions."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest

from workflow.analysis import analyze_video, save_observations
from workflow.creation import (audit_version, create_batch_contract, create_version,
                               show_version)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def sample(tmp_path):
    video = tmp_path / "source.mp4"
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=12", "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video),
    ], check=True, capture_output=True)
    analysis_dir = tmp_path / "analysis"
    manifest = analyze_video(video, analysis_dir, interval=.4, max_frames=10)
    observations = {
        "source_sha256": manifest["source"]["sha256"],
        "observer": "test visual observer", "observed_at": "2026-10-09T15:00:00+08:00",
        "segments": [{
            "kind": "action", "evidence_status": "observed",
            "start_seconds": 0, "end_seconds": 2,
            "description": "moving test pattern",
            "frame_ids": [frame["id"] for frame in manifest["frames"]],
        }],
        "known_facts": [], "popularity_hypotheses": [], "unknowns": [], "risks": [],
    }
    supplied = tmp_path / "observations-input.json"
    supplied.write_text(json.dumps(observations), encoding="utf-8")
    save_observations(analysis_dir / "manifest.json", supplied)
    first_frame = next(frame["id"] for frame in manifest["frames"]
                       if frame["source_time_seconds"] < 1)
    second_frame = next(frame["id"] for frame in manifest["frames"]
                        if frame["source_time_seconds"] >= 1)
    spec = {
        "job_id": "job_test", "product_series": "Test blush series",
        "analysis_path": str((analysis_dir / "analysis.json").resolve()),
        "edit_video_path": str(video.resolve()),
        "transcode_verification_path": None,
        "author": "draft writer",
        "shots": [
            {"role": "action", "source_start_seconds": 0, "source_end_seconds": 1,
             "description": "first action", "evidence_segment_index": 0,
             "evidence_frame_ids": [first_frame]},
            {"role": "result", "source_start_seconds": 1, "source_end_seconds": 2,
             "description": "result view", "evidence_segment_index": 0,
             "evidence_frame_ids": [second_frame]},
        ],
        "script_lines": [{"start_seconds": 0, "end_seconds": 2,
                          "text": "先点涂，再慢慢晕开。"}],
        "pending_configuration": ["product card path"],
    }
    return video, manifest, spec


def test_create_binds_shots_to_evidence_and_keeps_media_unchanged(sample, tmp_path):
    video, manifest, spec = sample
    before = digest(video)
    result = create_version(spec, tmp_path / "creation")
    saved = json.loads(Path(result["version_path"]).read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert saved["parent_version"] is None
    assert saved["total_seconds"] == 2
    assert [(shot["timeline_start_seconds"], shot["timeline_end_seconds"])
            for shot in saved["shots"]] == [(0, 1), (1, 2)]
    assert saved["analysis"]["source_sha256"] == manifest["source"]["sha256"]
    assert saved["edit_video"]["sha256"] == before
    assert saved["media_relationship"]["kind"] == "same_file"
    assert saved["human_approval"]["status"] == "pending_user"
    assert saved["copy_audit_status"] == "pending"
    assert digest(video) == before


@pytest.mark.parametrize("change", [
    lambda spec: spec["shots"][0].update(source_end_seconds=9),
    lambda spec: spec["shots"][0].update(evidence_frame_ids=["f9999"]),
    lambda spec: spec["shots"][0].update(evidence_segment_index=99),
    lambda spec: spec["script_lines"][0].update(end_seconds=1),
])
def test_invalid_time_or_evidence_cannot_create_version(sample, tmp_path, change):
    _, _, original = sample
    spec = copy.deepcopy(original)
    change(spec)
    with pytest.raises(ValueError):
        create_version(spec, tmp_path / "creation")
    assert not list((tmp_path / "creation").glob("v*.json"))


def test_verified_transcode_uses_distinct_edit_and_analysis_hashes(sample, tmp_path):
    source, manifest, spec = sample
    edit = tmp_path / "edit.mp4"
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-c:v", "mpeg4", "-q:v", "4", "-an", "-y", str(edit),
    ], check=True, capture_output=True)
    verification = tmp_path / "verification.json"
    verification.write_text(json.dumps({
        "raw_file": source.name,
        "source_url": manifest["source"]["source_url"],
        "method": "transcode",
        "results": {edit.stem: {"sha256": digest(edit), "duration": 2,
                              "full_decode": "passed"}},
    }), encoding="utf-8")
    spec["edit_video_path"] = str(edit.resolve())
    spec["transcode_verification_path"] = str(verification.resolve())
    result = create_version(spec, tmp_path / "creation")
    saved = json.loads(Path(result["version_path"]).read_text())
    assert saved["media_relationship"]["kind"] == "verified_transcode"
    assert saved["analysis"]["source_sha256"] != saved["edit_video"]["sha256"]
    assert saved["media_relationship"]["verification_sha256"] == digest(verification)
    spec["transcode_verification_path"] = None
    with pytest.raises(ValueError, match="verification"):
        create_version(spec, tmp_path / "other")


def test_revision_preserves_old_version_and_does_not_inherit_audit(sample, tmp_path):
    _, _, spec = sample
    output = tmp_path / "creation"
    first = create_version(spec, output)
    first_path = Path(first["version_path"])
    first_hash = digest(first_path)
    audit = {
        "reviewer": "independent copy editor", "verdict": "pass",
        "checks": {"spoken_naturalness": True, "evidence_accuracy": True,
                   "runtime_fit": True, "cta_truthfulness": True},
        "notes": ["Natural spoken draft; source claim checked."],
    }
    audited = audit_version(first_path, audit)
    assert Path(audited["audit_path"]).is_file()
    spec["script_lines"][0]["text"] = "点上去，用刷子晕开。"
    second = create_version(spec, output)
    saved = json.loads(Path(second["version_path"]).read_text())
    assert saved["version"] == 2
    assert saved["parent_version"] == 1
    assert saved["parent_sha256"] == first_hash
    assert saved["copy_audit_status"] == "pending"
    assert saved["human_approval"]["status"] == "pending_user"
    assert not (output / "v0002-audit.json").exists()
    assert digest(first_path) == first_hash


def test_audit_cannot_self_review_or_sign_human_approval(sample, tmp_path):
    _, _, spec = sample
    first = create_version(spec, tmp_path / "creation")
    version_path = Path(first["version_path"])
    audit = {
        "reviewer": "draft writer", "verdict": "pass",
        "checks": {"spoken_naturalness": True, "evidence_accuracy": True,
                   "runtime_fit": True, "cta_truthfulness": True},
        "notes": ["Checked."],
    }
    with pytest.raises(ValueError, match="independent"):
        audit_version(version_path, audit)
    audit["reviewer"] = "separate reviewer"
    audit["human_approval"] = {"status": "approved"}
    with pytest.raises(ValueError, match="approval"):
        audit_version(version_path, audit)
    assert not (tmp_path / "creation" / "v0001-audit.json").exists()


def test_changed_prior_draft_breaks_version_chain(sample, tmp_path):
    _, _, spec = sample
    output = tmp_path / "creation"
    first = create_version(spec, output)
    second = create_version(spec, output)
    first_path = Path(first["version_path"])
    first_path.write_text(first_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="version chain"):
        show_version(Path(second["version_path"]))
    with pytest.raises(ValueError, match="version chain"):
        create_version(spec, output)
    assert not (output / "v0003.json").exists()


def test_new_version_records_market_locale_and_pending_localized_voice_timing(sample, tmp_path):
    _, _, spec = sample
    spec.update({"market": "BR", "voiceover_locale": "pt-BR",
                 "angle": "点涂后如何处理边缘", "hook": "点上去太明显？先看它怎么晕开。",
                 "buyer_concern": "担心点涂后边界生硬", "proof_action": "同一人物连续点涂并刷具晕染",
                 "cta_direction": "核对色号和商品卡后查看商品"})
    result = create_version(spec, tmp_path / "creation")
    saved = json.loads(Path(result["version_path"]).read_text(encoding="utf-8"))
    assert saved["market"] == "BR"
    assert saved["market_profile"]["voiceover_locale"] == "pt-BR"
    assert saved["voiceover_locale"] == "pt-BR"
    assert saved["script_language"] == "zh-CN"
    assert saved["voiceover_timing_status"] == "pending_localized_voiceover"
    assert saved["creative_angle"]["proof_action"] == "同一人物连续点涂并刷具晕染"


@pytest.mark.parametrize("kind", ["collected_reference", "adapted_from_reference"])
def test_script_source_records_visual_reference_without_claiming_literal_reproduction(sample, tmp_path, kind):
    _, _, spec = sample
    reference_path = tmp_path / "reference-report.md"
    reference_path.write_text("hook / proof / CTA structure", encoding="utf-8")
    spec.update({
        "market": "BR", "voiceover_locale": "pt-BR",
        "angle": "结构改编", "hook": "先看这个顾虑。", "buyer_concern": "担心边界生硬",
        "proof_action": "连续点涂到刷具晕染", "cta_direction": "核对色号后查看商品",
        "script_source": {
            "kind": kind,
            "reference": {
                "path": str(reference_path), "source_url": "https://example.test/reference",
                "text_status": "unavailable",
                "structure_beats": {"hook": "开场顾虑", "buyer_concern": "边界生硬",
                                    "proof": "连续晕染", "objection_or_turn": "先点少量",
                                    "cta": "核对色号"},
                "adaptation_notes": "只适配结构节拍；没有原口播文字，不声明话术复刻。",
            },
        },
    })
    result = create_version(spec, tmp_path / "creation")
    saved = json.loads(Path(result["version_path"]).read_text(encoding="utf-8"))
    source = saved["script_source"]
    assert source["kind"] == kind
    assert source["claim_scope"] == "visual_structure_only"
    assert source["reference"]["text_status"] == "unavailable"
    assert source["reference"]["structure_beats"]["proof"] == "连续晕染"


def test_user_supplied_and_revision_sources_are_recorded(sample, tmp_path):
    _, _, spec = sample
    supplied_text = tmp_path / "user-script.txt"
    supplied_text.write_text("用户提供的文字稿。", encoding="utf-8")
    spec.update({"market": "BR", "voiceover_locale": "pt-BR",
                 "angle": "用户稿改写", "hook": "用户提供的开头。", "buyer_concern": "担心边界",
                 "proof_action": "连续点涂和晕染", "cta_direction": "核对色号后查看商品",
                 "script_source": {"kind": "user_supplied", "text_path": str(supplied_text)}})
    result = create_version(spec, tmp_path / "creation")
    saved = json.loads(Path(result["version_path"]).read_text(encoding="utf-8"))
    assert saved["script_source"]["kind"] == "user_supplied"
    assert saved["script_source"]["claim_scope"] == "user_provided_text"
    assert saved["script_source"]["text_path_sha256"] == digest(supplied_text)
    spec["script_source"] = {"kind": "revision", "revision_request": ["缩短 CTA", "删去内部术语"]}
    second = create_version(spec, tmp_path / "creation")
    revised = json.loads(Path(second["version_path"]).read_text(encoding="utf-8"))
    assert revised["script_source"]["kind"] == "revision"
    assert revised["script_source"]["parent_version"] == 1
    assert revised["script_source"]["revision_request"] == ["缩短 CTA", "删去内部术语"]


@pytest.mark.parametrize("market_draft", [False, True])
def test_obviously_fast_script_is_rejected_before_audit(sample, tmp_path, market_draft):
    _, _, spec = sample
    if market_draft:
        spec.update({"market": "BR", "voiceover_locale": "pt-BR",
                     "angle": "超长稿", "hook": "钩子", "buyer_concern": "顾虑",
                     "proof_action": "证明", "cta_direction": "CTA"})
    spec["script_lines"] = [{"start_seconds": 0, "end_seconds": 2,
                              "text": "这是一句明显超过两秒可清楚说完的中文口播稿并且还包含Color Bloom 2026。"}]
    with pytest.raises(ValueError, match="runtime estimate"):
        create_version(spec, tmp_path / "creation")


@pytest.mark.parametrize("verdict", ["pass", "revise"])
def test_audit_cannot_claim_runtime_fit_for_a_failing_estimate(sample, tmp_path, verdict):
    _, _, spec = sample
    result = create_version(spec, tmp_path / "creation")
    path = Path(result["version_path"])
    version = json.loads(path.read_text(encoding="utf-8"))
    version["script_lines"][0]["text"] = "这句话明显超过两秒能够清楚读出的长度，不能审核为时长通过。"
    path.write_text(json.dumps(version, ensure_ascii=False), encoding="utf-8")
    audit = {"reviewer": "separate reviewer", "verdict": verdict,
             "checks": {"spoken_naturalness": True, "evidence_accuracy": True,
                        "runtime_fit": True, "cta_truthfulness": True},
             "notes": ["Review the legacy draft against the current estimator."]}
    with pytest.raises(ValueError, match="runtime estimate"):
        audit_version(path, audit)
    assert not path.with_name("v0001-audit.json").exists()


def test_old_stored_pass_audit_gets_runtime_warning_without_mutating_audit(sample, tmp_path):
    _, _, spec = sample
    spec.update({"market": "BR", "voiceover_locale": "pt-BR",
                 "angle": "旧稿", "hook": "钩子", "buyer_concern": "顾虑",
                 "proof_action": "证明", "cta_direction": "CTA"})
    output = tmp_path / "creation"
    result = create_version(spec, output)
    version_path = Path(result["version_path"])
    version = json.loads(version_path.read_text(encoding="utf-8"))
    version["script_lines"] = [{"start_seconds": 0, "end_seconds": 2,
                                 "text": "这是一句明显超过两秒可清楚说完的中文口播稿并且还包含Color Bloom 2026。"}]
    version_path.write_text(json.dumps(version, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Simulate a legacy stored result from before automatic timing verification.
    legacy_audit = {"schema_version": 1, "version": 1, "version_path": str(version_path),
                    "version_sha256": digest(version_path), "reviewed_at": "2026-10-09T00:00:00+00:00",
                    "reviewer": "legacy reviewer", "verdict": "pass",
                    "status": "passed_needs_user_approval",
                    "checks": {"spoken_naturalness": True, "evidence_accuracy": True,
                                "runtime_fit": True, "cta_truthfulness": True,
                                "commercial_attractiveness": True},
                    "notes": ["Legacy pass."], "human_approval_status": "pending_user"}
    audit_path = version_path.with_name("v0001-audit.json")
    audit_path.write_text(json.dumps(legacy_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shown = show_version(version_path)
    assert shown["audit"]["checks"]["runtime_fit"] is True
    assert shown["audit"]["runtime_review"]["passes"] is False
    assert shown["effective_copy_audit_status"] == "needs_revision_runtime_warning"
    assert "runtime" in shown["runtime_warning"]
    assert json.loads(audit_path.read_text(encoding="utf-8"))["status"] == "passed_needs_user_approval"


def test_new_copy_audit_requires_commercial_attractiveness_field(sample, tmp_path):
    _, _, spec = sample
    spec.update({"market": "BR", "voiceover_locale": "pt-BR",
                 "angle": "点涂后的晕染过程", "hook": "点上去太明显？先看它怎么晕开。",
                 "buyer_concern": "担心边界生硬", "proof_action": "连续点涂并刷具晕染",
                 "cta_direction": "核对色号和商品卡后查看商品"})
    first = create_version(spec, tmp_path / "creation")
    version_path = Path(first["version_path"])
    legacy = {
        "reviewer": "separate reviewer", "verdict": "pass",
        "checks": {"spoken_naturalness": True, "evidence_accuracy": True,
                   "runtime_fit": True, "cta_truthfulness": True},
        "notes": ["Missing new commercial check."],
    }
    with pytest.raises(ValueError, match="commercial_attractiveness"):
        audit_version(version_path, legacy)
    current = {**legacy, "checks": {**legacy["checks"], "commercial_attractiveness": True},
               "notes": ["Hook names a concrete purchase concern; CTA is conditional on configured card."]}
    result = audit_version(version_path, current)
    saved = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))
    assert saved["checks"]["commercial_attractiveness"] is True


def test_batch_contract_keeps_unplanned_slots_and_rejects_unsupported_reuse():
    concepts = [
        {"ordinal": 1, "angle": "点涂后的晕染过程", "hook": "点上去太明显？先看它怎么晕开。",
         "buyer_concern": "担心点涂后边界生硬", "proof_requirement": "连续点涂到刷具晕染",
         "cta_direction": "确认色号和商品卡后查看商品", "source_asset_ids": ["asset_f826d491cbdf"]},
        {"ordinal": 2, "angle": "近景判断色彩范围", "hook": "近一点看，颜色到底落在哪里。",
         "buyer_concern": "担心镜头远看不清妆面", "proof_requirement": "转脸和脸颊近景",
         "cta_direction": "先核对色号，再看商品详情", "source_asset_ids": []},
        {"ordinal": 3, "angle": "新手分段上脸", "hook": "第一次用，别一上来就抹满。",
         "buyer_concern": "担心新手下手过重", "proof_requirement": "点涂、停顿、刷具晕染",
         "cta_direction": "按实际商品卡信息决定是否下单", "source_asset_ids": []},
    ]
    result = create_batch_contract({
        "market": "BR", "voiceover_locale": "pt-BR", "target_count": 24,
        "concepts": concepts, "allowed_asset_ids": ["asset_f826d491cbdf"],
        "recently_used_asset_ids": [], "shared_asset_reuse_authorized": False,
    })
    assert result["batch"]["target_count"] == 24
    assert result["batch"]["planned_count"] == 3
    assert result["batch"]["rendered_count"] == 0
    assert result["batch"]["representative_count"] == 3
    assert result["batch"]["unplanned_ordinals"] == list(range(4, 25))
    assert result["plans"][1]["material_status"] == "awaiting_asset_binding"
    assert result["plans"][0]["proof_support_status"] == "asset_bound"
    assert result["market_profile"]["voiceover_locale"] == "pt-BR"
    assert result["batch"]["reuse_policy"]["shared_asset_reuse"] == "not_authorized"
    concepts[1]["source_asset_ids"] = ["asset_f826d491cbdf"]
    with pytest.raises(ValueError, match="shared_asset_reuse"):
        create_batch_contract({
            "market": "BR", "voiceover_locale": "pt-BR", "target_count": 24,
            "concepts": concepts, "allowed_asset_ids": ["asset_f826d491cbdf"],
            "recently_used_asset_ids": [], "shared_asset_reuse_authorized": False,
        })


def test_batch_contract_rejects_template_only_variations_and_recent_assets():
    base = {"ordinal": 1, "angle": "一个角度", "hook": "具体钩子", "buyer_concern": "具体顾虑",
            "proof_requirement": "具体证明", "cta_direction": "具体 CTA",
            "source_asset_ids": ["asset_a"]}
    with pytest.raises(ValueError, match="difference"):
        create_batch_contract({"market": "BR", "target_count": 2,
                               "concepts": [{**base, "difference_basis": "换音乐"}],
                               "allowed_asset_ids": ["asset_a"],
                               "recently_used_asset_ids": [],
                               "shared_asset_reuse_authorized": True})
    with pytest.raises(ValueError, match="seven_day"):
        create_batch_contract({"market": "BR", "target_count": 2,
                               "concepts": [base], "allowed_asset_ids": ["asset_a"],
                               "recently_used_asset_ids": ["asset_a"],
                               "shared_asset_reuse_authorized": True})


def test_batch_contract_cli_writes_partial_contract(tmp_path):
    spec = tmp_path / "batch.json"
    output = tmp_path / "contract.json"
    spec.write_text(json.dumps({
        "market": "BR", "voiceover_locale": "pt-BR", "target_count": 24,
        "concepts": [{"ordinal": 1, "angle": "点涂后晕染", "hook": "点上去太明显？",
                      "buyer_concern": "担心边界生硬", "proof_requirement": "点涂到刷具晕染",
                      "cta_direction": "核对色号后查看商品", "source_asset_ids": []}],
        "allowed_asset_ids": [], "recently_used_asset_ids": [],
        "shared_asset_reuse_authorized": False,
    }), encoding="utf-8")
    result = subprocess.run([
        sys.executable, "scripts/plan_video.py", "batch-contract",
        "--spec", str(spec), "--output", str(output),
    ], cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    contract = json.loads(output.read_text(encoding="utf-8"))
    assert summary["target_count"] == 24
    assert summary["planned_count"] == 1
    assert summary["rendered_count"] == 0
    assert contract["voiceover_locale"] == "pt-BR"
    assert contract["batch"]["unplanned_ordinals"] == list(range(2, 25))
