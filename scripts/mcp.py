"""Absolute-path entry point for Codex; no shell or working-directory assumptions."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.mcp_server import mcp

mcp.run(transport='stdio')
