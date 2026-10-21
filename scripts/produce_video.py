#!/usr/bin/env python3
"""Render one evidence-bound production sample from a JSON plan."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import sqlite3

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _timestamp(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(cues):
    """Format measured (start, end, text) cues as an independent SRT."""
    blocks = []
    previous_end = 0
    for index, (start, end, text) in enumerate(cues, 1):
        validate_plan([(previous_end, end)], [(start, end)])
        if not text.strip():
            raise ValueError("empty subtitle")
        blocks.append(f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}")
        previous_end = end
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def validate_plan(windows, measured_cues):
    """Ensure every measured cue fits its approved timeline window."""
    if len(windows) != len(measured_cues):
        raise ValueError("measured cue count does not match approved window count")
    for (window_start, window_end), (cue_start, cue_end) in zip(windows, measured_cues):
        if (not all(math.isfinite(value) for value in (window_start, window_end, cue_start, cue_end))
                or window_start < 0 or window_end <= window_start
                or cue_start < window_start or cue_end > window_end or cue_end <= cue_start):
            raise ValueError(
                f"audio cue {cue_start:.3f}-{cue_end:.3f} is outside its window "
                f"{window_start:.3f}-{window_end:.3f}"
            )
    return True


def validate_render_input(plan):
    """Validate all bound bytes and timing, including when reusing a saved MP4."""
    windows = plan["voice_windows"]
    cues = plan["audio_cues"]
    validate_plan(windows, [(item["measured_start"], item["measured_end"]) for item in cues])
    total = plan["duration"]
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid duration")
    sources = plan.get("sources")
    if sources is None:
        sources = [{"path": plan["source"], "sha256": plan["source_sha256"]}]
    if not sources:
        raise ValueError("at least one video source is required")
    input_paths = [Path(item["path"]).resolve() for item in sources]
    input_paths.append(Path(plan["voice_audio"]).resolve())
    output = Path(plan["output"]).resolve()
    srt = Path(plan["srt"]).resolve()
    bindings = plan.get("bound_files", [])
    packaging = plan.get("overlays", []) + plan.get("sound_effects", [])
    input_paths += [Path(item["path"]).resolve() for item in bindings]
    input_paths += [Path(item["path"]).resolve() for item in packaging]
    if output in input_paths or srt in input_paths or output == srt:
        raise ValueError("outputs must not replace an input source")
    for filename, expected in [(item["path"], item["sha256"]) for item in sources] + [
                               (plan["voice_audio"], plan["voice_audio_sha256"])] + [
                                   (item["path"], item["sha256"]) for item in bindings + packaging]:
        with Path(filename).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"input hash mismatch: {filename}")
    for item in plan.get("overlays", []):
        start, end = item["start"], item["end"]
        if (not all(math.isfinite(v) for v in (start, end))
                or not 0 <= start < end <= total
                or any(type(item[k]) is not int or item[k] < 0 for k in ("x", "y"))):
            raise ValueError("invalid packaging overlay timing/position")
    for item in plan.get("sound_effects", []):
        start, duration, volume = item["start"], item["duration"], item["volume"]
        if (not all(math.isfinite(v) for v in (start, duration, volume))
                or start < 0 or duration <= 0 or start + duration > total
                or not 0 < volume <= 1):
            raise ValueError("invalid packaging sound timing/volume")
    cursor = 0
    for shot in plan["windows"]:
        source_index = shot.get("source_index", 0)
        if type(source_index) is not int or not 0 <= source_index < len(sources):
            raise ValueError("invalid shot source index")
        values = [shot[key] for key in ("source_start", "source_end", "timeline_start", "timeline_end")]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("invalid shot duration")
        source_length = shot["source_end"] - shot["source_start"]
        if (shot["source_start"] < 0 or source_length <= 0
                or not math.isclose(shot["timeline_start"], cursor, abs_tol=1e-6)
                or not math.isclose(source_length, shot["timeline_end"] - cursor, abs_tol=1e-6)):
            raise ValueError("shot duration or continuous timeline mismatch")
        cursor = shot["timeline_end"]
    if not math.isclose(cursor, total, abs_tol=1e-6):
        raise ValueError("total duration mismatch")
    for cue in cues:
        start, end, delay = cue["source_start"], cue["source_end"], cue["delay_ms"]
        if (not all(math.isfinite(value) for value in (start, end, delay))
                or start < 0 or end <= start or delay < 0 or int(delay) != delay
                or cue["measured_start"] < delay / 1000 - 1e-6
                or cue["measured_end"] > delay / 1000 + end - start + 1e-6
                or delay / 1000 + end - start > total + 1e-6):
            raise ValueError("audio trim would crop measured speech or exceed duration")
    subtitle_cues = plan.get("subtitle_cues", [
        (cue["measured_start"], cue["measured_end"], cue["text"]) for cue in cues])
    subtitle_text = build_srt(subtitle_cues)
    if any(end > total for _, end, _ in subtitle_cues):
        raise ValueError("subtitle outside video duration")
    return sources, subtitle_text


def render_plan(plan):
    sources, subtitle_text = validate_render_input(plan)
    total, cues = plan["duration"], plan["audio_cues"]
    output, srt = Path(plan["output"]).resolve(), Path(plan["srt"]).resolve()
    video_parts = []
    video_labels = []
    dimensions = ""
    if len(sources) > 1 or 'width' in plan or 'height' in plan:
        width, height = plan.get("width", 1080), plan.get("height", 1920)
        if any(type(value) is not int or value <= 0 or value % 2 for value in (width, height)):
            raise ValueError("invalid output dimensions")
        dimensions = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,")
    for index, item in enumerate(plan["windows"]):
        video_parts.append(
            f"[{item.get('source_index', 0)}:v]trim=start={item['source_start']}:end={item['source_end']},"
            f"setpts=PTS-STARTPTS,fps=60,{dimensions}setpts=N/(60*TB),format=yuv420p[v{index}]"
        )
        video_labels.append(f"[v{index}]")
    video_parts.append(
        "".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0,setpts=N/(60*TB),format=yuv420p[v]"
    )
    audio_parts = []
    audio_labels = []
    for index, cue in enumerate(cues):
        label = f"[a{index}]"
        audio_parts.append(
            f"[{len(sources)}:a]atrim=start={cue['source_start']}:end={cue['source_end']},"
            f"asetpts=PTS-STARTPTS,adelay={cue['delay_ms']}:all=1{label}"
        )
        audio_labels.append(label)
    audio_parts.append(
        "".join(audio_labels)
        + f"amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0:normalize=0,"
        f"asetpts=N/SR/TB,apad,atrim=duration={total}[a]"
    )
    overlays, effects = plan.get("overlays", []), plan.get("sound_effects", [])
    extra_inputs = []
    video_output, audio_output = "[v]", "[a]"
    for index, item in enumerate(overlays):
        source_index = len(sources) + 1 + index
        label = f"[packv{index}]"
        video_parts.append(
            f"{video_output}[{source_index}:v]overlay=x={item['x']}:y={item['y']}:"
            f"enable='gte(t,{item['start']})*lt(t,{item['end']})':eof_action=repeat{label}")
        video_output = label
        extra_inputs += ["-i", item["path"]]
    effect_labels = []
    for index, item in enumerate(effects):
        source_index = len(sources) + 1 + len(overlays) + index
        label = f"[sfx{index}]"
        audio_parts.append(
            f"[{source_index}:a]atrim=duration={item['duration']},asetpts=PTS-STARTPTS,"
            f"volume={item['volume']},adelay={round(item['start'] * 1000)}:all=1{label}")
        effect_labels.append(label)
        extra_inputs += ["-i", item["path"]]
    if effect_labels:
        audio_output = "[packa]"
        audio_parts.append("[a]" + "".join(effect_labels)
                           + f"amix=inputs={len(effects)+1}:duration=first:dropout_transition=0:normalize=0,"
                           + f"asetpts=N/SR/TB,atrim=duration={total}{audio_output}")
    filter_graph = ";".join(video_parts + audio_parts)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoder = plan.get("encoder", "libx264")
    if encoder not in ("h264_videotoolbox", "libx264"):
        raise ValueError("unsupported H.264 encoder")
    if os.name == 'nt' and encoder == 'h264_videotoolbox':
        raise ValueError('历史执行计划使用 Mac 编码器；请在新执行版本中显式选择 libx264')
    with tempfile.NamedTemporaryFile(prefix=output.stem + ".", suffix=".partial.mp4", dir=output.parent, delete=False) as handle:
        partial = Path(handle.name)
    video_options = (["-b:v", "8M"] if encoder == "h264_videotoolbox" else
                     ["-preset", "ultrafast", "-crf", "20"])
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-nostdin",
        *[arg for item in sources for arg in ("-i", item["path"])], "-i", plan["voice_audio"],
        *extra_inputs,
        "-filter_complex", filter_graph, "-map", video_output, "-map", audio_output,
        "-c:v", encoder, *video_options, "-profile:v", "high",
        "-level:v", "4.2", "-pix_fmt", "yuv420p", "-r", "60",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
        "-map_metadata", "-1",
        "-y", str(partial),
    ]
    try:
        encoded = subprocess.run(command, capture_output=True)
        if encoded.returncode:
            raise ValueError("encode failed: " + encoded.stderr.decode(errors="replace"))
        decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin",
                                  "-i", str(partial), "-f", "null", "-"], capture_output=True)
        if decoded.returncode:
            raise ValueError("output decode failed: " + decoded.stderr.decode(errors="replace"))
        srt.parent.mkdir(parents=True, exist_ok=True)
        srt.write_text(subtitle_text, encoding="utf-8")
        os.replace(partial, output)
    finally:
        if partial.exists():
            partial.unlink()
    return {"output": str(output), "srt": str(srt)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render")
    render.add_argument("plan", type=Path)
    render.add_argument("--workspace", type=Path, default=Path.cwd())
    render.add_argument("--library", help="本机素材台账 .sqlite3 路径")
    render.add_argument("--context", type=Path, help="商品、员工、市场和准确中文稿")
    from workflow.production.library import add_library_parser, run_library_command, LocalLibrary
    add_library_parser(subparsers)
    args = parser.parse_args(argv)
    try:
        if args.command == "library":
            print(json.dumps(run_library_command(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "render":
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            library = None
            if bool(args.library) != bool(args.context):
                raise ValueError("local library rendering requires both --library and --context")
            if args.library:
                library = LocalLibrary(args.workspace, args.library)
                context = json.loads(library.path(args.context).read_text(encoding="utf-8"))
                reuse = library.inspect(plan, context)
            render_input = json.loads(json.dumps(plan))
            workspace = args.workspace.resolve()
            for key in ("source", "voice_audio", "output", "srt", "voice_approval", "voice_selection",
                        "voice_recording", "word_metadata"):
                if render_input.get(key):
                    render_input[key] = str((workspace / render_input[key]).resolve())
            for item in (render_input.get("sources", []) + render_input.get("bound_files", [])
                         + render_input.get("overlays", []) + render_input.get("sound_effects", [])):
                item["path"] = str((workspace / item["path"]).resolve())
            plan_for_voice = render_input
            if plan.get("voice_approval") and plan.get("voice_selection"):
                from workflow.production.voices import select_voice, validate_recording
                new_recording = plan_for_voice.get("voice_recording")
                voice_options = dict(plan_for_voice.get("voice_options", {}))
                if voice_options.get("batch_state_path"):
                    voice_options["batch_state_path"] = str((workspace / voice_options["batch_state_path"]).resolve())
                selected = select_voice(plan_for_voice["voice_approval"], plan_for_voice["voice_selection"],
                                        plan["job_id"], plan["ordinal"],
                                        localization_sha256=plan["localization_sha256"] if new_recording else None,
                                        **voice_options)
                if new_recording:
                    binding = next((item for item in plan_for_voice.get("bound_files", [])
                                    if Path(item["path"]).resolve() == Path(new_recording).resolve()), None)
                    if not binding or hashlib.sha256(Path(new_recording).read_bytes()).hexdigest() != binding["sha256"]:
                        raise ValueError("recording evidence hash mismatch")
                    validate_recording(selected, json.loads(Path(new_recording).read_text(encoding='utf-8')),
                                       plan["voice_audio_sha256"],
                                       selection_sha256=hashlib.sha256(Path(plan_for_voice["voice_selection"]).read_bytes()).hexdigest(),
                                       word_metadata_sha256=hashlib.sha256(Path(plan_for_voice["word_metadata"]).read_bytes()).hexdigest())
                elif selected["approved_audition_sha256"] != plan["voice_audio_sha256"]:
                    raise ValueError("plan audio does not match the persisted approved voice")
            existing = library.existing(plan, context) if library else None
            if existing:
                _, subtitle_text = validate_render_input(render_input)
                subtitle_path = Path(render_input["srt"])
                if not subtitle_path.exists() or subtitle_path.read_bytes() != subtitle_text.encode("utf-8"):
                    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
                    subtitle_path.write_text(subtitle_text, encoding="utf-8")
                result = {"output": render_input["output"], "srt": render_input["srt"],
                          "usage": existing, "render_reused": True, "reuse_review": reuse}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            result = render_plan(render_input)
            if library:
                result["usage"] = library.record(plan, context)
                result["reuse_review"] = reuse
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
