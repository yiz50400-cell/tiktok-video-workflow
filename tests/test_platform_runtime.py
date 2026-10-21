"""Catch duplicate work, Windows open-file publication, and UTF-8 failures."""
import json
from pathlib import Path
import subprocess
import sys
import pytest


def test_lock_blocks_a_second_process_and_releases_after_exception(tmp_path):
    from workflow.platforms.locks import acquire
    path = tmp_path / '中文 空格.lock'
    contender = (
        "import sys; from workflow.platforms.locks import acquire; "
        "f=open(sys.argv[1],'a+b'); acquire(f,blocking=False)"
    )
    with pytest.raises(RuntimeError):
        with path.open('a+b') as stream:
            acquire(stream, blocking=False)
            result = subprocess.run([sys.executable, '-c', contender, str(path)], capture_output=True)
            assert result.returncode != 0
            assert b'BlockingIOError' in result.stderr
            raise RuntimeError('cancelled work')
    assert subprocess.run([sys.executable, '-c', contender, str(path)], capture_output=True).returncode == 0


def test_atomic_json_closes_temporary_handle_before_publication(tmp_path, monkeypatch):
    from workflow.platforms.files import atomic_json
    import tempfile
    import os
    handles = []
    original_temp = tempfile.NamedTemporaryFile
    original_replace = os.replace
    def temporary(*args, **kwargs):
        handle = original_temp(*args, **kwargs)
        handles.append(handle)
        return handle
    def replace(source, dest):
        if any(not h.file.closed for h in handles):
            raise PermissionError('Windows prohibits moving an open temporary file')
        return original_replace(source, dest)
    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', temporary)
    monkeypatch.setattr(os, 'replace', replace)
    path = tmp_path / '中文记录.json'
    atomic_json(path, {'中文':'已完成'})
    assert json.loads(path.read_text(encoding='utf-8')) == {'中文':'已完成'}


def test_failed_atomic_publication_preserves_prior_bytes(tmp_path, monkeypatch):
    from workflow.platforms.files import atomic_json
    import os
    path = tmp_path / 'state.json'
    path.write_bytes(b'{"prior":true}')
    def locked(*args):
        raise PermissionError('file is occupied')
    monkeypatch.setattr(os, 'replace', locked)
    with pytest.raises(PermissionError):
        atomic_json(path, {'prior':False})
    assert path.read_bytes() == b'{"prior":true}'
    assert list(tmp_path.iterdir()) == [path]


def test_new_selection_does_not_overwrite_existing(tmp_path):
    from workflow.platforms.files import atomic_json
    path = tmp_path / 'selection.json'
    atomic_json(path, {'voice':'first'}, replace=False)
    with pytest.raises(FileExistsError):
        atomic_json(path, {'voice':'second'}, replace=False)
    assert json.loads(path.read_text(encoding='utf-8'))['voice'] == 'first'


def test_utf8_child_input_and_output_are_independent_of_terminal_encoding(tmp_path):
    from workflow.orchestration.process import run
    code = "import sys; print(sys.stdin.read()); print('镜头检查',file=sys.stderr)"
    result = run([sys.executable, '-c', code], cwd=tmp_path, timeout=10,
                 env={'PYTHONIOENCODING':'ascii'}, input='中文 配音')
    assert result.returncode == 0
    assert result.stdout.strip() == '中文 配音'
    assert result.stderr.strip() == '镜头检查'


def test_missing_packaging_dependency_uses_a_complete_isolated_environment(monkeypatch):
    from workflow.platforms.runtime import production_command
    import importlib.util
    real_find=importlib.util.find_spec
    monkeypatch.setattr(importlib.util,'find_spec',lambda name: None if name=='PIL' else real_find(name))
    workspace=Path(__file__).resolve().parents[1]
    command=production_command(workspace)
    # The child really imports every production dependency; this is not a
    # mocked command assertion. Offline mode prevents downloading packages.
    result=subprocess.run([*command,'-c',"import edge_tts,numpy,PIL,imageio_ffmpeg; print('READY')"],
                          cwd=workspace,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='READY'
