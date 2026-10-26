import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from workflow.repair import load_spec, run_repair, task_status
from workflow.repair.pipeline import _frame_mask, _prepare_masks


def video(path: Path, frames=6):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    assert writer.isOpened()
    for index in range(frames):
        image = np.full((64, 64, 3), (index * 20, 70, 120), np.uint8)
        writer.write(image)
    writer.release()


def fixture(tmp_path, spec=None):
    source = tmp_path / "source.mp4"
    model = tmp_path / "model.pt"
    regions = tmp_path / "regions.json"
    video(source)
    model.write_bytes(b"test-model")
    regions.write_text(json.dumps(spec or {
        "dimensions": [64, 64],
        "regions": [{"name": "mark", "mode": "box", "box": [8, 8, 12, 12]}],
    }))
    return source, model, regions


class FakeEngine:
    calls = 0

    def __init__(self, _model):
        pass

    def repair(self, frame, mask):
        self.__class__.calls += 1
        result = frame.copy()
        result[mask > 0] = 0
        return result


def execute(tmp_path, source, model, regions, **kwargs):
    duration = kwargs.pop("duration", .4)
    return run_repair(source, regions, model, tmp_path / "outputs", 0, duration,
                      engine_factory=FakeEngine,
                      expected_model_md5=hashlib.md5(model.read_bytes()).hexdigest(), **kwargs)


def test_invalid_specs_and_time_range(tmp_path):
    source, model, regions = fixture(tmp_path)
    for spec in (
        {"dimensions": [64, 64], "regions": [{"name": "bad", "mode": "box", "box": [60, 0, 12, 12]}]},
        {"dimensions": [64, 64], "regions": [{"name": "bad", "mode": "keyframes", "keyframes": []}]},
        {"dimensions": [64, 64], "regions": []},
    ):
        regions.write_text(json.dumps(spec))
        with pytest.raises(ValueError):
            load_spec(regions, (64, 64), .6)
    regions.write_text(json.dumps({"dimensions": [64, 64], "regions": [{"name": "ok", "mode": "box", "box": [8, 8, 12, 12]}]}))
    with pytest.raises(ValueError):
        execute(tmp_path, source, model, regions, duration=1.0)
    with pytest.raises(ValueError, match="MD5"):
        run_repair(source, regions, model, tmp_path / "outputs", 0, .4,
                   engine_factory=FakeEngine, expected_model_md5="0" * 32)


def test_protected_region_cannot_overlap_mask(tmp_path):
    source, model, regions = fixture(tmp_path, {
        "dimensions": [64, 64],
        "regions": [{"name": "mark", "mode": "box", "box": [8, 8, 12, 12]}],
        "protect_regions": [[10, 10, 5, 5]],
    })
    with pytest.raises(ValueError, match="protected"):
        execute(tmp_path, source, model, regions)


def test_run_reuse_source_integrity_and_decode(tmp_path):
    source, model, regions = fixture(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    FakeEngine.calls = 0
    first = execute(tmp_path, source, model, regions)
    assert first["status"] == "NEEDS_VISUAL_REVIEW"
    assert first["reused"] is False
    assert FakeEngine.calls == 4
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    for name in ("repaired.mp4", "mask.mp4", "comparison.mp4"):
        path = Path(first["outputs"][name]["path"])
        assert path.is_file() and path.stat().st_size > 0
        cap = cv2.VideoCapture(str(path))
        assert cap.get(cv2.CAP_PROP_FRAME_COUNT) == 4
        cap.release()
    second = execute(tmp_path, source, model, regions)
    assert second["reused"] is True
    assert second["task_id"] == first["task_id"]
    assert FakeEngine.calls == 4


def test_failed_attempt_is_recorded_and_can_retry(tmp_path):
    source, model, regions = fixture(tmp_path)

    class FailingEngine(FakeEngine):
        def repair(self, frame, mask):
            raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError, match="inference failed"):
        run_repair(source, regions, model, tmp_path / "outputs", 0, .4,
                   engine_factory=FailingEngine,
                   expected_model_md5=hashlib.md5(model.read_bytes()).hexdigest())
    records = list((tmp_path / "outputs").glob("*/task.json"))
    assert len(records) == 1
    failed = json.loads(records[0].read_text())
    assert failed["status"] == "FAILED"
    assert failed["error"]["message"] == "inference failed"
    assert not (records[0].parent / "repaired.mp4").exists()
    orphan = records[0].parent / ".attempt-old"
    orphan.mkdir()
    (orphan / "partial.mp4").write_bytes(b"partial")
    recovered = execute(tmp_path, source, model, regions)
    assert recovered["status"] == "NEEDS_VISUAL_REVIEW"
    assert recovered["attempt"] == 2
    assert not orphan.exists()


def test_interruption_does_not_look_complete(tmp_path):
    source, model, regions = fixture(tmp_path)

    class InterruptingEngine(FakeEngine):
        def repair(self, frame, mask):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_repair(source, regions, model, tmp_path / "outputs", 0, .4,
                   engine_factory=InterruptingEngine,
                   expected_model_md5=hashlib.md5(model.read_bytes()).hexdigest())
    record = json.loads(next((tmp_path / "outputs").glob("*/task.json")).read_text())
    assert record["status"] == "INTERRUPTED"


def test_moving_boxes_and_mask_image(tmp_path):
    image = np.zeros((64, 64), np.uint8)
    image[25:28, 25:28] = 255
    cv2.imwrite(str(tmp_path / "image.png"), image)
    source, _, regions = fixture(tmp_path, {
        "dimensions": [64, 64],
        "regions": [
            {"name": "moving", "mode": "keyframes", "keyframes": [
                {"second": 0, "box": [0, 0, 4, 4]},
                {"second": .5, "box": [10, 10, 4, 4]},
            ]},
            {"name": "image", "mode": "mask_image", "file": "image.png"},
        ],
    })
    spec = load_spec(regions, (64, 64), .6)
    cap = cv2.VideoCapture(str(source))
    prepared = _prepare_masks(cap, spec, 10)
    ok, frame = cap.read()
    cap.release()
    assert ok
    mask, _ = _frame_mask(frame, .25, spec, prepared)
    assert mask[5, 5] and mask[26, 26]
    assert not mask[0, 0]


def test_frame_mask_paints_roi_only_inside_its_interval():
    frame = np.zeros((64, 64, 3), np.uint8)
    region = {
        "name": "banner",
        "mode": "box",
        "box": [4, 40, 20, 8],
        "intervals": [[0.0, 1.0]],
    }
    spec = {"dimensions": [64, 64], "regions": [region], "protect_regions": []}
    prepared = [(region, None, None)]
    on, _ = _frame_mask(frame, 0.5, spec, prepared)
    off, _ = _frame_mask(frame, 2.0, spec, prepared)
    assert int(on[44, 10]) == 255
    assert not off.any()


def test_empty_detected_mask_is_failure(tmp_path):
    source, model, regions = fixture(tmp_path)
    regions.write_text(json.dumps({
        "dimensions": [64, 64],
        "regions": [{"name": "blank", "mode": "mask_image", "file": "blank.png"}],
    }))
    cv2.imwrite(str(tmp_path / "blank.png"), np.full((64, 64), 1, np.uint8))
    with pytest.raises(RuntimeError, match="no masked pixels"):
        execute(tmp_path, source, model, regions)


def test_changed_mask_image_invalidates_cache(tmp_path):
    image = np.zeros((64, 64), np.uint8)
    image[8:14, 8:14] = 255
    cv2.imwrite(str(tmp_path / "image.png"), image)
    source, model, regions = fixture(tmp_path, {
        "dimensions": [64, 64],
        "regions": [{"name": "image", "mode": "mask_image", "file": "image.png"}],
    })
    first = execute(tmp_path, source, model, regions)
    image[16:22, 16:22] = 255
    cv2.imwrite(str(tmp_path / "image.png"), image)
    second = execute(tmp_path, source, model, regions)
    assert second["task_id"] != first["task_id"]


def test_corrupt_output_retries_instead_of_reusing(tmp_path):
    source, model, regions = fixture(tmp_path)
    first = execute(tmp_path, source, model, regions)
    Path(first["outputs"]["repaired.mp4"]["path"]).write_bytes(b"broken")
    second = execute(tmp_path, source, model, regions)
    assert second["reused"] is False
    assert second["attempt"] == 2
    assert Path(second["outputs"]["repaired.mp4"]["path"]).stat().st_size > 6


def test_abandoned_running_status_is_marked_interrupted(tmp_path):
    folder = tmp_path / "outputs" / "example"
    folder.mkdir(parents=True)
    record = {"task_id": "example", "status": "RUNNING", "attempt": 1,
              "updated_at": "old"}
    (folder / "task.json").write_text(json.dumps(record))
    result = task_status(tmp_path / "outputs", "example")
    assert result[0]["status"] == "INTERRUPTED"
    assert result[0]["error"]["type"] == "AbandonedRun"
    assert json.loads((folder / "task.json").read_text())["status"] == "INTERRUPTED"
