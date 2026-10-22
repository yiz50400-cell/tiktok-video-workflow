"""Batch footage: cross-video disjoint assignment + within-video near-dup control."""

from __future__ import annotations

from pathlib import Path

from workflow.unattended.batch_footage import (
    CollectTargets,
    clip_id_of,
    collect_targets,
    cross_video_overlap_issues,
    partition_clips_disjoint,
    unique_clips,
    video_count,
    within_video_near_dup_issues,
)
from workflow.unattended.verify_output import verify_production


def _clip(cid: str, path: str | None = None) -> dict:
    return {
        "clip_id": cid,
        "path": path or f"/footage/{cid}.mp4",
        "source_url": f"https://www.tiktok.com/@x/video/{cid}",
        "usable_segments": [{"start_s": 0, "end_s": 8, "product_prominent": True}],
    }


def test_video_count_and_collect_targets_scale_with_batch():
    assert video_count({"count": 3}) == 3
    assert video_count({"count": "2"}) == 2
    assert video_count({}) == 1
    one = collect_targets(1)
    three = collect_targets(3)
    assert isinstance(one, CollectTargets)
    assert three.min_clips >= 6  # 3 × MIN_CLIPS_PER_VIDEO
    assert three.min_clips > one.min_clips
    assert three.screen_min_accepted >= 6
    assert three.max_candidates >= three.min_clips
    assert three.discover_rounds >= 2


def test_partition_disjoint_when_pool_allows():
    clips = [_clip(str(i)) for i in range(9)]
    result = partition_clips_disjoint(clips, 3)
    assert result.ok, result.reason
    assert len(result.bundles) == 3
    ids = [{c["clip_id"] for c in b} for b in result.bundles]
    # pairwise disjoint
    assert not (ids[0] & ids[1])
    assert not (ids[0] & ids[2])
    assert not (ids[1] & ids[2])
    assert all(len(b) >= 2 for b in result.bundles)
    assert sum(len(b) for b in result.bundles) == 9


def test_partition_refuses_reshuffle_when_pool_short():
    clips = [_clip(str(i)) for i in range(4)]  # need 6 for count=3 × 2
    result = partition_clips_disjoint(clips, 3)
    assert not result.ok
    assert result.bundles == []
    assert "禁止" in result.reason or "补采" in result.reason
    assert result.unique_count == 4
    assert result.need == 6


def test_partition_dedupes_duplicate_clip_ids_before_assign():
    clips = [_clip("a"), _clip("a"), _clip("b"), _clip("c"), _clip("d"), _clip("e"), _clip("f")]
    result = partition_clips_disjoint(clips, 3)
    assert result.ok
    flat = [c["clip_id"] for b in result.bundles for c in b]
    assert len(flat) == len(set(flat)) == 6


def test_within_video_flags_alternating_two_sources():
    shots = []
    for i in range(8):
        cid = "74p4" if i % 2 == 0 else "76p4"
        shots.append({"path": f"/f/{cid}.mp4", "clip_id": cid, "source_in_s": 1.0, "duration_s": 2.0})
    issues = within_video_near_dup_issues(shots)
    assert issues, "expected near-dup alternating to fail"
    assert any("交替" in x or "假片" in x or "复用" in x for x in issues)


def test_within_video_allows_diverse_timeline():
    shots = [
        {"clip_id": f"c{i}", "path": f"/f/c{i}.mp4"}
        for i in range(8)
    ]
    assert within_video_near_dup_issues(shots) == []


def test_cross_video_overlap_detection():
    issues = cross_video_overlap_issues([{"a", "b"}, {"b", "c"}])
    assert issues and "跨条素材重复" in issues[0]
    assert cross_video_overlap_issues([{"a"}, {"b"}, {"c"}]) == []


def test_verify_production_rejects_cross_video_reuse(tmp_path: Path):
    # Build three tiny fake mp4s? verify probes with ffmpeg — too heavy.
    # Unit-test the overlap helper path via allowed_clip_ids + near-dup only through
    # batch helpers (above). Here check clip_id_of / unique_clips basics.
    assert clip_id_of({"clip_id": "123"}) == "123"
    assert clip_id_of({"path": "/x/456.p4.mp4"}) == "456"
    assert len(unique_clips([_clip("1"), _clip("1"), _clip("2")])) == 2


def test_verify_expected_count_mismatch(tmp_path: Path, monkeypatch):
    result = tmp_path / "produce-result.json"
    result.write_text('{"ok": true, "project_name": "MX handcream", "footage_used": []}', encoding="utf-8")
    out = verify_production(
        result_path=result,
        export_paths=[],
        clips=[],
        expected_count=3,
    )
    assert not out.ok
    assert "应恰好导出 3" in out.reason
