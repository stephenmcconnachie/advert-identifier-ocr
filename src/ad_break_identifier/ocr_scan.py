"""Stage 1: OCR-based advert boundary detection at 1 FPS.

Replaces the VLM-based ``main.py`` approach.  Extracts frames at 1 fps
across the full clip, OCRs each, and regex-matches the extracted text
against each advert's brand/advertiser.  The last frame with a match
becomes the coarse boundary timecode.

Output produces the same XML schema as the VLM stage (``<ad_break>`` /
``<advert>`` / ``<last_timecode>``) for downstream compatibility.
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

from .config import load_metadata_from_file
from .models import AdBreakMetadata, AdvertMetadata, AdvertResult, AdBreakResult
from .ocr_client import ocr_batch, DEFAULT_ENDPOINT, DEFAULT_MODEL


@dataclass
class AdvertScanWindow:
    """Time window for scanning a single advert."""
    advert: AdvertMetadata
    # Frame index range within the 1fps extraction
    start_frame: int
    end_frame: int
    # Match patterns
    patterns: list[re.Pattern]

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

DEFAULT_FPS = 1.0
ADVERT_PADDING = 2.0  # extra seconds before/after the advert window for OCR scan
MAX_OCR_BATCH_SIZE = 500  # max frames per OCR batch (keep memory under control)


# ── Text matching ────────────────────────────────────────────────────────


def build_match_patterns(
    brand: str,
    advertiser: str,
    category: str = "",
) -> list[re.Pattern]:
    """Build a list of compiled regex patterns from advert metadata.

    Generates:
    1. Full brand name (case-insensitive)
    2. Full advertiser name
    3. Individual words from multi-word terms (≥3 chars)
    4. Apostrophe-stripped variants
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

        # Individual words from multi-word terms
        words = term.split()
        if len(words) > 1:
            for word in words:
                if len(word) >= 3 and word not in seen:
                    seen.add(word)
                    patterns.append(
                        re.compile(re.escape(word), re.IGNORECASE)
                    )

        # Apostrophe-stripped (McDonald's → McDonalds)
        simplified = term.replace("'", "").replace("’", "")
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

    # Deduplicate (case-insensitive)
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


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "csv=p=0",
            video_path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    result.check_returncode()
    return float(result.stdout.strip())


def extract_1fps_frames(
    video_path: str,
    output_dir: Path,
    duration: float,
) -> list[Path]:
    """Extract frames at 1 FPS from the full video into output_dir.

    Returns sorted list of frame paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%04d.png")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", "fps=1",
        "-vsync", "vfr",
        "-frame_pts", "1",
        "-pix_fmt", "rgb24",
        "-t", f"{duration:.6f}",
        pattern,
    ]

    logger.info("Extracting frames at 1 FPS (duration=%.1fs)", duration)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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


# ── Frame-to-advert matching logic ────────────────────────────────────────


def compute_advert_windows(
    ad_metadata: AdBreakMetadata,
    total_frames: int,
    fps: float = DEFAULT_FPS,
) -> list[AdvertScanWindow]:
    """Compute scan windows for each advert within the clip.

    Each advert's window starts at the previous advert's estimated end
    (or clip start for the first) and ends at the current advert's
    estimated end.  This enforces ordering and handles back-to-back
    adverts.

    For adverts with no metadata timecode, the window is spread evenly
    across the total duration.
    """
    windows: list[AdvertScanWindow] = []
    total_duration = total_frames / fps

    # Compute cumulative durations
    advert_durations: list[float] = []
    for adv in ad_metadata.adverts:
        dur = adv.duration_seconds if adv.duration_seconds else 30
        advert_durations.append(float(dur))

    total_advert_duration = sum(advert_durations)

    # If there's no free space, start at frame 0
    current_frame = 0

    for i, adv in enumerate(ad_metadata.adverts):
        dur = advert_durations[i]
        end_sec = current_frame / fps + dur + ADVERT_PADDING

        # Clamp to total frames
        end_frame = min(int(end_sec * fps), total_frames - 1)
        if end_frame <= current_frame:
            end_frame = current_frame + 1

        patterns = build_match_patterns(
            brand=adv.brand,
            advertiser=adv.advertiser,
            category=adv.category,
        )

        windows.append(AdvertScanWindow(
            advert=adv,
            start_frame=current_frame,
            end_frame=end_frame,
            patterns=patterns,
        ))

        # Next advert starts where this one ends (minus padding overlap)
        current_frame = max(current_frame, end_frame - int(ADVERT_PADDING * fps))

    return windows


def scan_advert_window(
    window: AdvertScanWindow,
    ocr_results: list[dict],
) -> dict:
    """Scan OCR results for the last frame matching this advert's patterns.

    Returns a dict with keys:
      - matched: bool
      - last_match_frame: int or None (frame index into full 1fps sequence)
      - last_match_seconds: float or None (clip-relative seconds)
      - match_count: int
      - all_matching_frames: list[int]
      - matched_terms: list[str]
      - fallback: bool (True if no match found)
    """
    all_matches: list[int] = []
    matched_terms_set: set[str] = set()

    for ocr_res in ocr_results:
        idx = ocr_res["frame_index"]
        if idx < window.start_frame or idx > window.end_frame:
            continue

        text = ocr_res.get("text", "")
        matched, terms = match_ocr_text(text, window.patterns)

        if matched:
            all_matches.append(idx)
            matched_terms_set.update(t.lower() for t in terms)

    fallback = len(all_matches) == 0
    last_match_frame = all_matches[-1] if all_matches else None
    last_match_seconds = last_match_frame / 1.0 if last_match_frame is not None else None

    return {
        "matched": not fallback,
        "last_match_frame": last_match_frame,
        "last_match_seconds": last_match_seconds,
        "match_count": len(all_matches),
        "all_matching_frames": all_matches,
        "matched_terms": list(matched_terms_set),
        "fallback": fallback,
    }


# ── XML output ────────────────────────────────────────────────────────────


def _escape_xml(text: str) -> str:
    return html.escape(text, quote=True)


def format_xml(
    ad_metadata: AdBreakMetadata,
    scan_results: list[dict],
    fps: float = DEFAULT_FPS,
) -> str:
    """Format OCR results as XML in the same schema as main.py.

    Each ``<advert>`` contains ``<unique_id>``, ``<brand>``,
    ``<advertiser>``, ``<category>``, ``<duration_seconds>``,
    ``<last_timecode>`` (clip-relative), and ``<ocr_match_fallback>``
    if the OCR found no match for this advert.
    """
    lines = ["<ad_break>"]
    lines.append(f"    <!-- OCR-based detection (1 FPS) -->")
    lines.append(f"    <!-- Generated: {datetime.now().isoformat()} -->")

    for i, adv in enumerate(ad_metadata.adverts):
        scan = scan_results[i] if i < len(scan_results) else {"fallback": True, "last_match_seconds": None}

        lines.append("    <advert>")
        lines.append(f"        <unique_id>{_escape_xml(adv.unique_id)}</unique_id>")
        lines.append(f"        <brand>{_escape_xml(adv.brand)}</brand>")
        lines.append(f"        <advertiser>{_escape_xml(adv.advertiser)}</advertiser>")
        lines.append(f"        <category>{_escape_xml(adv.category)}</category>")
        if adv.duration_seconds:
            lines.append(f"        <duration_seconds>{adv.duration_seconds}</duration_seconds>")

        if scan.get("last_match_seconds") is not None:
            tc = seconds_to_timecode(scan["last_match_seconds"])
            lines.append(f"        <last_timecode>{_escape_xml(tc)}</last_timecode>")
        else:
            lines.append(f"        <last_timecode></last_timecode>")

        if scan.get("fallback"):
            lines.append(f"        <ocr_match_fallback>true</ocr_match_fallback>")
            if scan.get("match_count", 0) == 0:
                lines.append(f"        <description>OCR: no text match for {adv.brand}/{adv.advertiser}</description>")

        lines.append("    </advert>")

    lines.append("</ad_break>")
    return "\n".join(lines) + "\n"


# ── Pipeline state update ────────────────────────────────────────────────


def update_pipeline_state(
    metadata_file: str,
    ad_break_index: int,
    scan_results: list[dict],
    ad_metadata: AdBreakMetadata,
) -> None:
    """Update pipeline state JSON with coarse 1 FPS OCR results."""
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
        scan = scan_results[i] if i < len(scan_results) else {"fallback": True, "last_match_seconds": None}
        tc = ""
        secs_clip: float | None = None
        if scan.get("last_match_seconds") is not None:
            secs_clip = scan["last_match_seconds"]
            tc = seconds_to_timecode(secs_clip)

        update_entry: dict[str, Any] = {
            "status": "identified",
            "coarse_1fps": {
                "last_timecode": tc,
                "last_seconds_clip": secs_clip,
            },
        }
        if scan.get("fallback"):
            update_entry["coarse_1fps"]["ocr_match_fallback"] = True

        updates.append(update_entry)

    if updates:
        update_break_adverts(state, ad_break_index, updates)
        write_state(state_path, state)
        logger.info("Pipeline state updated: %s", state_path)


# ── Main orchestration ────────────────────────────────────────────────────


def run_ocr_scan(
    video_path: str,
    metadata: AdBreakMetadata,
    ad_break_index: int,
    metadata_file: str | None = None,
    ocr_endpoint: str = DEFAULT_ENDPOINT,
    ocr_model: str = DEFAULT_MODEL,
    output_dir: Path | None = None,
    verbose: bool = False,
    dry_run: bool = False,
) -> tuple[str, list[dict]]:
    """Run the full OCR scan pipeline.

    Args:
        video_path: Path to local video file.
        metadata: Parsed ad break metadata.
        ad_break_index: 1-based ad break index (for pipeline state).
        metadata_file: Path to metadata JSON (for pipeline state update).
        ocr_endpoint: vLLM OCR endpoint URL.
        ocr_model: OCR model name.
        output_dir: Directory for frames and results.
        verbose: Enable verbose logging.
        dry_run: If True, skip OCR API calls (useful for testing).

    Returns:
        Tuple of (xml_string, scan_results).
    """
    _log = logger.info if verbose else logger.debug

    # 1. Get video duration
    _log("Getting video duration...")
    duration = get_video_duration(video_path)
    total_frames_1fps = int(duration)
    _log("Video duration: %.1fs = %d frames at 1 FPS", duration, total_frames_1fps)

    # 2. Compute advert scan windows
    _log("Computing advert scan windows...")
    windows = compute_advert_windows(metadata, total_frames_1fps)
    for w in windows:
        _log(
            "  %s (%s/%s): frames [%d, %d]",
            w.advert.unique_id, w.advert.brand, w.advert.advertiser,
            w.start_frame, w.end_frame,
        )

    # 3. Extract frames at 1 FPS
    if output_dir:
        frames_dir = output_dir / "frames_1fps"
    else:
        frames_dir = Path(tempfile.mkdtemp(suffix="_ocr_frames"))

    _log("Extracting frames at 1 FPS to %s...", frames_dir)
    frame_paths = extract_1fps_frames(video_path, frames_dir, duration)
    _log("Extracted %d frames", len(frame_paths))

    # 4. OCR all frames
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

    # 5. Scan each advert's window for text matches
    scan_results: list[dict] = []
    for window in windows:
        _log("Scanning %s (%s)...", window.advert.unique_id, window.advert.brand)
        result = scan_advert_window(window, ocr_results)
        scan_results.append(result)
        if result["fallback"]:
            _log(
                "  -> NO MATCH (fallback) for %s/%s",
                window.advert.brand, window.advert.advertiser,
            )
        else:
            _log(
                "  -> Last match at frame %d (tc=%s)",
                result["last_match_frame"],
                seconds_to_timecode(result["last_match_seconds"]),
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
        )

    return xml_output, scan_results


# ── CLI ────────────────────────────────────────────────────────────────────


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OCR-based 1 FPS advert boundary detection",
    )

    parser.add_argument(
        "-v", "--video-url",
        required=True,
        help="URL to video with timecode overlay",
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
        help="Directory for frame images and debug output",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output XML path (default: beside video)",
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
        from .config import parse_cli_metadata
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

    # Download video
    logger.info("Downloading video: %s", parsed.video_url)
    local_video = download_video_to_temp(parsed.video_url)

    # Output directory
    output_dir: Path | None = None
    if parsed.output_dir:
        output_dir = Path(parsed.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        xml_output, _ = run_ocr_scan(
            video_path=local_video,
            metadata=metadata,
            ad_break_index=ad_break_index,
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
            xml_path = "ocr_scan_output.xml"

        with open(xml_path, "w") as f:
            f.write(xml_output)
        logger.info("XML output written to: %s", xml_path)
        print(xml_output)

    finally:
        if os.path.exists(local_video):
            os.unlink(local_video)

    return 0
