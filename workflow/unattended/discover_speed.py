"""Speed helpers for TikTok discovery (form hints, seed cache, reconnect abort).

Wall-clock of unattended collect is dominated by Codex web_search rounds
(often 400–900s, plus a second round when form-factor screening fails).
These helpers push form-factor into round-1 queries, cap search budget in the
prompt, reuse previously accepted URLs, and abort reconnect thrash early.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class FormHints:
    profile: str
    definition: str
    prefer_queries: list[str] = field(default_factory=list)
    reject_markers: list[str] = field(default_factory=list)
    brand_anchors: list[str] = field(default_factory=list)

    def prompt_block(self, *, max_searches: int = 5, min_urls: int = 8) -> str:
        queries = "\n".join(f"  {i}. `{q}`" for i, q in enumerate(self.prefer_queries, 1)) or "  （按商品名 + 形态词自行组合）"
        reject = "、".join(self.reject_markers[:12]) or "（无额外排除词）"
        brands = "、".join(self.brand_anchors) if self.brand_anchors else "（无固定品牌锚点）"
        return (
            "## 形态与检索预算（必须遵守，用来省时间）\n"
            f"- 形态标准（第 1 轮就按这个搜，不要先搜泛品类再筛选）：{self.definition}\n"
            f"- 品牌/品类锚点（可用来找带 /video/ 链接的公开页面）：{brands}\n"
            "- 优先用下面这些查询（可微调，但**必须保留形态词**；优先能直接给出 "
            "`tiktok.com/@…/video/<数字id>` 的镜像站如 tikwm / urlebird）：\n"
            f"{queries}\n"
            f"- **最多 {max_searches} 次 web_search**；一旦凑齐 ≥{min_urls} 条规范的 "
            "`@账号/video/<数字id>` URL，**立刻输出 JSON，不要继续搜**。\n"
            f"- 查询里不要带这些词（除非请求商品本身就是这类）：{reject}\n"
            "- 不要打开无关长文、不要为「完美名单」反复换站点；有可用 URL 就收工。\n"
        )


# Beauty/skincare: round-1 discovery may start across MX/BR/ID/PH (VO/captions stay on
# task market). Squeeze-web only — still Codex web_search → yt-dlp, not App in-app search.
BEAUTY_CROSS_MARKET_PROFILES = frozenset({"eye_cream", "lip_balm"})


def discover_min_urls(max_candidates: int) -> int:
    """Early-stop floor for Codex web_search; scales with batch candidate budget.

    Pre-squeeze hard-cap at 10 let count=10 batches stop after ~10 URLs despite needing
    ~20 screened clips. Floor stays 8 for tiny jobs; soft cap 20 so we do not chase forever.
    Expected gain vs post-3ec1e06 baseline: ~+20–50% unique URLs (same well — not App breadth).
    """
    n = max(1, int(max_candidates))
    return max(8, min(20, max(10, n // 6 + 8)))


# Ordered profiles: first match wins. Keep patterns lowercase-fold friendly.
_PROFILES: list[dict[str, Any]] = [
    {
        "name": "portable_blender_cup",
        "match": [
            # Only blender-specific words: generic words like 便携/随行 must not pull other
            # products (e.g. 便携颈挂风扇) into this profile.
            "榨汁", "果汁杯", "搅拌杯", "无线榨汁",
            "licuadora portatil", "licuadora personal", "vaso licuadora",
            "portable blender", "personal blender", "portable juicer",
            "blender cup", "blendjet", "ninja blast", "smoothie maker cup",
            "mini blender portable", "cordless blender",
        ],
        "definition": (
            "杯身与电机一体的无线便携榨汁杯：可单手握持、USB/USB-C 充电，"
            "搅拌后可直接当随行饮用杯；排除任何需装到独立电机底座上工作的台式/座式料理机。"
        ),
        "prefer_queries": [
            'tiktok "licuadora portátil recargable" USB vaso review',
            'tiktok "portable blender" BlendJet OR "Ninja Blast" review',
            'site:tikwm.com/video "portable blender" cup',
            'site:urlebird.com/video "portable blender" smoothie',
            '"tiktok.com/@" "/video/" "portable juicer cup"',
        ],
        "reject_markers": [
            "countertop", "pedestal", "base blender", "blender base",
            "台式", "底座", "机座", "料理机底座", "food processor",
            "ice cream maker", "ninja creami",
        ],
        "brand_anchors": ["BlendJet", "Ninja Blast", "Aura portable blender"],
    },
    {
        "name": "neck_fan",
        "match": [
            "颈挂风扇", "挂脖风扇", "挂颈风扇", "脖子风扇", "neck fan", "bladeless neck fan",
            "hanging neck fan", "ventilador de cuello", "ventilador de cuello portatil",
            "ventilador de pescoco", "kipas leher", "kipas angin leher", "neck electric fan",
        ],
        "definition": (
            "挂在脖子上的 U 形/环形便携电风扇（多为无叶、USB 充电、两侧出风）；"
            "排除手持小风扇、桌面风扇、落地扇、空调、腰挂风扇。"
        ),
        "prefer_queries": [
            'tiktok "neck fan" bladeless review',
            'tiktok "neck fan" Philippines review',
            'site:tikwm.com/video "neck fan"',
            'site:urlebird.com/video "neck fan" portable',
            '"tiktok.com/@" "/video/" "neck fan"',
        ],
        "reject_markers": ["handheld fan", "desk fan", "tower fan", "air conditioner", "手持风扇", "桌面风扇", "落地扇"],
        "brand_anchors": ["JISULIFE neck fan", "TORRAS neck fan"],
    },
    {
        "name": "eye_cream",
        "match": [
            "眼霜", "眼精华", "眼部精华", "眼霜霜", "eye cream", "eyecream", "eye gel",
            "contorno de ojos", "crema para ojos", "crema de ojos", "creme para olhos",
            "krim mata", "krim pelembab mata", "eye moisturizer", "under eye cream",
            "under-eye cream", "ojeras crema", "olheiras creme",
        ],
        "definition": (
            "眼霜/眼精华：涂抹于眼周的护肤霜/凝胶演示（点涂、按压、淡化黑眼圈/细纹）；"
            "优先收录真实涂抹/上脸过程，不要求片源必须是超高清棚拍静物。"
            "排除睫毛膏、眼线、眉笔、口红/唇釉、面霜（全脸）、面膜等其它品类。"
        ),
        "prefer_queries": [
            # EN + apply/on-face actions
            'tiktok "eye cream" apply under eye review',
            'tiktok "eye cream" dabbing under-eye tutorial',
            'tiktok "under eye cream" massage face',
            # ES (MX) local + apply
            'tiktok "contorno de ojos" aplicar resena',
            'tiktok "crema para ojos" rutina aplicar',
            'tiktok "crema de ojos" ojeras masaje',
            # PT (BR) local + apply
            'tiktok "creme para olhos" aplicacao',
            'tiktok "creme para olhos" contorno olheiras',
            # ID local + apply
            'tiktok "krim mata" oleskan review',
            'tiktok "krim mata" under eye',
            # public mirrors already used in this repo (tikwm / urlebird only — no new downloaders)
            'site:tikwm.com/video "eye cream" apply',
            'site:tikwm.com/video "contorno de ojos"',
            'site:urlebird.com/video "eye cream" under-eye',
            'site:urlebird.com/video "creme para olhos"',
            '"tiktok.com/@" "/video/" "eye cream" apply',
            '"tiktok.com/@" "/video/" "contorno de ojos"',
            '"tiktok.com/@" "/video/" "krim mata"',
        ],
        "reject_markers": [
            "mascara", "eyeliner", "eyebrow pencil", "brow pencil", "lipstick", "lip gloss",
            "睫毛膏", "眼线", "眉笔", "唇釉", "唇泥", "rimel", "delineador",
        ],
        "brand_anchors": [],
    },
    {
        "name": "lip_balm",
        "match": [
            "润唇膏", "护唇膏", "唇膏", "唇膜", "lip balm", "lipbalm", "chapstick",
            "balsamo labial", "bálsamo labial", "protetor labial", "lip moisturizer",
        ],
        "definition": (
            "管状/罐状润唇膏或护唇膏（涂在嘴唇上保湿，常见旋转管、小罐）；"
            "排除眉笔、眉粉、眼线笔、睫毛膏、口红（色泽浓妆口红）、唇釉、唇泥、粉底、腮红等其它美妆。"
        ),
        "prefer_queries": [
            'tiktok "lip balm" review apply',
            'tiktok "bálsamo labial" resenha',
            'tiktok "protetor labial" review',
            'site:tikwm.com/video "lip balm"',
            'site:urlebird.com/video "lip balm" moisturiz',
        ],
        "reject_markers": [
            "eyebrow pencil", "eyebrow pen", "brow pencil", "brow pen", "eyeliner", "mascara",
            "眉笔", "眉粉", "眼线", "睫毛膏", "lipstick", "lip gloss", "lip tint", "唇釉", "唇泥",
            "lápiz de cejas", "lapiz de cejas", "lápis de sobrancelha", "rimel",
        ],
        "brand_anchors": ["Blistex", "Carmex", "EOS lip balm", "Burt's Bees"],
    },
    {
        "name": "cleaning_brush",
        "match": [
            "清洁刷", "洗碗刷", "锅刷", "cleaning brush", "dish brush",
            "scrub brush", "escova de limpeza", "escova limpeza", "sikat",
        ],
        "definition": (
            "手持清洁刷/洗碗刷/锅刷：刷头+手柄的清洁工具演示；"
            "排除吸尘器、拖把、扫地机器人、洗衣机等非刷类电器。"
        ),
        "prefer_queries": [
            'tiktok "cleaning brush" dish scrub review',
            'tiktok "escova de limpeza" cozinha review',
            'site:tikwm.com/video "dish brush" scrub',
            'site:urlebird.com/video "cleaning brush" kitchen',
        ],
        "reject_markers": ["vacuum", "mop", "robot vacuum", "吸尘器", "拖把", "扫地"],
        "brand_anchors": [],
    },
]


def infer_form_hints(product: str, notes: str = "", style: str = "") -> FormHints:
    blob = _fold(f"{product} {notes} {style}")
    for profile in _PROFILES:
        if any(_fold(m) in blob for m in profile["match"]):
            return FormHints(
                profile=profile["name"],
                definition=profile["definition"],
                prefer_queries=list(profile["prefer_queries"]),
                reject_markers=list(profile["reject_markers"]),
                brand_anchors=list(profile["brand_anchors"]),
            )
    # Generic fallback: still enforce a search budget.
    product = (product or "product").strip() or "product"
    return FormHints(
        profile="generic",
        definition=(f"与「{product}」同一类型和形态的商品演示（先把商品名译成目标市场语言和英文再搜，"
                    f"不要换成别的品类）；排除明显不同品类与库存样片。"),
        prefer_queries=[
            f'tiktok "{product}" review demo',
            f'site:tikwm.com/video "{product}"',
            f'site:urlebird.com/video "{product}"',
            f'"tiktok.com/@" "/video/" "{product}"',
        ],
        reject_markers=["samplelib", "pexels", "stock footage"],
        brand_anchors=[],
    )


def product_slug(product: str, market: str) -> str:
    """Stable folder-safe key; keep letters/digits from any language so CN names do not collide."""
    raw = f"{market}-{product}".strip().casefold()
    parts: list[str] = []
    for ch in raw:
        if ch.isalnum():
            parts.append(ch)
        elif parts and parts[-1] != "-":
            parts.append("-")
    slug = "".join(parts).strip("-")
    if len(slug) < 4:  # market-only or empty product
        import hashlib
        digest = hashlib.sha1(f"{market}|{product}".encode("utf-8")).hexdigest()[:10]
        slug = f"{slug}-{digest}" if slug else digest
    return slug[:80]


def seed_path(seed_dir: Path, request: dict[str, Any]) -> Path:
    return Path(seed_dir) / f"{product_slug(str(request.get('product') or ''), str(request.get('market') or 'XX'))}.json"


SUPPORTED_COLLECTION_MARKETS = ("MX", "BR", "ID", "PH")


def _seed_items_from_path(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            item = {"url": item}
        if isinstance(item, dict) and item.get("url"):
            row = dict(item)
            if payload.get("market") and "source_market" not in row:
                row["source_market"] = payload.get("market")
            out.append(row)
    return out


def load_seed_candidates(
    seed_dir: Path | None,
    request: dict[str, Any],
    *,
    cross_market: bool = False,
) -> list[dict[str, Any]]:
    """Load cached TikTok URLs for this product.

    When ``cross_market`` is True, also merge seeds saved under other supported
    markets for the same product name (IZ: maximize unique same-product clips).
    """
    if not seed_dir:
        return []
    seed_dir = Path(seed_dir)
    primary = _seed_items_from_path(seed_path(seed_dir, request))
    if not cross_market:
        return primary
    product = str(request.get("product") or "")
    task_market = str(request.get("market") or "MX")
    seen = {str(item.get("url") or "") for item in primary}
    merged = list(primary)
    for market in SUPPORTED_COLLECTION_MARKETS:
        if market == task_market:
            continue
        for item in _seed_items_from_path(seed_path(seed_dir, {"product": product, "market": market})):
            url = str(item.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(item)
    return merged


def save_seed_candidates(
    seed_dir: Path | None,
    request: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    product_definition: str = "",
    max_keep: int = 40,
) -> Path | None:
    if not seed_dir or not candidates:
        return None
    seed_dir = Path(seed_dir)
    seed_dir.mkdir(parents=True, exist_ok=True)
    path = seed_path(seed_dir, request)
    existing = load_seed_candidates(seed_dir, request, cross_market=False)
    by_url: dict[str, dict[str, Any]] = {}
    for item in existing + list(candidates):
        url = str(item.get("url") or "")
        if url:
            by_url[url] = {k: item.get(k) for k in ("url", "account", "why", "hook", "product_match_evidence", "video_id") if item.get(k) is not None}
    merged = list(by_url.values())[-max_keep:]
    payload = {
        "market": request.get("market"),
        "product": request.get("product"),
        "product_definition": product_definition,
        "updated_at": _utc_now(),
        "candidates": merged,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def text_suggests_wrong_form(text: str, hints: FormHints) -> str | None:
    """Return a reject reason if discovery text clearly describes the wrong form."""
    if not hints.reject_markers or hints.profile == "generic":
        return None
    hay = _fold(text)
    for marker in hints.reject_markers:
        m = _fold(marker)
        if m and m in hay:
            return f"检索摘要疑似错误形态（含「{marker}」）"
    return None


_RECONNECT_RE = re.compile(r"Reconnecting\.\.\.\s*(\d+)\s*/\s*(\d+)", re.I)


def count_reconnects(events_text: str) -> int:
    """Highest reconnect attempt number seen in Codex JSONL / stderr text."""
    highest = 0
    for match in _RECONNECT_RE.finditer(events_text or ""):
        highest = max(highest, int(match.group(1)))
    # Also count distinct error lines that mention reconnecting.
    if highest == 0:
        highest = sum(1 for line in (events_text or "").splitlines() if "reconnecting" in line.casefold())
    return highest


def should_abort_reconnects(events_text: str, *, limit: int = 3) -> bool:
    return count_reconnects(events_text) >= limit


# --- Used-footage ledger: clips already put into a delivered collection -------------
# Keyed by market + form profile (falls back to product slug) so synonyms such as
# 便携榨汁机 / 便携榨汁杯 / licuadora portátil share one ledger.

class UsedFootageLedger:
    """Used-footage ledger: config/discovery-seeds/used/<key>.json, written only after delivery."""

    @staticmethod
    def path(seed_dir: Path, request: dict[str, Any], profile: str = "") -> Path:
        market = str(request.get("market") or "XX").lower()
        key = f"{market}-{profile}" if profile and profile != "generic" else product_slug(
            str(request.get("product") or ""), str(request.get("market") or "XX"))
        return Path(seed_dir) / "used" / f"{key}.json"


    @staticmethod
    def entries(seed_dir: Path | None) -> list[dict[str, str]]:
        """Every ledger entry from ALL used/*.json, oldest first by used_at (one entry per video ID)."""
        if not seed_dir:
            return []
        latest: dict[str, dict[str, str]] = {}
        for path in sorted((Path(seed_dir) / "used").glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items = payload.get("used") if isinstance(payload, dict) else None
            for item in items or []:
                if isinstance(item, dict) and item.get("video_id"):
                    vid, at = str(item["video_id"]), str(item.get("used_at") or "")
                    if vid not in latest or at > latest[vid]["used_at"]:
                        latest[vid] = {"video_id": vid, "used_at": at, "task_id": str(item.get("task_id") or "")}
        return sorted(latest.values(), key=lambda e: (e["used_at"], e["video_id"]))

    @staticmethod
    def load(seed_dir: Path | None, request: dict[str, Any] | None = None, profile: str = "") -> set[str]:
        """Every video ID that went into ANY delivered video, across all markets and products.

        A TikTok video ID is globally unique, so a clip used for MX must not come back for PH.
        Missing ledgers count as empty; unreadable ones are skipped (record sets them aside).
        """
        return {e["video_id"] for e in UsedFootageLedger.entries(seed_dir)}

    @staticmethod
    def recent(seed_dir: Path | None, limit: int = 120) -> list[str]:
        """The ``limit`` most recently used video IDs, oldest first."""
        return [e["video_id"] for e in UsedFootageLedger.entries(seed_dir)][-limit:] if limit > 0 else []

    @staticmethod
    def record(
        seed_dir: Path | None, request: dict[str, Any], video_ids: list[str], *,
        profile: str = "", task_id: str = "", max_keep: int = 500, lock_timeout: float = 30.0,
    ) -> Path | None:
        """Append clips of a DELIVERED video to the ledger. Lock + atomic replace, safe for parallel tasks.

        A missing ledger starts empty. A corrupt ledger is kept aside as *.corrupt-<ts> (never
        silently overwritten) and a new one is started.
        """
        if not seed_dir or not video_ids:
            return None
        path = UsedFootageLedger.path(seed_dir, request, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(path.suffix + ".lock")
        deadline = time.time() + lock_timeout
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode()); os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 120:  # stale lock from a crashed process
                        lock.unlink(missing_ok=True); continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(f"已用台账被占用超过 {lock_timeout}s：{lock}")
                time.sleep(0.1)
        try:
            items: list[Any] = []
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    items = list(payload.get("used") or [])
                except (OSError, json.JSONDecodeError, AttributeError):
                    path.replace(path.with_name(path.name + f".corrupt-{int(time.time())}"))
                    items = []
            have = {str(i.get("video_id")) for i in items if isinstance(i, dict)}
            for vid in video_ids:
                if vid and str(vid) not in have:
                    items.append({"video_id": str(vid), "task_id": task_id, "used_at": _utc_now()})
                    have.add(str(vid))
            items = items[-max_keep:]
            tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
            tmp.write_text(json.dumps({"market": request.get("market"), "profile": profile, "used": items},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return path
        finally:
            lock.unlink(missing_ok=True)
