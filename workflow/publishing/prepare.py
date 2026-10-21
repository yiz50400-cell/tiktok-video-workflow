"""Create an immutable, evidence-bound preparation package on this machine."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import imageio_ffmpeg

from workflow.markets import market_profile
from .copy import derive_copy

EVIDENCE_LABELS = {"execution":"执行清单", "creative":"中文稿", "zh_approval":"中文批准",
                   "localization":"本地化", "localization_audit":"审核", "qa":"成片QA"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入JSON须为对象")
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def local_path(raw, workspace):
    if not isinstance(raw, str) or not raw or "://" in raw:
        raise ValueError("仅支持明确的本地文件路径")
    return (workspace / raw).resolve()


def validate_request(request, workspace):
    if request.get("schema_version") != 1:
        raise ValueError("发布准备输入版本须为1")
    if request.get("delivery_method", "local_package") != "local_package":
        raise ValueError("本期仅支持本地准备；平台方式尚未接入和授权")
    market, locale = request.get("market"), request.get("locale")
    profile = market_profile(market, locale)
    if not locale or profile["voiceover_locale"] != locale:
        raise ValueError("须明确目标市场和语言")
    product_name = request.get("product_name_zh")
    if not isinstance(product_name, str) or not product_name.strip():
        raise ValueError("需要中文商品名")
    paths, data, hashes = {}, {}, {}
    for key, label in EVIDENCE_LABELS.items():
        paths[key] = local_path(request.get(key), workspace)
        if not paths[key].is_file():
            raise ValueError(f"缺少{label}证据")
        try:
            raw = paths[key].read_bytes()
            data[key] = json.loads(raw)
            if not isinstance(data[key], dict):
                raise ValueError("证据须为对象")
        except (ValueError, OSError) as exc:
            raise ValueError(f"{label}证据无法读取") from exc
        hashes[key] = hashlib.sha256(raw).hexdigest()
    approval = data["zh_approval"]
    if approval.get("status") != "approved" or approval.get("version_sha256") != hashes["creative"]:
        raise ValueError("中文批准未绑定当前中文稿")
    loc, audit, execution, qa = (data[key] for key in ("localization", "localization_audit", "execution", "qa"))
    if loc.get("creative_sha256") != hashes["creative"]:
        raise ValueError("本地化未绑定当前中文稿")
    if (loc.get("market"), loc.get("locale")) != (market, locale):
        raise ValueError("本地化市场或语言不匹配")
    if (audit.get("status") != "passed_independent_internal_text_review"
            or audit.get("material_changes_to_approved_meaning") != []
            or audit.get("creative_sha256") != hashes["creative"]
            or audit.get("zh_approval_sha256") != hashes["zh_approval"]
            or audit.get("localization_sha256") != hashes["localization"]
            or (audit.get("market"), audit.get("locale")) != (market, locale)):
        raise ValueError("审核未通过或未绑定当前中文批准与本地化")
    if execution.get("localization_sha256") != hashes["localization"]:
        raise ValueError("执行清单未绑定当前本地化")
    for key in ("creative", "zh_approval", "localization", "localization_audit"):
        if not any(local_path(item.get("path"), workspace) == paths[key] and item.get("sha256") == hashes[key]
                   for item in execution.get("bound_files", [])):
            raise ValueError(f"执行清单未绑定{EVIDENCE_LABELS[key]}证据")
    if qa.get("execution_sha256") != hashes["execution"]:
        raise ValueError("成片QA未绑定当前执行清单")
    paths["video"] = local_path(execution.get("output"), workspace)
    if not paths["video"].is_file() or paths["video"].suffix.lower() != ".mp4":
        raise ValueError("缺少本地MP4成片")
    hashes["video"] = digest(paths["video"])
    if qa.get("video_sha256") != hashes["video"] or qa.get("video", {}).get("full_decode") != "passed":
        raise ValueError("成片哈希或完整解码证据不匹配")
    if qa.get("status") not in ("technical_checks_passed_pending_visual_review", "technical_checks_passed"):
        raise ValueError("成片技术QA尚未通过")
    duration = execution.get("duration")
    if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
            or abs(qa["video"].get("duration_seconds", -1) - duration) > 0.1):
        raise ValueError("成片时长与执行清单不一致")
    cover = request.get("cover_seconds")
    if type(cover) not in (int, float) or not math.isfinite(cover) or not 0 <= cover < duration:
        raise ValueError("封面时间须位于成片内")
    if execution.get("srt"):
        from scripts.produce_video import build_srt
        paths["subtitle"] = local_path(execution["srt"], workspace)
        cues = execution.get("subtitle_cues")
        if cues is None:
            cues = [(c["measured_start"], c["measured_end"], c["text"]) for c in execution.get("audio_cues", [])]
        if not cues or not paths["subtitle"].is_file() or paths["subtitle"].read_text(encoding="utf-8") != build_srt(cues):
            raise ValueError("字幕未匹配当前执行清单")
        hashes["subtitle"] = digest(paths["subtitle"])
    account = request.get("account")
    if account is not None and (not isinstance(account, dict) or not account.get("handle") or account.get("market") != market):
        raise ValueError("账号须包含明确handle及匹配的市场")
    product_id = request.get("product_id")
    if product_id is not None and (not isinstance(product_id, str) or not product_id.strip()
                                   or request.get("product_market") != market):
        raise ValueError("商品ID须为员工提供的明确字符串，并标明同一市场")
    return paths, data, hashes, profile, derive_copy(request, data["creative"], loc)


def _public_result(output, manifest, reused):
    return {"ok": True, "state": "LOCAL_PREPARED", "reused": reused, "package_path": str(output),
            "preview_zh": manifest["preview_zh"], "blockers_zh": manifest["eligibility"]["blockers_zh"],
            "artifacts": {"video": str(output / "video.mp4"), "cover": str(output / "cover.jpg"),
                          "preview": str(output / "中文发布预览.md"), "manifest": str(output / "manifest.json")},
            "platform": manifest["platform"]}


def _reuse(output, fingerprint):
    try:
        receipt = read_json(output / "receipt.json")
        if receipt["input_fingerprint"] != fingerprint:
            raise ValueError("输入已变化，请使用新编号输出目录")
        if digest(output / "manifest.json") != receipt["manifest_sha256"]:
            raise ValueError("发布准备包损坏，请使用新编号输出目录")
        manifest = read_json(output / "manifest.json")
        for relative, item in manifest["files"].items():
            path = (output / relative).resolve()
            if not path.is_relative_to(output) or path != Path(item["path"]) or digest(path) != item["sha256"]:
                raise ValueError("发布准备包损坏，请使用新编号输出目录")
        return _public_result(output, manifest, True)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("输出目录缺少完整准备记录或产物损坏，请使用新编号输出目录") from exc


def _cover(video, seconds, destination):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
               "-ss", str(seconds), "-i", str(video), "-map", "0:v:0", "-frames:v", "1", "-an",
               "-vf", "scale=540:-2", "-q:v", "2", "-y", str(destination)]
    result = subprocess.run(command, capture_output=True, timeout=45)
    if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError("封面帧提取失败：" + result.stderr.decode(errors="replace")[-500:])


def _preview(request, profile, copy, manifest):
    product = manifest["product"]
    video_info = manifest["video"]
    account = request.get("account")
    cover_ms = round(request["cover_seconds"] * 1000)
    blocks = ["# 中文发布预览", "", f"商品：{request['product_name_zh']}",
              f"市场：{profile['name']}；目标语言稿已保存在内部制作文件。",
              f"账号：{account['handle'] if account else '待指定'}", "", "状态：本地准备完成；尚未上传。", "",
              f"发布标题：{copy['title_zh']}", f"标签依据：{'、'.join(copy['tag_context_zh']) or '未选择标签'}",
              "标题复用已确认中文原意；当地语言沿用已绑定的内部独立审核，外语全文免展示。", "",
              f"视频：{video_info['duration_seconds']}秒，{video_info['width']}×{video_info['height']}；沿用既有成片。",
              f"封面：成片 {cover_ms / 1000:.3f} 秒原帧，供员工在实际编辑入口选择。", "",
              "![封面](" + manifest["cover"]["path"] + ")", "",
              "商品待选：" + (f"已提供候选ID {product['product_id']}，待该市场账号核对。" if product['product_id'] else
                            "未指定准确商品ID或SKU；员工在目标账号橱窗中核对商品、市场及色号后手动挂载。"),
              "", "平台操作状态：本流程未打开账号、上传、保存草稿、挂商品或发布。", "",
              "交付前待核实：", ""]
    blocks += ["- " + item for item in manifest["eligibility"]["blockers_zh"]]
    blocks += ["", "员工后续手工操作：在原账号环境自行打开 TikTok，核对账号、商品和文案，上传视频、选封面、挂商品并发布。草稿是否可用由员工在平台内判断。",
               "", "准确当地语言文案、来源证据和哈希记录保存在包内 internal 目录，供员工使用。", ""]
    return "\n".join(blocks)


def prepare_publish(request_path, output_path, *, workspace):
    """Prepare or verify/reuse one local package. Never logs in or uploads."""
    workspace = Path(workspace).resolve()
    request_path = Path(request_path).resolve()
    output = Path(output_path).absolute()
    if output.is_symlink():
        raise ValueError("输出目录不能是符号链接")
    output = output.resolve()
    request_bytes = request_path.read_bytes()
    request_hash = hashlib.sha256(request_bytes).hexdigest()
    request = json.loads(request_bytes)
    if not isinstance(request, dict):
        raise ValueError("发布准备输入JSON须为对象")
    paths, data, hashes, profile, copy = validate_request(request, workspace)
    if digest(request_path) != request_hash:
        raise ValueError("准备期间请求输入变化，请使用新编号")
    if any(path.is_relative_to(output) for path in [request_path, *paths.values()]):
        raise ValueError("输出目录不能包含或覆盖来源文件")
    # Request bytes and source bytes make semantic changes require a new package version.
    fingerprint = hashlib.sha256(json.dumps({"request_sha256": request_hash, "files": hashes,
                                             "schema":1}, sort_keys=True).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".prepare.lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("同机准备任务已有锁；核对该执行进程后再重试") from exc
    started = time.perf_counter()
    partial = None
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        if output.exists():
            return _reuse(output, fingerprint)
        partial = Path(tempfile.mkdtemp(prefix=output.name + ".partial-", dir=output.parent))
        internal = partial / "internal"; internal.mkdir()
        evidence_dir = internal / "evidence"; evidence_dir.mkdir()
        for key in EVIDENCE_LABELS:
            shutil.copyfile(paths[key], evidence_dir / (key + ".json"))
        shutil.copyfile(request_path, internal / "request.json")
        shutil.copyfile(paths["video"], partial / "video.mp4")
        if "subtitle" in paths:
            shutil.copyfile(paths["subtitle"], partial / "subtitle.srt")
        _cover(partial / "video.mp4", request["cover_seconds"], partial / "cover.jpg")
        copy["review"].update({key + "_sha256": hashes[key] for key in ("creative", "zh_approval", "localization", "localization_audit")})
        write_json(internal / "publish-copy.json", copy)
        (internal / "caption.txt").write_text(copy["caption"] + "\n", encoding="utf-8")
        blockers = []
        scope = data["qa"].get("scope", {})
        if scope.get("final_user_acceptance") != "approved":
            blockers.append("成片最终验收待完成；当前包供开发预览")
        if scope.get("commercial_license") != "verified":
            blockers.append("素材商业复用权待核实，现有样片不能直接视为可商用")
        manifest = {"schema_version":1, "state":"LOCAL_PREPARED", "job_id":data["execution"].get("job_id"),
                    "created_at":datetime.now(timezone.utc).isoformat(), "input_fingerprint":fingerprint,
                    "delivery_method":"local_package", "account":request.get("account"),
                    "market":request["market"], "locale":request["locale"],
                    "product":{"name_zh":request["product_name_zh"], "product_id":request.get("product_id"),
                               "market":request["market"], "selection":"pending_employee_verification", "attached":False},
                    "platform":{"upload":"not_attempted", "inbox":"not_verified", "draft":"not_verified",
                                "publication":"not_attempted", "publish_id":None, "content_id":None, "url":None},
                    "eligibility":{"ready_for_upload":False,"blockers_zh":blockers},
                    "video":{**data["qa"]["video"], "path":str(output / "video.mp4"), "sha256":hashes["video"]},
                    "cover":{"seconds":request["cover_seconds"], "path":str(output / "cover.jpg"),
                             "platform_cover_applied":False},
                    "preview_zh":{"title":copy["title_zh"], "tag_context":copy["tag_context_zh"]},
                    "approval":{"chinese_user_approval":"approved_exact_bound_creative",
                                "publication_copy":copy["review"], "source_qa_scope":scope},
                    "sources":{key:{"path":str(path), "sha256":hashes[key]} for key,path in paths.items()}}
        (partial / "中文发布预览.md").write_text(_preview(request, profile, copy, manifest), encoding="utf-8")
        # Detect sources changing during copying instead of binding mixed versions.
        if digest(request_path) != request_hash or digest(internal / "request.json") != request_hash:
            raise ValueError("准备期间请求输入变化，请使用新编号")
        for key, path in paths.items():
            dest = evidence_dir / (key + ".json") if key in EVIDENCE_LABELS else partial / (
                "video.mp4" if key == "video" else "subtitle.srt")
            if digest(path) != hashes[key] or digest(dest) != hashes[key]:
                raise ValueError("准备期间来源变化，请使用新编号")
        manifest["files"] = {str(path.relative_to(partial)):{"path":str(output / path.relative_to(partial)),"sha256":digest(path)}
                             for path in sorted(partial.rglob("*")) if path.is_file()}
        manifest["preparation_seconds"] = round(time.perf_counter() - started, 4)
        write_json(partial / "manifest.json", manifest)
        write_json(partial / "receipt.json", {"input_fingerprint":fingerprint,"manifest_sha256":digest(partial / "manifest.json")})
        os.rename(partial, output)
        partial = None
        return _public_result(output, manifest, False)
    except Exception as exc:
        write_json(output.with_name(output.name + ".prepare-failure.json"),
                   {"state":"LOCAL_PREPARE_FAILED","input_fingerprint":fingerprint,
                    "error_type":type(exc).__name__, "error":str(exc), "platform_action":False})
        raise
    finally:
        if partial is not None:
            shutil.rmtree(partial)
        lock.unlink(missing_ok=True)
