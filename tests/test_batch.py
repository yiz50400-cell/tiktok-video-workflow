"""Real persisted intake and concept registration, without claiming rendered videos."""
import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        client.headers['X-Workflow-Client'] = 'studio'
        yield client


def create(client, **changes):
    payload = {'title': '指定商品批次', 'product': '用户指定腮红', 'market': 'BR', 'count': 24}
    payload.update(changes)
    response = client.post('/api/jobs', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def concept(ordinal, **changes):
    value = {'ordinal': ordinal, 'angle': f'创意方向{ordinal}',
             'hook': f'待审开头{ordinal}', 'buyer_concern': f'购买顾虑{ordinal}',
             'proof_requirement': f'需要核准的演示镜头{ordinal}',
             'cta_direction': '核对商品卡后引导查看色号', 'source_asset_ids': []}
    value.update(changes)
    return value


@pytest.mark.parametrize(('market', 'locale'), [('MX', 'es-MX'), ('BR', 'pt-BR'),
                                                ('ID', 'id-ID'), ('PH', None)])
def test_market_selects_voiceover_language_in_persisted_task(client, market, locale):
    job = create(client, market=market)
    saved = client.get('/api/jobs/' + job['id']).json()
    assert saved['market_profile']['voiceover_locale'] == locale
    if market == 'PH':
        assert saved['market_profile']['language_status'] == 'pending_selection'


def test_requested_quantity_allocates_pending_items_not_finished_content(client):
    job = create(client)
    batch = job['batch']
    assert batch['target_count'] == 24
    assert batch['planned_count'] == batch['rendered_count'] == 0
    assert len(batch['items']) == 24
    assert [item['ordinal'] for item in batch['items'] if item['representative']] == [1, 2, 3]
    assert all(item['status'] == 'WAITING_CONCEPT' for item in batch['items'])
    assert all(item['concept'] is None for item in batch['items'])


def test_philippines_language_can_be_explicit_but_cross_market_locale_is_rejected(client):
    assert create(client, market='PH', voiceover_locale='taglish-PH')['voiceover_locale'] == 'taglish-PH'
    response = client.post('/api/jobs', json={'title': 'wrong language', 'product': '腮红',
                                            'market': 'BR', 'voiceover_locale': 'es-MX'})
    assert response.status_code == 422
    assert len(client.get('/api/jobs').json()) == 1


def test_three_concepts_fill_only_three_of_twenty_four_slots(client):
    job = create(client)
    response = client.post(f"/api/jobs/{job['id']}/batch/concepts",
                           json={'concepts': [concept(i) for i in (1, 2, 3)]})
    assert response.status_code == 200, response.text
    batch = response.json()['batch']
    assert batch['planned_count'] == 3 and batch['rendered_count'] == 0
    assert batch['status'] == 'PARTIALLY_PLANNED'
    assert batch['items'][0]['status'] == 'CONCEPT_REVIEW'
    assert batch['items'][3]['status'] == 'WAITING_CONCEPT'
    assert response.json()['status'] == 'WAITING_MATERIAL'


def test_concept_retries_are_idempotent_and_changes_preserve_old_version(client):
    job = create(client, count=1)
    route = f"/api/jobs/{job['id']}/batch/concepts"
    first = concept(1)
    client.post(route, json={'concepts': [first]})
    repeated = client.post(route, json={'concepts': [first]}).json()['batch']['items'][0]
    assert len(repeated['revisions']) == 1
    changed = client.post(route, json={'concepts': [concept(1, hook='另一种真实开头')]}).json()['batch']['items'][0]
    assert len(changed['revisions']) == 2
    assert changed['revisions'][0]['concept']['hook'] == first['hook']
    assert changed['concept']['hook'] == '另一种真实开头'
    assert changed['human_approval_status'] == 'pending_user'


def test_duplicate_content_is_rejected_atomically_even_with_different_angle_labels(client):
    job = create(client)
    first = concept(1)
    copied = {**first, 'ordinal': 2, 'angle': '换音乐版本', 'hook': ' 待审开头１！ '}
    route = f"/api/jobs/{job['id']}/batch/concepts"
    response = client.post(route, json={'concepts': [first, copied]})
    assert response.status_code == 422, response.text
    assert client.get('/api/jobs/' + job['id']).json()['batch']['planned_count'] == 0


@pytest.mark.parametrize('changes', [{'ordinal': 25}, {'source_asset_ids': ['asset_not_in_this_job']},
                                      {'hook': '  '}])
def test_invalid_concept_cannot_change_saved_batch(client, changes):
    job = create(client)
    response = client.post(f"/api/jobs/{job['id']}/batch/concepts",
                           json={'concepts': [{**concept(1), **changes}]})
    assert response.status_code == 422
    assert client.get('/api/jobs/' + job['id']).json()['batch']['planned_count'] == 0


def test_cancelled_batch_cannot_receive_concepts(client):
    job = create(client)
    client.post('/api/jobs/' + job['id'] + '/cancel')
    response = client.post(f"/api/jobs/{job['id']}/batch/concepts", json={'concepts': [concept(1)]})
    assert response.status_code == 409


def test_batch_concepts_survive_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        client.headers['X-Workflow-Client'] = 'studio'
        job = create(client)
        response = client.post(f"/api/jobs/{job['id']}/batch/concepts", json={'concepts': [concept(1)]})
        assert response.status_code == 200
    with TestClient(create_app(tmp_path)) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        saved = client.get('/api/jobs/' + job['id']).json()
        assert saved['batch']['planned_count'] == 1
        assert saved['batch']['items'][0]['concept']['hook'] == '待审开头1'
        assert saved['batch']['rendered_count'] == 0


def test_pending_philippines_language_can_be_resolved_without_recreating_batch(client):
    job = create(client, market='PH')
    client.post(f"/api/jobs/{job['id']}/batch/concepts", json={'concepts': [concept(1)]})
    response = client.post(f"/api/jobs/{job['id']}/language", json={'voiceover_locale': 'en-PH'})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['id'] == job['id'] and saved['market'] == 'PH'
    assert saved['market_profile']['voiceover_locale'] == 'en-PH'
    assert saved['market_profile']['language_status'] == 'selected'
    assert saved['batch']['planned_count'] == 1
    rejected = client.post(f"/api/jobs/{job['id']}/language", json={'voiceover_locale': 'pt-BR'})
    assert rejected.status_code == 422
    assert client.get('/api/jobs/' + job['id']).json()['voiceover_locale'] == 'en-PH'


def test_existing_jobs_gain_a_readonly_batch_view_without_changing_history(tmp_path):
    from workflow.store import database, Job
    engine, sessions = database(tmp_path)
    original = {'title': '旧任务', 'product': '原商品', 'market': 'MX', 'count': 1}
    with sessions.begin() as session:
        session.add(Job(id='job_legacy', payload=original, status='ANALYSIS_REVIEW'))
    engine.dispose()
    with TestClient(create_app(tmp_path)) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        saved = client.get('/api/jobs/job_legacy').json()
        assert saved['voiceover_locale'] == 'es-MX'
        assert saved['batch']['target_count'] == 1
        assert saved['batch']['planned_count'] == 0
        assert saved['status'] == 'ANALYSIS_REVIEW'
    engine, sessions = database(tmp_path)
    with sessions() as session:
        assert session.get(Job, 'job_legacy').payload == original
    engine.dispose()
