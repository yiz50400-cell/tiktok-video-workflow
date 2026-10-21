"""Build a truthful, reviewable research package from collected candidates."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
from datetime import datetime, timezone

from .collector import candidate_from_seed


RELATIONSHIPS = {
    "same_product_candidate": {"same_product_family"},
    "different_product_structure_reference": {"different_or_unverified_product"},
}
USE_SCOPES = {
    "material_candidate_and_structure",
    "structure_only",
}
EVIDENCE_SCOPES = {"visual_structure_only", "reference_text_adaptation"}
TRANSCRIPTION_STATUSES = {"unavailable", "provided", "verified"}


def _analysis_summary(path: Path) -> dict:
    if not path.is_absolute() or not path.is_file():
        raise ValueError("analysis path must be an existing absolute file")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ("status", "asr_status", "ocr_status", "segments")
    if any(key not in data for key in required):
        raise ValueError("analysis attachment is missing status fields")
    return {
        "path": str(path.resolve()),
        "status": data["status"],
        "asr_status": data["asr_status"],
        "ocr_status": data["ocr_status"],
        "segments": data["segments"],
    }


def _reference(candidate: dict, analysis_paths: dict[str, Path] | None) -> dict:
    same = candidate.get("product_match_level") == "same_product_family"
    relationship = ("same_product_candidate" if same
                    else "different_product_structure_reference")
    usable = bool(candidate.get("material_eligibility")) and same
    use_scope = "material_candidate_and_structure" if usable else "structure_only"
    transcription = candidate.get("transcription_status", "unavailable")
    if transcription not in TRANSCRIPTION_STATUSES:
        transcription = "unavailable"
    result = {
        "candidate_id": candidate.get("id"),
        "source_url": candidate.get("source_url"),
        "relationship": relationship,
        "use_scope": use_scope,
        "visible_metrics": deepcopy(candidate.get("visible_metrics") or {}),
        "popularity_status": candidate.get("popularity_status") or
        "互动数据不可推出销量或转化",
        "hook": candidate.get("hook") or candidate.get("hook_and_action_observation") or
        "未从保存证据确认",
        "buyer_concern": candidate.get("buyer_concern") or "未从口播确认",
        "proof": candidate.get("demo_or_proof") or candidate.get("action_and_proof") or
        "未从保存证据确认",
        "objection_or_turn": candidate.get("objection_or_turn") or "未从口播确认",
        "cta": candidate.get("cta") or candidate.get("purchase_intent_evidence") or "未核实",
        "transcription_status": transcription,
        "evidence_scope": ("reference_text_adaptation" if transcription in {"provided", "verified"}
                            else "visual_structure_only"),
        "reuse_rights": candidate.get("reuse_rights") or "unverified",
    }
    if analysis_paths and result["candidate_id"] in analysis_paths:
        result["analysis"] = _analysis_summary(Path(analysis_paths[result["candidate_id"]]))
    return result


def validate_reference_package(package: dict) -> None:
    if not isinstance(package, dict) or package.get("schema_version") != 1:
        raise ValueError("reference package schema_version must be 1")
    product = package.get("product")
    if not isinstance(product, dict) or not product.get("name"):
        raise ValueError("reference package product.name is required")
    if package.get("market") not in {"MX", "BR", "ID", "PH"}:
        raise ValueError("reference package market is invalid")
    references = package.get("references")
    if not isinstance(references, list) or not references:
        raise ValueError("reference package needs references")
    seen = set()
    for item in references:
        if not isinstance(item, dict):
            raise ValueError("reference must be an object")
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("reference candidate_id is required")
        if candidate_id in seen:
            raise ValueError("reference candidate_id must be unique")
        seen.add(candidate_id)
        relationship = item.get("relationship")
        if relationship not in RELATIONSHIPS:
            raise ValueError("reference relationship is required")
        if item.get("use_scope") not in USE_SCOPES:
            raise ValueError("reference use_scope is required")
        if item.get("evidence_scope") not in EVIDENCE_SCOPES:
            raise ValueError("reference evidence_scope is required")
        if item.get("transcription_status") not in TRANSCRIPTION_STATUSES:
            raise ValueError("reference transcription_status is invalid")
        for field in ("hook", "buyer_concern", "proof", "objection_or_turn", "cta"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"reference {field} is required")
        if relationship == "different_product_structure_reference" and item["use_scope"] != "structure_only":
            raise ValueError("different-product reference must be structure_only")


def build_reference_package(report: dict, *, market: str,
                            product: dict | None = None,
                            analysis_paths: dict[str, Path] | None = None) -> dict:
    if not isinstance(report, dict) or not isinstance(report.get("candidates"), list):
        raise ValueError("collection report must contain candidates")
    if market not in {"MX", "BR", "ID", "PH"}:
        raise ValueError("market is invalid")
    product = deepcopy(product or report.get("product") or {})
    brand = product.get("brand", "")
    name = product.get("name", "")
    candidates = []
    for item in report["candidates"]:
        if not isinstance(item, dict):
            continue
        # Earlier collection evidence predates normalized candidate fields. Normalize it
        # in memory while retaining all saved evidence needed for review.
        if "product_match_level" not in item:
            normalized = candidate_from_seed(item, brand, name)
            normalized.update({key: value for key, value in item.items() if value is not None})
            item = normalized
        candidates.append(item)
    # Same-product candidates lead; visible engagement is a tie-breaker only.
    def rank(item):
        same = item.get("product_match_level") == "same_product_family"
        eligible = bool(item.get("material_eligibility"))
        likes = (item.get("visible_metrics") or {}).get("likes") or 0
        return (0 if eligible else 1 if same else 2, -likes, str(item.get("id", "")))

    references = [_reference(item, analysis_paths) for item in sorted(candidates, key=rank)]
    package = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "product": deepcopy(product),
        "market": market,
        "discovery": {
            "source_adapter": report.get("discovery", {}).get("source_adapter", "saved_candidate_evidence"),
            "automatic_search": report.get("discovery", {}).get("automatic_tiktok_search", "not_implemented"),
            "same_product_priority": True,
            "popularity_claim_policy": "visible_engagement_is_not_sales_evidence",
        },
        "references": references,
        "review_status": "pending_user",
        "approved": False,
        "rendered_count": 0,
    }
    validate_reference_package(package)
    return package
