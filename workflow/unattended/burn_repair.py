"""Salvage burned-in source text after cut/dodge fails.

Yang/IZ order (2026-10-26):
  1) Prefer clean cut/dodge segments (vision.apply_screening)
  2) Else remove/blur like watermark repair (ffmpeg delogo/boxblur; optional LaMa)
  3) Wrong-product stays a hard kill (never call this)

Outputs need visual review — same posture as workflow.repair.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Callable

TOP_FRAC = 0.26
BOTTOM_FRAC = 0.26
MIDDLE_Y0 = 0.28
MIDDLE_FRAC = 0.44
DEFAULT_LAMA_MODEL = Path(".runtime/models/big-lama.pt")


def text_regions_from_frames(frames: list[dict[str, Any]]) -> list[str]:
    counts: Counter[str] = Counter()
    for frame in frames or []:
        if str(frame.get("burned_text") or "none") == "none":
            continue
        region = str(frame.get("text_region") or "middle")
        if region in ("top", "bottom", "middle"):
            counts[region] += 1
    if not counts:
        return ["bottom"]
    order = {"bottom": 0, "top": 1, "middle": 2}
    return sorted(counts.keys(), key=lambda r: (order.get(r, 9), -counts[r]))


def band_box(region: str, width: int, height: int) -> list[int]:
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be positive")
    if region == "top":
        h = max(8, int(round(height * TOP_FRAC)))
        return [0, 0, width, h]
    if region == "bottom":
        h = max(8, int(round(height * BOTTOM_FRAC)))
        return [0, max(0, height - h), width, h]
    h = max(8, int(round(height * MIDDLE_FRAC)))
    y = max(0, int(round(height * MIDDLE_Y0)))
    if y + h > height:
        y = max(0, height - h)
    return [0, y, width, h]


def mask_spec_from_frames(
    frames: list[dict[str, Any]],
    width: int,
    height: int,
    *,
    regions: list[str] | None = None,
) -> dict[str, Any]:
    regions = regions or text_regions_from_frames(frames)
    boxes = []
    seen: set[str] = set()
    for region in regions:
        if region in seen:
            continue
        seen.add(region)
        boxes.append({"name": f"burn_{region}", "mode": "box", "box": band_box(region, width, height)})
    if not boxes:
        boxes.append({"name": "burn_bottom", "mode": "box", "box": band_box("bottom", width, height)})
    return {"dimensions": [int(width), int(height)], "regions": boxes, "purpose": "burned_text_salvage"}


def repair_hint_from_decision(decision: dict[str, Any], width: int, height: int) -> dict[str, Any] | None:
    if not decision.get("demoted") or decision.get("demote_reason") != "burn_cut":
        return None
    frames = decision.get("frames") or []
    regions = text_regions_from_frames(frames)
    return {
        "engine_order": ["delogo", "boxblur", "lama"],
        "regions": regions,
        "mask_spec": mask_spec_from_frames(frames, width, height, regions=regions),
        "needs_visual_review": True,
        "note": "cut/dodge left no clean segment — remove/blur burned text like watermark repair",
    }


def write_mask_spec(spec: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _inset_box(box: list[int], width: int, height: int, margin: int = 2) -> list[int]:
    x, y, w, h = box
    x2, y2 = x + w, y + h
    x = max(margin, x)
    y = max(margin, y)
    x2 = min(width - margin, x2)
    y2 = min(height - margin, y2)
    w = max(2, (x2 - x) - (x2 - x) % 2)
    h = max(2, (y2 - y) - (y2 - y) % 2)
    return [x, y, w, h]


def _delogo_filter(regions: list[dict[str, Any]], width: int, height: int) -> str:
    parts = []
    for region in regions:
        x, y, w, h = _inset_box(region["box"], width, height)
        parts.append(f"delogo=x={x}:y={y}:w={w}:h={h}:show=0")
    return ",".join(parts)


def _boxblur_filter_labeled(regions: list[dict[str, Any]]) -> str:
    n = len(regions)
    if n == 1:
        x, y, w, h = regions[0]["box"]
        return (
            f"[0:v]crop={w}:{h}:{x}:{y},boxblur=10:1[b];"
            f"[0:v][b]overlay={x}:{y}[outv]"
        )
    splits = "".join(f"[s{i}]" for i in range(n))
    filters = [f"[0:v]split={n}{splits}"]
    last = "[0:v]"
    for i, region in enumerate(regions):
        x, y, w, h = region["box"]
        filters.append(f"[s{i}]crop={w}:{h}:{x}:{y},boxblur=10:1[b{i}]")
        label = "[outv]" if i == n - 1 else f"[v{i}]"
        filters.append(f"{last}[b{i}]overlay={x}:{y}{label}")
        last = f"[v{i}]"
    return ";".join(filters)


def remove_or_blur_burn(
    source: Path,
    mask_spec: dict[str, Any],
    output: Path,
    *,
    ffmpeg: str,
    engine: str = "delogo",
) -> dict[str, Any]:
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    regions = list(mask_spec.get("regions") or [])
    if not regions:
        raise ValueError("mask_spec.regions required")
    if engine == "delogo":
        width, height = int(mask_spec["dimensions"][0]), int(mask_spec["dimensions"][1])
        vf = _delogo_filter(regions, width, height)
        cmd = [ffmpeg, "-y", "-i", str(source), "-vf", vf, "-c:a", "copy", str(output)]
    elif engine == "boxblur":
        fc = _boxblur_filter_labeled(regions)
        cmd = [
            ffmpeg, "-y", "-i", str(source),
            "-filter_complex", fc, "-map", "[outv]", "-map", "0:a?",
            "-c:a", "copy", str(output),
        ]
    else:
        raise ValueError(f"unsupported ffmpeg engine: {engine}")
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size < 1000:
        raise RuntimeError(
            f"{engine} failed rc={completed.returncode}: {(completed.stderr or '')[-800:]}"
        )
    return {
        "ok": True,
        "engine": engine,
        "output": str(output),
        "needs_visual_review": True,
        "cmd": cmd,
    }


def lama_repair_burn(
    source: Path,
    spec_path: Path,
    output_root: Path,
    *,
    model: Path | None = None,
    start: float = 0.0,
    duration: float | None = None,
    device: str = "auto",
) -> dict[str, Any]:
    from workflow.repair import run_repair

    model = Path(model or DEFAULT_LAMA_MODEL)
    if not model.is_file():
        repo = Path(__file__).resolve().parents[2]
        candidate = repo / DEFAULT_LAMA_MODEL
        if candidate.is_file():
            model = candidate
    if not model.is_file():
        raise FileNotFoundError(f"LaMa model missing: {model}")
    if duration is None:
        raise ValueError("duration required for lama_repair_burn")
    record = run_repair(
        Path(source), Path(spec_path), model, Path(output_root),
        float(start), float(duration), device=device,
    )
    repaired = Path(record.get("outputs", {}).get("repaired.mp4", {}).get("path") or "")
    if not repaired.is_file():
        task_id = record.get("task_id")
        if task_id:
            repaired = Path(output_root) / task_id / "repaired.mp4"
    return {
        "ok": record.get("status") == "NEEDS_VISUAL_REVIEW" or repaired.is_file(),
        "engine": "lama",
        "output": str(repaired) if repaired.is_file() else None,
        "task": record,
        "needs_visual_review": True,
    }


def full_span_segments(duration: float, *, product_prominent: bool = True) -> list[dict[str, Any]]:
    return [{
        "start_s": 0.0,
        "end_s": round(float(duration), 2),
        "product_prominent": product_prominent,
        "prominent_times": [round(float(duration) * 0.5, 2)],
        "crop": None,
        "salvaged": True,
    }]


def salvage_demoted_burn(
    decision: dict[str, Any],
    source: Path,
    *,
    width: int,
    height: int,
    duration: float,
    workdir: Path,
    ffmpeg: str,
    engines: tuple[str, ...] = ("delogo", "boxblur", "lama"),
    lama_model: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = log or (lambda _m: None)
    if decision.get("accepted"):
        return decision
    if not decision.get("demoted") or decision.get("demote_reason") != "burn_cut":
        return decision

    frames = decision.get("frames") or []
    hint = repair_hint_from_decision(decision, width, height) or {
        "mask_spec": mask_spec_from_frames(frames, width, height),
        "regions": text_regions_from_frames(frames),
    }
    decision = {**decision, "repair_hint": hint}
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    spec = hint["mask_spec"]
    spec_path = write_mask_spec(spec, workdir / "burn_mask_spec.json")
    errors: list[str] = []

    for engine in engines:
        try:
            if engine in ("delogo", "boxblur"):
                out = workdir / f"salvaged_{engine}.mp4"
                result = remove_or_blur_burn(source, spec, out, ffmpeg=ffmpeg, engine=engine)
            elif engine == "lama":
                dur = min(float(duration), 12.0)
                result = lama_repair_burn(
                    source, spec_path, workdir / "lama",
                    model=lama_model, start=0.0, duration=dur,
                )
                if not result.get("ok") or not result.get("output"):
                    raise RuntimeError(f"lama produced no output: {result}")
            else:
                continue
            salvaged = Path(result["output"])
            if not salvaged.is_file():
                raise RuntimeError("missing salvaged file")
            prominent = any(bool(f.get("product_prominent")) for f in frames) or True
            updated = {
                **decision,
                "accepted": True,
                "demoted": False,
                "salvaged": True,
                "salvage_engine": result.get("engine") or engine,
                "salvaged_path": str(salvaged),
                "usable_segments": full_span_segments(duration, product_prominent=bool(prominent)),
                "usable_s": round(float(duration), 2),
                "needs_visual_review": True,
                "reason": (
                    f"烧录文字切/躲不足，已用 {result.get('engine') or engine} 去字/模糊 salvage"
                    f"（regions={hint.get('regions')}，需目视复核）"
                ),
                "repair_hint": hint,
                "salvage_errors": errors,
            }
            log(f"burn salvage ok {Path(source).name} via {engine} → {salvaged.name}")
            return updated
        except Exception as exc:
            errors.append(f"{engine}: {exc}")
            log(f"burn salvage {engine} failed: {exc}")
    return {**decision, "salvage_attempted": True, "salvage_errors": errors, "repair_hint": hint}
