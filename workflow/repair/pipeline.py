"""Versioned LaMa repair jobs with explicit masks and review-only outputs."""

from __future__ import annotations

from workflow.platforms.locks import acquire
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

from .intervals import region_active_at


PARAM_VERSION = "repair-lama-v1"
MODEL_MD5 = "e3aa4aaa15225a33ec84f9f4bc47e500"
OUTPUT_NAMES = ("repaired.mp4", "mask.mp4", "comparison.mp4")


def digest(path: Path, algorithm="sha256") -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _box(box, dimensions):
    if (not isinstance(box, list) or len(box) != 4 or
            any(type(value) is not int for value in box)):
        raise ValueError("boxes must be [x, y, width, height] integer arrays")
    x, y, width, height = box
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > dimensions[0] or y + height > dimensions[1]:
        raise ValueError(f"box {box} is outside {dimensions}")
    return box


def load_spec(path: Path, dimensions: tuple[int, int], source_duration: float) -> dict:
    """Validate a source-specific mask specification before any inference."""
    path = Path(path).resolve()
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("dimensions") != list(dimensions):
        raise ValueError("mask dimensions do not match source video")
    regions = spec.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("at least one mask region is required")
    names = set()
    for region in regions:
        name = region.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("region names must be unique non-empty strings")
        names.add(name)
        mode = region.get("mode", "calibrated" if "calibration_seconds" in region else "box")
        if mode not in ("calibrated", "box", "keyframes", "mask_image"):
            raise ValueError(f"unsupported mask mode: {mode}")
        region["mode"] = mode
        if mode in ("calibrated", "box"):
            _box(region.get("box"), dimensions)
        if mode == "calibrated":
            seconds = region.get("calibration_seconds")
            if not isinstance(seconds, list) or not seconds or any(
                not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value >= source_duration
                for value in seconds
            ):
                raise ValueError("invalid calibration_seconds")
            width, height = region["box"][2:]
            for logo in region.get("colored_logo_boxes", []):
                _box(logo, (width, height))
        if mode == "keyframes":
            keys = region.get("keyframes")
            if not isinstance(keys, list) or not keys:
                raise ValueError("keyframes must be non-empty")
            previous = -1.0
            for key in keys:
                second = key.get("second")
                if (not isinstance(second, (int, float)) or not math.isfinite(second) or
                        second <= previous or second < 0 or second >= source_duration):
                    raise ValueError("keyframe seconds must increase within source duration")
                _box(key.get("box"), dimensions)
                previous = second
        if mode == "mask_image":
            filename = region.get("file")
            if not isinstance(filename, str) or not filename:
                raise ValueError("mask_image requires file")
            image_path = (path.parent / filename).resolve()
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None or image.shape != (dimensions[1], dimensions[0]) or not image.any():
                raise ValueError("mask image missing, empty, or mismatched dimensions")
            region["file"] = str(image_path)
    for protected in spec.get("protect_regions", []):
        _box(protected, dimensions)
    return spec


def _white_pixels(image):
    image = image.astype(np.int16)
    return (image.min(axis=2) > 185) & (image.max(axis=2) - image.min(axis=2) < 65)


def _read_frame(cap, second, fps):
    cap.set(cv2.CAP_PROP_POS_FRAMES, round(second * fps))
    ok, frame = cap.read()
    if not ok:
        raise ValueError(f"cannot read calibration frame at {second}s")
    return frame


def _prepare_masks(cap, spec, fps):
    prepared = []
    for region in spec["regions"]:
        mode = region["mode"]
        if mode == "calibrated":
            x, y, width, height = region["box"]
            votes = [_white_pixels(_read_frame(cap, second, fps)[y:y+height, x:x+width])
                     for second in region["calibration_seconds"]]
            core = np.mean(votes, axis=0) >= .8
            if np.count_nonzero(core) < 100:
                raise ValueError(f"calibration found too few persistent pixels: {region['name']}")
            patch = cv2.dilate(core.astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
            for lx, ly, lw, lh in region.get("colored_logo_boxes", []):
                patch[ly:ly+lh, lx:lx+lw] = 255
            prepared.append((region, core, patch))
        elif mode == "mask_image":
            image = cv2.imread(region["file"], cv2.IMREAD_GRAYSCALE)
            prepared.append((region, None, (image > 127).astype(np.uint8) * 255))
        else:
            prepared.append((region, None, None))
    return prepared


def _interpolated_box(keys, second, dimensions):
    if second <= keys[0]["second"]:
        return keys[0]["box"]
    if second >= keys[-1]["second"]:
        return keys[-1]["box"]
    for left, right in zip(keys, keys[1:]):
        if left["second"] <= second <= right["second"]:
            fraction = (second - left["second"]) / (right["second"] - left["second"])
            box = [round(a + fraction * (b-a)) for a, b in zip(left["box"], right["box"])]
            return _box(box, dimensions)
    raise AssertionError("keyframe interval missing")


def _frame_mask(frame, second, spec, prepared):
    height, width = frame.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    scores = {}
    calibrated = []
    for region, core, patch in prepared:
        if not region_active_at(region, second):
            continue
        mode = region["mode"]
        if mode == "calibrated":
            x, y, w, h = region["box"]
            score = float(np.mean(_white_pixels(frame[y:y+h, x:x+w])[core]))
            scores[region["name"]] = round(score, 4)
            calibrated.append((score, region, patch))
        elif mode == "mask_image":
            mask = cv2.bitwise_or(mask, patch)
        else:
            box = region["box"] if mode == "box" else _interpolated_box(region["keyframes"], second, (width, height))
            x, y, w, h = box
            mask[y:y+h, x:x+w] = 255
    if calibrated:
        score, region, patch = max(calibrated, key=lambda entry: entry[0])
        if score >= .55:
            x, y, w, h = region["box"]
            mask[y:y+h, x:x+w] = cv2.bitwise_or(mask[y:y+h, x:x+w], patch)
    for x, y, w, h in spec.get("protect_regions", []):
        if np.any(mask[y:y+h, x:x+w]):
            raise ValueError("mask overlaps protected region; adjust calibration before repair")
    return mask, scores


def resolve_device(device: str) -> str:
    if device not in ('cpu', 'auto', 'mps', 'cuda'):
        raise ValueError('unsupported inference device')
    if device == 'cpu':
        return device
    import torch
    if device == 'auto':
        return ('cuda' if torch.cuda.is_available() else
                'mps' if torch.backends.mps.is_available() else 'cpu')
    if ((device == 'cuda' and not torch.cuda.is_available()) or
            (device == 'mps' and not torch.backends.mps.is_available())):
        raise ValueError(f'inference device {device} is unavailable')
    return device


class LamaEngine:
    def __init__(self, model_path: Path, *, device='cpu'):
        self.device = resolve_device(device)
        import torch

        self.torch = torch
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.model = torch.jit.load(str(model_path), map_location="cpu").eval().to(self.device)

    def repair(self, frame, mask):
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return frame.copy()
        height, width = frame.shape[:2]
        crop_width = min(width, max(320, int(xs.max()-xs.min()+65)))
        crop_height = min(height, max(320, int(ys.max()-ys.min()+65)))
        x = max(0, min(int((xs.min()+xs.max()-crop_width)//2), width-crop_width))
        y = max(0, min(int((ys.min()+ys.max()-crop_height)//2), height-crop_height))
        crop = frame[y:y+crop_height, x:x+crop_width]
        patch_mask = mask[y:y+crop_height, x:x+crop_width]
        pad_bottom = (-crop_height) % 8
        pad_right = (-crop_width) % 8
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
        rgb = np.pad(rgb, ((0, pad_bottom), (0, pad_right), (0, 0)), mode="edge")
        padded_mask = np.pad(patch_mask, ((0, pad_bottom), (0, pad_right)))
        image = self.torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).unsqueeze(0).to(self.device)
        tensor_mask = self.torch.from_numpy((padded_mask > 0).astype(np.float32)).unsqueeze(0).unsqueeze(0).to(self.device)
        with self.torch.inference_mode():
            result = self.model(image, tensor_mask)[0].permute(1, 2, 0).cpu().numpy()
        result = cv2.cvtColor(np.clip(result[:crop_height, :crop_width] * 255, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        output = frame.copy()
        target = output[y:y+crop_height, x:x+crop_width]
        target[patch_mask > 0] = result[patch_mask > 0]
        return output


def _write_json(path: Path, value: dict):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _ffmpeg(*args):
    binary = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", *map(str, args)],
                   check=True, timeout=180)


def _encode_with_audio(silent, source, start, duration, output):
    _ffmpeg("-i", silent, "-ss", start, "-t", duration, "-i", source,
            "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264", "-preset", "fast",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", "-y", output)


def _encode_comparison(original, repaired, output):
    _ffmpeg("-i", original, "-i", repaired,
            "-filter_complex", "[0:v]scale=iw/2:ih/2,setsar=1[a];[1:v]scale=iw/2:ih/2,setsar=1[b];[a][b]hstack=inputs=2[v]",
            "-map", "[v]", "-map", "1:a:0?", "-c:v", "libx264", "-preset", "fast",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", "-y", output)


def _verify_video(path, expected_frames):
    _ffmpeg("-xerror", "-i", path, "-f", "null", "-")
    cap = cv2.VideoCapture(str(path))
    try:
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if frames != expected_frames:
            raise RuntimeError(f"{path.name}: {frames} frames, expected {expected_frames}")
        ok, _ = cap.read()
        if not ok:
            raise RuntimeError(f"{path.name}: first frame cannot be decoded")
    finally:
        cap.release()
    return {"path": str(path.resolve()), "sha256": digest(path), "frames": frames, "full_decode": "passed"}


def _produce(source, model, spec, fps, start_frame, count, stage, engine_factory):
    cap = cv2.VideoCapture(str(source))
    writers = {}
    frame_logs = []
    try:
        prepared = _prepare_masks(cap, spec, fps)
        width, height = spec["dimensions"]
        engine = engine_factory(model)
        for name in ("original", "repaired", "mask"):
            writer = cv2.VideoWriter(str(stage / f"{name}-silent.mp4"),
                                     cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError(f"could not open {name} video writer")
            writers[name] = writer
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        for index in range(count):
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"source ended at frame {index} of {count}")
            second = (start_frame + index) / fps
            mask, scores = _frame_mask(frame, second, spec, prepared)
            repaired = engine.repair(frame, mask) if mask.any() else frame.copy()
            if not np.array_equal(repaired[mask == 0], frame[mask == 0]):
                raise RuntimeError("LaMa changed pixels outside explicit mask")
            overlay = frame.copy()
            overlay[mask > 0] = (0, 0, 255)
            writers["original"].write(frame)
            writers["repaired"].write(repaired)
            writers["mask"].write(overlay)
            frame_logs.append({"frame": index, "source_seconds": round(second, 4),
                               "masked_pixels": int(np.count_nonzero(mask)), "scores": scores})
            if index in {0, count // 2, count - 1}:
                cv2.imwrite(str(stage / f"mask-{index:04d}.jpg"), overlay)
                cv2.imwrite(str(stage / f"repaired-{index:04d}.jpg"), repaired)
    finally:
        cap.release()
        for writer in writers.values():
            writer.release()
    if not any(entry["masked_pixels"] for entry in frame_logs):
        raise RuntimeError("no masked pixels in selected time range")
    _encode_with_audio(stage / "repaired-silent.mp4", source, start_frame/fps,
                       count/fps, stage / "repaired.mp4")
    _ffmpeg("-i", stage / "mask-silent.mp4", "-c:v", "libx264", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", stage / "mask.mp4")
    _encode_comparison(stage / "original-silent.mp4", stage / "repaired.mp4", stage / "comparison.mp4")
    for name in OUTPUT_NAMES:
        _verify_video(stage / name, count)
    return frame_logs


def _fingerprint(source_hash, model_hash, spec_hash, start_frame, count, device='cpu'):
    data = {"version": PARAM_VERSION, "source_sha256": source_hash, "model_md5": model_hash,
            "spec_sha256": spec_hash, "start_frame": start_frame, "frames": count,
            "codec": "h264-crf18-aac128k"}
    if device != 'cpu':
        data.update(version='repair-lama-v2-device', inference_device=device)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:20], data


def _spec_digest(spec_path, spec):
    hasher = hashlib.sha256(spec_path.read_bytes())
    for region in spec["regions"]:
        if region["mode"] == "mask_image":
            hasher.update(digest(Path(region["file"])).encode())
    return hasher.hexdigest()


def run_repair(source: Path, spec_path: Path, model: Path, output_root: Path,
               start: float, duration: float, *, engine_factory=LamaEngine,
               expected_model_md5=MODEL_MD5, device='auto') -> dict:
    device = resolve_device(device)
    if engine_factory is LamaEngine:
        engine_factory = lambda path: LamaEngine(path, device=device)
    source, spec_path, model, output_root = map(lambda p: Path(p).resolve(),
                                                (source, spec_path, model, output_root))
    if not source.is_file() or not spec_path.is_file() or not model.is_file():
        raise ValueError("source, mask specification, and model must exist")
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, duration)):
        raise ValueError("start and duration must be finite numbers")
    cap = cv2.VideoCapture(str(source))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or fps <= 0 or width <= 0 or height <= 0 or total_frames <= 0:
            raise ValueError("source video cannot be decoded")
    finally:
        cap.release()
    start_frame, count = round(start * fps), round(duration * fps)
    if start < 0 or duration <= 0 or count <= 0 or start_frame + count > total_frames:
        raise ValueError("time range is outside the source video")
    spec = load_spec(spec_path, (width, height), total_frames/fps)
    model_hash = digest(model, "md5")
    if model_hash.lower() != expected_model_md5.lower():
        raise ValueError(f"model MD5 mismatch: {model_hash}")
    source_hash = digest(source)
    spec_hash = _spec_digest(spec_path, spec)
    task_id, parameters = _fingerprint(source_hash, model_hash, spec_hash, start_frame, count, device)
    output_root.mkdir(parents=True, exist_ok=True)
    task_dir = output_root / task_id
    task_dir.mkdir(exist_ok=True)
    with (task_dir / ".lock").open("a+b") as lock:
        try:
            acquire(lock,blocking=False)
        except BlockingIOError as error:
            raise RuntimeError(f"repair task {task_id} is already running") from error
        record_path = task_dir / "task.json"
        prior = json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else None
        if prior and prior["status"] == "NEEDS_VISUAL_REVIEW" and all(
            Path(prior["outputs"][name]["path"]).is_file() and
            digest(Path(prior["outputs"][name]["path"])) == prior["outputs"][name]["sha256"]
            for name in OUTPUT_NAMES
        ):
            return {**prior, "reused": True}
        for orphan in task_dir.glob(".attempt-*"):
            if orphan.is_dir():
                shutil.rmtree(orphan)
        attempt = prior["attempt"] + 1 if prior else 1
        record = {"task_id": task_id, "status": "RUNNING", "attempt": attempt,
                  "created_at": prior["created_at"] if prior else _now(), "updated_at": _now(),
                  "source": str(source), "source_sha256": source_hash, "model": str(model),
                  "model_md5": model_hash, "mask_spec": str(spec_path), "parameters": parameters,
                  "start_seconds": start_frame/fps, "duration_seconds": count/fps,
                  "dimensions": [width, height], "fps": fps, "frames": count,
                  "engine": "local_big_lama_per_frame", "detector": "explicit_or_sample_calibrated_only",
                  "inference_device": device,
                  "clean_source_used": False, "visual_review": "pending", "reused": False,
                  "previous_status": prior["status"] if prior else None,
                  "history": prior.get("history", []) if prior else []}
        if prior:
            record["history"].append({"attempt": prior["attempt"], "status": prior["status"],
                                      "updated_at": prior["updated_at"], "error": prior.get("error")})
        _write_json(record_path, record)
        stage = task_dir / f".attempt-{attempt}-{uuid.uuid4().hex[:8]}"
        stage.mkdir()
        started = time.monotonic()
        try:
            logs = _produce(source, model, spec, fps, start_frame, count, stage, engine_factory)
            if digest(source) != source_hash:
                raise RuntimeError("source changed during repair")
            outputs = {}
            for name in OUTPUT_NAMES:
                target = task_dir / name
                os.replace(stage / name, target)
                outputs[name] = _verify_video(target, count)
            for preview in stage.glob("*.jpg"):
                os.replace(preview, task_dir / preview.name)
            _write_json(task_dir / "frames.json", {"frames": logs})
            record.update(status="NEEDS_VISUAL_REVIEW", updated_at=_now(),
                          elapsed_seconds=round(time.monotonic()-started, 2), outputs=outputs,
                          frame_log=str((task_dir / "frames.json").resolve()),
                          outside_mask_pixels_preserved_before_encoding=True)
            _write_json(record_path, record)
            return record
        except (Exception, KeyboardInterrupt) as error:
            record.update(status="INTERRUPTED" if isinstance(error, KeyboardInterrupt) else "FAILED",
                          updated_at=_now(), elapsed_seconds=round(time.monotonic()-started, 2),
                          error={"type": type(error).__name__, "message": str(error),
                                 "traceback": traceback.format_exc(limit=8)})
            _write_json(record_path, record)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def task_status(output_root: Path, task_id: str | None = None) -> list[dict]:
    root = Path(output_root).resolve()
    paths = [root / task_id / "task.json"] if task_id else sorted(root.glob("*/task.json"))
    results = []
    for path in paths:
        if not path.is_file():
            continue
        record = json.loads(path.read_text(encoding='utf-8'))
        if record["status"] == "RUNNING":
            with (path.parent / ".lock").open("a+b") as lock:
                try:
                    acquire(lock,blocking=False)
                except BlockingIOError:
                    pass
                else:
                    record = json.loads(path.read_text(encoding='utf-8'))
                    if record["status"] == "RUNNING":
                        record.update(status="INTERRUPTED", updated_at=_now(),
                                      error={"type": "AbandonedRun", "message": "process exited without finishing"})
                        _write_json(path, record)
        results.append(record)
    return results
