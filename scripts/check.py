"""Run the regression suite in an isolated uv environment.

Default: base + production + test dependencies; LaMa repair tests (torch/opencv,
several GB) are skipped. Pass --with-repair to include them. Other arguments
are forwarded to pytest.
"""
from pathlib import Path
import subprocess
import sys
import os

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from workflow.platforms.runtime import find_uv

REPAIR_TESTS = ('tests/test_repair.py', 'tests/test_repair_device.py')

args = sys.argv[1:]
with_repair = '--with-repair' in args
args = [a for a in args if a != '--with-repair']
try:uv = find_uv(root)
except ValueError as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1)
command = [uv, 'run', '--no-project',
           '--with-requirements', str(root / 'requirements.lock.txt'),
           '--with-requirements', str(root / 'requirements-production.txt'),
           '--with-requirements', str(root / 'requirements-dev.txt')]
if with_repair:
    command += ['--with-requirements', str(root / 'workflow/repair/requirements.txt')]
pytest_args = [] if with_repair else [f'--ignore={path}' for path in REPAIR_TESTS]
raise SystemExit(subprocess.run([
    *command, 'python', '-X', 'utf8', '-m', 'pytest', '-q', *pytest_args, *args], cwd=root,
    env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}).returncode)
