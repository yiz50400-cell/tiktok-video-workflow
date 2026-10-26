"""Direct salvage for burned-in source subtitles/text.

IZ/Yang (2026-10-26):
  1) Burned-in text → direct salvage. Do not prefer cut/dodge and do not skip treatment.
  2) Wrong-product / blacklist stays a hard kill (never call this).
  3) ROI hugs the subtitle/text bbox only (MAX_BAND_FRAC, assert_safe_roi).
     Ban tall / full-frame / face smear (v5b cream smear).

Look: fill / inpaint (LaMa) so glyphs and the subtitle bar are gone and skin
stays natural. IZ rejected gray soft boxblur / hue-desat bars and rejected
soft purple-frost transparent blur as the deliverable. Engine order is lama,
then a plain boxblur only if the weights are missing, then delogo last
because its border interpolation stretches colored chips. Neither fallback
is the default, and the boxblur fallback does not desaturate.

Temporal ROI plans carry intervals. LaMa paints a box only inside those
intervals (workflow.repair.intervals). Outputs need visual review.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Callable

# Thin subtitle bands only — old TOP/BOTTOM 0.26 + middle 0.44@y0.28 painted faces.
TOP_FRAC = 0.12
BOTTOM_FRAC = 0.14
MIDDLE_Y0 = 0.62  # lower-third caption (not face/center)
MIDDLE_FRAC = 0.14
MAX_BAND_FRAC = 0.15  # hard cap any single region height vs frame
FACE_ZONE_Y0 = 0.18
FACE_ZONE_Y1 = 0.55
MAX_FACE_OVERLAP_FRAC = 0.08  # max overlap with face zone vs frame height
DEFAULT_LAMA_MODEL = Path(".runtime/models/big-lama.pt")
# Fill first. Boxblur and delogo are explicit degraded fallbacks, never the look.
DEFAULT_SALVAGE_ENGINES = ("lama", "boxblur", "delogo")
# Fallback only, when LaMa weights are missing. No hue=s=0 (that painted gray bars).
FALLBACK_BLUR_RADIUS = 10
FALLBACK_BLUR_POWER = 2
# Neighboring ROIs overlap so glyphs do not flash in the handoff gap.
ROI_OVERLAP_S = 0.25
TRACK_IOU = 0.45


class UnsafeBurnRoiError(ValueError):
    """Raised when a delogo/boxblur box would smear face/center or exceed band caps."""


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


def _box_xyxy(box: list[int]) -> tuple[int, int, int, int]:
    x, y, w, h = (int(v) for v in box)
    return x, y, x + w, y + h


def face_overlap_height(box: list[int], height: int) -> int:
    """Pixels of vertical overlap between box and the face/center danger zone."""
    _x, y0, _x2, y1 = _box_xyxy(box)
    face0 = int(round(height * FACE_ZONE_Y0))
    face1 = int(round(height * FACE_ZONE_Y1))
    return max(0, min(y1, face1) - max(y0, face0))


def assert_safe_roi(
    box: list[int],
    width: int,
    height: int,
    *,
    name: str = "roi",
) -> list[int]:
    """Refuse tall / center-face boxes that would paint people (IZ v5b smear ban)."""
    width, height = int(width), int(height)
    if len(box) != 4:
        raise UnsafeBurnRoiError(f"{name}: box must be [x,y,w,h], got {box!r}")
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        raise UnsafeBurnRoiError(f"{name}: non-positive size {box}")
    if x < 0 or y < 0 or x + w > width + 1 or y + h > height + 1:
        raise UnsafeBurnRoiError(f"{name}: box out of frame {box} vs {width}x{height}")
    max_h = max(8, int(round(height * MAX_BAND_FRAC)))
    if h > max_h:
        raise UnsafeBurnRoiError(
            f"{name}: height {h} exceeds MAX_BAND_FRAC={MAX_BAND_FRAC} "
            f"(max {max_h}px on {height}px frame) — refuse tall/full-frame smear"
        )
    overlap = face_overlap_height(box, height)
    max_overlap = max(4, int(round(height * MAX_FACE_OVERLAP_FRAC)))
    if overlap > max_overlap and h > int(round(height * 0.10)):
        raise UnsafeBurnRoiError(
            f"{name}: overlaps face/center zone by {overlap}px "
            f"(>{max_overlap}px allowed) with h={h} — refuse half-face smear"
        )
    return [x, y, w, h]


def filter_safe_regions(
    regions: list[dict[str, Any]],
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Keep only ROI boxes that pass assert_safe_roi; drop unsafe ones."""
    safe: list[dict[str, Any]] = []
    for region in regions or []:
        box = region.get("box")
        if not box:
            continue
        try:
            cleaned = assert_safe_roi(list(box), width, height, name=str(region.get("name") or "roi"))
        except UnsafeBurnRoiError:
            continue
        safe.append({**region, "box": cleaned})
    return safe


def band_box(region: str, width: int, height: int) -> list[int]:
    """Return [x, y, w, h] hugging a thin subtitle band. Never a tall center box."""
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be positive")
    max_h = max(8, int(round(height * MAX_BAND_FRAC)))
    if region == "top":
        h = min(max(8, int(round(height * TOP_FRAC))), max_h)
        box = [0, 0, width, h]
    elif region == "bottom":
        h = min(max(8, int(round(height * BOTTOM_FRAC))), max_h)
        box = [0, max(0, height - h), width, h]
    else:
        # "middle" → lower-third caption strip (avoid face/center)
        h = min(max(8, int(round(height * MIDDLE_FRAC))), max_h)
        y = max(0, int(round(height * MIDDLE_Y0)))
        if y + h > height:
            y = max(0, height - h)
        box = [0, y, width, h]
    return assert_safe_roi(box, width, height, name=f"band:{region}")



def _coerce_text_bbox(raw: Any, width: int, height: int) -> list[int] | None:
    """Accept [x,y,w,h] pixels or normalized 0..1 floats; return pixel box or None."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        if all(k in raw for k in ("x", "y", "w", "h")):
            raw = [raw["x"], raw["y"], raw["w"], raw["h"]]
        elif all(k in raw for k in ("x", "y", "width", "height")):
            raw = [raw["x"], raw["y"], raw["width"], raw["height"]]
        else:
            return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    vals = [float(v) for v in raw]
    # normalized if all coords look like fractions
    if max(vals) <= 1.5:
        x = int(round(vals[0] * width))
        y = int(round(vals[1] * height))
        w = int(round(vals[2] * width))
        h = int(round(vals[3] * height))
    else:
        x, y, w, h = (int(round(v)) for v in vals)
    # even sizes help ffmpeg delogo
    w = max(2, w - (w % 2))
    h = max(2, h - (h % 2))
    return [x, y, w, h]


def text_bboxes_from_frames(frames: list[dict[str, Any]], width: int, height: int) -> list[list[int]]:
    """Collect safe pixel bboxes from screening frame text_bbox / bbox fields."""
    out: list[list[int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for frame in frames or []:
        if str(frame.get("burned_text") or "none") == "none":
            continue
        raw = frame.get("text_bbox") or frame.get("bbox") or frame.get("text_box")
        box = _coerce_text_bbox(raw, width, height)
        if not box:
            continue
        try:
            box = assert_safe_roi(box, width, height, name="text_bbox")
        except UnsafeBurnRoiError:
            continue
        key = tuple(box)
        if key in seen:
            continue
        seen.add(key)
        out.append(box)
    return out


def _iou(a: list[int], b: list[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _union_box(a: list[int], b: list[int]) -> list[int]:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1 = max(a[0] + a[2], b[0] + b[2])
    y1 = max(a[1] + a[3], b[1] + b[3])
    return [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((float(a), float(b)) for a, b in intervals if float(b) > float(a))
    if not ordered:
        return []
    merged: list[list[float]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(start, 2), round(end, 2)) for start, end in merged]


def _half_step(grid: list[float]) -> float:
    if len(grid) < 2:
        return 0.5
    gaps = [grid[i + 1] - grid[i] for i in range(len(grid) - 1) if grid[i + 1] > grid[i]]
    if not gaps:
        return 0.5
    gaps.sort()
    return max(0.15, gaps[len(gaps) // 2] / 2)


def _frame_time(frame: dict[str, Any], index: int, times: list[float] | None) -> float | None:
    if frame.get("t") is not None:
        return float(frame["t"])
    if frame.get("time_s") is not None:
        return float(frame["time_s"])
    idx = int(frame.get("i", index))
    if times and 0 <= idx < len(times):
        return float(times[idx])
    return None


def _sample_window(t: float, half: float, duration: float | None) -> tuple[float, float]:
    start = max(0.0, t - half)
    end = t + half
    if duration is not None:
        end = min(float(duration), end)
    if end <= start:
        end = start + 0.2
    return start, end


def _with_overlap(intervals: list[tuple[float, float]], duration: float | None) -> list[tuple[float, float]]:
    """Expand each window by half of ROI_OVERLAP_S so neighbors overlap ~0.25s."""
    pad = ROI_OVERLAP_S / 2.0
    expanded: list[tuple[float, float]] = []
    for start, end in intervals:
        start2 = max(0.0, start - pad)
        end2 = end + pad
        if duration is not None:
            end2 = min(float(duration), end2)
        expanded.append((start2, end2))
    return _merge_intervals(expanded)


def _raw_boxes(frame: dict[str, Any]) -> list[Any]:
    multi = frame.get("text_bboxes")
    if isinstance(multi, list) and multi:
        return list(multi)
    raw = frame.get("text_bbox") or frame.get("bbox") or frame.get("text_box")
    return [raw] if raw is not None else []


def _safe_box(raw: Any, width: int, height: int) -> list[int] | None:
    box = _coerce_text_bbox(raw, width, height)
    if not box:
        return None
    try:
        return assert_safe_roi(box, width, height, name="text_bbox")
    except UnsafeBurnRoiError:
        return None


def temporal_text_regions(
    frames: list[dict[str, Any]],
    width: int,
    height: int,
    *,
    times: list[float] | None = None,
    duration: float | None = None,
) -> list[dict[str, Any]]:
    """Cluster text bboxes into tight tracks with enable windows.

    Same-place chips share one ROI across the samples where they appear.
    A moved chip (different bbox) becomes another ROI. Windows overlap by
    ROI_OVERLAP_S so the handoff does not flash readable text. Boxes that
    fail assert_safe_roi are dropped — never grown into a tall/face smear.
    """
    width, height = int(width), int(height)
    samples: list[dict[str, Any]] = []
    grid: list[float] = [float(t) for t in (times or [])]
    for index, frame in enumerate(frames or []):
        if not isinstance(frame, dict):
            continue
        t = _frame_time(frame, index, times)
        if t is not None:
            grid.append(t)
        if str(frame.get("burned_text") or "none") == "none":
            continue
        for raw in _raw_boxes(frame):
            box = _safe_box(raw, width, height)
            if box:
                samples.append({"t": t, "box": box})
    if not samples:
        return []
    grid = sorted({round(t, 3) for t in grid})
    half = _half_step(grid) if any(sample["t"] is not None for sample in samples) else None
    duration_f = float(duration) if duration is not None else None
    clusters: list[dict[str, Any]] = []
    for sample in samples:
        box = sample["box"]
        window = _sample_window(sample["t"], half, duration_f) if sample["t"] is not None and half is not None else None
        placed = False
        for cluster in clusters:
            if _iou(cluster["box"], box) < TRACK_IOU:
                continue
            try:
                union = assert_safe_roi(_union_box(cluster["box"], box), width, height, name="text_bbox_union")
            except UnsafeBurnRoiError:
                continue
            cluster["box"] = union
            if window:
                cluster["windows"].append(window)
            placed = True
            break
        if not placed:
            clusters.append({"box": box, "windows": [window] if window else []})
    regions: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters):
        intervals = _with_overlap(cluster["windows"], duration_f) if cluster["windows"] else []
        region: dict[str, Any] = {"name": f"burn_bbox_{index}", "mode": "box", "box": cluster["box"]}
        if intervals:
            region["intervals"] = [[start, end] for start, end in intervals]
            region["t0"] = intervals[0][0]
            region["t1"] = intervals[-1][1]
        regions.append(region)
    return regions


def _attach_band_intervals(
    region_name: str,
    box: list[int],
    frames: list[dict[str, Any]],
    times: list[float] | None,
    duration: float | None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"name": f"burn_{region_name}", "mode": "box", "box": box}
    if not times and not any(isinstance(f, dict) and (f.get("t") is not None or f.get("time_s") is not None) for f in frames or []):
        return item
    grid = [float(t) for t in (times or [])]
    hits: list[float] = []
    for index, frame in enumerate(frames or []):
        if not isinstance(frame, dict) or str(frame.get("burned_text") or "none") == "none":
            continue
        where = str(frame.get("text_region") or "")
        if where != region_name:
            continue
        t = _frame_time(frame, index, times)
        if t is None:
            continue
        hits.append(t)
        grid.append(t)
    if not hits:
        return item
    grid = sorted({round(t, 3) for t in grid})
    half = _half_step(grid)
    duration_f = float(duration) if duration is not None else None
    windows = [_sample_window(t, half, duration_f) for t in hits]
    intervals = _with_overlap(windows, duration_f)
    if intervals:
        item["intervals"] = [[start, end] for start, end in intervals]
        item["t0"] = intervals[0][0]
        item["t1"] = intervals[-1][1]
    return item


def mask_spec_from_frames(
    frames: list[dict[str, Any]],
    width: int,
    height: int,
    *,
    regions: list[str] | None = None,
    times: list[float] | None = None,
    duration: float | None = None,
) -> dict[str, Any]:
    """Build a salvage mask. Prefer tight temporal text_bbox tracks; else thin bands.

    Each region may carry intervals [[t0, t1], ...] so LaMa fills the subtitle
    only while it is on screen. Bboxes that fail assert_safe_roi are dropped.
    """
    width, height = int(width), int(height)
    timed = temporal_text_regions(frames, width, height, times=times, duration=duration)
    if timed:
        return {
            "dimensions": [width, height],
            "regions": timed,
            "purpose": "burned_text_salvage",
            "preferred_engine": "lama",
        }
    # Fallback: thin top/bottom/lower-third bands (never a tall center box).
    selected = list(regions or text_regions_from_frames(frames))
    # Full-width "middle" still paints cheeks on face close-ups,
    # so only keep middle when no top/bottom signal.
    if "top" in selected or "bottom" in selected:
        selected = [r for r in selected if r in ("top", "bottom")]
    boxes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for region in selected:
        if region in seen:
            continue
        seen.add(region)
        try:
            box = band_box(region, width, height)
        except UnsafeBurnRoiError:
            continue
        boxes.append(_attach_band_intervals(region, box, frames, times, duration))
    if not boxes:
        boxes.append({
            "name": "burn_bottom",
            "mode": "box",
            "box": band_box("bottom", width, height),
        })
    return {
        "dimensions": [width, height],
        "regions": boxes,
        "purpose": "burned_text_salvage",
        "preferred_engine": "lama",
    }


def repair_hint_from_decision(
    decision: dict[str, Any],
    width: int,
    height: int,
    *,
    duration: float | None = None,
) -> dict[str, Any] | None:
    if not decision.get("demoted") or decision.get("demote_reason") != "burn_cut":
        return None
    frames = decision.get("frames") or []
    regions = text_regions_from_frames(frames)
    explicit = (decision.get("repair_hint") or {}).get("mask_spec")
    if isinstance(explicit, dict) and explicit.get("regions"):
        spec = explicit
    else:
        spec = mask_spec_from_frames(
            frames, width, height, regions=regions,
            times=decision.get("screen_times"), duration=duration,
        )
    return {
        "engine_order": list(DEFAULT_SALVAGE_ENGINES),
        "regions": regions,
        "mask_spec": spec,
        "needs_visual_review": True,
        "note": (
            "burned-in text → direct temporal salvage. Final look is LaMa fill "
            "inside hug-ROI intervals (text gone, skin kept). Boxblur/delogo are "
            "degraded fallbacks only: no hue-desat gray bars, and transparent "
            "blur is not the default. not cut/dodge-first"
        ),
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


def _region_intervals(region: dict[str, Any]) -> list[tuple[float, float]]:
    raw = region.get("intervals")
    if isinstance(raw, list) and raw:
        out: list[tuple[float, float]] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out.append((float(item[0]), float(item[1])))
        return out
    if region.get("t0") is not None and region.get("t1") is not None:
        return [(float(region["t0"]), float(region["t1"]))]
    return []


def _enable_clause(region: dict[str, Any]) -> str:
    """ffmpeg timeline enable. Commas are escaped so the filtergraph does not split."""
    intervals = _region_intervals(region)
    if not intervals:
        return ""
    parts = [f"between(t\\,{start:.2f}\\,{end:.2f})" for start, end in intervals]
    return ":enable='" + "+".join(parts) + "'"


def _boxblur_steps(w: int, h: int) -> str:
    """Degraded fallback only. Not the default look.

    One boxblur of the ROI's own pixels when LaMa weights are missing.
    ``hue=s=0`` is omitted: that desaturation painted the gray bars IZ banned.
    This is not the purple-frost transparent-blur default.
    """
    radius = min(FALLBACK_BLUR_RADIUS, max(1, min(int(w), int(h)) - 1))
    return f"boxblur={radius}:{FALLBACK_BLUR_POWER}"


def _delogo_filter(regions: list[dict[str, Any]], width: int, height: int) -> str:
    parts = []
    for region in regions:
        x, y, w, h = _inset_box(region["box"], width, height)
        parts.append(f"delogo=x={x}:y={y}:w={w}:h={h}:show=0{_enable_clause(region)}")
    return ",".join(parts)


def _boxblur_filter_labeled(regions: list[dict[str, Any]], width: int = 0, height: int = 0) -> str:
    """Degraded fallback graph: one boxblur, overlay enable=between(t,t0,t1). No hue."""
    n = len(regions)
    splits = "".join(f"[s{i}]" for i in range(n))
    filters = [f"[0:v]split={n + 1}[base]{splits}"]
    last = "[base]"
    for i, region in enumerate(regions):
        x, y, w, h = (int(v) for v in region["box"])
        if width and x + w > width:
            w = max(2, width - x)
        if height and y + h > height:
            h = max(2, height - y)
        filters.append(f"[s{i}]crop={w}:{h}:{x}:{y},{_boxblur_steps(w, h)}[b{i}]")
        label = "[outv]" if i == n - 1 else f"[v{i}]"
        filters.append(f"{last}[b{i}]overlay={x}:{y}{_enable_clause(region)}{label}")
        last = f"[v{i}]"
    return ";".join(filters)


def remove_or_blur_burn(
    source: Path,
    mask_spec: dict[str, Any],
    output: Path,
    *,
    ffmpeg: str,
    engine: str = "boxblur",
) -> dict[str, Any]:
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = int(mask_spec["dimensions"][0]), int(mask_spec["dimensions"][1])
    regions = filter_safe_regions(list(mask_spec.get("regions") or []), width, height)
    if not regions:
        raise UnsafeBurnRoiError(
            "mask_spec.regions empty or all unsafe (tall/face-center ROI refused)"
        )
    if engine == "delogo":
        vf = _delogo_filter(regions, width, height)
        cmd = [ffmpeg, "-y", "-i", str(source), "-vf", vf, "-c:a", "copy", str(output)]
    elif engine == "boxblur":
        fc = _boxblur_filter_labeled(regions, width, height)
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


def resolve_lama_model(model: Path | None = None) -> Path:
    """Return an existing LaMa weight file, or raise FileNotFoundError.

    An explicit ``model`` path is not replaced with a different file.
    The default search is cwd-relative ``.runtime/models/big-lama.pt`` and the
    same path under the repo root.
    """
    repo = Path(__file__).resolve().parents[2]
    if model:
        requested = Path(model)
        candidates = [requested] if requested.is_absolute() else [requested, repo / requested]
    else:
        candidates = [DEFAULT_LAMA_MODEL, repo / DEFAULT_LAMA_MODEL]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "LaMa model missing: " + ", ".join(str(path) for path in candidates)
        + " — burn salvage look is fill/inpaint; pass --model"
    )


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
    model = resolve_lama_model(model)
    from workflow.repair import run_repair

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
    engines: tuple[str, ...] = DEFAULT_SALVAGE_ENGINES,
    lama_model: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = log or (lambda _m: None)
    if decision.get("accepted"):
        return decision
    if not decision.get("demoted") or decision.get("demote_reason") != "burn_cut":
        return decision

    frames = decision.get("frames") or []
    hint = repair_hint_from_decision(decision, width, height, duration=duration) or {
        "mask_spec": mask_spec_from_frames(
            frames, width, height, times=decision.get("screen_times"), duration=duration,
        ),
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
                dur = float(duration)
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
            if frames:
                prominent = any(bool(f.get("product_prominent")) for f in frames)
            else:
                prominent = True
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
                    "烧录文字直接 salvage："
                    + (
                        "LaMa 填补去字（贴 ROI，皮肤保留，需目视复核）"
                        if (result.get("engine") or engine) == "lama"
                        else f"{result.get('engine') or engine} 降级（LaMa 未用上，需目视复核）"
                    )
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


def mask_from_plan(plan: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Turn a hand ROI plan into a mask spec. Each box must pass assert_safe_roi."""
    regions: list[dict[str, Any]] = []
    for index, item in enumerate(plan.get("regions") or []):
        box = assert_safe_roi(list(item["box"]), width, height, name=str(item.get("name") or f"plan_{index}"))
        region: dict[str, Any] = {
            "name": str(item.get("name") or f"plan_{index}"),
            "mode": "box",
            "box": box,
        }
        if item.get("intervals"):
            region["intervals"] = item["intervals"]
        elif item.get("t0") is not None and item.get("t1") is not None:
            region["t0"] = float(item["t0"])
            region["t1"] = float(item["t1"])
            region["intervals"] = [[float(item["t0"]), float(item["t1"])]]
        regions.append(region)
    if not regions:
        raise UnsafeBurnRoiError("ROI plan has no regions")
    return {
        "dimensions": [int(width), int(height)],
        "regions": regions,
        "purpose": "burned_text_salvage",
        "preferred_engine": "lama",
    }


def _probe_video(path: Path, ffmpeg: str) -> tuple[int, int, float]:
    from .collect import probe_media

    probe = probe_media(Path(path), ffmpeg=ffmpeg)
    if not probe.width or not probe.height or not probe.duration:
        raise RuntimeError(f"could not probe {path}")
    return int(probe.width), int(probe.height), float(probe.duration)


def main(argv: list[str] | None = None) -> int:
    """Re-run burn salvage on one clip. Default engine is LaMa fill.

    Examples:
      python -m workflow.unattended.burn_repair --source CLIP.mp4 --plan roi_plan.json --model /path/big-lama.pt --workdir ./burn_out
      python -m workflow.unattended.burn_repair --source CLIP.mp4 --decision decision.json --model /path/big-lama.pt --workdir ./burn_out
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Salvage burned-in subtitles with LaMa fill on a temporal hug-ROI plan"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--decision", type=Path, help="screening decision JSON (demote_reason=burn_cut)")
    parser.add_argument("--plan", type=Path, help="ROI plan JSON {regions:[{box:[x,y,w,h], t0, t1}]}")
    parser.add_argument("--model", type=Path, help="LaMa weights. Default: .runtime/models/big-lama.pt")
    parser.add_argument("--engine", choices=("lama", "boxblur", "delogo"), default="lama",
                        help="lama is the look. boxblur/delogo are explicit degraded fallbacks")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args(argv)
    if not args.plan and not args.decision:
        raise SystemExit("pass --plan or --decision")
    model: Path | None = None
    if args.engine == "lama":
        try:
            model = resolve_lama_model(args.model)
        except FileNotFoundError as exc:
            print(json.dumps({"ok": False, "engine": "lama", "error": str(exc)}, ensure_ascii=False))
            return 1
    width, height, duration = args.width, args.height, args.duration
    if not (width and height and duration):
        pw, ph, pd = _probe_video(args.source, args.ffmpeg)
        width = width or pw
        height = height or ph
        duration = duration or pd
    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        spec = mask_from_plan(plan, int(width), int(height))
        if args.engine == "lama":
            spec_path = write_mask_spec(spec, args.workdir / "burn_mask_spec.json")
            result = lama_repair_burn(
                args.source, spec_path, args.workdir / "lama",
                model=model, start=0.0, duration=float(duration),
            )
        else:
            out = args.workdir / f"salvaged_{args.engine}.mp4"
            result = remove_or_blur_burn(args.source, spec, out, ffmpeg=args.ffmpeg, engine=args.engine)
    else:
        decision = json.loads(args.decision.read_text(encoding="utf-8"))
        result = salvage_demoted_burn(
            decision, args.source,
            width=int(width), height=int(height), duration=float(duration),
            workdir=args.workdir, ffmpeg=args.ffmpeg,
            engines=(args.engine,), lama_model=model,
        )
    print(json.dumps({k: v for k, v in result.items() if k != "cmd"}, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") or result.get("salvaged") else 1


if __name__ == "__main__":
    raise SystemExit(main())
