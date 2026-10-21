"""Resume gates only: temporary fixtures never synthesize voice or render."""
import copy
import json
from pathlib import Path

import pytest

from tests.test_creation import sample
from tests.test_multisource_creation import mixed
from tests.test_pipeline import pipeline_client, create
from tests.test_source_lineage import lineage_pipeline, bind_originals
from workflow.publishing.prepare import digest, write_json
from workflow.store import JobAsset


def resume_bundle(context, *, single=False, upstream=False, legacy=False):
    _, pipeline, job, _, assets, _, root = context
    directory = root / 'existing-production'
    directory.mkdir(parents=True)
    chosen = assets[:1] if single else assets
    sources = []
    for asset in chosen:
        path, sha = pipeline.asset(job['id'], asset['id'])
        sources.append({'path': str(path), 'sha256': sha})
    production = {'sources': sources, 'windows': [
        {'source_index': i, 'source_start': i, 'source_end': i + 1,
         'timeline_start': i, 'timeline_end': i + 1} for i in range(len(chosen))]}
    creative = {'job_id': job['id'], 'ordinal': 1, 'production': production}
    if upstream:
        original_sources = []
        for source, asset in zip(sources, chosen):
            with pipeline.sessions() as session:
                from workflow.store import Asset
                original = session.get(Asset, asset['id']).metadata_json.get('original_sha256', asset['sha256'])
            original_sources.append({'edit_video': {'path': source['path'], 'sha256': source['sha256']},
                                     'original_sha256': original})
        version = directory / 'upstream-v0001.json'
        write_json(version, {'job_id': job['id'], 'sources': original_sources})
        creative.update(upstream_version_path=str(version), upstream_version_sha256=digest(version))
    execution = copy.deepcopy(production)
    if legacy:
        assert single
        creative.pop('production')
        execution = {'source': sources[0]['path'], 'source_sha256': sources[0]['sha256'],
                     'windows': production['windows']}
    write_json(directory / 'creative.json', creative)
    write_json(directory / 'execution.json', execution)
    write_json(directory / 'zh-approval.json', {'status': 'approved',
                                              'version_sha256': digest(directory / 'creative.json')})
    write_json(directory / 'localization-v0001.json', {'market': 'BR', 'locale': 'pt-BR'})
    write_json(directory / 'localization-audit-v0001.json', {
        'status': 'passed_independent_internal_text_review',
        'creative_sha256': digest(directory / 'creative.json'),
        'zh_approval_sha256': digest(directory / 'zh-approval.json'),
        'localization_sha256': digest(directory / 'localization-v0001.json'),
        'material_changes_to_approved_meaning': []})
    # Opaque old successful stage cache: resume gate must never rewrite it.
    (directory / 'stage-state.json').write_bytes(b'{"render":{"status":"SUCCEEDED","input":"old"}}\n')
    return directory


def snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


def rebind_fixture_approval(directory):
    """Keep synthetic approvals valid when arranging a malformed source fixture."""
    approval = directory / 'zh-approval.json'
    value = json.loads(approval.read_text())
    value['version_sha256'] = digest(directory / 'creative.json')
    write_json(approval, value)
    audit = directory / 'localization-audit-v0001.json'
    value = json.loads(audit.read_text())
    value.update(creative_sha256=digest(directory / 'creative.json'),
                 zh_approval_sha256=digest(approval))
    write_json(audit, value)


def resume(context, directory, *, adopt=False):
    _, pipeline, job, _, _, _, root = context
    return pipeline._item(root, job, {'ordinal': 1, 'production_directory': str(directory),
                                     'adopt_verified': adopt})


@pytest.mark.parametrize('kind', ['raw_repaired', 'two_repaired', 'duplicate_file'])
@pytest.mark.parametrize('adopt', [False, True])
def test_resume_rejects_shared_original_before_ready(lineage_pipeline, kind, adopt):
    _, pipeline, _, _, assets, _, _ = lineage_pipeline
    directory = resume_bundle(lineage_pipeline)
    if kind == 'raw_repaired':
        bind_originals(pipeline, assets[1:], [assets[0]['sha256']])
    elif kind == 'two_repaired':
        bind_originals(pipeline, assets, ['a' * 64, 'a' * 64])
    else:
        # A duplicate entry must not inflate sources even when hashes belong to the task.
        for name in ('creative.json', 'execution.json'):
            path = directory / name
            value = json.loads(path.read_text())
            config = value.get('production', value)
            config['sources'][1] = copy.deepcopy(config['sources'][0])
            write_json(path, value)
        rebind_fixture_approval(directory)
    before = snapshot(directory)
    with pytest.raises(ValueError, match='原片'):
        resume(lineage_pipeline, directory, adopt=adopt)
    assert snapshot(directory) == before


@pytest.mark.parametrize('location', ['production_source', 'upstream'])
def test_resume_rejects_changed_saved_lineage_even_when_sources_remain_distinct(lineage_pipeline, location):
    _, pipeline, _, _, assets, _, _ = lineage_pipeline
    bind_originals(pipeline, assets, ['a' * 64, 'b' * 64])
    directory = resume_bundle(lineage_pipeline, upstream=location == 'upstream')
    if location == 'production_source':
        creative = json.loads((directory / 'creative.json').read_text())
        creative['production']['sources'][1]['original_sha256'] = 'b' * 64
        write_json(directory / 'creative.json', creative)
        rebind_fixture_approval(directory)
    bind_originals(pipeline, assets[1:], ['c' * 64])
    before = snapshot(directory)
    with pytest.raises(ValueError, match='血缘.*新版本'):
        resume(lineage_pipeline, directory)
    assert snapshot(directory) == before


@pytest.mark.parametrize('change', ['changed_hash', 'swapped_sources', 'foreign_job'])
def test_resume_requires_bound_upstream_evidence(lineage_pipeline, change):
    _, _, job, _, _, _, _ = lineage_pipeline
    directory = resume_bundle(lineage_pipeline, upstream=True)
    path = directory / 'upstream-v0001.json'
    version = json.loads(path.read_text())
    if change == 'swapped_sources':
        version['sources'].reverse()
    elif change == 'foreign_job':
        version['job_id'] = 'job_other'
    else:
        version['sources'][0]['original_sha256'] = 'c' * 64
    write_json(path, version)
    if change != 'changed_hash':
        creative = json.loads((directory / 'creative.json').read_text())
        creative['upstream_version_sha256'] = digest(path)
        write_json(directory / 'creative.json', creative)
        rebind_fixture_approval(directory)
    before = snapshot(directory)
    with pytest.raises(ValueError, match='上游|血缘'):
        resume(lineage_pipeline, directory)
    assert snapshot(directory) == before


def test_resume_checks_execution_sources_as_well_as_creative(lineage_pipeline):
    directory = resume_bundle(lineage_pipeline)
    path = directory / 'execution.json'
    execution = json.loads(path.read_text())
    execution['sources'][1]['sha256'] = 'f' * 64
    write_json(path, execution)
    before = snapshot(directory)
    with pytest.raises(ValueError, match='本任务'):
        resume(lineage_pipeline, directory)
    assert snapshot(directory) == before


def test_resume_rejects_asset_link_removed_from_task(lineage_pipeline):
    _, pipeline, job, _, assets, _, _ = lineage_pipeline
    directory = resume_bundle(lineage_pipeline)
    with pipeline.sessions.begin() as session:
        session.delete(session.get(JobAsset, (job['id'], assets[1]['id'])))
    before = snapshot(directory)
    with pytest.raises(ValueError, match='本任务'):
        resume(lineage_pipeline, directory)
    assert snapshot(directory) == before


@pytest.mark.parametrize('kind', ['legacy_single', 'configured_single', 'legacy_mix', 'bound_mix'])
def test_valid_resume_preserves_existing_approval_and_cache_bytes(lineage_pipeline, kind):
    _, pipeline, _, _, assets, _, _ = lineage_pipeline
    if kind == 'bound_mix':
        bind_originals(pipeline, assets, ['a' * 64, 'b' * 64])
    directory = resume_bundle(lineage_pipeline, single=kind.endswith('single'),
                              legacy=kind == 'legacy_single', upstream=kind == 'bound_mix')
    before = snapshot(directory)
    first = resume(lineage_pipeline, directory)
    second = resume(lineage_pipeline, directory)
    assert first['status'] == second['status'] == 'READY_PRODUCTION'
    assert first['production_directory'] == second['production_directory'] == str(directory.resolve())
    assert snapshot(directory) == before
