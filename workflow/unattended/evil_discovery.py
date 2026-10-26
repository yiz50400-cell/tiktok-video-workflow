"""Evil0ctal (Douyin_TikTok_Download_API) URL discovery for unattended collect.

When the local Docker+Evil stack is healthy, default discovery searches TikTok
**page URLs** via guest+browser REST (`/api/v1/tiktok/search/posts`) and feeds
them into the existing yt-dlp download + vision screen path.

Probe fail / missing API key → clean degrade: caller falls back to Codex
web_search → yt-dlp. Evil never downloads media, never needs customer TikTok
cookies, and never requires a residential proxy.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_API_BASE = "http://127.0.0.1:8000"
TIKTOK_VIDEO_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.\-]+)/video/(\d{8,25})"
)
_SITE_PREFIX_RE = re.compile(
    r"(?i)\b(?:site:(?:tikwm|urlebird)\.com(?:/video)?|tiktok)\b"
)
_QUOTE_RE = re.compile(r'[\"“”]')


def evil_discovery_enabled() -> bool:
    """Default on; set VW_EVIL_DISCOVERY=0/false/off to force Codex-only."""
    raw = str(os.environ.get("VW_EVIL_DISCOVERY", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def resolve_api_base() -> str:
    return (os.environ.get("VW_EVIL_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def resolve_api_key(*, repo_root: Path | None = None) -> str | None:
    """API key from env, explicit file, or well-known local secret paths (never committed)."""
    env_key = (os.environ.get("VW_EVIL_API_KEY") or "").strip()
    if env_key:
        return env_key
    candidates: list[Path] = []
    file_env = (os.environ.get("VW_EVIL_API_KEY_FILE") or "").strip()
    if file_env:
        candidates.append(Path(file_env).expanduser())
    home = Path.home()
    candidates.extend(
        [
            home / ".config" / "video-workflow" / "dtk_api_key.txt",
            home / ".config" / "dtk" / "api_key.txt",
        ]
    )
    # Optional local secrets/ next to the checkout (never commit keys).
    roots: list[Path] = []
    if repo_root is not None:
        roots.append(Path(repo_root))
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        candidates.append(Path(root) / "secrets" / "dtk_api_key.txt")
    # Eng pilot sibling only when explicitly allowed (avoids pytest hitting a live stack).
    if str(os.environ.get("VW_EVIL_ALLOW_PILOT_KEY", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }:
        for root in roots:
            candidates.append(Path(root).parent / "dtk-pilot" / "secrets" / "dtk_api_key.txt")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            continue
    return None


def probe_evil(
    base: str | None = None,
    *,
    timeout: float = 2.5,
) -> tuple[bool, str]:
    """Cheap readiness check: /readyz (Postgres+Redis) then /healthz.

    Does not spend an API key. Returns (ok, reason).
    """
    api = (base or resolve_api_base()).rstrip("/")
    for path in ("/readyz", "/healthz"):
        url = api + path
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if int(getattr(resp, "status", 200) or 200) >= 400:
                    continue
                try:
                    payload = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    payload = {}
                status = str(payload.get("status") or "").lower()
                if status in {"ok", "ready", "healthy"} or (isinstance(payload, dict) and payload.get("ok") is True):
                    return True, f"{path} ok"
                if path == "/healthz" and int(getattr(resp, "status", 200) or 200) == 200:
                    return True, "healthz http 200"
        except urllib.error.HTTPError as exc:
            if exc.code == 503 and path == "/readyz":
                return False, "readyz 503 (Postgres/Redis not ready)"
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if path == "/healthz":
                return False, f"Evil API unreachable at {api}: {exc}"
            continue
    return False, f"Evil API probe failed at {api}"


def _clean_keyword(raw: str) -> str:
    text = _SITE_PREFIX_RE.sub(" ", str(raw or ""))
    text = _QUOTE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    # Drop boolean OR noise from web_search-style queries
    text = re.sub(r"\bOR\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def build_evil_queries(
    product: str,
    form_hints: Any,
    *,
    markets: list[str] | None = None,
    max_queries: int = 10,
) -> list[str]:
    """Turn form-hint prefer_queries + product into Evil keyword searches."""
    out: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        cleaned = _clean_keyword(q)
        # Allow short CJK product names (e.g. 眼霜 = 2 chars).
        if len(cleaned) < 2:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(cleaned)

    product = str(product or "").strip()
    if product:
        add(product)
    prefer = list(getattr(form_hints, "prefer_queries", None) or [])
    for q in prefer:
        add(q)
        if len(out) >= max_queries:
            break
    # Light market spice: append product once with a market code tag only if still thin
    for code in markets or []:
        if len(out) >= max_queries:
            break
        if product:
            add(f"{product} {code}")
    return out[:max_queries]


def _http_get_json(
    url: str,
    *,
    api_key: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"X-API-Key": api_key, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _poll_task(
    base: str,
    api_key: str,
    task_id: str,
    *,
    timeout: float = 90.0,
    sleep_s: float = 1.2,
    getter: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    get = getter or _http_get_json
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = get(f"{base}/api/v1/tasks/{urllib.parse.quote(task_id)}", api_key=api_key, timeout=30.0)
        data = last.get("data") if isinstance(last, dict) else None
        state = str((data or {}).get("state") or "").lower()
        if state in {"done", "failed", "error", "cancelled"}:
            return last
        # Some builds return the payload inline without async task
        if isinstance(data, dict) and data.get("items") is not None and not data.get("task_id"):
            return last
        time.sleep(sleep_s)
    raise TimeoutError(f"Evil task {task_id} timed out after {timeout:.0f}s")


def _items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    # Async envelope: data.data.items after poll
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(inner, dict):
        return []
    items = inner.get("items")
    return list(items) if isinstance(items, list) else []


def extract_video_urls(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize Evil search items → {url, account, video_id, why}."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        web = str(item.get("web_url") or "")
        m = TIKTOK_VIDEO_URL_RE.match(web.split("?", 1)[0])
        if not m:
            continue
        account, video_id = m.group(1), m.group(2)
        if video_id in seen:
            continue
        seen.add(video_id)
        title = str(item.get("title") or item.get("description") or "")[:180]
        out.append(
            {
                "url": f"https://www.tiktok.com/@{account}/video/{video_id}",
                "account": account,
                "video_id": video_id,
                "why": "evil search",
                "product_match_evidence": title,
            }
        )
    return out


def search_keyword_urls(
    keyword: str,
    *,
    base: str,
    api_key: str,
    count: int = 20,
    wait: float = 25.0,
    timeout: float = 90.0,
    getter: Callable[..., dict[str, Any]] | None = None,
    poller: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """One Evil keyword search → list of TikTok page URL dicts."""
    get = getter or _http_get_json
    poll = poller or (lambda *a, **k: _poll_task(*a, getter=get, **k))
    params = {
        "keyword": keyword,
        "count": str(int(count)),
        "wait": str(float(wait)),
        "refresh": "true",
    }
    url = f"{base}/api/v1/tiktok/search/posts?{urllib.parse.urlencode(params)}"
    first = get(url, api_key=api_key, timeout=max(timeout, wait + 15.0))
    data = first.get("data") if isinstance(first, dict) else None
    if isinstance(data, dict) and data.get("task_id") and str(data.get("state") or "").lower() not in {
        "done",
        "failed",
        "error",
        "cancelled",
    }:
        first = poll(base, api_key, str(data["task_id"]), timeout=timeout)
    # Failure envelope
    if isinstance(first, dict) and first.get("success") is False:
        err = first.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        raise RuntimeError(f"Evil search failed for {keyword!r}: {code or first}")
    state = str(((first.get("data") or {}) if isinstance(first, dict) else {}).get("state") or "").lower()
    if state in {"failed", "error", "cancelled"}:
        raise RuntimeError(f"Evil search task {state} for {keyword!r}")
    return extract_video_urls(_items_from_payload(first if isinstance(first, dict) else {}))


def try_evil_discovery(
    request: dict[str, Any],
    form_hints: Any,
    *,
    max_candidates: int = 12,
    markets: list[str] | None = None,
    seen: set[str] | None = None,
    used_ids: set[str] | None = None,
    max_queries: int = 8,
    per_query: int = 20,
    repo_root: Path | None = None,
    log: Callable[[str], None] = print,
    probe: Callable[..., tuple[bool, str]] | None = None,
    searcher: Callable[..., list[dict[str, str]]] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Attempt Evil URL discovery.

    Returns (fresh_candidates, meta). On any probe/config failure, candidates
    is empty and meta['degraded']=True with a reason — caller must Codex-fallback.
    """
    meta: dict[str, Any] = {
        "degraded": False,
        "probe_ok": False,
        "url_count": 0,
        "query_count": 0,
        "reason": "",
        "source": "evil",
    }
    if not evil_discovery_enabled():
        meta["degraded"] = True
        meta["reason"] = "VW_EVIL_DISCOVERY disabled"
        return [], meta

    base = resolve_api_base()
    api_key = resolve_api_key(repo_root=repo_root)
    if not api_key:
        meta["degraded"] = True
        meta["reason"] = (
            "Evil API key missing (set VW_EVIL_API_KEY or VW_EVIL_API_KEY_FILE; "
            "see docs/Docker与Evil0ctal自装.md)"
        )
        return [], meta

    probe_fn = probe or probe_evil
    ok, reason = probe_fn(base)
    meta["probe_ok"] = bool(ok)
    if not ok:
        meta["degraded"] = True
        meta["reason"] = reason
        return [], meta

    queries = build_evil_queries(
        str(request.get("product") or ""),
        form_hints,
        markets=markets,
        max_queries=max_queries,
    )
    if not queries:
        meta["degraded"] = True
        meta["reason"] = "no Evil search queries derived from product/form hints"
        return [], meta

    meta["query_count"] = len(queries)
    log(f"采集-Evil 就绪（{base}），默认加厚发现：{len(queries)} 个关键词 → yt-dlp")
    search_fn = searcher or (
        lambda kw: search_keyword_urls(kw, base=base, api_key=api_key, count=per_query)
    )
    seen_ids = set(seen or ())
    used = set(used_ids or ())
    fresh: list[dict[str, str]] = []
    reused_n = 0
    target = max(int(max_candidates), 1) * 2  # thicken beyond batch cap when possible

    for qi, keyword in enumerate(queries, start=1):
        if len(fresh) >= target:
            break
        try:
            rows = search_fn(keyword)
        except Exception as exc:  # noqa: BLE001 — one bad query must not kill discovery
            log(f"  Evil 搜词失败 [{qi}/{len(queries)}] {keyword!r}：{exc}")
            continue
        new_here = 0
        for row in rows:
            vid = str(row.get("video_id") or "")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            if vid in used:
                reused_n += 1
                continue
            fresh.append(row)
            new_here += 1
            if len(fresh) >= target:
                break
        log(f"  Evil 搜词 [{qi}/{len(queries)}] {keyword!r} → +{new_here} 新 URL（累计 {len(fresh)}）")

    meta["url_count"] = len(fresh)
    meta["reused_skipped"] = reused_n
    if not fresh:
        meta["degraded"] = True
        meta["reason"] = "Evil search returned 0 usable TikTok page URLs"
        log(f"采集-Evil 无可用 URL，降级 Codex web_search：{meta['reason']}")
    else:
        log(f"采集-Evil 加厚完成：{len(fresh)} 条新候选（跳过已用 {reused_n}）")
    return fresh, meta
