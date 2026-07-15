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
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
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

# Words too generic to use as individual brand match patterns.
# Full-phrase patterns still include these words; only standalone
# single-word matching is suppressed to avoid false positives from
# programme content (e.g. "cheese" in "Swiss cheese plant" matching
# "Consorzio grana padano cheese").
_BRAND_STOP_WORDS: frozenset = frozenset(
    {
        "book",
        "brand",
        "care",
        "cheese",
        "choice",
        "city",
        "club",
        "company",
        "cost",
        "deal",
        "deals",
        "design",
        "direct",
        "discount",
        "edge",
        "energy",
        "enterprise",
        "essentials",
        "expert",
        "extra",
        "family",
        "food",
        "free",
        "fresh",
        "full",
        "global",
        "gold",
        "group",
        "health",
        "help",
        "home",
        "house",
        "ideal",
        "international",
        "life",
        "line",
        "local",
        "love",
        "market",
        "media",
        "mobile",
        "money",
        "nationwide",
        "network",
        "new",
        "next",
        "north",
        "now",
        "office",
        "online",
        "original",
        "plus",
        "point",
        "premier",
        "premium",
        "price",
        "pro",
        "product",
        "protection",
        "rate",
        "real",
        "rent",
        "restaurant",
        "sale",
        "save",
        "secure",
        "select",
        "service",
        "shop",
        "show",
        "sign",
        "site",
        "smart",
        "solar",
        "solution",
        "south",
        "special",
        "star",
        "start",
        "store",
        "studio",
        "super",
        "support",
        "system",
        "team",
        "tech",
        "technology",
        "top",
        "total",
        "tour",
        "trade",
        "travel",
        "trip",
        "trust",
        "uk",
        "value",
        "view",
        "ware",
        "web",
        "west",
        "wide",
        "world",
        "year",
        "zone",
        # Added 2026-07-10: generic words found in 2026-07-09 run that
        # caused false-positive matches in programme content.
        # Each brand still matches via its other distinctive words or
        # full-phrase patterns.
        "blade",  # Vax cordless blade vacuum
        "cancer",  # CCS NHS Cancer, Cancer Research Race For Life
        "careers",  # CCS childcare careers
        "cash",  # Fast cash property (also subword: fastcashproperty)
        "chairs",  # HSL chairs
        "delivery",  # Deliveroo takeaway delivery (also: deliveroo)
        "easter",  # Tesco/Lidl/Disneyland Easter
        "experts",  # Trailfinders travel experts (also: trailfinders)
        "farm",  # Childs farm (also: childs)
        "fast",  # Fast cash property (also subword: fastcashproperty)
        "great",  # Great ormond street / First great western
        "insurance",  # Aviva insurance, Liverpool Victoria
        "lottery",  # Age UK / Postcode lottery
        "noodle",  # Pot noodle
        "noodles",  # Batchelors super noodles (also: super, batchelors)
        "over",  # AXA sun life over 50 ins plan
        "pain",  # Voltarol pain relief (also: voltarol)
        "paris",  # Disneyland paris, Loreal paris
        "pass",  # Merlin annual pass (also: merlin, annual)
        "phone",  # Google pixel 8 phone (also: pixel, google)
        "plan",  # AXA sun life plan
        "plenty",  # Essity plenty kitchen towel
        "power",  # Domestos power foam, E.ON (also: foam, domentos)
        "relief",  # Voltarol pain relief (also: voltarol)
        "research",  # Cancer research race for life (also: race, life)
        "reveal",  # Loreal bright reveal serum (also: bright, serum)
        "richmond",  # Pilgrims richmond sausages (also: sausages)
        "sausages",  # Pilgrims richmond sausages (also: richmond)
        "serum",  # Boots/Clarins/Nivea serum (also: boots, clarins, nivea)
        "shampoo",  # Herbal essences shampoo (also: herbal, essences)
        "sips",  # McDonalds winning sips (also: winning, mcdonalds)
        "smoking",  # CCS phe stop smoking
        "stain",  # Vanish oxi action stain remover (also: vanish)
        "stores",  # Poundland stores (also: poundland)
        "tail",  # Yellow tail shiraz wine (also: yellow, shiraz)
        "western",  # First great western (also: great)
        "winning",  # McDonalds winning sips (also: sips, mcdonalds)
        "wrinkle",  # Nivea wrinkle filler serum (also: filler, serum)
        "yellow",  # Yellow tail shiraz wine (also: tail, shiraz)
    }
)


# Brand-specific pattern overrides for cases where the auto-generated
# variants don't cover the OCR text seen in practice.  Each entry maps
# a brand name to extra patterns to try (as raw strings, case-insensitive).
# These are added to both exact (word-boundary) and substring pattern sets.
_BRAND_PATTERN_OVERRIDES: dict[str, list[str]] = {
    "Disneyland paris": ["Disney"],
    "Media 10 ideal home show": ["idealhomeshow"],
    "Edf energy": ["edfenergy"],
    "Cancer research race for life": ["raceforlife"],
    "Axa sun life over 50 ins plan": ["sunlife"],
    "Postcode lottery": ["postcodelottery"],
    "Motorway.co.uk": ["motorway"],
    "First great western": ["greatwestern"],
}


def build_exact_patterns(
    brand: str,
    skip_words: bool = False,
) -> list[re.Pattern]:
    """Build word-boundary regex patterns for exact matching.

    Only the brand name is used (not advertiser or category) to avoid
    false positives from common English words in those fields.
    Generates case-insensitive patterns with \\b word boundaries for
    brand and individual words from multi-word terms.
    Apostrophe-stripped variants are also included.

    When *skip_words* is True, only the full-phrase pattern (and its
    apostrophe-stripped variant) is generated — individual word
    patterns are suppressed.  This is used by the advertiser fallback
    (Tier 3) to avoid false positives from common words like "group"
    in advertiser names such as "Avios group".
    """
    patterns: list[re.Pattern] = []
    seen: set[str] = set()

    term = brand.strip()
    if not term:
        return patterns
    seen.add(term)

    escaped = re.escape(term)
    patterns.append(re.compile(rf"\b{escaped}\b", re.IGNORECASE))

    if not skip_words:
        words = term.split()
        if len(words) > 1:
            for word in words:
                if (
                    len(word) >= 4
                    and word not in seen
                    and word.lower() not in _BRAND_STOP_WORDS
                ):
                    seen.add(word)
                    patterns.append(
                        re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
                    )

    simplified = term.replace("'", "").replace("\u2019", "")
    if simplified != term and simplified not in seen:
        seen.add(simplified)
        patterns.append(re.compile(rf"\b{re.escape(simplified)}\b", re.IGNORECASE))

    # Brand-specific overrides
    if not skip_words and term in _BRAND_PATTERN_OVERRIDES:
        for override in _BRAND_PATTERN_OVERRIDES[term]:
            if override not in seen:
                seen.add(override)
                patterns.append(
                    re.compile(rf"\b{re.escape(override)}\b", re.IGNORECASE)
                )

    return patterns


def build_substring_patterns(
    brand: str,
    skip_words: bool = False,
) -> list[re.Pattern]:
    """Build unbounded regex patterns for fuzzy substring matching.

    Only the brand name is used (not advertiser or category).  Catches
    concatenated forms like "galaxychocolate.com".  Individual words
    shorter than 4 chars are excluded to avoid false positives from
    short common substrings (e.g. "age" inside "image").

    When *skip_words* is True, only the full-phrase substring pattern
    (and its apostrophe-stripped variant) is generated — individual
    word substrings are suppressed.  This is used by the advertiser
    fallback (Tier 3) to prevent generic words like "group" from
    matching unrelated OCR text.
    """
    patterns: list[re.Pattern] = []
    seen: set[str] = set()

    term = brand.strip()
    if not term:
        return patterns
    seen.add(term)

    escaped = re.escape(term)
    patterns.append(re.compile(escaped, re.IGNORECASE))

    if not skip_words:
        words = term.split()
        if len(words) > 1:
            for word in words:
                if (
                    len(word) >= 4
                    and word not in seen
                    and word.lower() not in _BRAND_STOP_WORDS
                ):
                    seen.add(word)
                    patterns.append(re.compile(re.escape(word), re.IGNORECASE))

    simplified = term.replace("'", "").replace("\u2019", "")
    if simplified != term and simplified not in seen:
        seen.add(simplified)
        patterns.append(re.compile(re.escape(simplified), re.IGNORECASE))

    # Brand-specific overrides
    if not skip_words and term in _BRAND_PATTERN_OVERRIDES:
        for override in _BRAND_PATTERN_OVERRIDES[term]:
            if override not in seen:
                seen.add(override)
                patterns.append(re.compile(re.escape(override), re.IGNORECASE))

    return patterns


def match_ocr_text(
    ocr_text: str,
    patterns: list[re.Pattern],
) -> tuple[bool, list[str]]:
    """Check OCR text against match patterns.

    Returns (matched, matched_terms).

    The text is tested in three passes:
    1. Original text
    2. Dot-stripped text — catches on-screen text like "GO.COMPARE"
       where dots act as visual word separators but the brand is a
       single token like "Gocompare.com".
    3. Newline-to-space flattened text — catches multi-line OCR like
       "POT\\nnoodle" that the brand phrase "Pot noodle" can't match
       across a newline.
    """
    if not ocr_text or not ocr_text.strip():
        return False, []

    def _check(text: str) -> tuple[bool, list[str]]:
        matched_terms: list[str] = []
        for pat in patterns:
            m = pat.search(text)
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

    matched, terms = _check(ocr_text)
    if matched:
        return matched, terms

    dotless = ocr_text.replace(".", "")
    if dotless != ocr_text:
        matched, terms = _check(dotless)
        if matched:
            return matched, terms

    flat = ocr_text.replace("\n", " ").replace("\r", " ")
    if flat != ocr_text and flat != dotless:
        return _check(flat)

    return False, []


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
                attempt + 1,
                max_retries,
                video_url,
            )
            subprocess.run(
                [
                    "curl",
                    "-L",
                    "-o",
                    temp_path,
                    "--fail",
                    "--silent",
                    "--show-error",
                    video_url,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(
                "Downloaded %d bytes to %s",
                Path(temp_path).stat().st_size,
                temp_path,
            )
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
                time.sleep(2**attempt)

    raise RuntimeError(f"Failed to download video after {max_retries} attempts")


def _start_local_video_server(video_path: str) -> tuple[str, HTTPServer]:
    """Start a temporary HTTP server for a local video file.

    When a local file path is provided instead of an HTTP URL, this
    function serves the file's parent directory over HTTP so the rest
    of the pipeline (which expects a downloadable URL) can proceed.

    Returns:
        Tuple of (http_url, server_instance).  Call ``server.shutdown()``
        to stop the server when done.
    """
    path = Path(video_path).resolve()
    parent = path.parent
    filename = path.name

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    class _SilentHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(parent), **kwargs)

        def log_message(self, fmt, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), _SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/{filename}"
    logger.info("Started local video server at %s serving %s", url, parent)
    return url, server


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
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_seconds:.6f}",
        "-i",
        video_path,
        "-t",
        f"{duration:.6f}",
        "-vf",
        f"fps={fps}",
        "-vsync",
        "vfr",
        "-frame_pts",
        "1",
        "-pix_fmt",
        "rgb24",
        pattern,
    ]

    logger.info(
        "Extracting frames at %g FPS: start=%.3fs, duration=%.3fs",
        fps,
        start_seconds,
        duration,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error("FFmpeg stderr:\n%s", result.stderr)
        raise RuntimeError(f"FFmpeg frame extraction failed: {result.stderr[-500:]}")

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
        data["frames"].append(
            {
                "frame_index": idx,
                "frame_name": res.get("frame_name", ""),
                "timestamp_clip": round(clip_ts, 3),
                "timestamp_broadcast": round(broadcast_ts, 3),
                "text": res.get("text", ""),
                "error": res.get("error"),
            }
        )

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
    correction: str | None = None  # set by clamp_correct() if adjusted
    original_last_match_frame: int | None = None  # frame before correction
    refined_end_seconds: float | None = None  # set by _refine_advert_end_frames()
    refinement_data: dict | None = (
        None  # diagnostic data from _refine_advert_end_frames()
    )


SOURCE_FPS = 25  # source video frame rate for refinement extraction


def _brand_variants(brand: str) -> list[tuple[str, str]]:
    """Generate progressively relaxed brand variants for matching.

    Each variant is a ``(text, label)`` pair where *label* describes
    the transformation (used in match tier reporting).

    Variants (in priority order):
    1. Original brand text  → ``"original"``
    2. ``&`` replaced with ``and``  → ``"ampersand->and"``
    3. Common TLD suffix removed  → ``"no-tld"``
    4. Spaces removed  → ``"concat"``
    5. ``&``→``and`` + spaces removed  → ``"concat-and"``
    6+ Prefixes of 3/4 (longest-first) so that e.g. ``Insure&gotravelinsurance``
       yields prefixes ``Insure&gotravel``, ``Insure&go`` etc. for substring
       matching when the full concatenated form is longer than what the OCR
       captured.
    """
    variants: list[tuple[str, str]] = [(brand, "original")]
    seen: set[str] = {brand}

    spaced = brand.replace("&", "and")
    if spaced != brand and spaced not in seen:
        variants.append((spaced, "ampersand->and"))
        seen.add(spaced)

    # TLD-stripped: remove common TLD suffixes (.com, .co.uk, .org, .net)
    # so that the core brand word can be matched standalone.
    # e.g. "Gocompare.com" → "Gocompare", "Interflora.co.uk" → "Interflora".
    # Combined with the dotless OCR fallback in match_ocr_text, this catches
    # on-screen text like "GO.COMPARE" against brand "Gocompare.com".
    _tld_pat = re.compile(r"\.(com|co\.uk|org|net)", re.IGNORECASE)
    tld_stripped = _tld_pat.sub("", brand)
    if tld_stripped != brand and tld_stripped not in seen:
        variants.append((tld_stripped, "no-tld"))
        seen.add(tld_stripped)

    concat = brand.replace(" ", "")
    if concat != brand and concat not in seen:
        variants.append((concat, "concat"))
        seen.add(concat)

    concat_and = spaced.replace(" ", "")
    if concat_and not in seen:
        variants.append((concat_and, "concat-and"))
        seen.add(concat_and)

    # Prefix variants for both concat forms — try longest first
    # Split on spaces preserving the original words (including "&" as a word)
    words = brand.split()
    for use_and in (True, False):
        base_label = "concat-and" if use_and else "concat"
        for n in range(len(words) - 1, 1, -1):  # at least 2 words
            parts = []
            for w in words[:n]:
                if use_and and w == "&":
                    parts.append("and")
                else:
                    parts.append(w)
            shortened = "".join(parts)
            if len(shortened) >= 4 and shortened not in seen:
                variants.append((shortened, f"concat({n})"))
                seen.add(shortened)

    # Possessive variants: for each word ending in 's' (but not 'ss'),
    # try the possessive form ("Butlins" → "Butlin's") as a full
    # phrase, so exact phrase matching catches the apostrophe form.
    words = brand.split()
    for i, word in enumerate(words):
        if len(word) >= 4 and word.endswith("s") and not word.endswith("ss"):
            possessive = word[:-1] + "'s"
            if possessive != word:
                parts = list(words)
                parts[i] = possessive
                variant = " ".join(parts)
                if variant not in seen:
                    variants.append((variant, f"poss({i})"))
                    seen.add(variant)

    return variants


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
        brand_variants = _brand_variants(adv.brand)

        matched = False
        result: BrandSearchResult | None = None

        # ── Helper: scan with a set of patterns ────────────────────────
        def _scan(
            patterns: list[re.Pattern],
        ) -> tuple[list[int], list[str]] | None:
            frames: list[int] = []
            terms: set[str] = set()
            consecutive_misses = 0
            MAX_MISSES = 50  # 50 frames = 10s at 5 FPS; allows brand text
            # to reappear after long gaps within the same advert while
            # still preventing theft from a later advert with the same
            # brand name (e.g. "Vax spotwash" vs "Vax platinum").
            for ocr_res in ocr_results:
                idx = ocr_res["frame_index"]
                if idx <= prev_last_frame:
                    continue
                text = ocr_res.get("text", "")
                hit, matched_terms = match_ocr_text(text, patterns)
                if hit:
                    frames.append(idx)
                    terms.update(t.lower() for t in matched_terms)
                    consecutive_misses = 0
                elif frames:  # only count misses after first match
                    consecutive_misses += 1
                    if consecutive_misses > MAX_MISSES:
                        break
            if frames:
                return frames, list(terms)
            return None

        # ── Groups 1 & 2: try full-phrase then individual-word matching  ──
        # across all variants, then pick whichever gives the latest frame.
        best_result_g1: BrandSearchResult | None = None
        best_result_g2: BrandSearchResult | None = None

        for variant, label in brand_variants:
            # Group 1 — full-phrase matching
            for pat, tier in [
                (rf"\b{re.escape(variant)}\b", f"exact({label})"),
                (re.escape(variant), f"substr({label})"),
            ]:
                hit = _scan([re.compile(pat, re.IGNORECASE)])
                if hit:
                    frames, terms = hit
                    cand = BrandSearchResult(
                        True,
                        frames[-1],
                        frames[-1] / fps,
                        tier,
                        len(frames),
                        frames,
                        terms,
                    )
                    if (
                        best_result_g1 is None
                        or cand.last_match_frame > best_result_g1.last_match_frame
                    ):
                        best_result_g1 = cand

            # Group 2 — individual-word matching
            # Skip for the original variant when a no-tld variant exists:
            # the original's TLD-glued word (e.g. "Tombola.co.uk") rarely
            # matches OCR, while its other words (e.g. "online") are too
            # generic and cause false positives.  The no-tld variant
            # produces the specific core word (e.g. "Tombola") instead.
            has_no_tld = any(l == "no-tld" for _, l in brand_variants)
            if label == "original" and has_no_tld:
                continue
            for pat_fn, tier_prefix in [
                (build_exact_patterns, "words"),
                (build_substring_patterns, "subwords"),
            ]:
                hit = _scan(pat_fn(brand=variant))
                if hit:
                    frames, terms = hit
                    cand = BrandSearchResult(
                        True,
                        frames[-1],
                        frames[-1] / fps,
                        f"{tier_prefix}({label})",
                        len(frames),
                        frames,
                        terms,
                    )
                    if (
                        best_result_g2 is None
                        or cand.last_match_frame > best_result_g2.last_match_frame
                    ):
                        best_result_g2 = cand

        # Pick the best result.  Priority (highest = worst):
        # 1 = G1 (full-phrase) with original/poss variant
        # 2 = G1 with transformed variant
        # 3 = G2 (individual words) with original/poss variant
        # 4 = G2 with transformed variant
        # Within each level, the later frame wins.
        def _priority(result: BrandSearchResult, is_g1: bool) -> int:
            label = result.match_tier.split("(")[-1].rstrip(")")
            is_high = label in ("original",) or label.startswith("poss")
            if is_g1 and is_high:
                return 0
            if not is_g1 and is_high:
                return 1
            if is_g1:
                return 2
            return 3

        best_result = best_result_g1
        if best_result_g2 is not None:
            if best_result is None:
                best_result = best_result_g2
            else:
                p1 = _priority(best_result, True)
                p2 = _priority(best_result_g2, False)
                if p2 < p1 or (
                    p2 == p1
                    and best_result_g2.last_match_frame > best_result.last_match_frame
                ):
                    best_result = best_result_g2

        # Take the later end position across both groups.  G2 (substring)
        # may find matches extending beyond G1 (full-phrase), and the
        # later position is more accurate for clip start calculation even
        # if G1 had a higher-quality match tier.
        if (
            best_result_g1 is not None
            and best_result_g2 is not None
            and best_result_g2.last_match_frame > best_result_g1.last_match_frame
        ):
            max_frame = max(
                best_result_g1.last_match_frame,
                best_result_g2.last_match_frame,
            )
            if max_frame > best_result.last_match_frame:
                best_result = BrandSearchResult(
                    True,
                    max_frame,
                    max_frame / fps,
                    best_result.match_tier,
                    best_result.match_count,
                    best_result.all_matching_frames,
                    best_result.matched_terms,
                )

        if best_result is not None:
            # Re-scan with the winning group's patterns to ensure we use
            # the correct prev_last_frame for ordering enforcement
            prev_last_frame = best_result.last_match_frame
            logger.info(
                "  %s (%s): %s at frame %d (%s)",
                adv.unique_id,
                adv.brand,
                best_result.match_tier,
                best_result.last_match_frame,
                ", ".join(best_result.matched_terms),
            )
            results.append(best_result)
            continue

        # Tier 3: advertiser fallback — retry with advertiser patterns when
        # brand-only failed (catches cases like "Disneyland" brand where
        # OCR only sees "Disney" from "Walt disney company").  Individual
        # word patterns are suppressed (skip_words=True) to avoid false
        # positives from generic words like "group" in "Avios group".
        adv_exact = build_exact_patterns(brand=adv.advertiser, skip_words=True)
        adv_sub = build_substring_patterns(brand=adv.advertiser, skip_words=True)
        adv_matches: list[int] = []
        adv_terms: set[str] = set()

        for ocr_res in ocr_results:
            idx = ocr_res["frame_index"]
            if idx <= prev_last_frame:
                continue
            text = ocr_res.get("text", "")
            matched, terms = match_ocr_text(text, adv_exact)
            if not matched:
                matched, terms = match_ocr_text(text, adv_sub)
            if matched:
                adv_matches.append(idx)
                adv_terms.update(t.lower() for t in terms)

        if adv_matches:
            last_frame = adv_matches[-1]
            result = BrandSearchResult(
                matched=True,
                last_match_frame=last_frame,
                last_match_seconds=last_frame / fps,
                match_tier="advertiser",
                match_count=len(adv_matches),
                all_matching_frames=adv_matches,
                matched_terms=list(adv_terms),
            )
            results.append(result)
            prev_last_frame = last_frame
            logger.info(
                "  %s (%s): advertiser match at frame %d (tc=%s), %d frames matched",
                adv.unique_id,
                adv.brand,
                last_frame,
                seconds_to_timecode(last_frame / fps),
                len(adv_matches),
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
            adv.unique_id,
            adv.brand,
        )

    return results


# ── Clamp/cage: pattern-based anomaly detection and correction ────────


def _effective_seconds(r: BrandSearchResult) -> float | None:
    """Return the best available position for clamp pattern analysis.

    Uses refined 25fps position when available (more precise), otherwise
    falls back to the raw 5fps match position.
    """
    if r.refined_end_seconds is not None:
        return r.refined_end_seconds
    return r.last_match_seconds


def _pattern_key(seconds: float) -> str:
    """Return the ``sec_digit.mmm`` pattern key for a timecode.

    At 5 FPS, every frame lands on .000, .200, .400, .600, .800.  After
    25fps refinement, positions have 25fps precision (.040, .080, .120,
    etc.).  The ``sec_digit`` (units digit of seconds) is the meaningful
    part — adverts with durations that are multiples of 10s all share the
    same sec_digit regardless of millis precision.
    """
    total_sec = int(seconds)
    sec_digit = total_sec % 10
    millis = int(round((seconds - total_sec) * 1000))
    return f"{sec_digit}.{millis:03d}"


# Minimum number of matched adverts that must share the majority
# ``sec_digit.mmm`` pattern for clamp correction to be applied.
# At 5fps the 5 possible millis values made 2-match coincidences
# common (needed ≥3).  After 25fps refinement (25 millis values),
# 2 matches is a meaningful signal, so the threshold is lowered to 2.
_CLAMP_MIN_MAJORITY_COUNT = 2


def clamp_check(
    scan_results: list[BrandSearchResult],
    majority_rule: bool = True,
) -> list[bool]:
    """Identify suspect matches by comparing their ``sec_digit.mmm``
    pattern against the majority across the break.

    Uses refined 25fps positions when available (more precise), falling
    back to 5fps positions otherwise.

    Args:
        scan_results: Brand search results.
        majority_rule: When True (default), require the majority pattern
            to be shared by at least half of all matched adverts.  When
            False, only the min count threshold applies.

    Returns a list of booleans, one per advert: ``True`` means anomaly.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for r in scan_results:
        secs = _effective_seconds(r)
        if r.matched and secs is not None:
            counts[_pattern_key(secs)] += 1

    if not counts:
        return [False] * len(scan_results)

    majority = counts.most_common(1)[0][0]
    majority_count = counts[majority]
    total_matched = sum(counts.values())

    logger.info(
        "Clamp majority pattern: %s (%d/%d matches)",
        majority,
        majority_count,
        total_matched,
    )

    # Gate: minimum count threshold
    if majority_count < _CLAMP_MIN_MAJORITY_COUNT:
        logger.info(
            "Clamp skipped: majority pattern %s has only %d/%d matches "
            "(need ≥%d)%s — insufficient support for correction",
            majority,
            majority_count,
            total_matched,
            _CLAMP_MIN_MAJORITY_COUNT,
            " and ≥50%" if majority_rule else "",
        )
        return [False] * len(scan_results)

    # Optional second gate: 50% majority rule
    if majority_rule and majority_count < (total_matched + 1) // 2:
        logger.info(
            "Clamp skipped: majority pattern %s has only %d/%d matches "
            "— fails 50%% rule (need ≥%d) — insufficient support for correction",
            majority,
            majority_count,
            total_matched,
            (total_matched + 1) // 2,
        )
        return [False] * len(scan_results)

    anomalies: list[bool] = []
    for r in scan_results:
        secs = _effective_seconds(r)
        if r.matched and secs is not None:
            anomalies.append(_pattern_key(secs) != majority)
        else:
            anomalies.append(False)
    return anomalies


def clamp_correct(
    scan_results: list[BrandSearchResult],
    adverts: list[AdvertMetadata],
    fps: float,
    majority_rule: bool = True,
) -> list[BrandSearchResult]:
    """Apply duration-based correction to anomalies flagged by clamp_check.

    Uses refined 25fps positions when available, falling back to 5fps.
    Snapping matches on ``sec_digit`` only (units digit of seconds),
    ignoring millis which varies with precision.

    Two strategies:
    - **Duration available**: compute expected frame from nearest trusted
      neighbours, then snap to the nearest frame matching the majority
      ``sec_digit`` pattern.
    - **No duration** (last advert): snap the original match to the nearest
      frame matching the majority pattern.
    """
    from collections import Counter

    # 1. Identify trusted indices and majority pattern
    counts: Counter[str] = Counter()
    for r in scan_results:
        secs = _effective_seconds(r)
        if r.matched and secs is not None:
            counts[_pattern_key(secs)] += 1
    if not counts:
        return scan_results
    majority = counts.most_common(1)[0][0]
    majority_count = counts[majority]
    total_matched = sum(counts.values())

    # Gate: same minimum-support checks as clamp_check.
    if majority_count < _CLAMP_MIN_MAJORITY_COUNT:
        logger.info(
            "Clamp corrector skipped: majority pattern %s has only "
            "%d/%d matches (need ≥%d)",
            majority,
            majority_count,
            total_matched,
            _CLAMP_MIN_MAJORITY_COUNT,
        )
        return scan_results
    if majority_rule and majority_count < (total_matched + 1) // 2:
        logger.info(
            "Clamp corrector skipped: majority pattern %s has only "
            "%d/%d matches (need ≥%d and ≥50%%)",
            majority,
            majority_count,
            total_matched,
            _CLAMP_MIN_MAJORITY_COUNT,
        )
        return scan_results

    trusted: set[int] = set()
    for i, r in enumerate(scan_results):
        secs = _effective_seconds(r)
        if r.matched and secs is not None:
            if _pattern_key(secs) == majority:
                trusted.add(i)

    snap_fps = fps
    k_sec_digit = int(majority.split(".")[0])

    def _snap(frame: int) -> int:
        """Snap *frame* to the nearest frame whose sec_digit matches the
        majority pattern.  Millis is not meaningful across different
        precision levels (5fps vs 25fps), so only sec_digit is used."""
        best = frame
        best_dist = abs(int(frame / fps * 1000) - (frame * 1000))
        for candidate in range(frame - 5 * int(fps), frame + 5 * int(fps) + 1):
            if candidate < 0:
                continue
            sec_digit = int(candidate / fps) % 10
            if sec_digit == k_sec_digit:
                dist = abs(candidate - frame)
                if dist < best_dist:
                    best = candidate
                    best_dist = dist
        return best

    results = list(scan_results)

    for i, r in enumerate(results):
        if not r.matched or r.last_match_seconds is None:
            continue
        if i in trusted:
            continue

        prev_trusted = max((j for j in trusted if j < i), default=None)
        next_trusted = min((j for j in trusted if j > i), default=None)
        dur = adverts[i].duration_seconds

        if dur is not None and prev_trusted is not None and next_trusted is not None:
            fwd = results[prev_trusted].last_match_frame
            for j in range(prev_trusted + 1, i + 1):
                if adverts[j].duration_seconds is not None:
                    fwd += int(adverts[j].duration_seconds * fps)
            bwd = results[next_trusted].last_match_frame
            for j in range(next_trusted, i, -1):
                if adverts[j].duration_seconds is not None:
                    bwd -= int(adverts[j].duration_seconds * fps)
            if abs(fwd - bwd) <= 1:
                snapped = _snap(fwd)
            elif abs(prev_trusted - i) <= 1 or abs(next_trusted - i) <= 1:
                if abs(prev_trusted - i) <= 1:
                    snapped = _snap(fwd)
                    logger.info(
                        "  %s fwd/bwd disagree (fwd=%d bwd=%d) — adjacent prev, trusting forward",
                        adverts[i].brand,
                        fwd,
                        bwd,
                    )
                else:
                    snapped = _snap(bwd)
                    logger.info(
                        "  %s fwd/bwd disagree (fwd=%d bwd=%d) — adjacent next, trusting backward",
                        adverts[i].brand,
                        fwd,
                        bwd,
                    )
            else:
                logger.info(
                    "  %s clamp forward=%d backward=%d disagree — keeping original",
                    adverts[i].brand,
                    fwd,
                    bwd,
                )
                continue
        elif dur is not None and prev_trusted is not None:
            expected = results[prev_trusted].last_match_frame
            for j in range(prev_trusted + 1, i + 1):
                if adverts[j].duration_seconds is not None:
                    expected += int(adverts[j].duration_seconds * fps)
            snapped = _snap(expected)
        elif dur is not None and next_trusted is not None:
            expected = results[next_trusted].last_match_frame
            for j in range(next_trusted, i, -1):
                if adverts[j].duration_seconds is not None:
                    expected -= int(adverts[j].duration_seconds * fps)
            snapped = _snap(expected)
        else:
            snapped = _snap(r.last_match_frame)

        if snapped != r.last_match_frame:
            dist = abs(snapped - r.last_match_frame)
            if dist <= 2:
                continue
            new_ts = snapped / fps
            results[i] = BrandSearchResult(
                matched=True,
                last_match_frame=snapped,
                last_match_seconds=new_ts,
                match_tier=r.match_tier,
                match_count=r.match_count,
                all_matching_frames=r.all_matching_frames,
                matched_terms=r.matched_terms,
                correction="duration" if dur is not None else "snap",
                original_last_match_frame=r.last_match_frame,
            )
            logger.info(
                "  %s corrected: frame %d → %d (%.3fs, pattern %s)",
                adverts[i].brand,
                r.last_match_frame,
                snapped,
                new_ts,
                majority,
            )

    return results


# ── 25fps end-frame refinement ───────────────────────────────────────────


def _text_similarity(a: str, b: str) -> float:
    """Return similarity ratio between two OCR texts (0.0 to 1.0).

    Normalises whitespace and case before comparing.
    """
    from difflib import SequenceMatcher

    a_norm = " ".join(a.strip().lower().split())
    b_norm = " ".join(b.strip().lower().split())
    if not a_norm and not b_norm:
        return 1.0
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


SIMILARITY_THRESHOLD = 0.5


def _refine_advert_end_frames(
    scan_results: list[BrandSearchResult],
    video_path: str,
    start_seconds: float,
    adverts: list[AdvertMetadata],
    ocr_endpoint: str,
    ocr_model: str,
    fps: float,
    ocr_results: list[dict] | None = None,
    output_dir: Path | None = None,
    verbose: bool = False,
) -> list[BrandSearchResult]:
    """Refine advert end positions by detecting text boundaries at 25fps.

    Extracts 5 source-rate frames (0.2s) starting at each advert's
    detected 5fps match frame.  Instead of looking for brand text,
    this uses **boundary detection**: it compares each refinement
    frame's OCR text to the current 5fps frame's text and the next
    5fps frame's text.  The last frame whose text matches the current
    frame is the refined end position.

    This handles cases where the brand text disappears before the
    advert ends (sponsorship endcards, logo-only screens, etc.).

    Diagnostic data is stored in ``r.refinement_data`` for QC HTML
    display.  When ``output_dir`` is provided, refinement frame
    images and OCR text are saved to ``output_dir / "refine" /``.

    Args:
        scan_results: Brand search results after clamp correction.
        video_path: Path to the local video file.
        start_seconds: Broadcast-absolute start of the frame range.
        adverts: Advert metadata list.
        ocr_endpoint: vLLM OCR endpoint URL.
        ocr_model: OCR model name.
        fps: 5fps frame extraction rate.
        ocr_results: OCR results from the 5fps pass (needed for
            current/next frame text comparison).
        output_dir: Directory to save refinement frames for inspection.
        verbose: Enable verbose logging.

    Returns:
        Updated scan results with ``refined_end_seconds`` and
        ``refinement_data`` set where refinement was applied.
    """
    if not scan_results or not video_path:
        return scan_results

    from ad_break_identifier.ocr_client import ocr_image

    # Cache video duration so we don't refine beyond the actual video.
    _video_duration: float | None = None

    def _duration() -> float:
        nonlocal _video_duration
        if _video_duration is None:
            import subprocess as _sp

            r = _sp.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            _video_duration = float(r.stdout.strip()) if r.returncode == 0 else 0.0
        return _video_duration

    matched_count = sum(
        1 for r in scan_results if r.matched and r.last_match_frame is not None
    )
    if matched_count == 0:
        return scan_results
    logger.info(
        "Refining %d advert end frame(s) at %d FPS...", matched_count, SOURCE_FPS
    )

    results = list(scan_results)

    # Prepare refinement output directory
    refine_save_dir: Path | None = None
    if output_dir:
        refine_save_dir = output_dir / "refine"
        refine_save_dir.mkdir(parents=True, exist_ok=True)

    for i, r in enumerate(results):
        if not r.matched or r.last_match_frame is None:
            continue

        match_frame = r.last_match_frame
        T_clip = match_frame / fps
        T_broadcast = start_seconds + T_clip

        brand = adverts[i].brand if i < len(adverts) else ""
        if not brand:
            continue

        # Get current and next 5fps frame OCR text
        current_text = ""
        next_text = ""
        if ocr_results and 0 <= match_frame < len(ocr_results):
            current_text = ocr_results[match_frame].get("text", "") or ""
        if ocr_results and 0 <= match_frame + 1 < len(ocr_results):
            next_text = ocr_results[match_frame + 1].get("text", "") or ""

        # Determine whether a boundary exists in the refinement window.
        # A boundary is present when the current 5fps frame has text and
        # either (a) the next 5fps frame has dissimilar text, or (b) the
        # next 5fps frame has no text at all (e.g. channel ident, black
        # frame, transition).  When both texts are similar, the boundary
        # is outside the window and refinement cannot help.
        current_next_sim = _text_similarity(current_text, next_text)
        has_boundary = bool(
            current_text and (not next_text or current_next_sim < SIMILARITY_THRESHOLD)
        )

        # Skip refinement if the seek position is beyond the video.
        vid_dur = _duration()
        if T_broadcast + 0.2 > vid_dur:
            logger.info(
                "  %s: T_broadcast=%.3fs exceeds video duration "
                "(%.3fs), skipping refinement",
                brand,
                T_broadcast,
                vid_dur,
            )
            r.refined_end_seconds = None
            r.refinement_data = {
                "brand": brand,
                "T_clip": round(T_clip, 3),
                "T_broadcast": round(T_broadcast, 3),
                "status": "beyond_video",
                "refined_end_seconds": None,
            }
            continue

        # Create temp directory for refinement frames
        refine_dir = Path(tempfile.mkdtemp(suffix="_refine"))
        try:
            pattern = str(refine_dir / "refine_%05d.png")
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{T_broadcast:.6f}",
                "-i",
                video_path,
                "-t",
                "0.2",
                "-r",
                str(SOURCE_FPS),
                "-frame_pts",
                "1",
                "-pix_fmt",
                "rgb24",
                pattern,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0 or "Output file is empty" in result.stderr:
                logger.warning(
                    "  %s: refinement FFmpeg failed (rc=%d): %s",
                    brand,
                    result.returncode,
                    result.stderr[-200:].strip() if result.stderr else "",
                )

            refine_frames = sorted(refine_dir.glob("refine_*.png"))
            if len(refine_frames) < 5:
                logger.warning(
                    "  %s: refinement produced only %d frames (expected 5), skipping",
                    brand,
                    len(refine_frames),
                )
                r.refinement_data = {
                    "brand": brand,
                    "T_clip": round(T_clip, 3),
                    "T_broadcast": round(T_broadcast, 3),
                    "current_5fps_text": current_text,
                    "next_5fps_text": next_text,
                    "current_next_similarity": round(current_next_sim, 3),
                    "has_boundary": has_boundary,
                    "method": "boundary_detection",
                    "status": "insufficient_frames",
                    "frame_count": len(refine_frames),
                    "frames": [],
                }
                continue

            candidate_frames = refine_frames[:5]

            # OCR candidate frames
            import requests

            session = requests.Session()
            ocr_texts: list[str] = []
            for cf in candidate_frames:
                try:
                    text = ocr_image(
                        image_path=cf,
                        endpoint=ocr_endpoint,
                        model=ocr_model,
                        session=session,
                    )
                    ocr_texts.append(text)
                except Exception:
                    ocr_texts.append("")

            # Save refinement frames and OCR text if output_dir provided
            if refine_save_dir:
                import shutil

                safe_brand = re.sub(r'[\\/:*?"<>|]', "_", brand)
                for j, cf in enumerate(candidate_frames):
                    saved = refine_save_dir / f"{safe_brand}_frame_{j}.png"
                    shutil.move(str(cf), str(saved))
                refine_json_path = refine_save_dir / f"{safe_brand}_refinement.json"
                import json as _json

                refine_data_for_save = {
                    "brand": brand,
                    "T_clip": round(T_clip, 3),
                    "T_broadcast": round(T_broadcast, 3),
                    "current_5fps_text": current_text,
                    "next_5fps_text": next_text,
                    "current_next_similarity": round(current_next_sim, 3),
                    "has_boundary": has_boundary,
                    "method": "boundary_detection",
                    "frames": [
                        {
                            "index": j,
                            "time_offset": round(j * (1.0 / SOURCE_FPS), 3),
                            "ocr_text": text,
                            "sim_to_current": round(
                                _text_similarity(text, current_text), 3
                            ),
                            "sim_to_next": round(_text_similarity(text, next_text), 3),
                        }
                        for j, text in enumerate(ocr_texts)
                    ],
                }
                refine_json_path.write_text(
                    _json.dumps(refine_data_for_save, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            # Boundary detection: find the last refinement frame whose text
            # is similar to the current 5fps frame's text (still in the same ad).
            last_current_idx: int | None = None
            frame_sim_data: list[dict] = []
            for j, text in enumerate(ocr_texts):
                sim_current = _text_similarity(text, current_text)
                sim_next = _text_similarity(text, next_text)
                is_current = sim_current >= SIMILARITY_THRESHOLD
                if is_current:
                    last_current_idx = j
                frame_sim_data.append(
                    {
                        "index": j,
                        "time_offset": round(j * (1.0 / SOURCE_FPS), 3),
                        "ocr_text": text,
                        "sim_to_current": round(sim_current, 3),
                        "sim_to_next": round(sim_next, 3),
                        "is_current_ad": is_current,
                    }
                )

            # Build refinement_data for QC HTML
            r.refinement_data = {
                "brand": brand,
                "T_clip": round(T_clip, 3),
                "T_broadcast": round(T_broadcast, 3),
                "current_5fps_text": current_text,
                "next_5fps_text": next_text,
                "current_next_similarity": round(current_next_sim, 3),
                "has_boundary": has_boundary,
                "method": "boundary_detection",
                "frames": frame_sim_data,
            }

            if not has_boundary:
                # Current and next 5fps texts are similar — no boundary
                # in this gap, refinement can't help.
                r.refinement_data["status"] = "no_boundary"
                r.refinement_data["refined_end_seconds"] = None
                logger.info(
                    "  %s: no boundary (5fps texts similar: %.2f), "
                    "keeping original end at %.3fs",
                    brand,
                    current_next_sim,
                    r.last_match_seconds,
                )
                continue

            if last_current_idx is not None:
                r.refined_end_seconds = T_clip + last_current_idx * (1.0 / SOURCE_FPS)
                adjusted = last_current_idx > 0
                r.refinement_data["status"] = "refined"
                r.refinement_data["refined_end_seconds"] = round(
                    r.refined_end_seconds, 3
                )
                r.refinement_data["last_current_idx"] = last_current_idx
                if adjusted:
                    logger.info(
                        "  %s: refined end from %.3fs to %.3fs (+%d source frame(s))",
                        brand,
                        r.last_match_seconds,
                        r.refined_end_seconds,
                        last_current_idx,
                    )
                else:
                    logger.info(
                        "  %s: refinement checked, no change (confirmed at %.3fs)",
                        brand,
                        r.last_match_seconds,
                    )
            else:
                # No refinement frame matched the current text — boundary
                # is at or before the 5fps match frame.
                r.refinement_data["status"] = "boundary_before_match"
                r.refinement_data["refined_end_seconds"] = None
                logger.warning(
                    "  %s: boundary before match frame, keeping original end",
                    brand,
                )

        except subprocess.TimeoutExpired:
            logger.warning("Refinement FFmpeg timed out for %s", brand)
            r.refinement_data = {
                "brand": brand,
                "T_clip": round(T_clip, 3),
                "T_broadcast": round(T_broadcast, 3),
                "method": "boundary_detection",
                "status": "ffmpeg_timeout",
                "frames": [],
            }
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Refinement FFmpeg failed for %s: %s", brand, e.stderr[-300:]
            )
            r.refinement_data = {
                "brand": brand,
                "T_clip": round(T_clip, 3),
                "T_broadcast": round(T_broadcast, 3),
                "method": "boundary_detection",
                "status": "ffmpeg_failed",
                "error": e.stderr[-300:],
                "frames": [],
            }
        except Exception as e:
            logger.warning("Refinement failed for %s: %s", brand, e)
            r.refinement_data = {
                "brand": brand,
                "T_clip": round(T_clip, 3),
                "T_broadcast": round(T_broadcast, 3),
                "method": "boundary_detection",
                "status": "error",
                "error": str(e),
                "frames": [],
            }
        finally:
            for f in refine_dir.glob("*.png"):
                f.unlink(missing_ok=True)
            try:
                refine_dir.rmdir()
            except OSError:
                pass

    return results


# ── XML output ────────────────────────────────────────────────────────────


def _escape_xml(text: str) -> str:
    return html.escape(text, quote=True)


def format_xml(
    ad_metadata: AdBreakMetadata,
    scan_results: list[BrandSearchResult],
    fps: float = DEFAULT_FPS,
) -> str:
    """Format detection results as XML.

    Each ``<advert>`` contains ``<unique_id>``, ``<brand>``,
    ``<advertiser>``, ``<category>``, ``<duration_seconds>``,
    ``<start_timecode>`` (clip-relative, computed from OCR frames),
    ``<last_timecode>`` (clip-relative), ``<match_tier>``, and
    ``<ocr_match_fallback>`` if no match was found.

    For adverts with known durations, start is derived from the end frame
    minus duration.  For the last advert in a multi-advert break (no
    duration), start is one frame after the preceding advert's last frame.
    Single-advert breaks and unmatched adverts omit ``<start_timecode>``.
    """
    lines = ["<ad_break>"]
    lines.append(f"    <!-- OCR-based detection (5 FPS, PaddleOCR-VL) -->")
    lines.append(f"    <!-- Generated: {datetime.now().isoformat()} -->")

    prev_effective_end: float | None = None
    for i, adv in enumerate(ad_metadata.adverts):
        scan = scan_results[i] if i < len(scan_results) else None

        lines.append("    <advert>")
        lines.append(f"        <unique_id>{_escape_xml(adv.unique_id)}</unique_id>")
        lines.append(f"        <brand>{_escape_xml(adv.brand)}</brand>")
        lines.append(f"        <advertiser>{_escape_xml(adv.advertiser)}</advertiser>")
        lines.append(f"        <category>{_escape_xml(adv.category)}</category>")

        if scan and scan.last_match_seconds is not None:
            effective_end = (
                scan.refined_end_seconds
                if scan.refined_end_seconds is not None
                else scan.last_match_seconds
            )
            duration_scheduled = adv.duration_seconds

            if duration_scheduled is not None:
                start_seconds = effective_end - duration_scheduled + (3.0 / SOURCE_FPS)
                dur_to_write = duration_scheduled
            elif prev_effective_end is not None:
                start_seconds = prev_effective_end + (3.0 / SOURCE_FPS)
                detected_dur = effective_end - start_seconds
                dur_to_write = round(detected_dur)
            else:
                start_seconds = None
                dur_to_write = None

            if dur_to_write is not None:
                lines.append(
                    f"        <duration_seconds>{dur_to_write}</duration_seconds>"
                )

            if start_seconds is not None:
                start_tc = seconds_to_timecode(start_seconds)
                lines.append(
                    f"        <start_timecode>{_escape_xml(start_tc)}</start_timecode>"
                )

            tc = seconds_to_timecode(effective_end)
            lines.append(f"        <last_timecode>{_escape_xml(tc)}</last_timecode>")
            lines.append(f"        <match_tier>{scan.match_tier}</match_tier>")
            if scan.matched_terms:
                lines.append(
                    f"        <matched_terms>{_escape_xml(', '.join(scan.matched_terms))}</matched_terms>"
                )
            if scan.correction:
                lines.append(
                    f"        <correction>{_escape_xml(scan.correction)}</correction>"
                )

            prev_effective_end = effective_end
        else:
            if adv.duration_seconds is not None:
                lines.append(
                    f"        <duration_seconds>{adv.duration_seconds}</duration_seconds>"
                )
            lines.append(f"        <last_timecode></last_timecode>")

        if scan and not scan.matched:
            lines.append(f"        <ocr_match_fallback>true</ocr_match_fallback>")
            lines.append(
                f"        <description>OCR: no text match for {_escape_xml(adv.brand)}/{_escape_xml(adv.advertiser)}</description>"
            )

        lines.append("    </advert>")

    lines.append("</ad_break>")
    return "\n".join(lines) + "\n"


# ── QC HTML report ──────────────────────────────────────────────────────────


_QC_CSS = """
html { transition: background 0.3s, color 0.3s; }
:root {
  --bg: #2e3440; --bg-card: #3b4252; --bg-code: #242933;
  --fg: #eceff4; --fg-muted: #81a1c1; --border: #4c566a;
  --accent: #88c0d0; --link: #88c0d0;
  --match-bg: #3a4a3a; --match-border: #a3be8c; --match-fg: #a3be8c;
  --fail-bg: #3a2e34; --fail-fg: #bf616a; --fail-border: #bf616a;
}
[data-theme="light"] {
  --bg: #eceff4; --bg-card: #ffffff; --bg-code: #e5e9f0;
  --fg: #2e3440; --fg-muted: #5e81ac; --border: #d8dee9;
  --accent: #5e81ac; --link: #5e81ac;
  --match-bg: #e8f0e8; --match-border: #a3be8c; --match-fg: #3a6a3a;
  --fail-bg: #f0e8e8; --fail-fg: #b84a4a; --fail-border: #bf616a;
}
[data-theme="gruvbox"] {
  --bg: #1d2021; --bg-card: #282828; --bg-code: #0d0d0d;
  --fg: #fbf1c7; --fg-muted: #a89984; --border: #665c54;
  --accent: #fabd2f; --link: #8ec07c;
  --match-bg: #1d221d; --match-border: #b8bb26; --match-fg: #b8bb26;
  --fail-bg: #221d1d; --fail-fg: #fb4934; --fail-border: #fb4934;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--fg); padding: 20px; line-height: 1.5; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
.subtitle { color: var(--fg-muted); font-size: 0.85rem; margin-bottom: 16px; }
.theme-nav { display: flex; gap: 8px; margin-bottom: 20px; }
.theme-btn { padding: 4px 12px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-card); color: var(--fg); cursor: pointer; font-size: 0.8rem; }
.theme-btn.active { border-color: var(--accent); color: var(--accent); }
.toc { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 16px 20px; margin-bottom: 24px; }
.toc h2 { font-size: 1rem; margin-bottom: 8px; }
.toc ol { padding-left: 20px; }
.toc a { color: var(--link); text-decoration: none; }
.toc a:hover { text-decoration: underline; }
details.advert-section { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 20px; padding: 0; }
details.advert-section[open] { padding-bottom: 6px; }
details.advert-section summary { padding: 14px 20px; cursor: pointer; font-size: 1.1rem; font-weight: 600; border-radius: 6px; list-style: none; display: flex; align-items: center; gap: 8px; }
details.advert-section summary::-webkit-details-marker { display: none; }
details.advert-section summary::before { content: "\u25b6"; font-size: 0.7rem; color: var(--accent); transition: transform 0.2s; }
details.advert-section[open] summary::before { content: "\u25bc"; }
details.advert-section summary:hover { background: var(--border); }
details.advert-section .advert-body { padding: 0 20px 12px; }
details.advert-section .advert-meta { color: var(--fg-muted); font-size: 0.85rem; margin-bottom: 12px; }
.frame-row { display: flex; gap: 16px; padding: 8px 12px; border-bottom: 1px solid var(--border); align-items: stretch; }
.frame-row:last-child { border-bottom: none; }
.frame-row.match { background: var(--match-bg); border-left: 4px solid var(--match-border); border-radius: 4px; margin: 4px 0; }
.frame-row.original-match { background: var(--diff-bg); border-left: 4px solid var(--diff-border); border-radius: 4px; margin: 4px 0; }
.frame-row img { width: 160px; height: auto; border-radius: 4px; flex-shrink: 0; object-fit: contain; }
.frame-info { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.frame-tc { font-weight: 600; font-size: 0.85rem; color: var(--accent); }
.match-badge { display: inline-block; background: var(--match-border); color: var(--bg); font-size: 0.7rem; font-weight: 700; padding: 1px 8px; border-radius: 3px; margin-left: 8px; }
.fallback-badge { display: inline-block; background: var(--fail-border); color: var(--bg); font-size: 0.7rem; font-weight: 700; padding: 1px 8px; border-radius: 3px; }
.original-badge { display: inline-block; background: var(--diff-border); color: var(--bg); font-size: 0.7rem; font-weight: 700; padding: 1px 8px; border-radius: 3px; margin-left: 8px; }
.correction-badge { display: inline-block; background: var(--accent); color: var(--bg); font-size: 0.7rem; font-weight: 700; padding: 1px 8px; border-radius: 3px; margin-left: 4px; }
.frame-text { background: var(--bg-code); border-radius: 4px; padding: 8px; font-family: "SF Mono", "Fira Code", monospace; font-size: 0.78rem; white-space: pre-wrap; word-break: break-word; overflow-x: auto; max-height: 120px; overflow-y: auto; }
.back-to-top { text-align: right; font-size: 0.8rem; margin-top: 8px; }
.back-to-top a { color: var(--link); text-decoration: none; }
.footer { text-align: center; color: var(--fg-muted); font-size: 0.75rem; margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border); }
.refine-section { margin-top: 12px; }
.refine-section summary { font-size: 0.85rem; color: var(--fg-muted); cursor: pointer; padding: 4px 0; }
.refine-body { padding: 8px 0 4px; }
.refine-meta { font-size: 0.8rem; color: var(--fg-muted); margin-bottom: 8px; line-height: 1.6; }
.refine-row { display: flex; gap: 12px; align-items: center; padding: 2px 0; font-size: 0.78rem; }
.refine-tc { color: var(--accent); font-weight: 600; min-width: 70px; }
.refine-sim { color: var(--fg-muted); font-size: 0.72rem; }
.refine-badge { display: inline-block; background: var(--match-border); color: var(--bg); font-size: 0.65rem; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-left: 4px; }
.refine-ok { color: var(--match-fg); font-weight: 600; }
.refine-warn { color: var(--fail-fg); font-weight: 600; }
"""

_QC_JS = """
(function() {
  var btns = document.querySelectorAll('.theme-btn');
  var root = document.documentElement;
  function setTheme(t) {
    root.setAttribute('data-theme', t);
    btns.forEach(function(b) { b.classList.toggle('active', b.dataset.theme === t); });
    try { localStorage.setItem('report-theme', t); } catch(e) {}
  }
  var initial = 'dark';
  try {
    var saved = localStorage.getItem('report-theme');
    if (saved) initial = saved;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) initial = 'light';
  } catch(e) {}
  setTheme(initial);
  btns.forEach(function(b) { b.addEventListener('click', function() { setTheme(b.dataset.theme); }); });
  var hash = window.location.hash;
  if (hash) {
    var el = document.getElementById(hash.slice(1));
    if (el && el.tagName === 'DETAILS') el.open = true;
  }
  window.addEventListener('hashchange', function() {
    var h = window.location.hash;
    if (h) {
      var e = document.getElementById(h.slice(1));
      if (e && e.tagName === 'DETAILS') e.open = true;
    }
  });
})();
"""


def _thumbnail_data_uri(src_path: Path, width: int = 160) -> str:
    """Generate a resized thumbnail and return it as a base64 data URI."""
    try:
        from PIL import Image
        import base64
        from io import BytesIO

        with Image.open(src_path) as img:
            w_percent = width / float(img.size[0])
            height = int(float(img.size[1]) * w_percent)
            thumb = img.resize((width, height), Image.LANCZOS)
            thumb = thumb.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
            buf = BytesIO()
            thumb.save(buf, "PNG", optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.warning("Thumbnail failed for %s: %s", src_path.name, exc)
        return ""


def generate_qc_html(
    ocr_results: list[dict],
    scan_results: list[BrandSearchResult],
    adverts: list[AdvertMetadata],
    fps: float,
    output_dir: Path,
    frames_dir: Path,
    start_seconds: float = 0.0,
) -> str:
    """Generate a themed QC HTML report with frame images and OCR text.

    For each advert with a match, shows 10 frames before and 10 frames
    after the last matching frame, with the matched frame highlighted.
    Thumbnails are embedded as base64 data URIs for fast single-file
    loading in a browser.
    """
    toc_entries: list[str] = []
    sections: list[str] = []
    frame_count = len(ocr_results)

    for i, adv in enumerate(adverts):
        scan = scan_results[i] if i < len(scan_results) else None
        anchor = f"advert-{i}"
        brand_esc = html.escape(adv.brand)
        advertiser_esc = html.escape(adv.advertiser)
        status = ""
        meta_extra = ""

        if scan and scan.matched:
            match_frame = scan.last_match_frame
            orig_frame = scan.original_last_match_frame
            assert match_frame is not None

            # Determine frame range: show ±10 around the corrected frame,
            # but also include the original anomalous frame if outside.
            start = max(0, match_frame - 10)
            end = min(frame_count - 1, match_frame + 10)
            if orig_frame is not None and orig_frame is not match_frame:
                start = min(start, max(0, orig_frame - 2))
                end = max(end, min(frame_count - 1, orig_frame + 2))

            tier_label = scan.match_tier
            terms = ", ".join(scan.matched_terms) if scan.matched_terms else ""
            meta_extra = f"Match tier: <strong>{tier_label}</strong>"
            if scan.correction:
                meta_extra += f' <span class="correction-badge">&#x2705; { scan.correction } corrected</span>'
            if (
                scan.refined_end_seconds is not None
                and scan.refined_end_seconds != scan.last_match_seconds
            ):
                meta_extra += (
                    f' <span class="correction-badge">&#x1F50D; refined: '
                    f"{seconds_to_timecode(scan.refined_end_seconds)}</span>"
                )
            if terms:
                meta_extra += (
                    f" &mdash; matched terms: <code>{html.escape(terms)}</code>"
                )

            frame_rows: list[str] = []
            for idx in range(start, end + 1):
                ocr_res = ocr_results[idx]
                frame_name = ocr_res["frame_name"]
                clip_ts = idx / fps
                bcast_ts = start_seconds + clip_ts
                text = ocr_res.get("text", "") or ""
                text_esc = html.escape(text)

                tc_clip = seconds_to_timecode(clip_ts)
                tc_bcast = seconds_to_timecode(bcast_ts)

                is_match = idx == match_frame
                is_orig = (
                    orig_frame is not None and idx == orig_frame and idx != match_frame
                )

                if is_match:
                    row_cls = ' class="match"'
                    badge = '<span class="match-badge">MATCH</span>'
                elif is_orig:
                    row_cls = ' class="original-match"'
                    badge = f'<span class="original-badge">&#x26A0; Original (frame { orig_frame })</span>'
                else:
                    row_cls = ""
                    badge = ""

                # Generate thumbnail as base64 data URI
                src = frames_dir / frame_name
                img_data = _thumbnail_data_uri(src)
                img_tag = f'<img src="{ img_data }" alt="Frame {idx}" loading="lazy">'

                row = f"""<div{ row_cls }>
  <div class="frame-row">
    { img_tag }
    <div class="frame-info">
      <div class="frame-tc">Frame {idx:05d} &mdash; Clip TC: {tc_clip} &mdash; Broadcast TC: {tc_bcast}{ badge }</div>
      <div class="frame-text">{ text_esc }</div>
    </div>
  </div>
</div>"""
                frame_rows.append(row)

            frames_html = "\n".join(frame_rows)

            # Build refinement diagnostic section
            refine_html = ""
            if scan.refinement_data:
                rdata = scan.refinement_data
                method = rdata.get("method", "")
                status = rdata.get("status", "unknown")
                frames_info = rdata.get("frames", [])
                cur_text = html.escape(rdata.get("current_5fps_text", "") or "")
                nxt_text = html.escape(rdata.get("next_5fps_text", "") or "")
                sim = rdata.get("current_next_similarity", 0.0)
                has_bnd = rdata.get("has_boundary", False)
                refined_val = rdata.get("refined_end_seconds")

                status_class = "refine-ok" if status == "refined" else "refine-warn"
                refine_rows: list[str] = []
                for finfo in frames_info:
                    j = finfo.get("index", 0)
                    off = finfo.get("time_offset", 0.0)
                    ftext = html.escape(finfo.get("ocr_text", "") or "")
                    sc = finfo.get("sim_to_current", 0.0)
                    sn = finfo.get("sim_to_next", 0.0)
                    is_cur = finfo.get("is_current_ad", False)
                    badge = ""
                    if is_cur:
                        badge = '<span class="refine-badge">CURRENT</span>'
                    refine_rows.append(
                        f'<div class="refine-row">'
                        f'<span class="refine-tc">+{off:.3f}s</span>'
                        f'<span class="refine-sim">cur={sc:.2f} nxt={sn:.2f}</span>'
                        f"{badge}</div>"
                        f'<div class="frame-text">{ftext}</div>'
                    )
                refine_rows_html = "\n".join(refine_rows)

                refined_display = ""
                if refined_val is not None:
                    refined_display = (
                        f"<strong>Refined end:</strong> "
                        f"{seconds_to_timecode(refined_val)} "
                        f"(+{rdata.get('last_current_idx', 0)} frames)"
                    )

                refine_html = f"""<details class="refine-section">
  <summary>25fps Refinement ({method}, {status})</summary>
  <div class="refine-body">
    <div class="refine-meta">
      <strong>Current 5fps text:</strong> <code>{cur_text}</code><br>
      <strong>Next 5fps text:</strong> <code>{nxt_text}</code><br>
      <strong>Similarity:</strong> {sim:.3f} &mdash; boundary: {has_bnd}
      &mdash; <span class="{status_class}">{status}</span>
      {refined_display}
    </div>
    {refine_rows_html}
  </div>
</details>"""

            summary_status = f'<span class="status-pass">&#x2713; { tier_label }</span>'
            if scan.correction:
                summary_status += (
                    f' <span class="correction-badge">&#x2705; corrected</span>'
                )
            section = f"""<details class="advert-section" id="{ anchor }">
  <summary>{ brand_esc } { summary_status }</summary>
  <div class="advert-body">
    <div class="advert-meta">Advertiser: { advertiser_esc } &mdash; { meta_extra }</div>
    { frames_html }
    {refine_html}
    <div class="back-to-top"><a href="#toc">&uarr; Back to top</a></div>
  </div>
</details>"""
            status = "YES"
        else:
            section = f"""<details class="advert-section" id="{ anchor }">
  <summary>{ brand_esc } <span class="status-fail">&#x2717; no match</span></summary>
  <div class="advert-body">
    <div class="advert-meta">Advertiser: { advertiser_esc }</div>
    <p style="color: var(--fg-muted); padding: 12px 0;">No match found for this advert.</p>
    <div class="back-to-top"><a href="#toc">&uarr; Back to top</a></div>
  </div>
</details>"""
            status = "NO"

        toc_text = brand_esc
        if scan and scan.correction and scan.original_last_match_frame is not None:
            toc_text += f" (corrected: { scan.original_last_match_frame } &rarr; { scan.last_match_frame })"
        toc_entries.append(
            f'<li><a href="#{ anchor }" onclick="document.getElementById(\'{ anchor }\').open=true">{ toc_text }</a> &mdash; { status }</li>'
        )
        sections.append(section)

    toc_html = "\n".join(toc_entries)
    sections_html = "\n".join(sections)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html_str = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QC Report &mdash; { html.escape(output_dir.name) }</title>
<style>{ _QC_CSS }</style>
</head>
<body>

<h1>OCR approach instead of Vision Language Model &mdash; QC Report &mdash; { html.escape(output_dir.name) }</h1>
<div class="subtitle">Generated: { now } &mdash; { frame_count } frames at { fps } FPS</div>

<div class="theme-nav">
  <button class="theme-btn" data-theme="light">Light</button>
  <button class="theme-btn active" data-theme="dark">Dark</button>
  <button class="theme-btn" data-theme="gruvbox">Gruvbox</button>
</div>

<div class="toc" id="toc">
  <h2>Contents &mdash; Advert Matches</h2>
  <ol>{ toc_html }</ol>
</div>

{ sections_html }

<div class="footer">Advert Identifier OCR &mdash; PaddleOCR-VL @ { fps } FPS</div>

<script>{ _QC_JS }</script>

</body>
</html>"""

    return html_str


# ── Pipeline state update ────────────────────────────────────────────────

_TIER_SCORES: dict[str, float] = {
    "exact": 1.0,
    "words": 0.75,
    "subwords": 0.5,
    "advertiser": 0.3,
    "anchor": 0.6,
    "anchor-verified": 0.8,
    "estimated": 0.1,
    "fallback": 0.0,
}


def _tier_base(tier: str) -> str:
    return tier.split("(")[0].strip()


_TIER_RANK: dict[str, int] = {
    "exact": 5,
    "words": 4,
    "subwords": 3,
    "advertiser": 2,
    "fallback": 0,
}


def anchor_based_reestimation(
    scan_results: list[BrandSearchResult],
    ad_metadata: AdBreakMetadata,
    ocr_results: list[dict],
    fps: float = DEFAULT_FPS,
    after_secs: float = DEFAULT_AFTER_SECS,
    before_secs: float = DEFAULT_BEFORE_SECS,
) -> list[BrandSearchResult]:
    """Re-estimate advert positions using the strongest OCR match as an anchor.

    Selects the match with the highest tier (exact > words > subwords >
    advertiser) as the trusted anchor, then computes all other advert
    positions from known durations.  Each predicted window is validated
    against OCR text — if brand words are found, the last matching frame
    is used (``anchor-verified`` tier); otherwise the duration-based
    position is used (``anchor`` tier).

    Returns a new list of :class:`BrandSearchResult` objects.
    """
    adverts = ad_metadata.adverts
    n = len(adverts)
    if n == 0:
        return scan_results

    matched = [
        (i, r)
        for i, r in enumerate(scan_results)
        if r.matched and r.last_match_frame is not None
    ]
    if not matched:
        logger.info("  Anchor re-estimation: no matched adverts, cannot anchor")
        return scan_results

    def _effective_end(r: BrandSearchResult) -> float:
        # Use raw Pass 1 match position only — never clamp-corrected or
        # refined positions, which may have been shifted by the forward-
        # backward clamp corrector.
        if r.last_match_seconds is not None:
            return r.last_match_seconds
        return 0.0

    best_idx, best_result = max(
        matched,
        key=lambda pair: (
            _TIER_RANK.get(_tier_base(pair[1].match_tier), 0),
            pair[1].match_count,
            -pair[0],
        ),
    )
    anchor_end = _effective_end(best_result)
    logger.info(
        "  Anchor re-estimation: using advert %d (%s, tier=%s, end=%.1fs) as anchor",
        best_idx + 1,
        adverts[best_idx].brand,
        best_result.match_tier,
        anchor_end,
    )

    durations = []
    for adv in adverts:
        d = adv.duration_seconds
        if d is None:
            d = 30
        durations.append(d)

    starts: list[float] = [0.0] * n
    ends: list[float] = [0.0] * n
    starts[best_idx] = anchor_end - durations[best_idx]
    ends[best_idx] = anchor_end

    for i in range(best_idx - 1, -1, -1):
        ends[i] = starts[i + 1]
        starts[i] = ends[i] - durations[i]

    for i in range(best_idx + 1, n):
        starts[i] = ends[i - 1]
        ends[i] = starts[i] + durations[i]

    extract_window = before_secs + after_secs
    new_results: list[BrandSearchResult] = []

    for i, adv in enumerate(adverts):
        pred_start = starts[i]
        pred_end = ends[i]
        start_frame = int(pred_start * fps)
        end_frame = int(pred_end * fps)

        patterns = build_exact_patterns(adv.brand)
        sub_patterns = build_substring_patterns(adv.brand)

        verified_frames: list[int] = []
        verified_terms: set[str] = set()
        for ocr_res in ocr_results:
            fi = ocr_res["frame_index"]
            if fi < start_frame or fi > end_frame:
                continue
            text = ocr_res.get("text", "")
            matched_ok, terms = match_ocr_text(text, patterns)
            if not matched_ok:
                matched_ok, terms = match_ocr_text(text, sub_patterns)
            if matched_ok:
                verified_frames.append(fi)
                verified_terms.update(t.lower() for t in terms)

        if verified_frames:
            last_f = verified_frames[-1]
            last_s = last_f / fps
            new_results.append(
                BrandSearchResult(
                    matched=True,
                    last_match_frame=last_f,
                    last_match_seconds=last_s,
                    match_tier="anchor-verified",
                    match_count=len(verified_frames),
                    all_matching_frames=verified_frames,
                    matched_terms=list(verified_terms),
                    correction=f"anchor from advert {best_idx + 1} ({adverts[best_idx].brand})",
                )
            )
            logger.info(
                "  Anchor: advert %d (%s) verified at frame %d (%.1fs)",
                i + 1,
                adv.brand,
                last_f,
                last_s,
            )
        else:
            outside = pred_start < 0 or pred_end > extract_window
            tier = "anchor" if not outside else "anchor"
            new_results.append(
                BrandSearchResult(
                    matched=False,
                    last_match_frame=end_frame,
                    last_match_seconds=pred_end,
                    match_tier=tier,
                    match_count=0,
                    all_matching_frames=[],
                    matched_terms=[],
                    correction=(
                        f"anchor from advert {best_idx + 1}"
                        f"{' (outside OCR window)' if outside else ''}"
                    ),
                )
            )
            logger.info(
                "  Anchor: advert %d (%s) estimated at %.1fs%s",
                i + 1,
                adv.brand,
                pred_end,
                " (outside OCR window)" if outside else "",
            )

    return new_results


def compute_break_confidence(
    scan_results: list[BrandSearchResult],
    ad_metadata: AdBreakMetadata,
    fps: float = DEFAULT_FPS,
    after_secs: float = DEFAULT_AFTER_SECS,
    before_secs: float = DEFAULT_BEFORE_SECS,
) -> dict[str, Any]:
    """Compute a per-break confidence score (0.0–1.0).

    Returns a dict with ``score`` (float), ``matched_count``,
    ``total_count``, and a list of ``reasons`` (strings explaining
    any deductions).
    """
    total = len(ad_metadata.adverts)
    if total == 0:
        return {"score": 0.0, "matched_count": 0, "total_count": 0, "reasons": []}

    matched = [r for r in scan_results if r.matched and r.last_match_frame is not None]
    matched_count = len(matched)
    match_ratio = matched_count / total

    avg_tier = 0.0
    if scan_results:
        tier_scores = [
            _TIER_SCORES.get(_tier_base(r.match_tier), 0.0) for r in scan_results
        ]
        avg_tier = sum(tier_scores) / len(tier_scores)

    first_tier = _tier_base(scan_results[0].match_tier) if scan_results else "fallback"
    first_ok = first_tier in ("exact", "words", "subwords")

    extract_window = before_secs + after_secs
    outside_window = any(
        r.last_match_seconds is not None and r.last_match_seconds > extract_window
        for r in scan_results
    )

    spacing_ok = True
    if matched_count >= 2:
        frames = [r.last_match_frame for r in matched if r.last_match_frame is not None]
        if frames:
            actual_span = (max(frames) - min(frames)) / fps
            expected_span = sum(
                adv.duration_seconds or 30 for adv in ad_metadata.adverts[:-1]
            )
            if expected_span > 0:
                ratio = actual_span / expected_span
                if ratio < 0.3:
                    spacing_ok = False

    score = 0.0
    reasons: list[str] = []

    score += 0.30 * match_ratio
    if match_ratio < 0.5:
        reasons.append(f"low match ratio ({matched_count}/{total})")

    score += 0.30 * avg_tier
    if avg_tier < 0.4:
        reasons.append(f"low average tier score ({avg_tier:.2f})")

    score += 0.20 if spacing_ok else 0.0
    if not spacing_ok:
        reasons.append("matched adverts clustered too closely")

    score += 0.10 if first_ok else 0.0
    if not first_ok:
        reasons.append(f"first advert weak match tier ({first_tier})")

    score += 0.10 if not outside_window else 0.0
    if outside_window:
        reasons.append("advert position exceeds extraction window")

    return {
        "score": round(score, 3),
        "matched_count": matched_count,
        "total_count": total,
        "reasons": reasons,
    }


def update_pipeline_state(
    metadata_file: str,
    ad_break_index: int,
    scan_results: list[BrandSearchResult],
    ad_metadata: AdBreakMetadata,
    fps: float,
    after_secs: float = DEFAULT_AFTER_SECS,
) -> None:
    """Update pipeline state JSON with detection results."""
    try:
        from .pipeline_state import (
            derive_state_path,
            read_state,
            write_state,
            update_break_adverts,
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
            secs_clip = (
                scan.refined_end_seconds
                if scan.refined_end_seconds is not None
                else scan.last_match_seconds
            )
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
        update_break_adverts(state, ad_break_index, updates, fps, after_secs)

        confidence = compute_break_confidence(
            scan_results, ad_metadata, fps, after_secs
        )
        break_data = state["ad_breaks"][ad_break_index - 1]
        break_data["confidence"] = confidence
        if confidence["reasons"]:
            logger.warning(
                "  Break %d confidence %.3f — %s",
                ad_break_index,
                confidence["score"],
                "; ".join(confidence["reasons"]),
            )
        else:
            logger.info(
                "  Break %d confidence %.3f",
                ad_break_index,
                confidence["score"],
            )

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
    video_stem: str | None = None,
    anchor_threshold: float = 0.6,
    clamp_majority_rule: bool = True,
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
            {
                "frame_index": i,
                "frame_name": p.name,
                "path": str(p),
                "text": "",
                "error": None,
            }
            for i, p in enumerate(frame_paths)
        ]
    else:
        _log("Running OCR on %d frames...", len(frame_paths))
        ocr_results = ocr_batch(
            image_paths=frame_paths,
            endpoint=ocr_endpoint,
            model=ocr_model,
            progress_callback=lambda c, t: _log("OCR %d/%d", c, t) if verbose else None,
        )

    # 4. Save OCR results to JSON
    if output_dir:
        stem = video_stem or output_dir.name
        ocr_json_path = output_dir / f"{stem}_break{ad_break_index}_ocr.json"
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

    # 6. Anchor-based re-estimation for low-confidence breaks
    # Run BEFORE clamp/cage so the anchor uses raw Pass 1 match positions
    # (uncontaminated by the clamp corrector's forward-backward shifting).
    anchor_invoked = False
    if anchor_threshold > 0:
        confidence = compute_break_confidence(scan_results, metadata, fps, after_secs)
        if confidence["score"] < anchor_threshold:
            logger.info(
                "  Break %d confidence %.3f < anchor threshold %.3f — "
                "running anchor re-estimation",
                ad_break_index,
                confidence["score"],
                anchor_threshold,
            )
            scan_results = anchor_based_reestimation(
                scan_results=scan_results,
                ad_metadata=metadata,
                ocr_results=ocr_results,
                fps=fps,
                after_secs=after_secs,
                before_secs=before_secs,
            )
            new_confidence = compute_break_confidence(
                scan_results, metadata, fps, after_secs
            )
            logger.info(
                "  Break %d confidence after anchor re-estimation: %.3f (was %.3f)",
                ad_break_index,
                new_confidence["score"],
                confidence["score"],
            )
            anchor_invoked = True

    # 7. 25fps end-frame refinement (if not dry run)
    # Run BEFORE clamp so the clamp operates on refined 25fps positions
    # (25 possible millis values) instead of raw 5fps positions (only 5).
    if not dry_run and video_path:
        scan_results = _refine_advert_end_frames(
            scan_results=scan_results,
            video_path=video_path,
            start_seconds=start_seconds,
            adverts=metadata.adverts,
            ocr_endpoint=ocr_endpoint,
            ocr_model=ocr_model,
            fps=fps,
            ocr_results=ocr_results,
            output_dir=output_dir,
            verbose=verbose,
        )

    # 8. Clamp/cage: detect and correct pattern anomalies (after refinement,
    # so positions have 25fps precision for better pattern discrimination).
    # Skip when anchor has already re-estimated all positions.
    if not anchor_invoked:
        anomalies = clamp_check(scan_results, majority_rule=clamp_majority_rule)
        if any(anomalies):
            logger.info("Clamp detected %d anomaly(ies), correcting...", sum(anomalies))
            scan_results = clamp_correct(
                scan_results, metadata.adverts, fps, majority_rule=clamp_majority_rule
            )
        else:
            logger.info("Clamp: no anomalies detected")

    # 9. Format XML
    xml_output = format_xml(metadata, scan_results, fps)
    _log("XML output: %d lines", len(xml_output.splitlines()))

    # 10. Generate QC HTML report (only when frames are kept on disk)
    if output_dir:
        qc_html = generate_qc_html(
            ocr_results=ocr_results,
            scan_results=scan_results,
            adverts=metadata.adverts,
            fps=fps,
            output_dir=output_dir,
            frames_dir=frames_dir,
            start_seconds=start_seconds,
        )
        qc_path = (
            output_dir
            / f"{video_stem or output_dir.name}_break{ad_break_index}_qc.html"
        )
        qc_path.write_text(qc_html, encoding="utf-8")
        _log("QC HTML written to: %s", qc_path)

    # 11. Update pipeline state
    if metadata_file and not dry_run:
        update_pipeline_state(
            metadata_file=metadata_file,
            ad_break_index=ad_break_index,
            scan_results=scan_results,
            ad_metadata=metadata,
            fps=fps,
            after_secs=after_secs,
        )

    # 12. Clean up frames (keep OCR JSON)
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
        "-v",
        "--video-url",
        required=True,
        help="Path or URL to the broadcast video (local paths are auto-served)",
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
        type=str,
        metavar="TITLE,CHANNEL",
        help="Programme before ad break: 'Lorraine,ITV1'",
    )
    parser.add_argument(
        "--prog-after",
        type=str,
        metavar="TITLE,CHANNEL",
        help="Programme after ad break: 'Daybreak,ITV1'",
    )
    parser.add_argument(
        "--advert",
        type=str,
        action="append",
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
        "--output",
        "-o",
        type=str,
        help="Output XML path (default: beside metadata file)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed progress",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip OCR API calls (for testing frame extraction only)",
    )
    parser.add_argument(
        "--anchor-threshold",
        type=float,
        default=0.6,
        help="Re-estimate low-confidence breaks using strongest OCR match "
        "as anchor when confidence falls below this value (0.0 disables, "
        "default: 0.6)",
    )
    parser.add_argument(
        "--clamp-majority-rule",
        action="store_true",
        default=False,
        help="Apply 50%% majority rule to clamp corrections (default: off — "
        "only min count threshold applies)",
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
        m = re.search(r"(\d{2})of(\d{2})\.mp4$", video_path.name, re.IGNORECASE)
        ad_break_index = int(m.group(1)) if m else 1

    # Load metadata
    metadata: AdBreakMetadata | None = None
    if parsed.metadata_file:
        metadata = load_metadata_from_file(parsed.metadata_file, ad_break_index)
    elif parsed.prog_before and parsed.prog_after and parsed.advert:
        metadata = parse_cli_metadata(
            parsed.prog_before,
            parsed.prog_after,
            parsed.advert,
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
                break_start_time,
                break_start_secs,
            )
    else:
        # Without metadata file, assume video starts at 0 and use ad break
        # index to estimate. This is a fallback for CLI-only metadata.
        break_start_secs = 0.0

    # Detect local video paths and serve them over HTTP
    video_url = parsed.video_url
    local_server: HTTPServer | None = None
    if Path(video_url).is_file():
        video_url, local_server = _start_local_video_server(video_url)
    else:
        logger.info("Video URL (remote): %s", video_url)

    # Download video
    logger.info("Downloading video: %s", video_url)
    local_video = download_video_to_temp(video_url)

    # Output directory
    output_dir: Path | None = None
    if parsed.output_dir:
        output_dir = Path(parsed.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        video_stem = Path(parsed.video_url.split("?")[0]).stem
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
            video_stem=video_stem,
            anchor_threshold=parsed.anchor_threshold,
            clamp_majority_rule=parsed.clamp_majority_rule,
        )

        # Write XML output
        if parsed.output:
            xml_path = parsed.output
        elif parsed.metadata_file:
            base = Path(parsed.metadata_file).parent
            xml_path = str(base / f"{video_stem}_break{ad_break_index}.xml")
        else:
            xml_path = "detect_output.xml"

        with open(xml_path, "w") as f:
            f.write(xml_output)
        logger.info("XML output written to: %s", xml_path)
        print(xml_output)

    finally:
        if os.path.exists(local_video):
            os.unlink(local_video)
        if local_server:
            local_server.shutdown()
            logger.info("Local video server stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
