"""Synthetic regression evidence only; never real batch acceptance."""
import copy
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from tests.test_creation import sample
from workflow.analysis import analyze_video, save_observations
from workflow.creation import create_version


@pytest.fixture
def mixed(sample, tmp_path):
    _, _, original = sample
    spec = copy.deepcopy(original)
    second = tmp_path / 'second.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi',
                    '-i', 'testsrc2=size=180x320:rate=12', '-vf', 'hue=h=90',
                    '-t', '2', '-c:v', 'libx264', str(second)], check=True)
    folder = tmp_path / 'second-analysis'
    manifest = analyze_video(second, folder, interval=.4, max_frames=10)
    observations = tmp_path / 'second-observations.json'
    observations.write_text(json.dumps({
        'source_sha256': manifest['source']['sha256'], 'observer': 'test observer',
        'observed_at': '2026-10-14T00:00:00Z',
        'segments': [{'kind': 'action', 'evidence_status': 'observed',
                      'start_seconds': 1, 'end_seconds': 2,
                      'description': 'Second synthetic source: only final second observed',
                      'frame_ids': [f['id'] for f in manifest['frames']
                                    if f['source_time_seconds'] >= 1]}],
        'known_facts': [], 'popularity_hypotheses': [], 'unknowns': [], 'risks': []}))
    save_observations(folder / 'manifest.json', observations)
    spec['sources'] = [
        {'analysis_path': spec.pop('analysis_path'),
         'edit_video_path': spec.pop('edit_video_path')},
        {'analysis_path': str(folder / 'analysis.json'), 'edit_video_path': str(second)}]
    spec.pop('transcode_verification_path', None)
    for index, shot in enumerate(spec['shots']):
        shot['source_index'] = index
    spec['min_unique_sources'] = 2
    return spec


def test_mixed_version_binds_each_shot_to_its_own_analysis(mixed, tmp_path):
    result = create_version(mixed, tmp_path / 'versions')
    saved = json.loads(Path(result['version_path']).read_text())
    assert len(saved['sources']) == 2
    assert len({s['analysis']['source_sha256'] for s in saved['sources']}) == 2
    assert [s['source_index'] for s in saved['shots']] == [0, 1]
    assert saved['source_diversity']['used_unique_originals'] == 2
    assert saved['analysis'] == saved['sources'][0]['analysis']
    assert saved['human_approval']['status'] == 'pending_user'


@pytest.mark.parametrize('change,match', [
    (lambda s: s['shots'][1].pop('source_index'), 'source_index'),
    (lambda s: s['shots'][1].update(source_index=True), 'source_index'),
    (lambda s: s['shots'][1].update(source_index=-1), 'source_index'),
    (lambda s: s['shots'][1].update(source_index=2), 'source_index'),
    (lambda s: s['shots'][0].update(source_index=1), 'observed evidence'),
    (lambda s: s['sources'].__setitem__(1, copy.deepcopy(s['sources'][0])), 'distinct original'),
    (lambda s: s['shots'][1].update(source_index=0), 'unused source'),
    (lambda s: s.update(min_unique_sources=3), 'unique source'),
])
def test_mixed_version_rejects_wrong_or_fake_source_evidence(mixed, tmp_path, change, match):
    change(mixed)
    with pytest.raises(ValueError, match=match):
        create_version(mixed, tmp_path / 'versions')
    assert not list((tmp_path / 'versions').glob('v*.json'))
