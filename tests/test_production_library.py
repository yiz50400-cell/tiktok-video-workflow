"""Local library behavior across retries, source variants and workspace moves."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest


def library_type():
    from workflow.production.library import LocalLibrary
    return LocalLibrary


def asset(root, name, content, **fields):
    path = root / name
    path.write_bytes(content)
    return {"path": name, "sha256": hashlib.sha256(content).hexdigest(),
            "product": "blush", "creator": "creator-a", "duration": 20,
            "review_status": "sampled_visual_review_passed", "rights_status": "unverified",
            "clips": [{"start": 0, "end": 3, "role": "application"},
                      {"start": 10, "end": 13, "role": "result"}], **fields}


def setup_library(tmp_path):
    source = asset(tmp_path, "original.mp4", b"original")
    clean = asset(tmp_path, "clean.mp4", b"clean", original_sha256=source["sha256"],
                  original_offset=0)
    lib = library_type()(tmp_path, "local/library.sqlite3")
    lib.register({"assets": [source, clean]})
    return lib, source, clean


def plan_and_context(tmp_path, clean):
    (tmp_path / "out.mp4").write_bytes(b"rendered")
    plan = {"job_id": "job-a", "ordinal": 1, "duration": 2,
            "source": clean["path"], "source_sha256": clean["sha256"],
            "windows": [{"source_start": 1, "source_end": 3,
                         "timeline_start": 0, "timeline_end": 2}], "output": "out.mp4"}
    context = {"product": "blush", "market": "BR", "employee": "employee-a",
               "angle": "edges", "hook": "点太多怎么办？", "chinese_lines": ["看边缘怎么晕开。"]}
    return plan, context


def test_registration_is_hash_deduplicated_and_survives_workspace_move(tmp_path):
    lib, source, clean = setup_library(tmp_path)
    renamed = asset(tmp_path, "renamed.mp4", b"clean", original_sha256=source["sha256"],
                    original_offset=0)
    lib.register({"assets": [renamed]})
    assert len(lib.inventory("blush")) == 2
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.copytree(tmp_path / "local", moved / "local")
    shutil.copy(tmp_path / "original.mp4", moved / "original.mp4")
    shutil.copy(tmp_path / "renamed.mp4", moved / "renamed.mp4")
    reopened = library_type()(moved, "local/library.sqlite3")
    inventory = reopened.inventory("blush")
    assert len(inventory) == 2
    assert all(item["available"] for item in inventory)
    assert next(item for item in inventory if item["sha256"] == clean["sha256"])["path"] == "renamed.mp4"


def test_usage_of_clean_clip_marks_same_original_interval_but_leaves_other_clip(tmp_path):
    lib, source, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    at = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert lib.record(plan, context, at=at)["new_record"] is True
    reopened = library_type()(tmp_path, "local/library.sqlite3")
    findings = reopened.inspect(plan, context, at=at)
    assert findings["recent_intervals"][0]["original_start"] == 1
    assert findings["exact_script_matches"]
    candidates = reopened.candidates("blush", at=at)
    assert {item["role"] for item in candidates} == {"result"}
    assert len(reopened.candidates("blush", at=datetime(2026, 10, 18, tzinfo=timezone.utc))) == 4


def test_repaired_excerpt_maps_to_original_coordinates(tmp_path):
    lib, source, clean = setup_library(tmp_path)
    excerpt = asset(tmp_path, "excerpt.mp4", b"excerpt", duration=3,
                    original_sha256=source["sha256"], original_offset=10,
                    clips=[{"start": 0, "end": 3, "role": "result"}])
    lib.register({"assets": [excerpt]})
    plan, context = plan_and_context(tmp_path, excerpt)
    lib.record(plan, context)
    original_plan = {**plan, "source": source["path"], "source_sha256": source["sha256"],
                     "windows": [{"source_start": 11, "source_end": 13,
                                  "timeline_start": 0, "timeline_end": 2}]}
    findings = lib.inspect(original_plan, context)
    assert findings["recent_intervals"][0]["original_start"] == 11
    assert not lib.inspect({**original_plan, "windows": [{"source_start": 0, "source_end": 2,
        "timeline_start": 0, "timeline_end": 2}]}, context)["recent_intervals"]


def test_retry_does_not_duplicate_usage_or_erase_human_review(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    first = lib.record(plan, context)
    lib.review(first["id"], "positive_feedback", {"user_reply": "剪的不错，还可以"})
    assert lib.record(plan, context)["new_record"] is False
    history = lib.history("blush")
    assert len(history) == 1
    assert history[0]["review"]["status"] == "positive_feedback"
    assert history[0]["market"] == "BR"
    assert history[0]["employee"] == "employee-a"


def test_tampered_registration_is_atomic_and_changed_sources_are_unavailable(tmp_path):
    lib = library_type()(tmp_path, "local/library.sqlite3")
    first = asset(tmp_path, "a.mp4", b"a")
    second = asset(tmp_path, "b.mp4", b"b", sha256="wrong")
    with pytest.raises(ValueError, match="hash"):
        lib.register({"assets": [first, second]})
    assert lib.inventory("blush") == []
    lib.register({"assets": [first]})
    (tmp_path / "a.mp4").write_bytes(b"changed")
    assert not lib.inventory("blush")[0]["available"]
    assert lib.candidates("blush") == []


def test_missing_output_or_invalid_shots_cannot_be_recorded(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    (tmp_path / "out.mp4").unlink()
    with pytest.raises((ValueError, OSError)):
        lib.record(plan, context)
    assert lib.history("blush") == []
    (tmp_path / "out.mp4").write_bytes(b"rendered")
    plan["windows"][0]["source_end"] = 100
    with pytest.raises(ValueError, match="duration|range"):
        lib.record(plan, context)


def test_same_script_is_detected_across_punctuation_and_angle_labels(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    lib.record(plan, context)
    other = {**context, "angle": "new label", "hook": "点 太 多 怎么 办!",
             "chinese_lines": ["看边缘怎么晕开！"]}
    assert lib.inspect(plan, other)["exact_script_matches"]


def test_metadata_cannot_silently_change_lineage_or_product(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    with pytest.raises(ValueError, match="metadata|lineage|range"):
        lib.register({"assets": [{**clean, "original_offset": 4}]})
    with pytest.raises(ValueError, match="product|metadata"):
        lib.register({"assets": [{**clean, "product": "foundation"}]})


def test_other_local_computer_has_independent_history(tmp_path):
    lib, source, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    lib.record(plan, context)
    other = tmp_path / "employee-b"
    other.mkdir()
    for spec in (source, clean):
        shutil.copy(tmp_path / spec["path"], other / spec["path"])
    separate = library_type()(other, "local/library.sqlite3")
    separate.register({"assets": [source, clean]})
    assert separate.history("blush") == []
    assert len(separate.candidates("blush")) == 4


def test_codex_cli_can_import_and_find_local_candidates(tmp_path, capsys):
    from scripts.produce_video import main
    source = asset(tmp_path, "source.mp4", b"source")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"assets": [source]}))
    common = ["--workspace", str(tmp_path), "--library", "local/library.sqlite3"]
    assert main(["library", "import", str(manifest), *common]) == 0
    capsys.readouterr()
    assert main(["library", "candidates", "--product", "blush", "--role", "result", *common]) == 0
    found = json.loads(capsys.readouterr().out)
    assert len(found) == 1
    assert found[0]["path"] == "source.mp4"
    assert found[0]["start"] == 10


def test_recorded_output_is_reused_and_tampering_requires_new_version(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    assert lib.existing(plan, context) is None
    first = lib.record(plan, context)
    assert lib.existing(plan, context)["id"] == first["id"]
    (tmp_path / "out.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="output|version"):
        lib.existing(plan, context)


def test_newly_inspected_clip_can_be_added_without_changing_lineage(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    new = {**clean, "clips": clean["clips"] + [{"start": 4, "end": 6, "role": "product"}]}
    lib.register({"assets": [new]})
    assert len(lib.candidates("blush", "product")) == 1
    assert len(lib.candidates("blush")) == 5


def test_new_plan_cannot_replace_an_already_registered_output(tmp_path):
    lib, _, clean = setup_library(tmp_path)
    plan, context = plan_and_context(tmp_path, clean)
    lib.record(plan, context)
    changed = {**plan, 'job_id': 'new-job'}
    with pytest.raises(ValueError, match='new numbered version'):
        lib.existing(changed, context)
    assert (tmp_path / 'out.mp4').read_bytes() == b'rendered'
