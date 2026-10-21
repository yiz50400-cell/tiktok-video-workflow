"""Fail-fast checks so Codex sessions actually see chatcut_desktop MCP tools.

ChatCut Desktop health (direct MCP probe) is NOT enough: Codex spawns chatcut-mcp
with env from config.toml. An invalid CHATCUT_MCP_CLIENT makes the launcher exit
immediately ("chatcut-mcp fatal: CHATCUT_MCP_CLIENT is invalid"), so Codex registers
zero tools and the model falls back to Computer Use / in-app AI.

Additionally, gpt-5.6-sol defers MCP tools while tool_search is removed; without
code_mode_host, deferred tools never reach the model (openai/codex#33609).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# Observed allowlist from ChatCut 0.6.13 launcher (invalid: codex_unattended, codex_cli).
VALID_CHATCUT_MCP_CLIENTS = frozenset(
    {
        "codex_app",
        "codex",
        "cursor",
        "claude",
        "claude_code",
        "hermes_agent",
        "unknown",
        "",
    }
)

REQUIRED_TOOL_NAMES = ("list_projects", "create_project", "push_asset", "export")


@dataclass(frozen=True)
class CodexMcpGateResult:
    ok: bool
    reason: str
    client: str | None = None
    code_mode_host: bool | None = None
    tool_count: int | None = None
    config_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "client": self.client,
            "code_mode_host": self.code_mode_host,
            "tool_count": self.tool_count,
            "config_path": self.config_path,
        }


def resolve_unattended_config_path(
    *,
    codex_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    env = os.environ if env is None else env
    home = Path(codex_home or env.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    profile = home / "unattended-chatcut.config.toml"
    if profile.is_file():
        return profile
    base = home / "config.toml"
    return base if base.is_file() else None


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML; prefer stdlib tomllib (3.11+), else tomli, else tiny fallback."""
    try:
        import tomllib as _toml  # type: ignore
    except ModuleNotFoundError:
        try:
            import tomli as _toml  # type: ignore
        except ModuleNotFoundError:
            return _load_toml_fallback(path)
    with path.open("rb") as fh:
        return _toml.load(fh)


def _load_toml_fallback(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    features: dict[str, Any] = {}
    for key in ("code_mode_host", "computer_use", "browser_use", "apps"):
        m = re.search(rf"^\s*{key}\s*=\s*(true|false)\s*$", raw, re.M | re.I)
        if m:
            features[key] = m.group(1).lower() == "true"
    client = ""
    m = re.search(r'^\s*CHATCUT_MCP_CLIENT\s*=\s*"([^"]*)"', raw, re.M)
    if m:
        client = m.group(1)
    cmd = None
    section = re.search(r"\[mcp_servers\.chatcut_desktop\](.*?)(?=\n\[|\Z)", raw, re.S)
    if section:
        cm = re.search(r'^\s*command\s*=\s*"([^"]+)"', section.group(1), re.M)
        if cm:
            cmd = cm.group(1)
    return {
        "features": features,
        "mcp_servers": {
            "chatcut_desktop": {
                "command": cmd,
                "env": {"CHATCUT_MCP_CLIENT": client},
            }
        },
    }


def inspect_chatcut_mcp_config(
    config: Mapping[str, Any],
) -> tuple[str, bool | None, dict[str, str]]:
    features = config.get("features") or {}
    code_mode_host = features.get("code_mode_host")
    cmh: bool | None = code_mode_host if isinstance(code_mode_host, bool) else None
    servers = config.get("mcp_servers") or {}
    chatcut = servers.get("chatcut_desktop") or {}
    env_map = {str(k): str(v) for k, v in dict(chatcut.get("env") or {}).items()}
    client = str(env_map.get("CHATCUT_MCP_CLIENT", ""))
    return client, cmh, env_map


def verify_codex_chatcut_mcp_ready(
    *,
    codex_home: Path | None = None,
    env: Mapping[str, str] | None = None,
    require_code_mode_host: bool = True,
    probe_tools: bool = True,
    timeout: float = 15.0,
) -> CodexMcpGateResult:
    """Validate Codex config + spawn chatcut-mcp with that env before produce."""
    path = resolve_unattended_config_path(codex_home=codex_home, env=env)
    if path is None:
        return CodexMcpGateResult(False, "未找到 unattended-chatcut.config.toml 或 config.toml")

    try:
        config = _load_toml(path)
    except Exception as exc:  # noqa: BLE001
        return CodexMcpGateResult(False, f"无法解析 Codex 配置：{exc}", config_path=str(path))

    client, code_mode_host, mcp_env = inspect_chatcut_mcp_config(config)
    servers = (config.get("mcp_servers") or {}).get("chatcut_desktop") or {}
    if not servers:
        return CodexMcpGateResult(
            False,
            "配置中缺少 [mcp_servers.chatcut_desktop]",
            client=client,
            code_mode_host=code_mode_host,
            config_path=str(path),
        )

    if client not in VALID_CHATCUT_MCP_CLIENTS:
        allowed = ", ".join(sorted(c for c in VALID_CHATCUT_MCP_CLIENTS if c))
        return CodexMcpGateResult(
            False,
            (
                f'CHATCUT_MCP_CLIENT="{client}" 不被 ChatCut 接受（会 fatal 退出）。'
                f"请改为合法值，例如：{allowed}"
            ),
            client=client,
            code_mode_host=code_mode_host,
            config_path=str(path),
        )

    if require_code_mode_host and code_mode_host is False:
        return CodexMcpGateResult(
            False,
            "features.code_mode_host=false：gpt-5.6-sol 下 deferred MCP 无法进入模型工具面，请改为 true",
            client=client,
            code_mode_host=code_mode_host,
            config_path=str(path),
        )

    if not probe_tools:
        return CodexMcpGateResult(
            True,
            "配置校验通过（未做 tools/list 探针）",
            client=client,
            code_mode_host=code_mode_host,
            config_path=str(path),
        )

    from workflow.chatcut.health import MCPError, find_mcp_launcher
    from workflow.chatcut import health as health_mod

    from workflow.chatcut.health import expand_chatcut_path

    command = servers.get("command")
    if isinstance(command, str) and command.strip():
        expanded = expand_chatcut_path(command, env=env)
        launcher: Path | None = Path(expanded)
        if not launcher.is_file():
            launcher = find_mcp_launcher(env=env)
    else:
        launcher = find_mcp_launcher(env=env)

    if launcher is None or not Path(launcher).is_file():
        return CodexMcpGateResult(
            False,
            f"找不到 chatcut-mcp：{command!r}",
            client=client,
            code_mode_host=code_mode_host,
            config_path=str(path),
        )

    class _EnvMCPSession(health_mod._MCPSession):
        def __enter__(self):
            merged = os.environ.copy()
            merged.update(mcp_env)
            merged.setdefault("CHATCUT_MCP_CLIENT", client)
            popen_kwargs = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": merged,
            }
            if os.name != "nt":
                popen_kwargs["start_new_session"] = True
            self.process = subprocess.Popen(
                health_mod._launcher_command(self.launcher),
                **popen_kwargs,
            )
            try:
                response = self.request(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "codex-mcp-gate", "version": "0.1"},
                    },
                )
                if "error" in response:
                    raise MCPError(f"MCP initialize failed: {response['error']}")
                self.notify("notifications/initialized", {})
                return self
            except BaseException:
                err = b""
                if self.process and self.process.stderr:
                    try:
                        # Non-blocking-ish: wait briefly for fatal line
                        time.sleep(0.2)
                        err = self.process.stderr.read() or b""
                    except Exception:
                        pass
                self._terminate()
                msg = err.decode("utf-8", errors="replace").strip()
                if msg:
                    raise MCPError(msg) from None
                raise

    try:
        with _EnvMCPSession(Path(launcher), timeout) as session:
            listed = session.request("tools/list", {})
            if "error" in listed:
                raise MCPError(f"tools/list failed: {listed['error']}")
            tools = (listed.get("result") or {}).get("tools") or []
            names = {t.get("name") for t in tools if isinstance(t, dict)}
            missing = [n for n in REQUIRED_TOOL_NAMES if n not in names]
            if missing:
                return CodexMcpGateResult(
                    False,
                    f"chatcut-mcp tools/list 缺少必要工具：{missing}（共 {len(names)} 个）",
                    client=client,
                    code_mode_host=code_mode_host,
                    tool_count=len(names),
                    config_path=str(path),
                )
            return CodexMcpGateResult(
                True,
                f"Codex MCP 门禁通过：client={client!r}, code_mode_host={code_mode_host}, tools={len(names)}",
                client=client,
                code_mode_host=code_mode_host,
                tool_count=len(names),
                config_path=str(path),
            )
    except Exception as exc:  # noqa: BLE001
        return CodexMcpGateResult(
            False,
            f"用配置中的 CHATCUT_MCP_CLIENT 拉起 chatcut-mcp 失败：{exc}",
            client=client,
            code_mode_host=code_mode_host,
            config_path=str(path),
        )
