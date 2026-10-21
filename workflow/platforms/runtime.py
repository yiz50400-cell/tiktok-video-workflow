"""Locate source/distribution executables without assuming Unix paths."""
from pathlib import Path
import os
import shutil
import sys
import importlib.util


def find_uv(workspace=None):
    suffix='.exe' if os.name=='nt' else ''
    candidates=[]
    if workspace is not None:candidates.append(Path(workspace)/'runtime'/('uv'+suffix))
    found=shutil.which('uv')
    if found:candidates.append(Path(found))
    candidates.extend([Path.home()/'.local/bin'/('uv'+suffix),Path.home()/'.cargo/bin'/('uv'+suffix)])
    for path in candidates:
        if path.is_file():return str(path)
    raise ValueError('未找到 uv；源码运行请先安装依赖，发行包需要内置运行环境')


def find_codex(workspace=None):
    suffix='.exe' if os.name=='nt' else ''
    candidates=[]
    if workspace is not None:candidates.append(Path(workspace)/'runtime'/('codex'+suffix))
    found=shutil.which('codex')
    if found:candidates.append(Path(found))
    # npm global on Windows often lands in %APPDATA%\npm\codex.cmd
    if os.name=='nt':
        found_cmd=shutil.which('codex.cmd')
        if found_cmd:candidates.append(Path(found_cmd))
        appdata=Path(os.environ.get('APPDATA') or (Path.home()/'AppData'/'Roaming'))
        candidates.extend([
            appdata/'npm'/('codex'+suffix),
            appdata/'npm'/'codex.cmd',
            Path.home()/'.local'/'bin'/('codex'+suffix),
        ])
    else:
        candidates.append(Path.home()/'.local/bin'/('codex'+suffix))
    for path in candidates:
        if path.is_file():return str(path)
    raise ValueError('未找到 Codex 运行程序')


def production_command(workspace):
    # edge_tts alone is insufficient: packaging also needs Pillow and numpy.
    modules=('edge_tts','numpy','PIL','imageio_ffmpeg')
    if not getattr(sys,'frozen',False) and all(importlib.util.find_spec(name) is not None for name in modules):
        return [sys.executable,'-X','utf8']
    command=[find_uv(workspace),'run','--offline','--no-project']
    base_lock=Path(workspace)/'requirements.lock.txt'
    if base_lock.is_file():command+=['--with-requirements',str(base_lock)]
    return [*command,'--with-requirements',str(Path(workspace)/'requirements-production.txt'),'python','-X','utf8']
