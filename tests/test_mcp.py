"""Call the actual stdio server against a running API, with isolated storage."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import imageio_ffmpeg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def running_api(tmp_path):
    with socket.socket() as socket_:
        socket_.bind(('127.0.0.1', 0))
        port = socket_.getsockname()[1]
    env = {**os.environ, 'WORKFLOW_DATA_DIR': str(tmp_path),
           'WORKFLOW_API_URL': f'http://127.0.0.1:{port}'}
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'workflow.api:create_app',
                                '--factory', '--host', '127.0.0.1', '--port', str(port)],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ready = False
        for _ in range(100):
            try:
                if httpx.get(f'http://127.0.0.1:{port}/', timeout=0.3).status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None:
                break
            time.sleep(0.05)
        assert ready, 'API server did not start'
        yield env, tmp_path
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_stdio_tools_create_query_and_import_real_video(running_api):
    env, data = running_api
    source = data / 'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=180x320:rate=24', '-t', '1', '-c:v', 'libx264',
                    str(source)], capture_output=True, check=True)

    async def verify():
        params = StdioServerParameters(command=sys.executable,
                                        args=['-m', 'workflow.mcp_server'], cwd=str(ROOT), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert 'workflow_import_video' in [t.name for t in tools.tools]
                assert all(name in [t.name for t in tools.tools] for name in
                           ('workflow_pipeline_inputs','workflow_run_pipeline','workflow_pipeline_evidence'))
                created = await session.call_tool('workflow_create_job', {'title': 'MCP 创建验收', 'product': '清洁刷'})
                assert not created.isError, created.content
                job = created.structuredContent
                assert job['status'] == 'WAITING_MATERIAL'
                imported = await session.call_tool('workflow_import_video', {'job_id': job['id'], 'path': str(source)})
                assert not imported.isError, imported.content
                queried = await session.call_tool('workflow_get_job', {'job_id': job['id']})
                assert not queried.isError
                assert queried.structuredContent['assets'][0]['width'] == 180
                assert queried.structuredContent['status'] == 'MATERIAL_READY'
                listed = await session.call_tool('workflow_list_jobs', {})
                assert listed.structuredContent['jobs'][0]['id'] == job['id']
                invalid = await session.call_tool('workflow_import_video', {'job_id': job['id'], 'path': str(data / 'missing.mp4')})
                assert invalid.isError
                assert source.exists(), 'Import must preserve the source file'
                connected=await session.call_tool('workflow_pipeline_inputs',{'job_id':job['id'],
                    'inputs':{'items':[{'ordinal':1,'asset_id':queried.structuredContent['assets'][0]['id']}]}})
                assert not connected.isError,connected.content
                progress=await session.call_tool('workflow_run_pipeline',{'job_id':job['id']})
                assert not progress.isError,progress.content
                state=progress.structuredContent['pipeline']['items'][0]
                assert state['status']=='WAITING_OBSERVATIONS'
                assert Path(state['analysis_manifest']).is_file()
                batch_job = await session.call_tool('workflow_create_job', {
                    'title': '巴西批量任务', 'product': '指定腮红', 'market': 'BR', 'count': 24,
                    'style': '达人试用'})
                assert not batch_job.isError, batch_job.content
                batch = batch_job.structuredContent
                assert batch['voiceover_locale'] == 'pt-BR'
                assert batch['batch']['planned_count'] == batch['batch']['rendered_count'] == 0
                planned = await session.call_tool('workflow_plan_batch', {
                    'job_id': batch['id'], 'concepts': [{'ordinal': 1,
                        'angle': '动作演示', 'hook': '先看它怎么被晕开',
                        'buyer_concern': '想看真实过程', 'proof_requirement': '连续点涂与刷具动作',
                        'cta_direction': '确认色号后查看商品卡'}]})
                assert not planned.isError, planned.content
                assert planned.structuredContent['batch']['planned_count'] == 1
                assert planned.structuredContent['batch']['rendered_count'] == 0
                listed = await session.call_tool('workflow_list_jobs', {})
                summary = next(j for j in listed.structuredContent['jobs'] if j['id'] == batch['id'])
                assert summary['batch_progress'] == {'target_count': 24, 'planned_count': 1, 'rendered_count': 0}

    asyncio.run(verify())
