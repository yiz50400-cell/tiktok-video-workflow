
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .inbox import Inbox
from .lock import LeaseFileLock
from .notify import TaskNotifier
from .producer import ProduceOptions, Producer
from .schema import RequestValidationError, raw_sha256, request_sha256, validate_request

LOGGER = logging.getLogger("workflow.unattended")


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _call_maybe_async(callable_obj: Callable[[], Any]) -> Any:
    result = callable_obj()
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _health_is_ok(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        for key in ("healthy", "ok", "available", "ready"):
            if key in result:
                return bool(result[key])
        status = result.get("status")
        if isinstance(status, str):
            return status.lower() in {"healthy", "ok", "ready", "available", "running"}
    for key in ("healthy", "ok", "available", "ready"):
        if hasattr(result, key):
            return bool(getattr(result, key))
    status = getattr(result, "status", None)
    if isinstance(status, str):
        return status.lower() in {"healthy", "ok", "ready", "available", "running"}
    return False


def default_probe() -> Any:
    from workflow.chatcut.health import probe_chatcut
    return probe_chatcut()


class WorkflowApiAdapter:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser().resolve()

    def create_job(self, request: dict[str, Any]) -> str:
        payload = {
            "title": request["title"],
            "product": request["product"],
            "market": request["market"],
            "voiceover_locale": None,
            "style": request.get("style") or "达人演示",
            "count": request["count"],
            "budget": 0,
            "owner": "本机无人值守",
            "sources": [],
            "notes": request.get("notes") or "",
        }
        result = asyncio.run(self._call("POST", "/api/jobs", payload))
        job_id = self._find_job_id(result)
        if not job_id:
            raise RuntimeError("create 已返回成功，但未找到 job_ 工作流任务编号")
        return job_id

    def plan_batch(self, workflow_job_id: str, concepts: list[Any]) -> None:
        asyncio.run(self._call("POST", f"/api/jobs/{workflow_job_id}/batch/concepts", {"concepts": concepts}))

    async def _call(self, method: str, route: str, payload: dict[str, Any]) -> Any:
        import httpx
        from workflow.api import create_app

        app = create_app(self.data_dir)
        async with app.router.lifespan_context(app):
            token = (self.data_dir / "access.token").read_text(encoding="utf-8").strip()
            headers = {"Authorization": "Bearer " + token, "X-Workflow-Client": "studio"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
                headers=headers,
                timeout=60,
            ) as client:
                response = await client.request(method, route, json=payload)
                if not response.is_success:
                    try:
                        detail = response.json().get("detail", "操作失败")
                    except ValueError:
                        detail = response.text or "操作失败"
                    if isinstance(detail, list):
                        detail = "；".join(
                            f"{item.get('loc', ['字段'])[-1]}：{item.get('msg', '校验失败')}" for item in detail
                        )
                    raise RuntimeError(str(detail))
                return response.json()

    @classmethod
    def _find_job_id(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.startswith("job_"):
            return value
        if isinstance(value, dict):
            for key in ("id", "job_id", "workflow_job_id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("job_"):
                    return candidate
            for child in value.values():
                candidate = cls._find_job_id(child)
                if candidate:
                    return candidate
        if isinstance(value, list):
            for child in value:
                candidate = cls._find_job_id(child)
                if candidate:
                    return candidate
        return None


WorkflowCliAdapter = WorkflowApiAdapter


class Supervisor:
    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        probe: Callable[[], Any] | None = None,
        workflow_client: Any | None = None,
        lease_seconds: float = 90,
        heartbeat_seconds: float = 10,
        logger: logging.Logger | None = None,
        producer: Any | None = None,
        notifier: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.inbox = Inbox(data_dir)
        self.probe = probe or default_probe
        self.workflow_client = workflow_client or WorkflowApiAdapter(self.inbox.paths.root.parent)
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.logger = logger or LOGGER
        self.producer = producer
        self.notifier = TaskNotifier.notify if notifier is None else notifier

    def run_once(
        self,
        *,
        produce: bool = False,
        produce_options: ProduceOptions | None = None,
    ) -> dict[str, Any]:
        self._produce = produce
        self._produce_options = produce_options or ProduceOptions()
        try:
            health = _call_maybe_async(self.probe)
        except Exception as exc:
            self._log(f"ChatCut 健康探针失败：{exc}。未认领新任务，请先运行 chatcut_health 检查。")
            return {"status": "deferred", "reason": f"ChatCut 健康探针失败：{exc}"}
        if not _health_is_ok(health):
            self._log("ChatCut 当前不健康，未认领新任务；本阶段不会自动 apply 或重启。")
            return {"status": "deferred", "reason": "ChatCut 当前不健康，未认领新任务"}
        self._log("ChatCut 健康探针通过。")

        lock = LeaseFileLock(
            self.inbox.paths.lock,
            lease_seconds=self.lease_seconds,
            heartbeat_seconds=self.heartbeat_seconds,
        )
        if not lock.acquire():
            self._log("已有一个无人值守监督器持有锁，本次立即退出。")
            return {"status": "locked", "reason": "监督器锁已被占用"}
        try:
            claimed = self.inbox.claim_next()
            if claimed is None:
                self._log("收件箱没有可处理的 JSON 任务。")
                return {"status": "idle", "reason": "没有待处理任务"}
            self._log(f"已原子认领任务文件：{claimed.name}")
            try:
                result = self._process_claimed(claimed, lock)
            except Exception as exc:
                self._safe_notify({"status": "failed", "task_id": claimed.stem,
                                   "reason": f"处理异常：{type(exc).__name__}: {exc}"})
                raise
            self._safe_notify(result)
            return result
        finally:
            lock.release()

    def watch(
        self,
        interval: float = 30,
        *,
        produce: bool = False,
        produce_options: ProduceOptions | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("轮询间隔必须大于 0")
        self._log(f"开始轮询无人值守收件箱，间隔 {interval:g} 秒；按 Ctrl+C 停止。")
        try:
            while True:
                self.run_once(produce=produce, produce_options=produce_options)
                time.sleep(interval)
        except KeyboardInterrupt:
            self._log("收到 Ctrl+C，无人值守监督器已优雅停止。")

    def _process_claimed(self, claimed: Path, lock: LeaseFileLock) -> dict[str, Any]:
        raw = claimed.read_bytes()
        raw_hash = raw_sha256(raw)
        try:
            decoded = json.loads(raw.decode("utf-8"))
            request = validate_request(decoded, check_media_exists=True)
        except (UnicodeDecodeError, json.JSONDecodeError, RequestValidationError) as exc:
            task_id = f"invalid_{raw_hash[:20]}"
            lock.set_task(task_id)
            now = _utc_now()
            reason = f"请求校验失败：{exc}"
            state = {
                "task_id": task_id,
                "status": "failed",
                "reason": reason,
                "request_sha256": raw_hash,
                "workflow_job_id": None,
                "attempt": 1,
                "created_at": now,
                "updated_at": now,
                "source_file": claimed.name,
            }
            self.inbox.write_task_request(task_id, {"source_file": claimed.name, "request_sha256": raw_hash})
            self.inbox.write_task_state(task_id, state)
            self.inbox.move(claimed, "failed")
            self._log(reason)
            return {"status": "failed", "task_id": task_id, "reason": reason}

        task_id = request["task_id"]
        sha256 = request_sha256(request)
        lock.set_task(task_id)
        existing = self.inbox.read_task_state(task_id)
        if existing:
            existing_hash = existing.get("request_sha256")
            if existing_hash == sha256:
                now = _utc_now()
                existing["duplicate_count"] = int(existing.get("duplicate_count") or 0) + 1
                existing["last_duplicate_at"] = now
                existing["updated_at"] = now
                existing["reason"] = "重复投递：task_id 与请求哈希均相同，未重复创建工作流任务"
                self.inbox.write_task_state(task_id, existing)
                self.inbox.move(claimed, "done")
                self._log(f"任务 {task_id} 为同哈希重复投递，沿用 {existing.get('workflow_job_id') or '原状态'}。")
                return {
                    "status": "duplicate",
                    "task_id": task_id,
                    "workflow_job_id": existing.get("workflow_job_id"),
                    "reason": existing["reason"],
                }
            now = _utc_now()
            reason = "任务冲突：相同 task_id 对应不同 request_sha256，未覆盖原任务"
            conflict_state = {
                "task_id": task_id,
                "status": "conflict",
                "reason": reason,
                "request_sha256": sha256,
                "existing_request_sha256": existing_hash,
                "workflow_job_id": existing.get("workflow_job_id"),
                "attempt": existing.get("attempt", 1),
                "created_at": now,
                "updated_at": now,
                "source_file": claimed.name,
            }
            self.inbox.write_conflict(task_id, sha256, request, conflict_state)
            self.inbox.move(claimed, "conflict")
            self._log(f"任务 {task_id} 发生哈希冲突，已移入 conflict。")
            return {"status": "conflict", "task_id": task_id, "reason": reason}

        now = _utc_now()
        state = {
            "task_id": task_id,
            "status": "processing",
            "reason": "已认领，正在创建工作流任务",
            "request_sha256": sha256,
            "workflow_job_id": None,
            "attempt": 1,
            "created_at": now,
            "updated_at": now,
            "source_file": claimed.name,
            "media_paths": request.get("media_paths", []),
        }
        self.inbox.write_task_request(task_id, request)
        self.inbox.write_task_state(task_id, state)

        workflow_job_id: str | None = None
        try:
            self._log(f"正在为任务 {task_id} 调用现有 create 管线。")
            workflow_job_id = self.workflow_client.create_job(request)
            if not isinstance(workflow_job_id, str) or not workflow_job_id.startswith("job_"):
                raise RuntimeError("create 返回了无效的工作流任务编号")
            state["workflow_job_id"] = workflow_job_id
            self.inbox.write_task_state(task_id, state)
            concepts = request.get("concepts")
            if concepts:
                self._log(f"任务 {task_id} 含 concepts，正在登记 plan-batch。")
                self.workflow_client.plan_batch(workflow_job_id, concepts)
            if getattr(self, "_produce", False):
                self._log(f"任务 {task_id} 已创建 {workflow_job_id}，开始 --produce 制作。")
                producer = self.producer or Producer(
                    self.inbox,
                    probe=self.probe,
                    options=getattr(self, "_produce_options", None) or ProduceOptions(),
                    log=self._log,
                )
                produce_result = producer.produce(
                    task_id=task_id,
                    workflow_job_id=workflow_job_id,
                    request=request,
                )
                state = self.inbox.read_task_state(task_id) or state
                if produce_result.status == "produced":
                    self.inbox.move(claimed, "done")
                    self._log(f"任务 {task_id} 制作完成：{produce_result.reason}")
                    return {
                        "status": "produced",
                        "task_id": task_id,
                        "workflow_job_id": workflow_job_id,
                        "reason": produce_result.reason,
                        "delivery_paths": produce_result.delivery_paths,
                    }
                self.inbox.move(claimed, "failed")
                self._log(f"任务 {task_id} 制作失败：{produce_result.reason}")
                return {
                    "status": "produce_failed",
                    "task_id": task_id,
                    "workflow_job_id": workflow_job_id,
                    "reason": produce_result.reason,
                    "delivery_paths": produce_result.delivery_paths,
                }

            now = _utc_now()
            state.update({
                "status": "succeeded",
                "reason": "工作流任务已创建；未开启 --produce，故未运行 ChatCut/Codex 制作或发布",
                "workflow_job_id": workflow_job_id,
                "updated_at": now,
                "completed_at": now,
            })
            self.inbox.write_task_state(task_id, state)
            self.inbox.move(claimed, "done")
            self._log(f"任务 {task_id} 已完成，workflow_job_id={workflow_job_id}。")
            return {
                "status": "succeeded",
                "task_id": task_id,
                "workflow_job_id": workflow_job_id,
                "reason": state["reason"],
            }
        except Exception as exc:
            now = _utc_now()
            if getattr(self, "_produce", False) and workflow_job_id:
                reason = f"制作阶段异常（工作流任务已创建为 {workflow_job_id}）：{exc}"
                status = "produce_failed"
            elif workflow_job_id:
                reason = f"create/plan 失败（工作流任务已创建为 {workflow_job_id}）：{exc}"
                status = "failed"
            else:
                reason = f"创建工作流任务失败：{exc}"
                status = "failed"
            state.update({
                "status": status,
                "reason": reason,
                "workflow_job_id": workflow_job_id,
                "updated_at": now,
                "failed_at": now,
            })
            self.inbox.write_task_state(task_id, state)
            self.inbox.move(claimed, "failed")
            self._log(f"任务 {task_id} 失败：{exc}")
            return {
                "status": status,
                "task_id": task_id,
                "workflow_job_id": workflow_job_id,
                "reason": reason,
            }
        finally:
            lock.set_task(None)

    def _safe_notify(self, result: dict[str, Any]) -> None:
        try:
            self.notifier(result)
        except Exception as exc:  # 通知失败不影响任务
            self.logger.warning("完成提醒发送失败（已忽略）：%s", exc)
            self._log(f"完成提醒发送失败（已忽略）：{exc}")

    def _log(self, message: str) -> None:
        self.logger.info(message)
        print(f"[无人值守] {message}")
