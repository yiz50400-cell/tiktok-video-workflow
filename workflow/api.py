from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
import hashlib
import os
import secrets
import json
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from .media import inspect_video, MAX_UPLOAD_BYTES, VIDEO_EXTENSIONS
from .store import database, Job, Asset, JobAsset, Activity, Analysis, identifier, job_dict, asset_dict, analysis_dict
from .analysis_attachment import validate_snapshot
from .markets import market_profile, MARKETS
from .batch import new_batch, register_concepts, ConceptsInput
from .collection.reference import validate_reference_package

ROOT = Path(__file__).resolve().parent.parent


class JobInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    market: Literal['MX', 'BR', 'ID', 'PH'] = 'MX'
    voiceover_locale: str | None = Field(default=None, min_length=1, max_length=30)
    style: str = Field(default='达人演示', min_length=1, max_length=100)
    count: int = Field(default=1, ge=1, le=100, strict=True)
    budget: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    owner: str = Field(default='本机制作员', min_length=1, max_length=60)
    sources: list[HttpUrl] = Field(default_factory=list, max_length=20)
    notes: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def valid_market_language(self):
        self.voiceover_locale = market_profile(self.market, self.voiceover_locale)['voiceover_locale']
        return self


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    index_path: str = Field(min_length=1, max_length=4096)
    index_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    index: dict
    manifest: dict
    observations: dict
    contact_sheet_hashes: dict[str, str]


class LanguageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    voiceover_locale: str = Field(min_length=1, max_length=30)


def create_app(data_dir=None):
    data = Path(data_dir or os.environ.get('WORKFLOW_DATA_DIR', ROOT / '.runtime')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    media = data / 'media'
    media.mkdir(exist_ok=True)
    token_file = data / 'access.token'
    try:
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as file:
            file.write(secrets.token_urlsafe(32))
    token = token_file.read_text(encoding='utf-8').strip()
    engine, sessions = database(data)

    @asynccontextmanager
    async def lifespan(_):
        yield
        engine.dispose()

    app = FastAPI(title='视频工作流 · 本地验收版', version='0.1.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.data_dir = data

    @app.middleware('http')
    async def local_access(request: Request, call_next):
        if request.url.hostname not in {'127.0.0.1', 'localhost', 'testserver'}:
            return JSONResponse({'detail': '本版仅用于本机验收。'}, status_code=403)
        if request.url.path.startswith('/api/'):
            supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({'detail': '缺少本机访问凭据：请通过 CLI 或已配置的 MCP 调用。'}, status_code=401)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                origin = request.headers.get('Origin')
                if origin and origin != str(request.base_url).rstrip('/'):
                    return JSONResponse({'detail': '请求来源不匹配。'}, status_code=403)
                if request.headers.get('X-Workflow-Client') != 'studio':
                    return JSONResponse({'detail': '缺少工作流客户端标识。'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def require_job(session, job_id):
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, '任务不存在，请检查任务 ID。')
        return job

    @contextmanager
    def editing(job_id):
        from workflow.orchestration.pipeline import pipeline_lock
        try:
            with pipeline_lock(data,job_id):yield
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    def attach(session, job, asset):
        if job.status == 'CANCELLED':
            raise HTTPException(409, '任务已取消，请创建新任务导入素材。')
        existing = session.get(JobAsset, (job.id, asset.id))
        if not existing:
            session.add(JobAsset(job_id=job.id, asset_id=asset.id))
            session.add(Activity(job_id=job.id, kind='asset_imported',
                                 message=f'已导入 {asset.filename} · 元数据与预览已读取'))
            job.status = 'MATERIAL_READY'
        return existing is not None

    @app.get('/api/capabilities')
    def capabilities():
        return {'version': '0.1.0', 'mode': 'local_review',
                'implemented': ['job_create', 'job_query', 'local_video_import',
                                'sha256_dedup', 'metadata', 'source_preview', 'mcp',
                                'analysis_attach', 'analysis_query', 'market_profiles',
                                'batch_intake', 'batch_concept_registry', 'pipeline_inputs',
                                'pipeline_run', 'pipeline_evidence', 'local_delivery'],
                'pending': ['automatic_platform_search', 'asr', 'ocr', 'real_batch_quality_acceptance'],
                'out_of_scope': ['account_access', 'platform_upload', 'platform_draft',
                                 'product_attachment', 'publishing', 'team_auth'],
                'max_upload_mb': 250, 'subscription_api_included': False,
                'markets': [market_profile(code) for code in MARKETS],
                'batch_render': True,
                'pipeline': {'target_count': 'requested_integer_1_to_100', 'workers': [1,2],
                             'quality_acceptance': 'per_video_review_required',
                             'semantic_steps': 'codex_observation_creation_and_independent_review'},
                'standalone_modules': {
                    'collection': {'entry': str(ROOT / 'scripts/collect_videos.py'),
                                   'automatic_search': False, 'integrated': True},
                    'repair': {'entry': str(ROOT / 'scripts/repair_video.py'),
                               'engine': 'local_lama', 'isolated_dependencies': True,
                               'automatic_watermark_detection': False, 'integrated': True,
                               'new_output_review': 'required'},
                    'analysis': {'entry': str(ROOT / 'scripts/analyze_video.py'),
                                 'asr': False, 'ocr': False, 'integrated': True},
                    'creation': {'entry': str(ROOT / 'scripts/plan_video.py'),
                                 'integrated': True, 'automatic_copy_generation': False},
                    'production': {'entry': str(ROOT / 'scripts/run_local_batch.py'),
                                   'integrated': True, 'sample_scope': 'approved_configured_video'},
                    'local_package': {'entry': str(ROOT / 'scripts/prepare_publish.py'),
                                      'integrated': True, 'pipeline_entry': 'workflow.orchestration.delivery',
                                      'platform_action': False}}}

    @app.get('/api/jobs')
    def list_jobs():
        with sessions() as session:
            return [job_dict(session, job) for job in session.scalars(
                select(Job).order_by(Job.created_at.desc()).limit(200)).all()]

    @app.post('/api/jobs', status_code=201)
    def create_job(payload: JobInput):
        with sessions.begin() as session:
            saved = payload.model_dump(mode='json')
            saved['market_profile'] = market_profile(payload.market, payload.voiceover_locale)
            saved['batch'] = new_batch(payload.count)
            job = Job(id=identifier('job'), payload=saved)
            session.add(job)
            session.flush()
            session.add(Activity(job_id=job.id, kind='created',
                                 message=f'任务已创建，目标 {payload.count} 条；等待商品素材与内容规划'))
            session.flush()
            return job_dict(session, job)

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str):
        with sessions() as session:
            return job_dict(session, require_job(session, job_id))

    class PipelineInputs(BaseModel):
        model_config = ConfigDict(extra='forbid')
        items: list[dict] = Field(default_factory=list, max_length=100)
        collection: dict | None = None
        workers: int = Field(default=1, ge=1, le=2, strict=True)

    @app.post('/api/jobs/{job_id}/pipeline/inputs')
    def pipeline_inputs(job_id: str, payload: PipelineInputs):
        from workflow.orchestration.pipeline import pipeline_lock, set_inputs
        try:
            with pipeline_lock(data, job_id), sessions.begin() as session:
                job = require_job(session, job_id)
                if job.status == 'CANCELLED':
                    raise HTTPException(409, '任务已取消')
                set_inputs(session, job, payload.model_dump(exclude_unset=True))
                session.flush()
                return job_dict(session, job)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/jobs/{job_id}/pipeline/run')
    async def pipeline_run(job_id: str):
        from workflow.orchestration.pipeline import Pipeline
        with sessions() as session:
            job = require_job(session, job_id)
            if job.status == 'CANCELLED':
                raise HTTPException(409, '任务已取消')
        try:
            await run_in_threadpool(Pipeline(data, sessions, ROOT).run, job_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        with sessions() as session:
            return job_dict(session, require_job(session, job_id))

    class PipelineEvidence(BaseModel):
        model_config = ConfigDict(extra='forbid')
        ordinal: int = Field(ge=1, le=100, strict=True)
        kind: Literal['chinese_confirmation', 'localization']
        records: dict

    @app.post('/api/jobs/{job_id}/pipeline/evidence')
    def pipeline_evidence(job_id: str, payload: PipelineEvidence):
        from workflow.orchestration.pipeline import Pipeline
        with sessions() as session:
            job = require_job(session, job_id)
            if job.status == 'CANCELLED':
                raise HTTPException(409, '任务已取消')
            if payload.ordinal > job.payload['count']:
                raise HTTPException(422, '序号超出任务数量')
        try:
            return Pipeline(data, sessions, ROOT).save_evidence(job_id, payload.ordinal, payload.kind, payload.records)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/jobs/{job_id}/batch/concepts')
    def add_batch_concepts(job_id: str, payload: ConceptsInput):
        with editing(job_id), sessions.begin() as session:
            # Serialize local batch writers so concurrent registrations do not lose revisions.
            from sqlalchemy import text
            session.execute(text('BEGIN IMMEDIATE'))
            job = require_job(session, job_id)
            if job.status == 'CANCELLED':
                raise HTTPException(409, '任务已取消，不能登记批量创意。')
            allowed = session.scalars(select(JobAsset.asset_id).where(JobAsset.job_id == job.id)).all()
            current = job.payload.get('batch') or new_batch(job.payload.get('count', 1))
            try:
                updated = register_concepts(current, [c.model_dump() for c in payload.concepts], allowed)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if current != updated:
                job.payload = {**job.payload, 'batch': updated}
                from workflow.store import invalidate_pipeline
                changed={c['ordinal'] for c in updated['items']
                         if c['concept']!=current['items'][c['ordinal']-1]['concept']}
                invalidate_pipeline(job,ordinals=changed,clear_drafts=True)
                session.add(Activity(job_id=job.id, kind='batch_concepts_registered',
                                     message=f"已登记 {updated['planned_count']}/{updated['target_count']} 条待审创意；尚未成片"))
            session.flush()
            return job_dict(session, job)

    class ReferencePackageInput(BaseModel):
        model_config = ConfigDict(extra='forbid')
        package: dict

    @app.post('/api/jobs/{job_id}/reference-package')
    def register_reference_package(job_id: str, payload: ReferencePackageInput):
        try:
            validate_reference_package(payload.package)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f'参考包格式不合法：{exc}') from exc
        with sessions.begin() as session:
            job = require_job(session, job_id)
            if job.status == 'CANCELLED':
                raise HTTPException(409, '任务已取消，不能登记参考包。')
            package_market = payload.package.get('market')
            job_market = job.payload.get('market')
            if package_market != job_market:
                raise HTTPException(
                    409,
                    f'参考包市场 {package_market!r} 与任务市场 {job_market!r} 不一致，请使用对应市场任务。',
                )
            saved = dict(job.payload)
            saved['reference_package'] = payload.package
            job.payload = saved
            session.add(Activity(job_id=job_id, kind='reference_package_registered',
                                 message='同款优先的商品广告参考包已登记；等待分析与中文创意审核'))
            session.flush()
            return job_dict(session, job)

    @app.post('/api/jobs/{job_id}/language')
    def select_language(job_id: str, payload: LanguageInput):
        with editing(job_id), sessions.begin() as session:
            from sqlalchemy import text
            session.execute(text('BEGIN IMMEDIATE'))
            job = require_job(session, job_id)
            if job.status == 'CANCELLED':
                raise HTTPException(409, '任务已取消，不能修改口播语言。')
            try:
                profile = market_profile(job.payload['market'], payload.voiceover_locale)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            current_locale = job.payload.get('voiceover_locale')
            if current_locale != payload.voiceover_locale:
                from copy import deepcopy
                batch = deepcopy(job.payload.get('batch') or new_batch(job.payload.get('count', 1)))
                batch['sample_review_status'] = 'pending'
                for item in batch['items']:
                    item['human_approval_status'] = 'pending_user'
                job.payload = {**job.payload, 'market_profile': profile, 'batch': batch,
                               'voiceover_locale': payload.voiceover_locale}
                from workflow.store import invalidate_pipeline
                invalidate_pipeline(job,clear_drafts=True)
                job.status='MATERIAL_READY' if session.scalar(select(JobAsset).where(JobAsset.job_id==job.id)) else 'WAITING_MATERIAL'
                session.add(Activity(job_id=job.id, kind='language_selected',
                                     message=f'目标口播语言已设为 {payload.voiceover_locale}；当地稿件待审'))
            session.flush()
            return job_dict(session, job)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel_job(job_id: str):
        with sessions.begin() as session:
            job = require_job(session, job_id)
            if job.status != 'CANCELLED':
                job.status = 'CANCELLED'
                session.add(Activity(job_id=job.id, kind='cancelled', message='任务已取消，原素材保留'))
            session.flush()
            return job_dict(session, job)

    @app.post('/api/jobs/{job_id}/advance')
    def advance_job(job_id: str):
        with sessions() as session:
            require_job(session, job_id)
        raise HTTPException(409, '此节点尚未接通，任务状态保持不变。')

    @app.post('/api/jobs/{job_id}/analyses')
    def attach_analysis(job_id: str, payload: AnalysisInput):
        snapshot = payload.model_dump()
        try:
            validate_snapshot(snapshot)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f'分析格式或证据区间不合法：{exc}') from exc
        fingerprint = hashlib.sha256((job_id + json.dumps(snapshot, sort_keys=True)).encode()).hexdigest()
        analysis_id = 'analysis_' + fingerprint[:24]
        with sessions.begin() as session:
            job = require_job(session, job_id)
            if job.status == 'CANCELLED':
                raise HTTPException(409, '任务已取消，不能接入分析。')
            asset = session.scalar(select(Asset).join(JobAsset).where(
                JobAsset.job_id == job_id, Asset.sha256 == snapshot['index']['source_sha256']))
            if not asset:
                raise HTTPException(409, '分析源文件尚未导入本任务，请先导入同哈希原片。')
            existing = session.get(Analysis, analysis_id)
            if existing:
                return {'analysis': analysis_dict(existing), 'duplicate': True}
            record = Analysis(id=analysis_id, job_id=job_id, asset_id=asset.id, payload=snapshot)
            session.add(record)
            job.status = 'ANALYSIS_REVIEW'
            session.add(Activity(job_id=job_id, kind='analysis_attached',
                                 message='分镜与结构分析已接入，保留待审；ASR/OCR 未执行'))
            session.flush()
            return {'analysis': analysis_dict(record), 'duplicate': False}

    @app.post('/api/jobs/{job_id}/assets')
    async def upload_asset(job_id: str, file: UploadFile):
        with sessions() as session:
            if require_job(session, job_id).status == 'CANCELLED':
                raise HTTPException(409, '任务已取消。')
        filename = Path((file.filename or 'video.mp4').replace('\\', '/')).name[:200]
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise HTTPException(422, '请上传 MP4、MOV、M4V、WebM 或 MKV 视频。')
        asset_id = identifier('asset')
        temporary = media / (asset_id + '.upload' + suffix)
        thumbnail = media / (asset_id + '.jpg')
        target = media / (asset_id + suffix)
        digest = hashlib.sha256()
        size = 0
        committed = False
        try:
            with temporary.open('wb') as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, '单个素材最大 250 MB，请先裁切或压缩。')
                    digest.update(chunk)
                    output.write(chunk)
            sha = digest.hexdigest()
            with sessions.begin() as session:
                existing = session.scalar(select(Asset).where(Asset.sha256 == sha))
                if existing:
                    attach(session, require_job(session, job_id), existing)
                    return JSONResponse({'asset': asset_dict(existing), 'duplicate': True}, status_code=200)
            try:
                metadata = await run_in_threadpool(inspect_video, temporary, thumbnail)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            temporary.replace(target)
            try:
                with sessions.begin() as session:
                    job = require_job(session, job_id)
                    asset = Asset(id=asset_id, sha256=sha, filename=filename,
                                  media_path=target.name, thumbnail_path=thumbnail.name,
                                  metadata_json=metadata)
                    session.add(asset)
                    session.flush()
                    attach(session, job, asset)
                    session.flush()
                    result = asset_dict(asset)
                committed = True
                return JSONResponse({'asset': result, 'duplicate': False}, status_code=201)
            except IntegrityError:
                # Another upload may have committed this hash while we decoded it.
                with sessions.begin() as session:
                    existing = session.scalar(select(Asset).where(Asset.sha256 == sha))
                    if not existing:
                        raise
                    attach(session, require_job(session, job_id), existing)
                    return JSONResponse({'asset': asset_dict(existing), 'duplicate': True}, status_code=200)
        finally:
            await file.close()
            temporary.unlink(missing_ok=True)
            if not committed:
                target.unlink(missing_ok=True)
                thumbnail.unlink(missing_ok=True)

    @app.get('/api/assets/{asset_id}/{kind}')
    def asset_file(asset_id: str, kind: Literal['media', 'thumbnail']):
        with sessions() as session:
            asset = session.get(Asset, asset_id)
            if not asset:
                raise HTTPException(404, '素材不存在。')
            path = media / (asset.media_path if kind == 'media' else asset.thumbnail_path)
        if not path.is_file():
            raise HTTPException(404, '素材文件已缺失，请重新导入。')
        return FileResponse(path)

    @app.get('/')
    def home():
        # Headless service: Codex CLI/MCP authenticate with the Bearer token in access.token.
        return JSONResponse({'service': 'codex-video-workflow',
                             'mode': 'headless', 'entry': 'Codex skill / tools'})

    return app
