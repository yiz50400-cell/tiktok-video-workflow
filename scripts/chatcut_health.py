#!/usr/bin/env python3
"""Probe ChatCut MCP health and optionally restart ChatCut on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow.chatcut.health import MCPError, probe_chatcut, restart_chatcut

REPORT_DIR = ROOT / ".runtime/unattended/health"


class UsageParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = UsageParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10.0, help="MCP probe timeout seconds")
    parser.add_argument(
        "--restart-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for ChatCut MCP after restart",
    )
    parser.add_argument("--apply", action="store_true", help="restart only when unhealthy")
    parser.add_argument("--force", action="store_true",
                        help="with --apply, restart even when healthy (planned/preventive restart)")
    parser.add_argument(
        "--reopen-project",
        help="optional project name/id to target after restart",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="also persist JSON under .runtime/unattended/health/",
    )
    return parser


def _write_report(payload: dict[str, object]) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S%z")
    path = REPORT_DIR / f"chatcut-health-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.restart_timeout <= 0:
        parser.error("timeouts must be greater than zero")
    if args.force and not args.apply:
        parser.error("--force requires --apply")
    if args.reopen_project and not args.apply:
        parser.error("--reopen-project requires --apply")

    try:
        health = probe_chatcut(timeout=args.timeout)
        result = restart_chatcut(
            health,
            apply=args.apply,
            force=args.force,
            timeout=args.timeout,
            restart_timeout=args.restart_timeout,
            reopen_project=args.reopen_project,
        )
    except (MCPError, NotImplementedError, OSError) as exc:
        payload = {
            "status": "unhealthy",
            "latency_ms": None,
            "reason": str(exc),
            "would_restart": True,
            "restart_attempted": bool(args.apply),
            "restarted": False,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = health.to_dict()
    payload.update(
        {
            "restart_attempted": result.attempted,
            "restarted": result.restarted,
            "restart_reason": result.reason,
            "post_restart_health": result.health.to_dict() if result.attempted else None,
            "reopened_project": result.reopened_project,
        }
    )
    if args.write_report:
        payload["report_path"] = _write_report(payload)
    print(json.dumps(payload, ensure_ascii=False))
    final_health = result.health if result.attempted else health
    return 0 if final_health.healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
