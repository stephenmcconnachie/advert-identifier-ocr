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

    If ``detection`` is set, the module automatically computes
    ``adjusted_start_broadcast`` from the clip_offset, duration, and
    detection last-seconds (clip-relative).

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

    for i, update in enumerate(updates):
        if i >= len(break_data["adverts"]):
            break  # safety: fewer adverts in state than updates

        advert = break_data["adverts"][i]
        if "status" in update:
            advert["status"] = update["status"]
        if "detection" in update:
            detection = update["detection"]
            advert["detection"] = detection

            # Compute broadcast-absolute adjusted start
            duration: int | None = advert.get("scheduled_duration_seconds")
            last_seconds_clip = detection.get("last_seconds_clip")
            if last_seconds_clip is None and detection.get("last_frame") is not None:
                last_seconds_clip = detection["last_frame"] / fps

            if duration is not None and last_seconds_clip is not None:
                last_bcast = clip_offset + last_seconds_clip
                adjusted_start = last_bcast - duration
                detection["last_seconds_broadcast"] = round(last_bcast, 3)
                detection["adjusted_start_broadcast"] = round(adjusted_start, 3)

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
