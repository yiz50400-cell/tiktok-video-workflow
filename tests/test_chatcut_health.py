from __future__ import annotations

from pathlib import Path

import pytest

from workflow.chatcut.health import (
    CHATCUT_MAIN_BINARY,
    DEFAULT_QUIT_WAIT_SECONDS,
    HealthReport,
    MCPError,
    _target_arguments,
    default_chatcut_agent_log,
    expand_chatcut_path,
    find_mcp_launcher,
    is_main_chatcut_command,
    probe_chatcut,
    resolve_project_id,
    restart_chatcut,
)


def unhealthy(reason: str = "timeout") -> HealthReport:
    return HealthReport(
        status="unhealthy",
        latency_ms=10_000,
        reason=reason,
        would_restart=True,
        launcher="/tmp/chatcut-mcp",
        process_running=True,
    )


def test_probe_healthy_uses_read_only_tool(tmp_path: Path) -> None:
    launcher = tmp_path / "chatcut-mcp"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    calls: list[tuple[Path, str, float]] = []

    def rpc_probe(path: Path, tool: str, timeout: float) -> None:
        calls.append((path, tool, timeout))

    report = probe_chatcut(
        launcher=launcher,
        timeout=1,
        rpc_probe=rpc_probe,
        process_check=lambda: True,
    )

    assert report.status == "healthy"
    assert report.tool == "list_projects"
    assert report.would_restart is False
    assert calls[0][1] == "list_projects"


def test_probe_timeout_is_unhealthy_even_when_process_exists(tmp_path: Path) -> None:
    launcher = tmp_path / "chatcut-mcp"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    def rpc_probe(path: Path, tool: str, timeout: float) -> None:
        raise TimeoutError("hung")

    report = probe_chatcut(
        launcher=launcher,
        timeout=1,
        rpc_probe=rpc_probe,
        process_check=lambda: True,
    )

    assert report.status == "unhealthy"
    assert report.process_running is True
    assert "timed out" in report.reason
    assert report.would_restart is True


def test_dry_run_never_restarts() -> None:
    actions: list[str] = []

    result = restart_chatcut(
        unhealthy(),
        apply=False,
        system="Darwin",
        quit_action=lambda **kwargs: actions.append("quit"),
        open_action=lambda: actions.append("open"),
    )

    assert result.attempted is False
    assert result.restarted is False
    assert actions == []


def test_apply_calls_quit_then_open_and_waits_for_health() -> None:
    actions: list[str] = []
    reports = iter(
        [
            unhealthy("starting"),
            HealthReport(
                status="healthy",
                latency_ms=120,
                reason="ready",
                would_restart=False,
                tool="list_projects",
                launcher="/tmp/chatcut-mcp",
                process_running=True,
            ),
        ]
    )

    result = restart_chatcut(
        unhealthy(),
        apply=True,
        system="Darwin",
        timeout=0.1,
        restart_timeout=3,
        probe=lambda **kwargs: next(reports),
        process_check=lambda: False,
        quit_action=lambda **kwargs: actions.append("quit"),
        open_action=lambda: actions.append("open"),
    )

    assert actions == ["quit", "open"]
    assert result.attempted is True
    assert result.restarted is True
    assert result.health.healthy is True


def test_reopen_project_runs_only_after_mcp_is_healthy(tmp_path: Path) -> None:
    launcher = tmp_path / "chatcut-mcp"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    actions: list[str] = []
    healthy = HealthReport(
        status="healthy",
        latency_ms=10,
        reason="ready",
        would_restart=False,
        tool="list_projects",
        launcher=str(launcher),
        process_running=True,
    )

    result = restart_chatcut(
        unhealthy(),
        apply=True,
        system="Darwin",
        launcher=launcher,
        reopen_project="TEST-无人值守-20261017",
        timeout=0.1,
        restart_timeout=1,
        probe=lambda **kwargs: healthy,
        process_check=lambda: False,
        quit_action=lambda **kwargs: actions.append("quit"),
        open_action=lambda: actions.append("open"),
        reopen_action=lambda path, project, timeout: actions.append(f"target:{project}"),
    )

    assert result.reopened_project == "TEST-无人值守-20261017"
    assert actions == ["quit", "open", "target:TEST-无人值守-20261017"]


@pytest.mark.parametrize(
    "tool_name",
    ["submit_video", "delete_project", "export_project", "target_project"],
)
def test_probe_rejects_dangerous_or_mutating_tool_names(tool_name: str, tmp_path: Path) -> None:
    launcher = tmp_path / "chatcut-mcp"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe or unsupported"):
        probe_chatcut(
            launcher=launcher,
            tools=[tool_name],
            rpc_probe=lambda *args: None,
            process_check=lambda: True,
        )



def test_find_launcher_prefers_environment(tmp_path: Path) -> None:
    configured = tmp_path / "custom-chatcut-mcp"
    configured.write_text("#!/bin/sh\n", encoding="utf-8")
    default = tmp_path / "Library/Application Support/ChatCut/chatcut-mcp"
    default.parent.mkdir(parents=True)
    default.write_text("#!/bin/sh\n", encoding="utf-8")

    assert find_mcp_launcher(
        env={"CHATCUT_MCP": str(configured)},
        system="Darwin",
        home=tmp_path,
    ) == configured


def test_windows_restart_is_explicitly_unverified() -> None:
    with pytest.raises(NotImplementedError, match="unverified"):
        restart_chatcut(unhealthy(), apply=True, system="Windows")


def healthy_report() -> HealthReport:
    return HealthReport(status="healthy", latency_ms=5, reason="ok", would_restart=False,
                        tool="list_projects", launcher="/tmp/chatcut-mcp", process_running=True)


def test_healthy_apply_without_force_does_not_restart() -> None:
    actions: list[str] = []
    result = restart_chatcut(healthy_report(), apply=True, system="Darwin",
                             quit_action=lambda **kwargs: actions.append("quit"),
                             open_action=lambda: actions.append("open"))
    assert result.attempted is False and actions == []


def test_force_restarts_healthy_chatcut_only_with_apply() -> None:
    actions: list[str] = []
    dry = restart_chatcut(healthy_report(), apply=False, force=True, system="Darwin",
                          quit_action=lambda **kwargs: actions.append("quit"),
                          open_action=lambda: actions.append("open"))
    assert dry.attempted is False and actions == []
    result = restart_chatcut(healthy_report(), apply=True, force=True, system="Darwin",
                             timeout=0.1, restart_timeout=1,
                             probe=lambda **kwargs: healthy_report(), process_check=lambda: False,
                             quit_action=lambda **kwargs: actions.append("quit"),
                             open_action=lambda: actions.append("open"))
    assert actions == ["quit", "open"] and result.restarted is True


def test_cli_force_requires_apply() -> None:
    from scripts.chatcut_health import main
    with pytest.raises(SystemExit) as exc:
        main(["--force"])
    assert exc.value.code == 1


PROJECTS = [
    {"projectId": "58b6bcf4-c69f-4270-a000-433099207a9a", "name": "TEST-无人值守-20261017"},
    {"projectId": "8dc0c77a-0481-43d2-9ead-3027249986ba", "name": "原项目"},
]


def test_resolve_project_id_by_name_and_prefix() -> None:
    assert resolve_project_id(PROJECTS, "TEST-无人值守-20261017").startswith("58b6bcf4")
    assert resolve_project_id(PROJECTS, "8dc0c77a") == PROJECTS[1]["projectId"]
    with pytest.raises(MCPError):
        resolve_project_id(PROJECTS, "不存在")


def test_target_arguments_supports_chatcut_project_id_schema() -> None:
    schema = {"properties": {"editorBaseUrl": {}, "projectId": {}}, "required": ["projectId"]}
    assert _target_arguments(schema, "58b6bcf4") == {"projectId": "58b6bcf4"}



def test_is_main_chatcut_command_ignores_mcp_helpers() -> None:
    assert is_main_chatcut_command(CHATCUT_MAIN_BINARY) is True
    assert is_main_chatcut_command(f"  {CHATCUT_MAIN_BINARY}  ") is True
    helper = (
        f"{CHATCUT_MAIN_BINARY} "
        "/Applications/ChatCut.app/Contents/Resources/app.asar/out/main/mcp/server.js"
    )
    assert is_main_chatcut_command(helper) is False
    assert is_main_chatcut_command("/Applications/ChatCut.app/Contents/Frameworks/"
                                   "ChatCut Helper.app/Contents/MacOS/ChatCut Helper") is False
    assert is_main_chatcut_command("") is False


def test_restart_caps_quit_wait_near_eight_seconds() -> None:
    seen: dict[str, float] = {}

    def quit_action(*, process_check, timeout):  # noqa: ARG001
        seen["timeout"] = timeout

    result = restart_chatcut(
        unhealthy(),
        apply=True,
        force=True,
        system="Darwin",
        timeout=0.1,
        restart_timeout=90,
        probe=lambda **kwargs: healthy_report(),
        process_check=lambda: False,
        quit_action=quit_action,
        open_action=lambda: None,
    )
    assert result.restarted is True
    assert seen["timeout"] == DEFAULT_QUIT_WAIT_SECONDS
    assert 5.0 <= DEFAULT_QUIT_WAIT_SECONDS <= 8.0


def test_find_launcher_windows_localappdata(tmp_path: Path) -> None:
    local = tmp_path / "Local"
    launcher = local / "ChatCut" / "chatcut-mcp.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"MZ")
    found = find_mcp_launcher(
        env={"LOCALAPPDATA": str(local), "APPDATA": str(tmp_path / "Roaming")},
        system="Windows",
        home=tmp_path,
    )
    assert found == launcher


def test_expand_chatcut_path_supports_windows_and_unix_vars(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "Local"
    local.mkdir()
    unixish = expand_chatcut_path(
        "$HOME/Library/Application Support/ChatCut/chatcut-mcp",
        env={"HOME": str(home), "USERPROFILE": str(home)},
    )
    assert Path(unixish) == home / "Library/Application Support/ChatCut/chatcut-mcp"
    winish = expand_chatcut_path(
        "%LOCALAPPDATA%/ChatCut/chatcut-mcp.exe",
        env={"LOCALAPPDATA": str(local), "USERPROFILE": str(home)},
    )
    assert Path(winish) == local / "ChatCut" / "chatcut-mcp.exe"


def test_default_agent_log_windows_uses_localappdata(tmp_path: Path) -> None:
    local = tmp_path / "Local"
    path = default_chatcut_agent_log(
        env={"LOCALAPPDATA": str(local)},
        system="Windows",
        home=tmp_path,
    )
    assert path == local / "ChatCut" / "logs" / "agent.log"
