"""Persist per-video choices from the market's approved audition pool."""

import hashlib
import json
from pathlib import Path
import re
import secrets
from workflow.platforms.files import atomic_bytes, atomic_json
from workflow.platforms.locks import acquire


def validate_recording(selected, record, audio_sha256, *, selection_sha256=None,
                       word_metadata_sha256=None):
    for key in ("voice", "localization_sha256", "rate", "volume", "pitch"):
        if record.get(key) != selected.get(key):
            raise ValueError(f"recording does not match persisted {key}")
    if record.get("audio_sha256") != audio_sha256:
        raise ValueError("recording audio hash mismatch")
    for key, actual in (("voice_selection_sha256", selection_sha256),
                        ("word_metadata_sha256", word_metadata_sha256)):
        if actual is not None and record.get(key) != actual:
            raise ValueError(f"recording {key} mismatch")
    return True


def _save_new_selection(path, record):
    atomic_json(path, record, replace=False)


def _save_batch(path, state):
    # Reserve before selection publication so retries keep the same voice.
    atomic_json(path, state)


def revise_localization_binding(selection_path, previous_localization_sha256,
                                localization_sha256, evidence_path):
    """Bind an independently reviewed translation revision to the same voice.

    This changes the script binding only. The prior selection bytes remain in
    an archive; generated audio still needs its own recording and QA evidence.
    """
    selection_path, evidence_path = Path(selection_path).resolve(), Path(evidence_path).resolve()
    old, new = previous_localization_sha256, localization_sha256
    if (any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (old, new)) or old == new):
        raise ValueError("localization revision requires distinct SHA256 bindings")
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes)
    if (evidence.get("status") != "passed_independent_internal_text_review"
            or evidence.get("previous_localization_sha256") != old
            or evidence.get("localization_sha256") != new
            or evidence.get("material_changes_to_approved_meaning") != []):
        raise ValueError("localization revision evidence does not authorize these bindings")
    initial = json.loads(selection_path.read_bytes())
    batch_path = Path(initial["batch_state_path"]) if initial.get("batch_state_path") else None
    lock_path = (batch_path or selection_path).with_suffix((batch_path or selection_path).suffix + ".lock")
    with lock_path.open("a+b") as lock:
        acquire(lock)
        current_bytes = selection_path.read_bytes()
        current = json.loads(current_bytes)
        if current.get("batch_state_path") != initial.get("batch_state_path"):
            raise ValueError("localization revision batch binding changed")
        if current.get("localization_sha256") not in (old, new):
            raise ValueError("localization revision does not match the previous binding")
        archive = selection_path.parent / f"voice-selection-before-{old[:12]}.json"
        if archive in (selection_path, evidence_path) or evidence_path in (selection_path, batch_path):
            raise ValueError("localization revision evidence/archive must be separate files")
        archived_bytes = archive.read_bytes() if archive.exists() else None
        if current.get("localization_sha256") == old:
            if archived_bytes is not None and archived_bytes != current_bytes:
                raise ValueError("localization revision archive already has different bytes")
            previous_bytes = current_bytes
        else:
            if archived_bytes is None:
                raise ValueError("localization revision archive is missing")
            previous_bytes = archived_bytes
        previous = json.loads(previous_bytes)
        if previous.get("localization_sha256") != old:
            raise ValueError("localization revision archive has the wrong previous binding")
        revision = {
            "previous_localization_sha256": old, "localization_sha256": new,
            "evidence_path": str(evidence_path),
            "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "previous_selection_path": str(archive),
            "previous_selection_sha256": hashlib.sha256(previous_bytes).hexdigest(),
        }
        revised = {**previous, "localization_sha256": new, "localization_revision": revision}
        if current not in (previous, revised):
            raise ValueError("localization revision differs from persisted voice/parameters/evidence")
        state = None
        if batch_path is not None:
            state = json.loads(batch_path.read_bytes())
            binding = {key: previous[key] for key in ("approval_sha256", "market", "locale")}
            reserved = state.get("selections", {}).get(str(selection_path))
            if (state.get("schema_version") != 1 or state.get("binding") != binding
                    or reserved not in (previous, revised)):
                raise ValueError("localization revision does not match its batch reservation")
        if current == revised and (state is None or reserved == revised):
            return current
        if archived_bytes is None:
            # Publish the exact old bytes once, before either binding is changed.
            atomic_bytes(archive, previous_bytes, replace=False)
        if state is not None and reserved != revised:
            state["selections"][str(selection_path)] = revised
            _save_batch(batch_path, state)
        if current != revised:
            _save_batch(selection_path, revised)
        return revised


def _delegated_pool(approval_path, approval, customer_id, market, locale):
    """Validate delegated auditory review without inventing user audition approval."""
    def bound_json(item):
        path = (approval_path.parent / item['path']).resolve()
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('delegated voice evidence hash mismatch')
        return json.loads(data)
    if not customer_id or approval.get('customer_id') != customer_id:
        raise ValueError('delegated voice pool belongs to another customer')
    policy = bound_json(approval['delegation'])
    rule = policy.get('language_rules', {}).get(market)
    locales = rule.get('options', []) if isinstance(rule, dict) else [rule]
    if (policy.get('customer_id') != customer_id
            or policy.get('decision_authority') != 'user_delegated_to_workflow'
            or not policy.get('source_turn_id') or market not in policy.get('markets', [])
            or locale not in locales
            or policy.get('new_voice_requires_internal_market_and_quality_review') is not True):
        raise ValueError('delegation does not authorize this customer/market/language')
    pool = approval.get('reviewed_auditions', [])
    if not pool:
        raise ValueError('delegated voice pool has no reviewed auditions')
    for item in pool:
        review = bound_json(item['review'])
        expected = {'market': market, 'locale': locale, 'voice': item['voice'],
                    'audio_sha256': item['sha256']}
        if (review.get('status') != 'passed_internal_voice_review'
                or review.get('evidence_kind') != 'auditory_review' or not review.get('reviewer')
                or any(review.get(k) != v for k, v in expected.items())
                or any(review.get('checks', {}).get(k) is not True for k in
                       ('target_language', 'market_fit', 'natural_delivery', 'no_distortion', 'product_fit'))):
            raise ValueError('delegated voice requires bound auditory review, not metrics alone')
    return pool


def select_voice(approval_path, selection_path, job_id, ordinal, *, localization_sha256=None,
                 approval_scope=None, market="BR", locale="pt-BR", rate="+0%",
                 volume="+0%", pitch="+0Hz", batch_state_path=None, prior_voice=None,
                 customer_id=None):
    """Choose once, optionally rotating a persisted market pool across a batch.

    Cross-job reuse requires ``approval_scope='market_pool'`` and the current
    script hash. This authorizes an audition pool, never a generated recording.
    ``prior_voice`` seeds adjacency only; it does not consume the new round.
    """
    approval_path, selection_path = Path(approval_path), Path(selection_path)
    approval_bytes = approval_path.read_bytes()
    approval = json.loads(approval_bytes)
    if approval_scope not in (None, "market_pool"):
        raise ValueError("invalid voice approval scope")
    delegated = approval.get('status') == 'internally_reviewed_voice_pool'
    generic_pool = delegated or approval.get('status') == 'user_approved_voice_pool'
    if (approval.get("status") not in ("user_approved_both_auditions", "user_approved_voice_pool",
                                      "internally_reviewed_voice_pool")
            or (not generic_pool and (market != "BR" or locale != "pt-BR"))
            or approval.get("market") != market or approval.get("locale") != locale
            or (approval.get("job_id") != job_id and approval_scope != "market_pool")):
        raise ValueError("voice approval does not cover this job/market")
    if approval_scope == "market_pool" and not localization_sha256:
        raise ValueError("market pool reuse requires the current localization hash")
    for name, value, suffix in (("rate", rate, "%"), ("volume", volume, "%"),
                                ("pitch", pitch, "Hz")):
        if not isinstance(value, str) or not re.fullmatch(r"[+-]\d+" + suffix, value):
            raise ValueError(f"invalid voice {name}")
    pool = (_delegated_pool(approval_path, approval, customer_id, market, locale)
            if delegated else approval["approved_auditions"])
    if (not pool or len({item["voice"] for item in pool}) != len(pool)
            or (not generic_pool and any(item["voice"] not in ("pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural")
                                         for item in pool))):
        raise ValueError("invalid approved BR voice pool")
    if generic_pool:
        from workflow.markets import market_profile
        market_profile(market, locale)
        if not delegated and (not approval.get('user_reply') or not approval.get('source_turn_id')):
            raise ValueError('market voice pool requires explicit audition confirmation')
        for item in pool:
            path = (approval_path.parent / item['file']).resolve()
            if (not isinstance(item.get('voice'), str) or not item['voice'].strip()
                    or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
                raise ValueError('approved audition bytes do not match confirmation')
    binding = {
        "job_id": job_id, "ordinal": ordinal, "market": market, "locale": locale,
        "localization_sha256": localization_sha256 or approval["localization_sha256"],
        "approval_sha256": hashlib.sha256(approval_bytes).hexdigest(),
        "rate": rate, "volume": volume, "pitch": pitch,
    }
    if delegated:
        binding.update(customer_id=customer_id, decision_authority='user_delegated_to_workflow',
                       delegation_sha256=approval['delegation']['sha256'])
    if localization_sha256 is not None or approval_scope == "market_pool":
        binding["approval_scope"] = "voice_pool_not_generated_recording"
    if approval_scope == "market_pool":
        binding["pool_authorization_scope"] = "market_pool"
    if batch_state_path is not None:
        batch_state_path = Path(batch_state_path).resolve()
        if batch_state_path == selection_path.resolve():
            raise ValueError("batch state and selection paths must differ")
        binding["batch_state_path"] = str(batch_state_path)
    elif prior_voice is not None:
        raise ValueError("prior voice requires a batch state")

    def validate_saved(saved):
        if (any(saved.get(key) != value for key, value in binding.items())
                or saved.get("batch_state_path") != binding.get("batch_state_path")
                or saved.get("pool_authorization_scope") != binding.get("pool_authorization_scope")):
            raise ValueError("persisted voice selection does not match the current approval")
        if not any(item["voice"] == saved.get("voice")
                   and item["sha256"] == saved.get("approved_audition_sha256") for item in pool):
            raise ValueError("persisted voice selection is outside the approved pool")
        return saved

    def make_record(selected, method):
        return {
            **binding, "selection_method": method,
            "voice": selected["voice"], "approved_audition_sha256": selected["sha256"],
            "audition_path": str((approval_path.parent / selected["file"]).resolve()),
            "retry_policy": "reuse_this_selection_and_parameters",
        }

    if batch_state_path is None:
        if selection_path.exists():
            return validate_saved(json.loads(selection_path.read_text(encoding="utf-8")))
        record = make_record(pool[secrets.randbelow(len(pool))],
                             "secrets.randbelow over the approved market pool")
        _save_new_selection(selection_path, record)
        return record

    # All callers sharing this batch serialize reservations, including parallel
    # producers. The lock is separate because the state file is atomically replaced.
    batch_state_path.parent.mkdir(parents=True, exist_ok=True)
    with batch_state_path.with_suffix(batch_state_path.suffix + ".lock").open("a+b") as lock:
        acquire(lock)
        saved = (validate_saved(json.loads(selection_path.read_text(encoding="utf-8")))
                 if selection_path.exists() else None)
        voices = [item["voice"] for item in pool]
        if prior_voice is not None and prior_voice not in voices:
            raise ValueError("prior voice is outside the approved pool")
        state_binding = {key: binding[key] for key in ("approval_sha256", "market", "locale")}
        if batch_state_path.exists():
            state = json.loads(batch_state_path.read_text(encoding="utf-8"))
            if (state.get("schema_version") != 1 or state.get("binding") != state_binding
                    or (prior_voice is not None and prior_voice != state.get("prior_voice"))):
                raise ValueError("batch voice state does not match the approval/market/seed")
        else:
            state = {"schema_version": 1, "binding": state_binding, "prior_voice": prior_voice,
                     "last_voice": prior_voice, "remaining": voices[:], "selections": {}}
        key = str(selection_path.resolve())
        reserved = state["selections"].get(key)
        if reserved is not None:
            validate_saved(reserved)
            if saved is not None and saved != reserved:
                raise ValueError("persisted voice selection differs from its batch reservation")
            if saved is None:
                _save_new_selection(selection_path, reserved)
            return reserved
        if saved is not None:
            raise ValueError("persisted voice selection has no matching batch reservation")
        remaining = state["remaining"] or voices[:]
        if len(set(remaining)) != len(remaining) or any(voice not in voices for voice in remaining):
            raise ValueError("invalid remaining batch voice pool")
        candidates = [voice for voice in remaining if voice != state["last_voice"]] or remaining
        chosen = candidates[secrets.randbelow(len(candidates))]
        record = make_record(next(item for item in pool if item["voice"] == chosen),
                             "random remaining approved pool; avoid adjacent repeats")
        state["remaining"] = [voice for voice in remaining if voice != chosen]
        state["last_voice"] = chosen
        state["selections"][key] = record
        _save_batch(batch_state_path, state)
        _save_new_selection(selection_path, record)
        return record
