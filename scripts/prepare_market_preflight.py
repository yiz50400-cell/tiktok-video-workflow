#!/usr/bin/env python3
"""Batch technical voice probes; never grant auditory or customer approval.

Default is a validated plan only. --generate uses the project's existing TTS
adapter, at most three simultaneous probes, preserving successful hash caches.
"""
import argparse,asyncio,hashlib,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from workflow.markets import plan_voice_probes
from workflow.platforms.files import atomic_json

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for k in ['catalog','texts','output']:p.add_argument('--'+k,type=Path,required=True)
 p.add_argument('--generate',action='store_true');a=p.parse_args()
 catalog=json.loads(a.catalog.read_text());texts=json.loads(a.texts.read_text())
 rows=plan_voice_probes(catalog.get('candidates',catalog),texts)
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
 if not a.generate:
  print(json.dumps({'plan':rows,'network_requests':0},ensure_ascii=False));return
 from scripts.prepare_packaged_video import audio_stage,speech_bounds
 from workflow.production.qa import measure_audio
 began=time.time();qa_version=digest(ROOT/'workflow/production/qa.py')
 async def run():
  sem=asyncio.Semaphore(3)
  async def one(row):
   async with sem:
    started=time.time();d=out/row['market']/row['voice'];d.mkdir(parents=True,exist_ok=True)
    try:
     got=(await audio_stage(d,{'segments':[{'text':row['text']}]},row))[0]
     audio=Path(got['path']);sha=digest(audio);q=d/'technical-qa.json'
     previous=json.loads(q.read_text()) if q.exists() else {}
     reused=previous.get('audio_sha256')==sha and previous.get('qa_version')==qa_version
     if reused:metrics=previous['metrics']
     else:
      metrics=await asyncio.to_thread(measure_audio,audio,d/'loudness.log')
      atomic_json(q,{'audio_sha256':sha,'qa_version':qa_version,'metrics':metrics})
     good=(metrics['decoded_duration_seconds']>0 and metrics['clipped_pcm_samples']==0 and metrics['sample_peak_dbfs'] is not None and metrics['sample_peak_dbfs']>-45)
     return {**row,**got,'audio_sha256':sha,'metrics':metrics,'qa_reused':reused,'elapsed_seconds':time.time()-started,'technical_pass':good,'auditory_review':'pending_not_replaced_by_metrics','production_pool_admission':False}
    except Exception as e:
     return {**row,'technical_pass':False,'error_type':type(e).__name__,'elapsed_seconds':time.time()-started,'successful_other_caches_preserved':True}
  return await asyncio.gather(*(one(row) for row in rows))
 result={'scope':'technical_preflight_not_full_video_acceptance','started_epoch':began,'rows':asyncio.run(run())}
 result.update(completed_epoch=time.time(),wall_seconds=time.time()-began)
 logs=out/'runs';logs.mkdir(exist_ok=True);path=logs/(str(time.time_ns())+'.json');atomic_json(path,result)
 print(json.dumps({'report':str(path),'wall_seconds':result['wall_seconds'],'passed':sum(r['technical_pass'] for r in result['rows']),'total':len(rows),'audio_cache_hits':sum(r.get('reused',False) for r in result['rows']),'rows':[{k:r.get(k) for k in ['market','locale','voice','path','technical_pass','elapsed_seconds','error_type']} for r in result['rows']]},ensure_ascii=False))
 if not all(r['technical_pass'] for r in result['rows']):sys.exit(1)
if __name__=='__main__':main()
