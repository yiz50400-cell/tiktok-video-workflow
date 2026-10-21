"""Measure real audio without interpreting metrics as human sound approval."""

from array import array
import math
from pathlib import Path
import re
import subprocess

import imageio_ffmpeg


def measure_audio(path, log_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # Preserve source channels. Equal-power stereo-to-mono downmix may add
    # 3 dB for identical channels and falsely report a clean export as clipped.
    decoded = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-vn",
                              "-ar", "48000", "-progress", "pipe:2", "-f", "f32le", "-"],
                             check=True, capture_output=True)
    pcm = decoded.stdout
    times = re.findall(r"^out_time_us=(\d+)$", decoded.stderr.decode(errors="replace"), re.M)
    if not pcm or not times or int(times[-1]) <= 0:
        raise ValueError("decoded audio or duration missing")
    duration_seconds = int(times[-1]) / 1e6
    samples = array("f")
    samples.frombytes(pcm)
    peak = max(abs(value) for value in samples)
    measured = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path),
                               "-vn", "-af", "ebur128=peak=true:framelog=verbose",
                               "-f", "null", "-"], check=True, capture_output=True)
    log = measured.stderr.decode(errors="replace")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text(log, encoding="utf-8")
    summary = log.rsplit("Summary:", 1)[-1]
    loudness = re.search(r"I:\s+(-?\d+\.\d+) LUFS", summary)
    true_peak = re.search(r"Peak:\s+(-?\d+\.\d+) dBFS", summary)
    return {
        "decoded_duration_seconds": duration_seconds,
        "sample_peak_dbfs": round(20 * math.log10(peak), 3) if peak else None,
        "clipped_pcm_samples": sum(abs(value) >= 1 for value in samples),
        "integrated_lufs": float(loudness.group(1)) if loudness else None,
        "true_peak_dbfs": float(true_peak.group(1)) if true_peak else None,
        "log": str(log_path),
    }
