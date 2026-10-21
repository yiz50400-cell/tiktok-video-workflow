import json
from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest

from workflow.analysis.core import analyze_video, save_observations


def make_video(path: Path):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=12",
                    "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)],
                   capture_output=True, text=True, check=True)


def observation(manifest):
    return {
        "source_sha256": manifest["source"]["sha256"],
        "observer": "Codex visual review",
        "observed_at": "2026-10-09T15:00:00+08:00",
        "segments": [{"kind": "hook", "evidence_status": "observed",
                      "start_seconds": 0, "end_seconds": 1,
                      "description": "Synthetic moving pattern", "frame_ids": [manifest["frames"][0]["id"]]}],
        "known_facts": ["Synthetic test video"], "popularity_hypotheses": [],
        "unknowns": ["No real audience"], "risks": [],
    }


def test_inspect_and_annotate_keep_traceable_source(tmp_path):
    video = tmp_path / "source.mp4"
    make_video(video)
    output = tmp_path / "result"
    manifest = analyze_video(video, output, interval=0.9, max_frames=6)
    assert manifest["source"]["path"] == str(video)
    assert video.is_file()
    assert manifest["media_analysis_status"] == {
        "asr": "not_run", "ocr": "not_run", "semantic": "awaiting_codex_observation"}
    assert "requested FFmpeg seek point" in manifest["method"]["frame_time_basis"]
    assert manifest["shots"][0]["start_seconds"] == 0
    assert manifest["shots"][-1]["end_seconds"] == manifest["metadata"]["duration_seconds"]
    assert all(0 <= shot["start_seconds"] < shot["end_seconds"] <= manifest["metadata"]["duration_seconds"]
               for shot in manifest["shots"])
    assert all(Path(frame["path"]).is_file() and frame["sha256"] for frame in manifest["frames"])
    cells = [cell for sheet in manifest["contact_sheets"] for cell in sheet["cells"]]
    assert {cell["frame_id"] for cell in cells} == {frame["id"] for frame in manifest["frames"]}
    assert all(Path(sheet["path"]).is_file() for sheet in manifest["contact_sheets"])
    supplied = tmp_path / "supplied.json"
    supplied.write_text(json.dumps(observation(manifest)), encoding="utf-8")
    result = save_observations(output / "manifest.json", supplied)
    assert Path(result["report_path"]).is_file()
    assert json.loads(Path(result["analysis_path"]).read_text())["status"] == "codex_observed_needs_review"
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "ASR 未执行；OCR 未执行" in report
    assert "Synthetic moving pattern" in report


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(source_sha256="wrong"),
    lambda data: data["segments"][0].update(end_seconds=999),
    lambda data: data["segments"][0].update(frame_ids=["f9999"]),
    lambda data: data["segments"][0].update(start_seconds=1.1, end_seconds=1.9),
    lambda data: data["segments"][0].update(frame_ids=[]),
    lambda data: data["segments"][0].update(evidence_status="unknown"),
])
def test_reject_untraceable_or_invalid_observation(tmp_path, mutation):
    video = tmp_path / "source.mp4"
    make_video(video)
    output = tmp_path / "result"
    manifest = analyze_video(video, output, interval=1, max_frames=4)
    data = observation(manifest)
    mutation(data)
    supplied = tmp_path / "supplied.json"
    supplied.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        save_observations(output / "manifest.json", supplied)
    assert not (output / "report.md").exists()


def test_existing_analysis_is_not_overwritten(tmp_path):
    video = tmp_path / "source.mp4"
    make_video(video)
    output = tmp_path / "result"
    analyze_video(video, output, interval=1, max_frames=4)
    with pytest.raises(ValueError, match="already exists"):
        analyze_video(video, output)


def test_changed_source_cannot_be_annotated(tmp_path):
    video = tmp_path / "source.mp4"
    make_video(video)
    output = tmp_path / "result"
    manifest = analyze_video(video, output, interval=1, max_frames=4)
    supplied = tmp_path / "supplied.json"
    supplied.write_text(json.dumps(observation(manifest)), encoding="utf-8")
    with video.open("ab") as changed:
        changed.write(b"changed")
    with pytest.raises(ValueError, match="missing or changed"):
        save_observations(output / "manifest.json", supplied)
