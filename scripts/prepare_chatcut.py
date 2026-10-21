#!/usr/bin/env python3
"""Prepare the existing native ChatCut route without touching the editor.

Caller supplies the reviewed shot/text plan and actual imported asset/track IDs.
This mechanical step cannot grant approval, generate speech, publish or render.
Its output is validateOnly:true and must pass native validation before commit.
Use --recordings + --pause-policy for fresh PCM checks; required for revisions.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.orchestration.local import StageStore
from workflow.platforms.files import atomic_json
from workflow.production.chatcut import (build_edit_batch, split_scene_audio, build_native_recipe,
                                         audit_narration, revision_impact, propose_narration_schedule, audit_caption_sync)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('plan', 'assets', 'tracks', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--voice', type=Path)
    parser.add_argument('--voice-sha256')
    parser.add_argument('--caption-style', type=Path)
    parser.add_argument('--export-dir', type=Path)
    parser.add_argument('--recordings', type=Path, help='Final PCM16 WAV manifest with text/file hashes and imported asset_id')
    parser.add_argument('--pause-policy', type=Path, help='Per-task gap thresholds and exact pause reviews')
    parser.add_argument('--caption-cards', type=Path, help='Current native caption cards and explicit max_offset_seconds')
    parser.add_argument('--previous-plan', type=Path, help='Previous version for sentence-level dependency invalidation')
    parser.add_argument('--picture-bounds', type=Path, help='Reviewed per-sentence action/source limits; proposal only')
    parser.add_argument('--fixed-duration-frames', type=int)
    args = parser.parse_args()
    if bool(args.voice) != bool(args.voice_sha256):
        parser.error('--voice and --voice-sha256 must be supplied together')
    if bool(args.caption_style) != bool(args.export_dir):
        parser.error('--caption-style and --export-dir must be supplied together')
    if bool(args.recordings) != bool(args.pause_policy):
        parser.error('--recordings and --pause-policy must be supplied together')
    if (args.previous_plan or args.picture_bounds or args.caption_cards) and not args.recordings:
        parser.error('revision/schedule preparation requires --recordings and --pause-policy')
    if args.fixed_duration_frames is not None and not args.picture_bounds:
        parser.error('--fixed-duration-frames requires --picture-bounds')
    if args.voice and args.recordings:
        parser.error('split --voice first; then audit final imported scene WAVs with --recordings')
    load = lambda path: json.loads(path.read_text(encoding='utf-8'))
    plan, assets, tracks = map(load, (args.plan, args.assets, args.tracks))
    output = args.output.resolve()
    recordings = load(args.recordings) if args.recordings else None
    policy = load(args.pause_policy) if args.pause_policy else None
    previous = load(args.previous_plan) if args.previous_plan else None
    bounds = load(args.picture_bounds) if args.picture_bounds else None
    captions = load(args.caption_cards) if args.caption_cards else None
    audit = impact = proposal = caption_audit = None
    try:
        if previous is not None:
            impact = revision_impact(previous, plan)
        if recordings is not None:
            # Always re-read/hash/decode before StageStore reuse. Never trust stale metrics.
            audit = audit_narration(plan, recordings, policy)
            for record in recordings:
                if not record.get('asset_id') or record['asset_id'] != assets.get(record['asset_key']):
                    audit['issues'].append({'code': 'audio_asset_binding_mismatch', 'asset_key': record['asset_key']})
            if audit['issues']: audit['status'] = 'requires_timing_review'
            if bounds is not None:
                proposal = propose_narration_schedule(plan, recordings, bounds,
                                                      fixed_duration_frames=args.fixed_duration_frames)
            if audit['status'] != 'passed_technical_timing':
                raise ValueError('narration timing blocked: '+', '.join(sorted({x['code'] for x in audit['issues']})))
            if captions is not None:
                caption_audit = audit_caption_sync(plan, captions['cards'], audit, max_offset_seconds=captions['max_offset_seconds'])
        batch = build_edit_batch(plan, assets, tracks)
        recipe = build_native_recipe(plan, assets, tracks, caption_style=load(args.caption_style),
                                     output_dir=args.export_dir) if args.caption_style else None
        if args.voice and hashlib.sha256(args.voice.read_bytes()).hexdigest() != args.voice_sha256:
            raise ValueError('narration hash mismatch')
    except ValueError as exc:
        # Keep successful previews immutable. Failed evidence gets its own content-addressed file.
        rejected = {'status': 'blocked_before_preview', 'error': str(exc), 'plan': plan,
                    'narration_audit': audit, 'revision_impact': impact, 'schedule_proposal': proposal, 'caption_input': captions,
                    'editor_mutated': False}
        sha = hashlib.sha256(json.dumps(rejected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        target = output / 'rejected' / (sha+'.json')
        if not target.exists(): atomic_json(target, rejected, replace=False)
        raise ValueError(f'{exc}; evidence: {target}') from exc
    timing_status = audit['status'] if audit else 'not_checked_recordings_and_pause_policy_required'
    if recipe is not None:
        recipe['narration_timing'] = timing_status
        recipe['gates'].append('current Chinese approval, matching imported WAVs, natural listening and final subtitle sync still required')
    inputs = {'plan': plan, 'assets': assets, 'tracks': tracks,
              'voice_path': str(args.voice.resolve()) if args.voice else None,
              'voice_sha256': args.voice_sha256, 'compiler_version': 3,
              'recordings': recordings, 'pause_policy': policy, 'previous_plan': previous,
              'picture_bounds': bounds, 'fixed_duration_frames': args.fixed_duration_frames, 'caption_cards': captions,
              'narration_audit_evidence': audit['evidence_sha256'] if audit else None}
    if recipe is not None: inputs['recipe'] = recipe
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()

    def prepare():
        audio = split_scene_audio(args.voice, plan['scenes'], output / 'audio',
                                  expected_sha256=args.voice_sha256, fps=plan['fps']) if args.voice else []
        preview = output / 'edit-batch.preview.json'
        checklist = output / 'native-caption-review.json'
        media = output / 'prepared-media.json'
        atomic_json(preview, {**batch, 'validateOnly': True})
        atomic_json(media, {'scene_audio': audio, 'import_paths': [r['path'] for r in audio],
                            'audited_recordings': recordings or []})
        atomic_json(checklist, {
            'scenes': plan['scenes'], 'only_final_narration_track_visible': True,
            'source_transcript_fix_batch_requires_preview': True,
            'exact_text_comparison_required': True,
            'final_frame': plan['duration_frames'] - 1,
            'check_final_title_exit_before_export': True,
            'creative_approval': 'not_granted_by_this_command',
            'perceptual_review': 'required_not_replaced_by_automated_checks',
            'narration_timing': timing_status,
            'caption_sync': caption_audit['status'] if caption_audit else 'not_checked_current_native_cards_required',
            'revision_invalidations': impact['invalidate'] if impact else [],
            'regenerate_or_retime_all_current_caption_cards': bool(impact and impact['invalidate']),
            'current_chinese_approval_required': bool(impact and impact['requires_current_copy_approval']),
            'schedule_proposal_not_applied': proposal is not None,
        })
        extra = []
        for filename, value in [('native-session.json', recipe), ('narration-audit.json', audit),
                                ('revision-impact.json', impact), ('narration-schedule.proposal.json', proposal),
                                ('caption-sync-audit.json', caption_audit)]:
            if value is not None:
                target = output / filename; atomic_json(target, value); extra.append(str(target))
        return {'outputs': [str(preview), str(checklist), str(media)] + extra + [r['path'] for r in audio],
                'batch_add_count': len(batch['adds']), 'audio_scene_count': len(audio),
                'narration_timing': timing_status, 'editor_mutated': False,
                'scope': 'mechanical_preparation_not_full_video_production'}

    result = StageStore(output).run('native-preparation', fingerprint, prepare)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
