"""Standalone TikTok commerce-video collection, without shared workflow state."""

from .collector import candidate_from_seed, collect, normalize_tiktok_url, unique_candidates
from .reference import build_reference_package, validate_reference_package

__all__ = ["candidate_from_seed", "collect", "normalize_tiktok_url", "unique_candidates",
           "build_reference_package", "validate_reference_package"]
