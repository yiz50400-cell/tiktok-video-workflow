"""Prepare a product edit solely from its approved production configuration."""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import sys
import wave

from workflow.publishing.prepare import digest, read_json, write_json
from workflow.orchestration.local import preflight
from workflow.production.voices import select_voice, validate_recording
from workflow.analysis.core import probe
from scripts.produce_video import validate_render_input


async def audio_stage(*args):
    from scripts.prepare_packaged_video import audio_stage as generate
    return await generate(*args)


def evidence(directory):
    directory=Path(directory).resolve()
    candidates=sorted(directory.glob('localization-v[0-9][0-9][0-9][0-9].json'))
    if not candidates:
        raise ValueError('localization evidence is missing')
    loc_path=candidates[-1]
    version=loc_path.stem.rsplit('-',1)[-1]
    audit_path=directory/f'localization-audit-{version}.json'
    if not audit_path.exists() and version=='v0001':
        audit_path=directory/'localization-audit.json'
    preflight({'plan':str(directory/'execution.json'),'localization':str(loc_path),'audit':str(audit_path)})
    creative=read_json(directory/'creative.json'); loc=read_json(loc_path); audit=read_json(audit_path)
    config=creative['production']
    if (loc.get('creative_sha256')!=digest(directory/'creative.json')
            or (loc.get('market'),loc.get('locale'))!=(creative['market'],creative['locale'])
            or (audit.get('market'),audit.get('locale'))!=(creative['market'],creative['locale'])
            or not audit.get('reviewer') or audit['reviewer']==creative.get('author')):
        raise ValueError('localization market, locale or independent reviewer mismatch')
    if len(creative['lines'])!=len(loc['segments']) or not loc['segments']:
        raise ValueError('localized segment count does not match approved Chinese lines')
    previous=0
    for index,(line,segment) in enumerate(zip(creative['lines'],loc['segments']),1):
        a,b=line['window']
        if (segment['line']!=index or segment['window']!=line['window']
                or not all(type(v) in (float,int) and math.isfinite(v) for v in (a,b))
                or not previous<=a<b<=config['duration'] or not segment['text'].strip()):
            raise ValueError('localized window differs from approved Chinese window')
        if bool(line.get('visible_zh'))!=bool(segment.get('visible_text')):
            raise ValueError('localized packaging differs from approved Chinese packaging')
        previous=b
    # Validate media and all packaging before the first network request.
    for item in config['sources']+config.get('sound_effects',[]):
        path=Path(item['path'])
        if not path.is_absolute() or digest(path)!=item['sha256']:
            raise ValueError('production source hash mismatch or non-absolute path')
    for sound in config.get('sound_effects',[]):
        values=[sound.get(key) for key in ('start','duration','volume')]
        if (any(type(value) not in (int,float) or not math.isfinite(value) for value in values)
                or sound['start']<0 or sound['duration']<=0 or sound['start']+sound['duration']>config['duration']
                or not 0<sound['volume']<=1):raise ValueError('invalid packaging sound timing/volume')
    metadata=[probe(Path(item['path'])) for item in config['sources']]
    cursor=0
    for shot in config['windows']:
        index=shot.get('source_index',0)
        if (type(index) is not int or not 0<=index<len(metadata)
                or not 0<=shot['source_start']<shot['source_end']<=metadata[index]['duration_seconds']+.02
                or not math.isclose(shot['timeline_start'],cursor,abs_tol=1e-6)
                or not math.isclose(shot['source_end']-shot['source_start'],shot['timeline_end']-cursor,abs_tol=1e-6)):
            raise ValueError('invalid approved shot/source window')
        cursor=shot['timeline_end']
    if not math.isclose(cursor,config['duration'],abs_tol=1e-6):
        raise ValueError('production duration differs from approved shots')
    from workflow.production.continuity import validate_continuity
    validate_continuity(config)
    for key,default in [('width',1080),('height',1920)]:
        value=config.get(key,default)
        if type(value) is not int or value<=0 or value%2:
            raise ValueError('invalid output dimensions')
    encoder=config.get('encoder','libx264')
    if encoder not in ('libx264','h264_videotoolbox'):
        raise ValueError('unsupported encoder')
    if encoder=='h264_videotoolbox' and sys.platform!='darwin':
        # VideoToolbox exists only on macOS; fail before voice/render work starts.
        raise ValueError('h264_videotoolbox 仅限 macOS；请在新制作配置中选择 libx264')
    pool=Path(config['voice_approval'])
    if not pool.is_absolute() or digest(pool)!=config['voice_approval_sha256']:
        raise ValueError('voice approval hash mismatch')
    return creative,config,loc,loc_path,audit_path


def reserve_voice(directory, batch_state_path=None):
    creative,config,loc,loc_path,audit_path=evidence(directory)
    options={**config.get('voice_options',{}),'approval_scope':'market_pool',
             'market':creative['market'],'locale':creative['locale']}
    selection=Path(directory)/'voice-selection.json'
    if batch_state_path is not None:
        options['batch_state_path']=str(Path(batch_state_path).resolve())
    elif selection.exists():
        saved=read_json(selection)
        if saved.get('batch_state_path'):
            options['batch_state_path']=saved['batch_state_path']
    return select_voice(config['voice_approval'],selection,creative['job_id'],creative['ordinal'],
                        localization_sha256=digest(loc_path),**options)


def _overlays(directory, config, loc):
    from PIL import Image, ImageDraw, ImageFont
    result=[]; root=directory/'packaging'; root.mkdir(exist_ok=True)
    for index,style in enumerate(config.get('packaging',[])):
        if (type(style.get('line')) is not int or not 1<=style['line']<=len(loc['segments'])
                or any(type(style.get(k)) is not int or style[k]<0 for k in ('x','y'))):
            raise ValueError('invalid approved packaging position or line')
        segment=loc['segments'][style['line']-1]
        if not segment.get('visible_text'):
            raise ValueError('packaging requires reviewed visible_text')
        font_path=Path(style['font'])
        if not font_path.is_absolute() or digest(font_path)!=style.get('font_sha256'):
            raise ValueError('packaging font must bind approved file hash')
        font=ImageFont.truetype(str(font_path),style.get('font_size',64))
        text=segment['visible_text']; bounds=font.getbbox(text,stroke_width=2)
        width=bounds[2]-bounds[0]+20; height=bounds[3]-bounds[1]+20
        if width>config.get('width',1080)-style['x'] or height>config.get('height',1920)-style['y']:
            raise ValueError('packaging exceeds approved visible region')
        im=Image.new('RGBA',(width,height),(0,0,0,0)); draw=ImageDraw.Draw(im)
        draw.text((10-bounds[0],10-bounds[1]),text,font=font,fill=style.get('color','#ffffff'),
                  stroke_width=2,stroke_fill='#242024')
        path=root/f'text-{index+1:02d}.png';im.save(path)
        result.append({'path':str(path),'sha256':digest(path),'start':segment['window'][0],
                       'end':segment['window'][1],'x':style['x'],'y':style['y']})
    return result


def prepare_configured(directory):
    import numpy as np
    from scripts.prepare_packaged_video import speech_bounds
    directory=Path(directory).resolve()
    creative,config,loc,loc_path,audit_path=evidence(directory)
    selected=reserve_voice(directory)
    overlays=_overlays(directory,config,loc)
    rows=asyncio.run(audio_stage(directory,loc,selected))
    if len(rows)!=len(loc['segments']):raise ValueError('TTS did not return every approved line')
    trimmed=[]; cues=[]; subtitles=[]; checks=[]; all_words=[]; cursor=0
    for row,segment in zip(rows,loc['segments']):
        values,start,end,words=speech_bounds(Path(row['path']),Path(row['metadata_path']))
        clip=values[round(start*48000):round(end*48000)]; length=len(clip)/48000
        a,b=segment['window']; delay=round((a+.025)*1000)
        if delay/1000+length>b:
            raise ValueError(f'line {row["line"]} needs {length:.3f}s; approved window {b-a:.3f}s')
        cues.append({'source_start':cursor,'source_end':cursor+length,'delay_ms':delay,
                     'measured_start':delay/1000+.015,'measured_end':delay/1000+length-.015,'text':segment['text']})
        groups=[]; group=[]
        for word in words:
            group.append(word)
            if len(group)>=5 or word['text'].endswith(('.',',','?','!')):
                groups.append(group);group=[]
        if group:groups.append(group)
        for i,g in enumerate(groups):
            ws=.015 if i==0 else max(.015,g[0]['offset']/1e7-start)
            we=length-.015 if i==len(groups)-1 else min(length-.015,(g[-1]['offset']+g[-1]['duration'])/1e7-start)
            if we>ws:subtitles.append([round(delay/1000+ws,6),round(delay/1000+we,6),' '.join(w['text'] for w in g)])
        if not groups:
            raise ValueError('TTS word boundaries missing; cannot create synchronized subtitles')
        all_words.extend({**w,'offset':round((w['offset']/1e7-start+cursor)*1e7),'line':row['line']} for w in words)
        checks.append({'line':row['line'],'speech_duration_with_padding':length,
                       'timeline_start':delay/1000,'timeline_end':delay/1000+length,
                       'audio_sha256':digest(row['path']),'metadata_sha256':digest(row['metadata_path'])})
        trimmed.append(clip);cursor+=length
    voice=directory/'voice-combined.wav'; metadata=directory/'word-boundaries-combined.jsonl'
    with wave.open(str(voice),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(48000)
        stream.writeframes(np.clip(np.concatenate(trimmed)*32767,-32768,32767).astype('<i2').tobytes())
    metadata.write_text(''.join(json.dumps(w,ensure_ascii=False)+'\n' for w in all_words), encoding='utf-8')
    recording={**{k:selected[k] for k in ('voice','rate','volume','pitch','localization_sha256')},
               'audio_sha256':digest(voice),'voice_selection_sha256':digest(directory/'voice-selection.json'),
               'word_metadata_sha256':digest(metadata),'actual_line_checks':checks,
               'independent_listening':'pending','native_human_review':False}
    write_json(directory/'voice-recording.json',recording)
    validate_recording(selected,recording,digest(voice),selection_sha256=digest(directory/'voice-selection.json'),
                        word_metadata_sha256=digest(metadata))
    write_json(directory/'audio-fit.json',{'checks':checks,'human_listening':'pending',
                'sentence_gaps_seconds':[round(cues[i+1]['measured_start']-cues[i]['measured_end'],3) for i in range(len(cues)-1)]})
    bounds=[directory/name for name in ('creative.json','zh-approval.json','voice-selection.json','voice-recording.json','audio-fit.json')]
    bounds += [loc_path,audit_path,Path(config['voice_approval']),metadata]
    bounds += [Path(row[key]) for row in rows for key in ('path','metadata_path')]
    bounds += [Path(style['font']) for style in config.get('packaging',[])]
    options={**config.get('voice_options',{}),'market':creative['market'],'locale':creative['locale'],'approval_scope':'market_pool'}
    if selected.get('batch_state_path'):options['batch_state_path']=selected['batch_state_path']
    plan={'job_id':creative['job_id'],'ordinal':creative['ordinal'],'version':creative['version'],
          'sources':config['sources'],'windows':config['windows'],'duration':config['duration'],
          'width':config.get('width',1080),'height':config.get('height',1920),'encoder':config.get('encoder','libx264'),
          'voice_audio':str(voice),'voice_audio_sha256':digest(voice),'voice_windows':[s['window'] for s in loc['segments']],
          'audio_cues':cues,'subtitle_cues':subtitles,'overlays':overlays,
          'sound_effects':config.get('sound_effects',[]),'output':str(directory/'成片.mp4'),
          'srt':str(directory/f'字幕-{creative["locale"]}.srt'),
          'bound_files':[{'path':str(p),'sha256':digest(p)} for p in bounds],
          'voice_approval':config['voice_approval'],'voice_selection':str(directory/'voice-selection.json'),
          'voice_recording':str(directory/'voice-recording.json'),'word_metadata':str(metadata),
          'localization_sha256':digest(loc_path),'voice_options':options,
          'user_final_acceptance':'pending','commercial_license':creative.get('commercial_license','unverified')}
    from workflow.production.continuity import validate_continuity
    plan['source_continuity_review']=validate_continuity(config)
    validate_render_input(plan);write_json(directory/'execution.json',plan)
    write_json(directory/'preparation-timing.json',{'tts_total_lines':len(rows),'tts_reused_lines':sum(r['reused'] for r in rows)})
    return plan
