"""Desktop notification when an unattended task finishes (one per task).

Only OS built-ins: PowerShell WinRT toast on Windows, osascript on macOS.
Title/body never get spliced into a script: they are passed as environment
variables (Windows) or argv (macOS), so task text cannot inject commands.
Failures never affect the task. Set WORKFLOW_NOTIFY=0 to disable.
"""
from __future__ import annotations

import os
import platform
import subprocess
from typing import Any, Callable, Mapping

_WINDOWS_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
    "$t = [Security.SecurityElement]::Escape($env:WF_NOTIFY_TITLE);"
    "$b = [Security.SecurityElement]::Escape($env:WF_NOTIFY_BODY);"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>' + $t + '</text><text>' + $b + '</text></binding></visual></toast>');"
    "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
    "[Windows.UI.Notifications.ToastNotification]::new($x))"
)

_MACOS_SCRIPT = [
    "-e", "on run argv",
    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
    "-e", "end run",
]


class TaskNotifier:
    """Single entry point for task-finished notifications."""

    SUCCESS_STATUSES = frozenset({"produced"})
    FAILURE_STATUSES = frozenset({"produce_failed", "failed", "conflict"})

    @staticmethod
    def build_message(result: Mapping[str, Any]) -> tuple[str, str] | None:
        status = result.get("status")
        task_id = result.get("task_id") or "-"
        if status in TaskNotifier.SUCCESS_STATUSES:
            paths = result.get("delivery_paths") or []
            where = str(paths[0]) if paths else "delivery 文件夹"
            if len(paths) > 1:
                where += f" 等 {len(paths)} 个文件"
            return "视频工作流：成片已完成", f"任务 {task_id} 已完成，成片：{where}"
        if status in TaskNotifier.FAILURE_STATUSES:
            reason = str(result.get("reason") or "未知原因")[:180]
            return "视频工作流：任务失败", f"任务 {task_id} 失败：{reason}"
        return None

    @staticmethod
    def windows_command(title: str, body: str) -> tuple[list[str], dict[str, str]]:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT]
        return cmd, {"WF_NOTIFY_TITLE": title, "WF_NOTIFY_BODY": body}

    @staticmethod
    def macos_command(title: str, body: str) -> tuple[list[str], dict[str, str]]:
        return ["osascript", *_MACOS_SCRIPT, title, body], {}

    @staticmethod
    def notify(
        result: Mapping[str, Any],
        *,
        system: str | None = None,
        runner: Callable[..., Any] = subprocess.run,
        env: Mapping[str, str] | None = None,
    ) -> bool:
        base_env = dict(os.environ if env is None else env)
        if str(base_env.get("WORKFLOW_NOTIFY", "1")).strip().lower() in {"0", "false", "off", "no"}:
            return False
        message = TaskNotifier.build_message(result)
        if message is None:
            return False
        system = platform.system() if system is None else system
        if system == "Windows":
            command, extra = TaskNotifier.windows_command(*message)
        elif system == "Darwin":
            command, extra = TaskNotifier.macos_command(*message)
        else:
            return False
        try:
            runner(command, env={**base_env, **extra}, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=15, check=False)
        except Exception:
            return False
        return True


notify_task_result = TaskNotifier.notify
