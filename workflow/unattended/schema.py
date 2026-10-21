
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ALLOWED_MARKETS = {"MX", "BR", "ID", "PH"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RequestValidationError(ValueError):
    pass


def canonical_json_bytes(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def request_sha256(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def raw_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise RequestValidationError(f"字段 {key} 必须是字符串")
    value = value.strip()
    if not value:
        raise RequestValidationError(f"字段 {key} 不能为空")
    return value


def validate_request(data: Any, *, check_media_exists: bool = True) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise RequestValidationError("请求根节点必须是 JSON 对象")
    try:
        json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"请求包含不可序列化内容：{exc}") from exc

    normalized = dict(data)
    normalized["title"] = _required_text(data, "title")
    normalized["product"] = _required_text(data, "product")

    market = data.get("market")
    if not isinstance(market, str) or market not in ALLOWED_MARKETS:
        raise RequestValidationError("字段 market 必须是 MX、BR、ID、PH 之一")
    normalized["market"] = market

    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise RequestValidationError("字段 count 必须是 1–100 的整数")
    normalized["count"] = count

    for key in ("style", "notes"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise RequestValidationError(f"字段 {key} 必须是字符串")
        if isinstance(value, str):
            normalized[key] = value.strip()

    schema_version = data.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise RequestValidationError("字段 schema_version 当前只支持整数 1")
    normalized["schema_version"] = 1

    media_paths = data.get("media_paths", [])
    if media_paths is None:
        media_paths = []
    if not isinstance(media_paths, list):
        raise RequestValidationError("字段 media_paths 必须是绝对路径字符串数组")
    checked_paths: list[str] = []
    for index, item in enumerate(media_paths):
        if not isinstance(item, str) or not item.strip():
            raise RequestValidationError(f"media_paths[{index}] 必须是非空字符串")
        item = item.strip()
        if not os.path.isabs(item):
            raise RequestValidationError(f"media_paths[{index}] 必须是绝对路径")
        path = Path(item)
        if check_media_exists and not path.exists():
            raise RequestValidationError(f"媒体路径不存在：{item}")
        checked_paths.append(item)
    if checked_paths or "media_paths" in data:
        normalized["media_paths"] = checked_paths

    concepts = data.get("concepts")
    if concepts is not None:
        if not isinstance(concepts, list) or not concepts:
            raise RequestValidationError("字段 concepts 必须是非空数组")
        required = (
            "ordinal",
            "angle",
            "hook",
            "buyer_concern",
            "proof_requirement",
            "cta_direction",
        )
        checked_concepts: list[dict[str, Any]] = []
        for index, concept in enumerate(concepts):
            if not isinstance(concept, Mapping):
                raise RequestValidationError(
                    f"concepts[{index}] 必须是对象，且字段对齐 workflow plan-batch "
                    f"（ordinal/angle/hook/buyer_concern/proof_requirement/cta_direction）"
                )
            missing = [key for key in required if key not in concept]
            if missing:
                raise RequestValidationError(
                    f"concepts[{index}] 缺少字段：{', '.join(missing)}"
                )
            ordinal = concept["ordinal"]
            if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 1 <= ordinal <= 100:
                raise RequestValidationError(f"concepts[{index}].ordinal 必须是 1–100 的整数")
            item: dict[str, Any] = {"ordinal": ordinal}
            for key in required[1:]:
                value = concept[key]
                if not isinstance(value, str) or not value.strip():
                    raise RequestValidationError(f"concepts[{index}].{key} 必须是非空字符串")
                item[key] = value.strip()
            asset_ids = concept.get("source_asset_ids", [])
            if asset_ids is None:
                asset_ids = []
            if not isinstance(asset_ids, list) or any(not isinstance(x, str) for x in asset_ids):
                raise RequestValidationError(f"concepts[{index}].source_asset_ids 必须是字符串数组")
            item["source_asset_ids"] = list(asset_ids)
            checked_concepts.append(item)
        normalized["concepts"] = checked_concepts

    task_id = data.get("task_id")
    if task_id is None:
        stable_input = dict(normalized)
        stable_input.pop("task_id", None)
        task_id = "task_" + request_sha256(stable_input)[:20]
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise RequestValidationError("字段 task_id 只能包含字母、数字、点、下划线、连字符，最长 128 字符")
    normalized["task_id"] = task_id
    return normalized
