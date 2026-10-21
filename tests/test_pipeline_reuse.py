from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from workflow.store import database


def test_media_reservation_is_atomic_across_tasks_and_idempotent_for_retry(tmp_path):
    from workflow.orchestration.reuse import reserve, complete
    engine,sessions=database(tmp_path)
    shots=[{'source_sha256':'original','start':1,'end':4}]
    first=reserve(sessions,'job_a',1,'creative-a',shots)
    assert reserve(sessions,'job_a',1,'creative-a',shots)==first
    with pytest.raises(ValueError,match='七天'):
        reserve(sessions,'job_b',1,'creative-b',[{'source_sha256':'original','start':3,'end':5}])
    assert reserve(sessions,'job_b',1,'creative-b',[{'source_sha256':'original','start':4,'end':6}])!=first
    complete(sessions,first)
    engine.dispose()


def test_reuse_expiration_respects_seven_days_and_shot_changes(tmp_path):
    from workflow.orchestration.reuse import reserve
    engine,sessions=database(tmp_path)
    at=datetime.now(timezone.utc)
    reserve(sessions,'job_old',1,'old',[{'source_sha256':'s','start':0,'end':3}],at=at-timedelta(days=8))
    reserve(sessions,'job_new',1,'new',[{'source_sha256':'s','start':0,'end':3}],at=at)
    with pytest.raises(ValueError,match='changed'):
        reserve(sessions,'job_new',1,'new',[{'source_sha256':'s','start':0,'end':4}],at=at)
    engine.dispose()


def test_legacy_library_history_and_repaired_lineage_are_enforced(tmp_path):
    from workflow.production.library import LocalLibrary, digest
    from workflow.orchestration.reuse import reserve
    raw=tmp_path/'raw.mp4';raw.write_bytes(b'raw')
    clean=tmp_path/'clean.mp4';clean.write_bytes(b'clean')
    output=tmp_path/'video.mp4';output.write_bytes(b'video')
    library=LocalLibrary(tmp_path,'library.sqlite3')
    common={'product':'商品','creator':'作者','review_status':'visual_review_passed','rights_status':'unverified'}
    library.register({'assets':[{'path':str(raw),'sha256':digest(raw),'duration':10,**common},
        {'path':str(clean),'sha256':digest(clean),'duration':5,'original_sha256':digest(raw),'original_offset':2,**common}]})
    plan={'job_id':'legacy','ordinal':1,'source':str(raw),'source_sha256':digest(raw),'output':str(output),
          'duration':2,'windows':[{'source_start':3,'source_end':5,'timeline_start':0,'timeline_end':2}]}
    library.record(plan,{'product':'商品','market':'BR','employee':'test','angle':'演示','hook':'看看','chinese_lines':['测试稿']})
    data=tmp_path/'data';data.mkdir();engine,sessions=database(data)
    with pytest.raises(ValueError,match='七天'):
        reserve(sessions,'new',1,'draft',[{'source_sha256':digest(clean),'start':0,'end':2}],libraries=[library.database])
    token=reserve(sessions,'new',2,'draft2',[{'source_sha256':digest(clean),'start':3,'end':5}],libraries=[library.database])
    with pytest.raises(ValueError,match='七天'):
        reserve(sessions,'other',1,'draft3',[{'source_sha256':digest(raw),'start':6,'end':7}],libraries=[library.database])
    assert token
    engine.dispose()


def test_parallel_reservations_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from workflow.orchestration.reuse import reserve
    engine,sessions=database(tmp_path)
    def attempt(job):
        try:return reserve(sessions,job,1,job,[{'source_sha256':'s','start':0,'end':3}])
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,['a','b']))
    assert sum(value is not None for value in results)==1
    engine.dispose()
