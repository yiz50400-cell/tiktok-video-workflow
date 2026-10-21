"""Deterministic frame extraction and validation for human video observations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

import imageio_ffmpeg


KINDS = {"hook", "pain", "action", "proof", "rhythm", "cta", "risk"}
EVIDENCE_STATES = {"observed", "hypothesis", "unknown"}
PTS_RE = re.compile(r"\bpts_time:\s*([0-9.]+)")


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"FFmpeg timed out after {timeout}s") from exc
    if result.returncode:
        raise ValueError(f"FFmpeg failed: {result.stderr[-2000:]}")
    return result


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"Video does not exist: {path}")
    result = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    output = result.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    video_line = next((line for line in output.splitlines() if "Video:" in line), "")
    dimensions = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video_line)
    fps_match = re.search(r"\b([\d.]+) fps\b", video_line)
    codec_match = re.search(r"Video:\s*([\w]+)", video_line)
    if not duration_match or not dimensions:
        raise ValueError(f"Cannot probe video: {path}: {output[-800:]}")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise ValueError("Video has zero duration")
    return {
        "duration_seconds": duration,
        "width": int(dimensions.group(1)),
        "height": int(dimensions.group(2)),
        "fps": float(fps_match.group(1)) if fps_match else None,
        "video_codec": codec_match.group(1) if codec_match else "unknown",
        "has_audio": any("Audio:" in line for line in output.splitlines()),
        "size_bytes": path.stat().st_size,
    }


def detect_boundaries(path: Path, duration: float, threshold: float,
                      min_shot: float) -> list[float]:
    if not 0 < threshold < 1 or min_shot <= 0:
        raise ValueError("scene threshold must be (0,1), min shot must be positive")
    command = [_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "info", "-threads", "1",
               "-i", str(path), "-map", "0:v:0", "-an", "-vf",
               f"scale=160:-2,select=gt(scene\\,{threshold}),showinfo", "-f", "null", "-"]
    result = _run(command, timeout=max(180, math.ceil(duration * 8)))
    found = sorted(float(match.group(1)) for line in result.stderr.splitlines()
                   if "showinfo" in line for match in [PTS_RE.search(line)] if match)
    boundaries = [0.0]
    for time in found:
        if boundaries[-1] + min_shot <= time < duration - min_shot:
            boundaries.append(round(time, 3))
    boundaries.append(duration)
    return boundaries


def _sample_times(boundaries: list[float], interval: float, max_frames: int,
                  frame_margin: float = .01) -> list[float]:
    duration = boundaries[-1]
    if interval <= 0 or max_frames < 2:
        raise ValueError("sample interval must be positive and max frames >= 2")
    mids = [(start + end) / 2 for start, end in zip(boundaries, boundaries[1:])]
    last_seek = max(0, duration - frame_margin - .001)
    regular = [min(last_seek, i * interval + 0.1)
               for i in range(math.ceil(duration / interval))]
    candidates = sorted(set(round(max(0, min(last_seek, t)), 3) for t in mids + regular
                            if t < duration))
    if len(candidates) <= max_frames:
        return candidates
    # Retain timeline coverage when a long clip would otherwise produce too many stills.
    indexes = {round(i * (len(candidates) - 1) / (max_frames - 1))
               for i in range(max_frames)}
    return [candidates[i] for i in sorted(indexes)]


def _extract_frame(path: Path, time: float, output: Path) -> None:
    _run([_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss",
          f"{time:.3f}", "-i", str(path), "-map", "0:v:0", "-an",
          "-frames:v", "1", "-vf", "scale=180:-2", "-pix_fmt", "yuvj420p", "-q:v", "3", "-y",
          str(output)], timeout=60)
    if not output.is_file() or output.stat().st_size == 0:
        raise ValueError(f"No decoded frame at {time:.3f}s")


def _contact_sheets(frames: list[dict], output_dir: Path) -> list[dict]:
    sheets = []
    for page, start in enumerate(range(0, len(frames), 12), 1):
        page_frames = frames[start:start + 12]
        sheet = output_dir / f"contact-{page:02d}.jpg"
        # Page-local cell order is row-major; the manifest maps every cell to a source time.
        _run([_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
              "-framerate", "1", "-start_number", str(start + 1), "-i",
              str(output_dir / "frames" / "frame-%04d.jpg"), "-vf",
              "scale=180:320:force_original_aspect_ratio=decrease,"
              "pad=180:320:(ow-iw)/2:(oh-ih)/2:black,"
              "tile=4x3:padding=4:margin=4", "-frames:v", "1", "-pix_fmt", "yuvj420p", "-q:v", "3",
              "-y", str(sheet)], timeout=60)
        if not sheet.is_file():
            raise ValueError(f"Contact sheet not created: {sheet}")
        sheets.append({"path": str(sheet.resolve()), "cells": [
            {"row": i // 4 + 1, "column": i % 4 + 1,
             "frame_id": frame["id"], "source_time_seconds": frame["source_time_seconds"]}
            for i, frame in enumerate(page_frames)]})
    return sheets


def analyze_video(input_path: Path, output_dir: Path, *, source_url: str | None = None,
                  source_evidence: Path | None = None, threshold: float = 0.25,
                  min_shot: float = 0.35, interval: float = 3.0,
                  max_frames: int = 60) -> dict:
    input_path = input_path.resolve(strict=True)
    output_dir = output_dir.resolve()
    if source_evidence is not None:
        source_evidence = source_evidence.resolve(strict=True)
        if not source_evidence.is_file():
            raise ValueError(f"Source evidence is not a file: {source_evidence}")
    if output_dir == input_path.parent or input_path.is_relative_to(output_dir):
        raise ValueError("Output directory must not contain the source video")
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        raise ValueError(f"Analysis already exists; choose a new output directory: {manifest_path}")
    metadata = probe(input_path)
    duration = metadata["duration_seconds"]
    boundaries = detect_boundaries(input_path, duration, threshold, min_shot)
    fps = metadata.get('fps')
    times = _sample_times(boundaries, interval, max_frames,
                          frame_margin=max(.01, 1 / fps) if fps and fps > 0 else .1)
    frame_dir = output_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, time in enumerate(times, 1):
        frame_path = frame_dir / f"frame-{index:04d}.jpg"
        _extract_frame(input_path, time, frame_path)
        frames.append({"id": f"f{index:04d}", "source_time_seconds": time,
                       "path": str(frame_path.resolve()), "sha256": _sha256(frame_path)})
    sheets = _contact_sheets(frames, output_dir)
    shots = [{"id": f"s{i:03d}", "start_seconds": start, "end_seconds": end,
              "status": "visual_cut_candidate"}
             for i, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1)]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"path": str(input_path), "sha256": _sha256(input_path),
                   "source_url": source_url,
                   "source_evidence_path": str(source_evidence) if source_evidence else None,
                   "source_evidence_sha256": _sha256(source_evidence) if source_evidence else None},
        "metadata": metadata,
        "method": {"scene_detector": "ffmpeg select gt(scene, threshold)",
                   "scene_threshold": threshold, "min_shot_seconds": min_shot,
                   "sampling_interval_seconds": interval, "max_frames": max_frames,
                   "frame_time_basis": "requested FFmpeg seek point; decoded frame may differ by one frame",
                   "boundary_warning": "Visual cut candidates, not semantic action boundaries"},
        "media_analysis_status": {"asr": "not_run", "ocr": "not_run", "semantic": "awaiting_codex_observation"},
        "shots": shots, "frames": frames, "contact_sheets": sheets,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _validate_observations(manifest: dict, observations: dict) -> None:
    duration = manifest["metadata"]["duration_seconds"]
    source_sha = manifest["source"]["sha256"]
    if observations.get("source_sha256") != source_sha:
        raise ValueError("Observation source_sha256 does not match manifest")
    if not observations.get("observer") or not observations.get("observed_at"):
        raise ValueError("observer and observed_at are required")
    frame_times = {frame["id"]: frame["source_time_seconds"] for frame in manifest["frames"]}
    segments = observations.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments must be a nonempty list")
    for index, segment in enumerate(segments):
        prefix = f"segment {index + 1}"
        if segment.get("kind") not in KINDS:
            raise ValueError(f"{prefix}: invalid kind")
        if segment.get("evidence_status") not in EVIDENCE_STATES:
            raise ValueError(f"{prefix}: invalid evidence_status")
        start, end = segment.get("start_seconds"), segment.get("end_seconds")
        if (not isinstance(start, (int, float)) or isinstance(start, bool)
                or not isinstance(end, (int, float)) or isinstance(end, bool)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration + 0.02):
            raise ValueError(f"{prefix}: invalid time interval (duration {duration})")
        if not isinstance(segment.get("description"), str) or not segment["description"].strip():
            raise ValueError(f"{prefix}: description is required")
        ids = segment.get("frame_ids", [])
        if not isinstance(ids, list) or any(frame_id not in frame_times for frame_id in ids):
            raise ValueError(f"{prefix}: frame_ids must reference extracted frames")
        if any(not start - 0.05 <= frame_times[frame_id] <= end + 0.05 for frame_id in ids):
            raise ValueError(f"{prefix}: cited frame is outside the observation interval")
        if segment["evidence_status"] == "observed" and not ids:
            raise ValueError(f"{prefix}: observed visual claim requires frame_ids")
        if segment["evidence_status"] == "unknown" and ids:
            raise ValueError(f"{prefix}: unknown claim cannot cite visual proof")
    for name in ("known_facts", "popularity_hypotheses", "unknowns", "risks"):
        if not isinstance(observations.get(name), list):
            raise ValueError(f"{name} must be a list")


def _render_report(manifest: dict, observations: dict) -> str:
    source = manifest["source"]
    lines = ["# 视频分镜与广告结构观察", "",
             f"- 源文件：`{source['path']}`", f"- SHA-256：`{source['sha256']}`",
             f"- 时长：{manifest['metadata']['duration_seconds']:.2f} 秒",
             f"- 来源页：{source['source_url'] or '未提供'}",
             f"- 观察者：{observations['observer']}；时间：{observations['observed_at']}",
             "- 自动能力：场景切换候选与本地取帧；ASR 未执行；OCR 未执行；语义为人工/本会话画面观察。",
             "- 帧时间为 FFmpeg 请求的 seek 点，实际解码帧可差约一帧；剪辑入出点需回放核准。", "",
             "## 联系图", ""]
    for sheet in manifest["contact_sheets"]:
        lines.append(f"![联系图]({sheet['path']})")
        lines.append("；".join(f"r{cell['row']}c{cell['column']}={cell['frame_id']}@{cell['source_time_seconds']:.2f}s"
                              for cell in sheet["cells"]))
        lines.append("")
    lines += ["## 结构区间", "", "| 区间 | 类型 | 证据级别 | 观察/判断 | 帧 |",
              "|---|---|---|---|---|"]
    for segment in observations["segments"]:
        description = segment["description"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {segment['start_seconds']:.2f}–{segment['end_seconds']:.2f}s | "
                     f"{segment['kind']} | {segment['evidence_status']} | {description} | "
                     f"{', '.join(segment.get('frame_ids', [])) or '—'} |")
    for heading, key in (("已见事实", "known_facts"), ("高互动原因假设", "popularity_hypotheses"),
                         ("未知项", "unknowns"), ("可用性与风险", "risks")):
        lines += ["", f"## {heading}", ""]
        lines += [f"- {item}" for item in observations[key]] or ["- 无"]
    lines += ["", "## 镜头候选边界", "",
              ", ".join(f"{shot['start_seconds']:.2f}s" for shot in manifest["shots"][1:]) or "无",
              "", "镜头候选只来自像素变化；上脸、晕染等动作可跨越边界。", ""]
    return "\n".join(lines)


def save_observations(manifest_path: Path, observations_path: Path) -> dict:
    manifest_path = manifest_path.resolve(strict=True)
    observations_path = observations_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema_version")
    source = Path(manifest["source"]["path"])
    if not source.is_file() or _sha256(source) != manifest["source"]["sha256"]:
        raise ValueError("Source video missing or changed since inspection")
    _validate_observations(manifest, observations)
    output_dir = manifest_path.parent
    saved = output_dir / "observations.json"
    if observations_path != saved:
        saved.write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = output_dir / "report.md"
    report.write_text(_render_report(manifest, observations), encoding="utf-8")
    index = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": manifest["source"]["sha256"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "observations_path": str(saved),
        "observations_sha256": _sha256(saved),
        "report_path": str(report),
        "report_sha256": _sha256(report),
        "status": "codex_observed_needs_review",
        "asr_status": "not_run",
        "ocr_status": "not_run",
        "segments": len(observations["segments"]),
    }
    index_path = output_dir / "analysis.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"analysis_path": str(index_path), **index}
