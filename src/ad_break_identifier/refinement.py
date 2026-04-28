"""Frame refinement stage for precise advert boundary detection.

This module takes coarse timecodes from primary detection (1 FPS) and refines
them to frame-accurate boundaries using 24 FPS analysis of 3-second clips.
"""

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

from .api_client import create_vllm_client, run_ensemble_sync
from .models import (
    AdBreakResult,
    AdvertResult,
    RefinedAdvertResult,
    RefinedAdBreakResult,
    RefinementStats,
)
from .prompts import build_refine_prompt

logger = logging.getLogger(__name__)


def _log_verbose(verbose: bool, message: str) -> None:
    """Print verbose message with timestamp to stderr."""
    if verbose:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def download_video_to_temp(video_url: str, max_retries: int = 3) -> str:
    """Download video from URL to temporary file using curl.

    Args:
        video_url: URL to download.
        max_retries: Maximum retry attempts.

    Returns:
        Path to downloaded temporary file.

    Raises:
        RuntimeError: If download fails after max_retries attempts.
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
                wait_time = 2 ** attempt
                logger.info(f"Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise RuntimeError(
                    f"Failed to download video after {max_retries} attempts: {e.stderr}"
                )

    raise RuntimeError("Unexpected code path in download_video_to_temp")


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
        return int(minutes) * 60 + float(seconds)
    elif len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    else:
        raise ValueError(f"Invalid timecode format: {timecode}")


def seconds_to_timecode(total_seconds: float) -> str:
    """Convert seconds to HH:MM:SS.mmm timecode.

    Args:
        total_seconds: Time in seconds.

    Returns:
        Timecode in HH:MM:SS.mmm format.
    """
    hours = int(total_seconds // 3600)
    remaining = total_seconds % 3600
    minutes = int(remaining // 60)
    seconds = remaining % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def extract_clip(
    video_url: str,
    start_seconds: float,
    duration: float,
    output_path: Path,
) -> str:
    """Extract video clip using FFmpeg with streaming input.

    Args:
        video_url: URL to video (can be http:// or file://).
        start_seconds: Start time in seconds.
        duration: Duration in seconds.
        output_path: Output file path.

    Returns:
        Path to extracted clip.

    Raises:
        RuntimeError: If FFmpeg fails, with clear message for partial files.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        video_url,
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "27",
        "-c:a",
        "copy",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    logger.debug(
        f"Extracting clip: start={start_seconds:.3f}s, duration={duration}s"
    )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error_msg = result.stderr
        if "partial file" in error_msg.lower():
            raise RuntimeError(
                f"Video file is incomplete (partial file). "
                f"Ensure the video is fully downloaded before refinement. "
                f"Error: {error_msg[-500:]}"
            )
        raise RuntimeError(f"FFmpeg failed: {error_msg}")

    return str(output_path)


def parse_refinement_response(response_text: str) -> tuple[int | None, str, str]:
    """Parse refinement response XML to extract frame number, confidence, description.

    Args:
        response_text: Raw response text from VLM.

    Returns:
        Tuple of (frame_number, confidence, description).
    """
    try:
        xml_content = _extract_refinement_xml(response_text)
        root = ElementTree.fromstring(xml_content)

        frame_el = root.find("last_frame")
        confidence_el = root.find("confidence")
        desc_el = root.find("description")

        frame = None
        if frame_el is not None and frame_el.text:
            try:
                frame = int(frame_el.text.strip())
                if not (0 <= frame <= 71):
                    logger.warning(f"Frame {frame} out of range 0-71, ignoring")
                    frame = None
            except ValueError:
                logger.warning(f"Invalid frame value: {frame_el.text}")

        confidence = confidence_el.text.strip() if confidence_el is not None and confidence_el.text else "UNKNOWN"
        description = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

        return frame, confidence, description

    except Exception as e:
        logger.warning(f"Failed to parse refinement response: {e}")
        return None, "UNKNOWN", ""


def _extract_refinement_xml(response_text: str) -> str:
    """Extract <advert> XML from refinement response.

    Args:
        response_text: Raw response text.

    Returns:
        XML content string.

    Raises:
        ValueError: If no valid XML found.
    """
    response_marker_match = re.search(r'\[RESPONSE\].*', response_text, re.DOTALL)
    if response_marker_match:
        text_after_marker = response_marker_match.group(0)
        match = re.search(r'<advert>(.*?)</advert>', text_after_marker, re.DOTALL)
        if match:
            return _sanitize_xml(match.group(0))

    all_matches = list(re.finditer(r'<advert>(.*?)</advert>', response_text, re.DOTALL))
    if all_matches:
        return _sanitize_xml(all_matches[-1].group(0))

    raise ValueError("No <advert> XML found in refinement response")


def _sanitize_xml(xml_content: str) -> str:
    """Sanitize XML content by escaping unescaped special characters."""
    import html

    protected = xml_content
    entity_map = {
        '&amp;': '\x00AMP\x00',
        '&lt;': '\x00LT\x00',
        '&gt;': '\x00GT\x00',
        '&quot;': '\x00QUOT\x00',
        '&apos;': '\x00APOS\x00',
    }

    for entity, placeholder in entity_map.items():
        protected = protected.replace(entity, placeholder)

    def escape_ampersand(match):
        entity_content = match.group(1)
        if re.match(r'^[a-zA-Z][a-zA-Z0-9]*$', entity_content):
            return '&amp;' + entity_content
        elif re.match(r'^#[0-9]+$', entity_content):
            return '&' + entity_content
        elif re.match(r'^#x[0-9a-fA-F]+$', entity_content):
            return '&' + entity_content
        else:
            return '&amp;' + entity_content

    protected = re.sub(r'&([^;<>&\s][^;<>]*)', escape_ampersand, protected)
    protected = re.sub(r'&(?![\x00])', '&amp;', protected)

    def escape_quotes_in_text(text):
        if text.startswith('<') and text.endswith('>'):
            return text
        else:
            result = text.replace('"', '\x00ESCQUOT\x00')
            return result

    parts = re.split(r'(<[^>]+>)', protected)
    processed_parts = [escape_quotes_in_text(part) for part in parts if part]
    protected = ''.join(processed_parts)
    protected = protected.replace('\x00ESCQUOT\x00', '&quot;')

    for entity, placeholder in entity_map.items():
        protected = protected.replace(placeholder, entity)

    return protected


def refine_single_advert(
    client,
    advert: AdvertResult,
    video_path: str,
    model: str,
    ensemble_size: int = 3,
    ensemble_delay: float = 5.0,
    fps: float = 25.0,
    verbose: bool = False,
) -> tuple[RefinedAdvertResult, list[tuple[str | None, str | None, dict[str, Any] | None]]]:
    """Refine a single advert's timecode using high-FPS analysis.

    Args:
        client: OpenAI/vLLM client.
        advert: AdvertResult from primary detection (includes advertiser/category).
        video_path: Local path to video file.
        model: Model name.
        ensemble_size: Number of ensemble calls (default: 3).
        ensemble_delay: Delay between ensemble calls (default: 5.0).
        fps: Frames per second for the video (default: 25.0).
        verbose: Enable verbose logging.

    Returns:
        Tuple of (RefinedAdvertResult, raw_responses).
    """
    result = RefinedAdvertResult(
        original_timecode=advert.timecode,
    )
    raw_responses: list = []

    if not advert.timecode:
        result.refinement_status = "error"
        result.description = "No original timecode to refine"
        return result, raw_responses

    try:
        coarse_seconds = timecode_to_seconds(advert.timecode)
        clip_center = coarse_seconds
        clip_start = clip_center - 1.5
        clip_duration = 3.0

        if clip_start < 0:
            clip_start = 0
            logger.warning(f"Clip start negative, adjusted to 0 for advert {advert.advert_id}")

        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "refinement_clip.mp4"

            _log_verbose(verbose, f"Extracting clip: start={clip_start:.3f}s, duration={clip_duration}s")

            try:
                extract_clip(video_path, clip_start, clip_duration, clip_path)
            except RuntimeError as e:
                result.refinement_status = "error"
                result.description = f"Clip extraction failed: {e}"
                return result, raw_responses

            clip_url = f"file://{clip_path}"

            prompt = build_refine_prompt(
                brand=advert.brand,
                advertiser=advert.advertiser,
                category=advert.category,
                duration=advert.duration_seconds,
                fps=fps,
            )

            _log_verbose(verbose, f"Making {ensemble_size} ensemble calls at {fps} FPS")

            raw_responses = run_ensemble_sync(
                client=client,
                video_url=clip_url,
                prompt=prompt,
                model=model,
                fps=fps,
                ensemble_size=ensemble_size,
                ensemble_delay=ensemble_delay,
            )

            valid_frames = []
            confidences = []
            descriptions = []
            invalid_count = 0

            for resp_text, resp_error, resp_dict in raw_responses:
                if resp_error:
                    logger.warning(f"Ensemble call failed: {resp_error}")
                    invalid_count += 1
                    continue

                frame, confidence, description = parse_refinement_response(resp_text or "")
                if frame is not None:
                    valid_frames.append(frame)
                    confidences.append(confidence)
                    if description:
                        descriptions.append(description)

            if not valid_frames:
                result.refinement_status = "fallback"
                result.description = "No valid frames from ensemble"
                return result, raw_responses

            median_frame = sorted(valid_frames)[len(valid_frames) // 2]

            refined_seconds = clip_start + (median_frame / fps)
            refined_timecode = seconds_to_timecode(refined_seconds)

            majority_confidence = max(set(confidences), key=confidences.count) if confidences else "MEDIUM"
            primary_description = descriptions[0] if descriptions else ""

            result.refined_timecode = refined_timecode
            result.refined_clip_frame = median_frame
            result.confidence = majority_confidence
            result.refinement_status = "success"
            result.description = primary_description

            _log_verbose(
                verbose,
                f"Voting: valid={len(valid_frames)}, invalid={invalid_count}, "
                f"median_frame={median_frame}, refined_timecode={refined_timecode}"
            )

            return result, raw_responses

    except Exception as e:
        result.refinement_status = "fallback"
        result.description = f"Refinement error: {str(e)}"
        return result, []


def parse_ad_break_xml(xml_path: str, metadata_json: str | None = None) -> AdBreakResult:
    """Parse XML output from primary detection and enrich with metadata if provided.

    Args:
        xml_path: Path to XML file.
        metadata_json: Optional path to original metadata JSON for enrichment.

    Returns:
        AdBreakResult with advert list populated.
    """
    metadata_lookup: dict[str, dict] = {}
    if metadata_json:
        import json
        with open(metadata_json, 'r') as f:
            data = json.load(f)
        ad_breaks = data.get('ad_breaks', [data])
        for ad_break in ad_breaks:
            for advert in ad_break.get('adverts', []):
                uid = advert.get('unique_id', '')
                if uid:
                    metadata_lookup[uid] = advert

    tree = ElementTree.parse(xml_path)
    root = tree.getroot()

    adverts = []
    for advert_el in root.findall("advert"):
        def get_text(tag: str) -> str | None:
            child = advert_el.find(tag)
            return child.text.strip() if child is not None and child.text else None

        unique_id = get_text("unique_id") or ""
        brand = get_text("brand") or ""
        last_timecode = get_text("last_timecode") or ""
        duration_str = get_text("duration_seconds") or ""

        advert = AdvertResult(
            advert_id=unique_id,
            brand=brand,
            timecode=last_timecode,
            duration_seconds=int(duration_str) if duration_str else None,
        )

        if unique_id in metadata_lookup:
            meta = metadata_lookup[unique_id]
            advert.advertiser = meta.get('advertiser', '')
            advert.category = meta.get('category', '')

        adverts.append(advert)

    return AdBreakResult(
        success=True,
        adverts=adverts,
        total_found=len(adverts),
        total_expected=len(adverts),
    )


def refine_advert_timecodes(
    xml_path: str,
    video_url: str,
    output_path: str | None = None,
    metadata_json: str | None = None,
    api_base_url: str = "http://localhost:8000/v1",
    api_key: str = "EMPTY",
    model: str = "Qwen/Qwen3.5-4B",
    ensemble_size: int = 3,
    ensemble_delay: float = 5.0,
    fps: float = 25.0,
    verbose: bool = False,
    debug_mode: bool = False,
) -> tuple[RefinedAdBreakResult, RefinementStats | None]:
    """Refine advert timecodes from primary detection XML.

    Args:
        xml_path: Path to XML from primary detection.
        video_url: URL to video.
        output_path: Output XML path (default: <xml_path>_refined.xml).
        metadata_json: Path to original metadata JSON for advert enrichment.
        api_base_url: vLLM API base URL.
        api_key: API key.
        model: Model name.
        ensemble_size: Number of ensemble calls per advert.
        ensemble_delay: Delay between ensemble calls.
        fps: Frames per second for the video (default: 25.0).
        verbose: Enable verbose logging.
        debug_mode: Enable debug mode to return raw responses.

    Returns:
        Tuple of (RefinedAdBreakResult, RefinementStats or None).
    """
    _log_verbose(verbose, f"Parsing primary detection XML: {xml_path}")
    ad_break_result = parse_ad_break_xml(xml_path, metadata_json)

    if not ad_break_result.adverts:
        return RefinedAdBreakResult(
            success=False,
            error="No adverts found in XML",
        ), None

    client = create_vllm_client(base_url=api_base_url, api_key=api_key)

    _log_verbose(verbose, f"Downloading video: {video_url}")
    temp_video_path = download_video_to_temp(video_url)

    refined_adverts = []
    all_raw_responses: list[dict] = []
    advert_voting_details: list[dict] = []
    total_refined = 0
    total_fallback = 0
    total_responses = 0
    valid_responses = 0
    invalid_responses = 0

    try:
        for i, advert in enumerate(ad_break_result.adverts, 1):
            _log_verbose(verbose, f"Refining advert {i}/{len(ad_break_result.adverts)}: {advert.brand} ({advert.advert_id})")

            refined, raw_responses = refine_single_advert(
                client=client,
                advert=advert,
                video_path=temp_video_path,
                model=model,
                ensemble_size=ensemble_size,
                ensemble_delay=ensemble_delay,
                fps=fps,
                verbose=verbose,
            )
            refined_adverts.append(refined)

            if refined.refinement_status == "success":
                total_refined += 1
                _log_verbose(
                    verbose,
                    f"  -> Refined: {advert.timecode} -> {refined.refined_timecode} "
                    f"(clip frame {refined.refined_clip_frame})"
                )
            else:
                total_fallback += 1
                _log_verbose(verbose, f"  -> Fallback to original: {advert.timecode} ({refined.description})")

            if debug_mode:
                total_responses += len(raw_responses)
                for j, (resp_text, resp_error, resp_dict) in enumerate(raw_responses, 1):
                    if resp_error:
                        invalid_responses += 1
                    else:
                        valid_responses += 1

                advert_voting_details.append({
                    "advert_position": i,
                    "advert_id": advert.advert_id,
                    "brand": advert.brand,
                    "value_type": "frame",
                    "voted_value": refined.refined_clip_frame if refined.refined_clip_frame is not None else "N/A",
                    "response_values": [
                        {"response_num": j, "value": "valid" if resp_text and not resp_error else "invalid"}
                        for j, (resp_text, resp_error, _) in enumerate(raw_responses, 1)
                    ],
                })

    finally:
        if temp_video_path and os.path.exists(temp_video_path):
            os.unlink(temp_video_path)
            _log_verbose(verbose, f"Cleaned up temp video: {temp_video_path}")

    if output_path is None:
        xml_path_obj = Path(xml_path)
        output_path = str(xml_path_obj.parent / f"{xml_path_obj.stem}_refined.xml")

    refined_xml = format_refined_xml(
        ad_break_result=ad_break_result,
        refined_adverts=refined_adverts,
        output_path=output_path,
    )

    result = RefinedAdBreakResult(
        success=True,
        adverts=refined_adverts,
        total_refined=total_refined,
        total_fallback=total_fallback,
    )

    if debug_mode:
        stats = RefinementStats(
            total_responses=total_responses,
            valid_responses=valid_responses,
            invalid_responses=invalid_responses,
            advert_voting_details=advert_voting_details,
        )
        return result, stats

    return result, None


def format_refined_xml(
    ad_break_result: AdBreakResult,
    refined_adverts: list[RefinedAdvertResult],
    output_path: str,
) -> str:
    """Write refined results to XML file.

    Args:
        ad_break_result: Original detection result.
        refined_adverts: Refined results per advert.
        output_path: Path to write XML.

    Returns:
        Path to written XML file.
    """
    def escape_xml(text: str) -> str:
        return html.escape(text, quote=True)

    lines = []
    lines.append("<ad_break>")

    for orig, refined in zip(ad_break_result.adverts, refined_adverts):
        lines.append("    <advert>")
        lines.append(f"        <unique_id>{escape_xml(orig.advert_id)}</unique_id>")
        lines.append(f"        <brand>{escape_xml(orig.brand)}</brand>")
        lines.append(f"        <advertiser>{escape_xml(orig.advertiser)}</advertiser>")
        lines.append(f"        <category>{escape_xml(orig.category)}</category>")
        if orig.duration_seconds:
            lines.append(f"        <duration_seconds>{orig.duration_seconds}</duration_seconds>")
        lines.append(f"        <last_timecode>{escape_xml(orig.timecode or '')}</last_timecode>")
        if refined.refined_timecode:
            lines.append(f"        <refined_timecode>{escape_xml(refined.refined_timecode)}</refined_timecode>")
        if refined.refined_clip_frame is not None:
            lines.append(f"        <refined_clip_frame>{refined.refined_clip_frame}</refined_clip_frame>")
        lines.append(f"        <refinement_status>{refined.refinement_status}</refinement_status>")
        if refined.description:
            lines.append(f"        <description>{escape_xml(refined.description)}</description>")
        lines.append("    </advert>")

    lines.append("</ad_break>")

    xml_content = "\n".join(lines)

    with open(output_path, "w") as f:
        f.write(xml_content)

    logger.info(f"Refined XML written to: {output_path}")
    return output_path