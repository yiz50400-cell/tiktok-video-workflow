import pytest
from workflow.production.continuity import validate_continuity


def configuration(cuts,short_reviews=()):
    return {'sources':[{'sha256':'s'}], 'windows':[{'source_start':10,'source_end':15,'timeline_start':3}],
        'source_continuity_review':{'status':'passed_codex_source_review','reviewer':'test',
            'shots':[{'index':1,'source_sha256':'s','source_start':10,'source_end':15,
                      'internal_cuts':cuts,'action_complete':True,'short_shot_reviews':list(short_reviews)}]}}


def test_source_cut_is_an_actual_short_shot_and_requires_review():
    with pytest.raises(ValueError,match='短于2秒'):validate_continuity(configuration([11]))
    result=validate_continuity(configuration([11],[{'range':[10,11],'reason':'独立结果提示，已完整看清；测试专用'}]))
    assert result['internal_cut_seconds']==[4]


def test_source_review_cannot_hide_changed_span_or_missing_action():
    config=configuration([]);config['windows'][0]['source_end']=14
    with pytest.raises(ValueError,match='不匹配'):validate_continuity(config)
    config=configuration([]);config['source_continuity_review']['shots'][0]['action_complete']=False
    with pytest.raises(ValueError,match='不匹配'):validate_continuity(config)
