#!/usr/bin/env python3
"""Local video evidence CLI; semantic observations are supplied explicitly."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from workflow.analysis import analyze_video, save_observations  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect = subparsers.add_parser("inspect", help="Probe, detect cut candidates, extract frames and contact sheets")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.add_argument("--output", type=Path, required=True)
    inspect.add_argument("--source-url")
    inspect.add_argument("--source-evidence", type=Path)
    inspect.add_argument("--scene-threshold", type=float, default=0.25)
    inspect.add_argument("--min-shot", type=float, default=0.35)
    inspect.add_argument("--sample-every", type=float, default=3.0)
    inspect.add_argument("--max-frames", type=int, default=60)
    annotate = subparsers.add_parser("annotate", help="Validate supplied visual observations and render report")
    annotate.add_argument("--manifest", type=Path, required=True)
    annotate.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = analyze_video(args.input, args.output, source_url=args.source_url,
                                   source_evidence=args.source_evidence,
                                   threshold=args.scene_threshold, min_shot=args.min_shot,
                                   interval=args.sample_every, max_frames=args.max_frames)
            summary = {"manifest_path": str((args.output / "manifest.json").resolve()),
                       "source_sha256": result["source"]["sha256"],
                       "duration_seconds": result["metadata"]["duration_seconds"],
                       "shot_candidates": len(result["shots"]), "frames": len(result["frames"]),
                       "contact_sheets": [sheet["path"] for sheet in result["contact_sheets"]],
                       "media_analysis_status": result["media_analysis_status"]}
        else:
            summary = save_observations(args.manifest, args.observations)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
