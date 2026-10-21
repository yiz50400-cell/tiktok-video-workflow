"""Verify existing analysis artifacts without decoding or regenerating them."""
import hashlib
import json
from pathlib import Path

from .analysis.core import _validate_observations


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_snapshot(payload):
    index, manifest, observations = (payload[key] for key in ('index', 'manifest', 'observations'))
    if index.get('schema_version') != 1 or manifest.get('schema_version') != 1:
        raise ValueError('分析文件版本不支持。')
    if index.get('status') != 'codex_observed_needs_review':
        raise ValueError('本入口只接入待审分析，不代签分析或稿件批准。')
    if index.get('asr_status') != 'not_run' or index.get('ocr_status') != 'not_run':
        raise ValueError('当前分析模块尚未接 ASR/OCR，不能登记为已执行。')
    if index['source_sha256'] != manifest['source']['sha256']:
        raise ValueError('分析与源素材哈希不一致。')
    _validate_observations(manifest, observations)
    if index.get('segments') != len(observations['segments']):
        raise ValueError('分析区间数量不一致。')


def load_analysis_attachment(index_path):
    index_path = Path(index_path)
    if not index_path.is_absolute() or not index_path.is_file():
        raise ValueError('请提供真实 analysis.json 的绝对路径。')
    index = json.loads(index_path.read_text(encoding='utf-8'))
    files = {}
    for kind in ('manifest', 'observations', 'report'):
        path = Path(index[kind + '_path'])
        if not path.is_absolute() or not path.is_file() or sha256(path) != index[kind + '_sha256']:
            raise ValueError(f'{kind} 文件缺失或哈希改变，请核对分析版本。')
        files[kind] = path
    payload = {
        'index_path': str(index_path.resolve()), 'index_sha256': sha256(index_path),
        'index': index, 'manifest': json.loads(files['manifest'].read_text(encoding='utf-8')),
        'observations': json.loads(files['observations'].read_text(encoding='utf-8')),
    }
    validate_snapshot(payload)
    manifest = payload['manifest']
    checks = [manifest['source'], *manifest['frames']]
    evidence = manifest['source'].get('source_evidence_path')
    if evidence:
        checks.append({'path': evidence, 'sha256': manifest['source']['source_evidence_sha256']})
    for item in checks:
        path = Path(item['path'])
        if not path.is_absolute() or not path.is_file() or sha256(path) != item['sha256']:
            raise ValueError('源视频、证据页或取帧文件缺失/改变，不能接入旧分析。')
    payload['contact_sheet_hashes'] = {}
    for sheet in manifest['contact_sheets']:
        path = Path(sheet['path'])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('联系图缺失。')
        payload['contact_sheet_hashes'][str(path)] = sha256(path)
    return payload
