"""Evil0ctal default discovery: happy path + clean degrade to Codex."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from workflow.unattended.collect import collect_footage
from tests.test_unattended_collect import _fake_discovery, _fake_downloader
from workflow.unattended.evil_discovery import (
    build_evil_queries,
    extract_video_urls,
    probe_evil,
    try_evil_discovery,
)


REQ = {
    "product": "眼霜",
    "market": "MX",
    "count": 1,
    "style": "种草",
    "notes": "",
}


def _hints():
    return SimpleNamespace(
        profile="eye_cream",
        definition="眼霜/眼部精华",
        prefer_queries=[
            'tiktok "contorno de ojos" aplicar',
            'site:urlebird.com/video "eye cream"',
            'tiktok "creme para olhos"',
        ],
        reject_markers=[],
        brand_anchors=[],
    )


def test_build_evil_queries_strips_web_search_chrome():
    qs = build_evil_queries("眼霜", _hints(), markets=["MX", "BR"], max_queries=6)
    assert qs[0] == "眼霜"
    joined = " | ".join(qs)
    assert "site:" not in joined.lower()
    assert "tiktok" not in joined.lower()
    assert any("contorno" in q.lower() for q in qs)


def test_extract_video_urls_normalizes_and_dedupes():
    items = [
        {"web_url": "https://www.tiktok.com/@a/video/11111111111?lang=es", "title": "t1"},
        {"web_url": "https://www.tiktok.com/@a/video/11111111111", "title": "dup"},
        {"web_url": "https://www.tiktok.com/tag/x", "title": "bad"},
        {"web_url": "https://www.tiktok.com/@b/video/22222222222", "description": "t2"},
    ]
    rows = extract_video_urls(items)
    assert [r["video_id"] for r in rows] == ["11111111111", "22222222222"]
    assert rows[0]["url"].startswith("https://www.tiktok.com/@a/video/")


def test_try_evil_degrades_when_probe_fails(monkeypatch):
    monkeypatch.delenv("VW_EVIL_API_KEY", raising=False)
    monkeypatch.setenv("VW_EVIL_API_KEY", "dtk_test_key")
    rows, meta = try_evil_discovery(
        REQ,
        _hints(),
        probe=lambda _base: (False, "readyz 503 (Postgres/Redis not ready)"),
        searcher=lambda _kw: (_ for _ in ()).throw(AssertionError("search must not run")),
        log=lambda _m: None,
    )
    assert rows == []
    assert meta["degraded"] is True
    assert meta["probe_ok"] is False
    assert "503" in meta["reason"]


def test_try_evil_degrades_when_disabled(monkeypatch):
    monkeypatch.setenv("VW_EVIL_DISCOVERY", "0")
    rows, meta = try_evil_discovery(
        REQ,
        _hints(),
        probe=lambda _base: (True, "ok"),
        searcher=lambda _kw: [{"url": "https://www.tiktok.com/@x/video/33333333333", "account": "x", "video_id": "33333333333"}],
        log=lambda _m: None,
    )
    assert rows == []
    assert meta["degraded"] is True
    assert "disabled" in meta["reason"].lower()


def test_try_evil_happy_path_returns_urls(monkeypatch):
    monkeypatch.setenv("VW_EVIL_DISCOVERY", "1")
    monkeypatch.setenv("VW_EVIL_API_KEY", "dtk_test_key")

    def searcher(keyword: str):
        # one unique video per keyword
        n = abs(hash(keyword)) % 10_000 + 10_000_00000
        return [
            {
                "url": f"https://www.tiktok.com/@u{n}/video/{n}",
                "account": f"u{n}",
                "video_id": str(n),
                "why": "evil search",
                "product_match_evidence": keyword,
            }
        ]

    rows, meta = try_evil_discovery(
        REQ,
        _hints(),
        max_candidates=3,
        max_queries=4,
        probe=lambda _base: (True, "readyz ok"),
        searcher=searcher,
        seen=set(),
        used_ids=set(),
        log=lambda _m: None,
    )
    assert meta["degraded"] is False
    assert meta["probe_ok"] is True
    assert meta["url_count"] == len(rows) >= 1
    assert all("tiktok.com/@" in r["url"] for r in rows)


def test_collect_uses_evil_before_codex_when_healthy(tmp_path, monkeypatch):
    """Evil happy path must satisfy the pool without calling Codex discovery."""
    monkeypatch.setenv("VW_EVIL_DISCOVERY", "1")
    monkeypatch.setenv("VW_EVIL_API_KEY", "dtk_test_key")

    calls = {"discover": 0, "evil": 0}

    def runner(prompt, **kwargs):
        calls["discover"] += 1
        raise AssertionError("Codex discovery must not run when Evil fills the pool")

    def searcher(keyword: str):
        calls["evil"] += 1
        base = 70000000000 + calls["evil"]
        return [
            {
                "url": f"https://www.tiktok.com/@evil{i}/video/{base + i}",
                "account": f"evil{i}",
                "video_id": str(base + i),
                "why": "evil search",
            }
            for i in range(4)
        ]

    # Patch try_evil_discovery's internals via module-level used by collect import path:
    # collect imports try_evil_discovery inside the function — monkeypatch the module attr.
    import workflow.unattended.evil_discovery as ed

    def fake_try(request, form_hints, **kwargs):
        kwargs = dict(kwargs)
        kwargs["probe"] = lambda _b: (True, "ok")
        kwargs["searcher"] = searcher
        kwargs.setdefault("log", lambda _m: None)
        return try_evil_discovery(request, form_hints, **kwargs)

    monkeypatch.setattr(ed, "try_evil_discovery", fake_try)

    def screener(request, clips, **kwargs):
        # Accept everything with enough usable segments for screening_sufficient defaults.
        per = {}
        for c in clips:
            cid = str(c["clip_id"])
            per[cid] = {
                "accepted": True,
                "reason": "ok",
                "usable_segments": [
                    {"start_s": 0.0, "end_s": 5.0, "product_prominent": True},
                    {"start_s": 5.0, "end_s": 10.0, "product_prominent": True},
                ],
            }
        return SimpleNamespace(ok=True, product_definition="眼霜", per_clip=per)

    result = collect_footage(
        REQ,
        tmp_path,
        discovery_runner=runner,
        downloader=_fake_downloader(),
        screener=screener,
        log=lambda _m: None,
        seed_dir=tmp_path / "empty-seeds",
        skip_discovery_if_seeds=True,
        min_clips=2,
        max_clips=4,
        max_candidates=4,
        discover_rounds=1,
    )
    assert result.ok, result.reason
    assert calls["discover"] == 0
    assert calls["evil"] >= 1
    assert result.timings.get("evil_probe_ok") == 1.0
    assert result.timings.get("evil_urls", 0) >= 1
    assert "Evil" in result.reason or result.timings.get("discovery_source") == 1.0


def test_collect_falls_back_to_codex_when_evil_probe_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("VW_EVIL_DISCOVERY", "1")
    monkeypatch.setenv("VW_EVIL_API_KEY", "dtk_test_key")

    import workflow.unattended.evil_discovery as ed

    def fake_try(request, form_hints, **kwargs):
        return [], {
            "degraded": True,
            "probe_ok": False,
            "url_count": 0,
            "query_count": 0,
            "reason": "Evil API unreachable",
            "source": "evil",
        }

    monkeypatch.setattr(ed, "try_evil_discovery", fake_try)

    # Use blender REQ so shared _fake_discovery prompt assert still holds.
    req = {
        "product": "licuadora portátil",
        "market": "MX",
        "style": "TikTok种草",
        "count": 1,
        "title": "t",
    }
    logs: list[str] = []
    result = collect_footage(
        req,
        tmp_path,
        discovery_runner=_fake_discovery(4),
        downloader=_fake_downloader(),
        log=logs.append,
        screen=False,
        seed_dir=tmp_path / "empty-seeds",
        min_clips=3,
        max_clips=4,
        max_candidates=4,
        discover_rounds=1,
    )
    assert result.ok, result.reason
    assert any("Evil 降级" in m or "降级 Codex" in m for m in logs)
    assert result.timings.get("evil_probe_ok") == 0.0


def test_probe_evil_handles_unreachable():
    ok, reason = probe_evil("http://127.0.0.1:1", timeout=0.3)
    assert ok is False
    assert "unreachable" in reason.lower() or "fail" in reason.lower() or "Evil" in reason
