
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


def _now_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _pid_exists(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LeaseFileLock:
    def __init__(self, path: Path, *, lease_seconds: float = 90, heartbeat_seconds: float = 10):
        self.path = Path(path)
        self.lease_seconds = float(lease_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds)
        self._handle = None
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task_id: str | None = None
        self._acquired_at: float | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        if not self._os_lock(handle):
            handle.close()
            return False
        metadata = self._read_metadata(handle)
        now = time.time()
        if metadata and not metadata.get("released"):
            lease_until = float(metadata.get("lease_until_epoch") or 0)
            owner_alive = _pid_exists(metadata.get("pid"))
            if owner_alive or now <= lease_until:
                self._os_unlock(handle)
                handle.close()
                return False
        self._handle = handle
        self._acquired_at = now
        self._stop.clear()
        self._write_heartbeat()
        self._thread = threading.Thread(target=self._heartbeat_loop, name="unattended-lock-heartbeat", daemon=True)
        self._thread.start()
        return True

    def set_task(self, task_id: str | None) -> None:
        self._task_id = task_id
        if self._handle is not None:
            self._write_heartbeat()

    def heartbeat(self) -> None:
        if self._handle is not None:
            self._write_heartbeat()

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(1.0, self.heartbeat_seconds + 0.5))
        with self._guard:
            now = time.time()
            metadata = {
                "pid": os.getpid(),
                "released": True,
                "released_at": _now_iso(now),
                "lease_until": _now_iso(now),
                "lease_until_epoch": now,
                "task_id": self._task_id,
            }
            self._rewrite(handle, metadata)
            self._os_unlock(handle)
            handle.close()
            self._handle = None
            self._thread = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("监督器锁已被占用")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                self._write_heartbeat()
            except OSError:
                return

    def _write_heartbeat(self) -> None:
        handle = self._handle
        if handle is None:
            return
        with self._guard:
            now = time.time()
            metadata = {
                "pid": os.getpid(),
                "released": False,
                "acquired_at": _now_iso(self._acquired_at),
                "heartbeat_at": _now_iso(now),
                "lease_until": _now_iso(now + self.lease_seconds),
                "lease_until_epoch": now + self.lease_seconds,
                "task_id": self._task_id,
            }
            self._rewrite(handle, metadata)

    @staticmethod
    def _read_metadata(handle) -> dict[str, Any]:
        handle.seek(0)
        content = handle.read().strip()
        if not content:
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _rewrite(handle, metadata: dict[str, Any]) -> None:
        handle.seek(0)
        handle.truncate()
        json.dump(metadata, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())

    @staticmethod
    def _os_lock(handle) -> bool:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                return False
        if msvcrt is not None:
            try:
                handle.seek(0)
                if handle.read(1) == "":
                    handle.seek(0)
                    handle.write(" ")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        raise RuntimeError("当前平台不支持文件锁")

    @staticmethod
    def _os_unlock(handle) -> None:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
