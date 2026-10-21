"""Health probing and conservative recovery for ChatCut Desktop.

A running ChatCut process is not sufficient evidence of health. The authoritative
signal is a lightweight, read-only MCP call completing within the configured
timeout.
"""

from __future__ import annotations

import json
import os
import platform
import select
import threading
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RESTART_TIMEOUT_SECONDS = 60.0
DEFAULT_QUIT_WAIT_SECONDS = 8.0
DEFAULT_TERM_WAIT_SECONDS = 5.0
CHATCUT_MAIN_BINARY = "/Applications/ChatCut.app/Contents/MacOS/ChatCut"
DEFAULT_PROBE_TOOLS = ("list_projects",)
DANGEROUS_TOOL_PREFIXES = ("submit_", "delete", "remove", "export")


class MCPError(RuntimeError):
    """Raised when the local ChatCut MCP launcher or protocol fails."""


@dataclass(frozen=True)
class HealthReport:
    status: str
    latency_ms: int | None
    reason: str
    would_restart: bool
    tool: str | None = None
    launcher: str | None = None
    process_running: bool | None = None

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RestartResult:
    attempted: bool
    restarted: bool
    reason: str
    health: HealthReport
    reopened_project: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["health"] = self.health.to_dict()
        return data


def expand_chatcut_path(value: str, *, env: Mapping[str, str] | None = None) -> str:
    """Expand ~, $HOME/${HOME}, and %VAR% so Mac/Windows Codex configs both work."""
    env_map = {**os.environ, **(dict(env) if env is not None else {})}
    home = str(Path(env_map.get("USERPROFILE") or env_map.get("HOME") or Path.home()).expanduser())
    expanded = value.replace("${HOME}", home).replace("$HOME", home)
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "ProgramFiles"):
        raw = env_map.get(key)
        if not raw:
            continue
        expanded = expanded.replace(f"%{key}%", str(raw))
        expanded = expanded.replace(f"%{key.lower()}%", str(raw))
        expanded = expanded.replace(f"${{{key}}}", str(raw))
        expanded = expanded.replace(f"${key}", str(raw))
    return str(Path(os.path.expanduser(expanded)))


def chatcut_data_dir(
    *,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if env is None else env
    system = platform.system() if system is None else system
    home = Path.home() if home is None else home
    if system == "Windows":
        local = Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        return local / "ChatCut"
    if system == "Darwin":
        return home / "Library" / "Application Support" / "ChatCut"
    return home / ".local" / "share" / "ChatCut"


def default_chatcut_agent_log(
    *,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> Path:
    return chatcut_data_dir(env=env, system=system, home=home) / "logs" / "agent.log"


def find_mcp_launcher(
    *,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> Path | None:
    env = os.environ if env is None else env
    configured = env.get("CHATCUT_MCP")
    if configured:
        candidate = Path(expand_chatcut_path(configured, env=env))
        return candidate if candidate.is_file() else None

    system = platform.system() if system is None else system
    home = Path.home() if home is None else home
    candidates: list[Path] = []
    if system == "Darwin":
        candidates.append(home / "Library/Application Support/ChatCut/chatcut-mcp")
    elif system == "Windows":
        local = Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        roaming = Path(env.get("APPDATA") or (home / "AppData" / "Roaming"))
        program_files = Path(env.get("ProgramFiles") or r"C:\Program Files")
        program_files_x86 = Path(env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
        for root in (local / "ChatCut", local / "Programs" / "ChatCut", roaming / "ChatCut",
                     program_files / "ChatCut", program_files_x86 / "ChatCut"):
            for name in ("chatcut-mcp.exe", "chatcut-mcp.cmd", "chatcut-mcp.bat", "chatcut-mcp"):
                candidates.append(root / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def is_safe_probe_tool(tool_name: str) -> bool:
    normalized = tool_name.strip().lower()
    if normalized not in DEFAULT_PROBE_TOOLS:
        return False
    return not any(normalized.startswith(prefix) for prefix in DANGEROUS_TOOL_PREFIXES)


def _launcher_command(launcher: Path) -> list[str]:
    path = str(launcher)
    suffix = launcher.suffix.lower()
    if os.name == "nt":
        if suffix in {".cmd", ".bat"}:
            return ["cmd.exe", "/c", path]
        return [path]
    if os.access(launcher, os.X_OK):
        return [path]
    return ["/bin/sh", path]


def is_main_chatcut_command(command: str) -> bool:
    """True for the Desktop main binary only (no MCP helper argv)."""
    return command.strip() == CHATCUT_MAIN_BINARY


def _main_chatcut_pids() -> list[int]:
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, command = parts
        if not is_main_chatcut_command(command):
            continue
        try:
            pids.append(int(pid_s))
        except ValueError:
            continue
    return pids


def _process_running() -> bool:
    """True only when the ChatCut Desktop main app process is alive.

    MCP stdio helpers reuse the same binary with a server.js argument and must
    not delay quit detection or keep process_running True after the UI exits.
    """
    return bool(_main_chatcut_pids())


def _encode_message(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _read_message(stream: Any, deadline: float) -> dict[str, Any]:
    # Windows cannot select() on pipes; read a full newline-delimited JSON line
    # with a worker thread and join timeout instead.
    if os.name == "nt":
        wait = deadline - time.monotonic()
        if wait <= 0:
            raise TimeoutError("MCP response timed out")
        box: list[Any] = []

        def _worker() -> None:
            try:
                box.append(stream.readline())
            except Exception as exc:  # noqa: BLE001
                box.append(exc)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(wait)
        if thread.is_alive():
            raise TimeoutError("MCP response timed out")
        if not box:
            raise TimeoutError("MCP response timed out")
        item = box[0]
        if isinstance(item, BaseException):
            raise MCPError(f"MCP read failed: {item}") from item
        if not item:
            raise MCPError("MCP launcher closed stdout")
        while True:
            line = item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item)
            line = line.strip()
            if line:
                if len(line) > 8 * 1024 * 1024:
                    raise MCPError("MCP response exceeded 8 MiB")
                try:
                    return json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MCPError(f"invalid MCP JSON: {exc}") from exc
            wait = deadline - time.monotonic()
            if wait <= 0:
                raise TimeoutError("MCP response timed out")
            box.clear()
            thread = threading.Thread(target=_worker, daemon=True)
            thread.start()
            thread.join(wait)
            if thread.is_alive():
                raise TimeoutError("MCP response timed out")
            if not box:
                raise TimeoutError("MCP response timed out")
            item = box[0]
            if isinstance(item, BaseException):
                raise MCPError(f"MCP read failed: {item}") from item
            if not item:
                raise MCPError("MCP launcher closed stdout")

    data = bytearray()
    while True:
        wait = deadline - time.monotonic()
        if wait <= 0:
            raise TimeoutError("MCP response timed out")
        ready, _, _ = select.select([stream], [], [], wait)
        if not ready:
            raise TimeoutError("MCP response timed out")
        chunk = os.read(stream.fileno(), 1)
        if not chunk:
            raise MCPError("MCP launcher closed stdout")
        if chunk == b"\n":
            if not data:
                continue
            try:
                return json.loads(data)
            except json.JSONDecodeError as exc:
                raise MCPError(f"invalid MCP JSON: {exc}") from exc
        data.extend(chunk)
        if len(data) > 8 * 1024 * 1024:
            raise MCPError("MCP response exceeded 8 MiB")


def _await_response(stream: Any, request_id: int, deadline: float) -> dict[str, Any]:
    while True:
        message = _read_message(stream, deadline)
        if message.get("id") == request_id:
            return message


class _MCPSession:
    def __init__(self, launcher: Path, timeout: float) -> None:
        self.launcher = launcher
        self.deadline = time.monotonic() + timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1

    def __enter__(self) -> "_MCPSession":
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        self.process = subprocess.Popen(
            _launcher_command(self.launcher),
            **popen_kwargs,
        )
        try:
            response = self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "chatcut-health", "version": "0.1"},
                },
            )
            if "error" in response:
                raise MCPError(f"MCP initialize failed: {response['error']}")
            self.notify("notifications/initialized", {})
            return self
        except BaseException:
            self._terminate()
            raise

    def _streams(self) -> tuple[Any, Any]:
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise MCPError("MCP launcher stdio unavailable")
        return self.process.stdin, self.process.stdout

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        stdin, stdout = self._streams()
        request_id = self._next_id
        self._next_id += 1
        stdin.write(
            _encode_message(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
        )
        stdin.flush()
        return _await_response(stdout, request_id, self.deadline)

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        stdin, _ = self._streams()
        stdin.write(_encode_message({"jsonrpc": "2.0", "method": method, "params": params}))
        stdin.flush()

    def _terminate(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        if os.name == "nt":
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=1.0)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._terminate()


def _jsonrpc_tool_call(
    launcher: Path,
    tool_name: str,
    arguments: Mapping[str, Any],
    timeout: float,
) -> None:
    with _MCPSession(launcher, timeout) as session:
        response = session.request(
            "tools/call",
            {"name": tool_name, "arguments": dict(arguments)},
        )
        if "error" in response:
            raise MCPError(f"MCP tools/call failed: {response['error']}")
        result = response.get("result")
        if isinstance(result, dict) and result.get("isError"):
            raise MCPError("MCP tool returned isError")


def _jsonrpc_probe(launcher: Path, tool_name: str, timeout: float) -> None:
    if not is_safe_probe_tool(tool_name):
        raise ValueError(f"unsafe or unsupported probe tool: {tool_name}")
    _jsonrpc_tool_call(launcher, tool_name, {}, timeout)


def probe_chatcut(
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    tools: Sequence[str] = DEFAULT_PROBE_TOOLS,
    launcher: Path | None = None,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
    rpc_probe: Callable[[Path, str, float], None] | None = None,
    process_check: Callable[[], bool] | None = None,
) -> HealthReport:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if not tools:
        raise ValueError("at least one probe tool is required")
    for tool_name in tools:
        if not is_safe_probe_tool(tool_name):
            raise ValueError(f"unsafe or unsupported probe tool: {tool_name}")

    launcher = launcher or find_mcp_launcher(env=env, system=system, home=home)
    process_check = _process_running if process_check is None else process_check
    running = process_check()
    if launcher is None:
        return HealthReport(
            status="unhealthy",
            latency_ms=None,
            reason="ChatCut MCP launcher not found",
            would_restart=True,
            launcher=None,
            process_running=running,
        )

    rpc_probe = _jsonrpc_probe if rpc_probe is None else rpc_probe
    started = time.monotonic()
    errors: list[str] = []
    for tool_name in tools:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            errors.append("probe timeout exhausted")
            break
        try:
            rpc_probe(launcher, tool_name, remaining)
            latency_ms = round((time.monotonic() - started) * 1000)
            return HealthReport(
                status="healthy",
                latency_ms=latency_ms,
                reason=f"MCP {tool_name} returned within timeout",
                would_restart=False,
                tool=tool_name,
                launcher=str(launcher),
                process_running=running,
            )
        except TimeoutError:
            errors.append(f"{tool_name}: timed out")
            break
        except (MCPError, OSError) as exc:
            errors.append(f"{tool_name}: {exc}")

    latency_ms = round((time.monotonic() - started) * 1000)
    return HealthReport(
        status="unhealthy",
        latency_ms=latency_ms,
        reason="; ".join(errors) or "MCP probe failed",
        would_restart=True,
        launcher=str(launcher),
        process_running=running,
    )


def _run_quiet(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def _wait_for_exit(process_check: Callable[[], bool], deadline: float) -> bool:
    while time.monotonic() < deadline:
        if not process_check():
            return True
        time.sleep(0.25)
    return not process_check()


def _quit_chatcut_macos(*, process_check: Callable[[], bool], timeout: float) -> None:
    # Graceful quit first; wait only for the main app (see _process_running).
    _run_quiet(
        ["osascript", "-e", 'tell application "ChatCut" to quit'],
        timeout=min(timeout, 10.0),
    )
    deadline = time.monotonic() + timeout
    if _wait_for_exit(process_check, deadline):
        return

    # Fallback: SIGTERM only the main Desktop binary PIDs, not MCP helpers by name.
    for pid in _main_chatcut_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if not _wait_for_exit(process_check, time.monotonic() + DEFAULT_TERM_WAIT_SECONDS):
        raise MCPError("ChatCut did not exit after AppleScript quit and SIGTERM")


def _open_chatcut_macos() -> None:
    result = _run_quiet(["open", "-a", "ChatCut"], timeout=10.0)
    if result.returncode != 0:
        raise MCPError("open -a ChatCut failed")


def resolve_project_id(projects: Sequence[Mapping[str, Any]], project: str) -> str:
    """Map a project name, full id or unique id prefix from list_projects to a projectId."""
    by_name = [p for p in projects if p.get("name") == project]
    if len(by_name) == 1:
        return str(by_name[0]["projectId"])
    if len(by_name) > 1:
        raise MCPError(f"project name is ambiguous: {project}")
    by_id = [p for p in projects if str(p.get("projectId", "")).startswith(project)]
    if len(by_id) == 1:
        return str(by_id[0]["projectId"])
    raise MCPError(f"project not found or ambiguous: {project}")


def _target_arguments(input_schema: Mapping[str, Any], project: str) -> dict[str, Any]:
    properties = input_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    for key in ("projectId", "project", "project_id", "project_name", "id", "name"):
        if key in properties:
            return {key: project}
    raise MCPError("target_project schema has no recognized project name/id field")


def _reopen_project(launcher: Path, project: str, timeout: float) -> None:
    with _MCPSession(launcher, timeout) as session:
        listed = session.request("tools/list", {})
        if "error" in listed:
            raise MCPError(f"MCP tools/list failed: {listed['error']}")
        tools = listed.get("result", {}).get("tools", [])
        target = next(
            (item for item in tools if isinstance(item, Mapping) and item.get("name") == "target_project"),
            None,
        )
        if target is None:
            raise MCPError("target_project is not available after restart")
        schema = target.get("inputSchema", {})
        if "projectId" in (schema.get("properties") or {}):
            # ChatCut 0.6.13: target_project requires projectId; resolve names via list_projects.
            listed_projects = session.request("tools/call", {"name": "list_projects", "arguments": {}})
            if "error" in listed_projects:
                raise MCPError(f"list_projects failed: {listed_projects['error']}")
            structured = (listed_projects.get("result") or {}).get("structuredContent") or {}
            project = resolve_project_id(structured.get("projects") or [], project)
        arguments = _target_arguments(schema, project)
        response = session.request(
            "tools/call",
            {"name": "target_project", "arguments": arguments},
        )
        if "error" in response:
            raise MCPError(f"target_project failed: {response['error']}")
        result = response.get("result")
        if isinstance(result, Mapping) and result.get("isError"):
            raise MCPError("target_project returned isError")


def restart_chatcut(
    current: HealthReport,
    *,
    apply: bool,
    force: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    restart_timeout: float = DEFAULT_RESTART_TIMEOUT_SECONDS,
    reopen_project: str | None = None,
    system: str | None = None,
    launcher: Path | None = None,
    probe: Callable[..., HealthReport] = probe_chatcut,
    process_check: Callable[[], bool] | None = None,
    quit_action: Callable[..., None] | None = None,
    open_action: Callable[[], None] | None = None,
    reopen_action: Callable[[Path, str, float], None] | None = None,
) -> RestartResult:
    if current.healthy and not force:
        return RestartResult(False, False, "already healthy", current)
    if not apply:
        return RestartResult(False, False, "dry-run; restart not applied", current)

    system = platform.system() if system is None else system
    if system == "Windows":
        raise NotImplementedError(
            "Windows ChatCut auto-restart is unverified; keep ChatCut Desktop open, "
            "or restart it manually, then re-run the MCP health probe / canary"
        )
    if system != "Darwin":
        raise NotImplementedError(f"ChatCut restart is only implemented for macOS, got {system}")

    process_check = _process_running if process_check is None else process_check
    quit_action = _quit_chatcut_macos if quit_action is None else quit_action
    open_action = _open_chatcut_macos if open_action is None else open_action
    reopen_action = _reopen_project if reopen_action is None else reopen_action

    quit_action(process_check=process_check, timeout=min(restart_timeout, DEFAULT_QUIT_WAIT_SECONDS))
    open_action()

    deadline = time.monotonic() + restart_timeout
    latest = current
    while time.monotonic() < deadline:
        remaining = max(0.1, min(timeout, deadline - time.monotonic()))
        latest = probe(timeout=remaining, launcher=launcher)
        if latest.healthy:
            reopened = None
            if reopen_project:
                actual_launcher = launcher or (
                    Path(latest.launcher) if latest.launcher else find_mcp_launcher()
                )
                if actual_launcher is None:
                    raise MCPError("cannot reopen project without MCP launcher")
                reopen_action(actual_launcher, reopen_project, remaining)
                reopened = reopen_project
            return RestartResult(True, True, "ChatCut restarted and MCP is ready", latest, reopened)
        time.sleep(1.0)

    return RestartResult(True, False, "ChatCut restarted but MCP did not become ready", latest)
