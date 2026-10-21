"""Analysis evidence can resume with a job without claiming script approval."""
import hashlib
import json

import pytest

from test_api import client, video, create_job
from test_cli import run_cli
from workflow.analysis import analyze_video, save_observations


@pytest.fixture
def prepared(tmp_path, video):
    source = tmp_path / 'source.mp4'
    source.write_bytes(video)
    output = tmp_path / 'analysis'
    manifest = analyze_video(source, output, interval=.3, max_frames=4)
    observations = {
        'source_sha256': manifest['source']['sha256'], 'observer': 'test reviewer',
        'observed_at': '2026-10-09T15:00:00+08:00',
        'segments': [{'kind': 'action', 'evidence_status': 'observed',
                      'start_seconds': 0, 'end_seconds': 1,
                      'description': 'Visible test pattern',
                      'frame_ids': [manifest['frames'][0]['id']]}],
        'known_facts': [], 'popularity_hypotheses': [], 'unknowns': [], 'risks': [],
    }
    input_file = tmp_path / 'input.json'
    input_file.write_text(json.dumps(observations))
    save_observations(output / 'manifest.json', input_file)
    return source, output


def test_cli_attaches_analysis_and_resumes_without_repeating_media_work(tmp_path, prepared):
    source, output = prepared
    job = json.loads(run_cli(tmp_path, 'create', '--title', 'analysis', '--product', 'brush').stdout)
    assert run_cli(tmp_path, 'import', job['id'], str(source)).returncode == 0
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    attached = run_cli(tmp_path, 'attach-analysis', job['id'], str(output / 'analysis.json'))
    assert attached.returncode == 0, attached.stderr
    saved = json.loads(run_cli(tmp_path, 'show', job['id']).stdout)
    assert saved['status'] == 'ANALYSIS_REVIEW'
    assert saved['analyses'][0]['asr_status'] == 'not_run'
    assert saved['analyses'][0]['status'] == 'codex_observed_needs_review'
    assert saved['analyses'][0]['source_sha256'] == before
    assert '确认' in saved['next_action']
    again = run_cli(tmp_path, 'attach-analysis', job['id'], str(output / 'analysis.json'))
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)['duplicate'] is True
    assert len(json.loads(run_cli(tmp_path, 'show', job['id']).stdout)['analyses']) == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('altered', ['report.md', 'frames'])
def test_changed_evidence_is_rejected_without_advancing_job(tmp_path, prepared, altered):
    source, output = prepared
    job = json.loads(run_cli(tmp_path, 'create', '--title', 'analysis', '--product', 'brush').stdout)
    run_cli(tmp_path, 'import', job['id'], str(source))
    target = output / 'report.md' if altered == 'report.md' else next((output / 'frames').glob('*.jpg'))
    target.write_bytes(b'changed evidence')
    result = run_cli(tmp_path, 'attach-analysis', job['id'], str(output / 'analysis.json'))
    assert result.returncode == 1
    saved = json.loads(run_cli(tmp_path, 'show', job['id']).stdout)
    assert saved['analyses'] == []
    assert saved['status'] == 'MATERIAL_READY'


def test_api_requires_source_in_this_job_and_rejects_cancelled_job(client, prepared):
    from workflow.analysis_attachment import load_analysis_attachment
    source, output = prepared
    first = create_job(client)
    second = create_job(client)
    client.post('/api/jobs/' + first['id'] + '/assets',
                files={'file': ('source.mp4', source.read_bytes(), 'video/mp4')})
    payload = load_analysis_attachment(output / 'analysis.json')
    assert client.post('/api/jobs/' + second['id'] + '/analyses', json=payload).status_code == 409
    client.post('/api/jobs/' + first['id'] + '/cancel')
    assert client.post('/api/jobs/' + first['id'] + '/analyses', json=payload).status_code == 409
