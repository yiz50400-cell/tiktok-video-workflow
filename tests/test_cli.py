"""Codex can operate persisted jobs without a browser or running server."""
import json
import os
from pathlib import Path
import subprocess
import sys

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent


def run_cli(data_dir, *args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/workflow.py'), *args],
                          cwd=data_dir, env={**os.environ, 'WORKFLOW_DATA_DIR': str(data_dir)},
                          capture_output=True, text=True, timeout=60)


def test_codex_commands_persist_import_and_return_inline_preview_paths(tmp_path):
    created = run_cli(tmp_path, 'create', '--title', 'Codex 原生任务', '--product', '清洁刷')
    assert created.returncode == 0, created.stderr
    job = json.loads(created.stdout)
    assert job['status'] == 'WAITING_MATERIAL'
    assert 'panel_url' not in job
    source = tmp_path / 'explicit-source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=180x320:rate=24', '-t', '1', '-c:v', 'libx264',
                    str(source)], capture_output=True, check=True)
    imported = run_cli(tmp_path, 'import', job['id'], str(source))
    assert imported.returncode == 0, imported.stderr
    result = json.loads(imported.stdout)
    asset = result['imports'][0]['asset']
    assert Path(asset['local_media_path']).is_file()
    assert Path(asset['local_thumbnail_path']).is_file()
    assert source.exists()
    saved = json.loads(run_cli(tmp_path, 'show', job['id']).stdout)
    assert saved['status'] == 'MATERIAL_READY'
    assert saved['assets'][0]['local_media_path'] == asset['local_media_path']
    assert len(json.loads(run_cli(tmp_path, 'list').stdout)) == 1
    assert not (tmp_path / 'dist').exists()
    inputs=tmp_path/'pipeline-inputs.json'
    inputs.write_text(json.dumps({'items':[{'ordinal':1,'asset_id':asset['id']}]}))
    registered=run_cli(tmp_path,'pipeline-inputs',job['id'],str(inputs))
    assert registered.returncode==0,registered.stderr
    progress=run_cli(tmp_path,'run',job['id'])
    assert progress.returncode==0,progress.stderr
    state=json.loads(progress.stdout)['pipeline']['items'][0]
    assert state['status']=='WAITING_OBSERVATIONS'
    assert Path(state['analysis_manifest']).is_file()


def test_cli_error_keeps_source_and_returns_actionable_json(tmp_path):
    result = run_cli(tmp_path, 'show', 'job_missing')
    assert result.returncode == 1
    assert result.stdout == ''
    error = json.loads(result.stderr)
    assert error['ok'] is False
    assert '任务不存在' in error['error']


def test_cli_accepts_indonesia_and_registers_actual_batch_concepts(tmp_path):
    created = run_cli(tmp_path, 'create', '--title', '印尼批量任务', '--product', '指定腮红',
                      '--market', 'ID', '--count', '24', '--style', '达人试用')
    assert created.returncode == 0, created.stderr
    job = json.loads(created.stdout)
    assert job['market_profile']['voiceover_locale'] == 'id-ID'
    assert job['batch']['planned_count'] == 0
    concepts = tmp_path / 'concepts.json'
    concepts.write_text(json.dumps({'concepts': [{'ordinal': 1, 'angle': '妆效近景',
        'hook': '先展示实际晕染动作', 'buyer_concern': '怎么晕开',
        'proof_requirement': '点涂到晕染连续镜头', 'cta_direction': '查看真实色号',
        'source_asset_ids': []}]}, ensure_ascii=False))
    registered = run_cli(tmp_path, 'plan-batch', job['id'], str(concepts))
    assert registered.returncode == 0, registered.stderr
    saved = json.loads(run_cli(tmp_path, 'show', job['id']).stdout)
    assert saved['batch']['planned_count'] == 1
    assert saved['batch']['rendered_count'] == 0
