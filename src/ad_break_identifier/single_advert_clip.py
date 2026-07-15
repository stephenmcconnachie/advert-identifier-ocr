#!/usr/bin/env python3
"""Extract individual advert clips from XML analysis results."""

import argparse
import concurrent.futures
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


def sanitize_filename(text: str) -> str:
    """Remove unsafe characters and replace spaces with hyphens for filenames."""
    text = text.replace(" ", "-")
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    return text


def parse_ad_break_index(video_url: str) -> int:
    """Extract 0-based ad break index from video URL filename.

    Args:
        video_url: URL or path containing filename like '01of08.mp4'.

    Returns:
        0-based index (0 for '01ofxx', 1 for '02ofxx', etc.).
    """
    video_path = Path(video_url.split("?")[0])
    filename = video_path.name
    match = re.search(r"(\d{2})of\d{2}", filename)
    if match:
        return int(match.group(1)) - 1
    return 0


def get_category_for_advert(json_path: str, ad_break_index: int, unique_id: str) -> str:
    """Get category from JSON metadata for a specific advert.

    Args:
        json_path: Path to JSON metadata file.
        ad_break_index: 0-based index of the ad break.
        unique_id: Advert unique_id to match.

    Returns:
        Sanitized category string, or 'unknown' if not found.
    """
    with open(json_path, "r") as f:
        data = json.load(f)

    ad_breaks = data.get("ad_breaks", [])
    if ad_break_index >= len(ad_breaks):
        return "unknown"

    for advert in ad_breaks[ad_break_index].get("adverts", []):
        if advert.get("unique_id") == unique_id:
            category = advert.get("category", "unknown")
            if category:
                return sanitize_filename(category)
            return "unknown"
    return "unknown"


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def timecode_to_seconds(timecode: str) -> float:
    """Convert MM:SS, HH:MM:SS, or HH:MM:SS.mmm timecode to seconds.

    Supports:
        - MM:SS         (e.g., "04:30" → 270.0)
        - HH:MM:SS      (e.g., "01:30:00" → 5400.0)
        - HH:MM:SS.mmm  (e.g., "00:04:30.080" → 270.08)

    Args:
        timecode: Timecode string in MM:SS, HH:MM:SS, or HH:MM:SS.mmm format.

    Returns:
        Total seconds as float (with millisecond precision).
    """
    parts = timecode.strip().split(":")

    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    elif len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    else:
        raise ValueError(f"Invalid timecode format: {timecode}")


def seconds_to_timecode(total_seconds: float) -> str:
    """Convert seconds to HH:MM:SS timecode.

    Args:
        total_seconds: Time in seconds.

    Returns:
        Timecode in HH:MM:SS format.
    """
    hours = int(total_seconds // 3600)
    remaining = total_seconds % 3600
    minutes = int(remaining // 60)
    seconds = int(remaining % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_advert_xml(xml_path: str) -> list[dict]:
    """Parse advert XML file.

    Args:
        xml_path: Path to XML file.

    Returns:
        List of dicts with advert details.

    Raises:
        FileNotFoundError: If XML file doesn't exist.
        ValueError: If XML is invalid or missing required fields.
    """
    if not Path(xml_path).exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    tree = ElementTree.parse(xml_path)
    root = tree.getroot()

    adverts = []
    for i, advert_el in enumerate(root.findall("advert")):

        def get_text(tag: str) -> str | None:
            child = advert_el.find(tag)
            return child.text.strip() if child is not None and child.text else None

        unique_id = get_text("unique_id") or ""
        brand = get_text("brand") or f"advert_{i + 1}"
        last_timecode = get_text("last_timecode") or ""
        start_timecode = get_text("start_timecode")
        duration_str = get_text("duration_seconds") or ""
        refined_timecode = get_text("refined_timecode")
        refined_clip_frame_str = get_text("refined_clip_frame")

        advert = {
            "index": i + 1,
            "unique_id": unique_id,
            "brand": brand,
            "last_timecode": last_timecode,
            "start_timecode": start_timecode,
            "duration_seconds": None if not duration_str else int(duration_str),
            "refined_timecode": refined_timecode,
            "refined_clip_frame": (
                None if not refined_clip_frame_str else int(refined_clip_frame_str)
            ),
        }
        adverts.append(advert)

    if not adverts:
        raise ValueError("No <advert> elements found in XML")

    return adverts


def parse_duration_from_unique_id(unique_id: str) -> int | None:
    suffix = unique_id[-3:] if len(unique_id) >= 3 else ""
    if suffix.isdigit():
        return int(suffix)
    return None


def calculate_durations(adverts: list[dict], default_duration: int = 30) -> list[dict]:
    """Calculate duration_seconds for last advert if missing.

    Args:
        adverts: List of advert dicts.
        default_duration: Default duration in seconds for a single-advert
            break when no duration is available (default: 30).

    Returns:
        Updated adverts list with all durations filled.
    """
    for i, advert in enumerate(adverts):
        if advert["duration_seconds"] is None:
            id_duration = parse_duration_from_unique_id(advert.get("unique_id", ""))
            if id_duration is not None:
                advert["duration_seconds"] = id_duration
                logger.info(
                    f"Advert {advert['index']}: derived {id_duration}s "
                    f"from unique_id {advert['unique_id']}"
                )
            elif i == 0 and len(adverts) == 1:
                # Single-advert break with no duration and unparseable ID
                advert["duration_seconds"] = default_duration
                logger.info(
                    f"Advert {advert['index']}: single advert, no duration in XML, "
                    f"using default {default_duration}s"
                )
            elif i == 0:
                raise ValueError(
                    f"Cannot calculate duration for first advert (index {advert['index']}): "
                    "no previous advert to reference"
                )
            else:
                prev_timecode = adverts[i - 1]["last_timecode"]
                curr_timecode = advert["last_timecode"]
                if not prev_timecode or not curr_timecode:
                    # Previous or current advert is unmatched (empty
                    # timecode) — can't calculate duration, use default.
                    advert["duration_seconds"] = default_duration
                    logger.info(
                        f"Advert {advert['index']}: no timecode available "
                        f"for duration calculation, using default {default_duration}s"
                    )
                else:
                    prev_secs = timecode_to_seconds(prev_timecode)
                    curr_secs = timecode_to_seconds(curr_timecode)
                    duration = curr_secs - prev_secs
                    if duration <= 0:
                        raise ValueError(
                            f"Invalid duration calculation for advert {advert['index']}: "
                            f"{curr_timecode} - {prev_timecode} = {duration}s"
                        )
                    advert["duration_seconds"] = int(duration)
                    logger.info(
                        f"Advert {advert['index']}: calculated duration = {duration}s "
                        f"({prev_timecode} to {curr_timecode})"
                    )

    return adverts


def download_video_to_temp(video_url: str, max_retries: int = 3) -> str:
    """Download video from URL to temporary file using curl.

    Args:
        video_url: URL to download.
        max_retries: Maximum retry attempts.

    Returns:
        Path to downloaded temporary file.
    """
    temp_file = None

    for attempt in range(max_retries):
        try:
            temp_fd, temp_path = tempfile.mkstemp(suffix=".mp4")
            os.close(temp_fd)
            temp_file = temp_path

            logger.info(f"Downloading video (attempt {attempt + 1}/{max_retries})")

            cmd = [
                "curl",
                "-L",
                "-o",
                temp_file,
                "--fail",
                "--silent",
                "--show-error",
                video_url,
            ]

            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"Downloaded to {temp_file}")
            return temp_file

        except subprocess.CalledProcessError as e:
            logger.warning(f"Download attempt {attempt + 1} failed: {e.stderr}")
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)
                temp_file = None

            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.info(f"Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise RuntimeError(
                    f"Failed to download video after {max_retries} attempts: {e.stderr}"
                )

    raise RuntimeError("Unexpected code path in download_video_to_temp")


def extract_advert_clip(
    video_input: str,
    start_seconds: float,
    duration_seconds: int,
    output_path: Path,
) -> str:
    """Extract advert clip using FFmpeg with lossless encoding.

    Args:
        video_input: Path or URL to video.
        start_seconds: Start time in seconds.
        duration_seconds: Duration in seconds.
        output_path: Output file path.

    Returns:
        Path to extracted clip.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        video_input,
        "-t",
        str(duration_seconds),
        "-c:v",
        "libx264",
        "-preset",
        "placebo",
        "-crf",
        "0",
        "-c:a",
        "copy",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    logger.info(
        f"Extracting clip: start={seconds_to_timecode(start_seconds)}, "
        f"duration={duration_seconds}s"
    )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    logger.info(f"Created: {output_path.name}")
    return str(output_path)


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Extract individual advert clips from XML analysis results"
    )

    parser.add_argument(
        "--xml-file",
        required=True,
        help="Path to XML file with advert analysis results",
    )
    parser.add_argument(
        "--json-file",
        required=True,
        help="Path to JSON metadata file (required for category extraction)",
    )
    parser.add_argument(
        "--video-url",
        required=True,
        help="Path or URL to the broadcast video (local paths auto-served by advert-identifier)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for clips (default: current directory)",
    )
    parser.add_argument(
        "--index",
        type=int,
        help="1-based advert index to process (default: all adverts)",
    )
    parser.add_argument(
        "--trim",
        type=float,
        default=0.0,
        help="Seconds to trim from start and end of each clip (default: 0.0)",
    )
    parser.add_argument(
        "--pad",
        type=float,
        default=0.0,
        help="Seconds to add to start and end of each clip (default: 0.0)",
    )
    parser.add_argument(
        "--clip-offset",
        type=float,
        default=0.0,
        help="Seconds from broadcast start to clip start (default: 0.0). "
        "Needed to convert clip-relative timecodes to broadcast-absolute.",
    )
    parser.add_argument(
        "--state-file",
        help="Path to pipeline state file. When provided, reads adjusted_start_broadcast "
        "from state instead of computing from XML timecode + clip_offset.",
    )
    parser.add_argument(
        "--ad-break-index",
        type=int,
        help="0-based ad break index (default: auto-detect from video URL filename)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Maximum parallel clip extractions (default: 10)",
    )
    parser.add_argument(
        "--start-offset-frames",
        type=int,
        default=0,
        help="Shift clip start N frames later (25fps), keep end frame "
        "unchanged. Appends _{N}frameslater to filename (default: 0)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    return parser


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    if args.trim > 0 and args.pad > 0:
        logger.error("--trim and --pad are mutually exclusive")
        return 1

    try:
        logger.info(f"Parsing XML: {args.xml_file}")
        adverts = parse_advert_xml(args.xml_file)
        logger.info(f"Found {len(adverts)} advert(s)")

        adverts = calculate_durations(adverts)

        is_url = args.video_url.startswith(("http://", "https://"))
        temp_video = None

        try:
            if is_url:
                temp_video = download_video_to_temp(args.video_url)
                video_input = temp_video
            else:
                if not Path(args.video_url).exists():
                    raise ValueError(f"Video file not found: {args.video_url}")
                video_input = args.video_url

            output_dir = Path(args.output_dir).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)

            indices_to_process = (
                [args.index] if args.index else [a["index"] for a in adverts]
            )

            if args.ad_break_index is not None:
                ad_break_index = args.ad_break_index
                logger.info(f"Using --ad-break-index: {ad_break_index} (0-based)")
            else:
                ad_break_index = parse_ad_break_index(args.video_url)
                logger.info(
                    f"Parsed ad break index from video URL: {ad_break_index} (0-based)"
                )

            clip_tasks: list[dict] = []
            for advert in adverts:
                if advert["index"] not in indices_to_process:
                    continue

                start_secs = None
                duration = advert["duration_seconds"]
                time_source_desc = ""

                start_tc = advert.get("start_timecode")
                last_tc = advert.get("last_timecode")
                if start_tc and last_tc:
                    start_secs = timecode_to_seconds(start_tc) + args.clip_offset
                    last_secs = timecode_to_seconds(last_tc)
                    duration = last_secs - timecode_to_seconds(start_tc)
                    time_source_desc = f"start_timecode={start_tc} + clip_offset={args.clip_offset:.3f}s"

                if start_secs is None and args.state_file:
                    try:
                        from ad_break_identifier.pipeline_state import (
                            read_state,
                            get_adjusted_starts,
                        )

                        state = read_state(args.state_file)
                        starts = get_adjusted_starts(state, ad_break_index + 1)
                        adv_idx = advert["index"] - 1
                        if adv_idx < len(starts) and starts[adv_idx] is not None:
                            start_secs = starts[adv_idx]
                            time_source_desc = (
                                f"pipeline_state adjusted_start={start_secs:.3f}s"
                            )
                    except Exception as e:
                        logger.warning(f"Could not read pipeline state: {e}")

                if start_secs is None:
                    time_source = (
                        advert.get("refined_timecode") or advert["last_timecode"]
                    )
                    if not time_source:
                        logger.warning(
                            f"Advert {advert['index']}: no timecode available "
                            f"(unmatched, skipping)"
                        )
                        continue
                    last_secs = timecode_to_seconds(time_source)
                    start_secs = last_secs - duration + args.clip_offset
                    time_source_desc = (
                        f"refined_timecode={time_source}"
                        if advert.get("refined_timecode")
                        else f"last_timecode={time_source}"
                    )
                    if args.clip_offset != 0.0:
                        time_source_desc += f" + clip_offset={args.clip_offset:.3f}s"

                logger.info(f"Advert {advert['index']}: {time_source_desc}")
                logger.info(f"Advert {advert['index']}: start_secs={start_secs:.3f}s")

                trim = args.trim
                pad = args.pad

                if trim > 0:
                    start_secs += trim
                    duration -= 2 * trim
                    logger.info(
                        f"Advert {advert['index']}: trim={trim}s, adjusted start={start_secs:.3f}s, duration={duration:.3f}s"
                    )

                if pad > 0:
                    start_secs -= pad
                    duration += 2 * pad
                    logger.info(
                        f"Advert {advert['index']}: pad={pad}s, adjusted start={start_secs:.3f}s, duration={duration:.3f}s"
                    )

                if args.start_offset_frames > 0:
                    offset = args.start_offset_frames / SOURCE_FPS
                    start_secs += offset
                    duration -= offset
                    logger.info(
                        f"Advert {advert['index']}: start offset {args.start_offset_frames}frames "
                        f"({offset:.3f}s), adjusted start={start_secs:.3f}s, duration={duration:.3f}s"
                    )

                if start_secs < 0:
                    logger.warning(
                        f"Advert {advert['index']}: start time {start_secs}s is negative, "
                        f"clipping to 00:00"
                    )
                    start_secs = 0

                if duration <= 0:
                    logger.warning(
                        f"Advert {advert['index']}: duration {duration}s is zero or negative, skipping"
                    )
                    continue

                safe_brand = sanitize_filename(advert["brand"])
                if args.start_offset_frames > 0:
                    safe_brand += f"_{args.start_offset_frames}frameslater"
                category = get_category_for_advert(
                    args.json_file, ad_break_index, advert["unique_id"]
                )
                output_path = (
                    output_dir / f"{advert['unique_id']}_{category}_{safe_brand}.mp4"
                )
                logger.info(
                    f"Advert {advert['index']}: category={category}, output={output_path.name}"
                )

                clip_tasks.append(
                    {
                        "index": advert["index"],
                        "video_input": video_input,
                        "start_seconds": start_secs,
                        "duration_seconds": duration,
                        "output_path": output_path,
                    }
                )

            if not clip_tasks:
                logger.warning("No advert clips to extract")
                return 0

            if args.max_workers <= 1:
                for task in clip_tasks:
                    extract_advert_clip(
                        video_input=task["video_input"],
                        start_seconds=task["start_seconds"],
                        duration_seconds=task["duration_seconds"],
                        output_path=task["output_path"],
                    )
            else:
                logger.info(
                    "Extracting %d advert clips with %d parallel workers...",
                    len(clip_tasks),
                    args.max_workers,
                )
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=args.max_workers
                ) as executor:
                    future_map = {}
                    for task in clip_tasks:
                        future = executor.submit(
                            extract_advert_clip,
                            task["video_input"],
                            task["start_seconds"],
                            task["duration_seconds"],
                            task["output_path"],
                        )
                        future_map[future] = task["index"]
                    for future in concurrent.futures.as_completed(future_map):
                        idx = future_map[future]
                        try:
                            future.result()
                        except Exception as e:
                            logger.error("Advert %d clip extraction failed: %s", idx, e)

            logger.info("Done")
            return 0

        finally:
            if temp_video and os.path.exists(temp_video):
                os.unlink(temp_video)
                logger.debug(f"Cleaned up temp video: {temp_video}")

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        return 1
    except RuntimeError as e:
        logger.error(f"Processing error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
