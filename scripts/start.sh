#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
if [ ! -x .venv/bin/python ]; then
  echo "请先按 README.md 安装项目依赖。"
  exit 1
fi
exec .venv/bin/python -m uvicorn workflow.api:create_app --factory --host 127.0.0.1 --port 8765
