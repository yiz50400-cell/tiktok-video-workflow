from __future__ import annotations

import json
import subprocess
from pathlib import Path

from workflow.unattended.inbox import Inbox
from workflow.unattended.producer import (
    MIN_DELIVERY_BYTES,
    ProduceOptions,
    Producer,
    build_codex_command,
    qualifying_delivery_videos,
)
from workflow.unattended.supervisor import Supervisor


class FakeWorkflowClient:
    def __init__(self):
        self.created = []

    def create_job(self, request):
        self.created.append(dict(request))
        return f"job_{len(self.created):04d}"

    def plan_batch(self, workflow_job_id, concepts):
        return None


def valid_payload(**updates):
    payload = {
        "task_id": "task_produce_001",
        "title": "墨西哥唇釉",
        "product": "唇釉",
        "market": "MX",
        "count": 2,
        "style": "自然带货",
    }
    payload.update(updates)
    return payload


def test_build_codex_command_includes_model_6a():
    command = build_codex_command(
        codex_bin="codex",
        project_root=Path("/tmp/proj"),
        last_message_path=Path("/tmp/last.txt"),
        model="gpt-5.6-sol",
        use_profile=True,
    )
    assert command[:4] == ["codex", "exec", "-m", "gpt-5.6-sol"]
    assert "-p" in command and "unattended-chatcut" in command
    assert 'approval_policy="never"' in command
    command2 = build_codex_command(
        codex_bin="codex", project_root=Path("/tmp/proj"),
        last_message_path=Path("/tmp/last.txt"),
        extra_writable_dirs=[Path("/tmp/data")], use_profile=False,
    )
    assert "--add-dir" in command2 and "/tmp/data" in command2


def test_producer_success_copies_delivery_and_audit(tmp_path):
    inbox = Inbox(tmp_path / "data")
    exports = None

    def fake_runner(prompt, **kwargs):
        nonlocal exports
        # producer creates exports_dir before calling runner; discover via prompt
        task_dir = inbox.task_dir("task_produce_001")
        exports = task_dir / "exports"
        video = exports / "out.mp4"
        video.write_bytes(b"x" * MIN_DELIVERY_BYTES)
        (task_dir / "produce-result.json").write_text(
            json.dumps({"ok": True, "export_paths": [str(video)], "renderer": "chatcut"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="ok", stderr="")

    producer = Producer(
        inbox,
        probe=lambda: {"healthy": True},
        codex_runner=fake_runner,
        options=ProduceOptions(mode="canary", codex_model="gpt-5.6-sol", project_root=tmp_path),
        log=lambda _m: None,
    )
    result = producer.produce(
        task_id="task_produce_001",
        workflow_job_id="job_0001",
        request=valid_payload(),
    )
    assert result.status == "produced"
    assert result.renderer == "chatcut"
    assert len(result.delivery_paths) == 1
    assert Path(result.delivery_paths[0]).is_file()
    audit = inbox.task_dir("task_produce_001") / "audit" / "unattended-auto-approve.json"
    assert audit.is_file()
    body = json.loads(audit.read_text(encoding="utf-8"))
    assert body["policy"] == "unattended_auto_approve"
    assert body["actor"] == "unattended-policy"
    state = inbox.read_task_state("task_produce_001")
    assert state["status"] == "produced"
    assert state["produce"]["codex_model"] == "gpt-5.6-sol"


def test_producer_codex_failure_without_fallback(tmp_path):
    inbox = Inbox(tmp_path / "data")

    def fake_runner(prompt, **kwargs):
        return subprocess.CompletedProcess(args=["codex"], returncode=7, stdout="", stderr="boom")

    producer = Producer(
        inbox,
        probe=lambda: {"healthy": True},
        codex_runner=fake_runner,
        options=ProduceOptions(mode="canary", allow_ffmpeg_fallback=False),
        log=lambda _m: None,
    )
    result = producer.produce(
        task_id="task_produce_fail",
        workflow_job_id="job_0002",
        request=valid_payload(task_id="task_produce_fail"),
    )
    assert result.status == "produce_failed"
    assert result.codex_returncode == 7
    assert inbox.read_task_state("task_produce_fail")["status"] == "produce_failed"


def test_supervisor_produce_false_matches_phase1(tmp_path):
    client = FakeWorkflowClient()
    supervisor = Supervisor(
        data_dir=tmp_path / "data",
        probe=lambda: {"healthy": True},
        workflow_client=client,
    )
    supervisor.inbox.enqueue(valid_payload(task_id="task_no_produce"))
    result = supervisor.run_once(produce=False)
    assert result["status"] == "succeeded"
    assert len(client.created) == 1
    assert result["workflow_job_id"].startswith("job_")


def test_supervisor_produce_true_uses_worker(tmp_path):
    client = FakeWorkflowClient()

    class FakeProducer:
        def __init__(self):
            self.calls = []

        def produce(self, *, task_id, workflow_job_id, request):
            self.calls.append((task_id, workflow_job_id, request["product"]))
            from workflow.unattended.producer import ProduceResult

            # mark state like real producer
            inbox = supervisor.inbox
            state = inbox.read_task_state(task_id) or {}
            state.update({"status": "produced", "reason": "mock", "workflow_job_id": workflow_job_id})
            inbox.write_task_state(task_id, state)
            delivery = inbox.task_dir(task_id) / "delivery" / "x.mp4"
            delivery.parent.mkdir(parents=True, exist_ok=True)
            delivery.write_bytes(b"x")
            return ProduceResult(
                status="produced",
                reason="mock produced",
                delivery_paths=[str(delivery)],
                renderer="chatcut",
                codex_returncode=0,
            )

    fake_producer = FakeProducer()
    supervisor = Supervisor(
        data_dir=tmp_path / "data",
        probe=lambda: {"healthy": True},
        workflow_client=client,
        producer=fake_producer,
    )
    supervisor.inbox.enqueue(valid_payload(task_id="task_with_produce"))
    result = supervisor.run_once(produce=True)
    assert result["status"] == "produced"
    assert fake_producer.calls and fake_producer.calls[0][0] == "task_with_produce"
    assert len(list(supervisor.inbox.paths.done.glob("*.json"))) == 1


def test_timeout_log_handles_bytes(tmp_path, monkeypatch):
    inbox = Inbox(tmp_path / "data")

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=1, output=b"out-bytes", stderr=b"err-bytes")

    monkeypatch.setattr("workflow.unattended.producer.subprocess.run", boom)
    from workflow.unattended.producer import default_codex_runner

    log_path = tmp_path / "log.json"
    try:
        default_codex_runner(
            "hi",
            cwd=tmp_path,
            log_path=log_path,
            last_message_path=tmp_path / "last.txt",
            timeout=1,
            project_root=tmp_path,
        )
        assert False, "expected TimeoutExpired"
    except subprocess.TimeoutExpired:
        pass
    body = json.loads(log_path.read_text(encoding="utf-8"))
    assert body["ok"] is False
    assert "out-bytes" in body["stdout"]
    assert "err-bytes" in body["stderr"]


def test_redact_secrets_in_as_text():
    from workflow.unattended.producer import _as_text
    raw = 'experimental_bearer_token = "sk-secret-value-here"'
    out = _as_text(raw)
    assert "sk-secret-value-here" not in out
    assert "***" in out


# --- ChatCut in-app AI Agent guard (2026-10-19) ---
from datetime import datetime, timedelta, timezone

from workflow.unattended.producer import (
    FORBIDDEN_INAPP_AGENT_RULES,
    detect_inapp_agent_runs,
    render_produce_prompt,
)


def _agent_log_lines(when: datetime, request_id: str = "req-1", cost: float = 0.3093) -> str:
    iso = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return "\n".join(
        [
            json.dumps({"level": "info", "msg": "Agent SDK attempt started", "time": iso,
                        "projectId": "proj-x", "requestId": request_id}),
            json.dumps({"level": "debug", "msg": "Execution completed", "time": iso,
                        "cost": cost, "durationMs": 74672, "requestId": request_id}),
        ]
    ) + "\n"


def test_prompt_forbids_inapp_agent_even_for_custom_template(tmp_path):
    custom = tmp_path / "custom.md"
    custom.write_text("自定义模板 {{task_id}}", encoding="utf-8")
    for template in (None, custom):
        prompt = render_produce_prompt(
            request={}, task_id="t1", workflow_job_id="job_1",
            exports_dir=tmp_path, delivery_dir=tmp_path, result_path=tmp_path / "r.json",
            template_path=template,
        )
        assert prompt.startswith(FORBIDDEN_INAPP_AGENT_RULES)
        assert "AI Agent" in prompt and "Computer Use" in prompt and "submit_" in prompt
        assert "chatcut_desktop MCP tools unavailable" in prompt


def test_detect_inapp_agent_runs_filters_by_time(tmp_path):
    log = tmp_path / "agent.log"
    now = datetime.now(timezone.utc)
    log.write_text(
        _agent_log_lines(now - timedelta(hours=1), "old", 0.07)
        + "not json\n"
        + _agent_log_lines(now + timedelta(seconds=1), "new", 0.31),
        encoding="utf-8",
    )
    runs = detect_inapp_agent_runs(log, now)
    assert [r["request_id"] for r in runs] == ["new"]
    assert runs[0]["cost"] == 0.31 and runs[0]["duration_ms"] == 74672
    assert detect_inapp_agent_runs(tmp_path / "missing.log", now) == []


def test_producer_fails_when_inapp_agent_used(tmp_path):
    inbox = Inbox(tmp_path / "data")
    agent_log = tmp_path / "agent.log"
    agent_log.write_text("", encoding="utf-8")

    def fake_runner(prompt, **kwargs):
        assert prompt.startswith(FORBIDDEN_INAPP_AGENT_RULES)
        task_dir = inbox.task_dir("task_produce_001")
        video = task_dir / "exports" / "out.mp4"
        video.write_bytes(b"fake-video")
        (task_dir / "produce-result.json").write_text(
            json.dumps({"ok": True, "export_paths": [str(video)]}), encoding="utf-8"
        )
        # simulate Codex typing into ChatCut's in-app agent during the run
        with agent_log.open("a", encoding="utf-8") as fh:
            fh.write(_agent_log_lines(datetime.now(timezone.utc) + timedelta(seconds=1)))
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="ok", stderr="")

    producer = Producer(
        inbox,
        probe=lambda: {"healthy": True},
        codex_runner=fake_runner,
        options=ProduceOptions(mode="canary", project_root=tmp_path, chatcut_agent_log=agent_log),
        log=lambda _m: None,
    )
    result = producer.produce(task_id="task_produce_001", workflow_job_id="job_0001", request=valid_payload())
    assert result.status == "produce_failed"
    assert "AI Agent" in result.reason and "0.3093" in result.reason
    assert result.renderer == "chatcut_inapp_agent"
    assert len(result.delivery_paths) == 1  # export kept for human review
    violation = inbox.task_dir("task_produce_001") / "logs" / "inapp-agent-violation.json"
    assert json.loads(violation.read_text(encoding="utf-8"))[0]["cost"] == 0.3093


def test_producer_guard_can_be_disabled(tmp_path):
    inbox = Inbox(tmp_path / "data")
    agent_log = tmp_path / "agent.log"

    def fake_runner(prompt, **kwargs):
        task_dir = inbox.task_dir("task_produce_001")
        video = task_dir / "exports" / "out.mp4"
        video.write_bytes(b"x" * MIN_DELIVERY_BYTES)
        (task_dir / "produce-result.json").write_text(
            json.dumps({"ok": True, "export_paths": [str(video)], "renderer": "chatcut"}),
            encoding="utf-8",
        )
        agent_log.write_text(_agent_log_lines(datetime.now(timezone.utc) + timedelta(seconds=1)), encoding="utf-8")
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="ok", stderr="")

    producer = Producer(
        inbox, probe=lambda: {"healthy": True}, codex_runner=fake_runner,
        options=ProduceOptions(mode="canary", project_root=tmp_path, chatcut_agent_log=agent_log, forbid_inapp_agent=False),
        log=lambda _m: None,
    )
    result = producer.produce(task_id="task_produce_001", workflow_job_id="job_0001", request=valid_payload())
    assert result.status == "produced"


# --- Codex MCP visibility gate (2026-10-19) ---
from workflow.unattended.codex_mcp_gate import (
    VALID_CHATCUT_MCP_CLIENTS,
    verify_codex_chatcut_mcp_ready,
)


def test_codex_mcp_gate_rejects_invalid_client(tmp_path):
    cfg = tmp_path / "unattended-chatcut.config.toml"
    cfg.write_text(
        "\n".join(
            [
                "[features]",
                "code_mode_host = true",
                "[mcp_servers.chatcut_desktop]",
                'command = "/Applications/ChatCut.app/Contents/MacOS/ChatCut"',
                "[mcp_servers.chatcut_desktop.env]",
                'CHATCUT_MCP_CLIENT = "codex_unattended"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = verify_codex_chatcut_mcp_ready(codex_home=tmp_path, probe_tools=False)
    assert result.ok is False
    assert "codex_unattended" in result.reason
    assert "codex_unattended" not in VALID_CHATCUT_MCP_CLIENTS


def test_codex_mcp_gate_rejects_code_mode_host_false(tmp_path):
    cfg = tmp_path / "unattended-chatcut.config.toml"
    cfg.write_text(
        "\n".join(
            [
                "[features]",
                "code_mode_host = false",
                "[mcp_servers.chatcut_desktop]",
                'command = "/tmp/fake-mcp"',
                "[mcp_servers.chatcut_desktop.env]",
                'CHATCUT_MCP_CLIENT = "codex_app"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = verify_codex_chatcut_mcp_ready(codex_home=tmp_path, probe_tools=False)
    assert result.ok is False
    assert "code_mode_host" in result.reason


def test_build_codex_command_enables_code_mode_host(tmp_path):
    from workflow.unattended.producer import build_codex_command

    cmd = build_codex_command(
        codex_bin="codex",
        project_root=tmp_path,
        last_message_path=tmp_path / "last.txt",
        use_profile=False,
    )
    assert "--enable" in cmd and "code_mode_host" in cmd
    assert "--disable" in cmd and "computer_use" in cmd


# --- No-BGM rule + MCP parameter examples (2026-10-19) ---
from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parents[1]


def test_prompt_forbids_background_music(tmp_path):
    prompt = render_produce_prompt(
        request={}, task_id="t1", workflow_job_id="job_1",
        exports_dir=tmp_path, delivery_dir=tmp_path, result_path=tmp_path / "r.json",
    )
    assert "禁止背景音乐" in prompt
    assert "search_stock_media" in prompt
    assert "muted" in prompt


def test_default_template_has_verified_mcp_param_examples():
    text = (_ROOT / "workflow/unattended/prompts/chatcut_produce.md").read_text(encoding="utf-8")
    for needle in (
        '"adds": [{"type": "video"',
        '"deletes": [{"id"',
        '"updates": [{"id"',
        '没有 action、json 参数',
        '{"action": "create", "json": "{\\"trackType\\":\\"video\\",\\"count\\":1}"}',
        '{"action": "batch", "edits": [',
        '"format": "video", "codec": "h264"',
        '"exportTaskIds"',
        "sourceIn",
        "不加背景音乐",
    ):
        assert needle in text, needle


def test_agents_md_has_no_bgm_rule():
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "声音硬性规则" in text and "不加背景音乐" in text


def test_pipeline_rejects_background_music():
    import pytest
    from workflow.orchestration.pipeline import _reject_background_music

    _reject_background_music({"sound_effects": []})
    _reject_background_music({"sound_effects": [{"description_zh": "刷子转动音效", "path": "/a/whoosh.wav"}]})
    with pytest.raises(ValueError):
        _reject_background_music({"background_music": {"path": "/a/bgm.mp3"}})
    with pytest.raises(ValueError):
        _reject_background_music({"sound_effects": [{"description_zh": "欢快背景音乐", "path": "/a/x.mp3"}]})


def test_feedback_prompt_recreates_when_no_project(tmp_path):
    """No project_id → must create_project from scratch, never target missing project."""
    result = tmp_path / "produce-result.json"
    result.write_text(json.dumps({"ok": False, "error": "create_project failed"}), encoding="utf-8")
    exports = tmp_path / "exports"
    exports.mkdir()
    prompt = Producer._feedback_prompt(
        "BASE_PROMPT",
        ["制作未生成成片文件"],
        result,
        exports,
        "task_abc",
        "MX",
        1,
    )
    assert "从头新建" in prompt or "create_project" in prompt
    assert "禁止** create_project" not in prompt and "禁止 create_project" not in prompt
    assert "BASE_PROMPT" in prompt
    assert "没有成功创建" in prompt or "无 project_id" in prompt


def test_feedback_prompt_keeps_inplace_edit_when_project_exists(tmp_path):
    result = tmp_path / "produce-result.json"
    result.write_text(json.dumps({"ok": True, "project_id": "proj-123", "export_paths": []}), encoding="utf-8")
    exports = tmp_path / "exports"
    exports.mkdir()
    prompt = Producer._feedback_prompt(
        "BASE",
        ["结尾CTA不清"],
        result,
        exports,
        "task_abc",
        "MX",
        1,
    )
    assert "proj-123" in prompt
    assert "禁止" in prompt and "create_project" in prompt
    assert "同一个工程" in prompt


def test_producer_empty_export_fails_fast_no_delivery(tmp_path, monkeypatch):
    """Production + thin pool: Codex exit 0 but no mp4 → clear fail, delivery stays 0."""
    from workflow.unattended.collect import Clip

    inbox = Inbox(tmp_path / "data")
    (tmp_path / "a.mp4").write_bytes(b"x" * 4000)
    (tmp_path / "b.mp4").write_bytes(b"x" * 4000)
    segs = [{"start_s": 0, "end_s": 20, "product_prominent": True}]

    def _clip(cid, name):
        return Clip(
            clip_id=cid,
            path=str(tmp_path / name),
            source_url=f"https://www.tiktok.com/@a/video/{cid}",
            account="a",
            title="eye cream",
            duration=60.0,
            width=1080,
            height=1920,
            keyword_hit=True,
            visible_metrics={},
            sheet_path="",
            sheet_times=[],
            usable_segments=segs,
        )

    class FakeCollection:
        ok = True
        reason = "缺素材（部分池，非满额成功）：已采到可用 2 条"
        clips = [_clip("111", "a.mp4"), _clip("222", "b.mp4")]
        candidates = []
        keywords = ["eye cream"]
        timings = {"partial_pool": 1.0, "partial_kept": 2.0}
        product_definition = "眼霜"
        screened_out = []
        manifest_path = None

    def fake_runner(prompt, **kwargs):
        task_dir = inbox.task_dir("task_empty_export")
        (task_dir / "produce-result.json").write_text(
            json.dumps({"ok": False, "error": "create_project failed"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="ok", stderr="")

    class Gate:
        ok = True
        reason = "ready"
        details = {}

    monkeypatch.setattr(
        "workflow.unattended.producer.verify_codex_chatcut_mcp_ready",
        lambda **kwargs: Gate(),
    )
    producer = Producer(
        inbox,
        probe=lambda: {"healthy": True},
        codex_runner=fake_runner,
        options=ProduceOptions(
            mode="production",
            project_root=tmp_path,
            chatcut_agent_log=None,
            collector=lambda *a, **k: FakeCollection(),
            review_retries=2,
        ),
        log=lambda _m: None,
    )
    result = producer.produce(
        task_id="task_empty_export",
        workflow_job_id="job_empty",
        request=valid_payload(task_id="task_empty_export", product="眼霜", count=10),
    )
    assert result.status == "produce_failed"
    assert result.delivery_paths == []
    reason = result.reason or ""
    assert (
        "制作未生成成片文件" in reason
        or "无可用导出" in reason
        or "exports=0" in reason
        or "ok!=true" in reason
        or "delivery 为空" in reason
    ), reason
    assert "假成功" in reason or "delivery 为空" in reason or "ok!=true" in reason
    # Must not claim successful partial delivery.
    assert "已交付 1/10" not in reason


def test_eye_cream_form_hints_prefer_apply():
    from workflow.unattended.discover_speed import infer_form_hints, text_suggests_wrong_form

    h = infer_form_hints("墨西哥眼霜", "", "种草")
    assert h.profile == "eye_cream"
    assert any("apply" in q or "aplicar" in q for q in h.prefer_queries)
    assert text_suggests_wrong_form("tiktok mascara review", h)
    assert text_suggests_wrong_form("contorno de ojos apply under eye", h) is None


def test_collect_exports_ignores_orphan_rglob(tmp_path):
    """Leftover mp4 under exports/ must not count unless planned or in result paths."""
    inbox = Inbox(tmp_path / "data")
    task_dir = inbox.task_dir("task_orphan")
    exports = task_dir / "exports"
    exports.mkdir(parents=True)
    orphan = exports / "orphan-leftover.mp4"
    orphan.write_bytes(b"x" * MIN_DELIVERY_BYTES)
    result_path = task_dir / "produce-result.json"
    result_path.write_text(
        json.dumps({"ok": True, "export_paths": [str(exports / "missing-planned.mp4")]}),
        encoding="utf-8",
    )
    producer = Producer(inbox, probe=lambda: {"healthy": True}, log=lambda _m: None)
    producer._export_paths_planned = [exports / "MX-task_orphan-chatcut.mp4"]
    found = producer._collect_exports(exports, result_path)
    assert found == []


def test_collect_exports_accepts_result_and_planned(tmp_path):
    inbox = Inbox(tmp_path / "data")
    task_dir = inbox.task_dir("task_paths")
    exports = task_dir / "exports"
    exports.mkdir(parents=True)
    planned = exports / "MX-task_paths-chatcut.mp4"
    planned.write_bytes(b"x" * MIN_DELIVERY_BYTES)
    listed = exports / "listed.mp4"
    listed.write_bytes(b"x" * MIN_DELIVERY_BYTES)
    (exports / "orphan.mp4").write_bytes(b"x" * MIN_DELIVERY_BYTES)
    result_path = task_dir / "produce-result.json"
    result_path.write_text(
        json.dumps({"ok": True, "export_paths": [str(listed)]}),
        encoding="utf-8",
    )
    producer = Producer(inbox, probe=lambda: {"healthy": True}, log=lambda _m: None)
    producer._export_paths_planned = [planned]
    found = {p.name for p in producer._collect_exports(exports, result_path)}
    assert found == {"listed.mp4", "MX-task_paths-chatcut.mp4"}
    assert "orphan.mp4" not in found


def test_producer_rejects_tiny_delivery_as_false_success(tmp_path, monkeypatch):
    """exit 0 + tiny export copy must not become status=produced."""
    from workflow.unattended.collect import Clip

    inbox = Inbox(tmp_path / "data")
    (tmp_path / "a.mp4").write_bytes(b"x" * 4000)
    (tmp_path / "b.mp4").write_bytes(b"x" * 4000)
    segs = [{"start_s": 0, "end_s": 20, "product_prominent": True}]

    def _clip(cid, name):
        return Clip(
            clip_id=cid,
            path=str(tmp_path / name),
            source_url=f"https://www.tiktok.com/@a/video/{cid}",
            account="a",
            title="eye cream",
            duration=60.0,
            width=1080,
            height=1920,
            keyword_hit=True,
            visible_metrics={},
            sheet_path="",
            sheet_times=[],
            usable_segments=segs,
        )

    class FakeCollection:
        ok = True
        reason = "ok"
        clips = [_clip("111", "a.mp4"), _clip("222", "b.mp4")]
        candidates = []
        keywords = ["eye cream"]
        timings = {}
        product_definition = "眼霜"
        screened_out = []
        manifest_path = None

    def fake_runner(prompt, **kwargs):
        task_dir = inbox.task_dir("task_tiny")
        # Write under the planned production name so _collect_exports finds it,
        # but keep bytes under MIN_DELIVERY_BYTES so qualifying_delivery fails.
        exports = task_dir / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        video = exports / "MX-task_tiny-chatcut.mp4"
        video.write_bytes(b"tiny")
        (task_dir / "produce-result.json").write_text(
            json.dumps({
                "ok": True,
                "project_name": "墨西哥眼霜 TikTok 无人值守 task_tiny",
                "export_paths": [str(video)],
                "footage_used": [{"path": str(tmp_path / "a.mp4"), "clip_id": "111"}],
                "shots": [
                    {"path": str(tmp_path / "a.mp4"), "source_in_s": 0.5, "duration_s": 2.0,
                     "start_frame": 0, "duration_frames": 60},
                    {"path": str(tmp_path / "b.mp4"), "source_in_s": 0.5, "duration_s": 2.0,
                     "start_frame": 60, "duration_frames": 60},
                ],
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr="")

    class Gate:
        ok = True
        reason = "ready"
        details = {}

    monkeypatch.setattr(
        "workflow.unattended.producer.verify_codex_chatcut_mcp_ready",
        lambda **kwargs: Gate(),
    )

    # Bypass media verify (tiny file is not a real H.264) so we reach the delivery gate.
    class Pass:
        ok = True
        reason = "pass"
        details = {}

    monkeypatch.setattr(
        "workflow.unattended.verify_output.verify_production",
        lambda **kwargs: Pass(),
    )

    producer = Producer(
        inbox,
        probe=lambda: {"healthy": True},
        codex_runner=fake_runner,
        options=ProduceOptions(
            mode="production",
            project_root=tmp_path,
            chatcut_agent_log=None,
            collector=lambda *a, **k: FakeCollection(),
            reviewer=lambda *a, **k: ([], {}),
            review_retries=0,
        ),
        log=lambda _m: None,
    )
    result = producer.produce(
        task_id="task_tiny",
        workflow_job_id="job_tiny",
        request=valid_payload(task_id="task_tiny", product="眼霜", count=1),
    )
    assert result.status == "produce_failed", result.reason
    assert "假成功" in (result.reason or "") or "delivery 为空" in (result.reason or "")
    assert result.delivery_paths == []
    assert qualifying_delivery_videos(inbox.task_dir("task_tiny") / "delivery") == []
