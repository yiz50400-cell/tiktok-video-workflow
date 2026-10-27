"""Post-produce verification for unattended ChatCut exports (stage E)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .collect import probe_media, resolve_ffmpeg
from .batch_footage import (
    cross_video_overlap_issues,
    extract_per_video_results,
    footage_ids_from_result_video,
    within_video_near_dup_issues,
)

FORBIDDEN_TOOL_RE = re.compile(r"^(submit_\w+|search_stock_media|import_url|delegate_task)$")
SILENCE_MAX_DB = -60.0


@dataclass
class VerifyResult:
    ok: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def scan_codex_events(events_path: Path | None) -> dict[str, Any]:
    """Count MCP tool calls and flag forbidden tools / computer-use items in codex --json events."""
    calls: dict[str, int] = {}
    forbidden: list[str] = []
    computer_use = 0
    if not events_path or not Path(events_path).is_file():
        return {"available": False, "calls": calls, "forbidden": forbidden, "computer_use_items": 0}
    for line in Path(events_path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if not isinstance(item, dict) or event.get("type") != "item.completed":
            continue
        kind = str(item.get("type") or "")
        if "computer" in kind:
            computer_use += 1
        if kind == "mcp_tool_call":
            tool = str(item.get("tool") or "")
            server = str(item.get("server") or "")
            key = f"{server}.{tool}"
            calls[key] = calls.get(key, 0) + 1
            if FORBIDDEN_TOOL_RE.match(tool):
                forbidden.append(key)
            if server and server != "chatcut_desktop":
                forbidden.append(key)
    # code_mode wraps MCP calls inside exec; fall back to text scan of the raw log.
    raw = Path(events_path).read_text(encoding="utf-8", errors="replace")
    for name in re.findall(r"mcp__chatcut_desktop__(\w+)\s*\(", raw):
        calls[f"chatcut_desktop.{name}"] = calls.get(f"chatcut_desktop.{name}", 0) + 1
        if FORBIDDEN_TOOL_RE.match(name):
            forbidden.append(f"chatcut_desktop.{name}")
    return {"available": True, "calls": calls, "forbidden": sorted(set(forbidden)), "computer_use_items": computer_use}


def _verify_one_export(
    export: Path,
    *,
    clips: list[dict[str, Any]],
    used_rows: list[Any],
    voiceover: bool,
    min_duration: float,
    max_duration: float,
    ffmpeg: str,
) -> tuple[VerifyResult | None, dict[str, Any], set[str]]:
    """Validate one export file. Returns (failure_or_None, probe_details, used_paths)."""
    details: dict[str, Any] = {}
    probe = probe_media(export, ffmpeg=ffmpeg, measure_volume=True)
    details["export"] = {
        "path": str(export), "bytes": export.stat().st_size, "duration": probe.duration,
        "width": probe.width, "height": probe.height, "vcodec": probe.vcodec,
        "has_audio": probe.has_audio, "max_volume_db": probe.max_volume_db,
    }
    if export.stat().st_size < 300_000:
        return VerifyResult(False, f"导出文件过小（{export.stat().st_size} B）", details), details, set()
    if (probe.vcodec or "").lower() != "h264" or not probe.duration:
        return VerifyResult(False, "导出不是可解码的 H.264 视频", details), details, set()
    if not (probe.width and probe.height and probe.height > probe.width and probe.width >= 720):
        return VerifyResult(False, f"导出分辨率不是竖屏 ≥720p：{probe.width}x{probe.height}", details), details, set()
    if not (min_duration <= probe.duration <= max_duration):
        return VerifyResult(False, f"导出时长 {probe.duration:.2f}s 不在 [{min_duration},{max_duration}]", details), details, set()
    if not voiceover and probe.has_audio and (probe.max_volume_db is None or probe.max_volume_db > SILENCE_MAX_DB):
        return VerifyResult(False, f"违反声音规则：无口播成片应静音，实测峰值 {probe.max_volume_db} dB", details), details, set()
    allowed = {str(Path(c["path"]).resolve()) for c in clips}
    used_paths = {
        str(Path(str(u.get("path"))).resolve())
        for u in used_rows
        if isinstance(u, dict) and u.get("path")
    }
    details["footage_used"] = sorted(used_paths)
    if not used_paths:
        return VerifyResult(False, "结果未列出 footage_used，无法证明使用了采集素材", details), details, set()
    outside = used_paths - allowed
    if outside:
        return VerifyResult(False, f"使用了素材清单以外的文件：{sorted(outside)}", details), details, set()
    return None, details, used_paths


def verify_production(
    *,
    result_path: Path,
    export_paths: list[Path],
    clips: list[dict[str, Any]],
    events_path: Path | None = None,
    voiceover: bool = False,
    min_duration: float = 8.0,
    max_duration: float = 60.0,
    ffmpeg: str | None = None,
    expected_count: int = 1,
    allowed_clip_ids_per_video: list[set[str]] | None = None,
) -> VerifyResult:
    details: dict[str, Any] = {}
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return VerifyResult(False, f"结果文件缺失或不是 JSON：{exc}", details)
    if not result.get("ok"):
        return VerifyResult(False, f"Codex 报告失败：{result.get('error') or result}", details)
    name = str(result.get("project_name") or "")
    if name.upper().startswith("TEST"):
        return VerifyResult(False, f"导出来自测试/金丝雀工程 {name!r}，不是本任务成片", details)
    expect = max(1, int(expected_count or 1))
    if len(export_paths) != expect:
        return VerifyResult(False, f"应恰好导出 {expect} 个成片，实际 {len(export_paths)}", details)
    ffmpeg = ffmpeg or resolve_ffmpeg()
    videos = extract_per_video_results(result, export_paths)
    if len(videos) != expect:
        return VerifyResult(
            False,
            f"结果应描述 {expect} 条成片（videos[] 或单条 shots），实际 {len(videos)}",
            details,
        )
    details["exports"] = []
    per_video_ids: list[set[str]] = []
    total_used_paths: set[str] = set()
    for index, (export, video) in enumerate(zip(export_paths, videos)):
        used_rows = list(video.get("footage_used") or []) or list(result.get("footage_used") or [])
        failure, one_details, used_paths = _verify_one_export(
            export,
            clips=clips,
            used_rows=used_rows,
            voiceover=voiceover,
            min_duration=min_duration,
            max_duration=max_duration,
            ffmpeg=ffmpeg,
        )
        details["exports"].append(one_details)
        if failure:
            failure.details = {**details, **failure.details, "video_index": index}
            return failure
        total_used_paths |= used_paths
        ids = footage_ids_from_result_video(video)
        if not ids and used_rows:
            # fallback: stem from paths
            from .batch_footage import clip_id_of
            ids = {clip_id_of(u) for u in used_rows if clip_id_of(u)}
        per_video_ids.append(ids)
        # Assigned-bundle enforcement (when producer partitioned the pool).
        if allowed_clip_ids_per_video and index < len(allowed_clip_ids_per_video):
            allowed_ids = allowed_clip_ids_per_video[index]
            if allowed_ids:
                leaked = sorted(ids - allowed_ids)
                if leaked:
                    return VerifyResult(
                        False,
                        f"第 {index + 1} 条成片用了未分配给它的素材：{leaked}（禁止同批换序复用）",
                        {**details, "video_index": index, "leaked_clip_ids": leaked},
                    )
        near = within_video_near_dup_issues(video.get("shots") or [])
        if near:
            return VerifyResult(False, f"第 {index + 1} 条：{near[0]}", {**details, "video_index": index})
    if expect > 1:
        overlap = cross_video_overlap_issues(per_video_ids)
        details["per_video_clip_ids"] = [sorted(s) for s in per_video_ids]
        if overlap:
            return VerifyResult(False, overlap[0], details)
    scan = scan_codex_events(events_path)
    details["codex_tools"] = scan
    if scan["forbidden"]:
        return VerifyResult(False, f"调用了禁用工具：{scan['forbidden']}", details)
    if scan["computer_use_items"]:
        return VerifyResult(False, "Codex 事件中出现 Computer Use", details)
    # Cheap completeness gate: if Codex recorded any chatcut_desktop MCP activity,
    # it must have called export. Rough-cut-only bail (edit_item then exit 0) fails here.
    chatcut_calls = [k for k in (scan.get("calls") or {}) if str(k).startswith("chatcut_desktop.")]
    if scan.get("available") and chatcut_calls and "chatcut_desktop.export" not in scan["calls"]:
        return VerifyResult(
            False,
            "Codex 未调用 chatcut_desktop.export（仅粗剪/无导出），拒绝当作成片",
            details,
        )
    # Keep legacy single-export detail key for older tests.
    if expect == 1 and details["exports"]:
        details["export"] = details["exports"][0].get("export", {})
        details["footage_used"] = details["exports"][0].get("footage_used", [])
    first = details["exports"][0].get("export", {}) if details["exports"] else {}
    return VerifyResult(
        True,
        f"成片校验通过：{expect} 条，首条 {first.get('width')}x{first.get('height')} "
        f"{float(first.get('duration') or 0):.2f}s，素材路径 {len(total_used_paths)} 个"
        + (f"；跨条 clip 集合互斥" if expect > 1 else ""),
        details,
    )
