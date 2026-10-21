import json
import math
from pathlib import Path
import struct
import subprocess
import wave

import imageio_ffmpeg
import pytest

from workflow.publishing.prepare import digest

def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False))


def make_voice_pool(directory):
    """Create synthetic test-only auditions without depending on client projects."""
    directory.mkdir()
    auditions = []
    for voice, frequency in [('pt-BR-FranciscaNeural', 440),
                             ('pt-BR-ThalitaMultilingualNeural', 660)]:
        audio = directory / f'{voice}.wav'
        with wave.open(str(audio), 'wb') as wav:
            wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b''.join(struct.pack('<h', round(1000 * math.sin(
                2 * math.pi * frequency * sample / 48000))) for sample in range(4800)))
        auditions.append({'voice': voice, 'file': audio.name, 'sha256': digest(audio)})
    approval = directory / 'voice-approval.json'
    save(approval, {'status': 'user_approved_voice_pool', 'market': 'BR', 'locale': 'pt-BR',
                    'user_reply': '测试专用合成夹具，不代表真人试听认可',
                    'source_turn_id': 'test-only-synthetic-fixture',
                    'approved_auditions': auditions})
    return approval


@pytest.fixture
def configured(tmp_path):
    pool = make_voice_pool(tmp_path / 'voice-pool')
    source = tmp_path / 'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=180x320:rate=60', '-t', '6', '-an', '-c:v', 'libx264',
                    '-preset', 'ultrafast', str(source)], check=True)
    windows = [{'source_start':0,'source_end':3,'timeline_start':0,'timeline_end':3},
               {'source_start':3,'source_end':6,'timeline_start':3,'timeline_end':6}]
    creative = {'schema_version':2, 'job_id':'job_test', 'ordinal':2, 'version':1,
                'product_name_zh':'测试商品', 'market':'BR','locale':'pt-BR', 'author':'author',
                'lines':[{'zh':'第一句测试口播。','visible_zh':'','window':[0,3]},
                         {'zh':'第二句测试口播。','visible_zh':'','window':[3,6]}],
                'production':{'sources':[{'path':str(source),'sha256':digest(source)}],
                              'windows':windows, 'duration':6,'width':180,'height':320,
                              'encoder':'libx264','packaging':[], 'sound_effects':[],
                              'voice_approval':str(pool),'voice_approval_sha256':digest(pool),
                              'voice_options':{'rate':'+0%','volume':'+0%','pitch':'+0Hz'}}}
    save(tmp_path/'creative.json', creative)
    creative['production']['source_continuity_review']={'status':'passed_codex_source_review','reviewer':'test_observer',
        'shots':[{'index':i,'source_sha256':digest(source),'source_start':w['source_start'],
                  'source_end':w['source_end'],'internal_cuts':[],'action_complete':True} for i,w in enumerate(windows,1)]}
    save(tmp_path/'creative.json', creative)
    save(tmp_path/'zh-approval.json', {'status':'approved', 'version_sha256':digest(tmp_path/'creative.json'),
                                     'user_reply':'测试确认','source_turn_id':'test-only'})
    loc = {'market':'BR','locale':'pt-BR','creative_sha256':digest(tmp_path/'creative.json'),
           'segments':[{'line':1,'text':'Primeiro teste.','visible_text':'','window':[0,3]},
                       {'line':2,'text':'Segundo teste.','visible_text':'','window':[3,6]}]}
    save(tmp_path/'localization-v0001.json', loc)
    save(tmp_path/'localization-audit-v0001.json', {
        'status':'passed_independent_internal_text_review', 'reviewer':'reviewer',
        'market':'BR','locale':'pt-BR','creative_sha256':digest(tmp_path/'creative.json'),
        'localization_sha256':digest(tmp_path/'localization-v0001.json'),
        'zh_approval_sha256':digest(tmp_path/'zh-approval.json'),'material_changes_to_approved_meaning':[]})
    return tmp_path


def fake_audio(directory, localization, selection):
    rows=[]
    for index, seg in enumerate(localization['segments'],1):
        audio=directory/f'test-{index}.wav'; meta=directory/f'test-{index}.jsonl'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v','error','-f','lavfi','-i',
                        'sine=frequency=440:sample_rate=48000','-t','0.5',str(audio)],check=True)
        meta.write_text(json.dumps({'type':'WordBoundary','offset':0,'duration':5000000,'text':seg['text']})+'\n')
        rows.append({'line':index,'path':str(audio),'metadata_path':str(meta),'reused':False})
    return rows


def test_generic_prepare_and_actual_render_accept_two_lines_and_request_identity(configured, monkeypatch):
    from workflow.production import preparation
    from scripts.produce_video import render_plan
    async def synth(*args): return fake_audio(*args)
    monkeypatch.setattr(preparation,'audio_stage',synth)
    plan=preparation.prepare_configured(configured)
    assert plan['ordinal']==2 and plan['job_id']=='job_test'
    assert len(plan['voice_windows'])==2 and len(plan['audio_cues'])==2
    assert plan['duration']==6
    result=render_plan(plan)
    assert Path(result['output']).is_file()
    assert Path(result['srt']).read_text().count('-->')==2


def test_invalid_localized_window_is_rejected_before_audio(configured, monkeypatch):
    from workflow.production import preparation
    loc=configured/'localization-v0001.json'; data=json.loads(loc.read_text()); data['segments'][1]['window']=[2,6];save(loc,data)
    audit=configured/'localization-audit-v0001.json'; data=json.loads(audit.read_text());data['localization_sha256']=digest(loc);save(audit,data)
    async def forbidden(*args): raise AssertionError('TTS called before gate')
    monkeypatch.setattr(preparation,'audio_stage',forbidden)
    with pytest.raises(ValueError,match='window'):
        preparation.prepare_configured(configured)


def test_voice_rotation_is_reserved_by_item_and_remains_locked(configured):
    from workflow.production.preparation import reserve_voice
    state=configured/'batch-voices.json'
    first=reserve_voice(configured,state)
    sibling=configured/'third'; sibling.mkdir()
    for name in ('creative.json','zh-approval.json','localization-v0001.json','localization-audit-v0001.json'):
        (sibling/name).write_bytes((configured/name).read_bytes())
    creative=json.loads((sibling/'creative.json').read_text()); creative['ordinal']=3;save(sibling/'creative.json',creative)
    for name,fields in [('zh-approval.json',{'version_sha256':digest(sibling/'creative.json')}),
                        ('localization-v0001.json',{'creative_sha256':digest(sibling/'creative.json')})]:
        d=json.loads((sibling/name).read_text());d.update(fields);save(sibling/name,d)
    audit=json.loads((sibling/'localization-audit-v0001.json').read_text());audit.update(
        creative_sha256=digest(sibling/'creative.json'),zh_approval_sha256=digest(sibling/'zh-approval.json'),
        localization_sha256=digest(sibling/'localization-v0001.json'));save(sibling/'localization-audit-v0001.json',audit)
    second=reserve_voice(sibling,state)
    assert first['voice']!=second['voice']
    assert reserve_voice(configured,state)==first


def rebind(configured):
    creative_sha=digest(configured/'creative.json')
    approved=json.loads((configured/'zh-approval.json').read_text());approved['version_sha256']=creative_sha
    save(configured/'zh-approval.json',approved)
    loc=json.loads((configured/'localization-v0001.json').read_text());loc['creative_sha256']=creative_sha
    save(configured/'localization-v0001.json',loc)
    audit=json.loads((configured/'localization-audit-v0001.json').read_text());audit.update(
        creative_sha256=creative_sha,zh_approval_sha256=digest(configured/'zh-approval.json'),
        localization_sha256=digest(configured/'localization-v0001.json'))
    save(configured/'localization-audit-v0001.json',audit)


def test_actual_configured_packaging_and_sound_render(configured,monkeypatch):
    from workflow.production import preparation
    from scripts.produce_video import render_plan
    import os
    fonts=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arial.ttf',
           Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
           Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font=next((p for p in fonts if p.is_file()),fonts[0])
    if not font.is_file():pytest.skip('configured platform font unavailable')
    sound=configured/'sfx.wav'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i',
                    'sine=frequency=880:sample_rate=48000','-t','0.2',str(sound)],check=True)
    creative=json.loads((configured/'creative.json').read_text());creative['lines'][0]['visible_zh']='试一下'
    creative['production']['packaging']=[{'line':1,'font':str(font),'font_sha256':digest(font),'font_size':24,'x':8,'y':200}]
    creative['production']['sound_effects']=[{'path':str(sound),'sha256':digest(sound),'start':0,'duration':0.2,
                                             'volume':0.1,'description_zh':'测试提示音'}]
    save(configured/'creative.json',creative)
    loc=json.loads((configured/'localization-v0001.json').read_text());loc['segments'][0]['visible_text']='Teste'
    save(configured/'localization-v0001.json',loc);rebind(configured)
    async def synth(*args):return fake_audio(*args)
    monkeypatch.setattr(preparation,'audio_stage',synth)
    plan=preparation.prepare_configured(configured)
    assert len(plan['overlays'])==len(plan['sound_effects'])==1
    result=render_plan(plan)
    assert Path(result['output']).is_file()
    assert any(item['sha256']==digest(font) for item in plan['bound_files'])


def test_actual_speech_overflow_stops_without_changing_approved_rate(configured,monkeypatch):
    from workflow.production import preparation
    creative=json.loads((configured/'creative.json').read_text());creative['lines'][0]['window']=[0,0.2]
    save(configured/'creative.json',creative)
    loc=json.loads((configured/'localization-v0001.json').read_text());loc['segments'][0]['window']=[0,0.2]
    save(configured/'localization-v0001.json',loc);rebind(configured)
    async def synth(*args):return fake_audio(*args)
    monkeypatch.setattr(preparation,'audio_stage',synth)
    with pytest.raises(ValueError,match='approved window'):preparation.prepare_configured(configured)
    assert json.loads((configured/'voice-selection.json').read_text())['rate']=='+0%'
    assert not (configured/'execution.json').exists()
