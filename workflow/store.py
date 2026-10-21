"""Persistence for the local review build; team deployment uses Postgres."""
from datetime import datetime, timezone
from pathlib import Path
import uuid

from sqlalchemy import create_engine, event, String, JSON, ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix):
    return prefix + '_' + uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default='WAITING_MATERIAL')
    created_at: Mapped[str] = mapped_column(String, default=now)


class Asset(Base):
    __tablename__ = 'assets'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    sha256: Mapped[str] = mapped_column(String, unique=True)
    filename: Mapped[str] = mapped_column(String)
    media_path: Mapped[str] = mapped_column(String)
    thumbnail_path: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class JobAsset(Base):
    __tablename__ = 'job_assets'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), primary_key=True)


class Activity(Base):
    __tablename__ = 'activities'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    kind: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Analysis(Base):
    __tablename__ = 'analyses'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class ProductionUse(Base):
    __tablename__ = 'pipeline_production_uses'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column()
    creative_sha256: Mapped[str] = mapped_column(String)
    shots: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default='RESERVED')
    created_at: Mapped[str] = mapped_column(String, default=now)


def database(data_dir: Path):
    engine = create_engine('sqlite:///' + str(data_dir / 'workflow.db'),
                           connect_args={'check_same_thread': False, 'timeout': 30})

    @event.listens_for(engine, 'connect')
    def configure_sqlite(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')

    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def asset_dict(asset):
    return {'id': asset.id, 'filename': asset.filename, 'sha256': asset.sha256,
            **asset.metadata_json, 'created_at': asset.created_at,
            'media_url': f'/api/assets/{asset.id}/media',
            'thumbnail_url': f'/api/assets/{asset.id}/thumbnail'}


def invalidate_pipeline(job, *, ordinals=None, clear_drafts=False):
    """Reset derived progress while retaining immutable files for audit/recovery."""
    from copy import deepcopy
    saved=deepcopy(job.payload)
    saved.pop('pipeline',None)
    if saved.get('batch'):
        saved['batch']['rendered_count']=0
        for item in saved['batch']['items']:
            item.pop('production_status',None);item.pop('package_path',None)
    if clear_drafts:
        for item in saved.get('pipeline_inputs',{}).get('items',[]):
            if ordinals is None or item['ordinal'] in ordinals:
                for key in ('spec_path','audit_path','production_directory','adopt_verified'):
                    item.pop(key,None)
    job.payload=saved


def job_dict(session, job):
    # Older tasks remain readable; adding a batch view does not sign their standalone drafts.
    from .markets import market_profile
    from .batch import new_batch
    payload = dict(job.payload)
    payload.setdefault('market_profile', market_profile(payload.get('market', 'MX'),
                                                        payload.get('voiceover_locale')))
    payload.setdefault('voiceover_locale', payload['market_profile']['voiceover_locale'])
    payload.setdefault('style', '达人演示')
    payload.setdefault('batch', new_batch(payload.get('count', 1)))
    assets = session.scalars(select(Asset).join(JobAsset).where(JobAsset.job_id == job.id)
                             .order_by(Asset.created_at)).all()
    activities = session.scalars(select(Activity).where(Activity.job_id == job.id)
                                 .order_by(Activity.id.desc()).limit(100)).all()
    analyses = session.scalars(select(Analysis).where(Analysis.job_id == job.id)
                               .order_by(Analysis.created_at)).all()
    return {'id': job.id, **payload, 'status': job.status, 'version': 1,
            'created_at': job.created_at, 'assets': [asset_dict(a) for a in assets],
            'analyses': [analysis_dict(a) for a in analyses],
            'events': [{'id': e.id, 'kind': e.kind, 'message': e.message,
                        'created_at': e.created_at} for e in activities],
            'next_action': '任务已取消' if job.status=='CANCELLED' else
                           payload['pipeline']['next_action'] if payload.get('pipeline') else
                           '采集或导入指定商品素材，准备批量内容创意' if job.status == 'WAITING_MATERIAL' else
                           '素材已入库；分析可用镜头并规划不同广告角度' if job.status == 'MATERIAL_READY'
            else '查看参考包、镜头与结构分析，确认时间计划和中文稿；ASR/OCR 尚未执行' if job.status == 'ANALYSIS_REVIEW'
                           else '查询任务记录并恢复完整链路'}


def analysis_dict(analysis):
    return {'id': analysis.id, 'asset_id': analysis.asset_id,
            'created_at': analysis.created_at, **analysis.payload['index'],
            'index_path': analysis.payload['index_path'],
            'index_sha256': analysis.payload['index_sha256'],
            'contact_sheets': analysis.payload['manifest']['contact_sheets'],
            'observations': analysis.payload['observations']}
