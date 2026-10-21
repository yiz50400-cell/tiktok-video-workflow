#!/usr/bin/env python3
"""Verify a saved edit and generate visual review sheets; metrics are not listening approval."""
from pathlib import Path
import argparse,json,subprocess,re,hashlib,sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import imageio_ffmpeg
from workflow.production.qa import measure_audio
from workflow.analysis.core import probe
from scripts.produce_video import validate_render_input,build_srt
from scripts.prepare_packaged_video import absolute_plan
FF=imageio_ffmpeg.get_ffmpeg_exe()
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(d):
 plan=json.loads((d/'execution.json').read_text(encoding='utf-8'))
 actual=absolute_plan(plan)
 validate_render_input(actual); video=ROOT/plan['output']; metadata=probe(video); metrics=measure_audio(video,d/'final-audio.log')
 result=subprocess.run([FF,'-hide_banner','-nostdin','-i',str(video),'-vf','blackdetect=d=0.2:pix_th=0.1,freezedetect=n=0.003:d=0.8','-af','silencedetect=noise=-60dB:d=0.08','-f','null','-'],capture_output=True,check=True)
 log=result.stderr.decode(errors='replace');(d/'decode-black-freeze-silence.log').write_text(log, encoding='utf-8')
 black=re.findall(r'black_start:[^\r\n]+',log); freeze=re.findall(r'lavfi.freezedetect.[^\r\n]+',log)
 raw=subprocess.run([FF,'-v','error','-i',str(video),'-vf','scale=54:96,format=gray','-an','-f','rawvideo','-'],capture_output=True,check=True).stdout
 size=54*96; means=[sum(raw[i:i+size])/size for i in range(0,len(raw),size)]
 flashes=[i for i in range(1,len(means)-1) if abs(means[i]-means[i-1])>35 and abs(means[i]-means[i+1])>35]
 cut_frames=[]
 for w in plan['windows'][1:]: cut_frames.extend(range(round(w['timeline_start']*60)-2,round(w['timeline_start']*60)+2))
 for t in plan.get('source_continuity_review',{}).get('internal_cut_seconds',[]): cut_frames.extend(range(round(t*60)-2,round(t*60)+2))
 cut_frames.extend([0,1,len(means)-2,len(means)-1]); select='+'.join(f'eq(n\\,{n})' for n in cut_frames)
 for filename,filters in [('cut-review.jpg',f"select='{select}',scale=216:384,tile=8x4"),('action-review.jpg','fps=5,scale=144:256,tile=10x8'),('contact-sheet.jpg','fps=1,scale=216:384,tile=5x3')]:
  subprocess.run([FF,'-v','error','-i',str(video),'-vf',filters,'-frames:v','1','-y',str(d/filename)],check=True)
 data=video.read_bytes(); faststart=0<=data.find(b'moov')<data.find(b'mdat')
 if (ROOT/plan['srt']).read_text(encoding='utf-8')!=build_srt(plan['subtitle_cues']): raise ValueError('subtitle differs from execution plan')
 passed=not black and not freeze and not flashes and faststart and abs(len(means)/60-plan['duration'])<=1/60 and metrics['clipped_pcm_samples']==0 and metadata['width']==plan.get('width',1080) and metadata['height']==plan.get('height',1920) and metadata['video_codec']=='h264'
 qa={'status':'technical_checks_passed_pending_visual_review' if passed else 'needs_review','video_sha256':digest(video),'execution_sha256':digest(d/'execution.json'),'video':{**metadata,'full_decode':'passed','frames':len(means),'measured_seconds':len(means)/60,'faststart':faststart},'audio':metrics,'timing':{'voice_windows_fit':True,'rate':json.loads((d/'voice-selection.json').read_text(encoding='utf-8'))['rate'],'speech_end_seconds':plan['audio_cues'][-1]['measured_end'],'subtitle_cues':len(plan['subtitle_cues']),'srt_matches_plan':True},'visual':{'black_intervals':black,'freeze_events':freeze,'isolated_luma_flash_candidates':flashes,'sampled_review':'pending','full_speed_human_playback':False},'sound_quality':{'voice_pool':'user_approved','independent_listening':'not_available_to_agent','native_human_review':False},'scope':{'chinese_user_approval':'approved_exact_bound_version','localization_internal_review':'passed','foreign_text_user_review':'waived_not_user_reviewed','commercial_license':'unverified','publishing':'not_authorized','final_user_acceptance':'pending'}}
 (d/'qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n', encoding='utf-8'); print(json.dumps(qa,ensure_ascii=False,indent=2))
 if not passed: raise ValueError('actual output requires review')
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path); args=parser.parse_args();verify(args.directory.resolve())
