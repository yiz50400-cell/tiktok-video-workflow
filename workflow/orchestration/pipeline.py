"""Connect task-owned media modules and keep real, resumable per-item progress."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from workflow.platforms.locks import acquire, release
import hashlib
import json
import re
from pathlib import Path
import shutil

from sqlalchemy import select

from workflow.store import Job, Asset, JobAsset, Activity, Analysis, identifier, now
from workflow.publishing.prepare import digest, read_json, write_json
from workflow.orchestration.local import StageStore


ITEM_FIELDS={'ordinal','asset_id','observations_path','sources','spec_path','audit_path','production_directory','adopt_verified','repair'}
SOURCE_FIELDS={'asset_id','observations_path','repair'}
NEXT={'WAITING_SOURCE':'采集或补充同款商品素材', 'WAITING_OBSERVATIONS':'Codex查看联系图并保存动作与包装观察',
      'WAITING_DRAFT':'Codex按素材和参考结构制作中文稿、镜头及包装方案',
      'WAITING_REPAIR_REVIEW':'Codex查看修补对比并完整播放，记录商品动作和遮罩外画面复核',
      'WAITING_COPY_AUDIT':'独立复核中文稿与制作方案', 'WAITING_ZH_APPROVAL':'展示中文稿及制作方案，记录用户明确确认',
      'WAITING_LOCALIZATION':'内部本地化并完成独立复核', 'FAILED':'根据该条错误修复后恢复',
      'LOCAL_DELIVERED':'播放样片并核对听感、完整动作和用户反馈'}


@contextmanager
def pipeline_lock(data,job_id):
    root=Path(data)/'pipelines'/job_id;root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a+b') as stream:
        try:acquire(stream,blocking=False)
        except BlockingIOError as exc:raise ValueError('该任务正在执行，请查询进度后再恢复') from exc
        try:yield root
        finally:release(stream)


def _original_sha256(asset):
    original=asset.metadata_json.get('original_sha256',asset.sha256)
    if not isinstance(original,str) or not re.fullmatch(r'[0-9a-f]{64}',original):
        raise ValueError('素材原片血缘SHA256无效')
    return original


def set_inputs(session,job,body):
    if set(body)-{'items','collection','workers'}:raise ValueError('未知链路输入字段')
    current=deepcopy(job.payload.get('pipeline_inputs',{'items':[]}))
    allowed=set(session.scalars(select(JobAsset.asset_id).where(JobAsset.job_id==job.id)))
    rows=body.get('items',[]);seen=set();merged={row['ordinal']:row for row in current['items']}
    if not isinstance(rows,list):raise ValueError('items须为列表')
    for row in rows:
        if not isinstance(row,dict) or set(row)-ITEM_FIELDS:raise ValueError('未知视频输入字段')
        ordinal=row.get('ordinal')
        if type(ordinal) is not int or not 1<=ordinal<=job.payload['count'] or ordinal in seen:
            raise ValueError('视频序号重复或超出任务数量')
        seen.add(ordinal)
        if 'sources' in row:
            sources=row['sources']
            if (not isinstance(sources,list) or not sources
                    or any(not isinstance(s,dict) or set(s)-SOURCE_FIELDS or not s.get('asset_id') for s in sources)):
                raise ValueError('sources须为含素材ID的非空列表')
            if any(key in row for key in SOURCE_FIELDS):raise ValueError('多来源不能同时填写旧单来源字段')
            if len({s['asset_id'] for s in sources})!=len(sources):raise ValueError('混剪来源不能重复')
            originals=set()
            for source in sources:
                if source['asset_id'] not in allowed:raise ValueError('素材必须属于当前任务')
                original=_original_sha256(session.get(Asset,source['asset_id']))
                if original in originals:raise ValueError('混剪来源必须来自不同原片')
                originals.add(original)
                observation=source.get('observations_path')
                if observation and (not Path(observation).is_absolute() or not Path(observation).is_file()):
                    raise ValueError('observations_path须为已存在的绝对文件路径')
                if source.get('repair'):
                    from workflow.orchestration.repair import validate_settings
                    validate_settings(source['repair'])
        if row.get('asset_id') and row['asset_id'] not in allowed:raise ValueError('素材必须属于当前任务')
        for key in ('observations_path','spec_path','audit_path'):
            if row.get(key) and (not Path(row[key]).is_absolute() or not Path(row[key]).is_file()):
                raise ValueError(f'{key}须为已存在的绝对文件路径')
        if row.get('production_directory') and (not Path(row['production_directory']).is_absolute()
                                               or not Path(row['production_directory']).is_dir()):
            raise ValueError('制作目录须为已存在的绝对路径')
        if 'adopt_verified' in row and type(row['adopt_verified']) is not bool:raise ValueError('adopt_verified须为布尔值')
        if row.get('repair'):
            from workflow.orchestration.repair import validate_settings
            validate_settings(row['repair'])
        merged[ordinal]=deepcopy(row)
    current['items']=[merged[key] for key in sorted(merged)]
    if 'collection' in body:
        if not isinstance(body['collection'],dict):raise ValueError('collection须为对象')
        settings=body['collection']
        if set(settings)-{'brand','product','seeds','urls','local_sources','download','resolve_urls','max_candidates'}:
            raise ValueError('未知采集字段')
        for seed in settings.get('seeds',[]):
            if not re.fullmatch(r'[A-Za-z0-9_-]+',seed.get('id','')):raise ValueError('采集候选须有安全的唯一id')
        for source in settings.get('local_sources',{}).values():
            if not Path(source).is_absolute() or not Path(source).is_file():raise ValueError('采集本地素材须为已有绝对路径')
        for key in ('download','resolve_urls'):
            if key in settings and type(settings[key]) is not bool:raise ValueError('采集联网选项须为布尔值')
        if type(settings.get('max_candidates',8)) is not int or not 1<=settings.get('max_candidates',8)<=100:
            raise ValueError('采集候选上限须为1–100')
        current['collection']=body['collection']
    if 'workers' in body:
        if type(body['workers']) is not int or not 1<=body['workers']<=2:raise ValueError('workers须为1或2')
        current['workers']=body['workers']
    saved={**job.payload,'pipeline_inputs':current}
    if current!=job.payload.get('pipeline_inputs'):
        saved.pop('pipeline',None)
        batch=deepcopy(saved['batch']);batch['rendered_count']=0
        for row in batch['items']:
            row.pop('production_status',None);row.pop('package_path',None)
        saved['batch']=batch
        job.status='MATERIAL_READY' if allowed else 'WAITING_MATERIAL'
    job.payload=saved
    return current


class Pipeline:
    def __init__(self,data,sessions,workspace):
        self.data=Path(data).resolve();self.sessions=sessions;self.workspace=Path(workspace).resolve()

    def job(self,job_id):
        with self.sessions() as session:
            job=session.get(Job,job_id)
            if not job:raise ValueError('任务不存在')
            if job.status=='CANCELLED':raise ValueError('任务已取消')
            return {'id':job.id,**deepcopy(job.payload)}

    def asset(self,job_id,asset_id):
        with self.sessions() as session:
            if not session.get(JobAsset,(job_id,asset_id)):raise ValueError('素材不属于当前任务')
            asset=session.get(Asset,asset_id)
            path=self.data/'media'/asset.media_path
            if digest(path)!=asset.sha256:raise ValueError('任务素材已损坏')
            return path,asset.sha256

    def persist(self,job_id,result):
        with self.sessions.begin() as session:
            from sqlalchemy import text
            session.execute(text('BEGIN IMMEDIATE'))
            job=session.get(Job,job_id)
            if job.status=='CANCELLED':raise ValueError('任务已取消')
            batch=deepcopy(job.payload['batch']);batch['rendered_count']=result['rendered_count']
            for row in result['items']:
                item=batch['items'][row['ordinal']-1]
                item['production_status']=row['status']
                if row.get('package_path'):item['package_path']=row['package_path']
                else:item.pop('package_path',None)
            job.payload={**job.payload,'pipeline':deepcopy(result),'batch':batch}
            job.status='LOCAL_DELIVERED' if result['delivered_count']==job.payload['count'] else 'PIPELINE_ACTIVE'

    def save_evidence(self,job_id,ordinal,kind,records):
        with pipeline_lock(self.data,job_id):
            job=self.job(job_id)
            rows=job.get('pipeline',{}).get('items',[])
            row=next((item for item in rows if item['ordinal']==ordinal),None)
            if row is None or not row.get('production_directory'):raise ValueError('先生成本条中文制作稿')
            directory=Path(row['production_directory']);creative=read_json(directory/'creative.json');sha=digest(directory/'creative.json')
            if creative.get('job_id')!=job_id or creative.get('ordinal',ordinal)!=ordinal:
                raise ValueError('中文稿与当前任务不匹配')
            if kind=='chinese_confirmation':
                if (records.get('version_sha256')!=sha or not records.get('user_reply')
                        or not records.get('source_turn_id')):
                    raise ValueError('中文确认须绑定准确稿件并记录用户回复及来源轮次')
                # Local conversation evidence, not authenticated multi-user approval.
                target=directory/'zh-approval.json'
                data={**records,'status':'approved','recorded_at':now(),'scope':'exact_local_chinese_production_plan'}
                if target.exists():
                    old=read_json(target)
                    if any(old.get(key)!=records.get(key) for key in records):raise ValueError('已有确认不能覆盖，请新建稿件版本')
                else:
                    from workflow.platforms.files import atomic_json
                    atomic_json(target,data,replace=False)
            elif kind=='localization':
                approval=directory/'zh-approval.json'
                if not approval.exists() or read_json(approval).get('version_sha256')!=sha:raise ValueError('中文确认尚未完成')
                loc=records['localization'];audit=records['audit'];version=records.get('version',1)
                if type(version) is not int or not 1<=version<=9999:raise ValueError('本地化版本须为1–9999整数')
                loc_path=directory/f'localization-v{version:04d}.json';audit_path=directory/f'localization-audit-v{version:04d}.json'
                if ((loc.get('market'),loc.get('locale'))!=(job['market'],job['voiceover_locale'])
                        or loc.get('creative_sha256')!=sha or audit.get('creative_sha256')!=sha
                        or audit.get('zh_approval_sha256')!=digest(approval)
                        or audit.get('status')!='passed_independent_internal_text_review'
                        or audit.get('material_changes_to_approved_meaning')!=[]
                        or not audit.get('reviewer') or audit['reviewer']==creative.get('author')
                        or (audit.get('market'),audit.get('locale'))!=(job['market'],job['voiceover_locale'])):
                    raise ValueError('内部本地化审核未绑定中文批准或存在实质变化')
                loc_bytes=(json.dumps(loc,ensure_ascii=False,indent=2)+'\n').encode()
                if audit.get('localization_sha256')!=hashlib.sha256(loc_bytes).hexdigest():
                    raise ValueError('审核须绑定准确保存格式的本地化SHA256')
                for target,value in ((loc_path,loc),(audit_path,audit)):
                    if target.exists() and read_json(target)!=value:raise ValueError('已有内部证据不能覆盖，使用新版本')
                if not loc_path.exists():loc_path.write_bytes(loc_bytes)
                if not audit_path.exists():write_json(audit_path,audit)
            else:raise ValueError('不支持的证据种类')
            return {'ordinal':ordinal,'kind':kind,'production_directory':str(directory),'creative_sha256':sha}

    def _stage(self,root,name,inputs,action):
        fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        directory=root/'stages'/f'{name}-{fingerprint[:20]}';directory.mkdir(parents=True,exist_ok=True)
        recorded=StageStore(directory).run(name,fingerprint,lambda:action(directory))
        return {**recorded['result'],'reused':recorded['reused']}

    def _import(self,job_id,source):
        from workflow.media import inspect_video, VIDEO_EXTENSIONS, MAX_UPLOAD_BYTES
        path=Path(source);sha=digest(path)
        if path.suffix.lower() not in VIDEO_EXTENSIONS or path.stat().st_size>MAX_UPLOAD_BYTES:
            raise ValueError('采集产物不是受支持的视频或超过250MB')
        with self.sessions.begin() as session:
            asset=session.scalar(select(Asset).where(Asset.sha256==sha))
            if asset is None:
                asset_id=identifier('asset');target=self.data/'media'/(asset_id+path.suffix.lower());thumb=self.data/'media'/(asset_id+'.jpg')
                shutil.copyfile(path,target)
                try:metadata=inspect_video(target,thumb)
                except Exception:
                    target.unlink(missing_ok=True);thumb.unlink(missing_ok=True);raise
                asset=Asset(id=asset_id,sha256=sha,filename=path.name,media_path=target.name,thumbnail_path=thumb.name,metadata_json=metadata)
                session.add(asset);session.flush()
            if not session.get(JobAsset,(job_id,asset.id)):
                session.add(JobAsset(job_id=job_id,asset_id=asset.id))
                session.add(Activity(job_id=job_id,kind='pipeline_collection_import',message='采集素材已关联任务'))
            return asset.id

    def _collect(self,root,job,settings):
        from workflow.collection import collect
        hashes={key:digest(value) for key,value in settings.get('local_sources',{}).items()}
        def action(directory):
            output=directory/'collection'
            previous=read_json(output/'collection.json') if (output/'collection.json').exists() else {}
            existing={c['id']:c['raw_path'] for c in previous.get('candidates',[]) if c.get('raw_path') and Path(c['raw_path']).is_file()}
            report=collect(seeds=settings.get('seeds',[]),brand=settings.get('brand',''),
                           product=settings.get('product',job['product']),output_dir=output,
                           urls=settings.get('urls',[]),local_sources=settings.get('local_sources',{}),
                           existing_raw=existing,download=settings.get('download',False),
                           resolve_urls=settings.get('resolve_urls',False),max_candidates=settings.get('max_candidates',8))
            if any(c['acquisition_status']=='failed' or c['preview_status']=='failed' for c in report['candidates']):
                raise ValueError(f'部分采集失败，已成功素材保留；查看 {output/"collection.json"}')
            return {'outputs':[str(p) for p in output.rglob('*') if p.is_file()],'report_path':str(output/'collection.json'),
                    'candidates':report['candidates']}
        result=self._stage(root,'collection',{'settings':settings,'source_hashes':hashes},action)
        for candidate in result['candidates']:
            if candidate.get('raw_path') and candidate['preview_status']=='validated':
                candidate['asset_id']=self._import(job['id'],candidate['raw_path'])
        return result

    def _analysis(self,root,job_id,asset_id):
        from workflow.analysis.core import analyze_video
        path,sha=self.asset(job_id,asset_id)
        def action(directory):
            output=directory/'analysis'
            if output.exists():shutil.rmtree(output)
            manifest=analyze_video(path,output)
            outputs=[str(p) for p in output.rglob('*') if p.is_file()]
            return {'outputs':outputs,'manifest':str(output/'manifest.json'),
                    'contact_sheets':[s['path'] for s in manifest['contact_sheets']]}
        return self._stage(root,'analysis',{'source_sha256':sha,'source_path':str(path)},action)

    def _observe(self,root,job_id,asset_id,manifest,observations):
        from workflow.analysis.core import save_observations
        from workflow.analysis_attachment import load_analysis_attachment
        def action(directory):
            # Observation variants receive their own analysis directory; upstream frames remain immutable.
            original=Path(manifest).parent; output=directory/'analysis'
            if output.exists():shutil.rmtree(output)
            shutil.copytree(original,output)
            record=read_json(output/'manifest.json')
            for frame in record['frames']:frame['path']=str(output/'frames'/Path(frame['path']).name)
            for sheet in record['contact_sheets']:sheet['path']=str(output/Path(sheet['path']).name)
            write_json(output/'manifest.json',record)
            saved=save_observations(output/'manifest.json',Path(observations))
            attachment=load_analysis_attachment(Path(saved['analysis_path']))
            with self.sessions.begin() as session:
                all_records=session.scalars(select(Analysis).where(Analysis.job_id==job_id)).all()
                if not any(a.payload['index_sha256']==attachment['index_sha256'] for a in all_records):
                    session.add(Analysis(id=identifier('analysis'),job_id=job_id,asset_id=asset_id,payload=attachment))
            return {'outputs':[str(p) for p in output.rglob('*') if p.is_file()], 'analysis':saved['analysis_path']}
        return self._stage(root,'observations',{'manifest':digest(manifest),'observations':digest(observations)},action)

    def _draft(self,root,job,item,analyses):
        from workflow.creation.core import create_version
        spec=read_json(item['spec_path'])
        if spec['job_id']!=job['id'] or spec.get('market')!=job['market'] or spec.get('voiceover_locale')!=job['voiceover_locale']:
            raise ValueError('创作输入与任务或市场不匹配')
        inputs=item.get('sources') or [item]
        if 'sources' in item:
            sources=spec.get('sources')
            if not isinstance(sources,list) or len(sources)!=len(inputs):
                raise ValueError('创作来源数量和顺序须对应链路sources')
            if any(spec.get(k) for k in ('analysis_path','edit_video_path','transcode_verification_path')):
                raise ValueError('多来源创作不能混入旧单来源字段')
        else:
            if 'sources' in spec:raise ValueError('多来源创作须登记链路sources')
            sources=[spec]
        trusted_source_originals={}
        for source_spec,source_input,analysis in zip(sources,inputs,analyses):
            source,source_sha=self.asset(job['id'],source_input['asset_id'])
            if digest(source_spec['edit_video_path'])!=source_sha:
                raise ValueError('创作素材必须为本任务对应顺序的已关联素材')
            source_spec['analysis_path']=analysis
            with self.sessions() as session:
                original=_original_sha256(session.get(Asset,source_input['asset_id']))
            if original in trusted_source_originals.values():
                raise ValueError('混剪来源必须来自不同原片')
            trusted_source_originals[source_sha]=original
        def action(directory):
            versions=directory/'versions'
            if versions.exists():shutil.rmtree(versions)
            version=create_version(spec,versions,trusted_source_originals=trusted_source_originals)
            return {'outputs':[version['version_path']],**version}
        analysis_hash=[digest(a) for a in analyses] if 'sources' in item else digest(analyses[0])
        stage_inputs={'spec':spec,'analysis_sha':analysis_hash}
        # Keep legacy single-original cache/approval bytes; multistream or
        # repaired inputs must include registry lineage before cache reuse.
        if len(inputs)>1 or any(sha!=original for sha,original in trusted_source_originals.items()):
            stage_inputs['trusted_source_originals']=trusted_source_originals
        return self._stage(root,f'draft-{item["ordinal"]}',stage_inputs,action)

    def _production_draft(self,root,job,item,draft,reviewed):
        spec=read_json(item['spec_path']);version=read_json(draft['version_path'])
        settings=spec.get('production_config')
        if not isinstance(settings,dict):return None
        allowed={'voice_approval','voice_approval_sha256','voice_options','packaging','sound_effects','width','height','encoder','visible_zh','source_continuity_review'}
        if set(settings)-allowed:raise ValueError('未知制作配置字段')
        visible=settings.get('visible_zh',['']*len(version['script_lines']))
        if len(visible)!=len(version['script_lines']):raise ValueError('中文包装数量与稿件不一致')
        production={key:value for key,value in settings.items() if key!='visible_zh'}
        version_sources=version.get('sources') or [{'edit_video':version['edit_video']}]
        production.update(sources=[{'path':s['edit_video']['path'],'sha256':s['edit_video']['sha256']}
                                   for s in version_sources],
                          duration=version['total_seconds'],windows=[{
                              **({'source_index':s['source_index']} if 'source_index' in s else {}),
                              'source_start':s['source_start_seconds'],'source_end':s['source_end_seconds'],
                              'timeline_start':s['timeline_start_seconds'],'timeline_end':s['timeline_end_seconds']}
                              for s in version['shots']])
        from workflow.production.continuity import validate_continuity
        validate_continuity(production)
        creative={'schema_version':2,'job_id':job['id'],'ordinal':item['ordinal'],'version':version['version'],
                  'author':version['author'],'market':job['market'],'locale':job['voiceover_locale'],
                  'product_name_zh':job['product'],'production':production,
                  'upstream_version_path':draft['version_path'],'upstream_version_sha256':digest(draft['version_path']),
                  'copy_audit_path':reviewed['audit_path'],'copy_audit_sha256':digest(reviewed['audit_path']),
                  'lines':[{'zh':line['text'],'visible_zh':visible[index],
                            'window':[line['start_seconds'],line['end_seconds']]}
                            for index,line in enumerate(version['script_lines'])]}
        def action(directory):
            bundle=directory/'production';bundle.mkdir(exist_ok=True)
            write_json(bundle/'creative.json',creative)
            preview=['# 中文稿与制作方案','',f'商品：{job["product"]}；市场：{job["market"]}；第{item["ordinal"]}条。',
                     f'总长：{production["duration"]}秒；原片来源：{len(production["sources"])}个。','']
            for line in creative['lines']:
                preview.extend([f'{line["window"][0]}–{line["window"][1]}秒：{line["zh"]}',f'画面文字：{line["visible_zh"] or "无"}',''])
            preview += ['## 镜头安排','']
            for shot in version['shots']:
                preview.append(f'{shot["timeline_start_seconds"]}–{shot["timeline_end_seconds"]}秒（来源{shot.get("source_index",0)+1}）：{shot["description"]}')
            options=production.get('voice_options',{})
            preview += ['', '配音：使用本市场已试听认可的音色，单条保持同一声音，按本方案时段自然清楚地口播。',
                        f'语速调整：{options.get("rate","+0%") }；音量调整：{options.get("volume","+0%") }；音调调整：{options.get("pitch","+0Hz") }。',
                        '包装：使用上方已列出的画面文字。']
            for style in production.get('packaging',[]):
                preview.append(f'第{style["line"]}句画面文字出现于该句时段；位置由画面重点与遮挡检查确定。')
            if not production.get('sound_effects'):preview.append('声音包装：本方案不叠加音效或背景音乐。')
            _reject_background_music(production)
            for sound in production.get('sound_effects',[]):
                if not sound.get('description_zh'):raise ValueError('音效或音乐须有中文说明以供确认')
                preview.append(f'{sound["start"]}–{sound["start"]+sound["duration"]}秒声音包装：{sound["description_zh"]}，音量比例 {sound.get("volume",1)}。')
            preview += ['', '中文确认后内部本地化与独立复核，按本方案继续制作。']
            (bundle/'中文稿审核.md').write_text('\n'.join(preview)+'\n', encoding='utf-8')
            return {'outputs':[str(bundle/'creative.json'),str(bundle/'中文稿审核.md')],'directory':str(bundle)}
        return self._stage(root,f'production-draft-{item["ordinal"]}',creative,action)['directory']

    def _resume_sources(self,job,creative,execution):
        """Recheck registry lineage before any existing bundle can be resumed."""
        with self.sessions() as session:
            assets={asset.sha256:asset for asset in session.scalars(
                select(Asset).join(JobAsset).where(JobAsset.job_id==job['id'])).all()}

        def validate(config):
            sources=config.get('sources')
            if sources is None:
                sources=[{'sha256':config.get('source_sha256')}]
            if (not isinstance(sources,list) or not sources
                    or any(not isinstance(source,dict) or source.get('sha256') not in assets
                           for source in sources)):
                raise ValueError('制作来源必须已导入本任务')
            originals=set()
            for source in sources:
                asset=assets[source['sha256']]
                original=_original_sha256(asset)
                if original in originals:raise ValueError('混剪来源必须来自不同原片')
                originals.add(original)
                if 'original_sha256' in source and source['original_sha256']!=original:
                    raise ValueError('制作来源血缘已变化，须创建新版本')
            return sources

        sources=validate(creative.get('production') or execution)
        hashes=[source['sha256'] for source in sources]
        if execution and creative.get('production'):
            execution_sources=validate(execution)
            if [source['sha256'] for source in execution_sources]!=hashes:
                raise ValueError('执行来源与制作稿血缘绑定不一致，须创建新版本')
        # Existing pipeline bundles bind the immutable creation version by hash.
        # Keep old standalone bundles with no upstream evidence readable.
        if 'upstream_version_path' in creative or 'upstream_version_sha256' in creative:
            path=creative.get('upstream_version_path')
            if (not isinstance(path,str) or not Path(path).is_absolute() or not Path(path).is_file()
                    or digest(path)!=creative.get('upstream_version_sha256')):
                raise ValueError('上游版本血缘证据失效，须创建新版本')
            upstream=read_json(path)
            if upstream.get('job_id')!=job['id']:
                raise ValueError('上游版本与本任务血缘不匹配，须创建新版本')
            saved=upstream.get('sources')
            if saved is None:saved=[{'edit_video':upstream.get('edit_video')}]
            if (not isinstance(saved,list) or len(saved)!=len(sources)
                    or any(not isinstance(source,dict) or not isinstance(source.get('edit_video'),dict)
                           or source['edit_video'].get('sha256')!=sha for source,sha in zip(saved,hashes))):
                raise ValueError('上游版本来源顺序或血缘不匹配，须创建新版本')
            for source,sha in zip(saved,hashes):
                if ('original_sha256' in source
                        and source['original_sha256']!=_original_sha256(assets[sha])):
                    raise ValueError('上游版本原片血缘已变化，须创建新版本')

    def _item(self,root,job,item):
        ordinal=item['ordinal'];row={'ordinal':ordinal,'status':'WAITING_SOURCE'}
        if item.get('production_directory'):
            directory=Path(item['production_directory']).resolve()
            creative=read_json(directory/'creative.json')
            if creative.get('job_id')!=job['id'] or creative.get('ordinal',ordinal)!=ordinal:
                raise ValueError('制作稿与任务序号不匹配')
            execution=read_json(directory/'execution.json') if (directory/'execution.json').exists() else {}
            self._resume_sources(job,creative,execution)
            row['production_directory']=str(directory)
            if not (directory/'zh-approval.json').exists():row['status']='WAITING_ZH_APPROVAL';return row
            approval=read_json(directory/'zh-approval.json')
            if approval.get('status')!='approved' or approval.get('version_sha256')!=digest(directory/'creative.json'):
                raise ValueError('中文批准未绑定当前制作稿')
            if not list(directory.glob('localization-v[0-9][0-9][0-9][0-9].json')):
                row['status']='WAITING_LOCALIZATION';return row
            from scripts.run_local_batch import latest_localization
            loc,audit=latest_localization(directory)
            if not audit.exists():row['status']='WAITING_LOCALIZATION';return row
            from workflow.orchestration.local import preflight
            preflight({'plan':str(directory/'execution.json'),'localization':str(loc),'audit':str(audit)})
            translation=read_json(loc)
            if (translation.get('market'),translation.get('locale'))!=(job['market'],job['voiceover_locale']):
                raise ValueError('本地化与目标市场不匹配')
            row['status']='READY_PRODUCTION';row['adopt_verified']=item.get('adopt_verified',False)
            return row
        inputs=deepcopy(item.get('sources') or ([item] if item.get('asset_id') else []))
        if not inputs:return row
        source_rows=[];analyses=[];waiting_repair=False;waiting_observations=False
        for index,source in enumerate(inputs):
            source_row={'source_index':index,'asset_id':source['asset_id']}
            if source.get('repair'):
                from workflow.orchestration.repair import prepare
                repaired=prepare(self,root,job['id'],source['asset_id'],source['repair'])
                source_row.update(repaired)
                if len(inputs)==1:row.update(repaired)
                if repaired['status']=='WAITING_REPAIR_REVIEW':
                    waiting_repair=True;source_rows.append(source_row);continue
                source['asset_id']=repaired['asset_id']
                source_row['asset_id']=source['asset_id']
            analysis=self._analysis(root,job['id'],source['asset_id'])
            source_row.update(status='WAITING_OBSERVATIONS',analysis_manifest=analysis['manifest'],
                              contact_sheets=analysis['contact_sheets'])
            if index==0:
                row.update(analysis_manifest=analysis['manifest'],contact_sheets=analysis['contact_sheets'])
            if source.get('observations_path'):
                observation=self._observe(root,job['id'],source['asset_id'],analysis['manifest'],source['observations_path'])
                source_row.update(status='WAITING_DRAFT',analysis_path=observation['analysis'])
                analyses.append(observation['analysis'])
                if index==0:row['analysis_path']=observation['analysis']
            else:waiting_observations=True
            source_rows.append(source_row)
        row['source_analyses']=source_rows
        row['status']='WAITING_REPAIR_REVIEW' if waiting_repair else 'WAITING_OBSERVATIONS' if waiting_observations else 'WAITING_DRAFT'
        if waiting_repair or waiting_observations:return row
        item={**item,'sources':inputs} if 'sources' in item else {**item,**inputs[0]}
        if not item.get('spec_path'):return row
        draft=self._draft(root,job,item,analyses)
        row.update(status='WAITING_COPY_AUDIT',version_path=draft['version_path'])
        if not item.get('audit_path'):return row
        from workflow.creation.core import audit_version
        audit=read_json(item['audit_path'])
        def action(directory):
            version_path=Path(draft['version_path']);path=version_path.with_name(version_path.stem+'-audit.json')
            if path.exists():raise ValueError('该稿审计已存在，修改审核请新建稿件版本')
            result=audit_version(version_path,audit)
            return {'outputs':[result['audit_path']],**result}
        reviewed=self._stage(root,f'audit-{ordinal}',{'draft':digest(draft['version_path']),'audit':audit},action)
        row.update(status='WAITING_ZH_APPROVAL' if reviewed['status']=='passed_needs_user_approval' else 'WAITING_DRAFT',copy_audit=reviewed)
        if reviewed['status']=='passed_needs_user_approval':
            directory=self._production_draft(root,job,item,draft,reviewed)
            if directory:
                row.update(self._item(root,job,{'ordinal':ordinal,'production_directory':directory}))
                row['chinese_review_path']=str(Path(directory)/'中文稿审核.md')
        return row

    def _run_production(self,directory):
        from workflow.platforms.runtime import production_command
        command=production_command(self.workspace)
        from workflow.orchestration.process import run
        result=run([*command,str(self.workspace/'scripts/run_local_batch.py'),str(directory)],
                   timeout=1800,cwd=self.workspace)
        (directory/'pipeline-run.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        if result.returncode:raise ValueError(f'制作失败，查看 {directory/"pipeline-run.log"}')

    def _produce(self,row,job,root):
        from workflow.orchestration.delivery import verify_production, deliver
        directory=Path(row['production_directory'])
        self.job(job['id'])
        reservation=None
        if not row['adopt_verified']:
            from workflow.orchestration.reuse import reserve
            creative=read_json(directory/'creative.json')
            configuration=creative.get('production') or read_json(directory/'execution.json')
            sources=configuration.get('sources') or [{'sha256':configuration['source_sha256']}]
            shots=[{'source_sha256':sources[w.get('source_index',0)]['sha256'],
                    'start':w['source_start'],'end':w['source_end']} for w in configuration['windows']]
            with self.sessions() as session:
                lineage={a.sha256:a.metadata_json for a in session.scalars(select(Asset)).all()
                         if a.metadata_json.get('original_sha256')}
            from workflow.production.library import default_library
            # Development ledgers under projects/ plus the configured customer ledger.
            libraries=list(self.workspace.glob('projects/**/library.sqlite3'))
            configured=Path(default_library())
            configured=configured if configured.is_absolute() else self.workspace/configured
            if configured.is_file() and configured not in libraries:libraries.append(configured)
            reservation=reserve(self.sessions,job['id'],row['ordinal'],digest(directory/'creative.json'),shots,
                                libraries=libraries,lineage=lineage)
            self._run_production(directory)
        self.job(job['id'])
        verify_production(directory,job['id'],row['ordinal'],job['market'],job['voiceover_locale'])
        dest=root/'delivery'/f'{row["ordinal"]:03d}'/digest(directory/'execution.json')[:20]
        package=deliver(directory,dest,job_id=job['id'],ordinal=row['ordinal'],market=job['market'],locale=job['voiceover_locale'])
        if reservation:
            from workflow.orchestration.reuse import complete
            complete(self.sessions,reservation)
        return {**row,**package,'status':'LOCAL_DELIVERED','video_path':str(dest/'成片.mp4'),
                'subtitle_path':str(dest/f'字幕-{job["voiceover_locale"]}.srt')}

    def run(self,job_id):
        with pipeline_lock(self.data,job_id) as root:
            job=self.job(job_id);inputs=job.get('pipeline_inputs',{'items':[]})
            supplied={item['ordinal']:item for item in inputs['items']}
            result={'target_count':job['count'],'rendered_count':0,'delivered_count':0,'items':[],
                    'drafted_count':0,
                    'updated_at':now(),'next_action':'','quality_acceptance':'per_video_review_required'}
            if inputs.get('collection'):
                try:result['collection']=self._collect(root,job,inputs['collection'])
                except Exception as exc:result['collection']={'status':'FAILED','error_zh':str(exc)}
            for ordinal in range(1,job['count']+1):
                self.job(job_id)
                try:row=self._item(root,job,supplied.get(ordinal,{'ordinal':ordinal}))
                except Exception as exc:row={'ordinal':ordinal,'status':'FAILED','error_zh':str(exc)}
                row['next_action']=NEXT.get(row['status'],'执行配音、渲染、技术检查和本机交付')
                result['items'].append(row)
                result['drafted_count']=sum(bool(r.get('production_directory') or r.get('version_path')) for r in result['items'])
                result['next_action']=row['next_action'];self.persist(job_id,result)
            ready=[row for row in result['items'] if row['status']=='READY_PRODUCTION']
            # Reservations are ordered before concurrent workers, preserving batch adjacency.
            for row in ready:
                if not row['adopt_verified']:
                    try:
                        from workflow.production.preparation import reserve_voice
                        reserve_voice(Path(row['production_directory']),root/'voice-rotation.json')
                    except Exception as exc:row.update(status='FAILED',error_zh=str(exc),next_action=NEXT['FAILED'])
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=inputs.get('workers',1)) as executor:
                futures={executor.submit(self._produce,row,job,root):row for row in ready if row['status']=='READY_PRODUCTION'}
                for future in as_completed(futures):
                    row=futures[future]
                    try:row.update(future.result())
                    except Exception as exc:row.update(status='FAILED',error_zh=str(exc))
                    row['next_action']=NEXT[row['status']]
                    result['rendered_count']=sum(r['status']=='LOCAL_DELIVERED' for r in result['items'])
                    result['delivered_count']=result['rendered_count'];self.persist(job_id,result)
            pending=[r for r in result['items'] if r['status']!='LOCAL_DELIVERED']
            result['next_action']=pending[0]['next_action'] if pending else '本机文件已交付，播放样片并记录质量反馈'
            self.persist(job_id,result)
            write_json(root/'pipeline-result.json',result)
            return result


_MUSIC_MARKERS = ("背景音乐", "配乐", "bgm", "music", "音乐", "song", "trilha")


def _reject_background_music(production: dict) -> None:
    """User rule 2026-10-19: never add background music (silent or voiceover-only)."""
    if production.get("background_music"):
        raise ValueError("成片禁止背景音乐：无口播须静音，有口播只保留口播")
    for sound in production.get("sound_effects", []) or []:
        text = " ".join(str(sound.get(k, "")) for k in ("description_zh", "path", "kind", "type")).lower()
        if sound.get("kind") == "music" or any(m in text for m in _MUSIC_MARKERS):
            raise ValueError("成片禁止背景音乐：sound_effects 不得放置音乐")
