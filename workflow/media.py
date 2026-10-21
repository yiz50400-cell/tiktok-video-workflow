"""Inspect a local, uploaded video and decode a real preview frame."""
from pathlib import Path
import re
import subprocess

import imageio_ffmpeg

MAX_UPLOAD_BYTES = 250 * 1024 * 1024
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}


def inspect_video(path: Path, thumbnail: Path):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-nostdin',
               '-protocol_whitelist', 'file,pipe', '-threads', '1', '-i', str(path),
               '-map', '0:v:0', '-frames:v', '1', '-an', '-filter_threads', '1',
               '-vf', 'scale=360:-2', '-y', str(thumbnail)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('视频读取超时，请换一段较小的素材重试。') from exc
    output = result.stderr
    video_line = next((line for line in output.splitlines() if 'Video:' in line), '')
    size = re.search(r'\b(\d{2,5})x(\d{2,5})\b', video_line)
    duration = re.search(r'Duration: (\d+):(\d+):([\d.]+)', output)
    codec = re.search(r'Video: ([\w]+)', video_line)
    fps = re.search(r'([\d.]+) fps', video_line)
    rotation = re.search(r'rotation of (-?[\d.]+) degrees', output)
    if result.returncode != 0 or not thumbnail.exists() or not size or not duration:
        raise ValueError('这个文件无法解码为视频，请上传可播放的 MP4、MOV 或 WebM。')
    h, m, s = duration.groups()
    return {'width': int(size[1]), 'height': int(size[2]),
            'duration': int(h) * 3600 + int(m) * 60 + float(s),
            'codec': codec[1] if codec else 'unknown',
            'fps': float(fps[1]) if fps else None,
            'rotation': float(rotation[1]) if rotation else 0,
            'size_bytes': path.stat().st_size,
            'analysis_status': 'METADATA_ONLY'}
