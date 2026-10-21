"""Catch cross-customer approvals, unreviewed audio admission and wrong native payloads."""
import hashlib,json
from pathlib import Path
import pytest
from workflow.production import voices,chatcut

def save(path,obj):
 path.write_text(json.dumps(obj));return hashlib.sha256(path.read_bytes()).hexdigest()

def pool(tmp_path):
 policy=tmp_path/'policy.json'
 ph=save(policy,{'customer_id':'customer-a','decision_authority':'user_delegated_to_workflow','source_turn_id':'human-turn','markets':['ID'],'new_voice_requires_internal_market_and_quality_review':True,'language_rules':{'ID':'id-ID'}})
 audio=tmp_path/'sample.wav';audio.write_bytes(b'fixture-audio')
 ah=hashlib.sha256(audio.read_bytes()).hexdigest()
 review=tmp_path/'review.json';rh=save(review,{'status':'passed_internal_voice_review','evidence_kind':'auditory_review','reviewer':'reviewer-a','market':'ID','locale':'id-ID','voice':'id-ID-GadisNeural','audio_sha256':ah,'checks':{k:True for k in ['target_language','market_fit','natural_delivery','no_distortion','product_fit']}})
 p=tmp_path/'pool.json';save(p,{'status':'internally_reviewed_voice_pool','customer_id':'customer-a','market':'ID','locale':'id-ID','delegation':{'path':str(policy),'sha256':ph},'reviewed_auditions':[{'voice':'id-ID-GadisNeural','file':'sample.wav','sha256':ah,'review':{'path':str(review),'sha256':rh}}]})
 return p,policy,review

def choose(p,out,**kw):
 return voices.select_voice(p,out,'job-a',1,localization_sha256='a'*64,approval_scope='market_pool',market='ID',locale='id-ID',customer_id=kw.get('customer_id','customer-a'))

def test_delegated_review_accepts_customer_scoped_evidence_and_locks_retry(tmp_path):
 p,_,_=pool(tmp_path);out=tmp_path/'selection.json';a=choose(p,out);b=choose(p,out)
 assert a==b and a['voice']=='id-ID-GadisNeural'
 assert a['decision_authority']=='user_delegated_to_workflow' and a['customer_id']=='customer-a'

@pytest.mark.parametrize('fault',['wrong-customer','tampered-delegation','metrics-only','tampered-audio','wrong-language'])
def test_delegated_review_blocks_unproven_or_cross_scope_selection(tmp_path,fault):
 p,policy,review=pool(tmp_path)
 if fault=='tampered-delegation':policy.write_text('{}')
 if fault=='tampered-audio':(tmp_path/'sample.wav').write_bytes(b'changed')
 if fault in ['metrics-only','wrong-language']:
  r=json.loads(review.read_text())
  if fault=='metrics-only':r['evidence_kind']='signal_metrics_only'
  else:r['locale']='pt-BR'
  rh=save(review,r);q=json.loads(p.read_text());q['reviewed_auditions'][0]['review']['sha256']=rh;save(p,q)
 with pytest.raises(ValueError):choose(p,tmp_path/'selection.json',customer_id='customer-b' if fault=='wrong-customer' else 'customer-a')
 assert not (tmp_path/'selection.json').exists()

def test_native_recipe_uses_proven_track_and_caption_contracts(tmp_path):
 fn=getattr(chatcut,'build_native_recipe',None)
 assert callable(fn),'shared native recipe compiler missing'
 plan={'fps':30,'duration_frames':60,'min_sources':1,'sources':[{'key':'s','sha256':'a'*64,'duration_seconds':3}],'shots':[{'asset_key':'s','start_frame':0,'duration_frames':60,'source_in_us':0}],'scenes':[{'asset_key':'v','start_frame':0,'duration_frames':60,'text':'Test voice.'}]}
 r=fn(plan,{'s':'source-id','v':'voice-id'},{'picture':'picture-track','voice':'voice-track'},caption_style={'fontFamily':'Inter'},output_dir=tmp_path)
 assert r['edit_preview']['validateOnly'] is True
 assert r['track_create']['picture']['json']=={'trackType':'video','count':1,'name':'商品画面'}
 ops=r['captions']['edits']
 assert {'action':'track','trackId':'picture-track','visible':False} in ops
 assert {'action':'track','trackId':'voice-track','visible':True} in ops
 assert r['export_video']['fps']==30 and r['export_video']['format']=='video'
 assert r['export_subtitles']['format']=='subtitles'

def test_market_probe_planner_keeps_philippine_languages_separate():
 from workflow import markets
 fn=getattr(markets,'plan_voice_probes',None)
 assert callable(fn),'market preflight planner missing'
 catalog={'MX':[{'ShortName':'es-MX-DaliaNeural','Locale':'es-MX'}],'PH':[{'ShortName':'fil-PH-BlessicaNeural','Locale':'fil-PH'},{'ShortName':'en-PH-RosaNeural','Locale':'en-PH'}]}
 rows=fn(catalog,{'es-MX':'Una prueba.','fil-PH':'Pagsubok ito.','en-PH':'A test.'})
 assert [(r['market'],r['locale']) for r in rows]==[('MX','es-MX'),('PH','fil-PH'),('PH','en-PH')]
 assert rows[1]['text']=='Pagsubok ito.'
 with pytest.raises(ValueError):fn({'ID':[{'ShortName':'pt-BR-FranciscaNeural','Locale':'pt-BR'}]},{'pt-BR':'Teste.'})
 with pytest.raises(ValueError):fn(catalog,{'es-MX':'Una prueba.'})

def test_cli_native_recipe_is_emitted_with_current_asset_bindings(tmp_path):
 import subprocess,sys
 root=Path(__file__).resolve().parents[1]
 p={'fps':30,'duration_frames':60,'min_sources':1,'sources':[{'key':'s','sha256':'a'*64,'duration_seconds':3}],'shots':[{'asset_key':'s','start_frame':0,'duration_frames':60,'source_in_us':0}],'scenes':[{'asset_key':'v','start_frame':0,'duration_frames':60,'text':'Test.'}]}
 for n,v in [('plan',p),('assets',{'s':'media-id','v':'speech-id'}),('tracks',{'picture':'V1','voice':'A1'}),('style',{'fontFamily':'Inter'})]:save(tmp_path/(n+'.json'),v)
 cmd=[sys.executable,str(root/'scripts/prepare_chatcut.py')]
 for n in ['plan','assets','tracks']:cmd+=['--'+n,str(tmp_path/(n+'.json'))]
 cmd+=['--output',str(tmp_path/'pack'),'--caption-style',str(tmp_path/'style.json'),'--export-dir',str(tmp_path/'delivery')]
 r=subprocess.run(cmd,capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 result=json.loads((tmp_path/'pack/native-session.json').read_text())
 assert result['edit_preview']['adds'][1]['assetId']=='speech-id'
 assert result['export_video']['outputPath']==str(tmp_path/'delivery/video.mp4')

def test_speech_fit_blocks_overrun_before_native_edit():
 fn=getattr(chatcut,'check_speech_fit',None)
 assert callable(fn),'early speech-fit guard missing'
 p={'fps':30,'scenes':[{'asset_key':'voice-01','start_frame':0,'duration_frames':171}]}
 rows=[{'asset_key':'voice-01','speech_seconds':5.125,'lead_seconds':.1,'tail_seconds':.15}]
 result=fn(p,rows);assert result[0]['spare_seconds']==pytest.approx(.325)
 rows[0]['speech_seconds']=5.725
 with pytest.raises(ValueError,match='voice-01'):fn(p,rows)
 rows[0]['speech_seconds']=float('nan')
 with pytest.raises(ValueError):fn(p,rows)
 with pytest.raises(ValueError):fn(p,[])

def test_market_probe_cli_dry_run_does_not_generate_audio(tmp_path):
 import subprocess,sys
 root=Path(__file__).resolve().parents[1]
 save(tmp_path/'catalog.json',{'candidates':{'ID':[{'ShortName':'id-ID-GadisNeural','Locale':'id-ID'}]}})
 save(tmp_path/'text.json',{'id-ID':'Ini tes suara.'})
 r=subprocess.run([sys.executable,str(root/'scripts/prepare_market_preflight.py'),'--catalog',str(tmp_path/'catalog.json'),'--texts',str(tmp_path/'text.json'),'--output',str(tmp_path/'probes')],capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 assert json.loads(r.stdout)['network_requests']==0
 assert not list((tmp_path/'probes').rglob('*.mp3'))


def test_boundary_captions_preserve_reviewed_text_and_reject_missing_words():
 fn=getattr(chatcut,'boundary_caption_cards',None)
 assert callable(fn),'verified word-boundary subtitle fallback missing'
 text='Pagsubok ito ng boses. Tingnan ang brush.'
 words=[{'type':'WordBoundary','text':t.strip('.'),'offset':i*5000000,'duration':3000000} for i,t in enumerate(text.split())]
 got=fn(text,words,duration_seconds=4,max_chars=24)
 assert ' '.join(r['text'].replace('\n',' ') for r in got)==text
 assert all(0<=r['start']<r['end']<=4 for r in got)
 assert all(len(line)<=24 for r in got for line in r['text'].splitlines())
 with pytest.raises(ValueError):fn(text,words[:-1],duration_seconds=4)
 words[1]['text']='WRONG'
 with pytest.raises(ValueError):fn(text,words,duration_seconds=4)


def test_boundary_captions_reject_bad_time_and_audio_overflow():
 fn=getattr(chatcut,'boundary_caption_cards',None)
 assert callable(fn)
 for offset,duration in [(-1,10),(0,-1),(0,0),(float('nan'),10),(0,50000000)]:
  with pytest.raises(ValueError):fn('Test.',[{'type':'WordBoundary','text':'Test','offset':offset,'duration':duration}],duration_seconds=1)
 with pytest.raises(ValueError):fn('Toolongword.',[{'type':'WordBoundary','text':'Toolongword','offset':0,'duration':1000000}],duration_seconds=1,max_chars=3)
