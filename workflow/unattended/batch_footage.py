"""Batch footage planning: scale collect targets and enforce cross-video / within-video clip diversity.

Root cause this module fixes (IZ MX hand-cream FAIL, 2026-10-22):
one enqueue with count=N collected only ~2 screened clips (screening_sufficient defaults),
then Codex reshuffled the same clip_ids across N deliveries (and alternated 1–2 sources
inside a single timeline). Looks fake.

Rules:
- Within one batch/run, clip_id sets of different videos must be disjoint.
- If the unique pool is too small for count × min_per_video, refuse and ask for refill —
  never pad by reordering / reshuffling the same sources across videos.
- Within one video, reject near-duplicate alternating cuts of only 1–2 source clips.
- UsedFootageLedger (cross-run reuse labeling) is unchanged and separate from within-batch
  reshuffle bans.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Preferred / minimum unique source clips assigned to each video in a batch.
CLIPS_PER_VIDEO = 3
MIN_CLIPS_PER_VIDEO = 2


def video_count(request: dict[str, Any] | None) -> int:
    raw = (request or {}).get("count", 1)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, 100))


@dataclass(frozen=True)
class CollectTargets:
    min_clips: int
    max_clips: int
    max_candidates: int
    discover_rounds: int
    max_searches: int
    screen_min_accepted: int
    screen_min_usable_s: float
    screen_min_prominent: int


def collect_targets(count: int) -> CollectTargets:
    """Scale discovery / screening floors so a batch of N can get disjoint pools."""
    n = max(1, int(count))
    min_clips = max(3, n * MIN_CLIPS_PER_VIDEO)
    prefer = max(min_clips, n * CLIPS_PER_VIDEO)
    return CollectTargets(
        min_clips=min_clips,
        max_clips=max(10, prefer + n * 2),
        max_candidates=max(12, prefer * 3),
        # Large batches need more discovery rounds / searches before giving up.
        discover_rounds=2 if n <= 2 else min(6, 2 + n),
        max_searches=max(8, 4 + n * 2),
        screen_min_accepted=max(2, n * MIN_CLIPS_PER_VIDEO),
        screen_min_usable_s=max(12.0, 10.0 * n),
        screen_min_prominent=max(2, n * MIN_CLIPS_PER_VIDEO),
    )


def clip_id_of(item: Any) -> str:
    """Best-effort source id from a Clip, dict shot, or footage_used row."""
    if item is None:
        return ""
    if isinstance(item, dict):
        for key in ("clip_id", "video_id"):
            value = item.get(key)
            if value:
                return str(value)
        path = item.get("path") or item.get("source_url") or ""
        if path:
            stem = Path(str(path)).stem
            # strip common download suffixes like .p4 / .f301
            return stem.split(".")[0] if stem else ""
        return ""
    value = getattr(item, "clip_id", None) or getattr(item, "video_id", None)
    if value:
        return str(value)
    path = getattr(item, "path", None) or getattr(item, "source_url", None)
    if path:
        stem = Path(str(path)).stem
        return stem.split(".")[0] if stem else ""
    return ""


def unique_clips(clips: list[Any]) -> list[Any]:
    """Preserve order, drop duplicate clip_ids (first wins)."""
    out: list[Any] = []
    seen: set[str] = set()
    for clip in clips:
        cid = clip_id_of(clip)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(clip)
    return out


@dataclass
class PartitionResult:
    ok: bool
    bundles: list[list[Any]]
    reason: str
    unique_count: int = 0
    need: int = 0

    def assignment_payload(self) -> list[dict[str, Any]]:
        """JSON-friendly per-video clip lists for the produce prompt."""
        rows: list[dict[str, Any]] = []
        for index, bundle in enumerate(self.bundles):
            entries = []
            for clip in bundle:
                if isinstance(clip, dict):
                    entries.append({
                        "clip_id": clip_id_of(clip),
                        "path": clip.get("path"),
                        "source_url": clip.get("source_url"),
                        "usable_segments": clip.get("usable_segments"),
                        "screen_grid": clip.get("screen_grid"),
                        "screen_times": clip.get("screen_times"),
                        "duration": clip.get("duration"),
                        "width": clip.get("width"),
                        "height": clip.get("height"),
                        "account": clip.get("account"),
                        "title": clip.get("title"),
                    })
                else:
                    entries.append({
                        "clip_id": clip_id_of(clip),
                        "path": getattr(clip, "path", None),
                        "source_url": getattr(clip, "source_url", None),
                        "usable_segments": getattr(clip, "usable_segments", None),
                        "screen_grid": getattr(clip, "screen_grid", None),
                        "screen_times": getattr(clip, "screen_times", None),
                        "duration": getattr(clip, "duration", None),
                        "width": getattr(clip, "width", None),
                        "height": getattr(clip, "height", None),
                        "account": getattr(clip, "account", None),
                        "title": getattr(clip, "title", None),
                    })
            rows.append({
                "video_index": index,
                "clip_ids": [clip_id_of(c) for c in bundle],
                "clips": entries,
            })
        return rows


def partition_clips_disjoint(
    clips: list[Any],
    count: int,
    *,
    min_per_video: int = MIN_CLIPS_PER_VIDEO,
    allow_partial: bool = False,
) -> PartitionResult:
    """Split unique clips into ``count`` disjoint bundles.

    Never reshuffles the same clip_id into multiple videos. If the pool is short
    and ``allow_partial`` is False, ``ok=False`` so the caller can refill.
    With ``allow_partial=True``, deliver as many videos as the pool allows
    (prefer ≥min_per_video each; carefully drop to 1/video only when needed)
    instead of all-or-nothing zero deliveries.
    """
    n = max(1, int(count))
    per = max(1, int(min_per_video))
    need = n * per
    pool = unique_clips(clips)
    if n == 1:
        # Single video: hand the whole unique pool through (existing behaviour).
        return PartitionResult(True, [list(pool)], f"单条成片，素材池 {len(pool)} 条", len(pool), need)
    if len(pool) < need:
        if not allow_partial:
            return PartitionResult(
                False,
                [],
                (
                    f"同批 {n} 条成片需要至少 {need} 条互不重复源素材"
                    f"（每条 ≥{per}），当前只有 {len(pool)} 条；"
                    "禁止把同一批素材换序复用凑数，请补采后再制作"
                ),
                len(pool),
                need,
            )
        # Partial: maximize videos while keeping bundles disjoint.
        per_use = per
        k = len(pool) // per_use
        lowered = False
        if k < 1 and len(pool) >= 1:
            per_use = 1
            k = len(pool)
            lowered = True
        if k < 1:
            return PartitionResult(
                False,
                [],
                f"素材池为空，无法交付任何成片（目标 {n}）",
                0,
                need,
            )
        k = min(k, n)
        bundles: list[list[Any]] = [[] for _ in range(k)]
        for index, clip in enumerate(pool):
            bundles[index % k].append(clip)
        for index, bundle in enumerate(bundles):
            if len(bundle) < per_use:
                return PartitionResult(
                    False,
                    [],
                    f"部分交付分区失败：第 {index + 1} 条只分到 {len(bundle)} 条（需 ≥{per_use}）",
                    len(pool),
                    need,
                )
        floor_note = f"，已降至每条 ≥{per_use} 源" if lowered else f"（每条 ≥{per_use}）"
        return PartitionResult(
            True,
            bundles,
            (
                f"缺素材：目标 {n} 条成片需 ≥{need} 源，池仅 {len(pool)}；"
                f"已按互不重复分配 {k}/{n} 条成片{floor_note}——"
                f"缺素材，已交付计划 {k}/{n}"
            ),
            len(pool),
            need,
        )
    bundles = [[] for _ in range(n)]
    for index, clip in enumerate(pool):
        bundles[index % n].append(clip)
    for index, bundle in enumerate(bundles):
        if len(bundle) < per:
            return PartitionResult(
                False,
                [],
                f"分区后第 {index + 1} 条只分到 {len(bundle)} 条素材（需 ≥{per}），请补采",
                len(pool),
                need,
            )
    return PartitionResult(
        True,
        bundles,
        f"已为 {n} 条成片分配互不重复素材（池 {len(pool)}，每条 ≥{per}）",
        len(pool),
        need,
    )


def within_video_near_dup_issues(shots: list[Any] | None) -> list[str]:
    """Flag timelines that only alternate 1–2 source clips (the MX FAIL pattern)."""
    if not shots:
        return []
    ids = [clip_id_of(s) for s in shots]
    ids = [i for i in ids if i]
    if len(ids) < 4:
        return []
    unique = list(dict.fromkeys(ids))
    issues: list[str] = []
    if len(unique) < 2:
        issues.append("单条成片几乎只用 1 条源素材，镜头多样性不足")
        return issues
    freq = Counter(ids)
    top_two = sum(count for _, count in freq.most_common(2))
    if len(unique) <= 2 and top_two >= int(len(ids) * 0.85 + 0.999):
        names = "+".join(u[:12] for u in unique[:2])
        issues.append(
            f"单条成片镜头几乎只在 {len(unique)} 条源素材（{names}）间复用"
            f"（{top_two}/{len(ids)}），看起来像假片；请换用更多不同源素材"
        )
    # Strict A-B-A-B alternation of exactly two sources across a long timeline.
    if len(unique) == 2 and len(ids) >= 6:
        switches = sum(1 for i in range(1, len(ids)) if ids[i] != ids[i - 1])
        if switches >= len(ids) - 2:
            issues.append(
                "单条成片在两条源素材间来回交替剪（换序凑节奏），禁止；"
                "每条至少用 ≥2 条不同源且不要整条时间线只在两段间乒乓"
            )
    return issues


def cross_video_overlap_issues(per_video_ids: list[set[str]]) -> list[str]:
    """Flag any clip_id that appears in more than one video of the same batch."""
    issues: list[str] = []
    owner: dict[str, int] = {}
    for index, ids in enumerate(per_video_ids):
        for cid in ids:
            if not cid:
                continue
            if cid in owner and owner[cid] != index:
                issues.append(
                    f"跨条素材重复：clip_id={cid} 同时出现在第 {owner[cid] + 1} 条"
                    f"与第 {index + 1} 条成片（禁止同批换序复用）"
                )
            else:
                owner[cid] = index
    return issues


def footage_ids_from_result_video(video: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in video.get("footage_used") or []:
        cid = clip_id_of(row)
        if cid:
            ids.add(cid)
    for shot in video.get("shots") or []:
        cid = clip_id_of(shot)
        if cid:
            ids.add(cid)
    return ids


def extract_per_video_results(result: dict[str, Any], export_paths: list[Path]) -> list[dict[str, Any]]:
    """Normalize single-video and multi-video produce-result shapes."""
    videos = result.get("videos")
    if isinstance(videos, list) and videos:
        out = []
        for index, video in enumerate(videos):
            if not isinstance(video, dict):
                continue
            row = dict(video)
            row.setdefault("video_index", index)
            if not row.get("export_path") and index < len(export_paths):
                row["export_path"] = str(export_paths[index])
            out.append(row)
        return out
    # Legacy single-video result.
    return [{
        "video_index": 0,
        "export_path": str(export_paths[0]) if export_paths else result.get("export_paths", [None])[0],
        "shots": result.get("shots") or [],
        "footage_used": result.get("footage_used") or [],
        "caption_lines": result.get("caption_lines") or [],
    }]
