#!/usr/bin/env python3
"""Prepare an existing video for employee publishing on this machine."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.publishing import prepare_publish


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地发布准备：沿用成片及已审核文案，输出中文预览")
    parser.add_argument("request", type=Path, help="发布准备JSON")
    parser.add_argument("--output", required=True, type=Path, help="新编号准备包目录")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="证据相对路径基准，默认项目目录")
    args = parser.parse_args(argv)
    try:
        result = prepare_publish(args.request, args.output, workspace=args.workspace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok":False,"error_zh":str(exc),"platform_action":False},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
