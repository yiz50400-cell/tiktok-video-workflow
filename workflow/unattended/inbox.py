
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import canonical_json_bytes, validate_request

BUCKETS = ("incoming", "processing", "done", "failed", "conflict")


def default_data_dir() -> Path:
    configured = os.environ.get("WORKFLOW_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / ".runtime"


@dataclass(frozen=True)
class InboxPaths:
    root: Path
    incoming: Path
    processing: Path
    done: Path
    failed: Path
    conflict: Path
    tasks: Path
    locks: Path
    lock: Path


class Inbox:
    def __init__(self, data_dir: str | os.PathLike[str] | None = None):
        base = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir()
        unattended = base / "unattended"
        inbox = unattended / "inbox"
        self.paths = InboxPaths(
            root=unattended,
            incoming=inbox / "incoming",
            processing=inbox / "processing",
            done=inbox / "done",
            failed=inbox / "failed",
            conflict=inbox / "conflict",
            tasks=unattended / "tasks",
            locks=unattended / "locks",
            lock=unattended / "locks" / "supervisor.lock",
        )
        self.ensure_directories()

    def ensure_directories(self) -> None:
        for path in (
            self.paths.incoming,
            self.paths.processing,
            self.paths.done,
            self.paths.failed,
            self.paths.conflict,
            self.paths.tasks,
            self.paths.locks,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def enqueue(self, data: dict[str, Any], *, check_media_exists: bool = True) -> Path:
        request = validate_request(data, check_media_exists=check_media_exists)
        task_id = request["task_id"]
        stamp = time.time_ns()
        destination = self.paths.incoming / f"{stamp:020d}-{task_id}.json"
        partial = destination.with_suffix(destination.suffix + ".partial")
        self._atomic_write_bytes(partial, canonical_json_bytes(request), replace_final=False)
        os.replace(partial, destination)
        return destination

    def incoming_files(self) -> list[Path]:
        files = [path for path in self.paths.incoming.iterdir() if path.is_file() and path.suffix == ".json"]
        return sorted(files, key=lambda path: (path.stat().st_mtime_ns, path.name))

    def claim_next(self) -> Path | None:
        for source in self.incoming_files():
            destination = self._available_destination(self.paths.processing / source.name)
            try:
                os.replace(source, destination)
                return destination
            except FileNotFoundError:
                continue
        return None

    def move(self, source: Path, bucket: str) -> Path:
        if bucket not in BUCKETS or bucket in {"incoming", "processing"}:
            raise ValueError(f"不支持的目标桶：{bucket}")
        destination_dir = getattr(self.paths, bucket)
        destination = self._available_destination(destination_dir / source.name)
        os.replace(source, destination)
        return destination

    def task_dir(self, task_id: str) -> Path:
        path = self.paths.tasks / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_task_request(self, task_id: str, request: dict[str, Any]) -> Path:
        destination = self.task_dir(task_id) / "request.json"
        self.atomic_write_json(destination, request)
        return destination

    def write_task_state(self, task_id: str, state: dict[str, Any]) -> Path:
        destination = self.task_dir(task_id) / "state.json"
        self.atomic_write_json(destination, state)
        return destination

    def read_task_state(self, task_id: str) -> dict[str, Any] | None:
        path = self.paths.tasks / task_id / "state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_conflict(self, task_id: str, sha256: str, request: dict[str, Any], state: dict[str, Any]) -> Path:
        directory = self.task_dir(task_id) / "conflicts" / sha256
        directory.mkdir(parents=True, exist_ok=True)
        self.atomic_write_json(directory / "request.json", request)
        self.atomic_write_json(directory / "state.json", state)
        return directory

    def list_summary(self) -> dict[str, Any]:
        buckets = {}
        for name in BUCKETS:
            path = getattr(self.paths, name)
            buckets[name] = sorted(item.name for item in path.iterdir() if item.is_file() and item.suffix == ".json")
        states = []
        for state_path in sorted(self.paths.tasks.glob("*/state.json")):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            states.append({
                "task_id": state.get("task_id", state_path.parent.name),
                "status": state.get("status"),
                "reason": state.get("reason"),
                "workflow_job_id": state.get("workflow_job_id"),
                "attempt": state.get("attempt"),
                "updated_at": state.get("updated_at"),
            })
        return {"buckets": buckets, "states": states}

    def atomic_write_json(self, destination: Path, data: dict[str, Any]) -> None:
        partial = destination.with_name(destination.name + ".partial")
        self._atomic_write_bytes(partial, canonical_json_bytes(data), replace_final=False)
        os.replace(partial, destination)

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes, *, replace_final: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        if replace_final:
            raise RuntimeError("replace_final 参数仅保留兼容，不应在此处使用")

    @staticmethod
    def _available_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        index = 1
        while True:
            candidate = destination.with_name(f"{stem}.{index}{suffix}")
            if not candidate.exists():
                return candidate
            index += 1
