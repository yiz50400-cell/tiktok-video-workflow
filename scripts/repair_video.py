"""Standalone command for local LaMa repair jobs."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.repair import run_repair, task_status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local LaMa video repair; outputs always need visual review")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("source", "mask-spec", "model", "output-root"):
        run.add_argument(f"--{name}", type=Path, required=True)
    run.add_argument("--start", type=float, required=True)
    run.add_argument("--duration", type=float, required=True)
    run.add_argument("--device", choices=['cpu', 'auto', 'mps', 'cuda'], default='auto')
    status = sub.add_parser("status")
    status.add_argument("--output-root", type=Path, required=True)
    status.add_argument("--task-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run_repair(args.source, args.mask_spec, args.model,
                                args.output_root, args.start, args.duration, device=args.device)
        else:
            result = task_status(args.output_root, args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "ERROR", "type": type(error).__name__, "message": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
