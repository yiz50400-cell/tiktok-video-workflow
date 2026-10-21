import json
import hashlib
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import imageio_ffmpeg
import pytest

from workflow.api import create_app


@pytest.fixture
def pipeline_client(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip(); client.headers['X-Workflow-Client']='studio'
        yield client


def create(client,count=1):
    return client.post('/api/jobs',json={'title':'测试批次','product':'测试商品','market':'BR','count':count}).json()


@pytest.mark.parametrize('count',[1,2,5,7,100])
def test_pipeline_uses_request_count_and_unplanned_items_are_waiting(pipeline_client,count):
    job=create(pipeline_client,count)
    response=pipeline_client.post(f'/api/jobs/{job["id"]}/pipeline/run',json={})
    assert response.status_code==200,response.text
    result=response.json()['pipeline']
    assert result['target_count']==count and len(result['items'])==count
    assert result['delivered_count']==0
    assert all(item['status']=='WAITING_SOURCE' for item in result['items'])
    assert '发布' not in result['next_action']


def test_source_analysis_runs_once_and_waits_for_codex_observations(pipeline_client,tmp_path):
    job=create(pipeline_client,2)
    source=tmp_path/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i',
                    'testsrc2=size=180x320:rate=30','-t','1','-c:v','libx264','-preset','ultrafast',str(source)],check=True)
    imported=pipeline_client.post(f'/api/jobs/{job["id"]}/assets',files={'file':('source.mp4',source.read_bytes(),'video/mp4')}).json()
    response=pipeline_client.post(f'/api/jobs/{job["id"]}/pipeline/inputs',json={'items':[{'ordinal':1,'asset_id':imported['asset']['id']}]})
    assert response.status_code==200,response.text
    first=pipeline_client.post(f'/api/jobs/{job["id"]}/pipeline/run',json={}).json()['pipeline']
    assert first['items'][0]['status']=='WAITING_OBSERVATIONS'
    manifest=Path(first['items'][0]['analysis_manifest']); before=manifest.stat().st_mtime_ns
    second=pipeline_client.post(f'/api/jobs/{job["id"]}/pipeline/run',json={}).json()['pipeline']
    assert second['items'][0]['analysis_manifest']==str(manifest)
    assert manifest.stat().st_mtime_ns==before
    assert second['items'][1]['status']=='WAITING_SOURCE'


def test_cross_job_assets_ordinals_and_cancelled_jobs_rejected(pipeline_client,tmp_path):
    a=create(pipeline_client,1); b=create(pipeline_client,1)
    for body in [{'items':[{'ordinal':2}]},{'items':[{'ordinal':1,'asset_id':'asset_other'}]}]:
        response=pipeline_client.post(f'/api/jobs/{a["id"]}/pipeline/inputs',json=body)
        assert response.status_code==422
    pipeline_client.post(f'/api/jobs/{b["id"]}/pipeline/run',json={})
    cancelled=pipeline_client.post(f'/api/jobs/{b["id"]}/cancel').json()
    assert cancelled['next_action']=='任务已取消'
    assert pipeline_client.post(f'/api/jobs/{b["id"]}/pipeline/run',json={}).status_code==409


def test_input_updates_preserve_other_item_and_replace_edited_item(pipeline_client):
    job=create(pipeline_client,3); route=f'/api/jobs/{job["id"]}/pipeline/inputs'
    assert pipeline_client.post(route,json={'items':[{'ordinal':1},{'ordinal':2}]}).status_code==200
    result=pipeline_client.post(route,json={'items':[{'ordinal':1}]}).json()
    assert [i['ordinal'] for i in result['pipeline_inputs']['items']]==[1,2]


@pytest.mark.parametrize('fields', [
    {'sources': []},
    {'sources': [{'asset_id': 'asset_other'}]},
    {'sources': [{'observations_path': '/tmp/observations.json'}]},
    {'asset_id': 'asset_other', 'sources': [{'asset_id': 'asset_other'}]},
    {'sources': [{'asset_id': 'asset_other', 'unchecked': True}]},
])
def test_multisource_input_rejects_missing_foreign_and_ambiguous_sources(pipeline_client,fields):
    job=create(pipeline_client)
    response=pipeline_client.post(f'/api/jobs/{job["id"]}/pipeline/inputs',
                                  json={'items':[{'ordinal':1,**fields}]})
    assert response.status_code==422


def test_collection_local_original_is_ingested_into_job_and_recovers(pipeline_client,tmp_path):
    job=create(pipeline_client,1);source=tmp_path/'original.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i',
                    'testsrc2=size=180x320:rate=30','-t','1','-c:v','libx264','-preset','ultrafast',str(source)],check=True)
    body={'collection':{'brand':'测试','product':'商品','seeds':[{'id':'demo','source_url':'https://example.test/video',
                        'product_match':'测试 商品','hook_and_action_observation':'演示商品'}],
                        'local_sources':{'demo':str(source)}},'items':[]}
    route=f'/api/jobs/{job["id"]}'
    assert pipeline_client.post(route+'/pipeline/inputs',json=body).status_code==200
    first=pipeline_client.post(route+'/pipeline/run',json={}).json()
    assert len(first['assets'])==1
    assert first['pipeline']['collection']['candidates'][0]['acquisition_status']=='imported'
    assert first['pipeline']['items'][0]['status']=='WAITING_SOURCE'
    second=pipeline_client.post(route+'/pipeline/run',json={}).json()
    assert [a['id'] for a in second['assets']]==[a['id'] for a in first['assets']]
    assert second['pipeline']['collection']['reused'] is True


def test_task_run_excludes_simultaneous_execution(pipeline_client):
    from workflow.orchestration.pipeline import pipeline_lock
    job=create(pipeline_client)
    data=pipeline_client.app.state.data_dir
    with pipeline_lock(data,job['id']):
        result=pipeline_client.post(f'/api/jobs/{job["id"]}/pipeline/run',json={})
        assert result.status_code==409 and '正在执行' in result.text

    ph=pipeline_client.post('/api/jobs',json={'title':'语言锁','product':'商品','market':'PH','count':1,
                                           'voiceover_locale':'fil-PH'}).json()
    with pipeline_lock(data,ph['id']):
        changed=pipeline_client.post(f'/api/jobs/{ph["id"]}/language',json={'voiceover_locale':'en-PH'})
        assert changed.status_code==409


def test_language_change_invalidates_pipeline_progress(pipeline_client):
    job=pipeline_client.post('/api/jobs',json={'title':'语言测试','product':'商品','market':'PH','count':2,
                                            'voiceover_locale':'fil-PH'}).json()
    route=f'/api/jobs/{job["id"]}'
    pipeline_client.post(route+'/pipeline/run',json={})
    changed=pipeline_client.post(route+'/language',json={'voiceover_locale':'en-PH'}).json()
    assert 'pipeline' not in changed
    assert changed['batch']['rendered_count']==0
    assert changed['status']=='WAITING_MATERIAL'


@pytest.mark.parametrize('source_count', [1, 2])
def test_actual_full_chain_waits_at_approval_and_delivers_then_recovers(pipeline_client,tmp_path,monkeypatch,source_count):
    from workflow.publishing.prepare import digest
    from workflow.production import preparation
    from workflow.orchestration.pipeline import Pipeline
    from scripts import run_local_batch
    from scripts.produce_video import render_plan
    from scripts.verify_packaged_video import verify
    from tests.test_configured_production import fake_audio, make_voice_pool
    pool = make_voice_pool(tmp_path / 'voice-pool')
    job=create(pipeline_client,1);route=f'/api/jobs/{job["id"]}'
    source=tmp_path/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i',
                    'testsrc2=size=180x320:rate=60','-t','6','-c:v','libx264','-preset','ultrafast',str(source)],check=True)
    asset=pipeline_client.post(route+'/assets',files={'file':('source.mp4',source.read_bytes(),'video/mp4')}).json()['asset']
    inputs={'ordinal':1,'asset_id':asset['id']}
    if source_count == 2:
        second=tmp_path/'source2.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(source),
                        '-vf','hue=h=90','-c:v','libx264',str(second)],check=True)
        second_asset=pipeline_client.post(route+'/assets',files={'file':('source2.mp4',second.read_bytes(),'video/mp4')}).json()['asset']
        inputs={'ordinal':1,'sources':[{'asset_id':asset['id']},{'asset_id':second_asset['id']}]}
    submitted=pipeline_client.post(route+'/pipeline/inputs',json={'items':[inputs]})
    assert submitted.status_code == 200,submitted.text
    state=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    manifest=json.loads(Path(state['analysis_manifest']).read_text())
    observations=tmp_path/'observations.json'
    observations.write_text(json.dumps({'source_sha256':manifest['source']['sha256'],'observer':'test_author',
        'observed_at':'2026-10-14T00:00:00Z','segments':[{'kind':'action','evidence_status':'observed',
            'start_seconds':0,'end_seconds':6,'description':'合成动态测试图，仅用于工具测试',
            'frame_ids':[f['id'] for f in manifest['frames']]}],
        'known_facts':[],'popularity_hypotheses':[],'unknowns':[],'risks':[]}))
    if source_count == 2:
        inputs['sources'][0]['observations_path']=str(observations)
        second_manifest=json.loads(Path(state['source_analyses'][1]['analysis_manifest']).read_text())
        second_observations=tmp_path/'observations2.json'
        data=json.loads(observations.read_text())
        data['source_sha256']=second_manifest['source']['sha256']
        data['segments'][0]['frame_ids']=[f['id'] for f in second_manifest['frames']]
        second_observations.write_text(json.dumps(data))
        inputs['sources'][1]['observations_path']=str(second_observations)
    else:
        inputs['observations_path']=str(observations)
    pipeline_client.post(route+'/pipeline/inputs',json={'items':[inputs]})
    state=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert state['status']=='WAITING_DRAFT',state
    observed=json.loads(Path(state['analysis_path']).read_text())
    observed_manifest=json.loads(Path(observed['manifest_path']).read_text())
    spec=tmp_path/'spec.json'
    spec.write_text(json.dumps({'job_id':job['id'],'product_series':'测试商品','author':'test_author','market':'BR',
        'voiceover_locale':'pt-BR','angle':'测试演示','hook':'先看商品','buyer_concern':'想看实际动作',
        'proof_action':'展示','cta_direction':'选择','edit_video_path':manifest['source']['path'],
        'shots':[{'role':'action','source_start_seconds':0,'source_end_seconds':6,'description':'合成动态测试图',
                  'evidence_segment_index':0,'evidence_frame_ids':[f['id'] for f in observed_manifest['frames']]}],
        'script_lines':[{'start_seconds':0,'end_seconds':3,'text':'把商品放到镜头前先仔细看一下。'},
                        {'start_seconds':3,'end_seconds':6,'text':'再看一下这里的动作是怎么做的。'}],
        'production_config':{'voice_approval':str(pool),'voice_approval_sha256':digest(pool),
                             'source_continuity_review':{'status':'passed_codex_source_review','reviewer':'test_observer',
                                 'shots':[{'index':1,'source_sha256':manifest['source']['sha256'],
                                           'source_start':0,'source_end':6,'internal_cuts':[],'action_complete':True}]},
                             'voice_options':{'rate':'+0%','volume':'+0%','pitch':'+0Hz'},
                             'width':180,'height':320,'encoder':'libx264','packaging':[]}}))
    if source_count == 2:
        data=json.loads(spec.read_text())
        data['sources']=[{'edit_video_path':data.pop('edit_video_path')},
                         {'edit_video_path':second_manifest['source']['path']}]
        data['min_unique_sources']=2
        data['shots'][0].update(source_index=0,source_end_seconds=3,
            evidence_frame_ids=[f['id'] for f in observed_manifest['frames'] if f['source_time_seconds']<=3])
        data['shots'].append({'role':'result','source_index':1,'source_start_seconds':3,'source_end_seconds':6,
            'description':'第二个不同原片的合成图','evidence_segment_index':0,
            'evidence_frame_ids':[f['id'] for f in second_manifest['frames'] if f['source_time_seconds']>=3]})
        continuity=data['production_config']['source_continuity_review']['shots']
        continuity[0]['source_end']=3
        continuity.append({'index':2,'source_sha256':second_manifest['source']['sha256'],
                           'source_start':3,'source_end':6,'internal_cuts':[],'action_complete':True})
        spec.write_text(json.dumps(data))
    inputs['spec_path']=str(spec)
    audit=tmp_path/'audit.json';audit.write_text(json.dumps({'reviewer':'test_reviewer','verdict':'pass',
        'checks':{'spoken_naturalness':True,'evidence_accuracy':True,'runtime_fit':True,
                  'cta_truthfulness':True,'commercial_attractiveness':True},'notes':['合成工具测试审核，不代表真实内容审核']}))
    inputs['audit_path']=str(audit)
    pipeline_client.post(route+'/pipeline/inputs',json={'items':[inputs]})
    state=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert state['status']=='WAITING_ZH_APPROVAL',state
    directory=Path(state['production_directory']);creative_sha=digest(directory/'creative.json')
    configuration=json.loads((directory/'creative.json').read_text())['production']
    assert len(configuration['sources'])==source_count
    assert [w['source_index'] for w in configuration['windows']]==list(range(source_count))
    calls=[]
    async def synthetic_audio(*args):calls.append('audio');return fake_audio(*args)
    monkeypatch.setattr(preparation,'audio_stage',synthetic_audio)
    # Execute real preparation/render/QA inside the test process; the network TTS is the only substitute.
    render_attempts=[]
    def local_stage(command,log):
        script=Path(command[1]).name
        if script=='prepare_packaged_video.py':preparation.prepare_configured(directory)
        elif script=='produce_video.py':
            render_attempts.append('render')
            if len(render_attempts)==1:raise RuntimeError('test temporary render failure')
            render_plan(json.loads((directory/'execution.json').read_text()))
        else:verify(directory)
        log.write_text('test process executed real media stage')
    monkeypatch.setattr(run_local_batch,'_run',local_stage)
    monkeypatch.setattr(Pipeline,'_run_production',lambda self,d:run_local_batch.run_batch(d))
    pipeline_client.post(route+'/pipeline/run',json={})
    assert calls==[]
    confirmed=pipeline_client.post(route+'/pipeline/evidence',json={'ordinal':1,'kind':'chinese_confirmation',
        'records':{'version_sha256':creative_sha,'user_reply':'测试专用确认','source_turn_id':'test-only'}})
    assert confirmed.status_code==200,confirmed.text
    state=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert state['status']=='WAITING_LOCALIZATION',state
    loc={'market':'BR','locale':'pt-BR','creative_sha256':creative_sha,
         'segments':[{'line':1,'text':'Primeiro teste.','visible_text':'','window':[0,3]},
                     {'line':2,'text':'Segundo teste.','visible_text':'','window':[3,6]}]}
    loc_sha=hashlib.sha256((json.dumps(loc,ensure_ascii=False,indent=2)+'\n').encode()).hexdigest()
    audit={'status':'passed_independent_internal_text_review','market':'BR','locale':'pt-BR',
           'reviewer':'test_reviewer','creative_sha256':creative_sha,
           'zh_approval_sha256':digest(directory/'zh-approval.json'),'localization_sha256':loc_sha,
           'material_changes_to_approved_meaning':[]}
    localized=pipeline_client.post(route+'/pipeline/evidence',json={'ordinal':1,'kind':'localization',
        'records':{'localization':loc,'audit':audit}})
    assert localized.status_code==200,localized.text
    result=pipeline_client.post(route+'/pipeline/run',json={}).json()
    assert result['pipeline']['items'][0]['status']=='FAILED'
    assert calls==['audio'] and result['batch']['rendered_count']==0,result['pipeline']['items'][0].get('error_zh')
    result=pipeline_client.post(route+'/pipeline/run',json={}).json()
    state=result['pipeline']['items'][0]
    assert state['status']=='LOCAL_DELIVERED',state
    assert result['batch']['rendered_count']==1
    package=Path(state['package_path']);assert (package/'中文口播.txt').exists()
    before=Path(state['video_path']).stat().st_mtime_ns
    resumed=pipeline_client.post(route+'/pipeline/run',json={}).json()
    assert resumed['pipeline']['items'][0]['reused'] is True
    assert Path(state['video_path']).stat().st_mtime_ns==before and calls==['audio']
    assert render_attempts==['render','render']
    # Simulate interruption after the atomic video rename but before stage success is saved.
    stage_file=directory/'stage-state.json';stages=json.loads(stage_file.read_text())
    stages['render']['status']='RUNNING';stage_file.write_text(json.dumps(stages))
    recovered=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert recovered['status']=='LOCAL_DELIVERED',recovered
    assert render_attempts==['render','render','render'] and calls==['audio']
    # Damaged delivery copies must not be silently accepted or overwritten.
    Path(state['video_path']).write_bytes(b'damaged')
    damaged=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert damaged['status']=='FAILED' and '损坏' in damaged['error_zh']


def test_partial_production_failure_isolated_and_completed_stage_reused(pipeline_client,tmp_path,monkeypatch):
    from workflow.orchestration.pipeline import Pipeline
    job=create(pipeline_client,2);route=f'/api/jobs/{job["id"]}'
    monkeypatch.setattr(Pipeline,'_item',lambda self,root,j,item:{'ordinal':item['ordinal'],
        'status':'READY_PRODUCTION','adopt_verified':True,'production_directory':str(tmp_path)})
    completed={};attempts=[]
    def produce(self,row,j,root):
        ordinal=row['ordinal']
        if ordinal in completed:return {**row,**completed[ordinal],'reused':True}
        attempts.append(ordinal)
        if ordinal==2 and attempts.count(2)==1:raise RuntimeError('one item failure')
        completed[ordinal]={'status':'LOCAL_DELIVERED','package_path':str(tmp_path/str(ordinal))}
        return {**row,**completed[ordinal],'reused':False}
    monkeypatch.setattr(Pipeline,'_produce',produce)
    pipeline_client.post(route+'/pipeline/inputs',json={'items':[],'workers':2})
    first=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']
    assert first['delivered_count']==1 and first['items'][1]['status']=='FAILED'
    second=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']
    assert second['delivered_count']==2 and second['items'][0]['reused'] is True
    assert attempts.count(1)==1 and attempts.count(2)==2


def test_optional_repair_waits_for_exact_review_then_analyzes_without_repairing_again(pipeline_client,tmp_path,monkeypatch):
    from workflow.orchestration import repair
    from workflow.publishing.prepare import digest,write_json
    from workflow.store import database,Asset
    job=create(pipeline_client);route=f'/api/jobs/{job["id"]}'
    source=tmp_path/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i',
                    'testsrc2=size=180x320:rate=30','-t','3','-c:v','libx264','-preset','ultrafast',str(source)],check=True)
    asset=pipeline_client.post(route+'/assets',files={'file':('source.mp4',source.read_bytes(),'video/mp4')}).json()['asset']
    mask=tmp_path/'mask.json';mask.write_text('{}');model=tmp_path/'model.pt';model.write_bytes(b'test mock model')
    calls=[]
    def fake_execute(workspace,raw,settings,directory):
        calls.append('repair');task=directory/'task.json'
        repaired=directory/'repaired.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(raw),'-ss','1','-t','2',
                        '-an','-c:v','libx264',str(repaired)],check=True)
        write_json(task,{'status':'NEEDS_VISUAL_REVIEW','source_sha256':digest(raw),'start_seconds':1,
                        'outputs':{name:{'path':str(repaired),'sha256':digest(repaired)} for name in ('repaired.mp4','comparison.mp4')}})
        return {'task_path':str(task),'outputs':[str(task),str(repaired)]}
    monkeypatch.setattr(repair,'execute',fake_execute)
    settings={'mask_spec':str(mask),'model':str(model),'start':1,'duration':2}
    item={'ordinal':1,'asset_id':asset['id'],'repair':settings}
    pipeline_client.post(route+'/pipeline/inputs',json={'items':[item]})
    row=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert row['status']=='WAITING_REPAIR_REVIEW' and calls==['repair']
    task=json.loads(Path(row['repair_task_path']).read_text());review=tmp_path/'review.json'
    write_json(review,{'status':'passed_internal_visual_review','reviewer':'test_only',
        'task_sha256':digest(row['repair_task_path']),'repaired_sha256':task['outputs']['repaired.mp4']['sha256'],
        'full_playback':True,'product_action_preserved':True,'outside_mask_review':True})
    settings['review_path']=str(review)
    pipeline_client.post(route+'/pipeline/inputs',json={'items':[item]})
    row=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert row['status']=='WAITING_OBSERVATIONS' and calls==['repair']
    engine,sessions=database(pipeline_client.app.state.data_dir)
    with sessions() as session:
        clean=session.get(Asset,row['asset_id'])
        assert clean.metadata_json['original_sha256']==asset['sha256']
        assert clean.metadata_json['original_offset']==1
    engine.dispose()
    review_data=json.loads(review.read_text());review_data['repaired_sha256']='stale';write_json(review,review_data)
    stale=pipeline_client.post(route+'/pipeline/run',json={}).json()['pipeline']['items'][0]
    assert stale['status']=='FAILED' and '复核' in stale['error_zh'] and calls==['repair']
