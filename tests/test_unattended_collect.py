from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from workflow.unattended.collect import (
    Candidate,
    build_discovery_command,
    collect_footage,
    keyword_hit,
    normalize_tiktok_url,
    parse_discovery_output,
    parse_probe,
    render_discover_prompt,
    resolve_ffmpeg,
    search_markets_for_round,
)
from workflow.unattended.discover_speed import (
    SUPPORTED_COLLECTION_MARKETS,
    count_reconnects,
    infer_form_hints,
    load_seed_candidates,
    product_slug,
    save_seed_candidates,
    should_abort_reconnects,
    text_suggests_wrong_form,
)
from workflow.unattended.inbox import Inbox
from workflow.unattended.producer import ProduceOptions, Producer, render_produce_prompt
from workflow.unattended.verify_output import scan_codex_events, verify_production


def _make_video(path: Path, *, w=540, h=960, seconds=6, audio="silent", cbr=False) -> Path:
    ffmpeg = resolve_ffmpeg()
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:duration={seconds}"]
    if audio == "silent":
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}"]
    elif audio == "tone":
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if cbr:
        cmd += ["-b:v", "1500k", "-minrate", "1500k", "-maxrate", "1500k", "-bufsize", "3000k",
                "-x264-params", "nal-hrd=cbr"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True)
    return path


# --- URL / discovery parsing -------------------------------------------------

def test_normalize_tiktok_url_accepts_video_pages_only():
    assert normalize_tiktok_url("https://www.tiktok.com/@a.b_c/video/7563721080284663054?lang=es") == (
        "https://www.tiktok.com/@a.b_c/video/7563721080284663054", "a.b_c", "7563721080284663054")
    for bad in ("https://www.tiktok.com/tag/licuadora", "https://vm.tiktok.com/ZMabc/",
                "https://samplelib.com/lib/preview/mp4/sample-5s.mp4",
                "https://www.tiktok.com/@x/photo/7563721080284663054", "https://www.pexels.com/video/1/"):
        assert normalize_tiktok_url(bad) is None


def test_parse_discovery_output_fenced_dedup_and_keywords():
    text = """Here:
```json
{"keywords": ["licuadora portátil", "portable blender"],
 "candidates": [
   {"url": "https://www.tiktok.com/@u1/video/11111111111", "why": "demo"},
   {"url": "https://www.tiktok.com/@u1/video/11111111111"},
   {"url": "https://www.tiktok.com/tag/blender"},
   {"url": "https://www.tiktok.com/@u2/video/22222222222", "product_match_evidence": "title"}]}
```"""
    cands, kws = parse_discovery_output(text)
    assert [c.video_id for c in cands] == ["11111111111", "22222222222"]
    assert kws == ["licuadora portátil", "portable blender"]


def test_parse_discovery_output_tolerates_bare_urls():
    cands, kws = parse_discovery_output("https://www.tiktok.com/@a/video/123456789\nhttps://www.tiktok.com/@b/video/987654321")
    assert len(cands) == 2 and kws == []


def test_keyword_hit_folds_accents_and_hashtags():
    info = {"title": "Mi nueva #LicuadoraPortatil 😍", "tags": ["cocina"]}
    assert keyword_hit(info, ["licuadora portátil"])
    assert not keyword_hit(info, ["aspiradora"])


def test_parse_probe_reads_video_audio_and_volume():
    stderr = ("  Duration: 00:00:17.57, start: 0.000000, bitrate: 7852 kb/s\n"
              "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1080x1920, 7846 kb/s, 30 fps\n"
              "  Stream #0:1[0x2](und): Audio: aac (LC), 48000 Hz, stereo\n"
              "[Parsed_volumedetect_0 @ 0x1] max_volume: -91.0 dB\n")
    probe = parse_probe(stderr)
    assert (probe.duration, probe.width, probe.height, probe.vcodec, probe.has_audio, probe.max_volume_db) == (
        17.57, 1080, 1920, "h264", True, -91.0)


def test_discovery_command_is_read_only_with_web_search_and_no_chatcut():
    cmd = build_discovery_command(codex_bin="codex", model="gpt-5.6-sol", workdir=Path("/tmp/w"),
                                  last_message_path=Path("/tmp/w/last.txt"), use_profile=True)
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert 'web_search="live"' in cmd
    assert "mcp_servers.chatcut_desktop.enabled=false" in cmd
    assert "danger-full-access" not in cmd and "--json" in cmd


# --- collect_footage with fakes ----------------------------------------------

def _fake_discovery(n: int):
    def runner(prompt, *, command, cwd, events_path, timeout):
        assert "{{" not in prompt and "licuadora" in prompt
        last = Path(command[command.index("-o") + 1])
        payload = {"keywords": ["licuadora portátil"],
                   "candidates": [{"url": f"https://www.tiktok.com/@c{i}/video/{10000000000 + i}"} for i in range(n)]}
        last.write_text(json.dumps(payload), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0
    return runner


def _fake_downloader(horizontal_ids=()):
    def download(cand: Candidate, raw_dir: Path, *, ffmpeg: str):
        w, h = (960, 540) if cand.video_id in horizontal_ids else (540, 960)
        _make_video(raw_dir / f"{cand.video_id}.mp4", w=w, h=h)
        cand.raw_path = str(raw_dir / f"{cand.video_id}.mp4")
        cand.info = {"id": cand.video_id, "extractor": "TikTok", "title": "Licuadora portatil review"}
        cand.status = "downloaded"
    return download


REQ = {"product": "licuadora portátil", "market": "MX", "style": "TikTok种草", "count": 1, "title": "t"}


def test_collect_footage_accepts_vertical_tiktok_clips(tmp_path):
    result = collect_footage(REQ, tmp_path, discovery_runner=_fake_discovery(5),
                             downloader=_fake_downloader(horizontal_ids={"10000000001"}), max_clips=3,
                             log=lambda _m: None, screen=False)
    assert result.ok, result.reason
    assert len(result.clips) == 3
    assert all(Path(c.sheet_path).is_file() and c.height > c.width and c.keyword_hit for c in result.clips)
    statuses = {c.video_id: c.status for c in result.candidates}
    assert statuses["10000000001"] == "rejected"
    manifest = json.loads((tmp_path / "footage" / "collection.json").read_text(encoding="utf-8"))
    assert manifest["ok"] and len(manifest["clips"]) == 3


def test_collect_footage_fails_fast_without_enough_candidates(tmp_path):
    # discover_rounds=1 keeps the hard fail-fast (no all-region expand).
    result = collect_footage(
        REQ, tmp_path, discovery_runner=_fake_discovery(2),
        downloader=_fake_downloader(), log=lambda _m: None, screen=False, discover_rounds=1,
    )
    assert not result.ok and "≥3" in result.reason
    assert result.clips == []


# --- verification ------------------------------------------------------------

def _result(tmp_path: Path, export: Path, used: list[str], name="墨西哥licuadora TikTok 无人值守 abc") -> Path:
    path = tmp_path / "produce-result.json"
    path.write_text(json.dumps({"ok": True, "project_name": name, "export_paths": [str(export)],
                                "footage_used": [{"path": u} for u in used]}), encoding="utf-8")
    return path


@pytest.fixture()
def clip_list(tmp_path):
    clip = _make_video(tmp_path / "footage" / "1.mp4")
    return [{"path": str(clip)}]


def test_verify_production_passes_silent_vertical(tmp_path, clip_list):
    export = _make_video(tmp_path / "out.mp4", w=720, h=1280, seconds=10, audio="silent", cbr=True)
    verdict = verify_production(result_path=_result(tmp_path, export, [clip_list[0]["path"]]),
                                export_paths=[export], clips=clip_list)
    assert verdict.ok, verdict.reason


def test_verify_production_rejects_audible_audio(tmp_path, clip_list):
    export = _make_video(tmp_path / "out.mp4", w=720, h=1280, seconds=10, audio="tone", cbr=True)
    verdict = verify_production(result_path=_result(tmp_path, export, [clip_list[0]["path"]]),
                                export_paths=[export], clips=clip_list)
    assert not verdict.ok and "声音规则" in verdict.reason


def test_verify_production_rejects_canary_and_foreign_footage(tmp_path, clip_list):
    export = _make_video(tmp_path / "out.mp4", w=720, h=1280, seconds=10, audio=None, cbr=True)
    v1 = verify_production(result_path=_result(tmp_path, export, [clip_list[0]["path"]], name="TEST-无人值守-20261017"),
                           export_paths=[export], clips=clip_list)
    assert not v1.ok and "金丝雀" in v1.reason
    v2 = verify_production(result_path=_result(tmp_path, export, ["/tmp/stock.mp4"]), export_paths=[export], clips=clip_list)
    assert not v2.ok and "清单以外" in v2.reason


def test_scan_codex_events_flags_forbidden_tools(tmp_path):
    events = tmp_path / "e.jsonl"
    events.write_text("\n".join(json.dumps(e) for e in [
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "chatcut_desktop", "tool": "export"}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "chatcut_desktop", "tool": "submit_music"}},
    ]), encoding="utf-8")
    scan = scan_codex_events(events)
    assert scan["forbidden"] == ["chatcut_desktop.submit_music"]
    assert scan["calls"]["chatcut_desktop.export"] == 1


# --- producer production mode -------------------------------------------------

def _producer_with(tmp_path, runner_result_name: str, review_queue=None):
    inbox = Inbox(tmp_path / "data")
    clip = _make_video(tmp_path / "clips" / "a.mp4")

    class FakeCollection:
        ok = True
        reason = "采集完成：1 条"
        timings = {"discover_s": 1.0}
        manifest_path = "m.json"
        candidates = []

        def __init__(self):
            from workflow.unattended.collect import Clip
            self.clips = [Clip(clip_id="a", path=str(clip), source_url="https://www.tiktok.com/@a/video/123456789",
                               account="a", title="t", duration=6.0, width=540, height=960, keyword_hit=True,
                               visible_metrics={}, sheet_path="s.jpg", sheet_times=[0.5],
                               usable_segments=[{"start_s": 0.0, "end_s": 5.0, "product_prominent": True}])]

    seen = {}

    def fake_runner(prompt, **kwargs):
        seen["prompt"] = prompt
        task_dir = inbox.task_dir("task_prod")
        export = _make_video(task_dir / "exports" / "MX-task_prod-chatcut.mp4", w=720, h=1280, seconds=10,
                             audio="silent", cbr=True)
        (task_dir / "produce-result.json").write_text(json.dumps({
            "ok": True, "project_name": runner_result_name, "export_paths": [str(export)],
            "footage_used": [{"path": str(clip)}],
            "shots": [{"path": str(clip), "source_in_s": 0.5, "duration_s": 2.0, "start_frame": 0, "duration_frames": 60},
                      {"path": str(clip), "source_in_s": 2.5, "duration_s": 2.0, "start_frame": 60, "duration_frames": 60}]}),
            encoding="utf-8")
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr="")

    reviews = {"queue": list(review_queue or [])}

    def fake_reviewer(export, result, request, **kwargs):
        seen.setdefault("reviews", []).append(kwargs["name"])
        issues = reviews["queue"].pop(0) if reviews["queue"] else []
        return issues, {"tiles": []}

    producer = Producer(inbox, probe=lambda: {"healthy": True}, codex_runner=fake_runner,
                        options=ProduceOptions(project_root=tmp_path, chatcut_agent_log=None,
                                               collector=lambda *a, **k: FakeCollection(), reviewer=fake_reviewer),
                        log=lambda _m: None)
    return inbox, producer, seen


def test_producer_production_mode_collects_and_verifies(tmp_path):
    inbox, producer, seen = _producer_with(tmp_path, "墨西哥licuadora TikTok 无人值守 task_prod")
    result = producer.produce(task_id="task_prod", workflow_job_id="job_1", request=REQ)
    assert result.status == "produced", result.reason
    assert "TEST-无人值守" not in seen["prompt"] and "{{" not in seen["prompt"]
    assert "a.mp4" in seen["prompt"] and "español de México" in seen["prompt"]
    stages = inbox.read_task_state("task_prod")["produce"]["stages"]
    assert stages["collect"]["ok"] and stages["verify"]["ok"] and "produce" in stages


def test_producer_production_mode_rejects_canary_export(tmp_path):
    """Regression for E2E run1 (2026-10-19): TEST canary export was marked produced."""
    inbox, producer, _ = _producer_with(tmp_path, "TEST-无人值守-20261017")
    result = producer.produce(task_id="task_prod", workflow_job_id="job_1", request=REQ)
    assert result.status == "produce_failed" and "金丝雀" in result.reason
    assert result.delivery_paths == []


def test_canary_template_is_separate_and_default_is_production():
    prompt = render_produce_prompt(request=REQ, task_id="t", workflow_job_id="j", exports_dir=Path("/e"),
                                   delivery_dir=Path("/d"), result_path=Path("/r.json"))
    assert "TEST-无人值守-20261017" not in prompt and "footage_used" in prompt


def test_collect_retries_discovery_after_timeout(tmp_path):
    calls = {"n": 0}
    good = _fake_discovery(3)

    def flaky(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="codex", timeout=1)
        return good(prompt, **kwargs)

    result = collect_footage(REQ, tmp_path, discovery_runner=flaky, downloader=_fake_downloader(), log=lambda _m: None,
                             screen=False)
    assert calls["n"] == 2 and result.ok, result.reason


def test_producer_retries_when_codex_bails_without_mcp(tmp_path):
    """Regression for E2E run2: Codex wrote 'MCP tools unavailable' without calling any tool."""
    inbox, producer, seen = _producer_with(tmp_path, "墨西哥licuadora TikTok 无人值守 task_prod")
    real_runner = producer.codex_runner
    attempts = {"n": 0}

    def bail_then_work(prompt, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            task_dir = inbox.task_dir("task_prod")
            (task_dir / "produce-result.json").write_text(
                json.dumps({"ok": False, "error": "chatcut_desktop MCP tools unavailable"}), encoding="utf-8")
            (task_dir / "logs" / "codex-events.jsonl").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr="")
        assert "第 2 次尝试" in prompt
        return real_runner(prompt, **kwargs)

    producer.codex_runner = bail_then_work
    result = producer.produce(task_id="task_prod", workflow_job_id="job_1", request=REQ)
    assert attempts["n"] == 2 and result.status == "produced", result.reason



# --- quality gates (2026-10-19, after run3 content problems) -----------------
from workflow.unattended.vision import (
    apply_screening,
    build_vision_command,
    check_shots,
    evaluate_review,
    screen_clips,
    screen_times,
)

_OK = {"product_visible": True, "product_prominent": False, "burned_text": "none", "watermark": False, "lifestyle_only": False}


def _frames(overrides=None):
    overrides = overrides or {}
    frames = []
    for i in range(12):
        frame = dict(_OK, i=i)
        frame.update(overrides.get(i, {}))
        frames.append(frame)
    return frames


def test_screen_times_skip_tiktok_end_card():
    times, span = screen_times(20.0)
    assert span == 17.0 and len(times) == 12 and max(times) < 17.0


def test_apply_screening_rejects_countertop_blender_form_factor():
    """run3: AURA countertop blender was mixed into a portable blender cup video."""
    times, span = screen_times(44.5)
    decision = apply_screening({"same_product": False, "form_factor_seen": "带底座的台式料理机", "reason": "有底座",
                                "frames": _frames()}, times, span)
    assert not decision["accepted"] and "台式料理机" in decision["reason"]


def test_apply_screening_drops_burned_text_watermark_and_lifestyle_frames():
    times, span = screen_times(15.0)
    decision = apply_screening({"same_product": True, "frames": _frames({
        0: {"product_prominent": True}, 1: {"product_prominent": True},
        2: {"burned_text": "heavy"}, 3: {"burned_text": "small"}, 4: {"watermark": True},
        5: {"lifestyle_only": True}, 6: {"product_visible": False}})}, times, span)
    assert decision["accepted"]
    segs = decision["usable_segments"]
    assert segs[0]["start_s"] == 0.0 and segs[0]["product_prominent"]
    assert abs(segs[0]["end_s"] - 2 * span / 12) < 0.02          # frames 0-1 merged, frame 2 (text) excluded
    assert all(s["start_s"] >= 7 * span / 12 - 0.02 for s in segs[1:])  # frames 2..6 excluded
    assert decision["rejected_frames"]["burned_text"] == 2


def test_screen_clips_with_fake_vision(tmp_path):
    clips = [{"clip_id": cid, "path": str(_make_video(tmp_path / f"{cid}.mp4", seconds=8)), "duration": 8.0}
             for cid in ("cup1", "cup2", "aura")]

    def ask(prompt, images, **kwargs):
        assert len(images) == 3 and "便携" in prompt or "licuadora" in prompt
        prominent = _frames({i: {"product_prominent": True} for i in range(12)})
        return {"product_definition": "手持随行杯式榨汁机",
                "clips": [{"clip_id": "cup1", "same_product": True, "frames": prominent},
                          {"clip_id": "cup2", "same_product": True, "frames": prominent},
                          {"clip_id": "aura", "same_product": False, "form_factor_seen": "台式料理机", "frames": prominent}]}

    outcome = screen_clips(REQ, clips, product_definition="", workdir=tmp_path / "screen", ffmpeg=resolve_ffmpeg(), ask=ask,
                           min_usable_s=8)
    assert outcome.ok, outcome.reason
    assert not outcome.per_clip["aura"]["accepted"] and outcome.per_clip["cup1"]["accepted"]
    assert Path(clips[0]["screen_grid"]).is_file()


def test_collect_records_screened_out_reason_in_manifest(tmp_path):
    from workflow.unattended.vision import ScreenOutcome

    def screener(request, dicts, **kwargs):
        per = {}
        for d in dicts:
            bad = d["clip_id"].endswith("2")
            per[d["clip_id"]] = ({"accepted": False, "reason": "不是同款商品类型/形态：台式料理机", "usable_segments": []} if bad else
                                 {"accepted": True, "reason": "ok", "usable_segments": [{"start_s": 0, "end_s": 3, "product_prominent": True}]})
        return ScreenOutcome(True, "质量初筛通过", "手持随行杯式榨汁机", per)

    result = collect_footage(REQ, tmp_path, discovery_runner=_fake_discovery(4), downloader=_fake_downloader(),
                             log=lambda _m: None, screener=screener)
    assert result.ok and len(result.clips) == 3
    manifest = json.loads((tmp_path / "footage" / "collection.json").read_text(encoding="utf-8"))
    assert manifest["product_definition"] == "手持随行杯式榨汁机"
    out = [c for c in manifest["candidates"] if c["status"] == "screened_out"]
    assert len(out) == 1 and "台式料理机" in out[0]["failure_reason"]
    assert manifest["screened_out"][0]["screening"]["reason"].startswith("不是同款")


def test_check_shots_enforces_segments_and_prominent_hook_cta():
    clips = [{"path": "/c/a.mp4", "usable_segments": [{"start_s": 0, "end_s": 3, "product_prominent": True},
                                                      {"start_s": 5, "end_s": 9, "product_prominent": False}]}]
    good = {"shots": [{"path": "/c/a.mp4", "source_in_s": 0.5, "duration_s": 2}, {"path": "/c/a.mp4", "source_in_s": 5.5, "duration_s": 2},
                      {"path": "/c/a.mp4", "source_in_s": 1.0, "duration_s": 1.8}]}
    assert check_shots(good, clips) == []
    bad = {"shots": [{"path": "/c/a.mp4", "source_in_s": 2.5, "duration_s": 2}, {"path": "/c/a.mp4", "source_in_s": 6, "duration_s": 2}]}
    issues = check_shots(bad, clips)
    assert any("开头" in i and "不在" in i for i in issues) and any("结尾CTA" in i and "不突出" in i for i in issues)
    assert check_shots({}, clips)


def test_evaluate_review_flags_lifestyle_cta_burned_text_and_watermark():
    ok = {"product_visible": True, "product_prominent": True, "same_product": True, "source_burned_text": "none",
          "watermark": False, "lifestyle_only": False}
    assert evaluate_review({"tiles": [dict(ok, i=0), dict(ok, i=1), dict(ok, i=2)]}, 3) == []
    issues = evaluate_review({"tiles": [dict(ok, i=0), dict(ok, i=1, source_burned_text="heavy", watermark=True),
                                        dict(ok, i=2, lifestyle_only=True, product_prominent=False)]}, 3)
    text = "；".join(issues)
    assert "烧录文字" in text and "水印" in text and "生活方式" in text and "结尾CTA镜头没有清楚展示商品" in text
    assert evaluate_review(None, 2)


def test_vision_command_attaches_images_read_only():
    cmd = build_vision_command(codex_bin="codex", model="gpt-5.6-sol", workdir=Path("/w"), last_message_path=Path("/w/l.txt"),
                               images=[Path("/w/a.jpg"), Path("/w/b.jpg")], use_profile=False)
    assert cmd[cmd.index("-i") + 1] == "/w/a.jpg" and cmd.count("-i") == 2
    assert cmd.index("-i") < cmd.index("-s") and cmd[cmd.index("-s") + 1] == "read-only"
    assert "mcp_servers.chatcut_desktop.enabled=false" in cmd and cmd[-1] == "-"


def test_producer_reedits_after_failed_review_then_delivers(tmp_path):
    inbox, producer, seen = _producer_with(tmp_path, "墨西哥licuadora TikTok 无人值守 task_prod",
                                           review_queue=[["结尾CTA镜头是纯生活方式画面，看不清商品"]])
    result = producer.produce(task_id="task_prod", workflow_job_id="job_1", request=REQ)
    assert result.status == "produced", result.reason
    assert seen["reviews"] == ["review", "review_r1"]
    assert "自动重剪" in seen["prompt"] and "生活方式" in seen["prompt"]
    stages = inbox.read_task_state("task_prod")["produce"]["stages"]
    assert stages["review"]["ok"] is False and stages["review_r1"]["ok"] is True and "produce_r1" in stages


def test_producer_never_delivers_when_review_keeps_failing(tmp_path):
    # Retryable issue (lifestyle) exhausted after default review_retries=1 → 2 reviews total.
    inbox, producer, seen = _producer_with(tmp_path, "墨西哥licuadora TikTok 无人值守 task_prod",
                                           review_queue=[["结尾CTA镜头是纯生活方式画面，看不清商品"]] * 3)
    result = producer.produce(task_id="task_prod", workflow_job_id="job_1", request=REQ)
    assert result.status == "produce_failed" and "不交付" in result.reason
    assert result.delivery_paths == [] and seen["reviews"] == ["review", "review_r1"]
    assert not any(inbox.task_dir("task_prod").joinpath("delivery").glob("*.mp4"))


def test_producer_fails_fast_on_burned_text_without_recut(tmp_path):
    inbox, producer, seen = _producer_with(tmp_path, "墨西哥licuadora TikTok 无人值守 task_prod",
                                           review_queue=[["开头镜头有原片烧录文字（英文字幕）"]])
    result = producer.produce(task_id="task_prod", workflow_job_id="job_1", request=REQ)
    assert result.status == "produce_failed"
    assert "不重剪" in result.reason and "烧录文字" in result.reason
    assert seen["reviews"] == ["review"]  # no produce_r1
    assert "produce_r1" not in (inbox.read_task_state("task_prod")["produce"]["stages"])


def test_apply_screening_marks_croppable_text_bands():
    """Only *small* top/bottom text with crop_ok is salvageable; heavy is never croppable."""
    times, span = screen_times(15.0)
    decision = apply_screening({"same_product": True, "frames": _frames({
        0: {"burned_text": "small", "text_region": "bottom", "crop_ok": True, "product_prominent": True},
        1: {"burned_text": "small", "text_region": "bottom", "crop_ok": True},
        2: {"burned_text": "heavy", "text_region": "middle", "crop_ok": False},
        3: {"burned_text": "small", "text_region": "top", "crop_ok": False}})}, times, span)
    segs = decision["usable_segments"]
    assert decision["accepted"]
    assert segs[0]["crop"] == "bottom" and abs(segs[0]["end_s"] - 2 * span / 12) < 0.02
    assert segs[1]["crop"] is None and segs[1]["start_s"] >= 4 * span / 12 - 0.02
    assert decision["rejected_frames"]["burned_text_croppable"] == 2


def test_apply_screening_rejects_heavy_burned_text_even_with_crop_ok():
    times, span = screen_times(15.0)
    # Many heavy frames (English burned subtitles) must reject the whole clip.
    decision = apply_screening({"same_product": True, "frames": _frames({
        i: {"burned_text": "heavy", "text_region": "bottom", "crop_ok": True, "product_prominent": True}
        for i in range(6)
    })}, times, span)
    assert not decision["accepted"]
    assert "烧录文字过多" in decision["reason"]
    assert decision["rejected_frames"]["burned_text_heavy"] >= 6


def test_collect_runs_supplement_round_when_screening_insufficient(tmp_path):
    """run6: 9/10 candidates were countertop blenders -> re-search with rejection reasons, excluding seen IDs."""
    from workflow.unattended.vision import ScreenOutcome

    prompts = []

    def runner(prompt, *, command, cwd, events_path, timeout):
        prompts.append(prompt)
        base = 10000000000 + 100 * len(prompts)
        last = Path(command[command.index("-o") + 1])
        ids = [10000000000 + i for i in range(3)] + [base + i for i in range(3)]  # repeats must be filtered
        last.write_text(json.dumps({"keywords": ["licuadora portátil"],
                                    "candidates": [{"url": f"https://www.tiktok.com/@c/video/{i}"} for i in ids]}), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    calls = []

    def screener(request, dicts, **kwargs):
        calls.append([d["clip_id"] for d in dicts])
        good = len(calls) > 1
        per = {d["clip_id"]: ({"accepted": True, "reason": "ok", "usable_segments": [{"start_s": 0, "end_s": 7, "product_prominent": True}]}
                              if good else {"accepted": False, "reason": "不是同款商品类型/形态：台式料理机", "usable_segments": []})
               for d in dicts}
        return ScreenOutcome(False, "不足", "手持随行杯式榨汁机", per)

    result = collect_footage(REQ, tmp_path, discovery_runner=runner, downloader=_fake_downloader(), log=lambda _m: None,
                             screener=screener)
    assert result.ok, result.reason
    assert len(prompts) == 2 and "补充检索" in prompts[1] and "台式料理机" in prompts[1]
    assert not set(calls[0]) & set(calls[1])  # second round only screens new clips
    manifest = json.loads((tmp_path / "footage" / "collection.json").read_text(encoding="utf-8"))
    assert sum(1 for c in manifest["candidates"] if c["status"] == "screened_out") == len(calls[0])


def test_collect_fails_after_max_rounds_without_enough_footage(tmp_path):
    from workflow.unattended.vision import ScreenOutcome

    def screener(request, dicts, **kwargs):
        return ScreenOutcome(False, "不足", "x", {d["clip_id"]: {"accepted": False, "reason": "台式", "usable_segments": []} for d in dicts})

    result = collect_footage(REQ, tmp_path, discovery_runner=_fake_discovery(4), downloader=_fake_downloader(),
                             log=lambda _m: None, screener=screener, discover_rounds=2)
    assert not result.ok and "质量初筛不足" in result.reason


# --- discovery speed ---------------------------------------------------------

def test_infer_form_hints_portable_blender_and_brush():
    blender = infer_form_hints("便携榨汁杯", notes="墨西哥", style="TikTok种草")
    assert blender.profile == "portable_blender_cup"
    assert "USB" in blender.definition or "杯" in blender.definition
    assert any("BlendJet" in q or "portátil" in q or "portable blender" in q for q in blender.prefer_queries)
    brush = infer_form_hints("escova de limpeza", "BR")
    assert brush.profile == "cleaning_brush"
    generic = infer_form_hints("未知小玩意")
    assert generic.profile == "generic" and len(generic.prefer_queries) >= 3


def test_render_discover_prompt_embeds_form_hints_and_search_budget():
    prompt = render_discover_prompt(
        {"product": "便携榨汁杯", "market": "MX", "style": "种草", "notes": ""},
        max_candidates=12, max_searches=5,
    )
    assert "{{" not in prompt
    assert "最多 5 次 web_search" in prompt
    assert "形态与检索预算" in prompt
    assert "BlendJet" in prompt or "portable blender" in prompt
    assert "licuadora" in prompt.casefold() or "便携" in prompt
    assert "交付市场" in prompt
    assert "优先交付市场" in prompt


def test_text_suggests_wrong_form_for_countertop_evidence():
    hints = infer_form_hints("licuadora portátil")
    assert text_suggests_wrong_form("demo of a countertop blender on a pedestal base", hints)
    assert text_suggests_wrong_form("手持随行杯 USB 充电", hints) is None


def test_reconnect_counter_and_abort_threshold():
    blob = "ERROR: Reconnecting... 1/5\nERROR: Reconnecting... 3/5 (stream disconnected)"
    assert count_reconnects(blob) == 3
    assert should_abort_reconnects(blob, limit=3)
    assert not should_abort_reconnects("Reconnecting... 2/5", limit=3)


def test_product_slug_keeps_chinese():
    assert "便携" in product_slug("便携榨汁杯", "MX")
    assert product_slug("licuadora portátil", "MX").startswith("mx-")


def test_seed_cache_skips_codex_discovery(tmp_path):
    """If seed URLs already satisfy screening, Codex discovery must not run."""
    from workflow.unattended.vision import ScreenOutcome

    req = {"product": "便携榨汁杯", "market": "MX", "style": "TikTok种草", "count": 1, "title": "t"}
    seed_dir = tmp_path / "seeds"
    urls = [f"https://www.tiktok.com/@seed{i}/video/{20000000000 + i}" for i in range(4)]
    save_seed_candidates(seed_dir, req, candidates=[{"url": u} for u in urls], product_definition="手持随行杯")

    calls = {"discover": 0}

    def runner(prompt, **kwargs):
        calls["discover"] += 1
        raise AssertionError("discovery should be skipped when seeds are enough")

    def screener(request, dicts, **kwargs):
        per = {d["clip_id"]: {"accepted": True, "reason": "ok",
                              "usable_segments": [{"start_s": 0, "end_s": 7, "product_prominent": True}]}
               for d in dicts}
        return ScreenOutcome(True, "ok", "手持随行杯式榨汁机", per)

    import time
    t0 = time.time()
    result = collect_footage(
        req, tmp_path / "work", discovery_runner=runner, downloader=_fake_downloader(),
        screener=screener, seed_dir=seed_dir, log=lambda _m: None,
    )
    elapsed = time.time() - t0
    assert result.ok, result.reason
    assert calls["discover"] == 0
    assert result.timings.get("discovery_calls") == 0
    assert len(result.clips) >= 2
    assert elapsed < 60  # no Codex; local ffmpeg only
    # Successful run should refresh the seed file
    loaded = load_seed_candidates(seed_dir, req)
    assert len(loaded) >= 2


def test_soft_filter_skips_wrong_form_before_download(tmp_path):
    from workflow.unattended.vision import ScreenOutcome

    def _write_mixed(prompt, *, command, cwd, events_path, timeout, **_extra):
        last = Path(command[command.index("-o") + 1])
        payload = {"keywords": ["licuadora portátil"], "product_definition": "手持随行杯",
                   "candidates": [
                       {"url": "https://www.tiktok.com/@good/video/30000000001", "why": "USB portable cup demo"},
                       {"url": "https://www.tiktok.com/@bad/video/30000000002",
                        "why": "countertop blender on pedestal base review"},
                       {"url": "https://www.tiktok.com/@good2/video/30000000003", "why": "BlendJet smoothie"},
                       {"url": "https://www.tiktok.com/@good3/video/30000000004", "why": "vaso licuadora USB"},
                   ]}
        last.write_text(json.dumps(payload), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    downloaded = []

    def downloader(cand: Candidate, raw_dir: Path, *, ffmpeg: str):
        downloaded.append(cand.video_id)
        _fake_downloader()(cand, raw_dir, ffmpeg=ffmpeg)

    def screener(request, dicts, **kwargs):
        per = {d["clip_id"]: {"accepted": True, "reason": "ok",
                              "usable_segments": [{"start_s": 0, "end_s": 6, "product_prominent": True}]}
               for d in dicts}
        return ScreenOutcome(True, "ok", "手持随行杯", per)

    result = collect_footage(
        REQ, tmp_path, discovery_runner=_write_mixed, downloader=downloader,
        screener=screener, log=lambda _m: None, seed_dir=tmp_path / "empty-seeds",
    )
    assert result.ok, result.reason
    assert "30000000002" not in downloaded
    assert any(c.status == "skipped_wrong_form" for c in result.candidates)


def test_used_footage_goes_last_and_fresh_discovery_runs(tmp_path):
    """Seeds that already went into a video must not short-circuit discovery; fresh clips win."""
    from workflow.unattended.discover_speed import UsedFootageLedger
    from workflow.unattended.vision import ScreenOutcome

    req = {"product": "便携榨汁机", "market": "MX", "style": "TikTok种草", "count": 1, "title": "t"}
    seed_dir = tmp_path / "seeds"
    old = [f"https://www.tiktok.com/@old{i}/video/{40000000000 + i}" for i in range(4)]
    save_seed_candidates(seed_dir, req, candidates=[{"url": u} for u in old], product_definition="手持随行杯")
    profile = infer_form_hints("便携榨汁机").profile
    UsedFootageLedger.record(seed_dir, req, [u.rsplit("/", 1)[1] for u in old], profile=profile, task_id="prev")

    prompts = []

    def runner(prompt, *, command, cwd, events_path, timeout, **_):
        prompts.append(prompt)
        last = Path(command[command.index("-o") + 1])
        cands = [{"url": f"https://www.tiktok.com/@new{i}/video/{50000000000 + i}", "why": "USB portable cup"}
                 for i in range(4)] + [{"url": old[0], "why": "again"}]
        last.write_text(json.dumps({"keywords": ["licuadora portátil"], "candidates": cands}), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    def screener(request, dicts, **kwargs):
        per = {d["clip_id"]: {"accepted": True, "reason": "ok",
                              "usable_segments": [{"start_s": 0, "end_s": 7, "product_prominent": True}]}
               for d in dicts}
        return ScreenOutcome(True, "ok", "手持随行杯式榨汁机", per)

    result = collect_footage(req, tmp_path / "work", discovery_runner=runner, downloader=_fake_downloader(),
                             screener=screener, seed_dir=seed_dir, log=lambda _m: None)
    assert result.ok, result.reason
    assert prompts and "40000000000" in prompts[0]  # used IDs listed as do-not-return
    ids = {c.clip_id for c in result.clips}
    assert ids and all(i.startswith("5") for i in ids), ids
    # collection alone must NOT mark footage as used (only delivery does)
    assert not (ids & UsedFootageLedger.load(seed_dir, req, profile))


def test_used_footage_fills_in_when_fresh_is_short(tmp_path):
    from workflow.unattended.discover_speed import UsedFootageLedger
    from workflow.unattended.vision import ScreenOutcome

    req = {"product": "便携榨汁机", "market": "MX", "style": "TikTok种草", "count": 1, "title": "t"}
    seed_dir = tmp_path / "seeds"
    old = [f"https://www.tiktok.com/@old{i}/video/{40000000000 + i}" for i in range(4)]
    save_seed_candidates(seed_dir, req, candidates=[{"url": u} for u in old], product_definition="手持随行杯")
    UsedFootageLedger.record(seed_dir, req, [u.rsplit("/", 1)[1] for u in old],
                    profile=infer_form_hints("便携榨汁机").profile)

    def runner(prompt, *, command, cwd, events_path, timeout, **_):
        last = Path(command[command.index("-o") + 1])
        last.write_text(json.dumps({"keywords": ["x"], "candidates": []}), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    def screener(request, dicts, **kwargs):
        per = {d["clip_id"]: {"accepted": True, "reason": "ok",
                              "usable_segments": [{"start_s": 0, "end_s": 7, "product_prominent": True}]}
               for d in dicts}
        return ScreenOutcome(True, "ok", "手持随行杯式榨汁机", per)

    result = collect_footage(req, tmp_path / "work", discovery_runner=runner, downloader=_fake_downloader(),
                             screener=screener, seed_dir=seed_dir, log=lambda _m: None)
    assert result.ok, result.reason
    assert "旧素材补位" in result.reason
    assert result.timings["reused_clips"] >= 1


def test_download_failure_reports_real_reason(tmp_path):
    from workflow.unattended.collect import DownloadFailure

    warn = "WARNING: [TikTok] The extractor is attempting impersonation, but no impersonate target is available"
    (tmp_path / "123.m4a").write_bytes(b"x")
    assert "只有音频" in DownloadFailure.describe(0, warn, tmp_path, "123")
    msg = DownloadFailure.describe(1, warn + "\nERROR: [TikTok] 456: Unable to extract webpage video data", tmp_path, "456")
    assert "Unable to extract" in msg and "impersonat" not in msg
    assert "impersonat" not in DownloadFailure.describe(1, warn, tmp_path, "789")



def test_download_failure_maps_tiktok_unreachable(tmp_path):
    from workflow.unattended.collect import DownloadFailure

    msg = DownloadFailure.describe(
        1,
        "ERROR: [TikTok] 456: Unable to extract webpage video data",
        tmp_path,
        "456",
    )
    assert "TikTok 不可达" in msg
    assert DownloadFailure.is_unreachable(msg)
    assert "全局" in msg or "HTTPS_PROXY" in msg
    assert not DownloadFailure.is_unreachable("不是视频：只有音频（多半是图文/轮播帖），已跳过")


def test_ytdlp_proxy_args_from_env(monkeypatch):
    from workflow.unattended import collect as mod

    monkeypatch.delenv("VW_YTDLP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    assert mod.ytdlp_proxy_args() == []
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    assert mod.ytdlp_proxy_args() == ["--proxy", "http://127.0.0.1:7897"]
    monkeypatch.setenv("VW_YTDLP_PROXY", "http://127.0.0.1:7890")
    assert mod.ytdlp_proxy_args() == ["--proxy", "http://127.0.0.1:7890"]


def test_collect_fails_fast_on_tiktok_unreachable_downloads(tmp_path):
    from workflow.unattended.collect import Candidate, collect_footage

    def runner(prompt, *, command, cwd, events_path, timeout):
        last = Path(command[command.index("-o") + 1])
        payload = {
            "keywords": ["licuadora"],
            "candidates": [
                {"url": f"https://www.tiktok.com/@c{i}/video/{10000000000 + i}"} for i in range(6)
            ],
        }
        last.write_text(json.dumps(payload), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    def downloader(cand: Candidate, raw_dir: Path, *, ffmpeg: str):
        cand.status = "download_failed"
        cand.failure_reason = (
            "TikTok 不可达（访问受限）。yt-dlp: ERROR: [TikTok] Unable to extract webpage video data。"
            "处理：请把 Clash 切全局"
        )

    result = collect_footage(
        REQ,
        tmp_path,
        discovery_runner=runner,
        downloader=downloader,
        log=lambda _m: None,
        screen=False,
        discover_rounds=2,
        discover_attempts=1,
        skip_discovery_if_seeds=True,
    )
    assert not result.ok
    assert "TikTok 不可达" in result.reason
    assert "停止空转" in result.reason
    # Should not have burned a second discovery round after network block.
    assert result.timings.get("discovery_calls", 0) <= 1


def test_discovery_admits_no_search_detection():
    from workflow.unattended.collect import discovery_admits_no_search

    assert discovery_admits_no_search("本次会话没有可用的网页搜索工具，无法检索并核实 TikTok 链接")
    assert not discovery_admits_no_search('{"candidates":[{"url":"https://www.tiktok.com/@a/video/1"}]}')


def test_discover_prompt_forbids_inventing_urls_without_web_search():
    from workflow.unattended.collect import render_discover_prompt

    prompt = render_discover_prompt(REQ, max_candidates=10)
    assert "无 web_search" in prompt or "没有可用的 web_search" in prompt
    assert "不要编造" in prompt

def test_used_ledger_parallel_and_corrupt(tmp_path):
    import threading
    from workflow.unattended.discover_speed import UsedFootageLedger
    req = {"product": "便携榨汁机", "market": "MX"}
    ts = [threading.Thread(target=UsedFootageLedger.record, args=(tmp_path, req, [f"{k}{i}" for i in range(20)]),
                           kwargs={"profile": "p"}) for k in "abcdefgh"]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(UsedFootageLedger.load(tmp_path, req, "p")) == 160
    path = UsedFootageLedger.path(tmp_path, req, "p")
    path.write_text("{broken", encoding="utf-8")
    assert UsedFootageLedger.load(tmp_path, req, "p") == set()
    UsedFootageLedger.record(tmp_path, req, ["z1"], profile="p")
    assert UsedFootageLedger.load(tmp_path, req, "p") == {"z1"}
    assert list(tmp_path.glob("used/*.corrupt-*"))


def test_generic_words_do_not_pull_other_products_into_blender_profile():
    assert infer_form_hints("便携颈挂风扇").profile == "neck_fan"
    assert infer_form_hints("便携小风扇").profile != "portable_blender_cup"
    assert infer_form_hints("随行保温杯").profile != "portable_blender_cup"
    assert infer_form_hints("便携榨汁杯").profile == "portable_blender_cup"


def test_used_ids_are_global_across_markets(tmp_path):
    from workflow.unattended.discover_speed import UsedFootageLedger
    UsedFootageLedger.record(tmp_path, {"product": "便携榨汁机", "market": "MX"}, ["111"], profile="portable_blender_cup")
    assert "111" in UsedFootageLedger.load(tmp_path, {"product": "便携颈挂风扇", "market": "PH"}, "neck_fan")


def test_each_portable_product_matches_only_its_own_profile():
    cases = {
        "便携颈挂风扇": "neck_fan", "ventilador de cuello": "neck_fan",
        "便携榨汁杯": "portable_blender_cup", "licuadora portátil": "portable_blender_cup",
        "便携洗碗刷": "cleaning_brush",
        "便携小风扇": "generic", "便携随行保温杯": "generic",
    }
    for product, want in cases.items():
        assert infer_form_hints(product).profile == want, product


def test_review_prompts_put_product_name_above_definition():
    root = Path(__file__).resolve().parents[1] / "workflow" / "unattended" / "prompts"
    for name in ("export_review.md", "footage_screen.md"):
        assert "以商品名称为准" in (root / name).read_text(encoding="utf-8")


def test_avoidlist_build_keeps_most_recent_used_ids(tmp_path):
    import json as _json
    from workflow.unattended.collect import AvoidList
    from workflow.unattended.discover_speed import UsedFootageLedger
    used = tmp_path / "used"; used.mkdir()
    # 150 IDs whose numeric order is the REVERSE of their use order: the old [-120:] slice kept the wrong ones
    rows = [{"video_id": str(9000 - i), "used_at": f"2026-10-{1 + i // 10:02d}T00:00:{i % 10:02d}+00:00"} for i in range(150)]
    (used / "a.json").write_text(_json.dumps({"used": rows[:80]}), encoding="utf-8")
    (used / "b.json").write_text(_json.dumps({"used": rows[80:]}), encoding="utf-8")
    recent = UsedFootageLedger.recent(tmp_path, 120)
    assert recent == [r["video_id"] for r in rows[30:]]
    assert len(UsedFootageLedger.load(tmp_path)) == 150
    out = AvoidList.build({"1", "2"}, recent, 120)
    assert len(out) == 120 and out[-2:] == ["1", "2"] and str(9000 - 149) in out and str(9000 - 30) not in out


def test_ledger_ids_match_clip_ids_including_reused(tmp_path):
    """clip_id is the TikTok video_id (the .reused tag only names a screen folder), so the ID that
    finish() counts as reused, the ID the producer records, and the ledger contents all agree."""
    from workflow.unattended.discover_speed import UsedFootageLedger
    from workflow.unattended.vision import ScreenOutcome

    req = {"product": "便携榨汁机", "market": "MX", "style": "TikTok种草", "count": 1, "title": "t"}
    seed_dir = tmp_path / "seeds"
    old = [f"https://www.tiktok.com/@old{i}/video/{40000000000 + i}" for i in range(4)]
    save_seed_candidates(seed_dir, req, candidates=[{"url": u} for u in old], product_definition="手持随行杯")
    profile = infer_form_hints("便携榨汁机").profile
    old_ids = [u.rsplit("/", 1)[1] for u in old]
    UsedFootageLedger.record(seed_dir, req, old_ids, profile=profile)

    def runner(prompt, *, command, cwd, events_path, timeout, **_):
        last = Path(command[command.index("-o") + 1])
        last.write_text(json.dumps({"keywords": ["x"], "candidates": []}), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    def screener(request, dicts, **kwargs):
        per = {d["clip_id"]: {"accepted": True, "reason": "ok",
                              "usable_segments": [{"start_s": 0, "end_s": 7, "product_prominent": True}]}
               for d in dicts}
        return ScreenOutcome(True, "ok", "手持随行杯式榨汁机", per)

    result = collect_footage(req, tmp_path / "work", discovery_runner=runner, downloader=_fake_downloader(),
                             screener=screener, seed_dir=seed_dir, log=lambda _m: None)
    assert result.ok, result.reason
    ids = [c.clip_id for c in result.clips]
    assert all(i.isdigit() for i in ids), ids  # never "123.reused" or a path stem
    assert all(Path(c.path).stem == c.clip_id for c in result.clips)
    reused = [i for i in ids if i in set(old_ids)]
    assert reused and result.timings["reused_clips"] == len(reused)
    assert f"旧素材补位 {len(reused)} 条" in result.reason
    # what the producer records after delivery (clip dicts' clip_id) lands in the ledger unchanged
    UsedFootageLedger.record(seed_dir, req, [str(i) for i in ids], profile=profile, task_id="t2")
    assert set(ids) <= UsedFootageLedger.load(seed_dir, req, profile)
    entries = [e for e in UsedFootageLedger.entries(seed_dir) if e["task_id"] == "t2"]
    assert {e["video_id"] for e in entries} == set(ids) - set(old_ids)  # re-used ones are not duplicated


# --- resume / form hints (2026-10-21 Windows e2e speed) -----------------------
from workflow.unattended.producer import qualifying_delivery_videos, review_issues_need_new_footage


def test_infer_form_hints_lip_balm_rejects_eyebrow_pencil():
    hints = infer_form_hints("巴西润唇膏", "真人试用种草")
    assert hints.profile == "lip_balm"
    assert "眉笔" in " ".join(hints.reject_markers) or "eyebrow" in " ".join(hints.reject_markers).lower()
    reason = text_suggests_wrong_form("this is an eyebrow pencil demo", hints)
    assert reason is not None
    assert "eyebrow" in reason.lower() or "眉" in reason


def test_qualifying_delivery_videos_and_resume_skips_recut(tmp_path, monkeypatch):
    inbox = Inbox(tmp_path / "data")
    task_id = "task_resume_001"
    task_dir = inbox.task_dir(task_id)
    delivery = task_dir / "delivery"
    delivery.mkdir(parents=True)
    good = delivery / "BR-task-chatcut.mp4"
    good.write_bytes(b"x" * 350_000)
    assert [p.name for p in qualifying_delivery_videos(delivery)] == [good.name]

    calls = {"collect": 0, "codex": 0}

    def fake_collector(*a, **k):
        calls["collect"] += 1
        raise AssertionError("collect must not run when delivery already has mp4")

    def fake_runner(prompt, **kwargs):
        calls["codex"] += 1
        raise AssertionError("codex must not run when delivery already has mp4")

    class Gate:
        ok = True
        reason = "test"

    monkeypatch.setattr("workflow.unattended.producer.verify_codex_chatcut_mcp_ready", lambda: Gate())
    producer = Producer(
        inbox, probe=lambda: {"healthy": True}, codex_runner=fake_runner,
        options=ProduceOptions(mode="production", project_root=tmp_path, chatcut_agent_log=None,
                               collector=fake_collector),
        log=lambda _m: None,
    )
    result = producer.produce(task_id=task_id, workflow_job_id="job_r", request=REQ)
    assert result.status == "produced" and result.renderer == "chatcut_resumed"
    assert calls["collect"] == 0 and calls["codex"] == 0
    assert inbox.read_task_state(task_id)["status"] == "produced"
    assert "跳过重剪" in result.reason


def test_review_issues_need_new_footage_markers():
    assert review_issues_need_new_footage(["开头镜头有原片烧录文字"])
    assert review_issues_need_new_footage(["第2镜头不是同款商品（眉笔）"])
    assert not review_issues_need_new_footage(["结尾CTA镜头是纯生活方式画面，看不清商品"])


def test_search_markets_for_round_task_then_expand():
    assert search_markets_for_round("MX", round_num=1) == ["MX"]
    assert search_markets_for_round("BR", round_num=1) == ["BR"]
    assert search_markets_for_round("MX", round_num=2) == list(SUPPORTED_COLLECTION_MARKETS)
    assert search_markets_for_round("BR", round_num=2)[0] == "BR"
    assert set(search_markets_for_round("PH", round_num=3, expand_all=True)) == set(SUPPORTED_COLLECTION_MARKETS)


def test_render_discover_prompt_all_regions_scope():
    prompt = render_discover_prompt(
        {"product": "护手霜", "market": "MX", "style": "种草", "notes": ""},
        max_candidates=12, max_searches=5,
        search_markets=["MX", "BR", "ID", "PH"],
    )
    assert "{{" not in prompt
    assert "全区扩采" in prompt
    assert "BR" in prompt and "ID" in prompt and "PH" in prompt
    assert "交付市场仍是 **MX**" in prompt or "交付市场仍是 **MX**" in prompt.replace(" ", "")
    assert "不一致也可以入库" in prompt
    # expanded scope bumps search budget by 2
    assert "最多 7 次 web_search" in prompt


def test_seed_cache_merges_cross_market(tmp_path):
    mx = {"product": "护手霜", "market": "MX"}
    br = {"product": "护手霜", "market": "BR"}
    save_seed_candidates(
        tmp_path, mx,
        candidates=[{"url": "https://www.tiktok.com/@mx/video/11111111111"}],
    )
    save_seed_candidates(
        tmp_path, br,
        candidates=[{"url": "https://www.tiktok.com/@br/video/22222222222"}],
    )
    only_mx = load_seed_candidates(tmp_path, mx, cross_market=False)
    assert [c["url"] for c in only_mx] == ["https://www.tiktok.com/@mx/video/11111111111"]
    merged = load_seed_candidates(tmp_path, mx, cross_market=True)
    urls = {c["url"] for c in merged}
    assert "https://www.tiktok.com/@mx/video/11111111111" in urls
    assert "https://www.tiktok.com/@br/video/22222222222" in urls


def test_collect_expands_markets_on_round_two(tmp_path):
    """Round 1 stays on task market; round 2 prompt must expand to all four."""
    from workflow.unattended.vision import ScreenOutcome

    req = {"product": "护手霜", "market": "MX", "style": "TikTok种草", "count": 1, "title": "t"}
    prompts: list[str] = []
    accepted = {"n": 0}
    urls_r1 = [f"https://www.tiktok.com/@a/video/{30000000000 + i}" for i in range(1)]
    urls_r2 = [f"https://www.tiktok.com/@b/video/{40000000000 + i}" for i in range(4)]

    def runner(prompt, **kwargs):
        prompts.append(prompt)
        last = Path(kwargs["command"][kwargs["command"].index("-o") + 1])
        if len(prompts) == 1:
            payload = {"keywords": ["crema"], "candidates": [{"url": u} for u in urls_r1]}
        else:
            payload = {"keywords": ["crema", "hand cream"], "candidates": [{"url": u} for u in urls_r2]}
        last.write_text(__import__("json").dumps(payload), encoding="utf-8")
        return 0

    def downloader(cand, raw_dir, ffmpeg):
        dest = Path(raw_dir) / f"{cand.video_id}.mp4"
        _make_video(dest, seconds=6)
        cand.status = "downloaded"
        cand.raw_path = str(dest)

    def screener(request, clips, **_kwargs):
        per = {
            str(c["clip_id"]): {
                "accepted": True,
                "reason": "ok",
                "usable_segments": [{"start_s": 0, "end_s": 5, "product_prominent": True}],
            }
            for c in clips
        }
        accepted["n"] += len(clips)
        # Only declare screen ok once floors are met so collect continues into expand round.
        ok = accepted["n"] >= 3
        return ScreenOutcome(ok, "ok" if ok else "不足", "护手霜", per)

    result = collect_footage(
        req, tmp_path / "work",
        discovery_runner=runner, downloader=downloader, screener=screener,
        discover_rounds=2, discover_attempts=1, min_clips=3, max_clips=6, max_candidates=6,
        skip_discovery_if_seeds=False, avoid_used=False, seed_dir=None,
        log=lambda _m: None, screen=True,
    )
    assert len(prompts) >= 2
    assert "优先交付市场" in prompts[0] or "优先在交付市场" in prompts[0]
    assert "全区扩采" in prompts[1]
    assert "BR" in prompts[1] and "ID" in prompts[1]
    assert result.timings.get("discovery_scope") == "all_regions"
    assert result.ok


def test_discovery_rate_limit_detection_and_not_no_search():
    from workflow.unattended.collect import discovery_admits_no_search, discovery_is_rate_limited

    blob = "Error: 429 Too Many Requests while calling web_search"
    assert discovery_is_rate_limited(blob)
    assert not discovery_admits_no_search(blob)
    assert discovery_is_rate_limited("请求过多 / 频率限制")
    assert not discovery_is_rate_limited('{"candidates":[{"url":"https://www.tiktok.com/@a/video/1"}]}')


def test_download_failure_rate_limit_not_unreachable(tmp_path):
    from workflow.unattended.collect import DownloadFailure

    msg = DownloadFailure.describe(
        1,
        "ERROR: [TikTok] 456: HTTP Error 429: Too Many Requests",
        tmp_path,
        "456",
    )
    assert "限流" in msg or "429" in msg
    assert DownloadFailure.is_rate_limited(msg)
    assert not DownloadFailure.is_unreachable(msg)


def test_collect_partial_ok_when_short_of_batch_floor(tmp_path, monkeypatch):
    """Batch floor needs many clips; with only a handful accepted still ok for partial deliver."""
    from workflow.unattended.batch_footage import CollectTargets
    from workflow.unattended.vision import ScreenOutcome

    # Keep discovery cheap while still exercising the high floor / partial path.
    monkeypatch.setattr(
        "workflow.unattended.collect.collect_targets",
        lambda count: CollectTargets(
            min_clips=20,
            max_clips=30,
            max_candidates=40,
            discover_rounds=2,
            max_searches=5,
            screen_min_accepted=20,
            screen_min_usable_s=100.0,
            screen_min_prominent=20,
        ),
    )
    req = {"product": "眼霜", "market": "MX", "count": 10, "style": "种草"}
    seen = {"n": 0}

    def runner(prompt, *, command, cwd, events_path, timeout):
        seen["n"] += 1
        last = Path(command[command.index("-o") + 1])
        base = 20000000000 + seen["n"] * 100
        urls = [f"https://www.tiktok.com/@eye/video/{base + i}" for i in range(2)]
        last.write_text(__import__("json").dumps({"keywords": ["eye cream"], "candidates": [{"url": u} for u in urls]}), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    def screener(request, dicts, **kwargs):
        per = {
            d["clip_id"]: {
                "accepted": True,
                "reason": "ok",
                "usable_segments": [{"start_s": 0, "end_s": 6, "product_prominent": True}],
            }
            for d in dicts
        }
        return ScreenOutcome(False, "不足", "眼霜", per)

    result = collect_footage(
        req,
        tmp_path,
        discovery_runner=runner,
        downloader=_fake_downloader(),
        screener=screener,
        log=lambda _m: None,
        discover_attempts=1,
        skip_discovery_if_seeds=True,
        avoid_used=False,
        seed_dir=None,
    )
    assert result.ok, result.reason
    assert len(result.clips) >= 2
    assert result.timings.get("partial_pool") == 1.0
    assert "缺素材" in result.reason and "部分交付" in result.reason



def test_thin_seed_cache_does_not_skip_discovery_for_batch(tmp_path, monkeypatch):
    """Regression (IZ MX eye-cream): 2 cached seeds + weak screen_ok must NOT set
    discovery_calls=0 / skip MX→BR/ID/PH expand when count needs a large pool.
    """
    from workflow.unattended.batch_footage import CollectTargets
    from workflow.unattended.vision import ScreenOutcome

    monkeypatch.setattr(
        "workflow.unattended.collect.collect_targets",
        lambda count: CollectTargets(
            min_clips=20,
            max_clips=30,
            max_candidates=40,
            discover_rounds=3,
            max_searches=5,
            screen_min_accepted=20,
            screen_min_usable_s=100.0,
            screen_min_prominent=20,
        ),
    )
    req = {"product": "眼霜", "market": "MX", "count": 10, "style": "种草", "title": "t"}
    seed_dir = tmp_path / "seeds"
    # Only two previously-accepted links — historically enough to trip seed_screen_ok.
    urls = [f"https://www.tiktok.com/@seed/video/{50000000000 + i}" for i in range(2)]
    save_seed_candidates(seed_dir, req, candidates=[{"url": u} for u in urls], product_definition="眼霜")

    prompts: list[str] = []
    calls = {"n": 0}

    def runner(prompt, *, command, cwd, events_path, timeout):
        calls["n"] += 1
        prompts.append(prompt)
        last = Path(command[command.index("-o") + 1])
        base = 60000000000 + calls["n"] * 100
        # Each round only yields a couple of URLs so the batch floor stays unmet.
        payload = {
            "keywords": ["eye cream", "contorno de ojos"],
            "candidates": [{"url": f"https://www.tiktok.com/@eye/video/{base + i}"} for i in range(2)],
        }
        last.write_text(json.dumps(payload), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    def screener(request, dicts, **kwargs):
        # Mimic old vision default: mark this tiny batch "ok" (≥2) while kwargs
        # carry the real batch floor — collect must ignore weak screen_ok.
        per = {
            d["clip_id"]: {
                "accepted": True,
                "reason": "ok",
                "usable_segments": [{"start_s": 0, "end_s": 8, "product_prominent": True}],
            }
            for d in dicts
        }
        return ScreenOutcome(True, "weak-ok", "眼霜", per)

    result = collect_footage(
        req,
        tmp_path / "work",
        discovery_runner=runner,
        downloader=_fake_downloader(),
        screener=screener,
        seed_dir=seed_dir,
        log=lambda _m: None,
        discover_attempts=1,
        skip_discovery_if_seeds=True,
        avoid_used=False,
    )
    assert calls["n"] >= 2, f"expected multi-round discovery, got {calls['n']}: {result.reason}"
    assert result.timings.get("discovery_calls", 0) >= 2
    # Later rounds must actually expand beyond task market.
    assert any("全区扩采" in p or "BR" in p for p in prompts[1:]), prompts
    assert result.timings.get("discovery_scope") == "all_regions"
    # Thin pool → partial ok is fine; claiming full seed-cache success is not.
    assert "未跑 Codex 检索" not in result.reason
    assert result.ok
    assert result.timings.get("partial_pool") == 1.0


def test_collect_retries_discovery_on_429(tmp_path, monkeypatch):
    from workflow.unattended import collect as mod

    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def runner(prompt, *, command, cwd, events_path, timeout):
        calls["n"] += 1
        last = Path(command[command.index("-o") + 1])
        if calls["n"] == 1:
            last.write_text("429 Too Many Requests from web_search", encoding="utf-8")
            events_path.write_text("rate limit", encoding="utf-8")
            return 1
        payload = {
            "keywords": ["crema"],
            "candidates": [
                {"url": f"https://www.tiktok.com/@ok/video/{30000000000 + i}"} for i in range(4)
            ],
        }
        last.write_text(__import__("json").dumps(payload), encoding="utf-8")
        events_path.write_text("", encoding="utf-8")
        return 0

    result = collect_footage(
        {"product": "护手霜", "market": "MX", "count": 1},
        tmp_path,
        discovery_runner=runner,
        downloader=_fake_downloader(),
        log=lambda _m: None,
        screen=False,
        discover_attempts=1,
        discover_rounds=1,
        min_clips=3,
        max_clips=6,
        max_candidates=6,
        skip_discovery_if_seeds=True,
        avoid_used=False,
        seed_dir=None,
    )
    assert calls["n"] >= 2
    assert sleeps, "expected backoff sleep on 429"
    assert result.timings.get("discovery_rate_limited", 0) >= 1
    assert result.ok, result.reason
