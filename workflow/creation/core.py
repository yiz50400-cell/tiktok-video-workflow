"""Validate a local edit plan and keep every script version immutable."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from workflow.analysis.core import probe
from workflow.analysis_attachment import load_analysis_attachment, sha256
from workflow.markets import market_profile


ROLES = {"hook", "product", "action", "result", "cta"}
AUDIT_CHECKS = {"spoken_naturalness", "evidence_accuracy", "runtime_fit", "cta_truthfulness"}
COMMERCIAL_AUDIT_CHECK = "commercial_attractiveness"
VERSION_NAME = re.compile(r"v(\d{4})\.json\Z")
SCRIPT_SOURCE_KINDS = {"original_from_visual_evidence", "collected_reference",
                       "adapted_from_reference", "user_supplied", "revision"}
INTERNAL_CJK_CHARS_PER_SECOND = 5.0
LATIN_TOKEN_SECONDS = .42
NUMBER_TOKEN_SECONDS = .35
PUNCTUATION_PAUSE_SECONDS = .12
MIN_ESTIMATE_RATIO = .65
MAX_ESTIMATE_RATIO = 1.12


def _time(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return round(float(value), 3)


def _absolute_file(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an absolute file path")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{label} must be an existing absolute file path")
    return path.resolve()


def _estimate_line(text, assigned_seconds):
    cjk_characters = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_tokens = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", text)
    number_tokens = re.findall(r"\d+(?:[.,]\d+)?", text)
    punctuation = len(re.findall(r"[，。！？、；：,:!?;]", text))
    estimated = (cjk_characters / INTERNAL_CJK_CHARS_PER_SECOND
                 + len(latin_tokens) * LATIN_TOKEN_SECONDS
                 + len(number_tokens) * NUMBER_TOKEN_SECONDS
                 + punctuation * PUNCTUATION_PAUSE_SECONDS)
    estimated = round(estimated, 2)
    assigned = round(assigned_seconds, 3)
    ratio = round(estimated / assigned, 2) if assigned else float("inf")
    return {
        "cjk_characters": cjk_characters,
        "latin_tokens": latin_tokens,
        "number_tokens": number_tokens,
        "estimated_seconds": estimated,
        "assigned_seconds": assigned,
        "estimate_ratio": ratio,
        "status": "ok" if MIN_ESTIMATE_RATIO <= ratio <= MAX_ESTIMATE_RATIO else "revise",
    }


def _estimate_voice_fit(lines, total, locale=None):
    estimates = []
    for line in lines:
        estimates.append({"start_seconds": line["start_seconds"],
                          "end_seconds": line["end_seconds"],
                          "text": line["text"],
                          **_estimate_line(line["text"], line["end_seconds"] - line["start_seconds"])})
    estimated_total = round(sum(item["estimated_seconds"] for item in estimates), 2)
    passes = all(item["status"] == "ok" for item in estimates)
    return {
        "estimator": {
            "name": "zh-internal-rough-v1",
            "cjk_characters_per_second": INTERNAL_CJK_CHARS_PER_SECOND,
            "latin_token_seconds": LATIN_TOKEN_SECONDS,
            "number_token_seconds": NUMBER_TOKEN_SECONDS,
            "punctuation_pause_seconds": PUNCTUATION_PAUSE_SECONDS,
            "acceptable_estimate_ratio": [MIN_ESTIMATE_RATIO, MAX_ESTIMATE_RATIO],
            "note": "粗估用于拒绝明显超速/过短，不替代实测音频。",
        },
        "lines": estimates,
        "estimated_total_seconds": estimated_total,
        "assigned_total_seconds": round(total, 3),
        "status": "estimated_pass" if passes else "estimated_revise",
        "measured_audio": False,
        "final_localized_voiceover": {
            "locale": locale,
            "status": "pending_localization",
            "measured_audio": False,
            "runtime_fit": "not_evaluated",
        },
    }


def _runtime_review(version):
    voice_fit = _estimate_voice_fit(version["script_lines"], version["total_seconds"],
                                    version.get("voiceover_locale"))
    return {
        "passes": voice_fit["status"] == "estimated_pass",
        "status": voice_fit["status"],
        "voice_fit": voice_fit,
    }


def _normalize_script_source(spec, versions):
    raw = spec.get("script_source", spec.get("script_origin"))
    if raw is None:
        return {"kind": "original_from_visual_evidence",
                "claim_scope": "visual_evidence_only",
                "acquisition": "upstream_analysis_or_local_observation"}
    if isinstance(raw, str):
        raw = {"kind": raw}
    if not isinstance(raw, dict) or raw.get("kind") not in SCRIPT_SOURCE_KINDS:
        raise ValueError("script_source.kind is not supported")
    kind = raw["kind"]
    if kind == "original_from_visual_evidence":
        return {"kind": kind, "claim_scope": "visual_evidence_only",
                "acquisition": "upstream_analysis_or_local_observation"}
    if kind == "revision":
        requests = raw.get("revision_request")
        if (not isinstance(requests, list) or not requests
                or any(not isinstance(item, str) or not item.strip() for item in requests)):
            raise ValueError("revision requires non-empty revision_request")
        return {"kind": kind, "parent_version": versions[-1] if versions else None,
                "revision_request": [item.strip() for item in requests],
                "claim_scope": "revised_draft", "acquisition": "creation_revision"}
    if kind == "user_supplied":
        if not any(isinstance(raw.get(key), str) and raw[key].strip()
                   for key in ("user_text", "text", "text_path")):
            raise ValueError("user_supplied script_source requires user_text or text_path")
        source = {"kind": kind, "claim_scope": "user_provided_text",
                  "acquisition": "user_provided"}
        for key in ("user_text", "text"):
            if isinstance(raw.get(key), str) and raw[key].strip():
                source[key] = raw[key].strip()
        if isinstance(raw.get("text_path"), str) and raw["text_path"].strip():
            path = _absolute_file(raw["text_path"], "script_source.text_path")
            source["text_path"] = str(path)
            source["text_path_sha256"] = sha256(path)
        return source
    reference = raw.get("reference")
    if not isinstance(reference, dict):
        raise ValueError(f"{kind} requires a reference object")
    source_fields = ("path", "text_path", "source_url", "source_name")
    if not any(isinstance(reference.get(key), str) and reference[key].strip()
               for key in source_fields):
        raise ValueError("reference needs a real path, text path, source URL or source name")
    text_status = reference.get("text_status", "unavailable")
    if text_status not in {"provided", "verified", "unavailable", "not_available"}:
        raise ValueError("reference.text_status is invalid")
    beats = reference.get("structure_beats")
    if not isinstance(beats, dict):
        raise ValueError("reference.structure_beats must record hook, concern, proof, turn and CTA")
    concern = beats.get("buyer_concern", beats.get("purchase_concern"))
    required_beats = {"hook": beats.get("hook"), "buyer_concern": concern,
                      "proof": beats.get("proof"),
                      "objection_or_turn": beats.get("objection_or_turn"),
                      "cta": beats.get("cta")}
    if any(not isinstance(value, str) or not value.strip() for value in required_beats.values()):
        raise ValueError("reference.structure_beats must record all five creative beats")
    normalized_reference = {**reference, "text_status": text_status,
                            "structure_beats": required_beats}
    for key in ("path", "text_path"):
        value = reference.get(key)
        if value:
            path = _absolute_file(value, f"reference.{key}")
            normalized_reference[key] = str(path)
            normalized_reference[key + "_sha256"] = sha256(path)
    claim_scope = "reference_text_adaptation" if text_status in {"provided", "verified"} else "visual_structure_only"
    normalized_reference["literal_reproduction_claim"] = False
    normalized_reference["use_scope"] = claim_scope
    if not isinstance(reference.get("adaptation_notes"), str) or not reference["adaptation_notes"].strip():
        raise ValueError("reference.adaptation_notes is required")
    return {"kind": kind, "claim_scope": claim_scope,
            "acquisition": "upstream_collected" if kind == "collected_reference" else "reference_adaptation",
            "reference": normalized_reference}


def _load_media(spec, analysis):
    edit = _absolute_file(spec.get("edit_video_path"), "edit_video_path")
    edit_hash = sha256(edit)
    metadata = probe(edit)
    manifest = analysis["manifest"]
    source = manifest["source"]
    analysis_duration = manifest["metadata"]["duration_seconds"]
    if edit_hash == source["sha256"]:
        if spec.get("transcode_verification_path"):
            raise ValueError("verification is only for a distinct transcoded edit video")
        relationship = {"kind": "same_file"}
    else:
        verification_path = _absolute_file(spec.get("transcode_verification_path"),
                                           "transcode verification")
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        result = verification.get("results", {}).get(edit.stem, {})
        if (verification.get("raw_file") != Path(source["path"]).name
                or verification.get("source_url") != source.get("source_url")
                or result.get("sha256") != edit_hash
                or result.get("full_decode") != "passed"
                or abs(_time(result.get("duration"), "verification duration")
                       - metadata["duration_seconds"]) > .12):
            raise ValueError("transcode verification does not match analysis and edit video")
        relationship = {"kind": "verified_transcode",
                        "verification_path": str(verification_path),
                        "verification_sha256": sha256(verification_path),
                        "method": verification.get("method")}
    if abs(metadata["duration_seconds"] - analysis_duration) > .12:
        raise ValueError("edit video duration differs from analysis source")
    return {"path": str(edit), "sha256": edit_hash, "metadata": metadata}, relationship


def _load_sources(spec, trusted_source_originals=None):
    supplied = spec.get('sources')
    if supplied is None:
        supplied = [spec]
    elif (not isinstance(supplied, list) or not supplied
          or any(not isinstance(source, dict) for source in supplied)):
        raise ValueError('sources must be a nonempty list of source objects')
    elif any(spec.get(key) for key in ('analysis_path', 'edit_video_path',
                                        'transcode_verification_path')):
        raise ValueError('sources cannot be combined with legacy media fields')
    if any(key in spec for key in ('original_sha256', 'trusted_source_originals')):
        raise ValueError('original lineage cannot be declared by a draft spec')
    if trusted_source_originals is not None:
        if (not isinstance(trusted_source_originals, dict)
                or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                       for pair in trusted_source_originals.items() for value in pair)):
            raise ValueError('trusted source lineage must bind SHA256 file and original hashes')
    sources = []
    originals = set()
    for source in supplied:
        if any(key in source for key in ('original_sha256', 'trusted_source_originals')):
            raise ValueError('original lineage cannot be declared by a draft source')
        path = _absolute_file(source.get('analysis_path'), 'analysis_path')
        analysis = load_analysis_attachment(path)
        edit, relationship = _load_media(source, analysis)
        source_sha = analysis['index']['source_sha256']
        original = source_sha
        if trusted_source_originals is not None:
            if edit['sha256'] not in trusted_source_originals:
                raise ValueError('trusted lineage is missing the actual edit video sha256')
            original = trusted_source_originals[edit['sha256']]
        if original in originals:
            raise ValueError('sources must identify distinct original videos')
        originals.add(original)
        sources.append({
            'analysis': {'index_path': str(path), 'index_sha256': analysis['index_sha256'],
                         'source_path': analysis['manifest']['source']['path'],
                         'source_sha256': source_sha, 'status': analysis['index']['status'],
                         'asr_status': analysis['index']['asr_status'],
                         'ocr_status': analysis['index']['ocr_status']},
            'edit_video': edit, 'media_relationship': relationship,
            '_attachment': analysis,
        })
        if trusted_source_originals is not None and (len(supplied) > 1 or original != source_sha):
            sources[-1]['original_sha256'] = original
    if (trusted_source_originals is not None
            and set(trusted_source_originals) != {source['edit_video']['sha256'] for source in sources}):
        raise ValueError('trusted lineage must match exactly the actual edit video sha256 hashes')
    return sources


def _validate_shots(spec, sources):
    supplied = spec.get("shots")
    if not isinstance(supplied, list) or not supplied:
        raise ValueError("shots must be a nonempty list")
    shots = []
    cursor = 0.0
    for number, shot in enumerate(supplied, 1):
        source_index = shot.get('source_index', 0 if len(sources) == 1 else None)
        if type(source_index) is not int or not 0 <= source_index < len(sources):
            raise ValueError(f'shot {number} has invalid or missing source_index')
        source = sources[source_index]
        analysis = source['_attachment']
        manifest = analysis['manifest']
        segments = analysis['observations']['segments']
        frames = {frame['id']: frame['source_time_seconds'] for frame in manifest['frames']}
        limit = min(manifest['metadata']['duration_seconds'],
                    source['edit_video']['metadata']['duration_seconds'])
        role = shot.get("role")
        start = _time(shot.get("source_start_seconds"), "shot start")
        end = _time(shot.get("source_end_seconds"), "shot end")
        if role not in ROLES or not 0 <= start < end <= limit + .02:
            raise ValueError(f"shot {number} has invalid role or source interval")
        description = shot.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"shot {number} needs a visual description")
        segment_index = shot.get("evidence_segment_index")
        if (isinstance(segment_index, bool) or not isinstance(segment_index, int)
                or not 0 <= segment_index < len(segments)):
            raise ValueError(f"shot {number} has invalid evidence segment")
        segment = segments[segment_index]
        if (segment["evidence_status"] != "observed"
                or start < segment["start_seconds"] - .05
                or end > segment["end_seconds"] + .05):
            raise ValueError(f"shot {number} exceeds observed evidence interval")
        ids = shot.get("evidence_frame_ids")
        if (not isinstance(ids, list) or not ids
                or any(frame_id not in segment["frame_ids"] or frame_id not in frames
                       or not start - .05 <= frames[frame_id] <= end + .05 for frame_id in ids)):
            raise ValueError(f"shot {number} has invalid evidence_frame_ids")
        duration = round(end - start, 3)
        shots.append({"id": f"p{number:03d}", "role": role, 'source_index': source_index,
                      "source_start_seconds": start, "source_end_seconds": end,
                      "timeline_start_seconds": round(cursor, 3),
                      "timeline_end_seconds": round(cursor + duration, 3),
                      "duration_seconds": duration, "description": description.strip(),
                      "evidence_segment_index": segment_index,
                      "evidence_frame_ids": ids})
        cursor = round(cursor + duration, 3)
    used = {shot['source_index'] for shot in shots}
    if used != set(range(len(sources))):
        raise ValueError('unused source cannot count as mixing evidence')
    minimum = spec.get('min_unique_sources', 1)
    if type(minimum) is not int or minimum < 1 or len(used) < minimum:
        raise ValueError('minimum unique source requirement is not met')
    requested = spec.get("total_seconds")
    if requested is not None and abs(_time(requested, "total_seconds") - cursor) > .001:
        raise ValueError("declared total_seconds differs from shot durations")
    return shots, cursor


def _validate_script(spec, total):
    supplied = spec.get("script_lines")
    if not isinstance(supplied, list) or not supplied:
        raise ValueError("script_lines must be a nonempty list")
    lines = []
    previous_end = 0.0
    for number, line in enumerate(supplied, 1):
        start = _time(line.get("start_seconds"), "script start")
        end = _time(line.get("end_seconds"), "script end")
        text = line.get("text")
        if (not 0 <= start < end <= total + .001 or start < previous_end - .001
                or start - previous_end > .8 or not isinstance(text, str)
                or not text.strip()):
            raise ValueError(f"script line {number} has an invalid interval, gap or text")
        lines.append({"start_seconds": start, "end_seconds": end, "text": text.strip()})
        previous_end = end
    if total - previous_end > .5:
        raise ValueError("script ends too early for planned runtime")
    voice_fit = _estimate_voice_fit(lines, total, spec.get("voiceover_locale"))
    if voice_fit["status"] == "estimated_revise":
        raise ValueError("runtime estimate requires script revision")
    chars = sum(len(re.findall(r"[\u4e00-\u9fff]", line["text"])) for line in lines)
    voiced = round(sum(line["end_seconds"] - line["start_seconds"] for line in lines), 3)
    voice_fit["summary"] = {"cjk_characters": chars, "voiced_seconds": voiced,
                             "cjk_characters_per_voiced_second": round(chars / voiced, 2)}
    return lines, voice_fit


def _existing_versions(directory):
    versions = []
    if directory.exists():
        for path in directory.iterdir():
            match = VERSION_NAME.fullmatch(path.name)
            if match:
                versions.append(int(match.group(1)))
    versions.sort()
    if versions and versions != list(range(1, versions[-1] + 1)):
        raise ValueError("version history has missing files")
    previous_hash = None
    for number in versions:
        path = directory / f"v{number:04d}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("version") != number
                or record.get("parent_version") != (number - 1 if number > 1 else None)
                or record.get("parent_sha256") != previous_hash):
            raise ValueError("version chain is changed or inconsistent")
        previous_hash = sha256(path)
    return versions


def create_version(spec: dict, output_dir: Path, *, trusted_source_originals=None) -> dict:
    """Validate and save an immutable draft.

    trusted_source_originals maps edit SHA256 to canonical original SHA256 and
    must come from the caller's task-owned Asset registry, never from spec JSON.
    Without it, independent calls check file hashes only; they cannot establish
    whether differently encoded files descend from one original video.
    """
    if not isinstance(spec, dict) or "human_approval" in spec or "copy_audit_status" in spec:
        raise ValueError("approval and audit status cannot be supplied by a draft")
    for field in ("job_id", "product_series", "author"):
        if not isinstance(spec.get(field), str) or not spec[field].strip():
            raise ValueError(f"{field} is required")
    creative_angle = None
    target_market = None
    if "market" in spec or "voiceover_locale" in spec:
        target_market = spec.get("market")
        target_locale = spec.get("voiceover_locale")
        target_market = market_profile(target_market, target_locale)
        required_angle = ("angle", "hook", "buyer_concern", "proof_action", "cta_direction")
        if any(not isinstance(spec.get(field), str) or not spec[field].strip()
               for field in required_angle):
            raise ValueError("market creative version requires angle, hook, buyer_concern, proof_action and cta_direction")
        creative_angle = {field: spec[field].strip() for field in required_angle}
        source_asset_ids = spec.get("source_asset_ids", [])
        recently_used = set(spec.get("recently_used_asset_ids", []))
        if (not isinstance(source_asset_ids, list)
                or any(not isinstance(asset, str) or not asset.strip() for asset in source_asset_ids)):
            raise ValueError("source_asset_ids must be a list of asset IDs")
        if not isinstance(spec.get("recently_used_asset_ids", []), list):
            raise ValueError("recently_used_asset_ids must be a list")
        if set(source_asset_ids) & recently_used:
            raise ValueError("source asset is within the seven_day no-reuse window")
        creative_angle["source_asset_ids"] = source_asset_ids
        creative_angle["reuse_policy"] = {
            "seven_day_rule": "enforced",
            "shared_asset_reuse_authorized": bool(spec.get("shared_asset_reuse_authorized", False)),
        }
    sources = _load_sources(spec, trusted_source_originals)
    shots, total = _validate_shots(spec, sources)
    saved_sources = [{key: value for key, value in source.items() if key != '_attachment'}
                     for source in sources]
    pending = spec.get("pending_configuration", [])
    if not isinstance(pending, list) or any(not isinstance(item, str) or not item.strip()
                                             for item in pending):
        raise ValueError("pending_configuration must be a list of nonempty strings")
    output_dir = Path(output_dir).resolve()
    versions = _existing_versions(output_dir)
    next_version = versions[-1] + 1 if versions else 1
    if next_version > 9999:
        raise ValueError("version limit reached")
    script_source = _normalize_script_source(spec, versions)
    lines, voice_fit = _validate_script(spec, total)
    parent_sha256 = sha256(output_dir / f"v{versions[-1]:04d}.json") if versions else None
    record = {
        "schema_version": 1, "version": next_version,
        "parent_version": versions[-1] if versions else None,
        "parent_sha256": parent_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": spec["job_id"].strip(), "product_series": spec["product_series"].strip(),
        "author": spec["author"].strip(),
        "analysis": saved_sources[0]['analysis'],
        "edit_video": saved_sources[0]['edit_video'],
        "media_relationship": saved_sources[0]['media_relationship'],
        "sources": saved_sources,
        "source_diversity": {'used_unique_originals': len(saved_sources),
                             'minimum_required': spec.get('min_unique_sources', 1)},
        "shots": shots, "total_seconds": total,
        "script_language": "zh-CN", "script_lines": lines, "voice_fit": voice_fit,
        "script_source": script_source,
        "voiceover_timing_status": "pending_localized_voiceover",
        "pending_configuration": pending,
        "copy_audit_status": "pending",
        "human_approval": {"status": "pending_user", "approved_by": None,
                           "approved_at": None},
    }
    if target_market is not None:
        record["market"] = target_market["code"]
        record["market_profile"] = target_market
        record["voiceover_locale"] = target_market["voiceover_locale"]
        record["creative_angle"] = creative_angle
    if len(saved_sources) > 1 or any('original_sha256' in source for source in saved_sources):
        record['source_diversity']['lineage_check'] = (
            'task_asset_metadata' if trusted_source_originals is not None else 'file_sha256_only')
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"v{next_version:04d}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"version": next_version, "version_path": str(path),
            "version_sha256": sha256(path), "total_seconds": total,
            "copy_audit_status": "pending", "human_approval_status": "pending_user"}


def audit_version(version_path: Path, audit: dict) -> dict:
    """Save a separate copy review bound to exact draft bytes; never approve for a human."""
    version_path = Path(version_path).resolve(strict=True)
    version = json.loads(version_path.read_text(encoding="utf-8"))
    if (not isinstance(audit, dict) or "human_approval" in audit
            or "approved_by" in audit or "approved_at" in audit):
        raise ValueError("human approval cannot be recorded by copy audit")
    runtime_review = _runtime_review(version)
    reviewer = audit.get("reviewer")
    if (not isinstance(reviewer, str) or not reviewer.strip()
            or reviewer.strip() == version["author"]):
        raise ValueError("copy audit requires an independent reviewer")
    verdict = audit.get("verdict")
    checks = audit.get("checks")
    notes = audit.get("notes")
    requires_commercial = "market" in version
    expected_checks = AUDIT_CHECKS | ({COMMERCIAL_AUDIT_CHECK} if requires_commercial else set())
    if (verdict not in {"pass", "revise"} or not isinstance(checks, dict)
            or set(checks) != expected_checks
            or any(not isinstance(value, bool) for value in checks.values())
            or (verdict == "pass" and not all(checks.values()))
            or not isinstance(notes, list) or not notes
            or any(not isinstance(note, str) or not note.strip() for note in notes)):
        if requires_commercial and (not isinstance(checks, dict)
                                    or COMMERCIAL_AUDIT_CHECK not in checks):
            raise ValueError("commercial_attractiveness check is required for a market draft")
        raise ValueError("copy audit requires a verdict, checks and review notes")
    if checks.get("runtime_fit") is True and not runtime_review["passes"]:
        raise ValueError("runtime estimate does not support runtime_fit=true")
    path = version_path.with_name(version_path.stem + "-audit.json")
    record = {"schema_version": 1, "version": version["version"],
              "version_path": str(version_path), "version_sha256": sha256(version_path),
              "reviewed_at": datetime.now(timezone.utc).isoformat(),
              "reviewer": reviewer.strip(), "verdict": verdict,
              "status": "passed_needs_user_approval" if verdict == "pass" else "needs_revision",
              "checks": checks, "notes": [note.strip() for note in notes],
              "human_approval_status": "pending_user",
              "audit_scope": "commercial creative review" if requires_commercial else "copy review"}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"audit_path": str(path), "audit_sha256": sha256(path),
            "status": record["status"], "human_approval_status": "pending_user"}


def show_version(version_path: Path) -> dict:
    version_path = Path(version_path).resolve(strict=True)
    _existing_versions(version_path.parent)
    version = json.loads(version_path.read_text(encoding="utf-8"))
    audit_path = version_path.with_name(version_path.stem + "-audit.json")
    audit = None
    runtime_review = _runtime_review(version)
    if audit_path.is_file():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("version_sha256") != sha256(version_path):
            raise ValueError("audit no longer matches version bytes")
        audit["runtime_review"] = runtime_review
    effective_status = audit["status"] if audit else "pending"
    runtime_warning = None
    if (audit and audit.get("status") == "passed_needs_user_approval"
            and not runtime_review["passes"]):
        effective_status = "needs_revision_runtime_warning"
        runtime_warning = "stored audit pass is superseded by a failing runtime estimate"
    return {"version_path": str(version_path), "version_sha256": sha256(version_path),
            "plan": version, "audit": audit,
            "runtime_review": runtime_review,
            "runtime_warning": runtime_warning,
            "effective_copy_audit_status": effective_status,
            "human_approval_status": "pending_user"}
