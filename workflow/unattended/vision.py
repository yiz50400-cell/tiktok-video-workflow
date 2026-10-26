"""Vision quality gates for unattended production (Codex gpt-5.6-sol with attached images).

* ``screen_clips``  — BEFORE editing: per clip, check 12 sampled frames for same product
  type / form factor, burned-in source text, watermarks and lifestyle-only frames, and derive
  ``usable_segments`` (only these may be cut into the timeline). Burned-in text is direct
  salvage (tight temporal LaMa fill), not cut/dodge-first; wrong-product still hard-kills.
* ``check_shots``   — after editing (deterministic): every shot lies inside a usable segment,
  hook and final CTA shots come from product-prominent segments.
* ``review_export`` — AFTER export: sample one frame per shot from the finished video and
  re-check product / burned text / watermark / lifestyle rules.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SCREEN_TEMPLATE = PROMPTS_DIR / "footage_screen.md"
REVIEW_TEMPLATE = PROMPTS_DIR / "export_review.md"
SCREEN_FRAMES = 12
GRID_COLS = 4
TAIL_SKIP_S = 3.0  # TikTok end card / outro
SEGMENT_TOLERANCE_S = 0.25


# ----------------------------------------------------------------------------- helpers

def parse_json_message(text: str) -> Any:
    if not text:
        return None
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.S)
    blob = fenced.group(1) if fenced else stripped[stripped.find("{"): stripped.rfind("}") + 1]
    try:
        return json.loads(blob) if blob else None
    except json.JSONDecodeError:
        return None


def build_vision_command(*, codex_bin: str, model: str, workdir: Path, last_message_path: Path,
                         images: list[Path], use_profile: bool) -> list[str]:
    command = [codex_bin, "exec", "-m", model]
    if use_profile:
        command.extend(["-p", "unattended-chatcut"])
    for image in images:  # keep -i before other flags: --image takes multiple values
        command.extend(["-i", str(image)])
    command.extend([
        "-s", "read-only",
        "-c", 'approval_policy="never"',
        "-c", 'model_reasoning_effort="medium"',
        "-c", "mcp_servers.chatcut_desktop.enabled=false",
        "--disable", "computer_use",
        "--skip-git-repo-check",
        "-C", str(workdir),
        "-o", str(last_message_path),
        "--json",
        "--color", "never",
        "-",
    ])
    return command


def default_vision_runner(prompt: str, *, command: list[str], cwd: Path, events_path: Path, timeout: float) -> int:
    with events_path.open("w", encoding="utf-8") as out:
        completed = subprocess.run(command, input=prompt, text=True, stdout=out, stderr=subprocess.PIPE,
                                   timeout=timeout, cwd=str(cwd), check=False)
    events_path.with_suffix(".stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
    return completed.returncode


def ask_vision(prompt: str, images: list[Path], *, workdir: Path, name: str, codex_bin: str, model: str,
               timeout: float = 600, attempts: int = 2, runner: Callable[..., int] | None = None) -> Any:
    workdir.mkdir(parents=True, exist_ok=True)
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    runner = runner or default_vision_runner
    (workdir / f"{name}-prompt.md").write_text(prompt, encoding="utf-8")
    for attempt in range(1, attempts + 1):
        last = workdir / f"{name}-last-message{'' if attempt == 1 else '.' + str(attempt)}.txt"
        command = build_vision_command(codex_bin=codex_bin, model=model, workdir=workdir, last_message_path=last,
                                       images=images, use_profile=(codex_home / "unattended-chatcut.config.toml").is_file())
        try:
            runner(prompt, command=command, cwd=workdir, events_path=workdir / f"{name}-events{attempt}.jsonl",
                   timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
        payload = parse_json_message(last.read_text(encoding="utf-8") if last.is_file() else "")
        if isinstance(payload, dict):
            return payload
    return None


def extract_frame_grid(video: Path, times: list[float], out: Path, *, ffmpeg: str, cols: int = GRID_COLS,
                       width: int = 256) -> Path:
    tmp = out.parent / (out.stem + "_frames")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for index, t in enumerate(times):
        subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", str(tmp / f"f{index:02d}.jpg")], capture_output=True, timeout=60)
        if not (tmp / f"f{index:02d}.jpg").is_file():  # past EOF: reuse previous frame
            prev = tmp / f"f{index - 1:02d}.jpg"
            if prev.is_file():
                shutil.copy(prev, tmp / f"f{index:02d}.jpg")
    rows = (len(times) + cols - 1) // cols
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(tmp / "f%02d.jpg"), "-vf", f"tile={cols}x{rows}:padding=4",
                    "-frames:v", "1", str(out)], capture_output=True, timeout=60)
    if not out.is_file():
        raise RuntimeError(f"抽帧拼图失败：{video}")
    return out


def screen_times(duration: float, n: int = SCREEN_FRAMES) -> tuple[list[float], float]:
    span = max(min(duration - TAIL_SKIP_S, duration), duration * 0.8)
    return [round((i + 0.5) * span / n, 2) for i in range(n)], span


# ----------------------------------------------------------------------------- screening (before editing)

CROP_REGIONS = ("top", "bottom")


def frame_crop(frame: dict[str, Any]) -> str | None | bool:
    """None = usable as is; "top"/"bottom" = usable after cropping that text band; False = skip frame.

    Heavy burned-in text is not a clean segment. Burn-dominated clips are demoted for
    direct salvage; a few burned frames in an otherwise clean clip stay out of
    usable_segments. Only *small* corner text at top/bottom with crop_ok may be kept
    with a crop hint. Wrong-product is a hard reject.
    """
    if not frame.get("product_visible") or frame.get("watermark") or frame.get("lifestyle_only"):
        return False
    burned = str(frame.get("burned_text") or "none")
    if burned == "none":
        return None
    if burned == "heavy":
        return False  # not a clean segment; burn-dominated clips are salvaged instead
    region = str(frame.get("text_region") or "")
    if region in CROP_REGIONS and frame.get("crop_ok"):
        return region
    return False


def frame_usable(frame: dict[str, Any]) -> bool:
    return frame_crop(frame) is not False


def _other_reject_count(rejected_frames: dict[str, int]) -> int:
    return (
        int(rejected_frames.get("watermark") or 0)
        + int(rejected_frames.get("lifestyle_only") or 0)
        + int(rejected_frames.get("no_product") or 0)
    )


def _burn_dominated(rejected_frames: dict[str, int]) -> bool:
    """True when burned-in text is the main reason clean frames are missing (not wrong-product)."""
    burned = int(rejected_frames.get("burned_text") or 0)
    heavy = int(rejected_frames.get("burned_text_heavy") or 0)
    return burned >= 1 and (burned + heavy) >= _other_reject_count(rejected_frames)


def _burn_direct_salvage(rejected_frames: dict[str, int], n_frames: int) -> bool:
    """Majority of samples carry burned-in text, and burn outweighs other defects.

    Those clips go to remove/blur even if a few clean frames remain.
    Sparse burns (not a majority) still keep the clean segments.
    """
    burned = int(rejected_frames.get("burned_text") or 0)
    n = max(int(n_frames), 1)
    if burned < 1 or _other_reject_count(rejected_frames) > burned:
        return False
    return burned * 2 >= n


def _stamp_frame_times(frames: list[Any], times: list[float]) -> list[Any]:
    stamped: list[Any] = []
    for index, frame in enumerate(frames or []):
        if not isinstance(frame, dict):
            stamped.append(frame)
            continue
        item = dict(frame)
        if item.get("t") is None and item.get("time_s") is None:
            idx = int(item.get("i", index))
            if 0 <= idx < len(times):
                item["t"] = round(float(times[idx]), 2)
        stamped.append(item)
    return stamped


def apply_screening(verdict: dict[str, Any] | None, times: list[float], span: float) -> dict[str, Any]:
    """Turn one clip's vision verdict into accept/reject + usable segments.

    Binding (IZ/Yang 2026-10-26): burned-in source text is direct salvage
    (temporal LaMa fill), not cut/dodge-first. Burn-dominated clips are demoted
    for salvage even if a few clean frames remain. Wrong-product still hard-kills.
    ROI safety lives in burn_repair (hug subtitle bbox; no tall/face smear).
    """
    if not isinstance(verdict, dict):
        return {"accepted": False, "demoted": False, "reason": "视觉初筛无结果", "usable_segments": [], "frames": []}
    frames = _stamp_frame_times(verdict.get("frames") or [], times)
    by_index = {int(f.get("i", k)): f for k, f in enumerate(frames) if isinstance(f, dict)}
    if not verdict.get("same_product"):
        return {"accepted": False, "demoted": False, "usable_segments": [], "frames": frames,
                "reason": f"不是同款商品类型/形态：{verdict.get('form_factor_seen') or ''}；{verdict.get('reason') or ''}".strip("；")}
    half = span / (2 * len(times)) if times else 0
    segments: list[dict[str, Any]] = []
    burned_frames_cut = 0
    for index, t in enumerate(times):
        frame = by_index.get(index) or {}
        crop = frame_crop(frame)
        if crop is False:
            burned = str(frame.get("burned_text") or "none")
            if burned != "none" and frame.get("product_visible") and not frame.get("watermark") and not frame.get("lifestyle_only"):
                burned_frames_cut += 1
            continue
        start, end = max(0.0, t - half), min(span, t + half)
        prominent = bool(frame.get("product_prominent"))
        if segments and abs(segments[-1]["end_s"] - start) < 0.05 and segments[-1]["crop"] == crop:
            segments[-1]["end_s"] = round(end, 2)
            segments[-1]["product_prominent"] = segments[-1]["product_prominent"] or prominent
            segments[-1]["prominent_times"] += [t] if prominent else []
        else:
            segments.append({"start_s": round(start, 2), "end_s": round(end, 2), "product_prominent": prominent,
                             "prominent_times": [t] if prominent else [], "crop": crop})
    rejected_frames = {
        "burned_text": sum(1 for f in by_index.values() if str(f.get("burned_text") or "none") != "none"),
        "burned_text_heavy": sum(1 for f in by_index.values() if str(f.get("burned_text") or "none") == "heavy"),
        "burned_text_croppable": sum(1 for f in by_index.values() if frame_crop(f) in CROP_REGIONS),
        "watermark": sum(1 for f in by_index.values() if f.get("watermark")),
        "lifestyle_only": sum(1 for f in by_index.values() if f.get("lifestyle_only")),
        "no_product": sum(1 for f in by_index.values() if not f.get("product_visible")),
    }
    usable_s = sum(s["end_s"] - s["start_s"] for s in segments)
    base = {
        "frames": frames,
        "rejected_frames": rejected_frames,
        "burned_frames_cut": burned_frames_cut,
        "usable_s": round(usable_s, 2),
        "screen_times": [round(float(t), 2) for t in times],
    }
    direct = _burn_direct_salvage(rejected_frames, len(by_index))
    if direct or not segments:
        # Direct salvage when burn dominates, or when burn left no clean window.
        # Never treat that as a wrong-product hard kill.
        if direct or _burn_dominated(rejected_frames):
            from .burn_repair import text_regions_from_frames
            regions = text_regions_from_frames(list(by_index.values()))
            return {
                **base,
                "accepted": False,
                "demoted": True,
                "demote_reason": "burn_cut",
                "repair_candidate": True,
                "repair_regions": regions,
                "usable_segments": [],
                "reason": (
                    f"烧录文字直接 salvage（heavy={rejected_frames['burned_text_heavy']}, "
                    f"burned={rejected_frames['burned_text']}/{max(len(by_index), 1)}，"
                    f"帧问题 {rejected_frames}）— demote，LaMa 填补去字，不优先切/躲；非错品硬杀"
                ),
            }
        return {
            **base,
            "accepted": False,
            "demoted": False,
            "usable_segments": [],
            "reason": f"无可用片段（帧问题统计 {rejected_frames}）",
        }
    # Sparse burns only: keep the clean segments. Burn-dominated clips never reach here.
    reason = verdict.get("reason") or "同款且有可用片段"
    if burned_frames_cut:
        reason = f"{reason}（烧录文字帧 {burned_frames_cut} 个未进净段，保留净段 {usable_s:.1f}s）"
    return {
        **base,
        "accepted": True,
        "demoted": False,
        "usable_segments": segments,
        "reason": reason,
    }


@dataclass
class ScreenOutcome:
    ok: bool
    reason: str
    product_definition: str
    per_clip: dict[str, dict[str, Any]] = field(default_factory=dict)


def render_screen_prompt(request: dict[str, Any], product_definition: str, clips: list[dict[str, Any]]) -> str:
    listing = "\n".join(
        f"- 图 {k + 1}：clip_id={c['clip_id']}，帧时间（秒，按行从左到右，i=0..{len(c['screen_times']) - 1}）={c['screen_times']}"
        for k, c in enumerate(clips)
    )
    return (SCREEN_TEMPLATE.read_text(encoding="utf-8")
            .replace("{{product}}", str(request.get("product") or ""))
            .replace("{{market}}", str(request.get("market") or ""))
            .replace("{{product_definition}}", product_definition or "（未提供，按商品名称常识判断）")
            .replace("{{clip_listing}}", listing))


def screen_clips(request: dict[str, Any], clips: list[dict[str, Any]], *, product_definition: str, workdir: Path,
                 ffmpeg: str, codex_bin: str = "codex", model: str = "gpt-5.6-sol",
                 ask: Callable[..., Any] | None = None, min_accepted: int = 2, min_usable_s: float = 12.0,
                 min_prominent: int = 2) -> ScreenOutcome:
    """``clips`` are dicts with clip_id/path/duration. Adds screen grid paths/times in place."""
    grids: list[Path] = []
    for clip in clips:
        times, span = screen_times(float(clip["duration"]))
        grid = extract_frame_grid(Path(clip["path"]), times, workdir / "grids" / f"{clip['clip_id']}.jpg", ffmpeg=ffmpeg)
        clip.update({"screen_times": times, "screen_span": span, "screen_grid": str(grid)})
        grids.append(grid)
    prompt = render_screen_prompt(request, product_definition, clips)
    ask = ask or ask_vision
    payload = ask(prompt, grids, workdir=workdir, name="screen", codex_bin=codex_bin, model=model)
    if not isinstance(payload, dict):
        return ScreenOutcome(False, "视觉初筛（Codex）两次都没有返回可解析 JSON", product_definition)
    verdicts = {str(v.get("clip_id")): v for v in payload.get("clips") or [] if isinstance(v, dict)}
    outcome = ScreenOutcome(True, "", str(payload.get("product_definition") or product_definition))
    for clip in clips:
        decision = apply_screening(verdicts.get(str(clip["clip_id"])), clip["screen_times"], clip["screen_span"])
        decision["form_factor_seen"] = (verdicts.get(str(clip["clip_id"])) or {}).get("form_factor_seen")
        outcome.per_clip[str(clip["clip_id"])] = decision
    accepted = [d for d in outcome.per_clip.values() if d["accepted"]]
    usable_s = sum(s["end_s"] - s["start_s"] for d in accepted for s in d["usable_segments"])
    prominent = sum(1 for d in accepted for s in d["usable_segments"] if s["product_prominent"])
    summary = f"同款合格 {len(accepted)}/{len(clips)} 条，可用片段 {usable_s:.1f}s，商品突出片段 {prominent} 个"
    if len(accepted) < min_accepted or usable_s < min_usable_s or prominent < min_prominent:
        outcome.ok = False
        outcome.reason = f"质量初筛不足（需 ≥{min_accepted} 条、≥{min_usable_s:.0f}s、突出片段 ≥{min_prominent}）：{summary}"
    else:
        outcome.reason = f"质量初筛通过：{summary}"
    return outcome


# ----------------------------------------------------------------------------- after editing

def _segments_for(path: str, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = str(Path(path).resolve())
    for clip in clips:
        if str(Path(clip["path"]).resolve()) == target:
            return clip.get("usable_segments") or []
    return []


def check_shots(result: dict[str, Any], clips: list[dict[str, Any]]) -> list[str]:
    """Deterministic shot rules; returns a list of issues (empty = pass)."""
    from .batch_footage import (
        extract_per_video_results,
        within_video_near_dup_issues,
    )

    videos = extract_per_video_results(result, [])
    if not videos:
        return ["结果文件缺少 shots / videos 列表"]
    issues: list[str] = []
    for video in videos:
        v_index = int(video.get("video_index") or 0)
        prefix = "" if len(videos) == 1 else f"第 {v_index + 1} 条："
        shots = video.get("shots") or []
        if not shots:
            issues.append(f"{prefix}结果文件缺少 shots 列表（每个镜头的 path/source_in_s/duration_s/start_frame/duration_frames）")
            continue
        for index, shot in enumerate(shots):
            segs = _segments_for(str(shot.get("path") or ""), clips)
            start = float(shot.get("source_in_s") or 0)
            end = start + float(shot.get("duration_s") or 0)
            seg = next((s for s in segs if start >= s["start_s"] - SEGMENT_TOLERANCE_S and end <= s["end_s"] + SEGMENT_TOLERANCE_S), None)
            label = "开头" if index == 0 else ("结尾CTA" if index == len(shots) - 1 else f"第{index + 1}")
            if seg is None:
                issues.append(f"{prefix}{label}镜头 {Path(str(shot.get('path'))).name}@{start:.2f}-{end:.2f}s 不在初筛可用片段内")
            elif index in (0, len(shots) - 1) and not seg["product_prominent"]:
                issues.append(f"{prefix}{label}镜头 {Path(str(shot.get('path'))).name}@{start:.2f}s 所在片段商品不突出")
        for issue in within_video_near_dup_issues(shots):
            issues.append(prefix + issue)
    return issues


def render_review_prompt(request: dict[str, Any], product_definition: str, shots: list[dict[str, Any]],
                         captions: list[str], cta: str) -> str:
    listing = "\n".join(f"- 格 {k}（第 {k + 1} 个镜头{'，开头钩子' if k == 0 else ''}{'，结尾 CTA' if k == len(shots) - 1 else ''}）"
                        for k in range(len(shots)))
    return (REVIEW_TEMPLATE.read_text(encoding="utf-8")
            .replace("{{product}}", str(request.get("product") or ""))
            .replace("{{product_definition}}", product_definition or "")
            .replace("{{shot_listing}}", listing)
            .replace("{{captions}}", json.dumps(captions, ensure_ascii=False))
            .replace("{{cta}}", cta)
            .replace("{{overlay_list}}", json.dumps(ReviewOverlay.texts(captions, cta), ensure_ascii=False)))


# CTA wordings we overlay per market, derived from producer.CTA_EXAMPLE (single source of truth).
from .producer import CTA_EXAMPLE as _CTA_EXAMPLE  # noqa: E402  (producer imports vision lazily)

KNOWN_CTA_TEXTS = tuple(dict.fromkeys(_CTA_EXAMPLE.values()))


class ReviewOverlay:
    """Texts the export reviewer must treat as ours (not source burned text)."""

    @staticmethod
    def texts(captions: list[str], cta: str) -> list[str]:
        """Only THIS task's captions + THIS task's market CTA. Other markets' CTAs are NOT
        ignored: if one shows up in the footage it is source burned text."""
        seen: list[str] = []
        for text in [*captions, cta]:
            text = str(text or "").strip()
            if text and text not in seen:
                seen.append(text)
        return seen


def evaluate_review(payload: Any, n_shots: int) -> list[str]:
    if not isinstance(payload, dict):
        return ["成片视觉复核没有返回可解析 JSON"]
    by_index = {int(t.get("i", k)): t for k, t in enumerate(payload.get("tiles") or []) if isinstance(t, dict)}
    issues: list[str] = []
    for index in range(n_shots):
        tile = by_index.get(index)
        label = "开头" if index == 0 else ("结尾CTA" if index == n_shots - 1 else f"第{index + 1}")
        if tile is None:
            issues.append(f"{label}镜头缺少复核结果")
            continue
        if tile.get("product_visible") and tile.get("same_product") is False:
            issues.append(f"{label}镜头不是同款商品（{tile.get('note') or ''}）")
        if str(tile.get("source_burned_text") or "none") != "none":
            issues.append(f"{label}镜头有原片烧录文字（{tile.get('note') or tile.get('source_burned_text')}）")
        if tile.get("watermark"):
            issues.append(f"{label}镜头有水印（{tile.get('note') or ''}）")
        if tile.get("lifestyle_only"):
            issues.append(f"{label}镜头是纯生活方式画面，看不清商品")
        if index in (0, n_shots - 1) and not (tile.get("product_visible") and tile.get("product_prominent")):
            issues.append(f"{label}镜头没有清楚展示商品")
    return issues


def review_export(export: Path, result: dict[str, Any], request: dict[str, Any], *, product_definition: str,
                  workdir: Path, ffmpeg: str, cta: str, codex_bin: str = "codex", model: str = "gpt-5.6-sol",
                  fps: float = 30.0, ask: Callable[..., Any] | None = None, name: str = "review") -> tuple[list[str], Any]:
    shots = result.get("shots") or []
    if not shots:
        return ["结果文件缺少 shots 列表，无法逐镜头复核"], None
    times = []
    for shot in shots:
        start = float(shot.get("start_frame") or 0) / fps
        dur = float(shot.get("duration_frames") or (float(shot.get("duration_s") or 1) * fps)) / fps
        times.append(round(start + dur * 0.5, 3))
    grid = extract_frame_grid(export, times, workdir / f"{name}-grid.jpg", ffmpeg=ffmpeg, cols=5, width=240)
    prompt = render_review_prompt(request, product_definition, shots, list(result.get("caption_lines") or []), cta)
    ask = ask or ask_vision
    payload = ask(prompt, [grid], workdir=workdir, name=name, codex_bin=codex_bin, model=model)
    return evaluate_review(payload, len(shots)), payload
