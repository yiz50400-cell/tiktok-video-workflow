"""Measured narration timing and copy-change dependencies, without editor actions.

PCM activity is not transcription, listening approval, or visual understanding.
Gap limits and visual constraints belong to each reviewed creative plan.
"""
from __future__ import annotations

from array import array
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import wave

ALGORITHM = 'pcm16-channel-peak-v1'
ACTIVITY_DBFS = -60
MERGE_QUIET_SECONDS = .03
EDGE_GUARD_SECONDS = .035


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _plan(plan):
    fps = _integer(plan['fps'], 'fps', 1)
    total = _integer(plan['duration_frames'], 'duration_frames', 1)
    rows = plan['scenes']
    if not isinstance(rows, list) or not rows:
        raise ValueError('scene coverage is empty')
    keys = set(); cursor = 0
    for row in rows:
        key = row.get('asset_key')
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError('missing or duplicate scene asset_key')
        keys.add(key)
        if not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError('scene text is required')
        start = _integer(row['start_frame'], 'start_frame')
        duration = _integer(row['duration_frames'], 'duration_frames', 1)
        if start != cursor:
            raise ValueError('scene coverage has a gap or overlap')
        if row.get('source_in_us', 0) != 0 or row.get('speed', 1) != 1:
            raise ValueError('audit requires final unretimed scene WAVs starting at source zero')
        cursor += duration
    if cursor != total:
        raise ValueError('scene coverage differs from picture duration')
    return fps, total, rows


def _recordings(rows, recordings):
    if not isinstance(recordings, list):
        raise ValueError('recordings must be a list')
    by_key = {}
    for record in recordings:
        key = record.get('asset_key')
        if not isinstance(key, str) or not key or key in by_key:
            raise ValueError('missing or duplicate recording asset_key')
        if not isinstance(record.get('path'), str) or not Path(record['path']).is_absolute():
            raise ValueError('recording path must be absolute')
        for name in ('sha256', 'text_sha256'):
            if not isinstance(record.get(name), str) or not re.fullmatch('[0-9a-f]{64}', record[name]):
                raise ValueError(f'recording {name} required')
        by_key[key] = record
    if set(by_key) != {r['asset_key'] for r in rows}:
        raise ValueError('recordings must cover exactly the current scenes')
    return by_key


def _activity(data):
    """Threshold per channel; opposite-phase stereo must not cancel out."""
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            p = wav.getparams(); raw = wav.readframes(p.nframes)
    except (wave.Error, EOFError) as exc:
        raise ValueError('invalid narration WAV') from exc
    if p.comptype != 'NONE' or p.sampwidth != 2 or p.nchannels < 1 or p.framerate < 1:
        raise ValueError('requires uncompressed PCM16 WAV')
    if not p.nframes or len(raw) != p.nframes * p.nchannels * 2:
        raise ValueError('empty or truncated PCM data')
    samples = array('h'); samples.frombytes(raw)
    if sys.byteorder != 'little': samples.byteswap()
    threshold = 32768 * 10 ** (ACTIVITY_DBFS / 20)
    merge = round(MERGE_QUIET_SECONDS * p.framerate)
    spans = []; first = last = None
    for n in range(p.nframes):
        active = any(abs(samples[n*p.nchannels+c]) >= threshold for c in range(p.nchannels))
        if not active: continue
        if last is not None and n - last - 1 > merge:
            spans.append((first / p.framerate, (last+1) / p.framerate)); first = None
        if first is None: first = n
        last = n
    if first is not None:
        spans.append((first / p.framerate, (last+1) / p.framerate))
    return {'duration_seconds': p.nframes / p.framerate, 'sample_rate': p.framerate,
            'channels': p.nchannels, 'intervals': spans}


def _measure(plan, recordings):
    fps, total, rows = _plan(plan)
    by_key = _recordings(rows, recordings)
    lines = []; issues = []
    for row in rows:
        key = row['asset_key']; record = by_key[key]
        text_hash = hashlib.sha256(row['text'].encode('utf-8')).hexdigest()
        expected_voice = row.get('voice_settings', plan.get('voice_settings'))
        if expected_voice is not None and record.get('voice_settings') != expected_voice:
            issues.append({'code': 'stale_audio_voice', 'asset_key': key})
        if text_hash != record['text_sha256']:
            issues.append({'code': 'stale_audio_text', 'asset_key': key})
        try:
            data = Path(record['path']).read_bytes()
        except OSError:
            issues.append({'code': 'audio_unavailable', 'asset_key': key}); continue
        actual = hashlib.sha256(data).hexdigest()
        if actual != record['sha256']:
            issues.append({'code': 'audio_hash_mismatch', 'asset_key': key}); continue
        try:
            measurement = _activity(data)
        except ValueError as exc:
            issues.append({'code': 'invalid_audio', 'asset_key': key, 'detail': str(exc)}); continue
        line = {'asset_key': key, 'sha256': actual, 'text_sha256': text_hash,
                'start_frame': row['start_frame'], 'duration_frames': row['duration_frames'], **measurement}
        spans = line['intervals']
        if not spans:
            issues.append({'code': 'no_audible_activity', 'asset_key': key})
            line.update(audible_start_seconds=None, audible_end_seconds=None)
        else:
            onset, end = spans[0][0], spans[-1][1]
            line.update(audible_start_seconds=onset, audible_end_seconds=end)
            sample = 1 / line['sample_rate']
            if onset < sample:
                issues.append({'code': 'audio_head_cut_risk', 'asset_key': key})
            if line['duration_seconds'] - end < sample:
                issues.append({'code': 'audio_tail_cut_risk', 'asset_key': key})
        lines.append(line)
    return fps, total, rows, lines, issues


def audit_narration(plan, recordings, policy):
    """Audit final per-scene WAVs at source zero against current scene windows."""
    kinds = ('opening', 'within', 'between', 'ending')
    limits = policy.get('max_gap_seconds', {})
    if not isinstance(limits, dict) or set(limits) != set(kinds):
        raise ValueError('explicit opening/within/between/ending gap limits required')
    for value in limits.values():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('gap limits must be finite non-negative numbers')
    exemptions = policy.get('allowed_pauses', [])
    if not isinstance(exemptions, list): raise ValueError('allowed_pauses must be a list')
    allowed = {}
    for item in exemptions:
        if (not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                for k in ('gap_id', 'reason', 'reviewed_by')) or item['gap_id'] in allowed):
            raise ValueError('each pause review needs a unique gap_id, reason and reviewed_by')
        allowed[item['gap_id']] = item
    fps, total, rows, lines, issues = _measure(plan, recordings)
    evidence = _hash({'plan': plan, 'recordings': recordings, 'algorithm': ALGORITHM,
                      'limits': limits, 'activity_dbfs': ACTIVITY_DBFS})
    intervals = []
    for line in lines:
        start = line['start_frame'] / fps
        window = line['duration_frames'] / fps
        end = line['audible_end_seconds']
        if end is not None:
            if end > window + 1e-9:
                issues.append({'code': 'speech_overrun', 'asset_key': line['asset_key'],
                               'overrun_seconds': end-window})
            elif window - end < 1 / line['sample_rate']:
                issues.append({'code': 'picture_end_cut_risk', 'asset_key': line['asset_key']})
        intervals.extend((start+a, start+b, line['asset_key']) for a,b in line['intervals'])
    intervals.sort()
    gaps = []
    def gap(kind, start, end, keys):
        length = end-start
        if length < -1e-9: return
        payload = {'kind': kind, 'start_seconds': start, 'end_seconds': end,
                   'seconds': max(0, length), 'asset_keys': keys}
        gid = _hash({'evidence': evidence, 'gap': payload})
        exceeds = length > limits[kind] + 1e-9
        approved = allowed.get(gid)
        payload.update(id=gid, threshold_seconds=limits[kind], exceeds_limit=exceeds,
                       requires_review=bool(exceeds and not approved), review=approved)
        gaps.append(payload)
        if payload['requires_review']:
            issues.append({'code': 'unreviewed_pause', 'gap_id': gid, 'kind': kind, 'seconds': length})
    if intervals:
        gap('opening', 0, intervals[0][0], [intervals[0][2]])
        for left, right in zip(intervals, intervals[1:]):
            gap('within' if left[2] == right[2] else 'between', left[1], right[0],
                [left[2]] if left[2] == right[2] else [left[2], right[2]])
        gap('ending', intervals[-1][1], total/fps, [intervals[-1][2]])
    current_ids = {g['id'] for g in gaps}
    for gid in allowed.keys() - current_ids:
        issues.append({'code': 'stale_pause_review', 'gap_id': gid})
    return {'schema_version': 1, 'algorithm': ALGORITHM, 'evidence_sha256': evidence,
            'plan_sha256': _hash(plan), 'status': 'requires_timing_review' if issues else 'passed_technical_timing',
            'auditory_approval': False, 'editor_mutated': False, 'lines': lines, 'gaps': gaps, 'issues': issues,
            'scope': 'PCM activity and manifest text binding, not ASR, listening or visual semantics',
            'required_reviews': ['actual spoken text', 'natural delivery', 'complete visual actions',
                                 'caption text and timing', 'final export']}


def revision_impact(previous_plan, current_plan):
    """Compute dependencies using stable sentence keys; no files are deleted."""
    _plan(previous_plan); _plan(current_plan)
    old = {s['asset_key']: s for s in previous_plan['scenes']}
    new = {s['asset_key']: s for s in current_plan['scenes']}
    inserted = [k for k in new if k not in old]
    deleted = [k for k in old if k not in new]
    rewritten = [k for k in new if k in old and new[k]['text'] != old[k]['text']]
    voice_changed = previous_plan.get('voice_settings') != current_plan.get('voice_settings')
    regenerate = [k for k in new if k not in old or k in rewritten or voice_changed
                  or new[k].get('voice_settings') != old[k].get('voice_settings')]
    retime = [k for k in new if k in old and (previous_plan['fps'] != current_plan['fps']
              or any(new[k][f] != old[k][f] for f in ('start_frame','duration_frames')))]
    changed = previous_plan != current_plan
    return {'schema_version': 1, 'previous_plan_sha256': _hash(previous_plan),
            'current_plan_sha256': _hash(current_plan), 'deleted': deleted, 'inserted': inserted,
            'rewritten': rewritten, 'regenerate_audio': regenerate,
            'reuse_audio': [k for k in new if k not in regenerate], 'retime': retime,
            'invalidate': ['caption_timing','picture_sync_review','export'] if changed else [],
            'requires_current_copy_approval': bool(inserted or deleted or rewritten),
            'requires_current_plan_review': changed,
            'reuse_condition': 'exact audio hash, current text hash and voice settings still verified',
            'editor_mutated': False}


def propose_narration_schedule(plan, recordings, bounds, fixed_duration_frames=None):
    """Propose speech-led windows inside reviewed visual limits, never apply them.

    Only outer silence trimming is proposed with an acoustic guard. Internal
    pauses, voice speed, words and source footage remain unchanged. Longer fixed
    duration is a conflict too: callers must explicitly review visual holds.
    """
    fps, _, rows, lines, issues = _measure(plan, recordings)
    if issues: raise ValueError('recording evidence requires review: '+', '.join(i['code'] for i in issues))
    if not isinstance(bounds, dict) or set(bounds) != {r['asset_key'] for r in rows}:
        raise ValueError('picture bounds must cover exactly every scene')
    scenes = []; trims = []; cursor = 0
    by_key = {line['asset_key']: line for line in lines}
    for row in rows:
        key = row['asset_key']; limit = bounds[key]; line = by_key[key]
        minimum = _integer(limit['min_picture_frames'], 'minimum picture frames', 1)
        maximum = _integer(limit['max_picture_frames'], 'maximum picture frames', 1)
        lead = _integer(limit['lead_frames'], 'lead_frames')
        tail = _integer(limit['tail_frames'], 'tail_frames')
        a = max(0, line['audible_start_seconds']-EDGE_GUARD_SECONDS)
        b = min(line['duration_seconds'], line['audible_end_seconds']+EDGE_GUARD_SECONDS)
        needed = math.ceil((b-a)*fps-1e-9)
        duration = max(minimum, needed+lead+tail)
        if minimum > maximum or duration > maximum:
            raise ValueError(f'{key}: complete narration cannot fit reviewed picture bounds')
        scenes.append({**deepcopy(row), 'start_frame': cursor, 'duration_frames': duration})
        trims.append({'asset_key': key, 'original_sha256': line['sha256'],
                      'source_start_seconds': a, 'source_end_seconds': b,
                      'timeline_start_frame': cursor+lead, 'audio_duration_frames': needed,
                      'speed_change': False, 'outer_silence_only': True})
        cursor += duration
    if fixed_duration_frames is not None:
        fixed = _integer(fixed_duration_frames, 'fixed duration', 1)
        if fixed != cursor:
            raise ValueError(f'fixed duration conflict: requested {fixed} frames; natural constrained proposal {cursor}; review copy/actions or duration, do not pad silence')
    return {'schema_version': 1, 'status': 'requires_visual_plan_review', 'editor_mutated': False,
            'duration_frames': cursor, 'scenes': scenes, 'audio_trim_proposals': trims,
            'source_plan_sha256': _hash(plan), 'requires_new_prepared_audio_and_audit': True,
            'required_reviews': ['Chinese approval for material changes', 'complete matching visual actions',
                                 'internal pauses', 'new subtitles', 'real listening and export']}


def audit_caption_sync(plan, cards, narration_audit, *, max_offset_seconds):
    """Check normalized native caption cards against current copy and PCM timing.

    This checks exported/queried card data, not rendering/readability. It does not
    generate external subtitles or replace the native caption editing route.
    """
    import unicodedata
    fps, total, rows = _plan(plan)
    tolerance = max_offset_seconds
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('caption timing tolerance must be finite and non-negative')
    if (narration_audit.get('plan_sha256') != _hash(plan)
            or narration_audit.get('status') != 'passed_technical_timing'):
        raise ValueError('caption check requires passed current-plan narration audit')
    if not isinstance(cards, list) or not cards:
        raise ValueError('caption cards are required')
    scenes = {s['asset_key']: s for s in rows}
    lines = {s['asset_key']: s for s in narration_audit['lines']}
    grouped = {key: [] for key in scenes}
    last_end = 0
    for card in cards:
        key = card.get('asset_key'); a = card.get('start_seconds'); b = card.get('end_seconds')
        if key not in scenes or not isinstance(card.get('text'), str) or not card['text'].strip():
            raise ValueError('caption contains deleted/unknown sentence or empty text')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (a,b)):
            raise ValueError('caption timings must be finite')
        scene = scenes[key]; start = scene['start_frame']/fps; end = start+scene['duration_frames']/fps
        if a < last_end-1e-9 or a < start-1e-9 or b <= a or b > end+1e-9 or b > total/fps+1e-9:
            raise ValueError('caption order/scene bounds mismatch')
        last_end = b; grouped[key].append(card)
    normalize = lambda text: re.sub(r'\s+', '', unicodedata.normalize('NFC', text))
    for key, scene in scenes.items():
        group = grouped[key]; line = lines.get(key)
        if not group or normalize(''.join(c['text'] for c in group)) != normalize(scene['text']):
            raise ValueError(f'{key}: caption text does not completely match current copy')
        if line is None or not line['intervals']:
            raise ValueError(f'{key}: missing current audio activity')
        start = scene['start_frame']/fps
        if (abs(group[0]['start_seconds']-(start+line['audible_start_seconds'])) > tolerance+1e-9
                or abs(group[-1]['end_seconds']-(start+line['audible_end_seconds'])) > tolerance+1e-9):
            raise ValueError(f'{key}: caption onset/tail differs from current recording')
        for left, right in line['intervals']:
            cursor = left+start; finish = right+start
            for card in group:
                if card['end_seconds'] < cursor: continue
                if card['start_seconds'] > cursor+tolerance+1e-9: break
                cursor = max(cursor, card['end_seconds'])
                if cursor >= finish-tolerance: break
            if cursor < finish-tolerance-1e-9:
                raise ValueError(f'{key}: audible activity lacks caption coverage')
    return {'schema_version': 1, 'status': 'passed_technical_caption_sync',
            'plan_sha256': _hash(plan), 'caption_sha256': _hash(cards),
            'narration_evidence_sha256': narration_audit['evidence_sha256'],
            'sentence_count': len(rows), 'card_count': len(cards), 'max_offset_seconds': tolerance,
            'perceptual_approval': False, 'editor_mutated': False,
            'scope': 'caption text/scene/PCM activity alignment; readability and word-level listening still require review'}
