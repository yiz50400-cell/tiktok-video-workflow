"""Stop the complete isolated media process group when its bounded run times out."""
import os
import signal
import subprocess
import sys
from pathlib import Path


def _stop(process):
    if os.name == 'nt':
        if process.poll() is None:
            process.kill()  # Job Object owned by supervisor kills all descendants.
    else:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    try:process.communicate(timeout=5)
    except subprocess.TimeoutExpired:pass
    finally:
        # Pipe EOF only proves the leader exited. A descendant may redirect
        # its pipes and ignore TERM; always clear remaining group members.
        if os.name == 'nt':
            if process.poll() is None:process.kill()
        else:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
    process.communicate(timeout=5)


def run(command,*,cwd,timeout,env=None,input=None):
    environment={**os.environ,**(env or {}),'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
    launch=command
    if os.name == 'nt':
        supervisor=Path(__file__).resolve().parents[1]/'platforms/windows_job.py'
        launch=[sys.executable,'-X','utf8',str(supervisor),*command]
    with subprocess.Popen(launch,cwd=cwd,env=environment,
                          stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,encoding='utf-8',errors='replace',
                          start_new_session=os.name!='nt') as process:
        try:
            stdout,stderr=process.communicate(input=input,timeout=timeout)
        except BaseException:
            _stop(process)
            raise
        return subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
