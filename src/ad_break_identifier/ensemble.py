"""Ensemble voting logic for ad break analysis."""

from collections import Counter
from statistics import median
from typing import Any

from .models import AdBreakResult, AdvertResult, EnsembleStats
from .response_parser import parse_ad_break_response


def timecode_to_seconds(timecode: str) -> float:
    """Convert timecode to seconds.
    
    Supports both HH:MM:SS.mmm and MM:SS formats.
    
    Args:
        timecode: Timecode string in HH:MM:SS.mmm or MM:SS format.
        
    Returns:
        Total seconds as float.
    """
    parts = timecode.split(":")
    
    if len(parts) == 3:
        # HH:MM:SS.mmm format
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds_parts = parts[2].split(".")
        seconds = int(seconds_parts[0])
        milliseconds = int(seconds_parts[1]) if len(seconds_parts) > 1 else 0
        return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0
    elif len(parts) == 2:
        # MM:SS format
        minutes = int(parts[0])
        seconds = int(parts[1])
        return minutes * 60 + seconds
    else:
        raise ValueError(f"Invalid timecode format: {timecode}")


def seconds_to_timecode(seconds: float) -> str:
    """Convert seconds to timecode string.
    
    Returns MM:SS format for times under 1 hour,
    HH:MM:SS.mmm format for longer times.
    
    Args:
        seconds: Seconds to convert.
        
    Returns:
        Timecode string in MM:SS or HH:MM:SS.mmm format.
    """
    total_seconds = int(seconds)
    secs = total_seconds % 60
    total_mins = total_seconds // 60
    mins = total_mins % 60
    hours = total_mins // 60
    
    if hours > 0:
        # HH:MM:SS.mmm format
        milliseconds = int((seconds - total_seconds) * 1000)
        return f"{hours:02d}:{mins:02d}:{secs:02d}.{milliseconds:03d}"
    else:
        # MM:SS format (as expected by the model/prompt)
        return f"{mins:02d}:{secs:02d}"


def vote_on_timecodes(timecodes: list[str]) -> str | None:
    """Select median timecode from list.
    
    Args:
        timecodes: List of timecode strings (HH:MM:SS.mmm).
        
    Returns:
        Median timecode or None if list is empty.
    """
    if not timecodes:
        return None
    
    seconds_list = [timecode_to_seconds(tc) for tc in timecodes]
    median_seconds = median(seconds_list)
    return seconds_to_timecode(median_seconds)


def vote_on_frames(frames: list[int]) -> int | None:
    """Select median frame from list.
    
    Args:
        frames: List of frame numbers (integers).
        
    Returns:
        Median frame number or None if list is empty.
    """
    if not frames:
        return None
    
    return int(median(frames))


def vote_on_adverts(
    parsed_results: list[AdBreakResult],
    mode: str = "timecode"
) -> tuple[list[AdvertResult], str, list[dict]]:
    """Vote on advert detections from ensemble.
    
    Args:
        parsed_results: List of parsed results from each ensemble member.
        mode: Analysis mode - "timecode" or "frame".
        
    Returns:
        Tuple of (final_adverts_list, voting_method_description, per_advert_voting_details).
    """
    if not parsed_results:
        return [], "no_responses", []
    
    num_adverts = max(len(r.adverts) for r in parsed_results if r.adverts)
    
    final_adverts = []
    voting_details = []  # Per-advert voting breakdown
    
    for pos in range(num_adverts):
        adverts_at_pos = [
            r.adverts[pos] for r in parsed_results if pos < len(r.adverts)
        ]
        
        if not adverts_at_pos:
            continue
        
        # Collect all values for this advert position
        advert_id = adverts_at_pos[0].advert_id if adverts_at_pos[0].advert_id else f"Advert_{pos+1}"
        brand = adverts_at_pos[0].brand if adverts_at_pos[0].brand else "Unknown"
        
        # Build per-response details
        response_values = []
        for i, a in enumerate(adverts_at_pos):
            if mode == "frame":
                value = a.frame
            else:
                value = a.timecode
            response_values.append({
                "response_num": i + 1,
                "value": value,
                "brand": a.brand,
                "description": a.description[:100] + "..." if a.description and len(a.description) > 100 else a.description,
            })
        
        # Vote based on mode
        if mode == "frame":
            frames = [a.frame for a in adverts_at_pos if a.frame is not None]
            voted_frame = vote_on_frames(frames)
            voted_timecode = None
            used_values = frames
            voted_value = voted_frame
        else:
            timecodes = [a.timecode for a in adverts_at_pos if a.timecode]
            voted_timecode = vote_on_timecodes(timecodes)
            voted_frame = None
            used_values = timecodes
            voted_value = voted_timecode
        
        brands = [a.brand for a in adverts_at_pos if a.brand]
        brand_counts = Counter(brands)
        voted_brand = brand_counts.most_common(1)[0][0] if brand_counts else ""
        
        ids = [a.advert_id for a in adverts_at_pos if a.advert_id]
        id_counts = Counter(ids)
        voted_id = id_counts.most_common(1)[0][0] if id_counts else ""
        
        descriptions = [a.description for a in adverts_at_pos if a.description]
        voted_description = descriptions[0] if descriptions else ""
        
        final_adverts.append(AdvertResult(
            timecode=voted_timecode,
            frame=voted_frame,
            advert_id=voted_id,
            brand=voted_brand,
            description=voted_description,
            confidence=0.0,
        ))
        
        # Store voting details for this advert
        voting_details.append({
            "advert_position": pos + 1,
            "advert_id": advert_id,
            "brand": brand,
            "response_values": response_values,
            "used_for_voting": used_values,
            "voted_value": voted_value,
            "voted_brand": voted_brand,
            "value_type": "frame" if mode == "frame" else "timecode",
        })
    
    voting_desc = "median_frame_majority_brand" if mode == "frame" else "median_timecode_majority_brand"
    return final_adverts, voting_desc, voting_details


def apply_ensemble_voting(
    raw_responses: list[tuple[str | None, str | None, dict[str, Any] | None]],
    expected_advert_count: int,
    expected_adverts: list | None = None,
    mode: str = "timecode",
) -> tuple[AdBreakResult, EnsembleStats]:
    """Apply ensemble voting to all responses.
    
    Args:
        raw_responses: List of (response_text, error, raw_response_dict) from ensemble calls.
        expected_advert_count: Number of adverts expected.
        expected_adverts: Optional list of AdvertMetadata for matching unique_ids.
        mode: Analysis mode - "timecode" or "frame".
        
    Returns:
        Tuple of (final_result, ensemble_stats).
    """
    parsed_results = []
    valid_count = 0
    invalid_count = 0
    
    for response_text, error, _ in raw_responses:
        if error or not response_text:
            invalid_count += 1
            continue
        
        parsed = parse_ad_break_response(response_text, expected_adverts, mode)
        if parsed.success:
            parsed.total_expected = expected_advert_count
            parsed_results.append(parsed)
            valid_count += 1
        else:
            invalid_count += 1
    
    stats = EnsembleStats(
        total_responses=len(raw_responses),
        valid_responses=valid_count,
        invalid_responses=invalid_count,
    )
    
    if not parsed_results:
        return AdBreakResult(
            success=False,
            error="No valid responses from ensemble",
        ), stats
    
    # Vote on adverts only (ident removed from output)
    voted_adverts, voting_method, voting_details = vote_on_adverts(parsed_results, mode)
    stats.voting_method = voting_method
    stats.advert_voting_details = voting_details

    # Wire duration_seconds from expected_adverts if available
    if expected_adverts:
        for i, advert in enumerate(voted_adverts):
            if advert.duration_seconds is None and i < len(expected_adverts):
                if expected_adverts[i].duration_seconds is not None:
                    advert.duration_seconds = expected_adverts[i].duration_seconds

    return AdBreakResult(
        success=True,
        ident_end_timecode=None,
        ident_end_frame=None,
        adverts=voted_adverts,
        total_found=len(voted_adverts),
        total_expected=expected_advert_count,
    ), stats
