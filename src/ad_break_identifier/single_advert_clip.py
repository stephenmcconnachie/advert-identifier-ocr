#!/usr/bin/env python3
"""Extract individual advert clips from XML analysis results."""

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def timecode_to_seconds(timecode: str) -> float:
    """Convert MM:SS or HH:MM:SS timecode to seconds.
    
    Args:
        timecode: Timecode string in MM:SS or HH:MM:SS format.
        
    Returns:
        Total seconds as float.
    """
    parts = timecode.strip().split(":")

    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + int(seconds)
    elif len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
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
        duration_str = get_text("duration_seconds") or ""

        advert = {
            "index": i + 1,
            "unique_id": unique_id,
            "brand": brand,
            "last_timecode": last_timecode,
            "duration_seconds": None if not duration_str else int(duration_str),
        }
        adverts.append(advert)

    if not adverts:
        raise ValueError("No <advert> elements found in XML")

    return adverts


def calculate_durations(adverts: list[dict]) -> list[dict]:
    """Calculate duration_seconds for last advert if missing.
    
    Args:
        adverts: List of advert dicts.
        
    Returns:
        Updated adverts list with all durations filled.
    """
    for i, advert in enumerate(adverts):
        if advert["duration_seconds"] is None:
            if i == 0:
                raise ValueError(
                    f"Cannot calculate duration for first advert (index {advert['index']}): "
                    "no previous advert to reference"
                )
            prev_timecode = adverts[i - 1]["last_timecode"]
            curr_timecode = advert["last_timecode"]
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
        "--video-url",
        required=True,
        help="URL or path to source video",
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

            indices_to_process = [args.index] if args.index else [a["index"] for a in adverts]

            for advert in adverts:
                if advert["index"] not in indices_to_process:
                    continue

                last_tc = advert["last_timecode"]
                duration = advert["duration_seconds"]
                last_secs = timecode_to_seconds(last_tc)
                start_secs = last_secs - duration

                if start_secs < 0:
                    logger.warning(
                        f"Advert {advert['index']}: start time {start_secs}s is negative, "
                        f"clipping to 00:00"
                    )
                    start_secs = 0

                safe_brand = advert["brand"].replace("/", "_").replace("\\", "_")
                output_path = output_dir / f"{advert['unique_id']}_{safe_brand}.mp4"

                extract_advert_clip(
                    video_input=video_input,
                    start_seconds=start_secs,
                    duration_seconds=duration,
                    output_path=output_path,
                )

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