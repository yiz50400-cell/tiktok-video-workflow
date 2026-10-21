"""Optional isolated LaMa execution and explicit review of repaired material."""
import math
from pathlib import Path

from workflow.publishing.prepare import digest, read_json
from workflow.store import Asset


def validate_settings(settings):
    if not isinstance(settings,dict) or set(settings)-{'mask_spec','model','start','duration','device','review_path'}:
        raise ValueError('未知修补配置字段')
    for key in ('mask_spec','model'):
        if not settings.get(key) or not Path(settings[key]).is_absolute() or not Path(settings[key]).is_file():
            raise ValueError(f'修补{key}须为已有绝对路径')
    if settings.get('review_path') and (not Path(settings['review_path']).is_absolute()
                                      or not Path(settings['review_path']).is_file()):
        raise ValueError('修补复核须为已有绝对路径')
    for key in ('start','duration'):
        v=settings.get(key)
        if type(v) not in (int,float) or not math.isfinite(v) or v<0 or key=='duration' and v==0:
            raise ValueError('修补区间无效')
    if settings.get('device','auto') not in ('auto','cpu','mps','cuda'):raise ValueError('修补设备无效')


def execute(workspace,source,settings,directory):
    from workflow.platforms.runtime import find_uv
    uv=find_uv(workspace)
    command=[uv,'run','--offline','--no-project','--with-requirements',str(workspace/'workflow/repair/requirements.txt'),
             'python',str(workspace/'scripts/repair_video.py'),'run','--source',str(source),
             '--mask-spec',settings['mask_spec'],'--model',settings['model'],'--output-root',str(directory/'repair'),
             '--start',str(settings['start']),'--duration',str(settings['duration']),
             '--device',settings.get('device','auto')]
    from workflow.orchestration.process import run
    result=run(command,cwd=workspace,timeout=1800)
    (directory/'repair-run.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
    if result.returncode:raise ValueError(f'修补失败，查看 {directory/"repair-run.log"}')
    tasks=list((directory/'repair').glob('*/task.json'))
    if len(tasks)!=1:raise ValueError('修补任务记录缺失或不唯一')
    task=read_json(tasks[0])
    if task['status']!='NEEDS_VISUAL_REVIEW':raise ValueError('修补尚未完成')
    return {'task_path':str(tasks[0]),'outputs':[str(tasks[0]),*(r['path'] for r in task['outputs'].values())]}


def prepare(pipeline,root,job_id,asset_id,settings):
    validate_settings(settings)
    source,sha=pipeline.asset(job_id,asset_id)
    parameters={k:v for k,v in settings.items() if k!='review_path'}
    result=pipeline._stage(root,'repair',{'source_sha256':sha,'settings':parameters,
        'mask_sha256':digest(settings['mask_spec']),'model_sha256':digest(settings['model'])},
        lambda directory:execute(pipeline.workspace,source,settings,directory))
    task=read_json(result['task_path']);clean=task['outputs']['repaired.mp4']
    if task['source_sha256']!=sha or digest(source)!=sha or digest(clean['path'])!=clean['sha256']:
        raise ValueError('修补证据或输出已变化')
    public={'repair_task_path':result['task_path'],'repair_comparison_path':task['outputs']['comparison.mp4']['path']}
    if not settings.get('review_path'):return {**public,'status':'WAITING_REPAIR_REVIEW'}
    review=read_json(settings['review_path'])
    if (review.get('status')!='passed_internal_visual_review' or not review.get('reviewer')
            or review.get('task_sha256')!=digest(result['task_path'])
            or review.get('repaired_sha256')!=clean['sha256']
            or any(review.get(k) is not True for k in ('full_playback','product_action_preserved','outside_mask_review'))):
        raise ValueError('修补复核须绑定当前输出并核对完整动作')
    clean_id=pipeline._import(job_id,clean['path'])
    with pipeline.sessions.begin() as session:
        original=session.get(Asset,asset_id);asset=session.get(Asset,clean_id)
        lineage={'original_sha256':original.metadata_json.get('original_sha256',sha),
                 'original_offset':original.metadata_json.get('original_offset',0)+task['start_seconds']}
        if any(k in asset.metadata_json and asset.metadata_json[k]!=v for k,v in lineage.items()):
            raise ValueError('修补素材血缘与已有记录冲突')
        asset.metadata_json={**asset.metadata_json,**lineage,'repair_task_path':result['task_path'],
                             'repair_review_path':settings['review_path'],'repair_review_sha256':digest(settings['review_path'])}
    return {**public,'asset_id':clean_id,'repair_review_path':settings['review_path'],'status':'REPAIR_REVIEWED'}
