"""When a mask region paints. Kept free of OpenCV so callers can import it alone."""

from __future__ import annotations

from typing import Any


def region_intervals(region: dict[str, Any]) -> list[tuple[float, float]] | None:
    """None means the region stays active for the whole repair range (static box)."""
    raw = region.get("intervals")
    if isinstance(raw, list) and raw:
        spans: list[tuple[float, float]] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                spans.append((float(item[0]), float(item[1])))
        return spans or None
    if region.get("t0") is not None and region.get("t1") is not None:
        return [(float(region["t0"]), float(region["t1"]))]
    return None


def region_active_at(region: dict[str, Any], second: float) -> bool:
    """True when this ROI should be inpainted at ``second``.

    Inclusive on both ends so a plan interval [t0, t1] covers the same
    moments as ffmpeg ``between(t,t0,t1)``.
    """
    intervals = region_intervals(region)
    if not intervals:
        return True
    t = float(second)
    return any(start <= t <= end for start, end in intervals)
