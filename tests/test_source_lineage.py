"""Synthetic lineage regressions; no real task, voice or render acceptance."""
import copy
import json
from pathlib import Path

import pytest

from tests.test_creation import sample
from tests.test_multisource_creation import mixed
from tests.test_pipeline import pipeline_client, create
from workflow.creation import create_version
from workflow.orchestration.pipeline import Pipeline
from workflow.publishing.prepare import digest
from workflow.store import Asset, database


@pytest.fixture
def lineage_pipeline(pipeline_client, mixed, tmp_path):
    job = create(pipeline_client)
    route = f'/api/jobs/{job["id"]}'
    assets = []
    for source in mixed['sources']:
        path = Path(source['edit_video_path'])
        response = pipeline_client.post(route + '/assets', files={
            'file': (path.name, path.read_bytes(), 'video/mp4')})
        assert response.status_code == 201, response.text
        assets.append(response.json()['asset'])
    mixed.update(job_id=job['id'], market='BR', voiceover_locale='pt-BR',
                 angle='合成测试', hook='先看动作', buyer_concern='想看动作',
                 proof_action='展示合成画面', cta_direction='查看商品')
    spec_path = tmp_path / 'spec.json'
    spec_path.write_text(json.dumps(mixed))
    item = {'ordinal': 1, 'sources': [{'asset_id': a['id']} for a in assets],
            'spec_path': str(spec_path)}
    engine, sessions = database(pipeline_client.app.state.data_dir)
    pipeline = Pipeline(pipeline_client.app.state.data_dir, sessions, tmp_path)
    yield pipeline_client, pipeline, job, item, assets, mixed, tmp_path / 'run'
    engine.dispose()


def bind_originals(pipeline, assets, originals):
    with pipeline.sessions.begin() as session:
        for asset, original in zip(assets, originals):
            record = session.get(Asset, asset['id'])
            record.metadata_json = {**record.metadata_json,
                                    'original_sha256': original, 'original_offset': 0}


@pytest.mark.parametrize('kind', ['raw_repaired', 'two_repaired'])
def test_inputs_reject_two_assets_from_one_original(lineage_pipeline, kind):
    client, pipeline, job, item, assets, _, _ = lineage_pipeline
    original = assets[0]['sha256'] if kind == 'raw_repaired' else 'a' * 64
    if kind == 'raw_repaired':
        bind_originals(pipeline, assets[1:], [original])
    else:
        bind_originals(pipeline, assets, [original, original])
    response = client.post(f'/api/jobs/{job["id"]}/pipeline/inputs', json={'items': [item]})
    assert response.status_code == 422, response.text
    assert '原片' in response.text
    assert not pipeline.job(job['id']).get('pipeline_inputs', {}).get('items')


@pytest.mark.parametrize('kind', ['raw_repaired', 'two_repaired'])
def test_draft_rechecks_lineage_before_reusing_cached_version(lineage_pipeline, kind):
    _, pipeline, job, item, assets, spec, root = lineage_pipeline
    analyses = [s['analysis_path'] for s in spec['sources']]
    first = pipeline._draft(root, job, item, analyses)
    saved_path = Path(first['version_path'])
    before = saved_path.read_bytes()
    original = assets[0]['sha256'] if kind == 'raw_repaired' else 'b' * 64
    if kind == 'raw_repaired':
        bind_originals(pipeline, assets[1:], [original])
    else:
        bind_originals(pipeline, assets, [original, original])
    with pytest.raises(ValueError, match='原片|distinct original'):
        pipeline._draft(root, job, item, analyses)
    assert saved_path.read_bytes() == before


def test_draft_records_originals_and_changes_cache_when_lineage_changes(lineage_pipeline):
    _, pipeline, job, item, assets, spec, root = lineage_pipeline
    analyses = [s['analysis_path'] for s in spec['sources']]
    bind_originals(pipeline, assets, ['a' * 64, 'b' * 64])
    first = pipeline._draft(root, job, item, analyses)
    version = json.loads(Path(first['version_path']).read_text())
    assert [s['original_sha256'] for s in version['sources']] == ['a' * 64, 'b' * 64]
    assert [s['analysis']['source_sha256'] for s in version['sources']] == [a['sha256'] for a in assets]
    assert version['source_diversity'] == {'used_unique_originals': 2, 'minimum_required': 2,
                                          'lineage_check': 'task_asset_metadata'}
    assert pipeline._draft(root, job, item, analyses)['reused'] is True
    before = Path(first['version_path']).read_bytes()
    bind_originals(pipeline, assets[1:], ['c' * 64])
    changed = pipeline._draft(root, job, item, analyses)
    assert changed['reused'] is False
    assert changed['version_path'] != first['version_path']
    assert Path(first['version_path']).read_bytes() == before
    assert json.loads(Path(changed['version_path']).read_text())['sources'][1]['original_sha256'] == 'c' * 64


@pytest.mark.parametrize('placement', ['source', 'top_level'])
def test_spec_cannot_declare_trusted_original_identity(mixed, tmp_path, placement):
    if placement == 'source':
        mixed['sources'][0]['original_sha256'] = 'a' * 64
    else:
        mixed['trusted_source_originals'] = {digest(s['edit_video_path']): 'a' * 64
                                           for s in mixed['sources']}
    with pytest.raises(ValueError, match='trusted|lineage|original_sha256'):
        create_version(mixed, tmp_path / 'versions')
    assert not list((tmp_path / 'versions').glob('v*.json'))


def test_standalone_multisource_states_file_hash_check_boundary(mixed, tmp_path):
    result = create_version(mixed, tmp_path / 'versions')
    version = json.loads(Path(result['version_path']).read_text())
    assert version['source_diversity']['lineage_check'] == 'file_sha256_only'


def test_standalone_trusted_context_rejects_shared_original(mixed, tmp_path):
    originals = {digest(s['edit_video_path']): 'a' * 64 for s in mixed['sources']}
    with pytest.raises(ValueError, match='distinct original'):
        create_version(mixed, tmp_path / 'versions', trusted_source_originals=originals)
    assert not list((tmp_path / 'versions').glob('v*.json'))


@pytest.mark.parametrize('originals', [{}, {'stale': 'a' * 64}])
def test_trusted_context_must_bind_actual_edit_hashes(mixed, tmp_path, originals):
    with pytest.raises(ValueError, match='lineage|SHA|sha256'):
        create_version(mixed, tmp_path / 'versions', trusted_source_originals=originals)


def test_legacy_single_source_keeps_hash_fields_and_approval_bytes(sample, tmp_path):
    _, manifest, spec = sample
    result = create_version(spec, tmp_path / 'versions')
    path = Path(result['version_path'])
    before = path.read_bytes()
    version = json.loads(before)
    assert version['analysis']['source_sha256'] == manifest['source']['sha256']
    assert version['source_diversity'] == {'used_unique_originals': 1, 'minimum_required': 1}
    assert 'original_sha256' not in version['sources'][0]
    # Simulate an existing immutable approval bound to exact draft bytes.
    approval = tmp_path / 'approval.json'
    approval.write_text(json.dumps({'status': 'approved', 'version_sha256': digest(path)}))
    approval_before = approval.read_bytes()
    create_version(copy.deepcopy(spec), tmp_path / 'versions')
    assert path.read_bytes() == before
    assert approval.read_bytes() == approval_before
    assert json.loads(approval.read_text())['version_sha256'] == digest(path)


def test_pipeline_reuses_legacy_single_source_fingerprint_and_approval(lineage_pipeline):
    _, pipeline, job, item, _, spec, root = lineage_pipeline
    legacy = copy.deepcopy(spec)
    source = legacy.pop('sources')[0]
    legacy.update(source)
    legacy.pop('min_unique_sources')
    for shot in legacy['shots']:
        shot['source_index'] = 0
    Path(item['spec_path']).write_text(json.dumps(legacy))
    single_item = {'ordinal': 1, 'asset_id': item['sources'][0]['asset_id'],
                   'spec_path': item['spec_path']}
    # Seed the pre-fix cache key using the independent legacy creation entry.
    def legacy_action(directory):
        version = create_version(legacy, directory / 'versions')
        return {'outputs': [version['version_path']], **version}
    cached = pipeline._stage(root, 'draft-1',
                             {'spec': legacy, 'analysis_sha': digest(source['analysis_path'])},
                             legacy_action)
    path = Path(cached['version_path'])
    before = path.read_bytes()
    approval = path.with_name('zh-approval.json')
    approval.write_text(json.dumps({'status': 'approved', 'version_sha256': digest(path)}))
    approval_before = approval.read_bytes()
    recovered = pipeline._draft(root, job, single_item, [source['analysis_path']])
    assert recovered['reused'] is True
    assert recovered['version_path'] == str(path)
    assert path.read_bytes() == before
    assert approval.read_bytes() == approval_before
    assert json.loads(approval.read_text())['version_sha256'] == digest(path)
