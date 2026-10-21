import json
from pathlib import Path
import subprocess
import sys

from workflow.collection import (
    candidate_from_seed,
    collect,
    normalize_tiktok_url,
    unique_candidates,
)

def test_bundled_downloader_runs_without_external_path_executable(tmp_path,monkeypatch):
    from workflow.collection.collector import _run
    package=tmp_path/'yt_dlp';package.mkdir()
    (package/'__init__.py').write_text('')
    (package/'__main__.py').write_text("print('BUNDLED_DOWNLOADER_OK')")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv('PYTHONPATH',str(tmp_path))
    monkeypatch.setenv('PATH','')
    result=_run(['yt-dlp','--version'],5)
    assert result.stdout.strip()=='BUNDLED_DOWNLOADER_OK'


def test_normalize_url_deduplicates_query_variants():
    base = "https://www.tiktok.com/@creator/video/1234567890123456789"
    assert normalize_tiktok_url(base + "?is_from_webapp=1") == base
    assert normalize_tiktok_url(base + "?foo=bar") == base


def test_unrelated_paid_ad_is_structure_only():
    seed = {
        "id": "foundation-paid",
        "source_url": "https://ads.tiktok.com/business/creativecenter/topads/123/pc/en",
        "product_match": "不同商品：粉底棒，不是SHEGLAM腮红",
        "commercial_status": "paid_ad_verified",
        "hook": "半脸效果",
        "action_and_proof": "面部涂抹",
    }
    candidate = candidate_from_seed(seed, "SHEGLAM", "Color Bloom Liquid Blush")
    assert candidate["use_classification"] == "ad_structure_reference"
    assert candidate["material_eligibility"] is False


def test_unverified_partnership_is_not_proven_commerce():
    candidate = candidate_from_seed({
        "id": "valeria",
        "source_url": "https://www.tiktok.com/@valeria/video/1234567890123456789",
        "product_match": "SHEGLAM Color Bloom 系列",
        "commercial_status": "同款演示；合作和付费投放均未确认",
        "hook_and_action_observation": "展示试色",
    }, "SHEGLAM", "Color Bloom Liquid Blush")
    assert candidate["source_classification"] == "ad_status_unverified"
    assert candidate["material_eligibility"] is False


def test_deduplicate_same_url_and_keep_evidence():
    url = "https://www.tiktok.com/@creator/video/1234567890123456789"
    items = unique_candidates([
        {"source_url": url, "hook": ""},
        {"source_url": url + "?foo=bar", "hook": "product close-up"},
    ])
    assert len(items) == 1
    assert items[0]["hook"] == "product close-up"


def test_failed_download_is_not_reported_as_retrieved(tmp_path):
    url = "https://www.tiktok.com/@creator/video/1234567890123456789"

    def failing_download(candidate, output_dir):
        raise RuntimeError("HTTP 403")

    result = collect(
        seeds=[{"id": "one", "source_url": url, "product_match": "SHEGLAM Color Bloom Liquid Blush",
                "commercial_status": "#SHEGLAMpartner", "hook_and_action_observation": "上脸演示"}],
        brand="SHEGLAM", product="Color Bloom Liquid Blush", output_dir=tmp_path,
        download=True, downloader=failing_download, resolver=lambda _: {"id": "1234567890123456789"},
    )
    item = result["candidates"][0]
    assert item["retrieval_status"] == "failed"
    assert item["failure_reason"] == "HTTP 403"
    assert item["raw_path"] is None
    assert item["preview_path"] is None
    assert json.loads((tmp_path / "collection.json").read_text())["candidates"][0]["retrieval_status"] == "failed"


def test_seed_metrics_preserve_unknown_views():
    seed = {"id": "one", "source_url": "https://www.tiktok.com/@a/video/1234567890123456789",
            "product_match": "SHEGLAM Color Bloom Liquid Blush", "visible_metrics": {"likes": 10, "views": None}}
    candidate = candidate_from_seed(seed, "SHEGLAM", "Color Bloom Liquid Blush")
    assert candidate["visible_metrics"]["views"] is None
    assert candidate["metric_missing_reasons"]["views"]
    assert candidate["popularity_evidence"] is None


def test_existing_material_keeps_original_and_does_not_download(tmp_path, monkeypatch):
    raw = tmp_path / "input.mp4"
    preview = tmp_path / "existing-preview.mp4"
    raw.write_bytes(b"raw")
    preview.write_bytes(b"preview")
    monkeypatch.setattr("workflow.collection.collector.verify_preview", lambda _: {"codec": "h264"})

    def unexpected(*args):
        raise AssertionError("network/download must not run")

    report = collect(
        seeds=[{"id": "one", "source_url": "https://www.tiktok.com/@a/video/1234567890123456789"}],
        brand="SHEGLAM", product="Color Bloom Liquid Blush", output_dir=tmp_path / "report",
        existing_raw={"one": raw}, existing_preview={"one": preview},
        resolver=unexpected, downloader=unexpected,
    )
    item = report["candidates"][0]
    assert item["retrieval_status"] == "existing_validated"
    assert item["url_resolution_status"] == "saved_evidence_only"
    assert item["raw_path"] == str(raw)
    assert item["preview_path"] == str(preview)
    assert raw.read_bytes() == b"raw"


def test_duplicate_content_is_marked_by_hash(tmp_path, monkeypatch):
    raw_a = tmp_path / "a.mp4"
    raw_b = tmp_path / "b.mp4"
    preview = tmp_path / "preview.mp4"
    raw_a.write_bytes(b"identical")
    raw_b.write_bytes(b"identical")
    preview.write_bytes(b"preview")
    monkeypatch.setattr("workflow.collection.collector.verify_preview", lambda _: {"decoded_frame": True})
    seeds = [{"id": key, "source_url": f"https://www.tiktok.com/@a/video/{video_id}"}
             for key, video_id in (("a", "1234567890123456789"), ("b", "2234567890123456789"))]
    report = collect(seeds=seeds, brand="SHEGLAM", product="Color Bloom Liquid Blush",
                     output_dir=tmp_path / "report", existing_raw={"a": raw_a, "b": raw_b},
                     existing_preview={"a": preview, "b": preview})
    assert report["candidates"][1]["duplicate_content_of"] == "a"


def test_preview_failure_preserves_acquired_raw_status(tmp_path, monkeypatch):
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"raw data")

    def fail_preview(*args):
        raise RuntimeError("codec failure")

    monkeypatch.setattr("workflow.collection.collector.make_preview", fail_preview)
    report = collect(
        seeds=[{"id": "one", "source_url": "https://www.tiktok.com/@a/video/1234567890123456789"}],
        brand="SHEGLAM", product="Color Bloom Liquid Blush", output_dir=tmp_path / "report",
        existing_raw={"one": raw},
    )
    item = report["candidates"][0]
    assert item["acquisition_status"] == "existing_validated"
    assert item["preview_status"] == "failed"
    assert item["raw_path"] == str(raw)
    assert item["preview_path"] is None
