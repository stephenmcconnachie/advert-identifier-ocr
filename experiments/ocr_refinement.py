#!/usr/bin/env python3
"""OCR-based advert boundary refinement for the advert-identifier pipeline.

EXPERIMENTAL — replaces the VLM-based 1fps → 25fps refinement with an
OCR-based approach:

  Stage 1 (1 FPS coarse scan)
    Extract frames at 1 fps across each advert's known duration window.
    Run OCR on each frame and regex-match the extracted text against the
    advert's brand/advertiser metadata.  The *last* frame with a match
    becomes the coarse boundary.

  Stage 2 (25 FPS refinement)
    Extract frames at 25 fps for a short window (default 3 s) around the
    coarse boundary.  Run OCR on each and find the last matching frame.
    The *last* matching frame index becomes the precise advert boundary.

Usage:

  # From 1 FPS detection XML + video URL
  python experiments/ocr_refinement.py \\
      --xml-file video/2024-03-26_ITV1HD_09:00:00_01of02.xml \\
      --video-base-url http://172.18.7.236:1100/ \\
      --output-dir experiments/ocr_results_001

  # From pipeline state file
  python experiments/ocr_refinement.py \\
      --state-file video/..._pipeline_state.json \\
      --video-base-url http://172.18.7.236:1100/ \\
      --ad-break-index 1 \\
      --output-dir experiments/ocr_results_002

  # Explicit adverts (for quick tests)
  python experiments/ocr_refinement.py \\
      --video-url http://172.18.7.236:1100/dry-run/2024-03-26_ITV1HD_09:00:00/01of02.mp4 \\
      --prog-before "This Morning,ITV1HD" \\
      --prog-after "Loose Women,ITV1HD" \\
      --advert "AD001|Disney|Disneyland|Theme Parks|30" \\
      --output-dir experiments/ocr_results_003

  # Stage 1 only (skip 25 fps refinement)
  python experiments/ocr_refinement.py \\
      --xml-file video/...xml \\
      --video-base-url http://172.18.7.236:1100/ \\
      --stage 1-only \\
      --output-dir experiments/ocr_results_004

  # Custom OCR model
  python experiments/ocr_refinement.py \\
      --xml-file video/...xml \\
      --video-base-url http://172.18.7.236:1100/ \\
      --ocr-endpoint http://localhost:8000/v1/chat/completions \\
      --ocr-model lightonai/LightOnOCR-2-1B \\
      --output-dir experiments/ocr_results_005
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from PIL import Image

# Allow importing sibling modules in experiments/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ocr_api_client import ocr_batch, ocr_image  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("ocr_refinement")


# ── Constants ────────────────────────────────────────────────────────────

# Default OCR endpoint/model (mirrors ocr_api_client defaults)
DEFAULT_OCR_ENDPOINT = "http://localhost:8000/v1/chat/completions"
DEFAULT_OCR_MODEL = "lightonai/LightOnOCR-2-1B"

# Frame extraction defaults
COARSE_FPS = 1.0          # Stage 1 sampling rate
REFINE_FPS = 25.0         # Stage 2 sampling rate
REFINE_WINDOW = 3.0       # Seconds around coarse boundary for refinement
ADVERT_PADDING = 2.0      # Extra seconds before/after the advert window

# Grid rendering
GRID_COLS = 10
CELL_W = 384
CELL_H = 216
LABEL_H = 24
GRID_DPI = 200
GRID_MAX_DIM = 1540


# ── Data models ──────────────────────────────────────────────────────────


@dataclass
class AdvertInfo:
    """Advert metadata from XML or CLI."""
    unique_id: str
    brand: str
    advertiser: str
    category: str
    duration_seconds: int | None = None
    # Timecode from the 1 FPS detection (rough start of advert)
    timecode: str | None = None
    # Refined timecode if available
    refined_timecode: str | None = None


@dataclass
class FrameOCRMatch:
    """OCR result and match status for a single frame."""
    frame_index: int
    frame_name: str
    path: str
    ocr_text: str
    matched: bool
    matched_terms: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class OcrStageResult:
    """Result of one OCR analysis stage (1 FPS or 25 FPS)."""
    stage: str  # "coarse_1fps" or "refined_25fps"
    fps: float
    start_seconds: float
    duration: float
    total_frames: int
    frames: list[FrameOCRMatch] = field(default_factory=list)
    last_matching_frame: int | None = None
    last_matching_timecode: str | None = None
    match_count: int = 0


@dataclass
class AdvertOCRResult:
    """Complete OCR refinement result for one advert."""
    unique_id: str
    brand: str
    advertiser: str
    category: str
    duration_seconds: int | None = None
    coarse: OcrStageResult | None = None
    refined: OcrStageResult | None = None
    output_dir: str | None = None


# ── Text matching ────────────────────────────────────────────────────────


def build_match_patterns(
    brand: str,
    advertiser: str,
    category: str = "",
) -> list[re.Pattern]:
    """Build a list of compiled regex patterns from advert metadata.

    For each non-empty term, generates:
    1. The full term as a case-insensitive regex
    2. A word-boundary version if the term has multiple words

    Also strips common suffixes for fuzzy matching:
    - "Disneyland" → matches "Disney" too (partial word match)
    - "McDonald's" → matches "Mcdonald" (apostrophe-insensitive)

    Args:
        brand: Brand name (e.g. "Disneyland").
        advertiser: Advertiser name (e.g. "Disney").
        category: Ad category (e.g. "Theme Parks").

    Returns:
        List of compiled regex patterns.
    """
    patterns: list[re.Pattern] = []
    seen: set[str] = set()

    raw_terms = [brand, advertiser, category]

    for term in raw_terms:
        term = term.strip()
        if not term or term in seen:
            continue
        seen.add(term)

        # Escape regex special chars (e.g. McDonald's → McDonald\\'s)
        escaped = re.escape(term)

        # Full term match (case-insensitive)
        patterns.append(re.compile(escaped, re.IGNORECASE))

        # Word-boundary match for multi-word terms
        # e.g. "Coca Cola" should match text containing "Coca" or "Cola"
        words = term.split()
        if len(words) > 1:
            for word in words:
                if len(word) >= 3 and word not in seen:
                    seen.add(word)
                    patterns.append(
                        re.compile(re.escape(word), re.IGNORECASE)
                    )

        # Also add a pattern for the term without possessive apostrophes
        # e.g. "McDonald's" → "McDonalds"
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

    Args:
        ocr_text: Text extracted by OCR.
        patterns: Compiled regex patterns from build_match_patterns().

    Returns:
        Tuple of (matched, list_of_matched_terms).
    """
    if not ocr_text or not ocr_text.strip():
        return False, []

    matched_terms: list[str] = []
    for pattern in patterns:
        m = pattern.search(ocr_text)
        if m:
            matched_terms.append(m.group(0))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_terms: list[str] = []
    for term in matched_terms:
        if term.lower() not in seen:
            seen.add(term.lower())
            unique_terms.append(term)

    return len(unique_terms) > 0, unique_terms


# ── Frame extraction (via FFmpeg) ────────────────────────────────────────


def download_video_to_temp(video_url: str, max_retries: int = 3) -> str:
    """Download video from URL to temporary file using curl."""
    for attempt in range(max_retries):
        temp_path: str | None = None
        try:
            temp_fd, temp_path = tempfile.mkstemp(suffix=".mp4")
            os.close(temp_fd)

            logger.info(
                "Downloading video (attempt %d/%d): %s",
                attempt + 1,
                max_retries,
                video_url,
            )

            cmd = [
                "curl", "-L", "-o", temp_path,
                "--fail", "--silent", "--show-error",
                video_url,
            ]

            subprocess.run(cmd, capture_output=True, text=True, check=True)
            file_size = Path(temp_path).stat().st_size
            logger.info("Downloaded %d bytes to %s", file_size, temp_path)
            return temp_path

        except subprocess.CalledProcessError as e:
            logger.warning(
                "Download attempt %d failed: %s",
                attempt + 1,
                e.stderr.strip(),
            )
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            if attempt < max_retries - 1:
                wait = 2**attempt
                logger.info("Retrying in %ds ...", wait)
                time.sleep(wait)

    raise RuntimeError(
        f"Failed to download video after {max_retries} attempts"
    )


def extract_frames(
    video_path: str,
    start_seconds: float,
    duration: float,
    output_dir: Path,
    fps: float = COARSE_FPS,
) -> list[Path]:
    """Extract individual frames from video using FFmpeg.

    Returns sorted list of paths to extracted frame images.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%04d.png")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", f"{start_seconds:.6f}",
        "-t", f"{duration:.6f}",
        "-vf", f"fps={fps}",
        "-vsync", "vfr",
        "-frame_pts", "1",
        "-pix_fmt", "rgb24",
        pattern,
    ]

    logger.info(
        "Extracting frames: start=%.3fs, duration=%.3fs, fps=%.1f",
        start_seconds, duration, fps,
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("FFmpeg stderr:\n%s", result.stderr)
        raise RuntimeError(
            f"FFmpeg frame extraction failed: {result.stderr[-500:]}"
        )

    frames = sorted(output_dir.glob("frame_*.png"))
    logger.info(
        "Extracted %d frame(s) to %s", len(frames), output_dir
    )
    return frames


def maybe_download_video(
    video_url: str | None = None,
    video_path: str | None = None,
    video_base_url: str | None = None,
    xml_path: str | None = None,
) -> tuple[str, bool]:
    """Resolve video source — download URL or return local path.

    Returns:
        Tuple of (local_path_or_url, was_downloaded).
    """
    if video_path:
        return video_path, False

    if video_url:
        local = download_video_to_temp(video_url)
        return local, True

    if video_base_url and xml_path:
        stem = Path(xml_path).stem
        if stem.endswith("_refined"):
            stem = stem[:-8]
        derived_url = f"{video_base_url.rstrip('/')}/{stem}.mp4"
        local = download_video_to_temp(derived_url)
        return local, True

    raise ValueError(
        "No video source provided. Use --video-url, --video-path, "
        "or --video-base-url + --xml-file."
    )


# ── XML parsing ──────────────────────────────────────────────────────────


def parse_advert_xml(xml_path: str) -> tuple[list[AdvertInfo], str | None]:
    """Parse 1 FPS detection XML and extract advert metadata.

    Args:
        xml_path: Path to the XML file.

    Returns:
        Tuple of (list of AdvertInfo, video_filename_or_None).
    """
    p = Path(xml_path)
    if not p.exists():
        raise FileNotFoundError(f"XML not found: {xml_path}")

    tree = ElementTree.parse(p)
    root = tree.getroot()

    # Try to get video filename
    video_file = root.get("video_file") or root.get("filename", None)

    adverts: list[AdvertInfo] = []
    for el in root.findall("advert"):
        def text(tag: str) -> str:
            child = el.find(tag)
            return child.text.strip() if child is not None and child.text else ""

        def int_text(tag: str) -> int | None:
            val = text(tag)
            return int(val) if val else None

        adv = AdvertInfo(
            unique_id=text("unique_id"),
            brand=text("brand"),
            advertiser=text("advertiser"),
            category=text("category"),
            duration_seconds=int_text("duration_seconds"),
            timecode=text("last_timecode") or None,
            refined_timecode=text("refined_timecode") or None,
        )
        adverts.append(adv)

    if not adverts:
        logger.warning("No <advert> elements found in XML")

    return adverts, video_file


def parse_pipeline_state(
    state_path: str,
    ad_break_index: int = 1,
) -> tuple[list[AdvertInfo], str | None, float | None]:
    """Parse pipeline state file for advert metadata and clip offset.

    Returns:
        Tuple of (adverts, video_filename, clip_offset_seconds_or_None).
    """
    with open(state_path) as f:
        state = json.load(f)

    breaks = state.get("ad_breaks", [])
    if ad_break_index < 1 or ad_break_index > len(breaks):
        raise ValueError(
            f"Ad break index {ad_break_index} out of range "
            f"(1-{len(breaks)})"
        )

    brk = breaks[ad_break_index - 1]
    clip_offset = brk.get("clip_offset")
    video_file = brk.get("video_file") or brk.get("filename")

    adverts: list[AdvertInfo] = []
    for adv_data in brk.get("adverts", []):
        c1 = adv_data.get("coarse_1fps", {}) or {}
        adv = AdvertInfo(
            unique_id=adv_data.get("unique_id", ""),
            brand=adv_data.get("brand", ""),
            advertiser=adv_data.get("advertiser", ""),
            category=adv_data.get("category", ""),
            duration_seconds=adv_data.get("duration_seconds"),
            timecode=c1.get("last_timecode"),
            refined_timecode=(
                adv_data.get("refined_25fps", {}) or {}
            ).get("refined_timecode"),
        )
        adverts.append(adv)

    return adverts, video_file, clip_offset


# ── Timecode helpers ──────────────────────────────────────────────────────


def seconds_to_timecode(total_seconds: float) -> str:
    """Convert seconds to MM:SS.SSS or HH:MM:SS.SSS timecode string."""
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


def tc_to_seconds(tc: str) -> float:
    """Convert HH:MM:SS or MM:SS timecode string to seconds."""
    parts = tc.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Invalid timecode: {tc}")


def estimate_window(
    advert: AdvertInfo,
    clip_offset: float | None = None,
) -> tuple[float, float]:
    """Estimate the start time and window duration for an advert.

    Uses refined_timecode → timecode → clip_offset as fallback chain.

    Returns:
        Tuple of (start_seconds, window_duration_seconds).
    """
    tc = advert.refined_timecode or advert.timecode
    if tc:
        start = tc_to_seconds(tc)
        if clip_offset is not None:
            start -= clip_offset
    elif clip_offset is not None:
        start = clip_offset
    else:
        start = 0.0

    # Window covers the advert duration + padding on both sides
    duration = (advert.duration_seconds or 30) + ADVERT_PADDING * 2
    start -= ADVERT_PADDING

    return max(start, 0.0), duration


# ── Grid image creation (for visual debugging) ────────────────────────────


def create_match_grid(
    frames: list[FrameOCRMatch],
    output_path: Path,
    columns: int = GRID_COLS,
    cell_w: int = CELL_W,
    cell_h: int = CELL_H,
    label_h: int = LABEL_H,
) -> Path:
    """Create a numbered grid where matched frames are highlighted green.

    Non-matching frames get a dimmed treatment.  Matched frames get a
    green border highlight and the matching terms shown in the label.
    """
    total = len(frames)
    rows = (total + columns - 1) // columns
    total_w = columns * cell_w
    total_h = rows * (cell_h + label_h)

    # Font
    font = None
    for font_path in [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Courier.dfont",
    ]:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(font_path, 11)
            break
        except Exception:
            continue

    canvas = Image.new("RGB", (total_w, total_h), color=(20, 20, 20))
    from PIL import ImageDraw, ImageFont as IF

    for idx, frm in enumerate(frames):
        col = idx % columns
        row = idx // columns
        x = col * cell_w
        y = row * (cell_h + label_h)

        path = Path(frm.path)
        try:
            if path.exists():
                img = Image.open(path).convert("RGB")
            else:
                raise FileNotFoundError(frm.path)

            if frm.matched:
                # Highlight: brighter rendering
                img = img.point(lambda p: min(255, int(p * 1.15)))
            else:
                # Dim non-matches
                img = img.point(lambda p: int(p * 0.6))

            img.thumbnail((cell_w, cell_h), Image.LANCZOS)
            paste_x = x + (cell_w - img.width) // 2
            paste_y = y + (cell_h - img.height) // 2
            canvas.paste(img, (paste_x, paste_y))

            # Border: green for match, grey for non-match
            draw = ImageDraw.Draw(canvas)
            border_color = (0, 200, 80) if frm.matched else (60, 60, 60)
            draw.rectangle(
                [x, y, x + cell_w, y + cell_h],
                outline=border_color, width=2,
            )

        except Exception as e:
            draw = ImageDraw.Draw(canvas)
            draw.rectangle(
                [x, y, x + cell_w, y + cell_h],
                fill=(40, 40, 40),
            )

        # Label
        label_parts = [f"{idx + 1:03d}/{total:03d}"]
        if frm.matched and frm.matched_terms:
            label_parts.append(f"[{','.join(frm.matched_terms[:2])}]")
        label = " ".join(label_parts)

        draw = ImageDraw.Draw(canvas)
        label_y = y + cell_h
        if font:
            bbox = draw.textbbox((0, 0), label, font=font)
            label_w = bbox[2] - bbox[0]
            label_x = x + (cell_w - label_w) // 2
            draw.text(
                (label_x, label_y + 2),
                label,
                fill=(200, 200, 200) if not frm.matched else (0, 255, 100),
                font=font,
            )
        else:
            draw.text((x + 4, label_y + 2), label, fill=(200, 200, 200))

    canvas.save(output_path, "PNG", dpi=(GRID_DPI, GRID_DPI))
    logger.info(
        "Match grid saved: %s (%d×%d px)", output_path, total_w, total_h
    )
    return output_path


# ── Core OCR analysis logic ──────────────────────────────────────────────


def run_ocr_stage(
    video_path: str,
    start_seconds: float,
    duration: float,
    fps: float,
    output_dir: Path,
    patterns: list[re.Pattern],
    stage_name: str,
    ocr_endpoint: str = DEFAULT_OCR_ENDPOINT,
    ocr_model: str = DEFAULT_OCR_MODEL,
) -> OcrStageResult:
    """Run one OCR stage: extract frames, OCR each, match against patterns.

    Args:
        video_path: Local path to video file.
        start_seconds: Start time in seconds.
        duration: Window duration in seconds.
        fps: Frames per second for extraction.
        output_dir: Directory to write frame images.
        patterns: Compiled regex patterns from build_match_patterns().
        stage_name: "coarse_1fps" or "refined_25fps".
        ocr_endpoint: vLLM OCR endpoint URL.
        ocr_model: OCR model name.

    Returns:
        OcrStageResult with frame-level match data.
    """
    stage_dir = output_dir / stage_name
    stage_dir.mkdir(parents=True, exist_ok=True)

    result = OcrStageResult(
        stage=stage_name,
        fps=fps,
        start_seconds=start_seconds,
        duration=duration,
        total_frames=0,
    )

    # 1. Extract frames
    frame_paths = extract_frames(
        video_path=video_path,
        start_seconds=start_seconds,
        duration=duration,
        output_dir=stage_dir,
        fps=fps,
    )
    result.total_frames = len(frame_paths)

    if not frame_paths:
        logger.warning("No frames extracted for %s stage", stage_name)
        return result

    # 2. OCR each frame (batch)
    ocr_results = ocr_batch(
        image_paths=frame_paths,
        endpoint=ocr_endpoint,
        model=ocr_model,
        progress_callback=lambda c, t: (
            logger.info(
                "OCR [%s] %d/%d frames", stage_name, c, t
            )
            if c % max(1, t // 10) == 0 or c == t
            else None
        ),
    )

    # 3. Match each OCR result against patterns
    last_match_idx: int | None = None

    for ocr_res in ocr_results:
        text = ocr_res.get("text", "")
        matched, terms = match_ocr_text(text, patterns)

        frame_match = FrameOCRMatch(
            frame_index=ocr_res["frame_index"],
            frame_name=ocr_res["frame_name"],
            path=ocr_res["path"],
            ocr_text=text,
            matched=matched,
            matched_terms=terms,
            error=ocr_res.get("error"),
        )
        result.frames.append(frame_match)

        if matched:
            last_match_idx = ocr_res["frame_index"]
            result.match_count += 1

    # 4. Compute coarse results
    result.last_matching_frame = last_match_idx
    if last_match_idx is not None and len(frame_paths) > 0:
        # Frame index → timecode within the window
        frame_time_in_window = last_match_idx / fps
        frame_global = start_seconds + frame_time_in_window
        result.last_matching_timecode = seconds_to_timecode(frame_global)

    logger.info(
        "[%s] %d/%d frames matched, last match at frame %s (tc=%s)",
        stage_name,
        result.match_count,
        result.total_frames,
        result.last_matching_frame,
        result.last_matching_timecode or "N/A",
    )

    return result


# ── Main orchestration ────────────────────────────────────────────────────


def process_advert(
    advert: AdvertInfo,
    video_path: str,
    clip_offset: float | None,
    output_dir: Path,
    patterns: list[re.Pattern],
    ocr_endpoint: str,
    ocr_model: str,
    stage_filter: Literal["all", "1-only"],
) -> AdvertOCRResult:
    """Process a single advert through the OCR pipeline.

    Returns:
        AdvertOCRResult with coarse + refined stage results.
    """
    start_sec, window_sec = estimate_window(advert, clip_offset)
    logger.info(
        "Processing advert %s (%s / %s) — window: %.1fs @ FPS %.1f",
        advert.unique_id, advert.brand, advert.advertiser,
        window_sec, COARSE_FPS,
    )

    # Stage 1: Coarse 1 FPS scan
    coarse = run_ocr_stage(
        video_path=video_path,
        start_seconds=start_sec,
        duration=window_sec,
        fps=COARSE_FPS,
        output_dir=output_dir,
        patterns=patterns,
        stage_name="coarse_1fps",
        ocr_endpoint=ocr_endpoint,
        ocr_model=ocr_model,
    )

    # Create coarse match grid
    coarse_grid = output_dir / f"{advert.unique_id}_coarse_grid.png"
    if coarse.frames:
        create_match_grid(coarse.frames, coarse_grid)

    # Stage 2: Refined 25 FPS (skip if 1-only or no match)
    refined: OcrStageResult | None = None
    if stage_filter != "1-only" and coarse.last_matching_frame is not None:
        refined_start = coarse.frames[coarse.last_matching_frame].frame_index / COARSE_FPS
        refined_start_global = start_sec + refined_start

        # Window: REFINE_WINDOW seconds centred on the last match
        refine_window_start = max(
            refined_start_global - REFINE_WINDOW / 2,
            0.0,
        )

        logger.info(
            "Refining around tc=%s (%.3fs), window=%.1fs @ 25 FPS",
            coarse.last_matching_timecode,
            refined_start_global,
            REFINE_WINDOW,
        )

        refined = run_ocr_stage(
            video_path=video_path,
            start_seconds=refine_window_start,
            duration=REFINE_WINDOW,
            fps=REFINE_FPS,
            output_dir=output_dir,
            patterns=patterns,
            stage_name="refined_25fps",
            ocr_endpoint=ocr_endpoint,
            ocr_model=ocr_model,
        )

        # Create refined match grid
        refined_grid = output_dir / f"{advert.unique_id}_refined_grid.png"
        if refined.frames:
            create_match_grid(refined.frames, refined_grid)

    return AdvertOCRResult(
        unique_id=advert.unique_id,
        brand=advert.brand,
        advertiser=advert.advertiser,
        category=advert.category,
        duration_seconds=advert.duration_seconds,
        coarse=coarse,
        refined=refined,
        output_dir=str(output_dir),
    )


# ── Results output ────────────────────────────────────────────────────────


def _frame_ocr_to_dict(f: FrameOCRMatch) -> dict:
    return {
        "frame_index": f.frame_index,
        "frame_name": f.frame_name,
        "path": f.path,
        "matched": f.matched,
        "matched_terms": f.matched_terms,
        "error": f.error,
        "ocr_text_preview": f.ocr_text[:200] if f.ocr_text else "",
    }


def _stage_to_dict(s: OcrStageResult) -> dict:
    return {
        "stage": s.stage,
        "fps": s.fps,
        "start_seconds": s.start_seconds,
        "duration": s.duration,
        "total_frames": s.total_frames,
        "match_count": s.match_count,
        "last_matching_frame": s.last_matching_frame,
        "last_matching_timecode": s.last_matching_timecode,
        "frames": [_frame_ocr_to_dict(f) for f in s.frames],
    }


def write_results(
    results: list[AdvertOCRResult],
    output_dir: Path,
    video_source: str,
    ocr_endpoint: str,
    ocr_model: str,
) -> None:
    """Write OCR refinement results as JSON."""
    output = {
        "pipeline": "ocr_refinement_experiment",
        "video_source": video_source,
        "ocr_endpoint": ocr_endpoint,
        "ocr_model": ocr_model,
        "timestamp": datetime.now().isoformat(),
        "adverts": [],
    }

    for r in results:
        adv_dict = {
            "unique_id": r.unique_id,
            "brand": r.brand,
            "advertiser": r.advertiser,
            "category": r.category,
            "duration_seconds": r.duration_seconds,
            "output_dir": r.output_dir,
        }
        if r.coarse:
            adv_dict["coarse_1fps"] = _stage_to_dict(r.coarse)
        if r.refined:
            adv_dict["refined_25fps"] = _stage_to_dict(r.refined)
        output["adverts"].append(adv_dict)

    result_path = output_dir / "ocr_refinement_results.json"
    with open(result_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("Results written to %s", result_path)


def print_summary(results: list[AdvertOCRResult]) -> None:
    """Print a human-readable summary to stdout."""
    sep = "─" * 60
    print(f"\n{sep}")
    print("OCR REFINEMENT SUMMARY")
    print(sep)

    for r in results:
        print(f"\nAdvert: {r.unique_id} — {r.brand} / {r.advertiser}")

        if r.coarse:
            c = r.coarse
            print(f"  [1 FPS] {c.match_count}/{c.total_frames} matched")
            if c.last_matching_timecode:
                print(f"          Last match @ tc={c.last_matching_timecode}")
                print(f"          (frame #{c.last_matching_frame})")
            else:
                print("          No matches found")

        if r.refined:
            ref = r.refined
            print(f"  [25 FPS] {ref.match_count}/{ref.total_frames} matched")
            if ref.last_matching_timecode:
                print(f"           Last match @ tc={ref.last_matching_timecode}")
                print(f"           (frame #{ref.last_matching_frame})")
            else:
                print("           No matches found")

    print(sep)


# ── CLI entry point ──────────────────────────────────────────────────────


def _parse_advert_cli_flag(val: str) -> AdvertInfo:
    """Parse --advert flag: ``id|advertiser|brand|category|duration``."""
    parts = val.split("|")
    if len(parts) != 5:
        raise ValueError(
            f"Invalid --advert format: '{val}'. "
            "Expected: id|advertiser|brand|category|duration"
        )
    return AdvertInfo(
        unique_id=parts[0].strip(),
        advertiser=parts[1].strip(),
        brand=parts[2].strip(),
        category=parts[3].strip(),
        duration_seconds=int(parts[4].strip()),
    )


def main():
    parser = argparse.ArgumentParser(
        description="OCR-based advert boundary refinement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input source (one of these groups)
    source = parser.add_argument_group("Input source (pick one pattern)")
    source.add_argument(
        "--xml-file",
        help="Path to 1 FPS detection XML file",
    )
    source.add_argument(
        "--state-file",
        help="Path to pipeline state JSON file",
    )
    source.add_argument(
        "--ad-break-index", type=int, default=1,
        help="Ad break index (1-based, for state file, default: 1)",
    )

    # Video source
    video = parser.add_argument_group("Video source")
    video.add_argument(
        "--video-url",
        help="Video HTTP URL (downloaded to temp)",
    )
    video.add_argument(
        "--video-path",
        help="Local video file path",
    )
    video.add_argument(
        "--video-base-url",
        metavar="URL",
        help="Base URL for videos when deriving from --xml-file. "
        "E.g. http://172.18.7.236:1100/",
    )

    # Manual advert specification (alternative to XML/state)
    manual = parser.add_argument_group("Manual adverts (instead of XML)")
    manual.add_argument(
        "--prog-before",
        help="Programme before break: \"Title,Channel\"",
    )
    manual.add_argument(
        "--prog-after",
        help="Programme after break: \"Title,Channel\"",
    )
    manual.add_argument(
        "--advert",
        action="append",
        metavar="ID|ADVERTISER|BRAND|CATEGORY|DURATION",
        help="Advert metadata (repeat for multiple). "
        "E.g. AD001|Disney|Disneyland|Theme Parks|30",
    )

    # OCR configuration
    ocr_grp = parser.add_argument_group("OCR configuration")
    ocr_grp.add_argument(
        "--ocr-endpoint",
        default=DEFAULT_OCR_ENDPOINT,
        help=f"vLLM OCR endpoint (default: {DEFAULT_OCR_ENDPOINT})",
    )
    ocr_grp.add_argument(
        "--ocr-model",
        default=DEFAULT_OCR_MODEL,
        help=f"OCR model name (default: {DEFAULT_OCR_MODEL})",
    )

    # Stage control
    stage = parser.add_argument_group("Stage control")
    stage.add_argument(
        "--stage",
        choices=["all", "1-only"],
        default="all",
        help="Which stages to run: 'all' (1fps + 25fps, default) or '1-only'",
    )

    # Output
    out = parser.add_argument_group("Output")
    out.add_argument(
        "--output-dir",
        default="experiments/ocr_results",
        help="Output directory for frames, grids, and results JSON",
    )
    out.add_argument(
        "--keep-frames",
        action="store_true",
        default=True,
        help="Keep extracted frame images (default: True)",
    )
    out.add_argument(
        "--no-keep-frames",
        action="store_false",
        dest="keep_frames",
        help="Delete extracted frame images after processing",
    )

    args = parser.parse_args()

    # ── Resolve advert list ──────────────────────────────────────────
    adverts: list[AdvertInfo] = []
    video_arg: str | None = None  # For result metadata
    clip_offset: float | None = None

    if args.xml_file:
        adverts, video_file = parse_advert_xml(args.xml_file)
        video_arg = args.xml_file
    elif args.state_file:
        adverts, video_file, clip_offset = parse_pipeline_state(
            args.state_file, args.ad_break_index
        )
        video_arg = args.state_file
    elif args.advert:
        for adv_str in args.advert:
            adverts.append(_parse_advert_cli_flag(adv_str))
    else:
        parser.error(
            "No input provided. Use --xml-file, --state-file, "
            "or --advert (with --prog-before/--prog-after)."
        )

    if not adverts:
        logger.error("No adverts to process — check input file")
        sys.exit(1)

    logger.info(
        "Loaded %d advert(s): %s",
        len(adverts),
        ", ".join(f"{a.brand}/{a.advertiser}" for a in adverts),
    )

    # ── Resolve video source ─────────────────────────────────────────
    try:
        local_video, was_downloaded = maybe_download_video(
            video_url=args.video_url,
            video_path=args.video_path,
            video_base_url=args.video_base_url,
            xml_path=args.xml_file,
        )
    except ValueError as e:
        parser.error(str(e))
        return  # unreachable

    # ── Setup output directory ───────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Process each advert ──────────────────────────────────────
        results: list[AdvertOCRResult] = []
        for advert in adverts:
            patterns = build_match_patterns(
                brand=advert.brand,
                advertiser=advert.advertiser,
                category=advert.category,
            )

            logger.info(
                "Built %d match pattern(s) for %s: %s",
                len(patterns),
                advert.unique_id,
                [p.pattern for p in patterns],
            )

            adv_dir = output_dir / advert.unique_id
            result = process_advert(
                advert=advert,
                video_path=local_video,
                clip_offset=clip_offset,
                output_dir=adv_dir,
                patterns=patterns,
                ocr_endpoint=args.ocr_endpoint,
                ocr_model=args.ocr_model,
                stage_filter=args.stage,
            )
            results.append(result)

        # ── Write results ────────────────────────────────────────────
        write_results(
            results=results,
            output_dir=output_dir,
            video_source=video_arg or local_video,
            ocr_endpoint=args.ocr_endpoint,
            ocr_model=args.ocr_model,
        )

        print_summary(results)

    finally:
        # Clean up downloaded temp file
        if was_downloaded and local_video and os.path.exists(local_video):
            os.unlink(local_video)
            logger.debug("Cleaned up temp video: %s", local_video)

        # Clean up frame images if requested
        if not args.keep_frames:
            for adv_dir in output_dir.iterdir():
                if adv_dir.is_dir():
                    for stage_dir in adv_dir.iterdir():
                        if stage_dir.is_dir():
                            for f in stage_dir.glob("frame_*.png"):
                                f.unlink()
                    # Remove empty dirs
                    try:
                        for stage_dir in adv_dir.iterdir():
                            if stage_dir.is_dir():
                                stage_dir.rmdir()
                    except OSError:
                        pass


if __name__ == "__main__":
    main()
