import subprocess
from workflow.unattended.notify import TaskNotifier as N


def _capture():
    calls = []
    return calls, lambda cmd, **kw: calls.append((cmd, kw.get("env") or {}))


def test_success_windows_passes_text_via_env_only():
    calls, runner = _capture()
    ok = N.notify({"status": "produced", "task_id": "t1", "delivery_paths": ["C:\\out\\a.mp4"]},
                  system="Windows", runner=runner, env={})
    assert ok and len(calls) == 1
    cmd, env = calls[0]
    assert cmd[0] == "powershell"
    assert "t1" not in " ".join(cmd)  # 文本不拼进脚本
    assert "t1" in env["WF_NOTIFY_BODY"] and "a.mp4" in env["WF_NOTIFY_BODY"]


def test_failure_macos_passes_text_as_argv():
    calls, runner = _capture()
    N.notify({"status": "produce_failed", "task_id": "t2", "reason": "素材不足"},
             system="Darwin", runner=runner, env={})
    cmd, _ = calls[0]
    assert cmd[0] == "osascript" and cmd[-1].endswith("素材不足") and cmd[-2] == "视频工作流：任务失败"


def test_injection_text_stays_data():
    calls, runner = _capture()
    evil = "'; Remove-Item C:\\ -Recurse; \" & rm -rf / $(x)"
    N.notify({"status": "failed", "task_id": "t", "reason": evil}, system="Windows", runner=runner, env={})
    cmd, env = calls[0]
    assert evil not in " ".join(cmd) and evil in env["WF_NOTIFY_BODY"]


def test_no_notify_for_idle_or_disabled():
    calls, runner = _capture()
    assert not N.notify({"status": "idle"}, system="Windows", runner=runner, env={})
    assert not N.notify({"status": "produced", "task_id": "x"}, system="Windows",
                        runner=runner, env={"WORKFLOW_NOTIFY": "0"})
    assert calls == []


def test_send_error_is_swallowed():
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 15)
    assert not N.notify({"status": "failed", "task_id": "t"}, system="Windows", runner=boom, env={})


def test_supervisor_calls_notifier_once(tmp_path, monkeypatch):
    from workflow.unattended.supervisor import Supervisor
    seen = []
    sup = Supervisor(data_dir=tmp_path, probe=lambda: {"status": "healthy", "healthy": True},
                     notifier=seen.append)
    monkeypatch.setattr(sup.inbox, "claim_next", lambda: tmp_path / "x.json")
    monkeypatch.setattr(sup, "_process_claimed", lambda c, l: {"status": "produced", "task_id": "t"})
    assert sup.run_once()["status"] == "produced"
    assert seen == [{"status": "produced", "task_id": "t"}]


def test_supervisor_notifier_failure_does_not_break(tmp_path, monkeypatch):
    from workflow.unattended.supervisor import Supervisor
    def bad(_):
        raise RuntimeError("no toast")
    sup = Supervisor(data_dir=tmp_path, probe=lambda: {"status": "healthy", "healthy": True}, notifier=bad)
    monkeypatch.setattr(sup.inbox, "claim_next", lambda: tmp_path / "x.json")
    monkeypatch.setattr(sup, "_process_claimed", lambda c, l: {"status": "failed", "task_id": "t"})
    assert sup.run_once()["status"] == "failed"


def test_supervisor_notifies_when_processing_raises(tmp_path, monkeypatch):
    import pytest
    from workflow.unattended.supervisor import Supervisor
    seen = []
    sup = Supervisor(data_dir=tmp_path, probe=lambda: {"status": "healthy", "healthy": True},
                     notifier=seen.append)
    monkeypatch.setattr(sup.inbox, "claim_next", lambda: tmp_path / "001-task_x.json")
    def crash(c, l):
        raise RuntimeError("ChatCut 断开")
    monkeypatch.setattr(sup, "_process_claimed", crash)
    with pytest.raises(RuntimeError):
        sup.run_once()
    assert len(seen) == 1 and seen[0]["status"] == "failed"
    assert seen[0]["task_id"] == "001-task_x" and "ChatCut 断开" in seen[0]["reason"]
