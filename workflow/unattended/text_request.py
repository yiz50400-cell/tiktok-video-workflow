"""Parse one-sentence Chinese/English enqueue text into inbox request JSON."""

from __future__ import annotations

import re
from typing import Any

from .schema import RequestValidationError

MARKET_ALIASES: list[tuple[str, str]] = [
    ("印度尼西亚", "ID"),
    ("indonesia", "ID"),
    ("philippines", "PH"),
    ("菲律宾", "PH"),
    ("mexico", "MX"),
    ("墨西哥", "MX"),
    ("brasil", "BR"),
    ("brazil", "BR"),
    ("巴西", "BR"),
    ("印尼", "ID"),
    ("mx", "MX"),
    ("br", "BR"),
    ("id", "ID"),
    ("ph", "PH"),
]

MARKET_LABEL = {"MX": "墨西哥", "BR": "巴西", "ID": "印尼", "PH": "菲律宾"}

CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


class TextRequestParseError(RequestValidationError):
    """Raised when one-sentence text cannot become a valid inbox request."""


def _normalize(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = text.strip()
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return text


def _find_market(text: str) -> tuple[str | None, str]:
    for alias, code in MARKET_ALIASES:
        if re.search(r"[A-Za-z]", alias):
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.I)
            match = pattern.search(text)
        else:
            match = re.search(re.escape(alias), text)
        if not match:
            continue
        cleaned = (text[: match.start()] + " " + text[match.end() :]).strip()
        cleaned = re.sub(r" {2,}", " ", cleaned)
        return code, cleaned
    return None, text


def _parse_count(text: str) -> tuple[int | None, str]:
    patterns: list[tuple[re.Pattern[str], Any]] = [
        (
            re.compile(
                r"(?:做|制作|拍|出|生产|需要|要|make|create|produce|do)?\s*([1-9][0-9]?)\s*(?:条|條|个|個|支|部|则|則|videos?|clips?|pieces?)",
                re.I,
            ),
            lambda m: int(m.group(1)),
        ),
        (
            re.compile(
                r"(?:做|制作|拍|出|生产|需要|要)?\s*([一二两三兩四五六七八九十])\s*(?:条|條|个|個|支|部|则|則)"
            ),
            lambda m: CN_NUM[m.group(1)],
        ),
        (
            re.compile(r"(?:count|qty|quantity)\s*[=:]?\s*([1-9][0-9]?)", re.I),
            lambda m: int(m.group(1)),
        ),
        (
            re.compile(r"(?:x|×|＊|\*)\s*([1-9][0-9]?)", re.I),
            lambda m: int(m.group(1)),
        ),
        (
            re.compile(r"\b([1-9][0-9]?)\s*(?:videos?|clips?)\b", re.I),
            lambda m: int(m.group(1)),
        ),
    ]
    for pattern, extractor in patterns:
        match = pattern.search(text)
        if not match:
            continue
        count = extractor(match)
        if 1 <= count <= 100:
            remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
            remainder = re.sub(r" {2,}", " ", remainder)
            return count, remainder
    return None, text


def _split_style_notes(text: str) -> tuple[str, str | None, str | None]:
    style = None
    notes = None
    working = text.strip(" ，,;；、")

    style_match = re.search(r"(?:风格|風格|style)\s*[:：]?\s*(.+)$", working, re.I)
    if style_match:
        style = style_match.group(1).strip(" ，,;；")
        working = working[: style_match.start()].strip(" ，,;；")
        return working, style or None, notes

    parts = re.split(r"[，,;；]", working, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        left, right = parts[0].strip(), parts[1].strip()
        if 1 <= len(right) <= 40 and not re.search(r"[/\\]", right):
            style = right
            working = left
        else:
            notes = right
            working = left
    return working, style, notes


def _extract_product(text: str) -> str | None:
    working = text.strip(" ，,;；、的")
    working = re.sub(r"^(?:请|請|帮我|幫我|给我|給我|帮忙|幫忙)\s*", "", working)
    working = re.sub(
        r"\s*(?:的)?\s*(?:视频|視頻|成片|短视频|短視頻|tiktok|商品视频|商品視頻)\s*$",
        "",
        working,
        flags=re.I,
    )
    working = re.sub(r"^(?:make|create|produce|do)\s+", "", working, flags=re.I)
    working = re.sub(r"\s+(?:make|create|produce|do)\s*$", "", working, flags=re.I)
    working = working.strip(" ，,;；、的")
    working = re.sub(r"\b(?:videos?|clips?|pieces?)\b", "", working, flags=re.I)
    working = re.sub(r"\b(?:make|create|produce|do)\b", " ", working, flags=re.I)
    working = re.sub(r" {2,}", " ", working).strip(" -_")
    if not working:
        return None
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{1,30}", working)
    if cjk_runs:
        best = max(cjk_runs, key=len)
        latin_only = re.sub(r"[\u4e00-\u9fff]+", " ", working).strip()
        if not latin_only or len(latin_only) <= 2:
            return best
    return working


def parse_enqueue_text(text: str) -> dict[str, Any]:
    """Parse a free-form sentence into an inbox request dict (not yet schema-validated)."""
    if not isinstance(text, str):
        raise TextRequestParseError("一句话投递内容必须是字符串")
    original = _normalize(text)
    if not original:
        raise TextRequestParseError(
            "一句话投递内容为空；请提供商品和市场，例如：墨西哥唇釉做2条，自然带货"
        )

    market, rest = _find_market(original)
    count, rest = _parse_count(rest)
    rest, style, notes = _split_style_notes(rest)
    product = _extract_product(rest)

    missing: list[str] = []
    if not market:
        missing.append("市场（MX/BR/ID/PH，或墨西哥/巴西/印尼/菲律宾）")
    if not product:
        missing.append("商品（product）")
    if missing:
        raise TextRequestParseError(
            "无法从一句话中解析出" + "、".join(missing) + f"。原文：{original}"
        )

    if count is None:
        count = 1

    payload: dict[str, Any] = {
        "title": f"{MARKET_LABEL[market]}{product}",
        "product": product,
        "market": market,
        "count": count,
        "notes": notes if notes else f"一句话投递：{original}",
    }
    if style:
        payload["style"] = style
    return payload
