"""Persist stage inputs and outputs so a local batch resumes safely."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Callable


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def _write_state(path: Path, state: dict) -> None:
    from workflow.platforms.files import atomic_json
    atomic_json(path, state)


def _valid_outputs(record: dict) -> bool:
    if not record.get('outputs'):
        return False
    for item in record.get('outputs', []):
        path = Path(item['path'])
        if not path.is_file():
            return False
        if item.get('sha256') != _file_digest(path):
            return False
    return True


class StageStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / 'stage-state.json'

    def _read(self):
        return json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else {}

    def can_retry(self, stage: str, input_fingerprint: str) -> bool:
        previous = self._read().get(stage, {})
        return previous.get('input') == input_fingerprint and previous.get('status') == 'FAILED'

    def run(self, stage: str, input_fingerprint: str, action: Callable[[], dict]):
        state = self._read()
        previous = state.get(stage)
        if previous:
            if previous.get('input') != input_fingerprint:
                raise ValueError(f'{stage} input changed; use a new run directory')
            if previous.get('status') == 'SUCCEEDED' and _valid_outputs(previous):
                return {**previous, 'reused': True}
        record = {'stage': stage, 'input': input_fingerprint, 'status': 'RUNNING',
                  'started_at': time.time()}
        state[stage] = record
        _write_state(self.state_path, state)
        started = time.perf_counter()
        try:
            result = action()
            outputs = []
            for raw in result.get('outputs', []):
                path = Path(raw)
                if not path.is_file():
                    raise ValueError(f'{stage} did not produce expected output: {path}')
                outputs.append({'path': str(path), 'sha256': _file_digest(path)})
            if not outputs:
                raise ValueError(f'{stage} produced no outputs')
            record.update(status='SUCCEEDED', outputs=outputs, result=result,
                          elapsed_seconds=round(time.perf_counter() - started, 3),
                          finished_at=time.time())
            state[stage] = record
            _write_state(self.state_path, state)
            return {**record, 'reused': False}
        except Exception as error:
            record.update(status='FAILED', error={'type': type(error).__name__, 'message': str(error)},
                          elapsed_seconds=round(time.perf_counter() - started, 3),
                          finished_at=time.time())
            state[stage] = record
            _write_state(self.state_path, state)
            raise


def preflight(binding: dict) -> dict:
    """Fail before synthesis/render if the requested approval evidence is stale."""
    creative = Path(binding['plan']).with_name('creative.json')
    approval = Path(binding['plan']).with_name('zh-approval.json')
    localization = Path(binding['localization'])
    audit = Path(binding['audit'])
    if not creative.is_file() or not approval.is_file():
        raise ValueError('Chinese approval evidence is missing')
    creative_sha = hashlib.sha256(creative.read_bytes()).hexdigest()
    approved = json.loads(approval.read_text(encoding='utf-8'))
    if approved.get('status') != 'approved' or approved.get('version_sha256') != creative_sha:
        raise ValueError('Chinese approval does not bind current creative')
    if not localization.is_file() or not audit.is_file():
        raise ValueError('localization audit evidence is missing')
    localization_sha = _file_digest(localization)
    approval_sha = _file_digest(approval)
    evidence = json.loads(audit.read_text(encoding='utf-8'))
    if (evidence.get('status') != 'passed_independent_internal_text_review'
            or evidence.get('localization_sha256') != localization_sha
            or evidence.get('creative_sha256') != creative_sha
            or evidence.get('zh_approval_sha256') != approval_sha
            or evidence.get('material_changes_to_approved_meaning') != []):
        raise ValueError('localization audit does not bind current approved meaning')
    return {'status': 'ready', 'creative_sha256': creative_sha,
            'localization_sha256': localization_sha, 'audit_sha256': _file_digest(audit)}
