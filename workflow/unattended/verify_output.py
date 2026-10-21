"""Post-produce verification for unattended ChatCut exports (stage E)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .collect import probe_media, resolve_ffmpeg

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
    if len(export_paths) != 1:
        return VerifyResult(False, f"应恰好导出 1 个成片，实际 {len(export_paths)}", details)
    export = export_paths[0]
    ffmpeg = ffmpeg or resolve_ffmpeg()
    probe = probe_media(export, ffmpeg=ffmpeg, measure_volume=True)
    details["export"] = {
        "path": str(export), "bytes": export.stat().st_size, "duration": probe.duration,
        "width": probe.width, "height": probe.height, "vcodec": probe.vcodec,
        "has_audio": probe.has_audio, "max_volume_db": probe.max_volume_db,
    }
    if export.stat().st_size < 300_000:
        return VerifyResult(False, f"导出文件过小（{export.stat().st_size} B）", details)
    if (probe.vcodec or "").lower() != "h264" or not probe.duration:
        return VerifyResult(False, "导出不是可解码的 H.264 视频", details)
    if not (probe.width and probe.height and probe.height > probe.width and probe.width >= 720):
        return VerifyResult(False, f"导出分辨率不是竖屏 ≥720p：{probe.width}x{probe.height}", details)
    if not (min_duration <= probe.duration <= max_duration):
        return VerifyResult(False, f"导出时长 {probe.duration:.2f}s 不在 [{min_duration},{max_duration}]", details)
    if not voiceover and probe.has_audio and (probe.max_volume_db is None or probe.max_volume_db > SILENCE_MAX_DB):
        return VerifyResult(False, f"违反声音规则：无口播成片应静音，实测峰值 {probe.max_volume_db} dB", details)
    allowed = {str(Path(c["path"]).resolve()) for c in clips}
    used = result.get("footage_used") or []
    used_paths = {str(Path(str(u.get("path"))).resolve()) for u in used if isinstance(u, dict) and u.get("path")}
    details["footage_used"] = sorted(used_paths)
    if not used_paths:
        return VerifyResult(False, "结果未列出 footage_used，无法证明使用了采集素材", details)
    outside = used_paths - allowed
    if outside:
        return VerifyResult(False, f"使用了素材清单以外的文件：{sorted(outside)}", details)
    scan = scan_codex_events(events_path)
    details["codex_tools"] = scan
    if scan["forbidden"]:
        return VerifyResult(False, f"调用了禁用工具：{scan['forbidden']}", details)
    if scan["computer_use_items"]:
        return VerifyResult(False, "Codex 事件中出现 Computer Use", details)
    return VerifyResult(True, f"成片校验通过：{probe.width}x{probe.height} {probe.duration:.2f}s，"
                              f"{'无音轨' if not probe.has_audio else f'静音(峰值 {probe.max_volume_db} dB)'}，"
                              f"素材 {len(used_paths)} 条", details)
