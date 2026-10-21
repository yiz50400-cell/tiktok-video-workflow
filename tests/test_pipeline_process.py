import subprocess
import sys
import time
import os
import pytest

from workflow.orchestration.process import run


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires Windows exit codes')
@pytest.mark.parametrize('exit_code',[23,0xffffffff])
def test_windows_supervisor_preserves_workload_exit_code(tmp_path,exit_code):
    code="import ctypes; ctypes.windll.kernel32.ExitProcess(%d)" % exit_code
    result=run([sys.executable,'-c',code],cwd=tmp_path,timeout=10)
    assert result.returncode==exit_code


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires real Windows Job Object')
def test_windows_supervisor_cleans_child_when_workload_parent_already_exited(tmp_path):
    ready=tmp_path/'child-started'
    marker=tmp_path/'orphan'
    child=("from pathlib import Path; import time; Path(%r).write_text('ready'); "
           "time.sleep(4); Path(%r).write_text('orphan')") % (str(ready),str(marker))
    parent=("import subprocess,sys,time; from pathlib import Path; "
            "subprocess.Popen([sys.executable,'-c',%r]); "
            "\nwhile not Path(%r).exists(): time.sleep(.01)") % (child,str(ready))
    result=run([sys.executable,'-c',parent],cwd=tmp_path,timeout=2)
    assert result.returncode == 0
    assert ready.exists(), 'workload did not actually start'
    time.sleep(4.2)
    assert not marker.exists()


def test_timed_out_media_child_cannot_write_after_parent_is_stopped(tmp_path):
    marker=tmp_path/'unexpected-output'
    child="import time; from pathlib import Path; time.sleep(.5); Path(%r).write_text('orphan')" % str(marker)
    parent="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(10)" % child
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable,'-c',parent],cwd=tmp_path,timeout=.15)
    time.sleep(.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name=='nt', reason='POSIX signals; Windows is covered by Job Object tests')
def test_timeout_kills_term_ignoring_descendant_even_after_pipes_close(tmp_path):
    ready=tmp_path/'ready';marker=tmp_path/'orphan'
    child=("import signal,time; from pathlib import Path; "
           "signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(%r).write_text('ready'); "
           "time.sleep(1); Path(%r).write_text('orphan')") % (str(ready),str(marker))
    parent=("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r], "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(10)") % child
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable,'-c',parent],cwd=tmp_path,timeout=.3)
    assert ready.exists()
    time.sleep(1.1)
    assert not marker.exists()


@pytest.mark.skipif(os.name=='nt', reason='Windows nested descendants share the owning Job')
def test_outer_timeout_cleans_nested_stage_process_group(tmp_path):
    from pathlib import Path
    ready=tmp_path/'ready';marker=tmp_path/'nested-orphan'
    child="import time; from pathlib import Path; Path(%r).write_text('ready'); time.sleep(1); Path(%r).write_text('orphan')" % (str(ready),str(marker))
    code=("import sys; from pathlib import Path; sys.path.insert(0,%r); "
          "from scripts.run_local_batch import _run; "
          "_run([sys.executable,'-c',%r],Path(%r))") % (str(Path(__file__).resolve().parents[1]),child,str(tmp_path/'stage.log'))
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable,'-c',code],cwd=tmp_path,timeout=.4)
    assert ready.exists()
    time.sleep(1.1)
    assert not marker.exists()
