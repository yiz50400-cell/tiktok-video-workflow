"""Unattended TikTok footage collection (stage A/B of ``once --produce``).

A. discovery: prefer local Evil0ctal (Docker guest+browser) keyword search for
   TikTok page URLs when the API is healthy; otherwise Codex (gpt-5.6-sol, read-only
   sandbox, live web search, ChatCut MCP disabled). Round 1 prefers the task market;
   later Codex rounds expand across global SUPPORTED_COLLECTION_MARKETS. Delivery
   VO/captions stay on task market. Evil only returns URLs — download stays yt-dlp.
   Speed path: form-factor hints, seed-cache, reconnect abort, parallel downloads
   (see discover_speed.py / evil_discovery.py).
B. retrieval + validation (deterministic Python): yt-dlp download, ffmpeg probe,
   vertical / duration / source checks, normalised H.264 copy and contact sheets.

Fails fast on true TikTok-unreachable (never stock/sample fallback).
When the unique pool is short of ``min_clips`` but some clips exist, returns
ok with a partial-pool reason so produce can deliver k/N instead of 0.
429 / rate-limit backs off and retries; it must not end discovery early.
See docs/采集规则-TikTok商品广告.md.
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
    BEAUTY_CROSS_MARKET_PROFILES,
    FormHints,
    SUPPORTED_COLLECTION_MARKETS,
    count_reconnects,
    discover_min_urls,
    infer_form_hints,
    load_seed_candidates,
    save_seed_candidates,
    should_abort_reconnects,
    text_suggests_wrong_form,
    UsedFootageLedger,
)

from .batch_footage import MIN_CLIPS_PER_VIDEO, collect_targets, video_count

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
    "US": ("en-US", "English (US)"),
    "GB": ("en-GB", "English (UK)"),
    "DE": ("de-DE", "Deutsch"),
    "FR": ("fr-FR", "français"),
    "ES": ("es-ES", "español (España)"),
    "IT": ("it-IT", "italiano"),
    "TR": ("tr-TR", "Türkçe"),
    "TH": ("th-TH", "ไทย"),
    "VN": ("vi-VN", "Tiếng Việt"),
    "MY": ("ms-MY", "Bahasa Melayu"),
    "JP": ("ja-JP", "日本語"),
    "KR": ("ko-KR", "한국어"),
    "CO": ("es-CO", "español (Colombia)"),
    "AR": ("es-AR", "español (Argentina)"),
    "CL": ("es-CL", "español (Chile)"),
    "AU": ("en-AU", "English (Australia)"),
    "SA": ("ar-SA", "العربية"),
    "SG": ("en-SG", "English (Singapore)"),
}


def search_markets_for_round(task_market: str, *, round_num: int, expand_all: bool = False) -> list[str]:
    """Approach B: task market first; expand to global supported markets when later rounds or forced.

    Delivery voiceover/captions stay on ``task_market``; this only widens footage discovery.
    """
    primary = str(task_market or "MX").upper()
    if primary not in SUPPORTED_COLLECTION_MARKETS:
        primary = "MX"
    if expand_all or int(round_num) >= 2:
        rest = [m for m in SUPPORTED_COLLECTION_MARKETS if m != primary]
        return [primary, *rest]
    return [primary]


def render_search_scope_block(task_market: str, search_markets: list[str]) -> tuple[str, str, str]:
    """Return (scope_block, priority_line, language_hint) for the discover prompt."""
    task_market = str(task_market or "MX").upper()
    markets = [m for m in search_markets if m in SUPPORTED_COLLECTION_MARKETS] or [task_market]
    expanded = len(markets) > 1
    market_list = " / ".join(markets)
    nl = chr(10)
    if expanded:
        if len(markets) > 6:
            # Keep full market codes once; summarize language families so Codex prompt stays compact.
            lang_line = (
                "- 语种覆盖（摘要）：ES/PT/EN/ID/DE/FR/IT/TR/TH/VI/MS/JA/KO/AR + English；"
                "完整市场码见上。另可辅以英语通吃查询。"
            )
        else:
            lang_bits = []
            for code in markets:
                locale, language = MARKET_LANGUAGE.get(code, ("en", "English"))
                lang_bits.append(f"{code}={language}（{locale}）")
            lang_line = f"- 各市场语言：{'；'.join(lang_bits)}；另可辅以英语通吃查询。"
        block = (
            "## 素材检索范围（全区扩采）" + nl
            + f"- 本轮在全球主流 TikTok 市场 **{market_list}** 检索同款商品竖屏种草/演示视频。" + nl
            + lang_line + nl
            + f"- 交付市场仍是 **{task_market}**：口播/字幕/CTA 语言后续制作按交付市场走；"
            + "本步只最大化独特可用的同款画面素材。" + nl
            + "- 画面语言、包装文案与交付市场不一致也可以入库（客户已确认可接受）。" + nl
        )
        priority = (
            f"在 {market_list} **同等优先**找高互动同款演示；不要只锁在交付市场 {task_market}。"
        )
        hint = "各检索市场语言 + 英语 + hashtag 各试；全球主流市场语种轮转，避免只搜交付市场。"
    else:
        lang_name = MARKET_LANGUAGE.get(task_market, ("en", "English"))[1]
        block = (
            "## 素材检索范围（优先交付市场）" + nl
            + f"- 本轮优先在交付市场 **{task_market}**（{lang_name}）检索。" + nl
            + "- 同品类其他全球支持市场的高互动同款镜头也可收入，用于补足干净商品操作画面。" + nl
            + f"- 若本轮合格素材不够，下一轮会自动扩到全球支持市场；口播/字幕仍按交付市场 {task_market}。" + nl
        )
        priority = (
            f"优先目标市场语言与市场（{task_market}）的高互动视频，其次同品类其他支持市场的高互动视频，"
            "用于补足干净的商品操作镜头。"
        )
        hint = "目标语言 + 英语 + hashtag 各试一轮即可。"
    return block, priority, hint


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
        partial = bool((self.timings or {}).get("partial_pool"))
        return {
            "ok": self.ok,
            "partial": partial,
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
    search_markets: list[str] | None = None,
) -> str:
    market = str(request.get("market") or "MX").upper()
    locale, language = MARKET_LANGUAGE.get(market, ("en", "English"))
    markets = search_markets or search_markets_for_round(market, round_num=1)
    scope_block, priority_line, language_hint = render_search_scope_block(market, markets)
    template = (template_path or DISCOVER_TEMPLATE).read_text(encoding="utf-8")
    hints = form_hints or infer_form_hints(
        str(request.get("product") or ""),
        str(request.get("notes") or ""),
        str(request.get("style") or ""),
    )
    # Expanded rounds get a slightly higher search budget across languages.
    searches = max_searches + (2 if len(markets) > 1 else 0)
    return (
        template.replace("{{product}}", str(request.get("product") or ""))
        .replace("{{market}}", market)
        .replace("{{locale}}", locale)
        .replace("{{language}}", language)
        .replace("{{style}}", str(request.get("style") or ""))
        .replace("{{notes}}", str(request.get("notes") or ""))
        .replace("{{max_candidates}}", str(max_candidates))
        .replace("{{search_scope_block}}", scope_block)
        .replace("{{search_priority_line}}", priority_line)
        .replace("{{search_language_hint}}", language_hint)
        .replace("{{form_hints}}", hints.prompt_block(max_searches=searches, min_urls=discover_min_urls(max_candidates)))
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


class TikTokUnreachableError(RuntimeError):
    """Fail-fast: this machine cannot download from TikTok right now."""



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

    # Transient throttling — backoff+retry; do NOT treat as permanent TikTok-unreachable.
    RATE_LIMIT_MARKERS = (
        "429",
        "too many requests",
        "http error 429",
        "rate-limit",
        "rate limit",
        "ratelimited",
        "请求过多",
        "频率限制",
        "限流",
    )

    # yt-dlp / TikTok patterns that mean this machine cannot reach TikTok usefully.
    # Rate limits are intentionally excluded (see RATE_LIMIT_MARKERS / is_rate_limited).
    UNREACHABLE_MARKERS = (
        "unable to extract webpage video data",
        "unable to download webpage",
        "http error 403",
        "403: forbidden",
        "403 forbidden",
        "access denied",
        "blocked",
        "ip address",
        "your ip",
        "captcha",
        "login required",
        "cookies are needed",
        "connection refused",
        "connection reset",
        "timed out",
        "timeout",
        "name or service not known",
        "temporary failure in name resolution",
        "network is unreachable",
        "failed to resolve",
        "ssl: unexpected eof",
        "ssl: certificate",
        "tunnel connection failed",
        "proxy error",
    )

    OPERATOR_TIP = (
        "TikTok 不可达：本机 yt-dlp 访问 TikTok 被拦（常见于 Clash「规则」模式未接管命令行）。"
        "请把 Clash Verge 切「全局」或开 TUN/系统代理，浏览器能打开 tiktok.com 后再重试；"
        "或给采集进程设 HTTPS_PROXY=http://127.0.0.1:7897（端口按 Clash 混合端口改）。"
        "换节点/换会话也可能有效。空转多轮检索或换候选链接修不好网络。"
    )

    @classmethod
    def is_rate_limited(cls, reason: str | None) -> bool:
        blob = (reason or "").casefold()
        if not blob:
            return False
        if "tiktok 限流" in blob or "429" in blob:
            return True
        return any(m in blob for m in cls.RATE_LIMIT_MARKERS)

    @classmethod
    def is_unreachable(cls, reason: str | None) -> bool:
        blob = (reason or "").casefold()
        if not blob:
            return False
        # 429 / rate-limit is transient — never classify as permanent unreachable.
        if cls.is_rate_limited(reason):
            return False
        if "tiktok 不可达" in blob or "访问受限" in blob:
            return True
        return any(m in blob for m in cls.UNREACHABLE_MARKERS)

    @classmethod
    def describe(cls, returncode: int, output: str, raw_dir: Path, video_id: str) -> str:
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
        joined = "\n".join(errors or [l for l in lines if not l.startswith("WARNING")])
        if cls.is_rate_limited(joined) or cls.is_rate_limited(output):
            detail = ("yt-dlp: " + " | ".join(errors[-2:]))[:180] if errors else "HTTP 429"
            return (
                f"TikTok 限流（429）。{detail}。"
                "处理：退避重试，勿当成网络全挂而提前结束采集。"
            )
        if cls.is_unreachable(joined) or cls.is_unreachable(output):
            detail = ("yt-dlp: " + " | ".join(errors[-2:]))[:180] if errors else "网络/代理层失败"
            return f"TikTok 不可达（访问受限）。{detail}。处理：{cls.OPERATOR_TIP}"
        if errors:
            return ("yt-dlp: " + " | ".join(errors[-2:]))[:400]
        others = [l for l in lines if not l.startswith("WARNING")]
        if returncode == 0:
            return "yt-dlp 正常结束但没有生成视频文件" + (f"（{others[-1][:200]}）" if others else "")
        return (f"yt-dlp 退出码 {returncode}" + (": " + " | ".join(others[-2:]) if others else "（只有警告输出，无具体错误）"))[:400]


def ytdlp_proxy_args() -> list[str]:
    """Pass Clash/local proxy to yt-dlp when the process env has one (CLI often bypasses system proxy)."""
    for key in (
        "VW_YTDLP_PROXY",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        value = (os.environ.get(key) or "").strip()
        if value:
            return ["--proxy", value]
    return []


def network_block_reason(candidates: list[Candidate], *, min_failures: int = 5) -> str | None:
    """If a batch of downloads all died on TikTok reachability, return a fail-fast reason."""
    tried = [c for c in candidates if c.status in {"download_failed", "downloaded"}]
    downloaded = [c for c in tried if c.status == "downloaded"]
    failed = [c for c in tried if c.status == "download_failed"]
    if downloaded or len(failed) < min_failures:
        return None
    unreachable = [c for c in failed if DownloadFailure.is_unreachable(c.failure_reason)]
    if len(unreachable) < max(min_failures, int(0.8 * len(failed))):
        return None
    return (
        f"采集失败：连续 {len(unreachable)}/{len(failed)} 条候选因 TikTok 不可达下载失败，已停止空转重试。"
        f"{DownloadFailure.OPERATOR_TIP}"
    )


DISCOVERY_NO_SEARCH_MARKERS = (
    "无法检索并核实",
    "无法检索",
    "没有可用的网页搜索",
    "无可用的网页搜索",
    "没有网页搜索工具",
    "网页搜索工具不可用",
    "no usable web search",
    "web search unavailable",
    "web_search unavailable",
    "cannot search and verify",
)

DISCOVERY_RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "ratelimited",
    "请求过多",
    "频率限制",
    "限流",
    "quota exceeded",
)


def discovery_is_rate_limited(text: str | None) -> bool:
    """True when discovery output / events show web_search or API throttling."""
    blob = text or ""
    low = blob.casefold()
    if any(m in low for m in DISCOVERY_RATE_LIMIT_MARKERS):
        return True
    return bool(re.search(r"\b429\b", blob))


def discovery_admits_no_search(text: str | None) -> bool:
    # Rate-limit looks like "cannot search" sometimes — never treat 429 as missing tool.
    if discovery_is_rate_limited(text):
        return False
    blob = text or ""
    low = blob.casefold()
    if any(marker.casefold() in low for marker in DISCOVERY_NO_SEARCH_MARKERS):
        return True
    return bool(re.search(r"没有可用.{0,12}搜索工具", blob))


def download_candidate(
    cand: Candidate, raw_dir: Path, *, ffmpeg: str, timeout: float = 180,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    rate_limit_retries: int = 3,
) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(raw_dir / f"{cand.video_id}.%(ext)s")
    cmd = resolve_ytdlp() + [
        "--no-playlist", "--no-progress", "--write-info-json",
        "-f", "b[vcodec^=h264]/b[vcodec^=avc]/bv*[vcodec^=avc]+ba/b",
        "--merge-output-format", "mp4", "--ffmpeg-location", ffmpeg,
        "--socket-timeout", "30", "--retries", "2",
        *ytdlp_proxy_args(),
        "-o", out_tmpl, cand.url,
    ]
    attempts = max(1, int(rate_limit_retries) + 1)
    completed = None
    for try_i in range(1, attempts + 1):
        try:
            completed = runner(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            cand.status, cand.failure_reason = "download_failed", f"yt-dlp 超时 {timeout}s"
            return
        media = [p for p in raw_dir.glob(f"{cand.video_id}.*") if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}]
        if completed.returncode == 0 and media:
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
            return
        reason = DownloadFailure.describe(
            completed.returncode,
            (completed.stderr or "") + "\n" + (completed.stdout or ""),
            raw_dir,
            cand.video_id,
        )
        cand.status, cand.failure_reason = "download_failed", reason
        if DownloadFailure.is_rate_limited(reason) and try_i < attempts:
            delay = min(60.0, float(2 ** try_i))
            time.sleep(delay)
            continue
        return


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
        blocked = network_block_reason(pending)
        if blocked:
            raise TikTokUnreachableError(blocked)
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
            min_accepted=screen_min_accepted,
            min_usable_s=screen_min_usable_s,
            min_prominent=screen_min_prominent,
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
            if clip.screen_times and not decision.get("screen_times"):
                decision = {**decision, "screen_times": [round(float(t), 2) for t in clip.screen_times]}
            # IZ/Yang 2026-10-27: default = defer full-source LaMa. Burn KEEP enters
            # unrepaired; produce LaMa-only timeline-used 3–8s windows (AC-8 still applies).
            # Opt-in full-clip salvage: VW_COLLECT_FULL_BURN_SALVAGE=1 (lama→boxblur→delogo).
            # Wrong-product never reaches this branch. Boxblur/delogo never the default look.
            if (
                not decision.get("accepted")
                and decision.get("demoted")
                and decision.get("demote_reason") == "burn_cut"
                and clip.path
            ):
                full_salvage = (os.environ.get("VW_COLLECT_FULL_BURN_SALVAGE") or "").strip().lower() in (
                    "1", "true", "yes", "on",
                )
                try:
                    if full_salvage:
                        from .burn_repair import DEFAULT_SALVAGE_ENGINES, salvage_demoted_burn
                        salvage_dir = Path(screen_dir) / "burn_salvage" / str(clip.clip_id)
                        decision = salvage_demoted_burn(
                            decision,
                            Path(clip.path),
                            width=int(clip.width),
                            height=int(clip.height),
                            duration=float(clip.duration),
                            workdir=salvage_dir,
                            ffmpeg=ffmpeg,
                            engines=DEFAULT_SALVAGE_ENGINES,
                            log=log,
                        )
                        if decision.get("salvaged") and decision.get("salvaged_path"):
                            clip.path = str(decision["salvaged_path"])
                            clip.watermark_status = f"burn_salvaged:{decision.get('salvage_engine')}"
                    else:
                        from .burn_repair import defer_demoted_burn
                        decision = defer_demoted_burn(
                            decision,
                            width=int(clip.width),
                            height=int(clip.height),
                            duration=float(clip.duration),
                            log=log,
                        )
                        if decision.get("deferred_burn_salvage"):
                            clip.watermark_status = "burn_deferred_segment_lama"
                except Exception as exc:
                    log(f"  烧录 salvage 异常 {clip.clip_id}：{exc}")
                    decision = {**decision, "salvage_attempted": True, "salvage_errors": [str(exc)]}
            clip.usable_segments = decision.get("usable_segments") or []
            clip.screening = {k: v for k, v in decision.items() if k != "usable_segments"}
            cand = next((c for c in all_candidates if c.video_id == clip.clip_id), None)
            if decision.get("accepted"):
                kept.append(clip)
                tag = ("（burn salvage）" if decision.get("salvaged") else ("（burn deferred→用段 LaMa）" if decision.get("deferred_burn_salvage") else ""))
                log(f"  初筛通过 {clip.clip_id}{tag}：可用片段 {[(s['start_s'], s['end_s'], s['product_prominent']) for s in clip.usable_segments]}")
            else:
                state["screened_out"].append(clip)
                rejections.append(f"{clip.clip_id}（@{cand.account if cand else ''}）：{decision.get('reason')}")
                if cand:
                    cand.status, cand.failure_reason = "screened_out", f"质量初筛淘汰：{decision.get('reason')}"
                log(f"  初筛淘汰 {clip.clip_id}：{decision.get('reason')}")
        return clips, bool(outcome.ok)

    # --- 0. Seed cache: reuse previously accepted URLs before paying for Codex ---
    seed_rows = load_seed_candidates(seed_dir, request, cross_market=True) if seed_dir else []
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
            try:
                seed_clips, seed_screen_ok = ingest_candidates(seed_cands, rnd=0, tag=".seed")
            except TikTokUnreachableError as exc:
                timings["discovery_calls"] = 0
                log(str(exc))
                return finish(False, str(exc), kept, all_candidates, keywords or [product])
        # Only skip Codex discovery when the *batch-scaled* floor is met.
        # Never treat vision's weak default (≥2 clips) / seed_screen_ok as "batch complete"
        # — that caused discovery_calls=0 + premature 采集完成 with ~2 cached links.
        enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
        pool_full = enough if screen else len(kept) >= min_clips
        if seed_cands and skip_discovery_if_seeds and pool_full:
            timings["discover_s"] = timings.get("discover_s", 0.0)
            timings["discovery_calls"] = 0
            return finish(True, f"采集完成（种子缓存，未跑 Codex 检索）：累计{summary}", kept, all_candidates, keywords or [product])
        if seed_cands and not pool_full:
            log(
                f"采集-种子仅 {len(kept)} 条可用（seed_screen_ok={seed_screen_ok}，"
                f"未达批次目标 min_clips≥{min_clips} / {summary}），"
                "继续发现检索并扩区，不因种子命中提前结束"
            )

    # --- 0.5 Evil0ctal default discovery (URL search only; download = yt-dlp) ---
    # Healthy local Docker+Evil → thicken candidate pool. Probe/key miss → Codex.
    enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
    pool_full = enough if screen else len(kept) >= min_clips
    if not pool_full:
        from .evil_discovery import try_evil_discovery

        beauty_expand = form_hints.profile in BEAUTY_CROSS_MARKET_PROFILES
        evil_markets = search_markets_for_round(
            str(request.get("market") or "MX"),
            round_num=2 if beauty_expand else 1,
            expand_all=beauty_expand,
        )
        started = time.time()
        evil_rows, evil_meta = try_evil_discovery(
            request,
            form_hints,
            max_candidates=max_candidates,
            markets=evil_markets,
            seen=seen,
            used_ids=used_ids,
            log=log,
        )
        add_time("evil_discover_s", started)
        timings["evil_probe_ok"] = 1.0 if evil_meta.get("probe_ok") else 0.0
        timings["evil_urls"] = float(evil_meta.get("url_count") or 0)
        timings["evil_queries"] = float(evil_meta.get("query_count") or 0)
        if evil_meta.get("degraded"):
            log(f"采集-Evil 降级 Codex web_search：{evil_meta.get('reason')}")
        elif evil_rows:
            evil_cands: list[Candidate] = []
            for item in evil_rows:
                norm = normalize_tiktok_url(str(item.get("url") or ""))
                if not norm:
                    continue
                canonical, account, video_id = norm
                if video_id in seen:
                    continue
                seen.add(video_id)
                evil_cands.append(Candidate(
                    url=canonical,
                    account=account or item.get("account"),
                    video_id=video_id,
                    why=item.get("why") or "evil search",
                    product_match_evidence=item.get("product_match_evidence"),
                ))
            all_candidates += evil_cands
            log(f"采集-Evil 候选入库准备：{len(evil_cands)} 条（范围 {'/'.join(evil_markets)}）")
            if evil_cands:
                try:
                    ingest_candidates(evil_cands, rnd=0, tag=".evil")
                except TikTokUnreachableError as exc:
                    timings["discovery_calls"] = discovery_calls
                    log(str(exc))
                    return finish(False, str(exc), kept, all_candidates, keywords or [product])
                enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
                if enough if screen else len(kept) >= min_clips:
                    timings["discovery_calls"] = discovery_calls
                    timings["discovery_source"] = 1.0  # 1 = evil-primary
                    return finish(
                        True,
                        f"采集完成（Evil 加厚发现，未跑 Codex 检索）：累计{summary}",
                        kept,
                        all_candidates,
                        keywords or [product],
                    )
                log(
                    f"采集-Evil 后仍不足（{summary}），继续 Codex web_search 补采"
                )

    for rnd in range(1, max(1, discover_rounds) + 1):
        # If seeds already almost enough, still allow one discovery round only when needed.
        enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
        if enough if screen else len(kept) >= min_clips:
            timings["discovery_calls"] = discovery_calls
            return finish(True, f"采集完成（{rnd - 1} 轮检索 + 种子）：累计{summary}", kept, all_candidates, keywords)

        tag = "" if rnd == 1 else f".round{rnd}"
        # --- A. discovery ---------------------------------------------------
        # Approach B: round 1 = task market; later rounds expand to global supported markets.
        # Beauty/skincare (eye_cream/lip_balm): round 1 also starts all-region (VO still task market).
        beauty_expand = form_hints.profile in BEAUTY_CROSS_MARKET_PROFILES
        markets = search_markets_for_round(
            str(request.get("market") or "MX"),
            round_num=rnd,
            expand_all=beauty_expand,
        )
        timings["discovery_scope"] = "all_regions" if len(markets) > 1 else "task_market"
        timings[f"discovery_markets_r{rnd}"] = float(len(markets))
        log(f"采集-检索第 {rnd} 轮范围：{'/'.join(markets)}（交付市场={request.get('market') or 'MX'}，口播语言不变）")
        started = time.time()
        prompt = render_discover_prompt(
            request, max_candidates=max_candidates, form_hints=form_hints,
            max_searches=max_searches, search_markets=markets,
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
        attempt = 0
        while attempt < attempts:
            attempt += 1
            if last_path.exists():
                last_path.replace(logs_dir / f"discover-last-message{tag}.attempt{attempt - 1}.txt")
            events_path = logs_dir / f"discover-events{tag}{'' if attempt == 1 else '.attempt' + str(attempt)}.jsonl"
            try:
                run_kwargs: dict[str, Any] = dict(
                    command=command, cwd=work_dir,
                    events_path=events_path,
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
            events_blob = events_path.read_text(encoding="utf-8") if events_path.is_file() else ""
            rate_blob = f"{text}\n{events_blob}"
            if discovery_is_rate_limited(rate_blob):
                timings["discovery_rate_limited"] = timings.get("discovery_rate_limited", 0.0) + 1.0
                delay = min(60.0, float(2 ** min(attempt, 5)))
                log(
                    f"采集-检索第 {rnd} 轮第 {attempt} 次遇到 429/限流，"
                    f"退避 {delay:.0f}s 后重试（不提前结束检索）"
                )
                time.sleep(delay)
                # Extend budget so throttling does not burn the only attempt.
                if attempt >= attempts and attempts < 6:
                    attempts = min(6, attempts + 2)
                continue
            if discovery_admits_no_search(text) and not parse_discovery_output(text)[0]:
                timings["discovery_calls"] = discovery_calls
                return finish(
                    False,
                    "采集-检索失败：Codex 会话没有可用的 web_search，无法核实 TikTok 链接（不要空转重试）。"
                    "请确认 Codex CLI 已登录、当前模型支持 live web_search，或在桌面版启用网页搜索后重试。",
                    [], all_candidates, keywords,
                )
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
            if discover_rounds > 1:
                # Keep whatever we got; later rounds expand to all supported markets.
                log(
                    f"采集-检索第 1 轮只得到 {len(candidates)} 条有效 URL（需 ≥{min_clips}），"
                    "先入库再扩到全球支持市场补采"
                )
                last_reason = (
                    f"第 1 轮交付市场检索不足（{len(candidates)} < {min_clips}），准备扩到全球支持市场"
                )
            else:
                timings["discovery_calls"] = discovery_calls
                return finish(
                    False,
                    f"采集-检索只得到 {len(candidates)} 条有效 TikTok 视频 URL（需 ≥{min_clips}），codex exit={rc}",
                    [], all_candidates, keywords,
                )
        if not candidates:
            last_reason = f"{last_reason}；第 {rnd} 轮补充检索没有新候选"
            continue

        try:
            clips, screen_ok = ingest_candidates(candidates, rnd=rnd, tag=tag)
        except TikTokUnreachableError as exc:
            timings["discovery_calls"] = discovery_calls
            log(str(exc))
            return finish(False, str(exc), kept, all_candidates, keywords)
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
        # Require batch-scaled enough — screen_ok alone (legacy ≥2 defaults) must not
        # end discovery before later rounds expand to global supported markets.
        if enough:
            timings["discovery_calls"] = discovery_calls
            return finish(True, f"采集完成（{rnd} 轮检索）：累计{summary}", kept, all_candidates, keywords)
        if screen_ok and not enough:
            log(
                f"采集-第 {rnd} 轮初筛对本批候选标记通过，但仍未达批次目标（{summary}），"
                "继续补充检索/扩区"
            )
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
        try:
            ingest_candidates(fallback, rnd=99, tag=".reused")
        except TikTokUnreachableError as exc:
            # If we already have usable clips, still allow partial deliver instead of zero.
            if kept:
                log(str(exc))
                last_reason = f"{last_reason}；旧素材补位遇 TikTok 不可达：{exc}"
            else:
                return finish(False, str(exc), kept, all_candidates, keywords)
        enough, summary = _screen_ok(kept) if screen else (len(kept) >= min_clips, f"{len(kept)} 条")
        if enough:
            return finish(True, f"采集完成（新素材不足，含旧素材补位）：累计{summary}", kept, all_candidates, keywords)
        last_reason = f"{last_reason}；旧素材补位后仍不足：{summary}"

    # Partial deliver: never all-or-nothing fail with 0 when some usable clips exist.
    # Producer will partition into as many disjoint videos as the pool allows.
    if len(kept) >= MIN_CLIPS_PER_VIDEO:
        timings["partial_pool"] = 1.0
        timings["partial_kept"] = float(len(kept))
        return finish(
            True,
            (
                f"缺素材（部分池，非满额成功）：目标≥{min_clips} 条互不重复源，已采到可用 {len(kept)} 条；"
                f"仅够部分交付/reused_clip 补位后的 k/N，制作侧不得按满额 {min_clips} 自信进剪。"
                f"{last_reason}"
            ),
            kept,
            all_candidates,
            keywords,
        )
    if len(kept) >= 1:
        timings["partial_pool"] = 1.0
        timings["partial_kept"] = float(len(kept))
        return finish(
            True,
            (
                f"缺素材：仅 {len(kept)} 条可用源（目标≥{min_clips}）；"
                f"将按每条成片 ≥1 源尝试部分交付。{last_reason}"
            ),
            kept,
            all_candidates,
            keywords,
        )
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
        f"\n\n## 补充检索（第 {rnd} 轮 · 全区扩采）\n"
        "上一轮候选下载后经视觉初筛，大部分被淘汰，原因如下（请避开这些类型）：\n"
        f"{lines}\n\n"
        f"形态标准：{definition or '与请求商品同一类型和形态'}\n"
        "请换更精确的检索词（商品形态 + 充电方式/手持/随行/杯身一体），"
        "并在 **MX / BR / ID / PH** 各市场语言与英语里找**还没用过的**同款竖屏演示；"
        "画面语言可与交付市场不一致。只返回画面里明确是该形态、商品大而清楚、烧录字幕少的视频。\n"
        f"以下视频 ID 已经用过，**不要再返回**：{', '.join(sorted(seen))}\n"
    )
