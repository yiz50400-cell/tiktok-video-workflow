import asyncio,hashlib,json
from pathlib import Path
import pytest
from scripts.prepare_packaged_video import audio_stage


def test_speech_connection_uses_working_endpoint_and_bounded_timeouts(tmp_path, monkeypatch):
    import edge_tts.communicate
    class Service:
        def __init__(self, text, voice, **kwargs):
            if 'api.msedgeservices.com/tts/cognitiveservices/websocket/v1' not in edge_tts.communicate.WSS_URL:
                raise RuntimeError('legacy endpoint rejected the connection')
            if kwargs.get('connect_timeout') != 15 or kwargs.get('receive_timeout') != 25:
                raise RuntimeError('unbounded service wait')
        async def save(self, audio, metadata):
            Path(audio).write_bytes(b'audio'); Path(metadata).write_text('words')
    monkeypatch.setattr('scripts.prepare_packaged_video.edge_tts.Communicate', Service)
    sel = {'voice':'pt-BR-FranciscaNeural','rate':'+15%','volume':'+0%','pitch':'+0Hz'}
    result = asyncio.run(audio_stage(tmp_path, {'segments':[{'text':'teste'}]}, sel))
    assert Path(result[0]['path']).read_bytes() == b'audio'


def test_successful_tts_lines_are_reused_and_only_changed_line_runs(tmp_path,monkeypatch):
    calls=[]
    class Service:
        def __init__(self,text,voice,**kwargs): self.text=text
        async def save(self,audio,metadata):
            calls.append(self.text)
            Path(audio).write_bytes(self.text.encode());Path(metadata).write_text('words '+self.text)
    monkeypatch.setattr('scripts.prepare_packaged_video.edge_tts.Communicate',Service)
    sel={'voice':'pt-BR-FranciscaNeural','rate':'+15%','volume':'+0%','pitch':'+0Hz'}
    loc={'segments':[{'text':'a'},{'text':'b'}]}
    first=asyncio.run(audio_stage(tmp_path,loc,sel)); assert calls==['a','b']
    assert all(not x['reused'] for x in first)
    second=asyncio.run(audio_stage(tmp_path,loc,sel)); assert calls==['a','b']
    assert all(x['reused'] for x in second)
    loc['segments'][1]['text']='c'
    third=asyncio.run(audio_stage(tmp_path,loc,sel)); assert calls==['a','b','c']
    assert third[0]['reused'] and not third[1]['reused']
    Path(first[0]['metadata_path']).write_bytes(b'corrupt')
    asyncio.run(audio_stage(tmp_path,loc,sel)); assert calls==['a','b','c','a']


def test_failed_tts_does_not_replace_successful_cached_line(tmp_path,monkeypatch):
    class Service:
        def __init__(self,text,voice,**kwargs): self.text=text
        async def save(self,audio,metadata):
            Path(audio).write_bytes(b'partial')
            if self.text=='bad': raise RuntimeError('service failed')
            Path(metadata).write_bytes(b'words')
    monkeypatch.setattr('scripts.prepare_packaged_video.edge_tts.Communicate',Service)
    sel={'voice':'pt-BR-FranciscaNeural','rate':'+15%','volume':'+0%','pitch':'+0Hz'}
    asyncio.run(audio_stage(tmp_path,{'segments':[{'text':'good'}]},sel))
    original=(tmp_path/'line-audio/01.mp3').read_bytes()
    with pytest.raises(RuntimeError): asyncio.run(audio_stage(tmp_path,{'segments':[{'text':'bad'}]},sel))
    assert (tmp_path/'line-audio/01.mp3').read_bytes()==original
    assert json.loads((tmp_path/'line-audio/01-cache.json').read_text())['inputs']['text']=='good'

@pytest.mark.parametrize('cache_text',['{','{}'])
def test_broken_cache_manifest_regenerates_only_that_line(tmp_path,monkeypatch,cache_text):
    calls=[]
    class Service:
        def __init__(self,text,voice,**kwargs): self.text=text
        async def save(self,audio,metadata):
            calls.append(self.text);Path(audio).write_bytes(self.text.encode());Path(metadata).write_text(self.text)
    monkeypatch.setattr('scripts.prepare_packaged_video.edge_tts.Communicate',Service)
    sel={'voice':'pt-BR-FranciscaNeural','rate':'+15%','volume':'+0%','pitch':'+0Hz'}
    loc={'segments':[{'text':'a'},{'text':'b'}]}
    asyncio.run(audio_stage(tmp_path,loc,sel))
    (tmp_path/'line-audio/02-cache.json').write_text(cache_text)
    result=asyncio.run(audio_stage(tmp_path,loc,sel))
    assert calls==['a','b','b']
    assert result[0]['reused'] and not result[1]['reused']


def test_approval_gate_remains_active_under_optimized_python(tmp_path):
    import subprocess,sys
    d=tmp_path
    (d/'creative.json').write_text('{}')
    (d/'zh-approval.json').write_text(json.dumps({'status':'approved','version_sha256':'wrong'}))
    (d/'localization-v0001.json').write_text('{}')
    (d/'audit.json').write_text('{}')
    code="from pathlib import Path; from scripts.prepare_packaged_video import verify_approvals; d=Path(__import__('sys').argv[1]); verify_approvals(d,d/'localization-v0001.json',d/'audit.json')"
    result=subprocess.run([sys.executable,'-O','-c',code,str(d)],capture_output=True,text=True)
    assert result.returncode!=0 and 'Chinese approval' in result.stderr


def test_existing_version_is_protected_before_asset_generation(tmp_path):
    from scripts.prepare_packaged_video import protect_existing_version
    output=tmp_path/'existing.mp4';output.write_bytes(b'original')
    loc=tmp_path/'localization-v0002.json';loc.write_text('new localization')
    (tmp_path/'execution.json').write_text(json.dumps({'output':str(output),'localization_sha256':'old'}))
    with pytest.raises(ValueError,match='new numbered version'):
        protect_existing_version(tmp_path,loc)
    assert output.read_bytes()==b'original'


def test_rate_limit_retries_failed_lines_serially_without_repeating_success(tmp_path, monkeypatch):
    from aiohttp import WSServerHandshakeError
    attempts={}; waits=[]; active=0; retry_active=[]
    real_sleep=asyncio.sleep
    async def backoff(seconds):
        waits.append(seconds)
    class Service:
        def __init__(self,text,voice,**kwargs): self.text=text
        async def save(self,audio,metadata):
            nonlocal active
            attempts[self.text]=attempts.get(self.text,0)+1
            if self.text!='good' and attempts[self.text]==1:
                raise WSServerHandshakeError(None,(),status=429,headers={'Retry-After':'2'})
            active+=1
            if attempts[self.text]>1: retry_active.append(active)
            await real_sleep(0)
            Path(audio).write_bytes(self.text.encode()); Path(metadata).write_text('words')
            active-=1
    monkeypatch.setattr('scripts.prepare_packaged_video.edge_tts.Communicate',Service)
    monkeypatch.setattr('scripts.prepare_packaged_video.asyncio.sleep',backoff)
    sel={'voice':'pt-BR-FranciscaNeural','rate':'+15%','volume':'+0%','pitch':'+0Hz'}
    result=asyncio.run(audio_stage(tmp_path,{'segments':[{'text':x} for x in ('good','a','b')]},sel))
    assert [Path(row['path']).read_bytes() for row in result]==[b'good',b'a',b'b']
    assert attempts=={'good':1,'a':2,'b':2}
    assert waits and all(x>=2 for x in waits)
    assert retry_active==[1,1]


def test_persistent_rate_limit_stops_and_keeps_successful_line_cache(tmp_path, monkeypatch):
    from aiohttp import WSServerHandshakeError
    attempts={}
    async def backoff(seconds): pass
    class Service:
        def __init__(self,text,voice,**kwargs): self.text=text
        async def save(self,audio,metadata):
            attempts[self.text]=attempts.get(self.text,0)+1
            if self.text=='bad': raise WSServerHandshakeError(None,(),status=429)
            Path(audio).write_bytes(b'complete'); Path(metadata).write_text('words')
    monkeypatch.setattr('scripts.prepare_packaged_video.edge_tts.Communicate',Service)
    monkeypatch.setattr('scripts.prepare_packaged_video.asyncio.sleep',backoff)
    sel={'voice':'pt-BR-FranciscaNeural','rate':'+15%','volume':'+0%','pitch':'+0Hz'}
    with pytest.raises(RuntimeError,match='line 2.*429'):
        asyncio.run(audio_stage(tmp_path,{'segments':[{'text':'good'},{'text':'bad'}]},sel))
    assert attempts=={'good':1,'bad':3}
    assert (tmp_path/'line-audio/01.mp3').read_bytes()==b'complete'
    assert (tmp_path/'line-audio/01-cache.json').exists()
    assert not (tmp_path/'line-audio/02-cache.json').exists()


def test_single_source_windows_produce_a_valid_bound_render_input(tmp_path, monkeypatch):
    from scripts import prepare_packaged_video as preparation
    from scripts.produce_video import validate_render_input
    monkeypatch.setattr(preparation,'ROOT',tmp_path)
    source=tmp_path/'source.mp4'; source.write_bytes(b'source')
    audio=tmp_path/'voice.wav'; audio.write_bytes(b'voice')
    sources,windows,duration=preparation.source_windows(tmp_path,source,[[1,3.7],[8,12.5]])
    plan={'sources':sources,'windows':windows,'duration':duration,'voice_windows':[[0,7.2]],
          'audio_cues':[{'measured_start':0,'measured_end':1,'source_start':0,'source_end':1,'delay_ms':0,'text':'test'}],
          'voice_audio':str(audio),'voice_audio_sha256':hashlib.sha256(b'voice').hexdigest(),
          'output':str(tmp_path/'out.mp4'),'srt':str(tmp_path/'out.srt')}
    actual=preparation.absolute_plan(plan)
    assert validate_render_input(actual)[0]==[{'path':str(source),'sha256':hashlib.sha256(b'source').hexdigest()}]
    assert duration==7.2
    assert windows==[{'source_start':1,'source_end':3.7,'timeline_start':0,'timeline_end':2.7},
                     {'source_start':8,'source_end':12.5,'timeline_start':2.7,'timeline_end':7.2}]
