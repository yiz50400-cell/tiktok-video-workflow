"""Candidate evidence, retrieval and independently verified media artifacts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import importlib.util
from urllib.parse import urlsplit, urlunsplit

import imageio_ffmpeg


VIDEO_URL = re.compile(r"^/@[A-Za-z0-9._]+/video/(\d{10,25})/?$")
METRIC_KEYS = ("views", "likes", "comments", "saves", "shares")


def normalize_tiktok_url(url):
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in {"tiktok.com", "www.tiktok.com"}:
        raise ValueError(f"unsupported TikTok video URL: {url}")
    match = VIDEO_URL.fullmatch(parts.path)
    if not match:
        raise ValueError(f"not a canonical TikTok video URL: {url}")
    return urlunsplit(("https", "www.tiktok.com", parts.path.rstrip("/"), "", ""))


def _identity(url):
    try:
        return "tiktok:" + VIDEO_URL.fullmatch(urlsplit(normalize_tiktok_url(url)).path).group(1)
    except ValueError:
        return urlsplit(url).path.rstrip("/") or url


def unique_candidates(candidates):
    merged = {}
    for candidate in candidates:
        key = _identity(candidate["source_url"])
        if key not in merged:
            merged[key] = dict(candidate)
        else:
            for field, value in candidate.items():
                if value and not merged[key].get(field):
                    merged[key][field] = value
    return list(merged.values())


def candidate_from_seed(seed, brand, product):
    url = seed["source_url"]
    evidence = seed.get("product_match", "")
    lower = evidence.lower()
    brand_match = brand.lower() in lower
    product_words = product.lower().split()
    family_match = len(product_words) >= 2 and " ".join(product_words[:2]) in lower
    unrelated = any(x in lower for x in ("不同商品", "不是"))
    same_family = brand_match and family_match and not unrelated
    commercial = seed.get("commercial_status", "")
    paid = "paid_ad_verified" in commercial
    partnership = any(x in commercial.lower() for x in ("partner", "embajador", "合作", "affiliate"))
    if re.search(r"合作.{0,12}(?:未确认|未核实|不明)", commercial) or "unverified" in commercial.lower():
        partnership = False
    hook = seed.get("hook") or seed.get("hook_and_action_observation") or ""
    proof = seed.get("action_and_proof") or seed.get("hook_and_action_observation") or ""
    purchase = seed.get("purchase_intent") or ""
    metrics = seed.get("visible_metrics") or {}
    visible = {key: metrics.get(key) for key in METRIC_KEYS}
    source_class = "paid_ad_verified" if paid else ("commerce_video" if partnership else "ad_status_unverified")
    return {
        "id": seed.get("id") or _identity(url).replace(":", "-"),
        "source_url": url,
        "account": seed.get("account") or seed.get("account_or_brand"),
        "market": seed.get("target_market") or seed.get("market_evidence") or None,
        "observed_at": seed.get("observed_at"),
        "posted_at": seed.get("posted_date"),
        "product_match_evidence": evidence,
        "product_match_level": "same_product_family" if same_family else "different_or_unverified_product",
        "sku_verification_status": "unverified" if same_family else "not_applicable",
        "source_classification": source_class,
        "use_classification": "same_sku_material_candidate" if same_family and partnership else "ad_structure_reference",
        "hook": hook,
        "demo_or_proof": proof,
        "purchase_intent_evidence": purchase if purchase and "未核实" not in purchase and "未确认" not in purchase else None,
        "commercial_intent_evidence": commercial if partnership or paid else None,
        "visible_metrics": visible,
        "metric_missing_reasons": {key: "not visible in saved source evidence" for key in METRIC_KEYS if visible[key] is None},
        "metrics_evidence_file": metrics.get("evidence_file"),
        "popularity_evidence": None,
        "popularity_reason": "account baseline and current growth not verified; engagement is not sales evidence",
        "material_eligibility": bool(same_family and partnership and proof),
        "material_eligibility_scope": "family-level candidate only; shade/SKU and reuse rights unverified" if same_family else None,
        "structure_reference_eligibility": bool(hook and proof),
        "reuse_rights": seed.get("reuse_rights") or "unverified",
        "retrieval_status": "not_attempted",
        "acquisition_status": "not_attempted",
        "preview_status": "not_attempted",
        "url_resolution_status": "not_attempted",
        "watermark_status": "pending_visual_inspection",
        "prior_watermark_evidence": seed.get("watermark_status"),
        "acquisition_method": None,
        "failure_reason": None,
        "raw_path": None,
        "preview_path": None,
        "thumbnail_path": None,
        "sha256": None,
        "duplicate_content_of": None,
    }


def candidate_from_url(url):
    normalized = normalize_tiktok_url(url)
    match = VIDEO_URL.fullmatch(urlsplit(normalized).path)
    candidate = candidate_from_seed({"id": match.group(1), "source_url": normalized}, "", "")
    candidate["material_eligibility"] = False
    return candidate


def _run(command, timeout):
    if command[0]=='yt-dlp' and importlib.util.find_spec('yt_dlp') is not None:
        command=[sys.executable,'-m','yt_dlp',*command[1:]]
    try:
        result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timed out after {timeout}s: {command[0]}") from exc
    if result.returncode:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError((message[-1] if message else "command failed")[:500])
    return result


def resolve_video(url, yt_dlp="yt-dlp"):
    normalized = normalize_tiktok_url(url)
    result = _run([yt_dlp, "--skip-download", "--dump-single-json", "--no-warnings",
                   "--socket-timeout", "15", normalized], 60)
    data = json.loads(result.stdout)
    if str(data.get("id")) != VIDEO_URL.fullmatch(urlsplit(normalized).path).group(1):
        raise RuntimeError("resolved video ID differs from requested URL")
    return {key: data.get(key) for key in ("id", "title", "description", "uploader", "duration",
                                            "width", "height", "view_count", "like_count",
                                            "comment_count", "save_count", "repost_count", "upload_date")}


def download_video(candidate, output_dir, yt_dlp="yt-dlp"):
    normalized = normalize_tiktok_url(candidate["source_url"])
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = re.sub(r"[^A-Za-z0-9_-]", "-", str(candidate["id"]))
    template = str(output_dir / f"{basename}.%(ext)s")
    _run([yt_dlp, "--no-playlist", "--no-overwrites", "--no-part", "--retries", "2",
          "--fragment-retries", "2", "--socket-timeout", "15", "--no-warnings",
          "-f", "best[format_id!=download]/best", "-o", template, normalized], 240)
    files = [p for p in output_dir.glob(f"{basename}.*") if p.suffix.lower() in {".mp4", ".webm", ".mkv"}]
    if len(files) != 1 or files[0].stat().st_size == 0:
        raise RuntimeError("downloader returned without exactly one nonempty video")
    return files[0]


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_preview(raw, preview, thumbnail):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    preview.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-threads", "2", "-i", str(raw),
               "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=720:-2:force_original_aspect_ratio=decrease,format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
               "-b:a", "128k", "-movflags", "+faststart", str(preview)]
    _run(command, 420)
    if not preview.exists() or preview.stat().st_size == 0:
        raise RuntimeError("preview transcode produced no file")
    _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(preview),
          "-f", "null", "-"], 180)
    _run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", "0.5", "-i", str(preview),
          "-frames:v", "1", str(thumbnail)], 30)
    if not thumbnail.exists() or thumbnail.stat().st_size == 0:
        raise RuntimeError("preview thumbnail failed to decode")


def verify_preview(path):
    """Verify an inherited preview without transcoding or changing it."""
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"existing preview missing or empty: {path}")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    probe = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    video = next((line for line in probe.stderr.splitlines() if "Video:" in line), "")
    audio = next((line for line in probe.stderr.splitlines() if "Audio:" in line), "")
    if "Video: h264" not in video or "yuv420p" not in video or (audio and "Audio: aac" not in audio):
        raise RuntimeError("existing preview is not H.264/AAC/yuv420p compatible")
    head = path.open("rb").read(2 * 1024 * 1024)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    if moov < 0 or mdat < 0 or moov > mdat:
        raise RuntimeError("existing preview is not faststart MP4")
    _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
          "-frames:v", "1", "-f", "null", "-"], 30)
    return {"video_codec": "h264", "pixel_format": "yuv420p", "audio_codec": "aac" if audio else None,
            "faststart": True, "decoded_frame": True}


def _save_report(report, output_dir):
    path = output_dir / "collection.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect(*, seeds, brand, product, output_dir, urls=(), local_sources=None,
            existing_raw=None, existing_preview=None, download=False, resolve_urls=False,
            downloader=None, resolver=None, max_candidates=8):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    local_sources = local_sources or {}
    existing_raw = existing_raw or {}
    existing_preview = existing_preview or {}
    resolver = resolver or resolve_video
    downloader = downloader or download_video
    candidates = unique_candidates([candidate_from_seed(item, brand, product) for item in seeds] +
                                   [candidate_from_url(url) for url in urls])[:max_candidates]
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "product": {"brand": brand, "name": product},
              "discovery": {"source_adapter": "saved_candidate_evidence", "seed_evidence": len(seeds),
                            "url_supplied": len(urls), "selected": len(candidates),
                            "automatic_tiktok_search": "not_implemented"},
              "candidates": candidates}
    _save_report(report, output_dir)
    seen_hashes = {}
    for item in candidates:
        needs_network = resolve_urls or (download and item["id"] not in existing_raw and item["id"] not in local_sources)
        if needs_network and item["source_url"].startswith("https://www.tiktok.com/"):
            try:
                item["resolved_metadata"] = resolver(item["source_url"])
                item["url_resolution_status"] = "resolved"
            except Exception as exc:
                item["url_resolution_status"] = "failed"
                item["resolution_failure_reason"] = str(exc)
        else:
            item["url_resolution_status"] = ("saved_evidence_only" if item["id"] in existing_raw or
                                             item["id"] in local_sources else "not_attempted")
        try:
            raw = None
            if item["id"] in existing_raw:
                raw = Path(existing_raw[item["id"]]).resolve()
                if not raw.is_file() or raw.stat().st_size == 0:
                    raise RuntimeError(f"existing original missing or empty: {raw}")
                item["acquisition_method"] = "existing_original_reference"
            elif item["id"] in local_sources:
                source = Path(local_sources[item["id"]]).resolve()
                if not source.is_file() or source.stat().st_size == 0:
                    raise RuntimeError(f"local source missing or empty: {source}")
                raw_dir = output_dir / "raw"
                raw_dir.mkdir(exist_ok=True)
                raw = raw_dir / f"{item['id']}{source.suffix.lower()}"
                if source != raw:
                    shutil.copy2(source, raw)
                item["acquisition_method"] = "local_import"
            elif download and item["source_url"].startswith("https://www.tiktok.com/"):
                raw = Path(downloader(item, output_dir / "raw"))
                item["acquisition_method"] = "yt_dlp_public_player"
            elif download:
                raise RuntimeError("download adapter unavailable for this source")
            if raw is not None:
                if not raw.is_file() or raw.stat().st_size == 0:
                    raise RuntimeError("acquisition returned a missing or empty file")
                item["raw_path"] = str(raw.resolve())
                item["sha256"] = _hash(raw)
                item["acquisition_status"] = ("downloaded" if item["acquisition_method"] == "yt_dlp_public_player"
                                              else "existing_validated" if item["acquisition_method"] == "existing_original_reference"
                                              else "imported")
                if item["sha256"] in seen_hashes:
                    item["duplicate_content_of"] = seen_hashes[item["sha256"]]
                else:
                    seen_hashes[item["sha256"]] = item["id"]
                if item["id"] in existing_preview:
                    preview = Path(existing_preview[item["id"]]).resolve()
                    item["preview_verification"] = verify_preview(preview)
                    item["preview_path"] = str(preview)
                else:
                    preview = output_dir / "previews" / f"{item['id']}.mp4"
                    thumbnail = output_dir / "previews" / f"{item['id']}.jpg"
                    make_preview(raw, preview, thumbnail)
                    item["preview_verification"] = verify_preview(preview)
                    item["preview_path"] = str(preview.resolve())
                    item["thumbnail_path"] = str(thumbnail.resolve())
                item["retrieval_status"] = ("downloaded" if item["acquisition_method"] == "yt_dlp_public_player"
                                            else "existing_validated" if item["acquisition_method"] == "existing_original_reference"
                                            else "imported")
                item["preview_status"] = "validated"
                item["watermark_status"] = "pending_visual_inspection"
        except Exception as exc:
            item["retrieval_status"] = ("raw_acquired_preview_failed" if item["acquisition_status"] != "not_attempted"
                                        else "failed")
            if item["acquisition_status"] == "not_attempted":
                item["acquisition_status"] = "failed"
                item["failure_stage"] = "acquisition"
            else:
                item["preview_status"] = "failed"
                item["failure_stage"] = "preview"
            item["failure_reason"] = str(exc)
            item["preview_path"] = None
            item["thumbnail_path"] = None
        finally:
            _save_report(report, output_dir)
    return report
