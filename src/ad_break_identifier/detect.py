"""OCR-based advert boundary detection at 5 FPS.

Extracts frames from the original broadcast video at 5 FPS around each
ad break, runs PaddleOCR-VL on every frame, stores the OCR results in a
queryable JSON file, then searches for each advert's brand/advertiser/
category using a two-tier matching strategy with ordering enforcement.

Two-tier matching:
    Tier 1 - exact word match: regex with word boundaries (\\bgalaxy\\b)
    Tier 2 - substring match:  unbounded regex (galaxy) to catch
             concatenated forms like "galaxychocolate.com"

Ordering enforcement:
    Each advert's last matching frame must be after the previous
    advert's last matching frame.  The search range for advert N is
    (prev_last_frame, end].

Output produces the same XML schema as the legacy VLM stage
(``<ad_break>`` / ``<advert>`` / ``<last_timecode>``) for downstream
compatibility with ``single_advert_clip``.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_metadata_from_file, parse_cli_metadata
from .models import AdBreakMetadata, AdvertMetadata
from .ocr_client import ocr_batch, DEFAULT_ENDPOINT, DEFAULT_MODEL
from .ocr_client import DEFAULT_OCR_PROMPT

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

DEFAULT_FPS = 5.0
DEFAULT_BEFORE_SECS = 10.0
DEFAULT_AFTER_SECS = 360.0


# ── Text matching ────────────────────────────────────────────────────────


def build_exact_patterns(
    brand: str,
    advertiser: str,
    category: str = "",
) -> list[re.Pattern]:
    """Build word-boundary regex patterns for exact matching.

    Generates case-insensitive patterns with \\b word boundaries for
    brand, advertiser, category, and individual words from multi-word
    terms.  Apostrophe-stripped variants are also included.
    """
    patterns: list[re.Pattern] = []
    seen: set[str] = set()

    for term in (brand, advertiser, category):
        term = term.strip()
        if not term or term in seen:
            continue
        seen.add(term)

        escaped = re.escape(term)
        patterns.append(re.compile(rf"\b{escaped}\b", re.IGNORECASE))

        words = term.split()
        if len(words) > 1:
            for word in words:
                if len(word) >= 3 and word not in seen:
                    seen.add(word)
                    patterns.append(
                        re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
                    )

        simplified = term.replace("'", "").replace("\u2019", "")
        if simplified != term and simplified not in seen:
            seen.add(simplified)
            patterns.append(re.compile(rf"\b{re.escape(simplified)}\b", re.IGNORECASE))

    return patterns


def build_substring_patterns(
    brand: str,
    advertiser: str,
    category: str = "",
) -> list[re.Pattern]:
    """Build unbounded regex patterns for fuzzy substring matching.

    Used as Tier 2 when exact word matching finds nothing.  Catches
    concatenated forms like "galaxychocolate.com".
    """
    patterns: list[re.Pattern] = []
    seen: set[str] = set()

    for term in (brand, advertiser, category):
        term = term.strip()
        if not term or term in seen:
            continue
        seen.add(term)

        escaped = re.escape(term)
        patterns.append(re.compile(escaped, re.IGNORECASE))

        words = term.split()
        if len(words) > 1:
            for word in words:
                if len(word) >= 3 and word not in seen:
                    seen.add(word)
                    patterns.append(re.compile(re.escape(word), re.IGNORECASE))

        simplified = term.replace("'", "").replace("\u2019", "")
        if simplified != term and simplified not in seen:
            seen.add(simplified)
            patterns.append(re.compile(re.escape(simplified), re.IGNORECASE))

    return patterns


def match_ocr_text(
    ocr_text: str,
    patterns: list[re.Pattern],
) -> tuple[bool, list[str]]:
    """Check OCR text against match patterns.

    Returns (matched, matched_terms).
    """
    if not ocr_text or not ocr_text.strip():
        return False, []

    matched_terms: list[str] = []
    for pat in patterns:
        m = pat.search(ocr_text)
        if m:
            matched_terms.append(m.group(0))

    seen: set[str] = set()
    unique: list[str] = []
    for t in matched_terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return len(unique) > 0, unique


# ── Video helpers ────────────────────────────────────────────────────────


def download_video_to_temp(video_url: str, max_retries: int = 3) -> str:
    """Download video from URL to temporary file using curl."""
    for attempt in range(max_retries):
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            logger.info(
                "Downloading video (attempt %d/%d): %s",
                attempt + 1, max_retries, video_url,
            )
            subprocess.run(
                ["curl", "-L", "-o", temp_path, "--fail", "--silent",
                 "--show-error", video_url],
                capture_output=True, text=True, check=True,
            )
            logger.info(
                "Downloaded %d bytes to %s",
                Path(temp_path).stat().st_size, temp_path,
            )
            return temp_path

        except subprocess.CalledProcessError as e:
            logger.warning(
                "Download attempt %d failed: %s",
                attempt + 1, e.stderr.strip(),
            )
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Failed to download video after {max_retries} attempts")


def extract_5fps_frames(
    video_path: str,
    start_seconds: float,
    duration: float,
    output_dir: Path,
    fps: float = DEFAULT_FPS,
) -> list[Path]:
    """Extract frames at the given FPS from a time range of the video.

    Args:
        video_path: Path to local video file.
        start_seconds: Start offset in the video (seconds).
        duration: Duration to extract (seconds).
        output_dir: Directory for frame PNGs.
        fps: Frame extraction rate (default 5.0).

    Returns sorted list of frame paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%05d.png")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_seconds:.6f}",
        "-i", video_path,
        "-t", f"{duration:.6f}",
        "-vf", f"fps={fps}",
        "-vsync", "vfr",
        "-frame_pts", "1",
        "-pix_fmt", "rgb24",
        pattern,
    ]

    logger.info(
        "Extracting frames at %g FPS: start=%.3fs, duration=%.3fs",
        fps, start_seconds, duration,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error("FFmpeg stderr:\n%s", result.stderr)
        raise RuntimeError(
            f"FFmpeg frame extraction failed: {result.stderr[-500:]}"
        )

    frames = sorted(output_dir.glob("frame_*.png"))
    logger.info("Extracted %d frame(s)", len(frames))
    return frames


def seconds_to_timecode(total_seconds: float) -> str:
    """Convert seconds to MM:SS.mmm timecode."""
    minutes = int(total_seconds // 60)
    secs = total_seconds % 60
    return f"{minutes:02d}:{secs:06.3f}"


def timecode_to_seconds(tc: str) -> float:
    """Convert MM:SS or HH:MM:SS timecode to seconds."""
    parts = tc.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Invalid timecode: {tc}")


def tod_to_seconds(tod: str) -> float:
    """Convert HH:MM:SS time-of-day to seconds since midnight."""
    parts = tod.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    raise ValueError(f"Invalid time-of-day format: {tod}")


# ── OCR results storage ──────────────────────────────────────────────────


def save_ocr_results(
    ocr_results: list[dict],
    output_path: Path,
    video_url: str,
    fps: float,
    start_seconds: float,
) -> None:
    """Save OCR results to a queryable JSON file.

    Each entry contains: frame_index, frame_name, timestamp (clip-relative
    seconds), timestamp_broadcast (broadcast-absolute seconds), text, error.
    """
    data = {
        "video_url": video_url,
        "fps": fps,
        "start_seconds": start_seconds,
        "frame_count": len(ocr_results),
        "frames": [],
    }

    for res in ocr_results:
        idx = res["frame_index"]
        clip_ts = idx / fps
        broadcast_ts = start_seconds + clip_ts
        data["frames"].append({
            "frame_index": idx,
            "frame_name": res.get("frame_name", ""),
            "timestamp_clip": round(clip_ts, 3),
            "timestamp_broadcast": round(broadcast_ts, 3),
            "text": res.get("text", ""),
            "error": res.get("error"),
        })

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("OCR results saved to: %s", output_path)


# ── Brand search with ordering enforcement ───────────────────────────────


@dataclass
class BrandSearchResult:
    """Result of searching for one advert's brand in OCR frames."""
    matched: bool
    last_match_frame: int | None
    last_match_seconds: float | None
    match_tier: str  # "exact", "substring", or "fallback"
    match_count: int
    all_matching_frames: list[int]
    matched_terms: list[str]


def search_with_ordering(
    ocr_results: list[dict],
    adverts: list[AdvertMetadata],
    fps: float = DEFAULT_FPS,
) -> list[BrandSearchResult]:
    """Search OCR results for each advert's brand with ordering enforcement.

    For each advert in order:
        1. Try exact word-boundary patterns (Tier 1)
        2. If no match, try substring patterns (Tier 2)
        3. Enforce that this advert's last frame > previous advert's last frame

    Args:
        ocr_results: List of OCR result dicts with frame_index and text.
        adverts: List of advert metadata in break order.
        fps: Frame extraction rate for converting frame index to seconds.

    Returns:
        List of BrandSearchResult, one per advert.
    """
    results: list[BrandSearchResult] = []
    prev_last_frame = -1  # Must be after previous; -1 means any frame >= 0

    for adv in adverts:
        exact_patterns = build_exact_patterns(
            brand=adv.brand,
            advertiser=adv.advertiser,
            category=adv.category,
        )
        substring_patterns = build_substring_patterns(
            brand=adv.brand,
            advertiser=adv.advertiser,
            category=adv.category,
        )

        # Tier 1: exact word match
        exact_matches: list[int] = []
        exact_terms: set[str] = set()

        for ocr_res in ocr_results:
            idx = ocr_res["frame_index"]
            if idx <= prev_last_frame:
                continue
            text = ocr_res.get("text", "")
            matched, terms = match_ocr_text(text, exact_patterns)
            if matched:
                exact_matches.append(idx)
                exact_terms.update(t.lower() for t in terms)

        if exact_matches:
            last_frame = exact_matches[-1]
            result = BrandSearchResult(
                matched=True,
                last_match_frame=last_frame,
                last_match_seconds=last_frame / fps,
                match_tier="exact",
                match_count=len(exact_matches),
                all_matching_frames=exact_matches,
                matched_terms=list(exact_terms),
            )
            results.append(result)
            prev_last_frame = last_frame
            logger.info(
                "  %s (%s): exact match at frame %d (tc=%s), %d frames matched",
                adv.unique_id, adv.brand,
                last_frame, seconds_to_timecode(last_frame / fps),
                len(exact_matches),
            )
            continue

        # Tier 2: substring match
        sub_matches: list[int] = []
        sub_terms: set[str] = set()

        for ocr_res in ocr_results:
            idx = ocr_res["frame_index"]
            if idx <= prev_last_frame:
                continue
            text = ocr_res.get("text", "")
            matched, terms = match_ocr_text(text, substring_patterns)
            if matched:
                sub_matches.append(idx)
                sub_terms.update(t.lower() for t in terms)

        if sub_matches:
            last_frame = sub_matches[-1]
            result = BrandSearchResult(
                matched=True,
                last_match_frame=last_frame,
                last_match_seconds=last_frame / fps,
                match_tier="substring",
                match_count=len(sub_matches),
                all_matching_frames=sub_matches,
                matched_terms=list(sub_terms),
            )
            results.append(result)
            prev_last_frame = last_frame
            logger.info(
                "  %s (%s): substring match at frame %d (tc=%s), %d frames matched",
                adv.unique_id, adv.brand,
                last_frame, seconds_to_timecode(last_frame / fps),
                len(sub_matches),
            )
            continue

        # No match at all — fallback
        result = BrandSearchResult(
            matched=False,
            last_match_frame=None,
            last_match_seconds=None,
            match_tier="fallback",
            match_count=0,
            all_matching_frames=[],
            matched_terms=[],
        )
        results.append(result)
        logger.warning(
            "  %s (%s): NO MATCH (fallback)",
            adv.unique_id, adv.brand,
        )

    return results


# ── XML output ────────────────────────────────────────────────────────────


def _escape_xml(text: str) -> str:
    return html.escape(text, quote=True)


def format_xml(
    ad_metadata: AdBreakMetadata,
    scan_results: list[BrandSearchResult],
) -> str:
    """Format detection results as XML.

    Each ``<advert>`` contains ``<unique_id>``, ``<brand>``,
    ``<advertiser>``, ``<category>``, ``<duration_seconds>``,
    ``<last_timecode>`` (clip-relative), ``<match_tier>``, and
    ``<ocr_match_fallback>`` if no match was found.
    """
    lines = ["<ad_break>"]
    lines.append(f"    <!-- OCR-based detection (5 FPS, PaddleOCR-VL) -->")
    lines.append(f"    <!-- Generated: {datetime.now().isoformat()} -->")

    for i, adv in enumerate(ad_metadata.adverts):
        scan = scan_results[i] if i < len(scan_results) else None

        lines.append("    <advert>")
        lines.append(f"        <unique_id>{_escape_xml(adv.unique_id)}</unique_id>")
        lines.append(f"        <brand>{_escape_xml(adv.brand)}</brand>")
        lines.append(f"        <advertiser>{_escape_xml(adv.advertiser)}</advertiser>")
        lines.append(f"        <category>{_escape_xml(adv.category)}</category>")
        if adv.duration_seconds:
            lines.append(f"        <duration_seconds>{adv.duration_seconds}</duration_seconds>")

        if scan and scan.last_match_seconds is not None:
            tc = seconds_to_timecode(scan.last_match_seconds)
            lines.append(f"        <last_timecode>{_escape_xml(tc)}</last_timecode>")
            lines.append(f"        <match_tier>{scan.match_tier}</match_tier>")
            if scan.matched_terms:
                lines.append(f"        <matched_terms>{_escape_xml(', '.join(scan.matched_terms))}</matched_terms>")
        else:
            lines.append(f"        <last_timecode></last_timecode>")

        if scan and not scan.matched:
            lines.append(f"        <ocr_match_fallback>true</ocr_match_fallback>")
            lines.append(f"        <description>OCR: no text match for {adv.brand}/{adv.advertiser}</description>")

        lines.append("    </advert>")

    lines.append("</ad_break>")
    return "\n".join(lines) + "\n"


# ── Pipeline state update ────────────────────────────────────────────────


def update_pipeline_state(
    metadata_file: str,
    ad_break_index: int,
    scan_results: list[BrandSearchResult],
    ad_metadata: AdBreakMetadata,
    fps: float,
) -> None:
    """Update pipeline state JSON with detection results."""
    try:
        from .pipeline_state import (
            derive_state_path, read_state, write_state, update_break_adverts,
        )
    except ImportError:
        logger.warning("Could not import pipeline_state module")
        return

    state_path = derive_state_path(metadata_file)
    if not Path(state_path).exists():
        logger.warning("Pipeline state file not found: %s", state_path)
        return

    state = read_state(state_path)
    updates: list[dict] = []

    for i, adv in enumerate(ad_metadata.adverts):
        scan = scan_results[i] if i < len(scan_results) else None
        tc = ""
        secs_clip: float | None = None
        last_frame: int | None = None
        match_tier = "fallback"
        matched_terms: list[str] = []

        if scan and scan.last_match_seconds is not None:
            secs_clip = scan.last_match_seconds
            tc = seconds_to_timecode(secs_clip)
            last_frame = scan.last_match_frame
            match_tier = scan.match_tier
            matched_terms = scan.matched_terms

        detection: dict[str, Any] = {
            "last_timecode": tc,
            "last_seconds_clip": secs_clip,
            "last_frame": last_frame,
            "match_tier": match_tier,
            "matched_terms": matched_terms,
        }

        update_entry: dict[str, Any] = {
            "status": "detected",
            "detection": detection,
        }

        updates.append(update_entry)

    if updates:
        update_break_adverts(state, ad_break_index, updates, fps)
        write_state(state_path, state)
        logger.info("Pipeline state updated: %s", state_path)


# ── Main orchestration ────────────────────────────────────────────────────


def run_detection(
    video_path: str,
    metadata: AdBreakMetadata,
    ad_break_index: int,
    break_start_secs: float,
    before_secs: float = DEFAULT_BEFORE_SECS,
    after_secs: float = DEFAULT_AFTER_SECS,
    fps: float = DEFAULT_FPS,
    metadata_file: str | None = None,
    ocr_endpoint: str = DEFAULT_ENDPOINT,
    ocr_model: str = DEFAULT_MODEL,
    output_dir: Path | None = None,
    verbose: bool = False,
    dry_run: bool = False,
) -> tuple[str, list[BrandSearchResult]]:
    """Run the full OCR detection pipeline.

    Args:
        video_path: Path to local video file.
        metadata: Parsed ad break metadata.
        ad_break_index: 1-based ad break index (for pipeline state).
        break_start_secs: Ad break start time in seconds (broadcast-absolute).
        before_secs: Seconds before break start to begin extraction.
        after_secs: Seconds after break start to end extraction.
        fps: Frame extraction rate.
        metadata_file: Path to metadata JSON (for pipeline state update).
        ocr_endpoint: vLLM OCR endpoint URL.
        ocr_model: OCR model name.
        output_dir: Directory for frames and OCR results.
        verbose: Enable verbose logging.
        dry_run: If True, skip OCR API calls.

    Returns:
        Tuple of (xml_string, scan_results).
    """
    _log = logger.info if verbose else logger.debug

    # 1. Compute extraction range
    start_seconds = max(0.0, break_start_secs - before_secs)
    duration = before_secs + after_secs
    _log("Extraction range: start=%.3fs, duration=%.3fs", start_seconds, duration)

    # 2. Extract frames
    if output_dir:
        frames_dir = output_dir / "frames"
    else:
        frames_dir = Path(tempfile.mkdtemp(suffix="_detect_frames"))

    _log("Extracting frames at %g FPS to %s...", fps, frames_dir)
    frame_paths = extract_5fps_frames(
        video_path=video_path,
        start_seconds=start_seconds,
        duration=duration,
        output_dir=frames_dir,
        fps=fps,
    )
    _log("Extracted %d frames", len(frame_paths))

    if not frame_paths:
        raise RuntimeError("No frames extracted from video")

    # 3. OCR all frames
    if dry_run:
        _log("DRY RUN: Skipping OCR API calls, using empty text")
        ocr_results = [
            {"frame_index": i, "frame_name": p.name, "path": str(p),
             "text": "", "error": None}
            for i, p in enumerate(frame_paths)
        ]
    else:
        _log("Running OCR on %d frames...", len(frame_paths))
        ocr_results = ocr_batch(
            image_paths=frame_paths,
            endpoint=ocr_endpoint,
            model=ocr_model,
            progress_callback=lambda c, t: _log("OCR %d/%d", c, t)
            if verbose else None,
        )

    # 4. Save OCR results to JSON
    if output_dir:
        video_stem = Path(video_path).stem
        ocr_json_path = output_dir / f"{video_stem}_ocr.json"
    else:
        ocr_json_path = Path(tempfile.mktemp(suffix="_ocr.json"))

    save_ocr_results(
        ocr_results=ocr_results,
        output_path=ocr_json_path,
        video_url=video_path,
        fps=fps,
        start_seconds=start_seconds,
    )
    _log("OCR results saved to: %s", ocr_json_path)

    # 5. Search for each advert's brand with ordering enforcement
    _log("Searching for brand matches with ordering enforcement...")
    scan_results = search_with_ordering(
        ocr_results=ocr_results,
        adverts=metadata.adverts,
        fps=fps,
    )

    # 6. Format XML
    xml_output = format_xml(metadata, scan_results)
    _log("XML output: %d lines", len(xml_output.splitlines()))

    # 7. Update pipeline state
    if metadata_file and not dry_run:
        update_pipeline_state(
            metadata_file=metadata_file,
            ad_break_index=ad_break_index,
            scan_results=scan_results,
            ad_metadata=metadata,
            fps=fps,
        )

    # 8. Clean up frames (keep OCR JSON)
    if not output_dir:
        for f in frames_dir.glob("*.png"):
            f.unlink()
        try:
            frames_dir.rmdir()
        except OSError:
            pass

    return xml_output, scan_results


# ── CLI ────────────────────────────────────────────────────────────────────


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OCR-based advert boundary detection (5 FPS, PaddleOCR-VL)",
    )

    parser.add_argument(
        "-v", "--video-url",
        required=True,
        help="URL to the original broadcast video",
    )
    parser.add_argument(
        "--metadata-file",
        type=str,
        help="JSON file with complete ad break metadata",
    )
    parser.add_argument(
        "--ad-break-index",
        type=int,
        default=None,
        help="Index of ad break (1-based, auto-detected from filename)",
    )
    parser.add_argument(
        "--prog-before",
        type=str, metavar="TITLE,CHANNEL",
        help="Programme before ad break: 'Lorraine,ITV1'",
    )
    parser.add_argument(
        "--prog-after",
        type=str, metavar="TITLE,CHANNEL",
        help="Programme after ad break: 'Daybreak,ITV1'",
    )
    parser.add_argument(
        "--advert",
        type=str, action="append",
        metavar="ID|ADVERTISER|BRAND|CATEGORY|DURATION",
        help="Advert (can specify multiple)",
    )
    parser.add_argument(
        "--before-secs",
        type=float,
        default=DEFAULT_BEFORE_SECS,
        help=f"Seconds before ad break start (default: {DEFAULT_BEFORE_SECS})",
    )
    parser.add_argument(
        "--after-secs",
        type=float,
        default=DEFAULT_AFTER_SECS,
        help=f"Seconds after ad break start (default: {DEFAULT_AFTER_SECS})",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help=f"Frame extraction rate (default: {DEFAULT_FPS})",
    )
    parser.add_argument(
        "--ocr-endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"vLLM OCR endpoint (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--ocr-model",
        default=DEFAULT_MODEL,
        help=f"OCR model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory for frame images and OCR results JSON",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output XML path (default: beside metadata file)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed progress",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip OCR API calls (for testing frame extraction only)",
    )
    return parser


def main(args: list[str] | None = None) -> int:
    parser = create_parser()
    parsed = parser.parse_args(args)

    logging.basicConfig(
        level=logging.DEBUG if parsed.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # Auto-detect ad break index from filename
    ad_break_index = parsed.ad_break_index
    if ad_break_index is None:
        video_path = Path(parsed.video_url.split("?")[0])
        m = re.search(r'(\d{2})of(\d{2})\.mp4$', video_path.name, re.IGNORECASE)
        ad_break_index = int(m.group(1)) if m else 1

    # Load metadata
    metadata: AdBreakMetadata | None = None
    if parsed.metadata_file:
        metadata = load_metadata_from_file(parsed.metadata_file, ad_break_index)
    elif parsed.prog_before and parsed.prog_after and parsed.advert:
        metadata = parse_cli_metadata(
            parsed.prog_before, parsed.prog_after, parsed.advert,
        )

    if not metadata or not metadata.adverts:
        logger.error(
            "No advert metadata provided. Use --metadata-file or "
            "--prog-before/--prog-after/--advert"
        )
        return 1

    logger.info(
        "Loaded %d advert(s): %s",
        len(metadata.adverts),
        ", ".join(f"{a.brand}/{a.advertiser}" for a in metadata.adverts),
    )

    # Compute break start time from metadata file
    break_start_secs = 0.0
    if parsed.metadata_file:
        with open(parsed.metadata_file) as f:
            meta_data = json.load(f)
        if "ad_breaks" in meta_data:
            breaks = meta_data["ad_breaks"]
            if ad_break_index >= 1 and ad_break_index <= len(breaks):
                break_start_time = breaks[ad_break_index - 1].get("start_time", "")
                if break_start_time:
                    break_start_secs = tod_to_seconds(break_start_time)
        video_start_time = meta_data.get("video_info", {}).get("start_time", "")
        if video_start_time:
            video_start_secs = tod_to_seconds(video_start_time)
            # break_start_secs is time-of-day; convert to video-relative offset
            # Actually we need the video-relative offset for FFmpeg -ss
            # But the video URL points to the original broadcast video,
            # so we use the broadcast-absolute time directly if the video
            # starts at 00:00:00, or compute the offset.
            # The metadata start_time is the video's start time-of-day.
            # FFmpeg -ss is relative to video start, so:
            break_start_secs = break_start_secs - video_start_secs
            logger.info(
                "Break start: %s (video-relative: %.3fs)",
                break_start_time, break_start_secs,
            )
    else:
        # Without metadata file, assume video starts at 0 and use ad break
        # index to estimate. This is a fallback for CLI-only metadata.
        break_start_secs = 0.0

    # Download video
    logger.info("Downloading video: %s", parsed.video_url)
    local_video = download_video_to_temp(parsed.video_url)

    # Output directory
    output_dir: Path | None = None
    if parsed.output_dir:
        output_dir = Path(parsed.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        xml_output, _ = run_detection(
            video_path=local_video,
            metadata=metadata,
            ad_break_index=ad_break_index,
            break_start_secs=break_start_secs,
            before_secs=parsed.before_secs,
            after_secs=parsed.after_secs,
            fps=parsed.fps,
            metadata_file=parsed.metadata_file,
            ocr_endpoint=parsed.ocr_endpoint,
            ocr_model=parsed.ocr_model,
            output_dir=output_dir,
            verbose=parsed.verbose,
            dry_run=parsed.dry_run,
        )

        # Write XML output
        if parsed.output:
            xml_path = parsed.output
        elif parsed.metadata_file:
            base = Path(parsed.metadata_file).parent
            video_stem = Path(parsed.video_url.split("?")[0]).stem
            xml_path = str(base / f"{video_stem}.xml")
        else:
            xml_path = "detect_output.xml"

        with open(xml_path, "w") as f:
            f.write(xml_output)
        logger.info("XML output written to: %s", xml_path)
        print(xml_output)

    finally:
        if os.path.exists(local_video):
            os.unlink(local_video)

    return 0


if __name__ == "__main__":
    sys.exit(main())
