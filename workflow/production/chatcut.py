"""Deterministic preparation for approved edits, not a second editing engine.

No network, editor mutation, approval, voice change or generation occurs here.
The caller retains the existing creative/approval/media-quality gates.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import unicodedata
import wave

from .narration import audit_narration, revision_impact, propose_narration_schedule, audit_caption_sync


def _int(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label} must be an integer >= {minimum}')
    return value


def _cover(rows, total):
    cursor = 0
    if not rows:
        raise ValueError('empty timeline rows')
    for row in rows:
        start = _int(row['start_frame'], 'start_frame')
        length = _int(row['duration_frames'], 'duration_frames', 1)
        if start != cursor:
            raise ValueError('timeline has a gap or overlap')
        cursor += length
    if cursor != total:
        raise ValueError('timeline rows do not cover the complete duration')


def build_edit_batch(plan, assets, tracks):
    """Compile all planned media into ONE atomic native edit_item add batch."""
    fps = _int(plan['fps'], 'fps', 1)
    total = _int(plan['duration_frames'], 'duration_frames', 1)
    _cover(plan['shots'], total)
    _cover(plan['scenes'], total)
    if 'voice_measurements' in plan:
        check_speech_fit(plan, plan['voice_measurements'])
    sources = {s['key']: s for s in plan['sources']}
    if len(sources) != len(plan['sources']):
        raise ValueError('duplicate source keys')
    hashes = set()
    for shot in plan['shots']:
        source = sources.get(shot['asset_key'])
        if not source or not re.fullmatch(r'[0-9a-f]{64}', source['sha256']):
            raise ValueError('source hash or identity missing')
        offset = _int(shot['source_in_us'], 'source_in_us')
        duration = source['duration_seconds']
        if (type(duration) not in (int, float) or not math.isfinite(duration)
                or offset / 1e6 + shot['duration_frames'] / fps > duration + 1e-6):
            raise ValueError('shot exceeds source duration')
        hashes.add(source['sha256'])
    if len(hashes) < _int(plan.get('min_sources', 1), 'min_sources', 1):
        raise ValueError('not enough independent source hashes')
    gain = plan.get('voice_gain_db', 0)
    if type(gain) not in (int, float) or not math.isfinite(gain):
        raise ValueError('voice gain must be finite')
    adds = []

    def item(row, kind, track):
        asset = assets.get(row['asset_key'])
        target = tracks.get(track)
        if not isinstance(asset, str) or not asset or not isinstance(target, str) or not target:
            raise ValueError(f'missing asset or track for {row["asset_key"]}')
        return {'type': kind, 'assetId': asset, 'trackId': target,
                'startFrame': row['start_frame'], 'durationFrames': row['duration_frames']}

    for row in plan['shots']:
        adds.append({**item(row, 'video', 'picture'), 'sourceIn': row['source_in_us'],
                     'muted': True, 'fit': 'cover'})
    for row in plan['scenes']:
        if not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError('scene narration text missing')
        adds.append({**item(row, 'audio', 'voice'), 'sourceIn': 0, 'decibelAdjustment': gain})
    tail = _int(plan.get('title_tail_frames', 0), 'title_tail_frames')
    fade = _int(plan.get('title_fade_frames', 0), 'title_fade_frames')
    for row in plan.get('titles', []):
        start = _int(row['start_frame'], 'title start')
        length = _int(row['duration_frames'], 'title duration', 1)
        if start + length > total:
            raise ValueError('title exceeds timeline')
        end = min(start + length, total - tail)
        if end - start <= max(1, fade):
            raise ValueError('title too short after reserved exit')
        layout = row.get('layout', {})
        if any(k not in ('left', 'top', 'width', 'height', 'opacity', 'rotation') for k in layout):
            raise ValueError('unsupported title layout field')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in layout.values()):
            raise ValueError('title layout must be finite')
        adds.append({**item(row, 'motion-graphic', 'titles'), **deepcopy(layout),
                     'durationFrames': end - start, 'fadeOutDurationFrames': fade,
                     'propertyOverrides': deepcopy(row.get('properties', {}))})
    return {'adds': adds}


def check_speech_fit(plan, measurements):
    """Reject measured overruns before editor import; never accelerate speech."""
    if len(measurements) != len(plan['scenes']):
        raise ValueError('speech measurements must cover every scene')
    result = []
    for scene, row in zip(plan['scenes'], measurements):
        key = scene['asset_key']
        values = [row.get(k) for k in ('speech_seconds', 'lead_seconds', 'tail_seconds')]
        if (row.get('asset_key') != key or any(type(v) not in (int, float)
                or not math.isfinite(v) or v < 0 for v in values) or values[0] <= 0):
            raise ValueError(f'invalid measured speech for {key}')
        spare = scene['duration_frames'] / plan['fps'] - sum(values)
        if spare < -1e-6:
            raise ValueError(f'{key} speech exceeds its approved window by {-spare:.3f}s')
        result.append({'asset_key': key, 'spare_seconds': max(0, spare), 'speed_change': False})
    return result


def build_native_recipe(plan, assets, tracks, *, caption_style, output_dir):
    """Emit proven native payloads, never editor actions or creative approvals."""
    batch = build_edit_batch(plan, assets, tracks)
    if plan['fps'] not in (24, 25, 30, 50, 60):
        raise ValueError('unsupported native export frame rate')
    if not isinstance(caption_style, dict) or not caption_style:
        raise ValueError('supply the reviewed caption style')
    output = Path(output_dir).resolve()
    return {
        'schema_version': 1,
        'scope': 'mechanical_payloads_not_creative_or_perceptual_approval',
        'speech_fit': (check_speech_fit(plan, plan['voice_measurements'])
                       if 'voice_measurements' in plan else 'not_checked_measurements_required'),
        'track_create': {key: {'action': 'create', 'json': {
            'trackType': kind, 'count': 1, 'name': label}} for key, kind, label in
            [('picture', 'video', '商品画面'), ('voice', 'audio', '最终旁白'),
             ('titles', 'video', '重点标题')]},
        'track_creation_rule': 'create only missing tracks; bind returned IDs before compilation',
        'edit_preview': {**batch, 'validateOnly': True},
        'captions_enable': {'action': 'enable', 'preset': 'plain'},
        'captions': {'action': 'batch', 'edits': [
            {'action': 'track', 'trackId': tracks['picture'], 'visible': False},
            {'action': 'track', 'trackId': tracks['voice'], 'visible': True},
            {'action': 'style', 'style': deepcopy(caption_style)}]},
        'export_video': {'format': 'video', 'codec': 'h264', 'fps': plan['fps'],
                         'resolution': '1080p', 'outputPath': str(output / 'video.mp4')},
        'export_subtitles': {'format': 'subtitles', 'subtitleFormat': 'srt',
                             'outputPath': str(output / 'video.srt')},
        'gates': ['bind actual active timelineId to both exports',
                  'inspect and fix source transcription before caption styling',
                  'validate edit_preview before removing validateOnly and committing',
                  'check speech fits, source internal cuts and final frame before export',
                  'full decode, exact subtitles and perceptual review remain required'],
    }


def split_scene_audio(audio, scenes, output, *, expected_sha256, fps):
    """Split final PCM once at scene boundaries; preserve every sample exactly.

    Content-addressed outputs are verified before reuse. Corrupt copies are
    kept aside rather than silently overwritten. No resampling or retiming.
    """
    fps = _int(fps, 'fps', 1)
    data = Path(audio).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('narration hash mismatch')
    with wave.open(io.BytesIO(data), 'rb') as w:
        params = w.getparams()
        pcm = w.readframes(w.getnframes())
    if params.comptype != 'NONE' or params.sampwidth != 2:
        raise ValueError('requires uncompressed16-bit PCM narration')
    total_frames = round(params.nframes / params.framerate * fps)
    if abs(total_frames / fps - params.nframes / params.framerate) > 1 / params.framerate:
        raise ValueError('narration duration is not frame aligned')
    _cover(scenes, total_frames)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stride = params.nchannels * params.sampwidth
    result = []
    for i, scene in enumerate(scenes, 1):
        a = round(scene['start_frame'] / fps * params.framerate)
        b = round((scene['start_frame'] + scene['duration_frames']) / fps * params.framerate)
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as w:
            w.setparams(params); w.writeframes(pcm[a * stride:b * stride])
        payload = stream.getvalue(); sha = hashlib.sha256(payload).hexdigest()
        path = output / f'voice-scene-{i:02d}-{sha[:12]}.wav'
        reused = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha
        if not reused:
            if path.exists():
                path.rename(path.with_name(path.name + f'.corrupt-{time.time_ns()}'))
            tmp = path.with_suffix('.wav.partial'); tmp.write_bytes(payload); tmp.replace(path)
        result.append({'asset_key': scene['asset_key'], 'path': str(path), 'sha256': sha,
                       'start_frame': scene['start_frame'], 'duration_frames': scene['duration_frames'],
                       'sample_count': b - a, 'reused': reused})
    return result


def transcript_corrections(text, words):
    """Return one native FIX batch, never guess locators or unmatched words.

    Word-count or major text mismatch stays a manual review item, not an
    automatically forced transcript. Native preview:true is still mandatory.
    """
    expected = unicodedata.normalize('NFC', text).split()
    if not expected or len(expected) != len(words):
        raise ValueError('word count mismatch: inspect complete native transcript')
    normalize = lambda s: re.sub(r'[^\w]+', '', unicodedata.normalize('NFC', s).casefold())
    equal = sum(normalize(a) == normalize(b['text']) for a, b in zip(expected, words))
    if equal / len(expected) < .5:
        raise ValueError('transcript differs substantially: verify voice asset and language')
    fixes = []; locators = set()
    for token, word in zip(expected, words):
        segment = _int(word['segmentIndex'], 'segmentIndex', 1)
        index = _int(word['wordIndex'], 'wordIndex')
        if (segment, index) in locators:
            raise ValueError('duplicate native word locator')
        locators.add((segment, index))
        if token != word['text']:
            fixes.append(f'FIX [s{segment}]: {index} -> {json.dumps(token, ensure_ascii=False)}')
    return '\n'.join(fixes)


def boundary_caption_cards(text, events, *, duration_seconds, max_chars=36):
    """Fallback for verified native-ASR failure, using exact TTS word timings.

    Only space-delimited reviewed text that matches every WordBoundary token
    is accepted. This supplies timing, not listening or localization approval.
    Returned times are seconds relative to the unretimed source audio.
    """
    if (type(duration_seconds) not in (int, float)
            or not math.isfinite(duration_seconds) or duration_seconds <= 0):
        raise ValueError('invalid audio duration')
    _int(max_chars, 'max_chars', 1)
    expected = text.split()
    words = [e for e in events if e.get('type') == 'WordBoundary']
    norm = lambda s: re.sub(r'[^\w]+', '', unicodedata.normalize('NFC', s).casefold())
    if not expected or len(expected) != len(words):
        raise ValueError('word boundaries do not cover reviewed text')
    timed = []; previous_end = 0
    for token, event in zip(expected, words):
        offset, duration = event.get('offset'), event.get('duration')
        if (not norm(token) or norm(token) != norm(event.get('text', ''))
                or len(token) > max_chars
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in (offset, duration)) or offset < 0 or duration <= 0):
            raise ValueError('invalid word or word-boundary timing')
        start, end = offset / 1e7, (offset + duration) / 1e7
        if start < previous_end - 1e-6 or end > duration_seconds + 1e-6:
            raise ValueError('overlapping words or boundary exceeds audio')
        timed.append((token, start, end)); previous_end = end
    cards = []; lines = []; start = end = 0
    def flush():
        if lines:
            cards.append({'text': '\n'.join(lines), 'start': start, 'end': end})
    for token, a, b in timed:
        if not lines:
            lines = [token]; start = a
        elif len(lines[-1]) + 1 + len(token) <= max_chars:
            lines[-1] += ' ' + token
        elif len(lines) < 2:
            lines.append(token)
        else:
            flush(); lines = [token]; start = a
        end = b
        if token.endswith(('.', '!', '?', '。', '！', '？')):
            flush(); lines = []
    flush()
    for i, card in enumerate(cards):
        following = cards[i+1]['start'] if i+1 < len(cards) else duration_seconds
        card['end'] = min(card['end'] + .12, following, duration_seconds)
    return cards
