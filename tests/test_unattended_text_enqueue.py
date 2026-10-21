from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import unattended as unattended_cli
from workflow.unattended.inbox import Inbox
from workflow.unattended.text_request import TextRequestParseError, parse_enqueue_text


@pytest.mark.parametrize(
    ("text", "market", "product", "count", "style"),
    [
        ("墨西哥唇釉做2条，自然带货", "MX", "唇釉", 2, "自然带货"),
        ("巴西清洁刷做3条", "BR", "清洁刷", 3, None),
        ("印尼 护肤套装 做1条，达人试用", "ID", "护肤套装", 1, "达人试用"),
        ("菲律宾耳机 x2 style:开箱测评", "PH", "耳机", 2, "开箱测评"),
        ("Brazil lipstick 3 videos, tutorial", "BR", "lipstick", 3, "tutorial"),
        ("MX 面膜做两条", "MX", "面膜", 2, None),
        ("帮我做墨西哥口红1条", "MX", "口红", 1, None),
    ],
)
def test_parse_enqueue_text_sentences(text, market, product, count, style):
    payload = parse_enqueue_text(text)
    assert payload["market"] == market
    assert payload["product"] == product
    assert payload["count"] == count
    assert payload["title"]
    if style is None:
        assert "style" not in payload
    else:
        assert payload["style"] == style
    assert "一句话投递" in payload["notes"] or payload.get("notes")


def test_parse_requires_market_and_product():
    with pytest.raises(TextRequestParseError, match="市场"):
        parse_enqueue_text("唇釉做2条，自然带货")
    with pytest.raises(TextRequestParseError, match="商品"):
        parse_enqueue_text("墨西哥做2条")
    with pytest.raises(TextRequestParseError, match="为空"):
        parse_enqueue_text("   ")


def test_cli_text_enqueue_writes_inbox_json(tmp_path, capsys):
    data = tmp_path / "data"
    code = unattended_cli.main(
        ["--data-dir", str(data), "enqueue", "--text", "墨西哥唇釉做2条，自然带货"]
    )
    assert code == 0
    files = list((data / "unattended" / "inbox" / "incoming").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["market"] == "MX"
    assert body["product"] == "唇釉"
    assert body["count"] == 2
    assert body["style"] == "自然带货"
    assert body["task_id"].startswith("task_")
    out = capsys.readouterr().out
    assert "已原子投递" in out
    assert "market=MX" in out


def test_cli_text_from_stdin(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(
        unattended_cli.sys.stdin,
        "read",
        lambda: "Indonesia serum make 1 video, soft light",
    )
    code = unattended_cli.main(["--data-dir", str(data), "enqueue", "--text", "-"])
    assert code == 0
    inbox = Inbox(data)
    files = inbox.incoming_files()
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["market"] == "ID"
    assert body["product"].lower() == "serum"
    assert body["count"] == 1
