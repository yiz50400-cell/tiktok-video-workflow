"""Create and verify a local video/subtitle/Chinese delivery bundle."""
from pathlib import Path
import json
import os
import shutil
import tempfile

from workflow.publishing.prepare import digest, read_json, write_json
from scripts.produce_video import validate_render_input, build_srt
from scripts.run_local_batch import _actual_plan, latest_localization
from workflow.orchestration.local import preflight


def verify_production(directory, job_id, ordinal, market, locale):
    directory=Path(directory).resolve()
    loc,audit=latest_localization(directory)
    preflight({'plan':str(directory/'execution.json'),'localization':str(loc),'audit':str(audit)})
    creative=read_json(directory/'creative.json'); translation=read_json(loc)
    execution=read_json(directory/'execution.json'); qa=read_json(directory/'qa.json')
    if (creative.get('job_id')!=job_id or execution.get('job_id')!=job_id
            or execution.get('ordinal')!=ordinal
            or (translation.get('market'),translation.get('locale'))!=(market,locale)
            or translation.get('creative_sha256')!=digest(directory/'creative.json')
            or execution.get('localization_sha256')!=digest(loc)):
        raise ValueError('制作记录与任务、序号或目标市场不匹配')
    actual=_actual_plan(execution); validate_render_input(actual)
    for path in (directory/'creative.json',directory/'zh-approval.json',loc,audit):
        if not any(Path(item['path']).resolve()==path.resolve() and item['sha256']==digest(path)
                   for item in actual.get('bound_files',[])):
            raise ValueError('执行清单缺少中文或内部审核的绑定证据')
    video=Path(actual['output']); srt=Path(actual['srt'])
    if (qa.get('status') not in ('technical_checks_passed_pending_visual_review','technical_checks_passed')
            or qa.get('video_sha256')!=digest(video)
            or qa.get('execution_sha256')!=digest(directory/'execution.json')
            or qa.get('video',{}).get('full_decode')!='passed'
            or srt.read_text(encoding='utf-8')!=build_srt(execution['subtitle_cues'])):
        raise ValueError('成片或字幕未通过当前版本技术检查')
    return creative,execution,qa,video,srt,loc,audit


def deliver(directory, destination, *, job_id, ordinal, market, locale):
    destination=Path(destination).resolve()
    creative,execution,qa,video,srt,loc,audit=verify_production(directory,job_id,ordinal,market,locale)
    directory=Path(directory).resolve()
    sources={'成片.mp4':video,f'字幕-{locale}.srt':srt}
    evidence=[directory/name for name in ('creative.json','zh-approval.json','execution.json','qa.json')]+[loc,audit]
    for path in evidence:sources['internal/'+path.name]=path
    for name in ('voice-selection.json','voice-recording.json','audio-fit.json','final-independent-review.json'):
        if (directory/name).exists():sources['internal/'+name]=directory/name
    fingerprint=digest(directory/'execution.json')+':'+':'.join(digest(p) for p in sources.values())
    if destination.exists():
        manifest=read_json(destination/'manifest.json')
        if manifest.get('input_fingerprint')!=fingerprint:
            raise ValueError('交付输入已变化，请使用新版本')
        for name,record in manifest['files'].items():
            if digest(destination/name)!=record['sha256']:
                raise ValueError('交付包损坏，请使用新版本')
        return {'package_path':str(destination),'reused':True,'manifest':str(destination/'manifest.json')}
    if any(p.is_relative_to(destination) for p in sources.values()):
        raise ValueError('交付目录不能覆盖来源文件')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix=destination.name+'.partial-',dir=destination.parent))
    try:
        for name,path in sources.items():
            target=temporary/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        lines=creative.get('lines') or creative.get('script_lines')
        chinese=[line.get('zh') or line.get('zh_voiceover') or line.get('text') for line in lines]
        if not chinese or any(not isinstance(text,str) or not text for text in chinese):
            raise ValueError('缺少准确中文稿，不能交付')
        (temporary/'中文口播.txt').write_text('\n\n'.join(chinese)+'\n', encoding='utf-8')
        explanation=(f'# 商品视频本机交付\n\n任务：{job_id}；第{ordinal}条；市场：{market}。\n\n'
                     f'时长：{execution["duration"]}秒。中文稿已确认；目标语言已内部复核，外语全文免用户审阅。\n\n'
                     '成片和独立字幕已通过技术检查。真实音频听感、完整播放及用户最终认可按单独审核记录确认。\n\n'
                     f'素材商业许可：{qa.get("scope",{}).get("commercial_license","unverified")}。\n\n'
                     '员工后续自行在原账号环境上传、选择商品并发布。\n')
        (temporary/'交付说明.md').write_text(explanation, encoding='utf-8')
        for name,path in sources.items():
            if digest(temporary/name)!=digest(path):raise ValueError('交付期间来源变化')
        manifest={'schema_version':1,'job_id':job_id,'ordinal':ordinal,'market':market,'locale':locale,
                  'input_fingerprint':fingerprint,'final_user_acceptance':qa.get('scope',{}).get('final_user_acceptance','pending'),
                  'files':{str(p.relative_to(temporary)):{'sha256':digest(p),'path':str(destination/p.relative_to(temporary))}
                           for p in temporary.rglob('*') if p.is_file()}}
        write_json(temporary/'manifest.json',manifest);os.rename(temporary,destination)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return {'package_path':str(destination),'manifest':str(destination/'manifest.json'),'reused':False}
