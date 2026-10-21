
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from workflow.unattended.inbox import Inbox
from workflow.unattended.lock import LeaseFileLock
from workflow.unattended.supervisor import Supervisor


class FakeWorkflowClient:
    def __init__(self):
        self.created = []
        self.planned = []

    def create_job(self, request):
        self.created.append(dict(request))
        return f"job_{len(self.created):04d}"

    def plan_batch(self, workflow_job_id, concepts):
        self.planned.append((workflow_job_id, list(concepts)))


def make_supervisor(tmp_path: Path, client=None, healthy=True):
    data_dir = tmp_path / "data"
    client = client or FakeWorkflowClient()
    probe = lambda: {"healthy": healthy}
    return Supervisor(data_dir=data_dir, probe=probe, workflow_client=client), client


def valid_payload(**updates):
    payload = {
        "task_id": "task_demo_001",
        "title": "墨西哥口红样片",
        "product": "唇釉",
        "market": "MX",
        "count": 2,
        "style": "自然带货",
        "notes": "阶段1只创建工作流任务",
    }
    payload.update(updates)
    return payload


def test_validation_failure_moves_to_failed(tmp_path):
    supervisor, client = make_supervisor(tmp_path)
    inbox = supervisor.inbox
    bad = inbox.paths.incoming / "bad.json"
    bad.write_text(json.dumps({"title": "", "product": "唇釉", "market": "MX", "count": 1}), encoding="utf-8")

    result = supervisor.run_once()

    assert result["status"] == "failed"
    assert not list(inbox.paths.incoming.glob("*.json"))
    assert len(list(inbox.paths.failed.glob("*.json"))) == 1
    assert client.created == []
    state_files = list(inbox.paths.tasks.glob("*/state.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "不能为空" in state["reason"]


def test_enqueue_uses_partial_then_atomic_replace(tmp_path, monkeypatch):
    inbox = Inbox(tmp_path / "data")
    calls = []
    real_replace = os.replace

    def recording_replace(src, dst):
        calls.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("workflow.unattended.inbox.os.replace", recording_replace)
    queued = inbox.enqueue(valid_payload())

    assert queued.exists()
    assert queued.suffix == ".json"
    assert any(src.name.endswith(".partial") and dst == queued for src, dst in calls)
    assert not list(inbox.paths.incoming.glob("*.partial"))


def _sample_concepts():
    return [
        {
            "ordinal": 1,
            "angle": "教程型",
            "hook": "先痛点后演示",
            "buyer_concern": "是否好用",
            "proof_requirement": "真人上脸对比",
            "cta_direction": "关注店铺",
            "source_asset_ids": [],
        },
        {
            "ordinal": 2,
            "angle": "前后对比",
            "hook": "三秒见效果",
            "buyer_concern": "会不会拔干",
            "proof_requirement": "近景细节",
            "cta_direction": "点击购买",
            "source_asset_ids": [],
        },
    ]


def test_claim_then_create_and_optional_plan(tmp_path):
    supervisor, client = make_supervisor(tmp_path)
    payload = valid_payload(concepts=_sample_concepts())
    supervisor.inbox.enqueue(payload)

    result = supervisor.run_once()

    assert result["status"] == "succeeded"
    assert result["workflow_job_id"].startswith("job_")
    assert len(client.created) == 1
    assert client.planned == [(result["workflow_job_id"], _sample_concepts())]
    assert len(list(supervisor.inbox.paths.done.glob("*.json"))) == 1
    state = json.loads((supervisor.inbox.task_dir(payload["task_id"]) / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["workflow_job_id"] == result["workflow_job_id"]


def test_same_task_and_hash_is_duplicate_without_second_create(tmp_path):
    supervisor, client = make_supervisor(tmp_path)
    payload = valid_payload()
    supervisor.inbox.enqueue(payload)
    first = supervisor.run_once()
    supervisor.inbox.enqueue(payload)
    second = supervisor.run_once()

    assert first["status"] == "succeeded"
    assert second["status"] == "duplicate"
    assert len(client.created) == 1
    assert len(list(supervisor.inbox.paths.done.glob("*.json"))) == 2


def test_same_task_different_hash_moves_to_conflict(tmp_path):
    supervisor, client = make_supervisor(tmp_path)
    supervisor.inbox.enqueue(valid_payload(notes="版本A"))
    assert supervisor.run_once()["status"] == "succeeded"
    supervisor.inbox.enqueue(valid_payload(notes="版本B"))

    result = supervisor.run_once()

    assert result["status"] == "conflict"
    assert len(client.created) == 1
    assert len(list(supervisor.inbox.paths.conflict.glob("*.json"))) == 1
    conflict_records = list((supervisor.inbox.task_dir("task_demo_001") / "conflicts").glob("*/state.json"))
    assert len(conflict_records) == 1


def test_concurrency_lock_allows_only_one_supervisor(tmp_path):
    supervisor, client = make_supervisor(tmp_path)
    supervisor.inbox.enqueue(valid_payload())
    held = LeaseFileLock(supervisor.inbox.paths.lock, lease_seconds=60, heartbeat_seconds=5)
    assert held.acquire()
    try:
        result = supervisor.run_once()
    finally:
        held.release()

    assert result["status"] == "locked"
    assert len(client.created) == 0
    assert len(list(supervisor.inbox.paths.incoming.glob("*.json"))) == 1


def test_unhealthy_probe_does_not_claim(tmp_path):
    supervisor, client = make_supervisor(tmp_path, healthy=False)
    supervisor.inbox.enqueue(valid_payload())

    result = supervisor.run_once()

    assert result["status"] == "deferred"
    assert len(client.created) == 0
    assert len(list(supervisor.inbox.paths.incoming.glob("*.json"))) == 1
    assert not list(supervisor.inbox.paths.processing.glob("*.json"))


def test_missing_media_path_fails_in_processing(tmp_path):
    supervisor, client = make_supervisor(tmp_path)
    payload = valid_payload(task_id="task_media_missing", media_paths=[str(tmp_path / "missing.mp4")])
    # 模拟外部系统按约定原子投递，但内容校验留给 supervisor。
    partial = supervisor.inbox.paths.incoming / "media.json.partial"
    partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, supervisor.inbox.paths.incoming / "media.json")

    result = supervisor.run_once()

    assert result["status"] == "failed"
    assert len(client.created) == 0
    assert "不存在" in result["reason"]



def test_invalid_concepts_rejected_at_enqueue(tmp_path):
    inbox = Inbox(tmp_path / "data")
    with pytest.raises(Exception) as exc:
        inbox.enqueue(valid_payload(concepts=["教程型"]))
    assert "plan-batch" in str(exc.value) or "对象" in str(exc.value)
