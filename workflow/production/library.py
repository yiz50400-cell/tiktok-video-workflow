"""Local production index; source media stay in place and usage stays on this PC."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import unicodedata

# Relative to --workspace. Override with --library or WORKFLOW_LIBRARY.
DEFAULT_LIBRARY = ".runtime/library/library.sqlite3"
# Development-machine ledger, only mentioned in error messages; never a silent default.
LEGACY_DEV_LIBRARY = "projects/sheglam-color-bloom/production-dev/local-library/library.sqlite3"


def default_library():
    return os.environ.get("WORKFLOW_LIBRARY") or DEFAULT_LIBRARY


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def moment(at=None):
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise ValueError("usage time must include timezone")
    return at.astimezone(timezone.utc)


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def interval(start, end, duration):
    if (any(type(value) not in (int, float) or not math.isfinite(value)
            for value in (start, end, duration)) or start < 0 or end <= start or end > duration + 1e-6):
        raise ValueError("clip range exceeds source duration")


class LocalLibrary:
    def __init__(self, workspace, database):
        self.workspace = Path(workspace).resolve()
        self.database = self.path(database)
        if self.database.suffix != ".sqlite3":
            raise ValueError("library database must use .sqlite3 suffix")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS assets (
                    sha256 TEXT PRIMARY KEY, metadata TEXT NOT NULL, paths TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS uses (
                    id TEXT PRIMARY KEY, product TEXT NOT NULL, created_at TEXT NOT NULL,
                    snapshot TEXT NOT NULL, review TEXT NOT NULL);
            """)

    def path(self, path):
        resolved = (self.workspace / path).resolve()
        if not resolved.is_relative_to(self.workspace):
            raise ValueError("library files must be inside this workspace")
        return resolved

    def relative(self, path):
        return self.path(path).relative_to(self.workspace).as_posix()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, manifest):
        """Import inspected metadata atomically, allowing aliases for identical bytes."""
        assets = manifest["assets"]
        if not isinstance(assets, list) or not assets:
            raise ValueError("asset manifest is empty")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in assets:
                filename = self.relative(item["path"])
                if self.path(filename) == self.database or digest(self.path(filename)) != item["sha256"]:
                    raise ValueError("asset hash mismatch")
                for field in ("product", "creator", "review_status", "rights_status"):
                    if not isinstance(item.get(field), str) or not item[field].strip():
                        raise ValueError(f"asset metadata requires {field}")
                interval(0, item["duration"], item["duration"])
                original = item.get("original_sha256", item["sha256"])
                offset = item.get("original_offset", 0)
                if type(offset) not in (int, float) or not math.isfinite(offset) or offset < 0:
                    raise ValueError("invalid original offset")
                if original == item["sha256"] and offset != 0:
                    raise ValueError("original source must start at zero")
                if original != item["sha256"]:
                    row = connection.execute("SELECT metadata FROM assets WHERE sha256=?", (original,)).fetchone()
                    if row is None:
                        raise ValueError("register original source before its processed variants")
                    parent = json.loads(row["metadata"])
                    if (parent["original_sha256"] != original or parent["product"] != item["product"]
                            or parent["creator"] != item["creator"]):
                        raise ValueError("invalid source lineage metadata")
                    interval(offset, offset + item["duration"], parent["duration"])
                clips = item.get("clips", [])
                if not isinstance(clips, list):
                    raise ValueError("clips must be a list")
                for clip in clips:
                    interval(clip["start"], clip["end"], item["duration"])
                    if not isinstance(clip.get("role"), str) or not clip["role"].strip():
                        raise ValueError("clip role is required")
                metadata = {key: value for key, value in item.items() if key not in ("path", "sha256")}
                metadata.update(original_sha256=original, original_offset=offset)
                old = connection.execute("SELECT metadata, paths FROM assets WHERE sha256=?",
                                         (item["sha256"],)).fetchone()
                paths = [filename]
                if old:
                    old_metadata = json.loads(old["metadata"])
                    if ({key: value for key, value in old_metadata.items() if key != "clips"}
                            != {key: value for key, value in metadata.items() if key != "clips"}):
                        raise ValueError("existing asset metadata or lineage cannot change silently")
                    metadata["clips"] = list({encoded(clip): clip for clip in
                                              old_metadata.get("clips", []) + clips}.values())
                    paths = sorted(set(json.loads(old["paths"]) + paths))
                connection.execute("INSERT INTO assets VALUES (?, ?, ?) ON CONFLICT(sha256) "
                                   "DO UPDATE SET paths=excluded.paths, metadata=excluded.metadata",
                                   (item["sha256"], encoded(metadata), encoded(paths)))
        return {"registered_assets": len(assets), "distinct_assets": len(self.inventory())}

    def inventory(self, product=None):
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM assets ORDER BY sha256").fetchall()
        result = []
        for row in rows:
            metadata = json.loads(row["metadata"])
            if product is not None and metadata["product"] != product:
                continue
            paths = json.loads(row["paths"])
            valid = None
            for filename in paths:
                try:
                    if digest(self.path(filename)) == row["sha256"]:
                        valid = filename
                        break
                except OSError:
                    continue
            result.append({**metadata, "sha256": row["sha256"], "paths": paths,
                           "path": valid, "available": valid is not None})
        return result

    def history(self, product=None):
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM uses ORDER BY created_at DESC, id").fetchall()
        return [{**json.loads(row["snapshot"]), "id": row["id"], "created_at": row["created_at"],
                 "review": json.loads(row["review"])} for row in rows
                if product is None or row["product"] == product]

    def _snapshot(self, plan, context):
        for key in ("product", "market", "employee", "angle", "hook"):
            if not isinstance(context.get(key), str) or not context[key].strip():
                raise ValueError(f"usage context requires {key}")
        lines = context.get("chinese_lines")
        if not isinstance(lines, list) or not lines or any(not isinstance(line, str) or not line.strip() for line in lines):
            raise ValueError("usage context requires exact Chinese lines")
        sources = plan.get("sources") or [{"path": plan["source"], "sha256": plan["source_sha256"]}]
        with self.connection() as connection:
            known = {row["sha256"]: json.loads(row["metadata"])
                     for row in connection.execute("SELECT sha256, metadata FROM assets")}
        for source in sources:
            if digest(self.path(source["path"])) != source["sha256"]:
                raise ValueError("plan source hash mismatch")
            if source["sha256"] not in known or known[source["sha256"]]["product"] != context["product"]:
                raise ValueError("plan source is not registered for this product")
        shots = []
        cursor = 0
        for shot in plan["windows"]:
            index = shot.get("source_index", 0)
            if type(index) is not int or not 0 <= index < len(sources):
                raise ValueError("invalid shot source index")
            source = sources[index]
            metadata = known[source["sha256"]]
            start, end = shot["source_start"], shot["source_end"]
            interval(start, end, metadata["duration"])
            if (not math.isclose(shot["timeline_start"], cursor, abs_tol=1e-6)
                    or not math.isclose(shot["timeline_end"] - cursor, end - start, abs_tol=1e-6)):
                raise ValueError("shot duration or continuous timeline mismatch")
            cursor = shot["timeline_end"]
            shots.append({"source_sha256": source["sha256"],
                          "original_sha256": metadata["original_sha256"],
                          "original_start": round(metadata["original_offset"] + start, 6),
                          "original_end": round(metadata["original_offset"] + end, 6),
                          "creator": metadata["creator"]})
        if not shots or not math.isclose(cursor, plan["duration"], abs_tol=1e-6):
            raise ValueError("total duration mismatch")
        return {**context, "job_id": plan["job_id"], "ordinal": plan["ordinal"],
                "plan_sha256": hashlib.sha256(encoded(plan).encode()).hexdigest(),
                "shots": shots, "output": self.relative(plan["output"]),
                "script_key": normalized("".join(lines)), "hook_key": normalized(context["hook"])}

    def _recent(self, product, at, days):
        if type(days) not in (int, float) or not math.isfinite(days) or days < 0:
            raise ValueError("cooldown days must be nonnegative")
        cutoff = moment(at) - timedelta(days=days)
        return [item for item in self.history(product) if cutoff <= datetime.fromisoformat(item["created_at"]) <= moment(at)]

    @staticmethod
    def _overlaps(shot, previous):
        return (shot["original_sha256"] == previous["original_sha256"]
                and max(shot["original_start"], previous["original_start"])
                < min(shot["original_end"], previous["original_end"]) - 1e-6)

    def inspect(self, plan, context, *, at=None, cooldown_days=7):
        snapshot = self._snapshot(plan, context)
        history = self.history(context["product"])
        recent = self._recent(context["product"], at, cooldown_days)
        overlap = [{**shot, "previous_use": item["id"]} for shot in snapshot["shots"]
                   for item in recent if any(self._overlaps(shot, old) for old in item["shots"])]
        return {"scope": "this_local_library", "recent_intervals": overlap,
                "exact_script_matches": [item["id"] for item in history if item["script_key"] == snapshot["script_key"]],
                "exact_hook_matches": [item["id"] for item in history if item["hook_key"] == snapshot["hook_key"]],
                "semantic_similarity": "requires_codex_review", "cooldown_days": cooldown_days}

    def candidates(self, product, role=None, *, at=None, cooldown_days=7):
        recent = self._recent(product, at, cooldown_days)
        candidates = []
        for item in self.inventory(product):
            if not item["available"] or item["review_status"] not in ("sampled_visual_review_passed", "visual_review_passed"):
                continue
            for clip in item.get("clips", []):
                if role is not None and clip["role"] != role:
                    continue
                mapped = {"original_sha256": item["original_sha256"],
                          "original_start": item["original_offset"] + clip["start"],
                          "original_end": item["original_offset"] + clip["end"]}
                if any(self._overlaps(mapped, shot) for use in recent for shot in use["shots"]):
                    continue
                candidates.append({**clip, **mapped, "path": item["path"], "sha256": item["sha256"],
                                   "creator": item["creator"], "rights_status": item["rights_status"],
                                   "review_status": item["review_status"]})
        return candidates

    def record(self, plan, context, *, at=None):
        snapshot = self._snapshot(plan, context)
        identifier = hashlib.sha256(encoded(snapshot).encode()).hexdigest()
        snapshot["output_sha256"] = digest(self.path(plan["output"]))
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT snapshot FROM uses WHERE id=?", (identifier,)).fetchone()
            if old:
                if json.loads(old["snapshot"])["output_sha256"] != snapshot["output_sha256"]:
                    raise ValueError("recorded output changed; keep it in a new numbered version")
                return {"id": identifier, "new_record": False}
            connection.execute("INSERT INTO uses VALUES (?, ?, ?, ?, ?)",
                (identifier, context["product"], moment(at).isoformat(), encoded(snapshot),
                 encoded({"status": "pending_user", "evidence": None})))
        return {"id": identifier, "new_record": True}

    def existing(self, plan, context):
        identifier = hashlib.sha256(encoded(self._snapshot(plan, context)).encode()).hexdigest()
        with self.connection() as connection:
            row = connection.execute("SELECT snapshot FROM uses WHERE id=?", (identifier,)).fetchone()
            if row is None:
                output = self.relative(plan["output"])
                if any(json.loads(item["snapshot"])["output"] == output
                       for item in connection.execute("SELECT snapshot FROM uses")):
                    raise ValueError("output already registered; keep changed plans in a new numbered version")
        if row is None:
            return None
        snapshot = json.loads(row["snapshot"])
        if digest(self.path(plan["output"])) != snapshot["output_sha256"]:
            raise ValueError("recorded output changed; keep it in a new numbered version")
        return {"id": identifier, "new_record": False}

    def review(self, identifier, status, evidence):
        if status not in ("accepted", "rejected", "positive_feedback") or not evidence.get("user_reply"):
            raise ValueError("review requires explicit user feedback evidence")
        with self.connection() as connection:
            row = connection.execute("SELECT snapshot FROM uses WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError("unknown usage record")
            snapshot = json.loads(row["snapshot"])
            if digest(self.path(snapshot["output"])) != snapshot["output_sha256"]:
                raise ValueError("reviewed output hash mismatch")
            connection.execute("UPDATE uses SET review=? WHERE id=?",
                (encoded({"status": status, "evidence": evidence}), identifier))
        return {"id": identifier, "review": status}


def add_library_parser(subparsers):
    parser = subparsers.add_parser("library", help="本机素材索引和使用台账")
    commands = parser.add_subparsers(dest="library_command", required=True)
    for name in ("import", "inventory", "candidates", "check", "record", "history", "review"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", type=Path, default=Path.cwd())
        command.add_argument("--library", default=None,
                             help="台账 .sqlite3（相对 --workspace）；默认 WORKFLOW_LIBRARY 或 " + DEFAULT_LIBRARY)
        if name == "import":
            command.add_argument("manifest", type=Path)
        if name in ("inventory", "history", "candidates"):
            command.add_argument("--product", required=name == "candidates")
        if name == "candidates":
            command.add_argument("--role")
            command.add_argument("--cooldown-days", type=float, default=7)
        if name in ("check", "record"):
            command.add_argument("plan", type=Path)
            command.add_argument("context", type=Path)
        if name == "check":
            command.add_argument("--cooldown-days", type=float, default=7)
        if name == "review":
            command.add_argument("id")
            command.add_argument("status", choices=("accepted", "rejected", "positive_feedback"))
            command.add_argument("evidence", type=Path)


def run_library_command(args):
    database = args.library or default_library()
    if args.library_command != "import" and not (Path(args.workspace) / database).resolve().is_file():
        # Do not silently create an empty ledger: a missing file means wrong workspace/config.
        raise ValueError(f"未找到本机素材台账：{database}（相对 {Path(args.workspace).resolve()}）。"
                         "请用 --library 或环境变量 WORKFLOW_LIBRARY 指定；首次使用先运行 library import。"
                         f"开发机旧台账位于 {LEGACY_DEV_LIBRARY}。")
    library = LocalLibrary(args.workspace, database)

    def read(path):
        return json.loads(library.path(path).read_text(encoding="utf-8"))

    name = args.library_command
    if name == "import":
        return library.register(read(args.manifest))
    if name == "inventory":
        return library.inventory(args.product)
    if name == "candidates":
        return library.candidates(args.product, args.role, cooldown_days=args.cooldown_days)
    if name == "history":
        return library.history(args.product)
    if name == "check":
        return library.inspect(read(args.plan), read(args.context), cooldown_days=args.cooldown_days)
    if name == "record":
        return library.record(read(args.plan), read(args.context))
    if name == "review":
        return library.review(args.id, args.status, read(args.evidence))
