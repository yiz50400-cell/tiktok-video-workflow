#!/usr/bin/env python3
"""Prepare an approved five-window product edit with reusable per-line TTS caches.

No drafting, approval, publishing or paid generation is performed by this entry.
"""
from pathlib import Path
import argparse,asyncio,hashlib,json,subprocess,sys,time
import edge_tts,imageio_ffmpeg
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from workflow.production.voices import select_voice,validate_recording
from workflow.production.qa import measure_audio
from scripts.produce_video import validate_render_input
FF=imageio_ffmpeg.get_ffmpeg_exe()

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
def run(args):
 r=subprocess.run([FF,'-hide_banner','-nostdin',*args],capture_output=True)
 if r.returncode: raise ValueError(r.stderr.decode(errors='replace')[-3500:])
 return r

def relative(path): return str(path.resolve().relative_to(ROOT))

def ref(path):
    """Workspace-relative when possible; absolute for configured files outside the project."""
    path=Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)

# Historical (template-less) BR sample only. New jobs use creative.production with an
# explicit font file + SHA256 and voice_approval + SHA256 (workflow/production/preparation.py).
LEGACY_VOICE_APPROVAL=Path('projects/sheglam-color-bloom/creation-dev/br-representatives/01-boundary/voice-review-v0002/voice-approval.json')

def historical_voice_approval():
    """WORKFLOW_VOICE_APPROVAL > <WORKFLOW_DATA_DIR>/voice-pools/br/voice-approval.json > dev sample path."""
    import os
    explicit=os.environ.get('WORKFLOW_VOICE_APPROVAL')
    data=Path(os.environ.get('WORKFLOW_DATA_DIR') or ROOT/'.runtime')
    candidates=[Path(explicit)] if explicit else [data/'voice-pools'/'br'/'voice-approval.json', ROOT/LEGACY_VOICE_APPROVAL]
    for candidate in candidates:
        if candidate.is_file(): return candidate.resolve()
    raise ValueError('未找到巴西音色池批准文件；请设置 WORKFLOW_VOICE_APPROVAL，或放到 '
                     + str(data/'voice-pools'/'br'/'voice-approval.json')
                     + '。新任务请在 creative.production 中绑定 voice_approval 及其 SHA256。')

def historical_font():
    """WORKFLOW_PACKAGING_FONT, else the platform's Arial Bold equivalent."""
    import os
    explicit=os.environ.get('WORKFLOW_PACKAGING_FONT')
    candidates=[Path(explicit)] if explicit else [
        Path('/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
        Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'arialbd.ttf',
        Path('/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf')]
    for candidate in candidates:
        if candidate.is_file(): return candidate
    raise ValueError('历史包装字体不可用；请设置 WORKFLOW_PACKAGING_FONT 指向本机字体文件，或建立绑定本机字体文件及哈希的新制作配置并确认中文方案')

def source_windows(d,source,spans):
    clips=sorted((d/'source-clips').glob('*.mp4'))
    if clips and len(clips)!=len(spans):
        raise ValueError('source clip count does not match approved spans')
    sources=[{'path':relative(path),'sha256':sha(path)} for path in (clips or [source])]
    windows=[]; timeline=0
    for index,(a,b) in enumerate(spans):
        duration=round(b-a,6); end=round(timeline+duration,6)
        window={'source_start':0 if clips else a,'source_end':duration if clips else b,
                'timeline_start':timeline,'timeline_end':end}
        if clips: window['source_index']=index
        windows.append(window); timeline=end
    return sources,windows,round(timeline,6)

def absolute_plan(plan):
    actual = json.loads(json.dumps(plan))
    for key in ('source','voice_audio','output','srt','voice_approval','voice_selection',
                'voice_recording','word_metadata'):
        if actual.get(key): actual[key] = str((ROOT / actual[key]).resolve())
    for collection in ('sources','bound_files','overlays','sound_effects'):
        for item in actual.get(collection, []):
            item['path'] = str((ROOT / item['path']).resolve())
    return actual

def load_template(d, loc_path):
    path=d/'approved-template.json'
    if not path.exists(): return None
    template=json.loads(path.read_text(encoding='utf-8'))
    validate_render_input(absolute_plan(template))
    if template.get('localization_sha256') != sha(loc_path):
        raise ValueError('approved template does not bind current localization')
    for name in ('creative.json','zh-approval.json','voice-selection.json','edit-layout.json'):
        current=d/name
        matches=[item for item in template['bound_files'] if Path(item['path']).name==name]
        if not current.exists() or len(matches)!=1 or sha(current)!=matches[0]['sha256']:
            raise ValueError(f'approved template does not bind current {name}')
    return template

def verify_approvals(d,loc_path,audit_path):
 zh=json.loads((d/'zh-approval.json').read_text(encoding='utf-8')); audit=json.loads(audit_path.read_text(encoding='utf-8'))
 if zh.get('status')!='approved' or zh.get('version_sha256')!=sha(d/'creative.json'):
  raise ValueError('Chinese approval does not bind current creative')
 if (audit.get('status')!='passed_independent_internal_text_review'
     or audit.get('localization_sha256')!=sha(loc_path)
     or audit.get('creative_sha256')!=sha(d/'creative.json')
     or audit.get('zh_approval_sha256')!=sha(d/'zh-approval.json')
     or audit.get('material_changes_to_approved_meaning')!=[]):
  raise ValueError('internal localization audit does not bind current approved meaning')
 return audit

def protect_existing_version(d,loc_path):
 execution=d/'execution.json'
 if not execution.exists(): return
 old=json.loads(execution.read_text(encoding='utf-8'))
 if not (ROOT/old['output']).exists(): return
 if old['localization_sha256']!=sha(loc_path):
  raise ValueError('existing output requires a new numbered version for changed localization')
 actual=absolute_plan(old)
 try: validate_render_input(actual)
 except (ValueError,OSError) as error:
  raise ValueError('existing output requires a new numbered version for changed assets') from error

async def audio_stage(d,loc,sel):
 from aiohttp import WSServerHandshakeError,ClientConnectionError
 # Compatibility route verified against the public Edge service in the 06 sample.
 import edge_tts.communicate
 from edge_tts.constants import TRUSTED_CLIENT_TOKEN
 edge_tts.communicate.WSS_URL='wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key='+TRUSTED_CLIENT_TOKEN
 line_dir=d/'line-audio'; line_dir.mkdir(exist_ok=True); semaphore=asyncio.Semaphore(3)
 async def generate(index,seg):
  audio=line_dir/f'{index:02d}.mp3'; metadata=line_dir/f'{index:02d}.jsonl'; cache=line_dir/f'{index:02d}-cache.json'
  inputs={'text':seg['text'],**{k:sel[k] for k in ('voice','rate','volume','pitch')}}
  if cache.exists():
   try:
    record=json.loads(cache.read_text(encoding='utf-8'))
   except (ValueError,OSError):
    record={}
   if isinstance(record,dict) and record.get('inputs')==inputs and audio.exists() and metadata.exists() and record.get('audio_sha256')==sha(audio) and record.get('metadata_sha256')==sha(metadata):
    return {'line':index,'reused':True,'path':str(audio),'metadata_path':str(metadata)}
  async with semaphore:
   comm=edge_tts.Communicate(seg['text'],sel['voice'],rate=sel['rate'],volume=sel['volume'],pitch=sel['pitch'],boundary='WordBoundary',connect_timeout=15,receive_timeout=25)
   audio_tmp=audio.with_suffix('.partial.mp3'); meta_tmp=metadata.with_suffix('.partial.jsonl')
   await asyncio.wait_for(comm.save(str(audio_tmp),str(meta_tmp)),timeout=60)
   audio_tmp.replace(audio); meta_tmp.replace(metadata)
   save(cache,{'inputs':inputs,'audio_sha256':sha(audio),'metadata_sha256':sha(metadata)})
   return {'line':index,'reused':False,'path':str(audio),'metadata_path':str(metadata)}
 # Finish outstanding work before retrying so a throttled service receives
 # one request at a time, and successful lines remain reusable on any failure.
 rows=await asyncio.gather(*[generate(i,s) for i,s in enumerate(loc['segments'],1)],return_exceptions=True)
 for offset,result in enumerate(rows):
  if not isinstance(result,Exception): continue
  error=result
  for attempt in range(2):
   retryable=(isinstance(error,(TimeoutError,ClientConnectionError)) or
              isinstance(error,WSServerHandshakeError) and (error.status==429 or error.status>=500))
   if not retryable: raise error
   delay=2 if attempt==0 else 5
   if isinstance(error,WSServerHandshakeError):
    try: delay=max(delay,min(15,float((error.headers or {}).get('Retry-After',0))))
    except (ValueError,TypeError): pass
   await asyncio.sleep(delay)
   try:
    rows[offset]=await generate(offset+1,loc['segments'][offset])
    break
   except (TimeoutError,ClientConnectionError,WSServerHandshakeError) as failure:
    error=failure
  else:
   status=getattr(error,'status',type(error).__name__)
   raise RuntimeError(f'TTS line {offset+1} failed after 3 attempts ({status}); successful line caches preserved') from None
 save(d/'line-generation.json',rows)
 return rows

def speech_bounds(audio,metadata):
 import numpy as np
 pcm=run(['-v','error','-i',str(audio),'-vn','-ac','1','-ar','48000','-f','f32le','-']).stdout
 values=np.frombuffer(pcm,dtype='<f4')
 active=np.where(np.abs(values)>0.001)[0]
 if len(active)==0: raise ValueError('TTS has no audible samples')
 words=[json.loads(x) for x in metadata.read_text(encoding='utf-8').splitlines() if x.strip()]
 words=[x for x in words if x['type']=='WordBoundary']
 # -60 dBFS PCM keeps the actual initial/final phonemes; include 35 ms padding.
 start=max(0,active[0]/48000-.035); end=min(len(values)/48000,(active[-1]+1)/48000+.035)
 return values,start,end,words

def prepare(d,audio_only=False):
 if 'production' in json.loads((d/'creative.json').read_text(encoding='utf-8')):
  from workflow.production.preparation import prepare_configured
  return prepare_configured(d)
 started=time.perf_counter()
 creative=json.loads((d/'creative.json').read_text(encoding='utf-8')); zh=json.loads((d/'zh-approval.json').read_text(encoding='utf-8'))
 loc_path=sorted(d.glob('localization-v[0-9][0-9][0-9][0-9].json'))[-1]
 version=loc_path.stem.split('-')[-1]
 audit_path=d/f'localization-audit-{version}.json'
 if not audit_path.exists() and version=='v0001': audit_path=d/'localization-audit.json'
 loc=json.loads(loc_path.read_text(encoding='utf-8')); audit=json.loads(audit_path.read_text(encoding='utf-8'))
 audit=verify_approvals(d,loc_path,audit_path)
 protect_existing_version(d,loc_path)
 template=load_template(d,loc_path)
 font_path=str(historical_font()) if template is None else None
 app=historical_voice_approval()
 options={'approval_scope':'market_pool','rate':'+15%'}
 selection_path=d/'voice-selection.json'
 if selection_path.exists() and json.loads(selection_path.read_text(encoding='utf-8'))['localization_sha256'] != sha(loc_path):
  from workflow.production.voices import revise_localization_binding
  revise_localization_binding(selection_path,audit['previous_localization_sha256'],sha(loc_path),audit_path)
 # A locked selection from an earlier approved revision is authoritative.  Do
 # not add a new batch reservation to it; new videos get the rotating pool.
 persisted=json.loads(selection_path.read_text(encoding='utf-8')) if selection_path.exists() else None
 if persisted and persisted.get('batch_state_path'):
  options['batch_state_path']=persisted['batch_state_path']
  if persisted.get('prior_voice'): options['prior_voice']=persisted['prior_voice']
 sel=select_voice(app,selection_path,creative['job_id'],1,localization_sha256=sha(loc_path),**options)
 rows=asyncio.run(audio_stage(d,loc,sel)); print('selected',sel['voice'],'audio',rows,flush=True)
 import numpy as np
 import wave
 trimmed=[]; cues=[]; subtitles=[]; combined_words=[]; cursor=0; checks=[]
 for row,seg in zip(rows,loc['segments']):
  audio=Path(row['path']); metadata=Path(row['metadata_path'])
  values,start,end,words=speech_bounds(audio,metadata)
  first,last=round(start*48000),round(end*48000); clip=values[first:last]; length=len(clip)/48000
  a,b=seg['window']; delay=round((a+.025)*1000)
  if delay/1000+length>b: raise ValueError(f"line {row['line']} needs {length:.3f}s; window {b-a:.3f}s")
  cues.append({'source_start':cursor,'source_end':cursor+length,'delay_ms':delay,'measured_start':delay/1000+.015,'measured_end':delay/1000+length-.015,'text':seg['text']})
  # The audio boundaries are measured; word events provide clause anchors.
  groups=[]; group=[]
  for word in words:
   group.append(word)
   if len(group)>=5 or word['text'].endswith(('.',',','?','!')):
    groups.append(group); group=[]
  if group: groups.append(group)
  for gi,g in enumerate(groups):
   ws=max(0,g[0]['offset']/1e7-start); we=min(length,(g[-1]['offset']+g[-1]['duration'])/1e7-start)
   if gi==0: ws=.015
   if gi==len(groups)-1: we=length-.015
   if we>ws: subtitles.append([round(delay/1000+ws,6),round(delay/1000+we,6),' '.join(w['text'] for w in g)])
  for word in words: combined_words.append({**word,'offset':round((word['offset']/1e7-start+cursor)*1e7),'line':row['line']})
  checks.append({'line':row['line'],'decoded_duration_seconds':len(values)/48000,'trim_start':start,'trim_end':end,'speech_duration_with_padding':length,'timeline_start':delay/1000,'timeline_end':delay/1000+length,'source_audio_sha256':sha(audio),'word_metadata_sha256':sha(metadata),'clipped_pcm_samples':int(np.sum(np.abs(values)>=1))})
  trimmed.append(clip); cursor+=length
 save(d/'audio-fit.json',{'checks':checks,'sentence_gaps_seconds':[round(cues[i+1]['measured_start']-cues[i]['measured_end'],3) for i in range(4)],'timing_method':'PCM_-60dBFS_trim_35ms_padding_front_anchor_25ms','voice':sel['voice'],'rate':sel['rate'],'human_listening':'pending_user_playback'})
 print('fit',[(x['line'],round(x['speech_duration_with_padding'],3)) for x in checks],flush=True)
 if audio_only: return
 voice=d/'voice-combined.wav'
 samples=np.concatenate(trimmed)
 with wave.open(str(voice),'wb') as f:
  f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000); f.writeframes(np.clip(samples*32767,-32768,32767).astype('<i2').tobytes())
 meta=d/'word-boundaries-combined.jsonl'; meta.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in combined_words), encoding='utf-8')
 record={**{k:sel[k] for k in ('voice','rate','volume','pitch','localization_sha256')},'audio_sha256':sha(voice),'voice_selection_sha256':sha(d/'voice-selection.json'),'word_metadata_sha256':sha(meta),'actual_line_checks':checks,'independent_listening':'not_available_to_agent','native_human_review':False}
 save(d/'voice-recording.json',record); validate_recording(sel,record,sha(voice),selection_sha256=sha(d/'voice-selection.json'),word_metadata_sha256=sha(meta))
 from PIL import Image,ImageDraw,ImageFont
 overlays=[]; overlay_dir=d/'packaging'; overlay_dir.mkdir(exist_ok=True)
 # Historical templates reuse exact approved overlay assets. New cross-platform
 # jobs use production.packaging with an explicit font file and SHA256.
 for index,seg in enumerate(loc['segments'] if template is None else [],1):
  text=seg['visible_text']; chunks= [text]
  # Packaging text is derived from the internally reviewed localization; the
  # user-facing approval remains the Chinese source of truth.
  size=72 if index in (1,5) else 64
  font=ImageFont.truetype(font_path,size)
  while max(font.getlength(x) for x in chunks)>840:
   size-=2; font=ImageFont.truetype(font_path,size)
  im=Image.new('RGBA',(920,110*len(chunks)),(0,0,0,0)); draw=ImageDraw.Draw(im)
  for j,line in enumerate(chunks):
   color='#ff8fbd' if (index==2 and j==0) or (index==3 and j==1) or index==5 else '#ffffff'
   draw.text((460,10+j*100),line,font=font,anchor='mt',fill=color,stroke_width=4,stroke_fill='#242024')
  file=overlay_dir/f'text-{index:02d}.png'; im.save(file)
  # Titles over ceiling; action hints on chest, safely above platform UI.
  y=220 if index in (1,2,5) else 1410
  overlays.append({'path':relative(file),'sha256':sha(file),'start':seg['window'][0],'end':seg['window'][1],'x':80,'y':y})
 effects=[]
 layout=json.loads((d/'edit-layout.json').read_text(encoding='utf-8'))
 for name,duration in ([('click',.10),('swipe',.26),('chime',.30)] if template is None else []):
  anchor=layout['sfx_anchors'][name]
  t=np.arange(round(duration*48000))/48000
  if name=='click': sound=np.sin(2*np.pi*(1400*t-2000*t*t))*np.exp(-t*55)
  elif name=='swipe': sound=np.random.default_rng(4).normal(size=len(t)); sound=np.convolve(sound,np.ones(12)/12,mode='same')*np.sin(np.pi*t/duration)**2
  else: sound=(np.sin(2*np.pi*880*t)+.4*np.sin(2*np.pi*1320*t))*np.exp(-t*12)
  sound=sound/max(abs(sound))*.11
  file=overlay_dir/f'{name}.wav'
  with wave.open(str(file),'wb') as f:
   f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000); f.writeframes((sound*32767).astype('<i2').tobytes())
  effects.append({'path':relative(file),'sha256':sha(file),'start':anchor,'duration':duration,'volume':.55,'origin':'locally_synthesized'})
 # Source spans sum to the five exact approved windows, all at original speed.
 spans=layout['spans']
 if template:
  source=(ROOT/template['source']).resolve()
  sources=template.get('sources') or [{'path':template['source'],'sha256':template['source_sha256']}]
  windows=template['windows']; duration=template['duration']
  overlays=template.get('overlays',[]); effects=template.get('sound_effects',[])
 else:
  source=(d/'source-clean.mp4').resolve() if (d/'source-clean.mp4').exists() else (d/creative['source']).resolve()
  sources,windows,duration=source_windows(d,source,spans)
 bounds=[d/x for x in ('creative.json','zh-approval.json','voice-selection.json','voice-recording.json','audio-fit.json','word-boundaries-combined.jsonl','edit-layout.json')]+[app,loc_path,audit_path]
 bounds += [Path(row[k]) for row in rows for k in ('path','metadata_path')]
 if template:
  bounds.append(d/'approved-template.json')
  bounds += [(ROOT/item['path']).resolve() for item in template['bound_files']]
 prior_execution=json.loads((d/'execution.json').read_text(encoding='utf-8')) if (d/'execution.json').exists() else {}
 encoder=(template if template is not None else prior_execution).get('encoder','libx264')
 plan={'job_id':creative['job_id'],'ordinal':1,'version':3,'source':relative(source),'source_sha256':sha(source),'sources':sources,'windows':windows,'voice_audio':relative(voice),'voice_audio_sha256':sha(voice),'voice_windows':[s['window'] for s in loc['segments']],'audio_cues':cues,'subtitle_cues':subtitles,'duration':duration,'width':1080,'height':1920,'encoder':encoder,'output':relative(d/'竖屏样片.mp4'),'srt':relative(d/'字幕-pt-BR.srt'),'bound_files':[{'path':ref(p) if p==app else relative(p),'sha256':sha(p)} for p in bounds],'overlays':overlays,'sound_effects':effects,'voice_approval':ref(app),'voice_selection':relative(d/'voice-selection.json'),'voice_recording':relative(d/'voice-recording.json'),'word_metadata':relative(meta),'localization_sha256':sha(loc_path),'voice_options':{**options,**({'batch_state_path':relative(Path(options['batch_state_path']))} if options.get('batch_state_path') else {})},'user_final_acceptance':'pending','commercial_license':'unverified','revision_of':'production-v0002'}
 actual=absolute_plan(plan)
 validate_render_input(actual); save(d/'execution.json',plan)
 context={'product':'SHEGLAM Color Bloom Liquid Blush','market':'BR','employee':'current_codex_development_session','angle':'双颊试用与边缘观察','hook':creative['lines'][0]['zh_voiceover'],'chinese_lines':[x['zh_voiceover'] for x in creative['lines']],'sample_version':'production-v0002','revision_of':'production-v0001'}
 save(d/'usage-context.json',context)
 save(d/'preparation-timing.json',{'elapsed_seconds':round(time.perf_counter()-started,3),'tts_reused_lines':sum(row['reused'] for row in rows),'tts_total_lines':len(rows),'source_transcode':'reused_existing','final_render':'separate_render_command'})
 print('prepared',relative(d/'execution.json'),flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('directory',type=Path); parser.add_argument('--audio-only',action='store_true'); args=parser.parse_args(); prepare(args.directory.resolve(),args.audio_only)
