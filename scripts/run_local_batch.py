#!/usr/bin/env python3
"""Resume an approved local production batch without repeating successful stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.orchestration import StageStore, preflight
from workflow.platforms.locks import acquire, release
from scripts.produce_video import validate_render_input


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def latest_localization(directory: Path) -> tuple[Path, Path]:
    candidates = sorted(directory.glob("localization-v[0-9][0-9][0-9][0-9].json"))
    if not candidates:
        raise ValueError("localization evidence is missing")
    localization = candidates[-1]
    version = localization.stem.rsplit("-", 1)[-1]
    audit = directory / f"localization-audit-{version}.json"
    if not audit.exists() and version == "v0001":
        audit = directory / "localization-audit.json"
    return localization, audit


def _run(command: list[str], log_path: Path) -> None:
    # Stage processes inherit the outer pipeline's group/Job. Creating another
    # POSIX session here would escape the owning pipeline timeout cleanup.
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    log_path.write_text((result.stdout or "") + ("\n" + result.stderr if result.stderr else ""), encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"stage command failed ({result.returncode}); see {log_path}")

def _actual_plan(plan: dict) -> dict:
    actual = json.loads(json.dumps(plan))
    for key in ('source', 'voice_audio', 'output', 'srt', 'voice_approval',
                'voice_selection', 'voice_recording', 'word_metadata'):
        if actual.get(key): actual[key] = str((ROOT / actual[key]).resolve())
    for collection in ('sources', 'bound_files', 'overlays', 'sound_effects'):
        for item in actual.get(collection, []):
            item['path'] = str((ROOT / item['path']).resolve())
    return actual

def _plan_fingerprint(plan: dict) -> str:
    values = [json.dumps(plan, ensure_ascii=False, sort_keys=True)]
    if not plan.get('sources') and plan.get('source'):
        values.append(digest((ROOT / plan['source']).resolve()))
    for collection in ('sources', 'bound_files', 'overlays', 'sound_effects'):
        for item in plan.get(collection, []):
            path = (ROOT / item['path']).resolve()
            values.append(digest(path))
    for key in ('voice_audio', 'voice_approval', 'voice_selection', 'voice_recording', 'word_metadata'):
        if plan.get(key): values.append(digest((ROOT / plan[key]).resolve()))
    return hashlib.sha256(':'.join(values).encode()).hexdigest()


def run_batch(directory: Path, *, python: str = sys.executable, stages: set[str] | None = None) -> dict:
    directory=Path(directory).resolve()
    if not directory.is_dir():raise ValueError(f'batch directory does not exist: {directory}')
    with (directory/'.production.lock').open('a+b') as lock:
        try:acquire(lock,blocking=False)
        except BlockingIOError as exc:raise ValueError('this production directory is already running') from exc
        try:return _run_batch(directory,python=python,stages=stages)
        finally:release(lock)


def _run_batch(directory: Path, *, python: str = sys.executable, stages: set[str] | None = None) -> dict:
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError(f"batch directory does not exist: {directory}")
    execution = directory / "execution.json"
    creative = directory / "creative.json"
    approval = directory / "zh-approval.json"
    localization, audit = latest_localization(directory)
    binding = {"plan": str(execution), "localization": str(localization), "audit": str(audit)}
    # This gate runs before any network TTS or video render work.
    preflight(binding)
    store = StageStore(directory)
    requested = stages or {"prepare", "render", "qa"}
    unknown = requested - {"prepare", "render", "qa"}
    if unknown:
        raise ValueError(f"unknown stage(s): {', '.join(sorted(unknown))}")

    def prepare():
        log = directory / "stage-prepare.log"
        _run([python, str(ROOT / "scripts/prepare_packaged_video.py"), str(directory)], log)
        outputs = [directory / name for name in ("execution.json", "preparation-timing.json", "voice-recording.json")]
        prepared = json.loads((directory / "execution.json").read_text(encoding='utf-8'))
        for collection in ('bound_files','overlays','sound_effects'):
            outputs.extend((ROOT / item['path']).resolve() for item in prepared.get(collection,[]))
        if prepared.get('voice_audio'):
            outputs.append((ROOT / prepared['voice_audio']).resolve())
        outputs=list(dict.fromkeys(outputs))
        return {"outputs": [str(path) for path in outputs], "log": str(log)}

    input_files = [creative, approval, localization, audit]
    for extra in (directory / "edit-layout.json", directory / "approved-template.json",
                  directory / "source-clean.mp4", ROOT / "scripts/prepare_packaged_video.py"):
        if extra.exists():
            input_files.append(extra)
    input_hash = ":".join(digest(path) for path in input_files)
    results = {"directory": str(directory), "stages": {}}
    if "prepare" in requested:
        prior=store._read().get('prepare',{})
        if prior.get('status')=='SUCCEEDED' and execution.exists():
            validate_render_input(_actual_plan(json.loads(execution.read_text(encoding='utf-8'))))
        results["stages"]["prepare"] = store.run("prepare", input_hash, prepare)
    elif not execution.exists():
        raise ValueError("execution.json is missing; run prepare first")

    if not execution.exists():
        raise ValueError("prepare did not produce execution.json")
    plan = json.loads(execution.read_text(encoding="utf-8"))

    def render():
        log = directory / "stage-render.log"
        _run([python, str(ROOT / "scripts/produce_video.py"), "render", str(execution), "--workspace", str(ROOT)], log)
        output = ROOT / plan["output"]
        subtitle = ROOT / plan["srt"]
        return {"outputs": [str(path) for path in (output, subtitle)], "log": str(log)}

    actual = _actual_plan(plan)
    render_hash = _plan_fingerprint(plan)
    if "render" in requested:
        output_path = ROOT / plan["output"]
        if output_path.exists() and not store.can_retry("render", render_hash):
            prior = store._read().get("render", {})
            if not (prior.get("input") == render_hash and prior.get("status") in ("SUCCEEDED","RUNNING")):
                raise ValueError("render output already exists without a matching successful stage record; use a new numbered directory")
        validate_render_input(actual)
        if plan.get('localization_sha256') != digest(localization):
            raise ValueError('execution does not bind current localization')
        results["stages"]["render"] = store.run("render", render_hash, render)
    elif "qa" in requested and not (ROOT / plan["output"]).exists():
        raise ValueError("render output is missing; run render first")

    def qa():
        log = directory / "stage-qa.log"
        _run([python, str(ROOT / "scripts/verify_packaged_video.py"), str(directory)], log)
        outputs = [directory / name for name in ("qa.json", "cut-review.jpg", "action-review.jpg", "contact-sheet.jpg")]
        return {"outputs": [str(path) for path in outputs], "log": str(log)}

    if "qa" in requested:
        validate_render_input(actual)
        if plan.get('localization_sha256') != digest(localization):
            raise ValueError('execution does not bind current localization')
        qa_hash = _plan_fingerprint(plan) + ":" + digest(ROOT / plan["output"])
        results["stages"]["qa"] = store.run("qa", qa_hash, qa)
    (directory / "batch-run-result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--stage", choices=["prepare", "render", "qa"], action="append",
                        help="run only these stages; defaults to all three")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_batch(args.directory, stages=set(args.stage) if args.stage else None),
                         ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
