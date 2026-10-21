"""Absolute-path Codex tool entry point, runnable from any task directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
