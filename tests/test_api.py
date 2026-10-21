from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import imageio_ffmpeg
import pytest

from workflow.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        client.headers['X-Workflow-Client'] = 'studio'
        yield client


def create_job(client, **changes):
    body = {'title': '厨房清洁刷 · 第一条', 'product': '旋转清洁刷', 'market': 'MX',
            'count': 1, 'budget': 20, 'owner': '测试员工', 'sources': [], 'notes': '先看效果'}
    body.update(changes)
    response = client.post('/api/jobs', json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope='session')
def video(tmp_path_factory):
    file = tmp_path_factory.mktemp('media') / 'test.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=180x320:rate=24', '-t', '1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(file)], check=True, capture_output=True)
    return file.read_bytes()


def test_created_job_survives_app_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        c.headers['Authorization']='Bearer '+(c.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        c.headers['X-Workflow-Client'] = 'studio'
        job = create_job(c)
    with TestClient(create_app(tmp_path)) as c:
        c.headers['Authorization']='Bearer '+(c.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        saved = c.get('/api/jobs/' + job['id']).json()
        assert saved['product'] == '旋转清洁刷'
        assert saved['status'] == 'WAITING_MATERIAL'
        assert saved['assets'] == []
        assert len(c.get('/api/jobs').json()) == 1


@pytest.mark.parametrize('changes', [{'title': '  '}, {'count': 0}, {'count': 101},
                                     {'budget': -1}, {'sources': ['file:///etc/passwd']},
                                     {'market': 'XX'}])
def test_invalid_job_is_not_saved(client, changes):
    body = {'title': '清洁刷', 'product': '清洁刷', 'count': 1, 'market': 'MX', 'sources': []}
    body.update(changes)
    assert client.post('/api/jobs', json=body).status_code == 422
    assert client.get('/api/jobs').json() == []


def test_upload_is_playable_and_duplicate_is_not_counted_twice(client, video):
    job = create_job(client)
    url = '/api/jobs/' + job['id'] + '/assets'
    first = client.post(url, files={'file': ('素材.mp4', video, 'video/mp4')})
    assert first.status_code == 201, first.text
    asset = first.json()['asset']
    assert asset['width'] == 180 and asset['height'] == 320
    assert 0.9 <= asset['duration'] <= 1.1
    assert client.get(asset['media_url']).content == video
    assert client.get(asset['media_url'], headers={'Range': 'bytes=0-99'}).status_code == 206
    again = client.post(url, files={'file': ('改名.mp4', video, 'video/mp4')})
    assert again.status_code == 200
    saved = client.get('/api/jobs/' + job['id']).json()
    assert len(saved['assets']) == 1
    assert saved['status'] == 'MATERIAL_READY'
    assert not any(e['kind'] == 'analysis_complete' for e in saved['events'])


def test_bad_video_is_not_stored(client):
    job = create_job(client)
    response = client.post('/api/jobs/' + job['id'] + '/assets',
                           files={'file': ('fake.mp4', b'not a video', 'video/mp4')})
    assert response.status_code == 422
    assert client.get('/api/jobs/' + job['id']).json()['assets'] == []


def test_missing_job_returns_404(client):
    assert client.get('/api/jobs/missing').status_code == 404


def test_unimplemented_render_cannot_advance(client):
    job = create_job(client)
    assert client.post('/api/jobs/' + job['id'] + '/advance',
                       json={'stage': 'publish'}).status_code == 409
    assert client.get('/api/jobs/' + job['id']).json()['status'] == 'WAITING_MATERIAL'


def test_capabilities_scope_ends_at_local_delivery(client):
    capabilities = client.get('/api/capabilities').json()
    assert 'publishing' not in capabilities['pending']
    assert capabilities['out_of_scope'] == ['account_access', 'platform_upload',
                                            'platform_draft', 'product_attachment', 'publishing',
                                            'team_auth']
    assert capabilities['standalone_modules']['production']['integrated'] is True
    assert 'pipeline_run' in capabilities['implemented']
    assert capabilities['batch_render'] is True
    assert capabilities['standalone_modules']['local_package']['platform_action'] is False


def test_cancelled_job_cannot_accept_media(client, video):
    job = create_job(client)
    assert client.post('/api/jobs/' + job['id'] + '/cancel').status_code == 200
    response = client.post('/api/jobs/' + job['id'] + '/assets',
                           files={'file': ('test.mp4', video, 'video/mp4')})
    assert response.status_code == 409
    assert client.get('/api/jobs/' + job['id']).json()['status'] == 'CANCELLED'


def test_cross_origin_write_is_rejected(client):
    response = client.post('/api/jobs', json={'title': 'task', 'product': 'brush'},
                           headers={'Origin': 'https://other.example'})
    assert response.status_code == 403


def test_api_without_local_session_is_rejected(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/jobs').status_code == 401
