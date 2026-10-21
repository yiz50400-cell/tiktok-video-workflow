"""Serialize local seven-day material reservations before media generation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from sqlalchemy import select, text
from workflow.store import ProductionUse


def _canonical(shots,metadata):
    mapped=[]
    for shot in shots:
        source=metadata.get(shot['source_sha256'],{})
        offset=source.get('original_offset',0)
        mapped.append({'source_sha256':source.get('original_sha256',shot['source_sha256']),
                       'start':round(shot['start']+offset,6),'end':round(shot['end']+offset,6)})
    return mapped


def _overlaps(shots,previous):
    return any(a['source_sha256']==b['source_sha256'] and max(a['start'],b['start'])<min(a['end'],b['end'])-1e-6
               for a in shots for b in previous)


def reserve(sessions,job_id,ordinal,creative_sha256,shots,*,at=None,libraries=(),lineage=None):
    at=at or datetime.now(timezone.utc)
    if at.tzinfo is None:raise ValueError('usage time requires timezone')
    if not shots or any(not shot.get('source_sha256') or
                        not all(type(shot[k]) in (int,float) and math.isfinite(shot[k]) for k in ('start','end'))
                        or not 0<=shot['start']<shot['end'] for shot in shots):
        raise ValueError('invalid usage intervals')
    identity=hashlib.sha256(f'{job_id}:{ordinal}:{creative_sha256}'.encode()).hexdigest()
    with sessions() as session:
        attached=[]
        try:
            for index,path in enumerate(libraries):
                if not Path(path).is_file():continue
                alias=f'library_{index}'
                session.execute(text(f'ATTACH DATABASE :path AS {alias}'),{'path':str(Path(path).resolve())})
                attached.append(alias)
            # Lock all attached SQLite writers while reading lineage/history and reserving.
            session.execute(text('BEGIN IMMEDIATE'))
            metadata=dict(lineage or {});history=[]
            for alias in attached:
                for sha,data in session.execute(text(f'SELECT sha256, metadata FROM {alias}.assets')):
                    record=json.loads(data)
                    if sha in metadata and any(metadata[sha].get(k,default)!=record.get(k,default)
                                               for k,default in [('original_sha256',sha),('original_offset',0)]):
                        raise ValueError('素材原片血缘记录冲突')
                    metadata[sha]=record
                for created_at,snapshot in session.execute(text(f'SELECT created_at, snapshot FROM {alias}.uses')):
                    if datetime.fromisoformat(created_at)>=at-timedelta(days=7):history.append(json.loads(snapshot))
            shots=_canonical(shots,metadata)
            old=session.get(ProductionUse,identity)
            if old:
                if old.shots!=shots:raise ValueError('reserved shots changed; create a new version')
                session.commit();return identity
            recent=session.scalars(select(ProductionUse).where(ProductionUse.created_at>=(at-timedelta(days=7)).isoformat())).all()
            previous=[use.shots for use in recent]
            previous.extend([{'source_sha256':s['original_sha256'],'start':s['original_start'],'end':s['original_end']}
                             for s in use['shots']] for use in history)
            if any(_overlaps(shots,rows) for rows in previous):
                raise ValueError('素材区间在七天内已使用或预留，请补充素材')
            session.add(ProductionUse(id=identity,job_id=job_id,ordinal=ordinal,creative_sha256=creative_sha256,
                                      shots=shots,status='RESERVED',created_at=at.astimezone(timezone.utc).isoformat()))
            session.commit()
        finally:
            session.rollback()
            for alias in attached:session.execute(text(f'DETACH DATABASE {alias}'))
            session.commit()
    return identity


def complete(sessions,identity):
    with sessions.begin() as session:
        use=session.get(ProductionUse,identity)
        if use is None:raise ValueError('unknown material reservation')
        use.status='COMPLETED'
