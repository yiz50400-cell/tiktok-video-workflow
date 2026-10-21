import json
from pathlib import Path
import pytest


def complete_plan(directory, output, subtitle):
    import hashlib
    source=directory/'source.bin'; source.write_bytes(b'source')
    audio=directory/'audio.bin'; audio.write_bytes(b'audio')
    digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    loc=directory/'localization-v0001.json'
    return {'source':str(source),'source_sha256':digest(source),
            'voice_audio':str(audio),'voice_audio_sha256':digest(audio),
            'output':str(output),'srt':str(subtitle),'duration':1,
            'localization_sha256':digest(loc),
            'windows':[{'source_start':0,'source_end':1,'timeline_start':0,'timeline_end':1}],
            'voice_windows':[[0,1]],
            'audio_cues':[{'source_start':0,'source_end':.5,'delay_ms':0,
                           'measured_start':0,'measured_end':.5,'text':'test'}]}


def test_resume_preserves_success_and_retries_failed_stage(tmp_path):
    from workflow.orchestration.local import StageStore
    calls = []
    store = StageStore(tmp_path)
    def good():
        calls.append('good')
        output = tmp_path / 'asset'; output.write_text('ready')
        return {'outputs': [str(output)]}
    def bad():
        raise RuntimeError('temporary failure')
    store.run('audio', 'input-a', good)
    with pytest.raises(RuntimeError):
        store.run('render', 'input-b', bad)
    resumed = StageStore(tmp_path)
    assert resumed.run('audio', 'input-a', good)['reused']
    assert calls == ['good']
    assert resumed.run('render', 'input-b', good)['reused'] is False
    (tmp_path / 'asset').write_text('corrupt')
    assert resumed.run('audio', 'input-a', good)['reused'] is False


def test_changed_stage_input_requires_new_run_directory(tmp_path):
    from workflow.orchestration.local import StageStore
    store = StageStore(tmp_path)
    with pytest.raises(ValueError, match='no outputs'):
        store.run('audio', 'first', lambda: {'outputs': []})
    (tmp_path / 'asset').write_text('ready')
    store.run('audio', 'first', lambda: {'outputs': [str(tmp_path / 'asset')]})
    with pytest.raises(ValueError, match='new run'):
        store.run('audio', 'changed', lambda: {'outputs': []})


def test_batch_preflight_checks_bound_approvals_before_expensive_work(tmp_path):
    from workflow.orchestration.local import preflight
    directory = tmp_path / 'approved'; directory.mkdir()
    (directory / 'creative.json').write_text('{}')
    (directory / 'zh-approval.json').write_text('{"status":"approved","version_sha256":"wrong"}')
    loc = directory / 'localization-v0001.json'; loc.write_text('{}')
    audit = directory / 'audit.json'; audit.write_text('{}')
    plan = directory / 'execution.json'; plan.write_text('{}')
    with pytest.raises(ValueError, match='Chinese approval'):
        preflight({'plan':str(plan),'localization':str(loc),'audit':str(audit)})


def test_batch_runner_retries_only_failed_render_and_then_reuses_all_stages(tmp_path, monkeypatch):
    import hashlib
    from scripts import run_local_batch

    directory = tmp_path / 'approved'; directory.mkdir()
    creative = directory / 'creative.json'; creative.write_text('{"lines":[]}')
    approval = directory / 'zh-approval.json'
    approval.write_text(json.dumps({'status':'approved', 'version_sha256':hashlib.sha256(creative.read_bytes()).hexdigest()}))
    localization = directory / 'localization-v0001.json'; localization.write_text('{}')
    audit = directory / 'localization-audit.json'; audit.write_text(json.dumps({
        'status':'passed_independent_internal_text_review',
        'localization_sha256':hashlib.sha256(localization.read_bytes()).hexdigest(),
        'creative_sha256':hashlib.sha256(creative.read_bytes()).hexdigest(),
        'zh_approval_sha256':hashlib.sha256(approval.read_bytes()).hexdigest(),
        'material_changes_to_approved_meaning':[]}))
    output = tmp_path / 'sample.mp4'; subtitle = tmp_path / 'sample.srt'
    plan = complete_plan(directory, output, subtitle)
    calls = []

    def fake_run(command, log_path):
        script = Path(command[1]).name
        calls.append(script)
        if script == 'prepare_packaged_video.py':
            (directory / 'execution.json').write_text(json.dumps(plan))
            (directory / 'preparation-timing.json').write_text('{}')
            (directory / 'voice-recording.json').write_text('{}')
        elif script == 'produce_video.py':
            if calls.count(script) == 1:
                raise RuntimeError('transient render error')
            output.write_bytes(b'video'); subtitle.write_text('subtitle')
        else:
            for name in ('qa.json','cut-review.jpg','action-review.jpg','contact-sheet.jpg'):
                (directory / name).write_bytes(name.encode())
        log_path.write_text('ok')

    monkeypatch.setattr(run_local_batch, '_run', fake_run)
    with pytest.raises(RuntimeError, match='transient'):
        run_local_batch.run_batch(directory)
    run_local_batch.run_batch(directory)
    reused = run_local_batch.run_batch(directory)
    assert calls == ['prepare_packaged_video.py', 'produce_video.py', 'produce_video.py', 'verify_packaged_video.py']
    assert all(row['reused'] for row in reused['stages'].values())


def test_batch_runner_refuses_to_overwrite_existing_untracked_render(tmp_path, monkeypatch):
    import hashlib
    from scripts import run_local_batch

    directory = tmp_path / 'approved'; directory.mkdir()
    creative = directory / 'creative.json'; creative.write_text('{}')
    approval = directory / 'zh-approval.json'; approval.write_text(json.dumps({
        'status':'approved','version_sha256':hashlib.sha256(creative.read_bytes()).hexdigest()}))
    localization = directory / 'localization-v0001.json'; localization.write_text('{}')
    audit = directory / 'localization-audit.json'; audit.write_text(json.dumps({
        'status':'passed_independent_internal_text_review',
        'localization_sha256':hashlib.sha256(localization.read_bytes()).hexdigest(),
        'creative_sha256':hashlib.sha256(creative.read_bytes()).hexdigest(),
        'zh_approval_sha256':hashlib.sha256(approval.read_bytes()).hexdigest(),
        'material_changes_to_approved_meaning':[]}))
    output = tmp_path / 'existing.mp4'; output.write_bytes(b'keep')
    subtitle = tmp_path / 'sample.srt'
    (directory / 'execution.json').write_text(json.dumps(complete_plan(directory,output,subtitle)))
    with pytest.raises(ValueError, match='already exists'):
        run_local_batch.run_batch(directory, stages={'render'})
    assert output.read_bytes() == b'keep'


def test_prepare_only_does_not_require_rendered_video(tmp_path, monkeypatch):
    import hashlib
    from scripts import run_local_batch
    d=tmp_path/'approved'; d.mkdir()
    creative=d/'creative.json'; creative.write_text('{}')
    approval=d/'zh-approval.json'; approval.write_text(json.dumps({'status':'approved',
        'version_sha256':hashlib.sha256(creative.read_bytes()).hexdigest()}))
    loc=d/'localization-v0001.json'; loc.write_text('{}')
    (d/'localization-audit.json').write_text(json.dumps({
        'status':'passed_independent_internal_text_review',
        'localization_sha256':hashlib.sha256(loc.read_bytes()).hexdigest(),
        'creative_sha256':hashlib.sha256(creative.read_bytes()).hexdigest(),
        'zh_approval_sha256':hashlib.sha256(approval.read_bytes()).hexdigest(),
        'material_changes_to_approved_meaning':[]}))
    output=tmp_path/'video.mp4'; subtitle=tmp_path/'video.srt'
    plan=complete_plan(d,output,subtitle)
    def fake_run(command,log_path):
        (d/'execution.json').write_text(json.dumps(plan))
        (d/'preparation-timing.json').write_text('{}')
        (d/'voice-recording.json').write_text('{}')
    monkeypatch.setattr(run_local_batch,'_run',fake_run)
    result=run_local_batch.run_batch(d,stages={'prepare'})
    assert result['stages']['prepare']['status']=='SUCCEEDED'
    assert not output.exists()


def test_render_fingerprint_includes_source_bytes(tmp_path):
    from scripts.run_local_batch import _plan_fingerprint
    (tmp_path/'localization-v0001.json').write_text('{}')
    plan=complete_plan(tmp_path,tmp_path/'out.mp4',tmp_path/'out.srt')
    before=_plan_fingerprint(plan)
    Path(plan['source']).write_bytes(b'changed')
    assert _plan_fingerprint(plan)!=before
