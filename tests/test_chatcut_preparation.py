import hashlib
import struct
import wave
from copy import deepcopy
from pathlib import Path

import pytest
from workflow.production import chatcut


def plan():
    return {
        'fps': 30, 'duration_frames': 120, 'min_sources': 2,
        'sources': [
            {'key': 'source-a', 'sha256': 'a' * 64, 'duration_seconds': 10},
            {'key': 'source-b', 'sha256': 'b' * 64, 'duration_seconds': 10},
        ],
        'shots': [
            {'asset_key': 'source-a', 'start_frame': 0, 'duration_frames': 60, 'source_in_us': 1000000},
            {'asset_key': 'source-b', 'start_frame': 60, 'duration_frames': 60, 'source_in_us': 3000000},
        ],
        'scenes': [
            {'asset_key': 'voice-01', 'start_frame': 0, 'duration_frames': 60, 'text': 'First sentence.'},
            {'asset_key': 'voice-02', 'start_frame': 60, 'duration_frames': 60, 'text': 'Second sentence.'},
        ],
        'titles': [{'asset_key': 'title', 'start_frame': 60, 'duration_frames': 60,
                    'properties': {'nameText': 'A title'},
                    'layout': {'left': 0, 'top': -700, 'width': 1920, 'height': 1080}}],
        'title_tail_frames': 6, 'title_fade_frames': 6, 'voice_gain_db': 4,
    }


ASSETS = {k: 'id-' + k for k in ['source-a', 'source-b', 'voice-01', 'voice-02', 'title']}
TRACKS = {'picture': 'V1', 'voice': 'A1', 'titles': 'V2'}


def test_one_batch_preserves_offsets_windows_and_clear_tail():
    result = chatcut.build_edit_batch(plan(), ASSETS, TRACKS)
    assert len(result['adds']) == 5
    picture = [i for i in result['adds'] if i['type'] == 'video']
    assert [i['sourceIn'] for i in picture] == [1000000, 3000000]
    assert all(i['muted'] for i in picture)
    audio = [i for i in result['adds'] if i['type'] == 'audio']
    assert [(i['startFrame'], i['durationFrames'], i['sourceIn']) for i in audio] == [(0, 60, 0), (60, 60, 0)]
    title = [i for i in result['adds'] if i['type'] == 'motion-graphic'][0]
    assert title['startFrame'] + title['durationFrames'] == 114
    assert title['fadeOutDurationFrames'] == 6
    assert title['propertyOverrides']['nameText'] == 'A title'


@pytest.mark.parametrize('fault', ['gap', 'overrun', 'duplicate-source', 'missing-asset', 'voice-gap', 'short-title'])
def test_invalid_batch_is_rejected_before_editor_mutation(fault):
    p = plan(); assets = dict(ASSETS)
    if fault == 'gap': p['shots'][1]['start_frame'] = 61
    if fault == 'overrun': p['shots'][1]['source_in_us'] = 9500000
    if fault == 'duplicate-source': p['sources'][1]['sha256'] = p['sources'][0]['sha256']
    if fault == 'missing-asset': assets.pop('voice-02')
    if fault == 'voice-gap': p['scenes'][1]['start_frame'] = 61
    if fault == 'short-title': p['titles'][0].update(start_frame=117, duration_frames=3)
    with pytest.raises(ValueError): chatcut.build_edit_batch(p, assets, TRACKS)


def test_sample_exact_split_reuses_hashes_and_repairs_only_corrupt_scene(tmp_path):
    voice = tmp_path / 'voice.wav'
    pcm = b''.join(struct.pack('<h', (i % 2000) - 1000) for i in range(192000))
    with wave.open(str(voice), 'wb') as w:
        w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed')); w.writeframes(pcm)
    sha = hashlib.sha256(voice.read_bytes()).hexdigest()
    first = chatcut.split_scene_audio(voice, plan()['scenes'], tmp_path / 'pack', expected_sha256=sha, fps=30)
    out = []
    for row in first:
        with wave.open(row['path'], 'rb') as w: out.append(w.readframes(w.getnframes()))
    assert b''.join(out) == pcm
    second = chatcut.split_scene_audio(voice, plan()['scenes'], tmp_path / 'pack', expected_sha256=sha, fps=30)
    assert [r['reused'] for r in second] == [True, True]
    Path(second[0]['path']).write_bytes(b'corrupt')
    third = chatcut.split_scene_audio(voice, plan()['scenes'], tmp_path / 'pack', expected_sha256=sha, fps=30)
    assert [r['reused'] for r in third] == [False, True]
    assert list((tmp_path / 'pack').glob('*.corrupt-*'))
    with pytest.raises(ValueError, match='hash'):
        chatcut.split_scene_audio(voice, plan()['scenes'], tmp_path / 'blocked', expected_sha256='0' * 64, fps=30)
    assert not (tmp_path / 'blocked').exists()


def test_transcript_batch_preserves_original_locators():
    words = [
        {'segmentIndex': 2, 'wordIndex': 7, 'text': 'Try'},
        {'segmentIndex': 2, 'wordIndex': 8, 'text': 'Sky'},
        {'segmentIndex': 3, 'wordIndex': 0, 'text': 'Tube.'},
    ]
    assert chatcut.transcript_corrections('Try Sky Tubes.', words) == 'FIX [s3]: 0 -> "Tubes."'
    assert chatcut.transcript_corrections('Try Sky Tube.', words) == ''
    with pytest.raises(ValueError): chatcut.transcript_corrections('Different count', words)
    with pytest.raises(ValueError): chatcut.transcript_corrections('Entirely unrelated speech', words)


def test_builder_does_not_change_approved_input():
    original = plan(); before = deepcopy(original)
    chatcut.build_edit_batch(original, ASSETS, TRACKS)
    assert original == before


def test_cli_writes_preview_only_and_rejects_rebinding_existing_output(tmp_path):
    import json
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    for name, value in [('plan', plan()), ('assets', ASSETS), ('tracks', TRACKS)]:
        (tmp_path / f'{name}.json').write_text(json.dumps(value))
    command = [sys.executable, str(root / 'scripts/prepare_chatcut.py'),
               '--plan', str(tmp_path / 'plan.json'), '--assets', str(tmp_path / 'assets.json'),
               '--tracks', str(tmp_path / 'tracks.json'), '--output', str(tmp_path / 'pack')]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    batch = json.loads((tmp_path / 'pack/edit-batch.preview.json').read_text())
    assert batch['validateOnly'] is True
    assert len(batch['adds']) == 5
    repeat = subprocess.run(command, capture_output=True, text=True)
    assert repeat.returncode == 0, repeat.stderr
    assert json.loads(repeat.stdout)['reused'] is True
    changed = plan(); changed['voice_gain_db'] = 2
    (tmp_path / 'plan.json').write_text(json.dumps(changed))
    failure = subprocess.run(command, capture_output=True, text=True)
    assert failure.returncode != 0 and 'new run directory' in failure.stderr
    assert json.loads((tmp_path / 'pack/edit-batch.preview.json').read_text()) == batch
