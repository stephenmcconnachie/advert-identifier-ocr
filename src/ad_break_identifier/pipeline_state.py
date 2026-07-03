"""Pipeline state file: persistent tracking of advert data across pipeline stages.

The state file is a JSON document created at Stage 1 (metadata extraction) and
updated by each subsequent stage. It tracks every advert's progression through
the pipeline and provides the final ``adjusted_start_broadcast`` for clip
extraction.

State file naming convention:
    ``{video_stem}_pipeline_state.json``  (alongside the metadata JSON)

Coordinate reference:
    All ``*_broadcast`` values are seconds from the start of the full broadcast
    video file. All ``*_clip`` values are seconds from the start of the
    extracted frame range (see ``docs/coordinate-systems.md`` for details).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PIPELINE_VERSION = 2

# ── Status lifecycle ──────────────────────────────────────────────────────
STATUS_METADATA_EXTRACTED = "metadata_extracted"
STATUS_DETECTED = "detected"              # after OCR detection (5 FPS)
STATUS_CLIPPED = "clipped"                # after final advert clip extraction


# ── Public API ────────────────────────────────────────────────────────────


def derive_state_path(metadata_json_path: str) -> str:
    """Derive the pipeline state file path from a metadata JSON path.

    ``video_2024-03-26_ITV1HD_09:00:00_metadata.json``
        → ``video_2024-03-26_ITV1HD_09:00:00_pipeline_state.json``
    """
    p = Path(metadata_json_path)
    stem = p.stem
    # Remove trailing ``_metadata`` suffix if present
    if stem.endswith("_metadata"):
        stem = stem[:-9]
    return str(p.parent / f"{stem}_pipeline_state.json")


def create_initial_state(
    metadata_json_path: str,
    before_secs: float,
) -> dict[str, Any]:
    """Create the initial pipeline state from metadata JSON.

    Reads the metadata JSON, computes ``clip_offset`` per ad break, and
    initialises each advert with ``status = "metadata_extracted"``.

    Args:
        metadata_json_path: Path to the ``_metadata.json`` file.
        before_secs: Seconds before ad break start used in frame extraction.

    Returns:
        The state dictionary (already containing ``pipeline_version``).

    Raises:
        FileNotFoundError: If the metadata JSON doesn't exist.
        KeyError: If required fields are missing from the metadata.
    """
    resolved_path = Path(metadata_json_path).resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_json_path}")
    with open(resolved_path) as f:
        data = json.load(f)

    video_info = data["video_info"]
    video_start_tod: str = video_info["start_time"]  # e.g. "09:45:00"
    video_start_secs = _tod_to_seconds(video_start_tod)

    ad_breaks: list[dict[str, Any]] = []
    for break_data in data.get("ad_breaks", []):
        break_start_tod: str = break_data["start_time"]
        break_start_secs = _tod_to_seconds(break_start_tod)
        clip_offset = break_start_secs - video_start_secs - before_secs

        adverts_out: list[dict[str, Any]] = []
        for adv in break_data.get("adverts", []):
            adverts_out.append({
                "unique_id": adv.get("unique_id", ""),
                "brand": adv.get("brand", ""),
                "advertiser": adv.get("advertiser", ""),
                "category": adv.get("category", ""),
                "scheduled_duration_seconds": adv.get("duration_seconds"),
                "status": STATUS_METADATA_EXTRACTED,
                "detection": None,
            })

        ad_breaks.append({
            "index": break_data.get("index", len(ad_breaks) + 1),
            "start_time": break_start_tod,
            "clip_offset": round(clip_offset, 3),
            "adverts": adverts_out,
        })

    state: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "before_secs": before_secs,
        "video_info": {
            "filepath": video_info.get("filepath", ""),
            "start_time": video_start_tod,
        },
        "ad_breaks": ad_breaks,
    }
    return state


def read_state(path: str) -> dict[str, Any]:
    """Read pipeline state from a JSON file.

    Args:
        path: Path to the ``_pipeline_state.json`` file.

    Returns:
        The state dictionary.
    """
    resolved_path = Path(path).resolve()
    with open(resolved_path) as f:
        return json.load(f)


def write_state(path: str, state: dict[str, Any]) -> None:
    """Write pipeline state to a JSON file.

    Args:
        path: Path to write the ``_pipeline_state.json`` file.
        state: The state dictionary.
    """
    resolved_path = Path(path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved_path, "w") as f:
        json.dump(state, f, indent=2)


def update_break_adverts(
    state: dict[str, Any],
    ad_break_index: int,
    updates: list[dict[str, Any]],
    fps: float = 5.0,
) -> dict[str, Any]:
    """Update advert data for one ad break in the state.

    ``ad_break_index`` is 1-based (matching the CLI ``--ad-break-index``
    convention).  ``updates`` is a list of dicts, one per advert in break
    order.  Each dict may contain any of::

        {"status": "...",
         "detection": {...}}

    **Pass 1** writes status and detection data.  **Pass 2** derives
    ``adjusted_start_broadcast`` for every advert using the effective end
    position (``last_seconds_clip``, which may be 25fps-refined):

    - Adverts with known ``scheduled_duration_seconds``:
      ``start = last_seconds_clip - duration``
    - Last advert in a multi-advert break:
      ``start = prev_advert_effective_end + 1 source frame (1/25s)``
      (the effective end of the preceding advert becomes the anchor)
    - Single-advert breaks (no duration, no preceding advert):
      logged to a per-video ``_single_advert_breaks.log`` file; no start
      computed.

    Args:
        state: The current pipeline state (mutated in-place and returned).
        ad_break_index: 1-based index of the ad break.
        updates: Per-advert update dicts in break order.
        fps: Frame extraction rate (used for frame-to-seconds conversion
            if last_seconds_clip is missing).

    Returns:
        The mutated state (same object as input).

    Raises:
        IndexError: If ``ad_break_index`` is out of range.
    """
    break_data = state["ad_breaks"][ad_break_index - 1]
    clip_offset: float = break_data["clip_offset"]
    before_secs: float = state.get("before_secs", 10.0)

    # ── Pass 1: write status and detection data ──────────────────────
    for i, update in enumerate(updates):
        if i >= len(break_data["adverts"]):
            break
        advert = break_data["adverts"][i]
        if "status" in update:
            advert["status"] = update["status"]
        if "detection" in update:
            advert["detection"] = update["detection"]

    # ── Pass 2: derive start for every advert ────────────────────────
    # Non-last adverts (known duration): start = effective_end - duration
    #   where effective_end is last_seconds_clip (which may be refined to
    #   25fps precision).  Previously this used last_frame / fps (the raw
    #   5fps frame), which was up to 4 source frames too early when the
    #   end had been refined, causing clips to open on the previous advert.
    # Last advert (multi-advert break):
    #     start = prev_effective_end + 1 source frame
    # Single-advert break: logged and skipped
    prev_effective_end: float | None = None
    for i, advert in enumerate(break_data["adverts"]):
        detection = advert.get("detection")
        if detection is None or detection.get("last_seconds_clip") is None:
            _log_unmatched(state, break_data["index"], advert)

            duration = advert.get("scheduled_duration_seconds")
            if prev_effective_end is not None and duration is not None:
                estimated_end = prev_effective_end + duration
                start_seconds_clip = prev_effective_end + (1.0 / 25.0)
                start_broadcast = clip_offset + start_seconds_clip
                if detection is None:
                    detection = {}
                    advert["detection"] = detection
                detection["last_seconds_clip"] = estimated_end
                detection["start_seconds_clip"] = round(start_seconds_clip, 3)
                detection["adjusted_start_broadcast"] = round(
                    start_broadcast, 3
                )
                detection["match_tier"] = "estimated"
                prev_effective_end = estimated_end
                logger.info(
                    "  %s: estimated end at %.3fs from previous advert "
                    "(unmatched)",
                    advert.get("brand", "unknown"),
                    estimated_end,
                )
            else:
                prev_effective_end = None
            continue

        effective_end: float = detection["last_seconds_clip"]
        duration: int | None = advert.get("scheduled_duration_seconds")

        if duration is not None:
            start_seconds_clip = effective_end - duration + (1.0 / 25.0)
        elif prev_effective_end is not None:
            start_seconds_clip = prev_effective_end + (1.0 / 25.0)  # one source frame
            detected_duration = effective_end - start_seconds_clip
            detection["detected_duration_seconds"] = round(detected_duration, 1)
        else:
            # Single-advert break — no duration, no preceding advert
            unique_id = advert.get("unique_id", "unknown")
            brand = advert.get("brand", "unknown")
            try:
                log_path = _single_advert_log_path(state)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a") as f:
                    f.write(
                        f"{datetime.now().isoformat()} | "
                        f"Single-advert break: track_id={unique_id}, "
                        f"brand={brand}, effective_end={effective_end}, "
                        f"clip_offset={clip_offset}, fps={fps}\n"
                    )
                logger.warning(
                    "Single-advert break: %s / %s — logged to %s",
                    unique_id, brand, log_path,
                )
            except OSError:
                logger.warning(
                    "Single-advert break: %s / %s (could not write log)",
                    unique_id, brand,
                )
            prev_effective_end = None
            continue

        start_broadcast = clip_offset + start_seconds_clip

        detection["start_seconds_clip"] = round(start_seconds_clip, 3)
        detection["adjusted_start_broadcast"] = round(start_broadcast, 3)

        prev_effective_end = effective_end

    return state


def get_adjusted_starts(
    state: dict[str, Any],
    ad_break_index: int,
) -> list[float | None]:
    """Get ``adjusted_start_broadcast`` for each advert in a break.

    Returns ``None`` for adverts that haven't been detected yet.

    Args:
        state: The pipeline state.
        ad_break_index: 1-based index of the ad break.

    Returns:
        List of ``adjusted_start_broadcast`` values (or ``None``) in advert
        order.
    """
    break_data = state["ad_breaks"][ad_break_index - 1]
    results: list[float | None] = []
    for adv in break_data["adverts"]:
        det = adv.get("detection")
        if det and det.get("adjusted_start_broadcast") is not None:
            results.append(det["adjusted_start_broadcast"])
        else:
            results.append(None)
    return results


# ── Internal helpers ──────────────────────────────────────────────────────


def _single_advert_log_path(state: dict[str, Any]) -> Path:
    """Derive the single-advert break log path from the state video path."""
    video_path = Path(state.get("video_info", {}).get("filepath", ""))
    return video_path.parent / f"{video_path.stem}_single_advert_breaks.log"


def _unmatched_adverts_log_path(state: dict[str, Any]) -> Path:
    """Derive the unmatched-adverts log path from the state video path."""
    video_path = Path(state.get("video_info", {}).get("filepath", ""))
    return video_path.parent / f"{video_path.stem}_unmatched_adverts.log"


def _log_unmatched(
    state: dict[str, Any],
    break_index: int,
    advert: dict[str, Any],
) -> None:
    """Append an entry to the unmatched-adverts log for this video."""
    from datetime import datetime

    unique_id = advert.get("unique_id", "unknown")
    brand = advert.get("brand", "unknown")
    duration = advert.get("scheduled_duration_seconds", "unknown")
    try:
        log_path = _unmatched_adverts_log_path(state)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(
                f"{datetime.now().isoformat()} | "
                f"Break {break_index}: track_id={unique_id}, "
                f"brand={brand}, duration={duration}\n"
            )
        logger.info(
            "Unmatched advert %s / %s — logged to %s",
            unique_id, brand, log_path,
        )
    except OSError:
        logger.warning(
            "Unmatched advert %s / %s (could not write log)",
            unique_id, brand,
        )


def _tod_to_seconds(tod: str) -> float:
    """Convert HH:MM:SS time-of-day to seconds since midnight.

    Args:
        tod: Time string in HH:MM:SS format.

    Returns:
        Total seconds as float.
    """
    parts = tod.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    raise ValueError(f"Invalid time-of-day format: {tod}")
