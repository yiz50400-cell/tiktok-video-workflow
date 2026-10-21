"""Real PCM fixtures catch stale copy, clipping, silent padding and revision drift."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest
from workflow.production import chatcut


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def pcm(tmp_path, key, text, duration=2, active=((.1, 1.8),), channels=1):
    path = tmp_path / (key + '.wav')
    data = bytearray()
    for n in range(round(duration * 8000)):
        value = 5000 if any(a <= n / 8000 < b for a, b in active) else 0
        data.extend(struct.pack('<' + 'h' * channels, *[value * (-1 if c % 2 else 1) for c in range(channels)]))
    with wave.open(str(path), 'wb') as w:
        w.setparams((channels, 2, 8000, 0, 'NONE', 'not compressed'))
        w.writeframes(data)
    return {'asset_key': key, 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'text_sha256': digest(text)}


def setup(tmp_path):
    p = {'fps': 30, 'duration_frames': 120, 'scenes': [
        {'asset_key': 'a', 'text': 'First.', 'start_frame': 0, 'duration_frames': 60},
        {'asset_key': 'b', 'text': 'Second.', 'start_frame': 60, 'duration_frames': 60}]}
    records = [pcm(tmp_path, 'a', 'First.'), pcm(tmp_path, 'b', 'Second.')]
    policy = {'max_gap_seconds': {'opening': .5, 'within': .8, 'between': .8, 'ending': .5},
              'allowed_pauses': []}
    return p, records, policy


def codes(report):
    return [x['code'] for x in report['issues']]


def test_normal_pcm_gaps_pass_without_granting_listening_approval(tmp_path):
    p, records, policy = setup(tmp_path)
    r = chatcut.audit_narration(p, records, policy)
    assert r['status'] == 'passed_technical_timing'
    assert r['auditory_approval'] is False
    assert r['gaps'][1]['seconds'] == pytest.approx(.3)
    assert r['lines'][0]['audible_end_seconds'] == pytest.approx(1.8)


@pytest.mark.parametrize('kind', ['within', 'between', 'opening', 'ending'])
def test_long_pause_is_not_hidden_by_matching_total_duration(tmp_path, kind):
    p, records, policy = setup(tmp_path)
    if kind == 'within': records[0] = pcm(tmp_path, 'a', 'First.', active=((.1,.3),(1.5,1.8)))
    if kind == 'between': records[0] = pcm(tmp_path, 'a', 'First.', active=((.1,.5),))
    if kind == 'opening': records[0] = pcm(tmp_path, 'a', 'First.', active=((1,1.8),))
    if kind == 'ending': records[1] = pcm(tmp_path, 'b', 'Second.', active=((.1,.7),))
    r = chatcut.audit_narration(p, records, policy)
    assert 'unreviewed_pause' in codes(r)
    assert any(g['kind'] == kind and g['requires_review'] for g in r['gaps'])


def test_changed_text_cannot_reuse_old_audio_binding(tmp_path):
    p, records, policy = setup(tmp_path); p['scenes'][1]['text'] = 'Customer revised this.'
    assert 'stale_audio_text' in codes(chatcut.audit_narration(p, records, policy))


def test_hash_changed_audio_cannot_reuse_old_measurement(tmp_path):
    p, records, policy = setup(tmp_path); Path(records[0]['path']).write_bytes(b'changed')
    assert 'audio_hash_mismatch' in codes(chatcut.audit_narration(p, records, policy))


def test_speech_must_not_be_cut_or_continue_beyond_picture(tmp_path):
    p, records, policy = setup(tmp_path)
    records[1] = pcm(tmp_path, 'b', 'Second.', duration=3, active=((.1,2.7),))
    assert 'speech_overrun' in codes(chatcut.audit_narration(p, records, policy))


def test_audio_ending_on_active_samples_is_flagged(tmp_path):
    p, records, policy = setup(tmp_path)
    records[1] = pcm(tmp_path, 'b', 'Second.', active=((.1,2),))
    assert 'audio_tail_cut_risk' in codes(chatcut.audit_narration(p, records, policy))


def test_silence_is_not_accepted_as_narration(tmp_path):
    p, records, policy = setup(tmp_path); records[0] = pcm(tmp_path, 'a', 'First.', active=())
    assert 'no_audible_activity' in codes(chatcut.audit_narration(p, records, policy))


def test_stereo_opposite_phase_is_not_downmixed_to_silence(tmp_path):
    p, records, policy = setup(tmp_path); records[0] = pcm(tmp_path, 'a', 'First.', channels=2)
    assert chatcut.audit_narration(p, records, policy)['status'] == 'passed_technical_timing'


def test_intentional_pause_is_bound_to_exact_current_gap(tmp_path):
    p, records, policy = setup(tmp_path)
    records[0] = pcm(tmp_path, 'a', 'First.', active=((.1,.5),))
    first = chatcut.audit_narration(p, records, policy)
    gap = next(g for g in first['gaps'] if g['requires_review'])
    policy['allowed_pauses'] = [{'gap_id': gap['id'], 'reason': 'Show the complete brush movement.', 'reviewed_by': 'editor'}]
    assert chatcut.audit_narration(p, records, policy)['status'] == 'passed_technical_timing'
    changed = deepcopy(p); changed['scenes'][0]['text'] = 'Reworded.'
    records[0]['text_sha256'] = digest('Reworded.')
    result = chatcut.audit_narration(changed, records, policy)
    assert 'unreviewed_pause' in codes(result)
    assert 'stale_pause_review' in codes(result)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'negative-policy', 'boolean-policy', 'nan-policy', 'gap', 'overlap'])
def test_malformed_or_incomplete_evidence_fails_closed(tmp_path, fault):
    p, records, policy = setup(tmp_path)
    if fault == 'missing': records.pop()
    if fault == 'duplicate': records.append(records[0])
    if fault == 'negative-policy': policy['max_gap_seconds']['between'] = -1
    if fault == 'boolean-policy': policy['max_gap_seconds']['between'] = True
    if fault == 'nan-policy': policy['max_gap_seconds']['between'] = float('nan')
    if fault == 'gap': p['scenes'][1]['start_frame'] = 70
    if fault == 'overlap': p['scenes'][1]['start_frame'] = 50
    with pytest.raises(ValueError): chatcut.audit_narration(p, records, policy)


def test_delete_sentence_reuses_remaining_audio_but_invalidates_placements(tmp_path):
    p, _, _ = setup(tmp_path); new = deepcopy(p)
    new['scenes'] = [dict(p['scenes'][1], start_frame=0)]; new['duration_frames'] = 60
    r = chatcut.revision_impact(p, new)
    assert r['deleted'] == ['a']; assert r['reuse_audio'] == ['b']
    assert r['retime'] == ['b']; assert r['regenerate_audio'] == []
    assert set(r['invalidate']) == {'caption_timing','picture_sync_review','export'}


def test_insert_and_rewrite_only_regenerate_affected_sentences(tmp_path):
    p, _, _ = setup(tmp_path); new = deepcopy(p)
    new['scenes'][0]['text'] = 'Revised.'
    new['scenes'].insert(1, {'asset_key':'c','text':'Inserted.','start_frame':60,'duration_frames':30})
    new['scenes'][2]['start_frame'] = 90;new['duration_frames'] = 150
    r = chatcut.revision_impact(p,new)
    assert r['regenerate_audio'] == ['a','c']; assert r['reuse_audio'] == ['b']
    assert r['inserted'] == ['c']; assert r['rewritten'] == ['a']; assert r['requires_current_copy_approval'] is True


def test_whole_script_and_voice_parameter_change_invalidate_audio(tmp_path):
    p, _, _ = setup(tmp_path); new=deepcopy(p)
    for s in new['scenes']: s['text'] += ' New wording.'
    assert chatcut.revision_impact(p,new)['regenerate_audio'] == ['a','b']
    new=deepcopy(p);new['voice_settings']={'voice':'another','rate':'+0%'}
    assert chatcut.revision_impact(p,new)['regenerate_audio'] == ['a','b']


def test_same_plan_reuses_audio_and_does_not_invalidate_export(tmp_path):
    p, _, _ = setup(tmp_path); before=deepcopy(p)
    r=chatcut.revision_impact(p,p)
    assert r['invalidate']==[] and r['regenerate_audio']==[] and r['retime']==[]
    assert p==before


def test_schedule_proposal_compacts_windows_without_changing_customer_text(tmp_path):
    p,records,_=setup(tmp_path)
    records=[pcm(tmp_path,s['asset_key'],s['text'],active=((.1,.9),)) for s in p['scenes']]
    bounds={k:{'min_picture_frames':30,'max_picture_frames':90,'lead_frames':0,'tail_frames':0} for k in ['a','b']}
    r=chatcut.propose_narration_schedule(p,records,bounds)
    assert r['duration_frames']==60
    assert [(s['start_frame'],s['duration_frames'],s['text']) for s in r['scenes']]==[(0,30,'First.'),(30,30,'Second.')]
    assert r['status']=='requires_visual_plan_review' and r['editor_mutated'] is False
    assert p['duration_frames']==120


def test_schedule_preserves_action_minimum_and_rejects_unavailable_picture(tmp_path):
    p,records,_=setup(tmp_path)
    bounds={k:{'min_picture_frames':90,'max_picture_frames':120,'lead_frames':0,'tail_frames':0} for k in ['a','b']}
    assert chatcut.propose_narration_schedule(p,records,bounds)['duration_frames']==180
    bounds['a']['max_picture_frames']=30;bounds['a']['min_picture_frames']=30
    with pytest.raises(ValueError,match='picture'):chatcut.propose_narration_schedule(p,records,bounds)


def test_fixed_total_conflict_never_silently_stretches_or_truncates(tmp_path):
    p,records,_=setup(tmp_path)
    bounds={k:{'min_picture_frames':30,'max_picture_frames':90,'lead_frames':0,'tail_frames':0} for k in ['a','b']}
    with pytest.raises(ValueError,match='fixed duration'):chatcut.propose_narration_schedule(p,records,bounds,fixed_duration_frames=30)


def cli_fixture(tmp_path):
    p, records, policy = setup(tmp_path)
    p.update(sources=[{'key':'source','sha256':'a'*64,'duration_seconds':10}],
             shots=[{'asset_key':'source','start_frame':0,'duration_frames':120,'source_in_us':0}])
    assets={'source':'video-id','a':'audio-a','b':'audio-b'}
    for r in records:r['asset_id']=assets[r['asset_key']]
    paths={}
    for key,value in [('plan',p),('recordings',records),('pause-policy',policy),
                      ('assets',assets),('tracks',{'picture':'V1','voice':'A1'}),('previous-plan',deepcopy(p))]:
        paths[key]=tmp_path/(key+'.json');paths[key].write_text(json.dumps(value))
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/prepare_chatcut.py')]
    for key in ['plan','assets','tracks','recordings','pause-policy']:command += ['--'+key,str(paths[key])]
    command += ['--output',str(tmp_path/'pack')]
    return p,records,policy,paths,command


def run_cli(command):
    return subprocess.run(command,capture_output=True,text=True)


def test_direct_builder_cannot_skip_supplied_speech_fit(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    p['voice_measurements']=[{'asset_key':s['asset_key'],'speech_seconds':3,'lead_seconds':0,'tail_seconds':0} for s in p['scenes']]
    with pytest.raises(ValueError,match='exceeds'):
        chatcut.build_edit_batch(p,json.loads(paths['assets'].read_text()),json.loads(paths['tracks'].read_text()))


def test_current_voice_configuration_must_match_recording_evidence(tmp_path):
    p,records,policy=setup(tmp_path);p['voice_settings']={'voice':'new-voice','rate':'+0%'}
    assert 'stale_audio_voice' in codes(chatcut.audit_narration(p,records,policy))
    for r in records:r['voice_settings']=deepcopy(p['voice_settings'])
    assert chatcut.audit_narration(p,records,policy)['status']=='passed_technical_timing'


def test_revised_cli_writes_current_audit_and_reuses_same_safe_run(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    prior=deepcopy(p);prior['scenes'][0]['text']='Old sentence.'
    paths['previous-plan'].write_text(json.dumps(prior));cmd += ['--previous-plan',str(paths['previous-plan'])]
    first=run_cli(cmd);assert first.returncode==0,first.stderr
    report=json.loads((tmp_path/'pack/narration-audit.json').read_text())
    change=json.loads((tmp_path/'pack/revision-impact.json').read_text())
    assert report['status']=='passed_technical_timing' and report['auditory_approval'] is False
    assert change['regenerate_audio']==['a'] and change['reuse_audio']==['b']
    assert json.loads((tmp_path/'pack/edit-batch.preview.json').read_text())['validateOnly'] is True
    assert json.loads(run_cli(cmd).stdout)['reused'] is True


@pytest.mark.parametrize('fault',['stale-text','wrong-file','pause','overrun','wrong-asset','missing-asset-binding'])
def test_cli_blocks_bad_current_evidence_before_preview(tmp_path,fault):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    if fault=='stale-text':p['scenes'][0]['text']='New sentence.'
    if fault=='wrong-file':Path(records[0]['path']).write_bytes(b'broken')
    if fault=='pause':
        old=records[0];records[0]=pcm(tmp_path,'a','First.',active=((.1,.5),));records[0]['asset_id']=old['asset_id']
    if fault=='overrun':
        old=records[1];records[1]=pcm(tmp_path,'b','Second.',duration=3,active=((.1,2.6),));records[1]['asset_id']=old['asset_id']
    if fault=='wrong-asset':records[0]['asset_id']='old-import'
    if fault=='missing-asset-binding':records[0].pop('asset_id')
    paths['plan'].write_text(json.dumps(p));paths['recordings'].write_text(json.dumps(records))
    r=run_cli(cmd);assert r.returncode!=0
    assert not (tmp_path/'pack/edit-batch.preview.json').exists()
    assert list((tmp_path/'pack/rejected').glob('*.json'))


def test_bad_new_input_cannot_overwrite_previous_successful_preview(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    first=run_cli(cmd);assert first.returncode==0,first.stderr
    before={x.name:x.read_bytes() for x in (tmp_path/'pack').glob('*.json')}
    p['scenes'][0]['text']='Changed';paths['plan'].write_text(json.dumps(p))
    assert run_cli(cmd).returncode!=0
    assert before=={x.name:x.read_bytes() for x in (tmp_path/'pack').glob('*.json')}


def test_changed_physical_audio_is_checked_before_cached_success(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    first=run_cli(cmd);assert first.returncode==0,first.stderr
    Path(records[0]['path']).write_bytes(b'tampered')
    assert run_cli(cmd).returncode!=0


def test_revision_flag_requires_current_audio_and_policy(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    for flag in ['--recordings','--pause-policy']:
        i=cmd.index(flag);del cmd[i:i+2]
    cmd += ['--previous-plan',str(paths['previous-plan'])]
    r=run_cli(cmd);assert r.returncode!=0 and 'requires --recordings and --pause-policy' in r.stderr


def test_legacy_cli_does_not_claim_new_audit_passed(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    for flag in ['--recordings','--pause-policy']:
        i=cmd.index(flag);del cmd[i:i+2]
    r=run_cli(cmd);assert r.returncode==0,r.stderr
    checklist=json.loads((tmp_path/'pack/native-caption-review.json').read_text())
    assert checklist['narration_timing']=='not_checked_recordings_and_pause_policy_required'


def test_proposal_cli_produces_review_artifact_not_shortened_edit(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    bounds={k:{'min_picture_frames':30,'max_picture_frames':90,'lead_frames':0,'tail_frames':0} for k in ['a','b']}
    path=tmp_path/'bounds.json';path.write_text(json.dumps(bounds));cmd += ['--picture-bounds',str(path)]
    r=run_cli(cmd);assert r.returncode==0,r.stderr
    proposal=json.loads((tmp_path/'pack/narration-schedule.proposal.json').read_text())
    assert proposal['status']=='requires_visual_plan_review'
    assert proposal['duration_frames']<p['duration_frames']
    batch=json.loads((tmp_path/'pack/edit-batch.preview.json').read_text())
    assert [(x['startFrame'],x['durationFrames']) for x in batch['adds'] if x['type']=='audio']==[(0,60),(60,60)]


def test_caption_checks_bind_text_timing_and_current_audio(tmp_path):
    p,records,policy=setup(tmp_path);audit=chatcut.audit_narration(p,records,policy)
    cards=[{'asset_key':'a','text':'First.','start_seconds':.1,'end_seconds':1.8},
           {'asset_key':'b','text':'Second.','start_seconds':2.1,'end_seconds':3.8}]
    result=chatcut.audit_caption_sync(p,cards,audit,max_offset_seconds=.15)
    assert result['status']=='passed_technical_caption_sync'
    assert result['perceptual_approval'] is False


@pytest.mark.parametrize('fault',['deleted-text','wrong-text','old-time','cut-tail','missing','stale-audit','uncovered-speech'])
def test_caption_revision_drift_is_not_accepted(tmp_path,fault):
    p,records,policy=setup(tmp_path);audit=chatcut.audit_narration(p,records,policy)
    cards=[{'asset_key':'a','text':'First.','start_seconds':.1,'end_seconds':1.8},
           {'asset_key':'b','text':'Second.','start_seconds':2.1,'end_seconds':3.8}]
    if fault=='deleted-text':cards[1]['asset_key']='deleted'
    if fault=='wrong-text':cards[1]['text']='Previous version.'
    if fault=='old-time':cards[1]['start_seconds']=.1;cards[1]['end_seconds']=1.8
    if fault=='cut-tail':cards[1]['end_seconds']=3.0
    if fault=='missing':cards.pop()
    if fault=='stale-audit':p['scenes'][1]['text']='Updated.'
    if fault=='uncovered-speech':
        p['scenes'][0]['text']='First word.';records[0]['text_sha256']=digest('First word.')
        audit=chatcut.audit_narration(p,records,policy)
        cards=[{'asset_key':'a','text':'First','start_seconds':.1,'end_seconds':.4},
               {'asset_key':'a','text':'word.','start_seconds':1.3,'end_seconds':1.8},cards[1]]
    with pytest.raises(ValueError):chatcut.audit_caption_sync(p,cards,audit,max_offset_seconds=.15)


def test_cli_checks_native_caption_reference_without_changing_editor(tmp_path):
    p,records,policy,paths,cmd=cli_fixture(tmp_path)
    cards={'max_offset_seconds':.15,'cards':[
        {'asset_key':'a','text':'First.','start_seconds':.1,'end_seconds':1.8},
        {'asset_key':'b','text':'Second.','start_seconds':2.1,'end_seconds':3.8}]}
    path=tmp_path/'captions.json';path.write_text(json.dumps(cards));cmd+=['--caption-cards',str(path)]
    first=run_cli(cmd);assert first.returncode==0,first.stderr
    report=json.loads((tmp_path/'pack/caption-sync-audit.json').read_text())
    assert report['status']=='passed_technical_caption_sync' and report['editor_mutated'] is False
