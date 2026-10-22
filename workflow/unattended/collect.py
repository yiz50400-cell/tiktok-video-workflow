"""Unattended TikTok footage collection (stage A/B of ``once --produce``).

A. discovery: Codex (gpt-5.6-sol, read-only sandbox, live web search, ChatCut MCP
   disabled) proposes real TikTok creator videos for the product/market.
   Speed path: form-factor hints in round-1 queries, search-budget in the prompt,
   seed-cache reuse, reconnect abort, parallel downloads (see discover_speed.py).
B. retrieval + validation (deterministic Python): yt-dlp download, ffmpeg probe,
   vertical / duration / source checks, normalised H.264 copy and contact sheets.

Fails fast (never falls back to stock/sample footage) when fewer than
``min_clips`` valid clips are obtained. See docs/采集规则-TikTok商品广告.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor

from .discover_speed import (
    FormHints,
    count_reconnects,
    infer_form_hints,
    load_seed_candidates,
    save_seed_candidates,
    should_abort_reconnects,
    text_suggests_wrong_form,
    UsedFootageLedger,
)

from .batch_footage import collect_targets, video_count

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DISCOVER_TEMPLATE = PROMPTS_DIR / "tiktok_discover.md"

TIKTOK_VIDEO_URL_RE = re.compile(r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.\-]+)/video/(\d{8,25})")
BANNED_SOURCE_MARKERS = (
    "samplelib", "pexels", "pixabay", "mixkit", "coverr", "videvo",
    "storyblocks", "shutterstock", "istock", "envato", "sample-videos",
)
MARKET_LANGUAGE = {
    "MX": ("es-MX", "español de México"),
    "BR": ("pt-BR", "português do Brasil"),
    "ID": ("id-ID", "Bahasa Indonesia"),
    "PH": ("en-PH", "English (Philippines; light Taglish OK)"),
}


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def resolve_ffmpeg() -> str:
    configured = os.environ.get("VW_FFMPEG")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(f"找不到 ffmpeg（VW_FFMPEG / PATH / imageio_ffmpeg 均不可用）：{exc}")


def resolve_ytdlp() -> list[str]:
    configured = os.environ.get("VW_YTDLP")
    if configured and Path(configured).is_file():
        return [configured]
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.is_file():
        return [str(venv_bin)]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return [sys.executable, "-m", "yt_dlp"]


@dataclass
class Probe:
    duration: float | None
    width: int | None
    height: int | None
    vcodec: str | None
    has_audio: bool
    max_volume_db: float | None = None


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\S+.*?Video:\s*(\w+).*?,\s*(\d{2,5})x(\d{2,5})")
_MAXVOL_RE = re.compile(r"max_volume:\s*(-?[\d.]+|-inf)\s*dB")


def parse_probe(stderr: str) -> Probe:
    duration = None
    match = _DUR_RE.search(stderr)
    if match:
        h, m, s = match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    width = height = None
    vcodec = None
    vmatch = _VIDEO_RE.search(stderr)
    if vmatch:
        vcodec = vmatch.group(1)
        width, height = int(vmatch.group(2)), int(vmatch.group(3))
    has_audio = bool(re.search(r"Stream #\S+.*?Audio:", stderr))
    max_volume = None
    mv = _MAXVOL_RE.search(stderr)
    if mv:
        max_volume = float("-inf") if mv.group(1) == "-inf" else float(mv.group(1))
    return Probe(duration, width, height, vcodec, has_audio, max_volume)


def probe_media(path: Path, *, ffmpeg: str | None = None, measure_volume: bool = False) -> Probe:
    ffmpeg = ffmpeg or resolve_ffmpeg()
    base = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60)
    probe = parse_probe(base.stderr)
    # Rotation metadata: treat displaymatrix rotation of +-90 as swapped dims.
    if re.search(r"rotation of -?90", base.stderr) and probe.width and probe.height:
        probe.width, probe.height = probe.height, probe.width
    if measure_volume and probe.has_audio:
        vol = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        probe.max_volume_db = parse_probe(vol.stderr).max_volume_db
    return probe


@dataclass
class Candidate:
    url: str
    account: str | None = None
    why: str | None = None
    hook: str | None = None
    product_match_evidence: str | None = None
    video_id: str | None = None
    status: str = "pending"
    failure_reason: str | None = None
    raw_path: str | None = None
    clip_path: str | None = None
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class Clip:
    clip_id: str
    path: str
    source_url: str
    account: str | None
    title: str
    duration: float
    width: int
    height: int
    keyword_hit: bool
    visible_metrics: dict[str, Any]
    sheet_path: str
    sheet_times: list[float]
    acquisition_method: str = "yt-dlp(public TikTok page)"
    watermark_status: str = "unverified"
    screen_grid: str | None = None
    screen_times: list[float] = field(default_factory=list)
    usable_segments: list[dict[str, Any]] = field(default_factory=list)
    screening: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectResult:
    ok: bool
    reason: str
    clips: list[Clip]
    candidates: list[Candidate]
    keywords: list[str]
    manifest_path: str | None
    timings: dict[str, float]
    product_definition: str = ""
    screened_out: list[Clip] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "keywords": self.keywords,
            "product_definition": self.product_definition,
            "clips": [asdict(c) for c in self.clips],
            "screened_out": [asdict(c) for c in self.screened_out],
            "candidates": [asdict(c) for c in self.candidates],
            "timings": self.timings,
            "written_at": _utc_now(),
        }


def normalize_tiktok_url(url: str) -> tuple[str, str, str] | None:
    """Return (canonical_url, account, video_id) for a TikTok video page URL."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if any(marker in url.casefold() for marker in BANNED_SOURCE_MARKERS):
        return None
    match = TIKTOK_VIDEO_URL_RE.match(url)
    if not match:
        return None
    account, video_id = match.group(1), match.group(2)
    return f"https://www.tiktok.com/@{account}/video/{video_id}", account, video_id


def parse_discovery_output(text: str) -> tuple[list[Candidate], list[str]]:
    """Parse the discovery agent's final message (JSON, possibly fenced)."""
    payload: Any = None
    if text:
        stripped = text.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.S)
        blob = fenced.group(1) if fenced else stripped[stripped.find("{"): stripped.rfind("}") + 1]
        try:
            payload = json.loads(blob) if blob else None
        except json.JSONDecodeError:
            payload = None
    candidates: list[Candidate] = []
    keywords: list[str] = []
    seen: set[str] = set()
    raw_items: list[Any] = []
    if isinstance(payload, dict):
        raw_items = payload.get("candidates") or []
        keywords = [str(k) for k in payload.get("keywords") or [] if str(k).strip()]
    if not raw_items and text:  # tolerate a bare URL list
        raw_items = [{"url": u} for u in re.findall(r"https?://\S+", text)]
    for item in raw_items:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        norm = normalize_tiktok_url(str(item.get("url") or ""))
        if not norm:
            continue
        canonical, account, video_id = norm
        if video_id in seen:
            continue
        seen.add(video_id)
        candidates.append(
            Candidate(
                url=canonical,
                account=account,
                video_id=video_id,
                why=item.get("why"),
                hook=item.get("hook"),
                product_match_evidence=item.get("product_match_evidence"),
            )
        )
    return candidates, keywords


def parse_product_definition(text: str) -> str:
    from .vision import parse_json_message

    payload = parse_json_message(text or "")
    return str(payload.get("product_definition") or "") if isinstance(payload, dict) else ""


def render_discover_prompt(
    request: dict[str, Any],
    *,
    max_candidates: int,
    template_path: Path | None = None,
    form_hints: FormHints | None = None,
    max_searches: int = 5,
) -> str:
    market = request.get("market") or "MX"
    locale, language = MARKET_LANGUAGE.get(market, ("en", "English"))
    template = (template_path or DISCOVER_TEMPLATE).read_text(encoding="utf-8")
    hints = form_hints or infer_form_hints(
        str(request.get("product") or ""),
        str(request.get("notes") or ""),
        str(request.get("style") or ""),
    )
    return (
        template.replace("{{product}}", str(request.get("product") or ""))
        .replace("{{market}}", market)
        .replace("{{locale}}", locale)
        .replace("{{language}}", language)
        .replace("{{style}}", str(request.get("style") or ""))
        .replace("{{notes}}", str(request.get("notes") or ""))
        .replace("{{max_candidates}}", str(max_candidates))
        .replace("{{form_hints}}", hints.prompt_block(max_searches=max_searches, min_urls=max(8, min(max_candidates, 10))))
    )


def build_discovery_command(*, codex_bin: str, model: str, workdir: Path, last_message_path: Path, use_profile: bool) -> list[str]:
    command = [codex_bin, "exec", "-m", model]
    if use_profile:
        command.extend(["-p", "unattended-chatcut"])
    command.extend([
        "-s", "read-only",
        "-c", 'approval_policy="never"',
        "-c", 'model_reasoning_effort="medium"',
        "-c", 'web_search="live"',
        # discovery must not touch ChatCut (and must not misuse its tools as a search engine)
        "-c", "mcp_servers.chatcut_desktop.enabled=false",
        "--disable", "computer_use",
        "--skip-git-repo-check",
        "-C", str(workdir),
        "-o", str(last_message_path),
        "--json",
        "--color", "never",
        "-",
    ])
    return command


def default_discovery_runner(
    prompt: str,
    *,
    command: list[str],
    cwd: Path,
    events_path: Path,
    timeout: float,
    reconnect_abort: int = 3,
    poll_s: float = 2.0,
) -> int:
    """Run Codex discovery; abort early if the event stream reconnect-loops."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = events_path.parent / "discover-stderr.txt"
    with events_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            text=True,
            cwd=str(cwd),
        )
        assert proc.stdin is not None
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        started = time.time()
        while True:
            rc = proc.poll()
            if rc is not None:
                return int(rc)
            if time.time() - started >= timeout:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                blob = events_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                blob = ""
            if should_abort_reconnects(blob, limit=reconnect_abort):
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                err.write(chr(10) + "[discover] aborted after " + str(reconnect_abort) + " reconnects" + chr(10))
                err.flush()
                return 99
            time.sleep(poll_s)


def _info_text(info: dict[str, Any]) -> str:
    parts = [str(info.get(k) or "") for k in ("title", "description", "fulltitle", "track")]
    parts.extend(str(t) for t in info.get("tags") or [])
    return " ".join(parts)


def keyword_hit(info: dict[str, Any], keywords: list[str]) -> bool:
    haystack = _fold(_info_text(info))
    haystack_compact = re.sub(r"[\s#_\-]", "", haystack)
    for kw in keywords:
        k = _fold(kw).strip("# ")
        if not k:
            continue
        if k in haystack or re.sub(r"[\s#_\-]", "", k) in haystack_compact:
            return True
    return False


AUDIO_SUFFIXES = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav"}


AVOID_LIMIT = 120


class AvoidList:
    @staticmethod
    def build(seen: set[str], recent_used: list[str], limit: int = AVOID_LIMIT) -> list[str]:
        """Do-not-return IDs for the discovery prompt: this run's own candidates plus the most
        recently used ledger IDs (oldest first), capped at ``limit`` keeping the newest."""
        ordered = [v for v in recent_used if v not in seen] + sorted(seen)
        return ordered[-limit:] if limit > 0 else []


class DownloadFailure:
    """Turns yt-dlp output into a human reason (never just a WARNING line)."""

    @staticmethod
    def describe(returncode: int, output: str, raw_dir: Path, video_id: str) -> str:
        """Return the real reason a download produced no video, never just a WARNING line."""
        audio = [p for p in raw_dir.glob(f"{video_id}.*") if p.suffix.lower() in AUDIO_SUFFIXES]
        info: dict[str, Any] = {}
        info_path = raw_dir / f"{video_id}.info.json"
        if info_path.is_file():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                info = {}
        formats = info.get("formats") or []
        has_video = any((f.get("vcodec") or "none") != "none" for f in formats if isinstance(f, dict))
        if audio or (formats and not has_video):
            return "不是视频：只有音频（多半是图文/轮播帖），已跳过"
        lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
        errors = [l for l in lines if l.startswith("ERROR") or "ERROR:" in l]
        if errors:
            return ("yt-dlp: " + " | ".join(errors[-2:]))[:400]
        others = [l for l in lines if not l.startswith("WARNING")]
        if returncode == 0:
            return "yt-dlp 正常结束但没有生成视频文件" + (f"（{others[-1][:200]}）" if others else "")
        return (f"yt-dlp 退出码 {returncode}" + (": " + " | ".join(others[-2:]) if others else "（只有警告输出，无具体错误）"))[:400]


def download_candidate(
    cand: Candidate, raw_dir: Path, *, ffmpeg: str, timeout: float = 180,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(raw_dir / f"{cand.video_id}.%(ext)s")
    cmd = resolve_ytdlp() + [
        "--no-playlist", "--no-progress", "--write-info-json",
        "-f", "b[vcodec^=h264]/b[vcodec^=avc]/bv*[vcodec^=avc]+ba/b",
        "--merge-output-format", "mp4", "--ffmpeg-location", ffmpeg,
        "--socket-timeout", "30", "--retries", "2", "-o", out_tmpl, cand.url,
    ]
    try:
        completed = runner(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        cand.status, cand.failure_reason = "download_failed", f"yt-dlp 超时 {timeout}s"
        return
    media = [p for p in raw_dir.glob(f"{cand.video_id}.*") if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}]
    if completed.returncode != 0 or not media:
        cand.status, cand.failure_reason = "download_failed", DownloadFailure.describe(
            completed.returncode, (completed.stderr or "") + "\n" + (completed.stdout or ""), raw_dir, cand.video_id)
        return
    cand.raw_path = str(media[0])
    info_path = raw_dir / f"{cand.video_id}.info.json"
    if info_path.is_file():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            info = {}
        keep = ("id", "title", "description", "uploader", "uploader_id", "channel", "webpage_url",
                "extractor", "duration", "view_count", "like_count", "comment_count", "repost_count",
                "save_count", "timestamp", "upload_date", "tags", "width", "height", "vcodec")
        cand.info = {k: info.get(k) for k in keep if k in info}
    cand.status = "downloaded"


def normalize_clip(raw: Path, dest: Path, probe: Probe, *, ffmpeg: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (probe.vcodec or "").lower() == "h264":
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(raw), "-c", "copy", "-movflags", "+faststart", str(dest)]
    else:
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(raw), "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(dest)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"ffmpeg 规范化失败：{(done.stderr or '')[-300:]}")


def make_contact_sheet(clip: Path, sheet: Path, duration: float, *, ffmpeg: str, frames: int = 8) -> list[float]:
    times = [round(duration * (i + 0.5) / frames, 2) for i in range(frames)]
    rate = frames / max(duration, 0.1)
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(clip), "-vf",
           f"fps={rate:.5f},scale=270:-2,tile={frames // 2}x2", "-frames:v", "1", str(sheet)]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not sheet.is_file():
        raise RuntimeError("联系表生成失败")
    return times


def validate_and_prepare(cand: Candidate, footage_dir: Path, keywords: list[str], *, ffmpeg: str,
                         min_duration: float, max_duration: float) -> Clip | None:
    raw = Path(cand.raw_path or "")
    info = cand.info or {}
    extractor = str(info.get("extractor") or "").casefold()
    if extractor and "tiktok" not in extractor:
        cand.status, cand.failure_reason = "rejected", f"非 TikTok 提取器：{extractor}"
        return None
    if info.get("id") and str(info.get("id")) != cand.video_id:
        cand.status, cand.failure_reason = "rejected", "下载内容 id 与候选 URL 不一致"
        return None
    probe = probe_media(raw, ffmpeg=ffmpeg)
    if not probe.duration or not probe.width or not probe.height:
        cand.status, cand.failure_reason = "rejected", "ffmpeg 无法解析时长/分辨率"
        return None
    if probe.height < probe.width * 1.5:
        cand.status, cand.failure_reason = "rejected", f"非竖屏 {probe.width}x{probe.height}"
        return None
    if probe.duration < min_duration or probe.duration > max_duration:
        cand.status, cand.failure_reason = "rejected", f"时长 {probe.duration:.1f}s 不在 [{min_duration},{max_duration}]"
        return None
    clip_path = footage_dir / f"{cand.video_id}.mp4"
    normalize_clip(raw, clip_path, probe, ffmpeg=ffmpeg)
    final = probe_media(clip_path, ffmpeg=ffmpeg)
    if not final.duration or (final.vcodec or "").lower() != "h264":
        cand.status, cand.failure_reason = "rejected", "规范化后不是可解码 H.264"
        return None
    sheet = footage_dir / "sheets" / f"{cand.video_id}.jpg"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    times = make_contact_sheet(clip_path, sheet, final.duration, ffmpeg=ffmpeg)
    cand.status, cand.clip_path = "accepted", str(clip_path)
    metrics = {k: info.get(k) for k in ("view_count", "like_count", "comment_count", "repost_count", "save_count")}
    return Clip(
        clip_id=cand.video_id or clip_path.stem,
        path=str(clip_path),
        source_url=cand.url,
        account=info.get("uploader_id") or info.get("uploader") or cand.account,
        title=str(info.get("title") or info.get("description") or "")[:300],
        duration=round(final.duration, 3),
        width=final.width or probe.width,
        height=final.height or probe.height,
        keyword_hit=keyword_hit(info, keywords),
        visible_metrics=metrics,
        sheet_path=str(sheet),
        sheet_times=times,
    )



def filter_wrong_form_candidates(candidates: list[Candidate], hints: FormHints, log: Callable[[str], None]) -> list[Candidate]:
    """Drop discovery rows whose own why/evidence already admits the wrong form-factor."""
    kept: list[Candidate] = []
    for cand in candidates:
        blob = " ".join(str(x or "") for x in (cand.why, cand.product_match_evidence, cand.hook))
        reason = text_suggests_wrong_form(blob, hints)
        if reason:
            cand.status, cand.failure_reason = "skipped_wrong_form", reason
            log(f"  跳过 {cand.url}：{reason}")
            continue
        kept.append(cand)
    return kept


def download_candidates_parallel(
    candidates: list[Candidate],
    raw_dir: Path,
    *,
    ffmpeg: str,
    download: Callable[..., None],
    max_workers: int = 4,
) -> None:
    """yt-dlp downloads are network-bound; run a few in parallel safely."""
    workers = max(1, min(max_workers, len(candidates) or 1))
    if workers == 1:
        for cand in candidates:
            download(cand, raw_dir, ffmpeg=ffmpeg)
        return

    def _one(cand: Candidate) -> None:
        download(cand, raw_dir, ffmpeg=ffmpeg)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, candidates))


def collect_footage(
    request: dict[str, Any],
    work_dir: Path,
    *,
    codex_bin: str = "codex",
    model: str = "gpt-5.6-sol",
    discover_timeout: float = 480,
    discover_attempts: int = 2,
    min_clips: int = 3,
    max_clips: int = 10,
    max_candidates: int = 12,
    min_duration: float = 5.0,
    max_duration: float = 300.0,
    discovery_runner: Callable[..., int] | None = None,
    downloader: Callable[..., None] | None = None,
    log: Callable[[str], None] = print,
    screen: bool = True,
    screener: Callable[..., Any] | None = None,
    discover_rounds: int = 2,
    seed_dir: Path | None = None,
    max_searches: int = 5,
    download_workers: int = 4,
    reconnect_abort: int = 3,
    skip_discovery_if_seeds: bool = True,
    avoid_used: bool = True,
) -> CollectResult:
    work_dir = Path(work_dir)
    footage_dir = work_dir / "footage"
    raw_dir = work_dir / "raw"
    logs_dir = work_dir / "logs"
    for d in (footage_dir, raw_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    manifest_path = footage_dir / "collection.json"

    # Scale discovery/screening floors with request.count so a batch of N videos
    # gets enough unique sources for disjoint assignment (no within-batch reshuffle).
    # count==1 keeps caller max_clips/candidates (tests pass explicit caps); count>1 expands.
    batch_n = video_count(request)
    targets = collect_targets(batch_n)
    min_clips = max(int(min_clips), targets.min_clips)
    if batch_n > 1:
        max_clips = max(int(max_clips), targets.max_clips)
        max_candidates = max(int(max_candidates), targets.max_candidates)
        discover_rounds = max(int(discover_rounds), targets.discover_rounds)
        max_searches = max(int(max_searches), targets.max_searches)
    else:
        max_clips = max(int(max_clips), min_clips)
    screen_min_accepted = targets.screen_min_accepted
    screen_min_usable_s = targets.screen_min_usable_s
    screen_min_prominent = targets.screen_min_prominent
    timings["batch_count"] = float(batch_n)
    timings["target_min_clips"] = float(min_clips)

    state: dict[str, Any] = {"definition": "", "screened_out": []}
    used_ids: set[str] = set()

    def finish(ok: bool, reason: str, clips: list[Clip], cands: list[Candidate], kws: list[str]) -> CollectResult:
        reused_n = sum(1 for c in clips if c.clip_id in used_ids)
        timings["reused_clips"] = float(reused_n)
        if ok:
            reason = f"{reason}（其中旧素材补位 {reused_n} 条，新素材 {len(clips) - reused_n} 条）"
        result = CollectResult(ok, reason, clips, cands, kws, str(manifest_path), timings,
                               product_definition=state["definition"], screened_out=state["screened_out"])
        # Persist accepted URLs so the next run for the same product can skip Codex discovery.
        if ok and clips and seed_dir is not None:
            seed_rows = []
            for clip in clips:
                seed_rows.append({
                    "url": clip.source_url,
                    "account": clip.account,
                    "video_id": clip.clip_id,
                    "why": "seed from prior accepted collection",
                    "product_match_evidence": clip.title,
                })
            saved = save_seed_candidates(
                seed_dir, request, candidates=seed_rows, product_definition=state["definition"],
            )
            if saved:
                timings["seed_saved"] = 1.0
                log(f"采集-种子已更新：{saved}（{len(seed_rows)} 条）")
        manifest_path.write_text(json.dumps(result.manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    product = str(request.get("product") or "")
    form_hints = infer_form_hints(product, str(request.get("notes") or ""), str(request.get("style") or ""))
    state["definition"] = form_hints.definition if form_hints.profile != "generic" else ""
    used_ids = UsedFootageLedger.load(seed_dir, request, form_hints.profile) if avoid_used else set()

    def _screen_ok(kept_clips: list[Clip]) -> tuple[bool, str]:
        return screening_sufficient(
            kept_clips,
            min_accepted=screen_min_accepted,
            min_usable_s=screen_min_usable_s,
            min_prominent=screen_min_prominent,
        )

    fallback: list[Candidate] = []  # already-used footage, only touched when fresh footage is short
    if used_ids:
        log(f"采集-已用素材台账 {len(used_ids)} 条（profile={form_hints.profile}），本次优先找没用过的新素材")
    log(
        f"采集-批次目标 count={int(timings['batch_count'])} → min_clips≥{min_clips}/max_clips={max_clips}/candidates={max_candidates}/rounds={discover_rounds}/searches={max_searches}；初筛≥{screen_min_accepted}条/≥{screen_min_usable_s:.0f}s/突出≥{screen_min_prominent}"
    )
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    runner = discovery_runner or default_discovery_runner
    ffmpeg = resolve_ffmpeg()
    download = downloader or download_candidate
    from .vision import screen_clips

    run_screen = screener or screen_clips
    all_candidates: list[Candidate] = []
    keywords: list[str] = []
    kept: list[Clip] = []
    seen: set[str] = set()
    rejections: list[str] = []
    last_reason = "采集未产生结果"
    discovery_calls = 0

    def add_time(key: str, started_at: float) -> None:
        timings[key] = round(timings.get(key, 0.0) + time.time() - started_at, 1)

    def ingest_candidates(candidates: list[Candidate], *, rnd: int, tag: str) -> tuple[list[Clip], bool]:
        """Download/validate/screen new candidates. Returns (validated_clips, screen_ok_flag)."""
        nonlocal kept
        candidates = filter_wrong_form_candidates(candidates, form_hints, log)
        if not candidates:
            return [], False
        started = time.time()
        pending = [c for c in candidates if c.status == "pending"]
        # Download all pending (discovery already caps count). Rejected horizontals must not
        # starve max_clips — keep validating until we have enough accepted this round.
        if pending:
            download_candidates_parallel(
                pending, raw_dir, ffmpeg=ffmpeg, download=download, max_workers=download_workers,
            )
        clips: list[Clip] = []
        for cand in pending:
            if len(kept) + len(clips) >= max_clips and not screen:
                cand.status = "skipped_enough"
                continue
            if cand.status != "downloaded":
                log(f"  候选 {cand.url} 下载失败：{cand.failure_reason}")
                continue
            try:
                clip = validate_and_prepare(
                    cand, footage_dir, keywords, ffmpeg=ffmpeg,
                    min_duration=min_duration, max_duration=max_duration,
                )
            except Exception as exc:  # keep going with other candidates
                cand.status, cand.failure_reason = "rejected", f"校验异常：{exc}"
                clip = None
            if clip:
                clips.append(clip)
                log(f"  入库 {clip.clip_id} {clip.width}x{clip.height} {clip.duration:.1f}s 关键词命中={clip.keyword_hit}")
            else:
                log(f"  候选 {cand.url} 未入库：{cand.failure_reason}")
        add_time("download_validate_s", started)
        clips.sort(key=lambda c: (not c.keyword_hit))
        if not screen:
            # Cap kept at max_clips for non-screen path (matches previous behaviour).
            room = max(0, max_clips - len(kept))
            kept.extend(clips[:room])
            for extra in clips[room:]:
                cand = next((c for c in all_candidates if c.video_id == extra.clip_id), None)
                if cand:
                    cand.status = "skipped_enough"
            return clips[:room], True
        if not clips:
            return [], False
        started = time.time()
        dicts = [{"clip_id": c.clip_id, "path": c.path, "duration": c.duration} for c in clips]
        screen_dir = work_dir / "screen" if rnd <= 1 and tag != ".seed" else work_dir / "screen" / f"round{rnd}{tag}"
        outcome = run_screen(
            request, dicts, product_definition=state["definition"],
            workdir=screen_dir, ffmpeg=ffmpeg, codex_bin=codex_bin, model=model,
        )
        add_time("screen_s", started)
        state["definition"] = outcome.product_definition or state["definition"]
        by_id = {str(c.clip_id): c for c in clips}
        for d in dicts:
            clip = by_id[str(d["clip_id"])]
            decision = outcome.per_clip.get(str(clip.clip_id)) or {
                "accepted": False, "reason": "无初筛结果", "usable_segments": [],
            }
            clip.screen_grid, clip.screen_times = d.get("screen_grid"), d.get("screen_times") or []
            clip.usable_segments = decision.get("usable_segments") or []
            clip.screening = {k: v for k, v in decision.items() if k != "usable_segments"}
            cand = next((c for c in all_candidates if c.video_id == clip.clip_id), None)
            if decision.get("accepted"):
                kept.append(clip)
                log(f"  初筛通过 {clip.clip_id}：可用片段 {[(s['start_s'], s['end_s'], s['product_prominent']) for s in clip.usable_segments]}")
            else:
                state["screened_out"].append(clip)
                rejections.append(f"{clip.clip_id}（@{cand.account if cand else ''}）：{decision.get('reason')}")
                if cand:
                    cand.status, cand.failure_reason = "screened_out", f"质量初筛淘汰：{decision.get('reason')}"
                log(f"  初筛淘汰 {clip.clip_id}：{decision.get('reason')}")
        return clips, bool(outcome.ok)

    # --- 0. Seed cache: reuse previously accepted URLs before paying for Codex ---
    seed_rows = load_seed_candidates(seed_dir, request) if seed_dir else []
    if seed_rows:
        started = time.time()
        seed_cands: list[Candidate] = []
        for item in seed_rows:
            norm = normalize_tiktok_url(str(item.get("url") or ""))
            if not norm:
                continue
            canonical, account, video_id = norm
            if video_id in seen:
                continue
            seen.add(video_id)
            seed_cands.append(Candidate(
                url=canonical, account=account or item.get("account"), video_id=video_id,
                why=item.get("why") or "seed cache", product_match_evidence=item.get("product_match_evidence"),
            ))
        fallback += [c for c in seed_cands if c.video_id in used_ids]
        seed_cands = [c for c in seed_cands if c.video_id not in used_ids]
        all_candidates += seed_cands
        add_time("seed_load_s", started)
        log(f"采集-种子命中：没用过 {len(seed_cands)} 条，已用过 {len(fallback)} 条排到最后（profile={form_hints.profile}）")
        seed_screen_ok = False
        if seed_cands:
            seed_clips, seed_screen_ok = ingest_candidates(seed_cands, rnd=0, tag=".seed")
        enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
        if seed_cands and skip_discovery_if_seeds and ((seed_screen_ok or enough) if screen else len(kept) >= min_clips):
            timings["discover_s"] = timings.get("discover_s", 0.0)
            timings["discovery_calls"] = 0
            return finish(True, f"采集完成（种子缓存，未跑 Codex 检索）：累计{summary}", kept, all_candidates, keywords or [product])

    for rnd in range(1, max(1, discover_rounds) + 1):
        # If seeds already almost enough, still allow one discovery round only when needed.
        enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
        if enough if screen else len(kept) >= min_clips:
            timings["discovery_calls"] = discovery_calls
            return finish(True, f"采集完成（{rnd - 1} 轮检索 + 种子）：累计{summary}", kept, all_candidates, keywords)

        tag = "" if rnd == 1 else f".round{rnd}"
        # --- A. discovery ---------------------------------------------------
        started = time.time()
        prompt = render_discover_prompt(
            request, max_candidates=max_candidates, form_hints=form_hints, max_searches=max_searches,
        )
        if rnd > 1:
            prompt += render_supplement(seen, rejections, state["definition"] or form_hints.definition, rnd)
        elif state["definition"]:
            prompt += f"\n\n（预填形态，勿改成台式/座式等排除形态）product_definition 请输出：{state['definition']}\n"
        avoid = AvoidList.build(seen, UsedFootageLedger.recent(seed_dir, AVOID_LIMIT) if avoid_used else [])
        if avoid:
            prompt += f"\n以下视频 ID 已经用过（之前的成片、种子或上一轮），**不要再返回**，请找别的视频：{', '.join(avoid)}\n"
        (logs_dir / f"discover-prompt{tag}.md").write_text(prompt, encoding="utf-8")
        last_path = logs_dir / f"discover-last-message{tag}.txt"
        command = build_discovery_command(
            codex_bin=codex_bin, model=model, workdir=work_dir, last_message_path=last_path,
            use_profile=(codex_home / "unattended-chatcut.config.toml").is_file(),
        )
        candidates: list[Candidate] = []
        rc: int | str = "n/a"
        attempts = max(1, discover_attempts if rnd == 1 else 1)
        for attempt in range(1, attempts + 1):
            if last_path.exists():
                last_path.replace(logs_dir / f"discover-last-message{tag}.attempt{attempt - 1}.txt")
            try:
                run_kwargs: dict[str, Any] = dict(
                    command=command, cwd=work_dir,
                    events_path=logs_dir / f"discover-events{tag}{'' if attempt == 1 else '.attempt' + str(attempt)}.jsonl",
                    timeout=discover_timeout,
                )
                # reconnect_abort is only for the real Codex runner; test doubles must not see it
                # (they often forward **kwargs into a strict helper and would raise TypeError).
                if discovery_runner is None:
                    rc = default_discovery_runner(prompt, reconnect_abort=reconnect_abort, **run_kwargs)
                else:
                    rc = runner(prompt, **run_kwargs)
                discovery_calls += 1
            except subprocess.TimeoutExpired:
                rc = f"timeout {discover_timeout}s"
                log(f"采集-检索第 {rnd} 轮第 {attempt} 次 Codex 超时（{discover_timeout}s）")
                continue
            text = last_path.read_text(encoding="utf-8") if last_path.is_file() else ""
            parsed, kws = parse_discovery_output(text)
            candidates = [c for c in parsed if c.video_id not in seen]
            reused = [c for c in candidates if c.video_id in used_ids]
            if reused:
                fallback += [c for c in reused if all(f.video_id != c.video_id for f in fallback)]
                candidates = [c for c in candidates if c.video_id not in used_ids]
                log(f"采集-检索返回 {len(reused)} 条之前用过的视频，已排到最后备用")
            keywords += [k for k in kws if k not in keywords]
            state["definition"] = state["definition"] or parse_product_definition(text) or form_hints.definition
            if rc == 99:
                log(f"采集-检索第 {rnd} 轮第 {attempt} 次因重连循环中止（reconnect_abort={reconnect_abort}）")
            if len(candidates) >= (min_clips if rnd == 1 and not kept else 1):
                break
            log(f"采集-检索第 {rnd} 轮第 {attempt} 次只得到 {len(candidates)} 条新的有效 URL（exit={rc}）")
        add_time("discover_s", started)
        if product and product not in keywords:
            keywords.append(product)
        seen.update(c.video_id for c in candidates if c.video_id)
        all_candidates += candidates
        log(
            f"采集-检索第 {rnd} 轮完成：codex exit={rc}，新的有效 TikTok 候选 {len(candidates)} 条，"
            f"关键词 {keywords[:6]}，profile={form_hints.profile}"
        )
        if rnd == 1 and not kept and len(candidates) < min_clips and not fallback:
            timings["discovery_calls"] = discovery_calls
            return finish(
                False,
                f"采集-检索只得到 {len(candidates)} 条有效 TikTok 视频 URL（需 ≥{min_clips}），codex exit={rc}",
                [], all_candidates, keywords,
            )
        if not candidates:
            last_reason = f"{last_reason}；第 {rnd} 轮补充检索没有新候选"
            continue

        clips, screen_ok = ingest_candidates(candidates, rnd=rnd, tag=tag)
        if rnd == 1 and not screen and len(kept) < min_clips and not seed_rows:
            timings["discovery_calls"] = discovery_calls
            return finish(
                False,
                f"采集入库只有 {len(kept)} 条合格竖屏 TikTok 素材（需 ≥{min_clips}）；不使用库存/样例素材替代",
                kept, all_candidates, keywords,
            )
        if rnd == 1 and screen and not kept and len(clips) < min_clips and not seed_rows:
            # Mirror old fail-fast when discovery produced too few downloadable clips before screening.
            pass
        if not screen:
            timings["discovery_calls"] = discovery_calls
            return finish(True, f"采集完成：{len(kept)} 条 TikTok 竖屏素材入库（未做视觉初筛）", kept, all_candidates, keywords)
        enough, summary = _screen_ok(kept)
        if screen_ok or enough:
            timings["discovery_calls"] = discovery_calls
            return finish(True, f"采集完成（{rnd} 轮检索）：累计{summary}", kept, all_candidates, keywords)
        last_reason = (
            f"质量初筛不足（需 ≥{screen_min_accepted} 条、≥{screen_min_usable_s:.0f}s、"
            f"突出片段 ≥{screen_min_prominent}，已检索 {rnd} 轮）：累计{summary}"
        )
        if rnd < discover_rounds:
            log(f"第 {rnd} 轮初筛后仍不足（{summary}），带着淘汰原因做第 {rnd + 1} 轮补充检索。")
    timings["discovery_calls"] = discovery_calls
    if fallback:
        log(f"采集-新素材不够（{last_reason}），启用 {len(fallback)} 条之前用过的素材补位")
        seen.update(c.video_id for c in fallback)
        all_candidates += fallback
        ingest_candidates(fallback, rnd=99, tag=".reused")
        enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
        if enough:
            return finish(True, f"采集完成（新素材不足，含旧素材补位）：累计{summary}", kept, all_candidates, keywords)
        last_reason = f"{last_reason}；旧素材补位后仍不足：{summary}"
    return finish(False, last_reason, kept, all_candidates, keywords)


SCREEN_MIN_ACCEPTED = 2
SCREEN_MIN_USABLE_S = 12.0
SCREEN_MIN_PROMINENT = 2


def screening_sufficient(
    kept: list[Clip],
    *,
    min_accepted: int | None = None,
    min_usable_s: float | None = None,
    min_prominent: int | None = None,
) -> tuple[bool, str]:
    """Whether kept clips are enough to start produce.

    Thresholds scale with batch ``count`` (see ``collect_targets``) so a 3-video
    enqueue cannot stop after only 2 screened sources and then reshuffle them.
    """
    need_n = SCREEN_MIN_ACCEPTED if min_accepted is None else int(min_accepted)
    need_s = SCREEN_MIN_USABLE_S if min_usable_s is None else float(min_usable_s)
    need_p = SCREEN_MIN_PROMINENT if min_prominent is None else int(min_prominent)
    usable_s = sum(s["end_s"] - s["start_s"] for c in kept for s in c.usable_segments)
    prominent = sum(1 for c in kept for s in c.usable_segments if s.get("product_prominent"))
    summary = (
        f"同款合格 {len(kept)} 条（需≥{need_n}），可用片段 {usable_s:.1f}s（需≥{need_s:.0f}），"
        f"商品突出片段 {prominent} 个（需≥{need_p}）"
    )
    ok = len(kept) >= need_n and usable_s >= need_s and prominent >= need_p
    return ok, summary


def render_supplement(seen: set[str], rejections: list[str], definition: str, rnd: int) -> str:
    lines = "\n".join(f"- {r}" for r in rejections[-20:]) or "- （无）"
    return (
        f"\n\n## 补充检索（第 {rnd} 轮）\n"
        "上一轮候选下载后经视觉初筛，大部分被淘汰，原因如下（请避开这些类型）：\n"
        f"{lines}\n\n"
        f"形态标准：{definition or '与请求商品同一类型和形态'}\n"
        "请换更精确的检索词（例如商品形态 + 充电方式/手持/随行/杯身一体，以及目标语言的同义词和 hashtag），"
        "只返回**画面里明确是该形态**、商品大而清楚、烧录字幕少的视频。\n"
        f"以下视频 ID 已经用过，**不要再返回**：{', '.join(sorted(seen))}\n"
    )
