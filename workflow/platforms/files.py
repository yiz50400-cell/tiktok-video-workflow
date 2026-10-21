"""Publish complete files after closing handles, including on Windows."""
import json
import os
from pathlib import Path
import tempfile


def atomic_bytes(path, value, *, replace=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # NTFS and POSIX: atomically create, never overwrite evidence.
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_json(path, value, *, replace=True):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8'),
                 replace=replace)
