"""Stage 2: OCR-based frame-accurate refinement of advert boundaries.

Replaces the VLM-based ``refinement.py`` approach.  Reads the 1 FPS
OCR scan XML, extracts 3-second clips at 25 FPS around each advert's
coarse boundary, OCRs every frame, and finds the last frame matching
the brand/advertiser text.

Output produces the same refined XML schema as the VLM stage
(``<refined_timecode>``, ``<refined_clip_frame>``) for compatibility.
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
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .config import load_metadata_from_file
from .models import (
    AdBreakResult, AdvertResult,
    RefinedAdvertResult, RefinedAdBreakResult,
)
from .ocr_client import ocr_batch, DEFAULT_ENDPOINT, DEFAULT_MODEL
from .ocr_scan import build_match_patterns, match_ocr_text, seconds_to_timecode

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

REFINE_FPS = 25.0
REFINE_WINDOW = 3.0       # seconds around the boundary (±1.5 s)
CENTER_OFFSET = 0.0       # offset from coarse timecode to center the window
MAX_CLIP_FRAMES = 75      # max frames for 3s at 25 FPS

# ── XML parsing (same as refinement.py) ──────────────────────────────────


def parse_ad_break_xml(
    xml_path: str,
    metadata_json: str | None = None,
) -> AdBreakResult:
    """Parse OCR scan XML, enriched with metadata for advertiser/category.

    Same signature and return type as ``refinement.parse_ad_break_xml``
    for drop-in compatibility.
    """
    metadata_lookup: dict[str, dict] = {}
    if metadata_json:
        with open(metadata_json) as f:
            data = json.load(f)
        for ad_break in data.get("ad_breaks", [data]):
            for advert in ad_break.get("adverts", []):
                uid = advert.get("unique_id", "")
                if uid:
                    metadata_lookup[uid] = advert

    tree = ElementTree.parse(xml_path)
    root = tree.getroot()

    adverts: list[AdvertResult] = []
    for el in root.findall("advert"):
        def _text(tag: str) -> str | None:
            child = el.find(tag)
            return child.text.strip() if child is not None and child.text else None

        uid = _text("unique_id") or ""
        brand = _text("brand") or ""
        tc = _text("last_timecode") or ""
        dur_str = _text("duration_seconds") or ""

        advert = AdvertResult(
            advert_id=uid,
            brand=brand,
            timecode=tc,
            duration_seconds=int(dur_str) if dur_str else None,
        )

        if uid in metadata_lookup:
            meta = metadata_lookup[uid]
            advert.advertiser = meta.get("advertiser", "")
            advert.category = meta.get("category", "")

        adverts.append(advert)

    return AdBreakResult(
        success=True,
        adverts=adverts,
        total_found=len(adverts),
        total_expected=len(adverts),
    )


# ── Video helpers ────────────────────────────────────────────────────────


def download_video_to_temp(video_url: str, max_retries: int = 3) -> str:
    """Download video from URL to temporary file."""
    for attempt in range(max_retries):
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
            logger.info("Downloading video (attempt %d/%d)...", attempt + 1, max_retries)
            subprocess.run(
                ["curl", "-L", "-o", temp_path, "--fail", "--silent",
                 "--show-error", video_url],
                capture_output=True, text=True, check=True,
            )
            logger.info("Downloaded to %s", temp_path)
            return temp_path
        except subprocess.CalledProcessError as e:
            logger.warning("Download failed: %s", e.stderr.strip())
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to download after {max_retries} attempts")


def extract_25fps_frames(
    video_path: str,
    start_seconds: float,
    duration: float,
    output_dir: Path,
) -> list[Path]:
    """Extract frames at 25 FPS for a time window.

    Returns sorted list of frame paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%04d.png")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", f"{start_seconds:.6f}",
        "-t", f"{duration:.6f}",
        "-vf", f"fps={REFINE_FPS}",
        "-vsync", "vfr",
        "-frame_pts", "1",
        "-pix_fmt", "rgb24",
        pattern,
    ]

    logger.info(
        "Extracting frames at 25 FPS: start=%.3fs, duration=%.3fs",
        start_seconds, duration,
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("FFmpeg failed:\n%s", result.stderr)
        raise RuntimeError(f"FFmpeg failed: {result.stderr[-500:]}")

    frames = sorted(output_dir.glob("frame_*.png"))
    logger.info("Extracted %d frame(s)", len(frames))
    return frames


def timecode_to_seconds(tc: str) -> float:
    """Convert MM:SS or HH:MM:SS timecode to seconds."""
    parts = tc.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Invalid timecode: {tc}")


# ── Refinement logic ─────────────────────────────────────────────────────


def refine_single_advert(
    advert: AdvertResult,
    video_path: str,
    fps: float = REFINE_FPS,
    window: float = REFINE_WINDOW,
    ocr_endpoint: str = DEFAULT_ENDPOINT,
    ocr_model: str = DEFAULT_MODEL,
    verbose: bool = False,
    dry_run: bool = False,
) -> tuple[RefinedAdvertResult, str, list[dict]]:
    """Refine a single advert's boundary using 25 FPS OCR analysis.

    Args:
        advert: Advert from 1 FPS detection.
        video_path: Path to local video.
        fps: Frames per second for extraction.
        window: Window duration in seconds centered on coarse tc.
        ocr_endpoint: vLLM OCR endpoint.
        ocr_model: OCR model name.
        verbose: Enable verbose logging.
        dry_run: Skip OCR API calls.

    Returns:
        Tuple of (RefinedAdvertResult, prompt_text, raw_ocr_results).
    """
    _log = logger.info if verbose else logger.debug

    # Build match patterns
    patterns = build_match_patterns(
        brand=advert.brand,
        advertiser=advert.advertiser,
        category=advert.category,
    )

    _log(
        "Refining %s (%s/%s): tc=%s, patterns=%s",
        advert.advert_id, advert.brand, advert.advertiser,
        advert.timecode, [p.pattern for p in patterns],
    )

    # Default fallback result
    result = RefinedAdvertResult(
        original_timecode=advert.timecode,
        refined_timecode=None,
        refined_clip_frame=None,
        refinement_status="fallback",
        description="No OCR match found",
    )

    if not advert.timecode:
        return result, "No timecode from 1 FPS detection", []

    # Compute window around coarse timecode
    try:
        coarse_seconds = timecode_to_seconds(advert.timecode)
    except ValueError as e:
        _log("Invalid timecode: %s", e)
        return result, f"Invalid timecode: {advert.timecode}", []

    window_start = max(coarse_seconds - window / 2, 0.0)

    # Extract frames at 25 FPS
    output_dir = Path(tempfile.mkdtemp(suffix=f"_refine_{advert.advert_id}"))
    try:
        frame_paths = extract_25fps_frames(
            video_path=video_path,
            start_seconds=window_start,
            duration=window,
            output_dir=output_dir,
        )

        if not frame_paths:
            return result, "No frames extracted", []

        # OCR frames
        ocr_results: list[dict] = []
        if dry_run:
            _log("DRY RUN: Skipping OCR")
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
            )

        # Find last matching frame
        last_match_idx: int | None = None
        matched_terms: set[str] = set()

        for i, ocr_res in enumerate(ocr_results):
            text = ocr_res.get("text", "")
            matched, terms = match_ocr_text(text, patterns)
            if matched:
                last_match_idx = i
                matched_terms.update(t.lower() for t in terms)

        if last_match_idx is not None:
            # Compute refined timecode (clip-relative)
            refined_seconds = window_start + (last_match_idx / fps)
            refined_tc = seconds_to_timecode(refined_seconds)

            result.refined_timecode = refined_tc
            result.refined_clip_frame = last_match_idx
            result.refinement_status = "success"
            result.description = f"OCR matched: {', '.join(matched_terms)}"
            _log(
                "  -> Refined: %s → %s (frame %d)",
                advert.timecode, refined_tc, last_match_idx,
            )
        else:
            _log("  -> No OCR match for %s/%s", advert.brand, advert.advertiser)

    finally:
        # Clean up extracted frames
        for f in output_dir.glob("*.png"):
            f.unlink()
        try:
            output_dir.rmdir()
        except OSError:
            pass

    return result, "", ocr_results


# ── Combined refinement pipeline ─────────────────────────────────────────


def refine_advert_timecodes(
    xml_path: str,
    video_url: str,
    output_path: str | None = None,
    metadata_json: str | None = None,
    ocr_endpoint: str = DEFAULT_ENDPOINT,
    ocr_model: str = DEFAULT_MODEL,
    verbose: bool = False,
    dry_run: bool = False,
) -> RefinedAdBreakResult:
    """Refine all advert timecodes from OCR scan XML.

    Args:
        xml_path: Path to 1 FPS OCR scan XML.
        video_url: URL to video.
        output_path: Output refined XML path.
        metadata_json: Path to metadata JSON for advert enrichment.
        ocr_endpoint: vLLM OCR endpoint.
        ocr_model: OCR model name.
        verbose: Enable verbose logging.
        dry_run: Skip OCR API calls.

    Returns:
        RefinedAdBreakResult with per-advert refinement status.
    """
    _log = logger.info if verbose else logger.debug
    _log("Parsing 1 FPS XML: %s", xml_path)
    ad_break_result = parse_ad_break_xml(xml_path, metadata_json)

    if not ad_break_result.adverts:
        return RefinedAdBreakResult(
            success=False,
            error="No adverts found in XML",
        )

    # Download video
    _log("Downloading video: %s", video_url)
    local_video = download_video_to_temp(video_url)

    refined_adverts: list[RefinedAdvertResult] = []
    total_refined = 0
    total_fallback = 0

    try:
        for i, advert in enumerate(ad_break_result.adverts, 1):
            _log(
                "Refining %d/%d: %s (%s)",
                i, len(ad_break_result.adverts),
                advert.brand, advert.advert_id,
            )

            refined, _, _ = refine_single_advert(
                advert=advert,
                video_path=local_video,
                ocr_endpoint=ocr_endpoint,
                ocr_model=ocr_model,
                verbose=verbose,
                dry_run=dry_run,
            )
            refined_adverts.append(refined)

            if refined.refinement_status == "success":
                total_refined += 1
            else:
                total_fallback += 1

    finally:
        if os.path.exists(local_video):
            os.unlink(local_video)

    # Write refined XML
    if output_path is None:
        xml_path_obj = Path(xml_path)
        output_path = str(xml_path_obj.parent / f"{xml_path_obj.stem}_refined.xml")

    _write_refined_xml(
        ad_break_result=ad_break_result,
        refined_adverts=refined_adverts,
        output_path=output_path,
    )
    _log("Refined XML written: %s", output_path)

    # Update pipeline state
    if metadata_json:
        _update_pipeline_state(
            metadata_json=metadata_json,
            xml_path=xml_path,
            refined_adverts=refined_adverts,
        )

    return RefinedAdBreakResult(
        success=True,
        adverts=refined_adverts,
        total_refined=total_refined,
        total_fallback=total_fallback,
    )


# ── XML output ────────────────────────────────────────────────────────────


def _write_refined_xml(
    ad_break_result: AdBreakResult,
    refined_adverts: list[RefinedAdvertResult],
    output_path: str,
) -> None:
    """Write refined XML with the same schema as ``refinement.py``."""
    def esc(text: str) -> str:
        return html.escape(text, quote=True)

    lines = ["<ad_break>"]
    lines.append("    <!-- OCR-based 25 FPS refinement -->")
    lines.append(f"    <!-- Generated: {datetime.now().isoformat()} -->")

    for orig, ref in zip(ad_break_result.adverts, refined_adverts):
        lines.append("    <advert>")
        lines.append(f"        <unique_id>{esc(orig.advert_id)}</unique_id>")
        lines.append(f"        <brand>{esc(orig.brand)}</brand>")
        if orig.advertiser:
            lines.append(f"        <advertiser>{esc(orig.advertiser)}</advertiser>")
        if orig.category:
            lines.append(f"        <category>{esc(orig.category)}</category>")
        if orig.duration_seconds:
            lines.append(f"        <duration_seconds>{orig.duration_seconds}</duration_seconds>")
        lines.append(f"        <last_timecode>{esc(orig.timecode or '')}</last_timecode>")
        if ref.refined_timecode:
            lines.append(f"        <refined_timecode>{esc(ref.refined_timecode)}</refined_timecode>")
        if ref.refined_clip_frame is not None:
            lines.append(f"        <refined_clip_frame>{ref.refined_clip_frame}</refined_clip_frame>")
        lines.append(f"        <refinement_status>{ref.refinement_status}</refinement_status>")
        if ref.description:
            lines.append(f"        <description>{esc(ref.description)}</description>")
        # If OCR didn't match, add fallback element for traceability
        if ref.refinement_status == "fallback" and "No OCR match" in ref.description:
            lines.append("        <ocr_match_fallback>true</ocr_match_fallback>")
        lines.append("    </advert>")

    lines.append("</ad_break>")

    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── Pipeline state update ────────────────────────────────────────────────


def _update_pipeline_state(
    metadata_json: str,
    xml_path: str,
    refined_adverts: list[RefinedAdvertResult],
) -> None:
    """Update pipeline state with refined 25 FPS results."""
    try:
        from .pipeline_state import (
            derive_state_path, read_state, write_state, update_break_adverts,
        )
    except ImportError:
        logger.warning("Could not import pipeline_state")
        return

    # Determine ad_break_index from XML filename
    xml_name = Path(xml_path).name
    m = re.search(r'(\d{2})of\d{2}', xml_name)
    b_idx = int(m.group(1)) if m else 1

    state_path = derive_state_path(metadata_json)
    if not Path(state_path).exists():
        logger.warning("Pipeline state not found: %s", state_path)
        return

    state = read_state(state_path)
    updates: list[dict] = []

    for ref in refined_adverts:
        r25: dict[str, Any] = {
            "last_timecode": ref.refined_timecode or "",
            "clip_frame": ref.refined_clip_frame,
            "refinement_status": ref.refinement_status,
        }
        if ref.refined_timecode:
            try:
                r25["last_seconds_clip"] = timecode_to_seconds(ref.refined_timecode)
            except Exception:
                pass
        updates.append({
            "status": "refined",
            "refined_25fps": r25,
        })

    if updates:
        update_break_adverts(state, b_idx, updates)
        write_state(state_path, state)
        logger.info("Pipeline state updated: %s", state_path)


# ── CLI ────────────────────────────────────────────────────────────────────


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OCR-based 25 FPS frame-accurate refinement",
    )
    parser.add_argument(
        "--xml-file", required=True,
        help="Path to 1 FPS detection XML",
    )
    parser.add_argument(
        "--video-url", required=True,
        help="URL to video",
    )
    parser.add_argument(
        "--json-file",
        help="Original metadata JSON (for advertiser/category lookup)",
    )
    parser.add_argument(
        "--output",
        help="Output refined XML path (default: <xml>_refined.xml)",
    )
    parser.add_argument(
        "--ocr-endpoint", default=DEFAULT_ENDPOINT,
        help=f"vLLM OCR endpoint (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--ocr-model", default=DEFAULT_MODEL,
        help=f"OCR model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed progress",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip OCR API calls",
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

    xml_path = Path(parsed.xml_file)
    if not xml_path.exists():
        logger.error("XML file not found: %s", xml_path)
        return 1

    result = refine_advert_timecodes(
        xml_path=str(xml_path),
        video_url=parsed.video_url,
        output_path=parsed.output,
        metadata_json=parsed.json_file,
        ocr_endpoint=parsed.ocr_endpoint,
        ocr_model=parsed.ocr_model,
        verbose=parsed.verbose,
        dry_run=parsed.dry_run,
    )

    if result.success:
        logger.info(
            "Refinement complete: %d refined, %d fallback",
            result.total_refined, result.total_fallback,
        )
        return 0
    else:
        logger.error("Refinement failed: %s", result.error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
