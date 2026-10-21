#!/usr/bin/env python3
"""Create and inspect evidence-bound shot plans and Chinese copy audits."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from workflow.creation import (audit_version, create_batch_contract, create_version,
                               show_version)  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Save a new plan and Chinese draft version")
    create.add_argument("--spec", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser("audit", help="Review exact draft bytes without human approval")
    audit.add_argument("--version", type=Path, required=True)
    audit.add_argument("--input", type=Path, required=True)
    show = commands.add_parser("show", help="Read a plan with its separate copy review")
    show.add_argument("--version", type=Path, required=True)
    batch = commands.add_parser("batch-contract", help="Validate a partial batch creative contract")
    batch.add_argument("--spec", type=Path, required=True)
    batch.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_version(json.loads(args.spec.read_text(encoding="utf-8")), args.output)
        elif args.command == "audit":
            result = audit_version(args.version, json.loads(args.input.read_text(encoding="utf-8")))
        elif args.command == "show":
            result = show_version(args.version)
        else:
            result = create_batch_contract(json.loads(args.spec.read_text(encoding="utf-8")))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            result = {"contract_path": str(args.output.resolve()),
                      "target_count": result["batch"]["target_count"],
                      "planned_count": result["batch"]["planned_count"],
                      "rendered_count": result["batch"]["rendered_count"],
                      "representative_count": result["batch"]["representative_count"]}
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
