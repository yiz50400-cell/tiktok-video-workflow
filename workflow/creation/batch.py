"""Validate creation-side batch concepts without claiming rendered output."""

from __future__ import annotations

import re
from copy import deepcopy

from workflow.markets import market_profile


_TEXT_FIELDS = ("angle", "hook", "buyer_concern", "proof_requirement", "cta_direction")
_TEMPLATE_ONLY = {"music", "audio", "speed", "tempo", "order", "sequence", "shuffle", "变速", "换音乐", "调顺序"}


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _norm(value):
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _validate_ordinal(value, target_count):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= target_count:
        raise ValueError(f"ordinal must be between 1 and {target_count}")


def create_batch_contract(spec: dict) -> dict:
    """Return a truthful partial creation plan compatible with shared concepts."""
    if not isinstance(spec, dict):
        raise ValueError("batch spec must be an object")
    market = spec.get("market")
    locale = spec.get("voiceover_locale")
    profile = market_profile(market, locale)
    target_count = spec.get("target_count")
    if isinstance(target_count, bool) or not isinstance(target_count, int) or not 1 <= target_count <= 100:
        raise ValueError("target_count must be an integer from 1 to 100")
    concepts = spec.get("concepts", [])
    if not isinstance(concepts, list) or len(concepts) > target_count:
        raise ValueError("concepts must contain no more than target_count items")
    allowed_assets = set(spec.get("allowed_asset_ids", []))
    recent_assets = set(spec.get("recently_used_asset_ids", []))
    if not all(isinstance(item, str) and item for item in allowed_assets | recent_assets):
        raise ValueError("asset IDs must be non-empty strings")
    reuse_authorized = spec.get("shared_asset_reuse_authorized", False)
    if not isinstance(reuse_authorized, bool):
        raise ValueError("shared_asset_reuse_authorized must be boolean")
    ordinals = set()
    semantic_keys = set()
    bound_assets = {}
    plans = []
    for raw in concepts:
        if not isinstance(raw, dict):
            raise ValueError("each concept must be an object")
        ordinal = raw.get("ordinal")
        _validate_ordinal(ordinal, target_count)
        if ordinal in ordinals:
            raise ValueError("duplicate ordinal")
        ordinals.add(ordinal)
        values = {field: _text(raw.get(field), field) for field in _TEXT_FIELDS}
        basis = raw.get("difference_basis", "")
        if basis:
            basis_text = _text(basis, "difference_basis")
            basis_norm = _norm(basis_text)
            if any(token in basis_norm for token in _TEMPLATE_ONLY):
                raise ValueError("difference_basis cannot be template-only music, speed or order")
        key = tuple(_norm(values[field]) for field in _TEXT_FIELDS)
        if key in semantic_keys:
            raise ValueError("duplicate creative difference")
        semantic_keys.add(key)
        assets = raw.get("source_asset_ids", [])
        if not isinstance(assets, list) or any(not isinstance(asset, str) or not asset for asset in assets):
            raise ValueError("source_asset_ids must be a list of asset IDs")
        if not set(assets) <= allowed_assets:
            raise ValueError("source asset is not part of the available creation set")
        if set(assets) & recent_assets:
            raise ValueError("source asset is within the seven_day no-reuse window")
        for asset in assets:
            bound_assets.setdefault(asset, []).append(ordinal)
        plans.append({"ordinal": ordinal, **values,
                      "proof_action": values["proof_requirement"],
                      "source_asset_ids": assets,
                      "material_status": "asset_bound" if assets else "awaiting_asset_binding",
                      "proof_support_status": "asset_bound" if assets else "awaiting_asset_binding",
                      "difference_basis": basis.strip() if isinstance(basis, str) else basis})
    reused = {asset: ordinals for asset, ordinals in bound_assets.items() if len(ordinals) > 1}
    if reused and not reuse_authorized:
        raise ValueError("shared_asset_reuse requires explicit authorization")
    plans.sort(key=lambda item: item["ordinal"])
    planned_ordinals = {item["ordinal"] for item in plans}
    return {
        "schema_version": 1,
        "market": profile["code"],
        "market_profile": profile,
        "voiceover_locale": profile["voiceover_locale"],
        "internal_script_language": "zh-CN",
        "plans": deepcopy(plans),
        "batch": {
            "status": "PARTIALLY_PLANNED" if plans else "WAITING_CONCEPTS",
            "target_count": target_count,
            "planned_count": len(plans),
            "rendered_count": 0,
            "representative_count": min(3, len(plans)),
            "unplanned_ordinals": [ordinal for ordinal in range(1, target_count + 1)
                                   if ordinal not in planned_ordinals],
            "reuse_policy": {
                "seven_day_rule": "enforced",
                "shared_asset_reuse": "authorized" if reused else "not_authorized",
                "shared_asset_reuse_authorized": reuse_authorized,
                "shared_assets": reused,
            },
        },
    }
