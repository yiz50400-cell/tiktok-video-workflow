"""Collect and validate a bounded set of TikTok product-video candidates."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.collection import collect


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-file", type=Path, help="Saved candidate evidence JSON")
    parser.add_argument("--url", action="append", default=[], help="TikTok video URL; repeatable")
    parser.add_argument("--local-source", action="append", default=[], metavar="ID=PATH",
                        help="Import an existing original for this candidate")
    parser.add_argument("--existing-raw", action="append", default=[], metavar="ID=PATH",
                        help="Reference and hash an existing original without copying")
    parser.add_argument("--existing-preview", action="append", default=[], metavar="ID=PATH",
                        help="Verify an existing compatible preview without transcoding")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true", help="Retrieve candidates without local originals")
    parser.add_argument("--resolve-urls", action="store_true", help="Resolve TikTok URLs live with yt-dlp")
    parser.add_argument("--max-candidates", type=int, default=8)
    args = parser.parse_args(argv)
    if not 1 <= args.max_candidates <= 8:
        parser.error("--max-candidates must be 1..8")
    seeds = []
    if args.seed_file:
        data = json.loads(args.seed_file.read_text(encoding="utf-8"))
        seeds = data["candidates"] if isinstance(data, dict) else data
    if not seeds and not args.url:
        parser.error("provide --seed-file or --url")
    def parse_mappings(values, flag):
        mappings = {}
        for value in values:
            if "=" not in value:
                parser.error(f"{flag} requires ID=PATH")
            identity, path = value.split("=", 1)
            mappings[identity] = path
        return mappings

    local_sources = parse_mappings(args.local_source, "--local-source")
    existing_raw = parse_mappings(args.existing_raw, "--existing-raw")
    existing_preview = parse_mappings(args.existing_preview, "--existing-preview")
    report = collect(seeds=seeds, urls=args.url, brand=args.brand, product=args.product,
                     output_dir=args.output_dir, local_sources=local_sources,
                     existing_raw=existing_raw, existing_preview=existing_preview,
                     download=args.download, resolve_urls=args.resolve_urls,
                     max_candidates=args.max_candidates)
    print(json.dumps({"report_path": str((args.output_dir / "collection.json").resolve()),
                      "discovery": report["discovery"],
                      "results": [{"id": x["id"], "resolution": x["url_resolution_status"],
                                   "retrieval": x["retrieval_status"], "failure": x["failure_reason"]}
                                  for x in report["candidates"]]}, ensure_ascii=False, indent=2))
    return 0 if all(item["retrieval_status"] not in {"failed", "raw_acquired_preview_failed"}
                    for item in report["candidates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
