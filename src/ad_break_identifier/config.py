"""Configuration for ad break analysis."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .models import AdBreakMetadata, AdvertMetadata, ProgrammeMetadata

logger = logging.getLogger(__name__)


@dataclass
class AdBreakConfig:
    """Configuration for ad break analysis with ensemble support."""
    
    # Video
    video_url: str
    
    # Metadata
    ad_break_metadata: AdBreakMetadata | None = None
    metadata_file: str | None = None
    ad_break_index: int = 0  # Index when metadata file has multiple ad breaks
    prog_before: str | None = None
    prog_after: str | None = None
    adverts_cli: list[str] | None = None
    
    # API
    api_base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model_name: str = "Qwen/Qwen3.5-4B"
    
    # Video sampling
    fps: float = 1.0
    
    # Mode: timecode or frame
    mode: Literal["timecode", "frame"] = "timecode"
    
    # Ensemble
    enable_ensemble: bool = True
    ensemble_size: int = 5
    ensemble_delay: float = 10.0
    
    # Output
    output_format: Literal["json", "text"] = "json"
    verbose: bool = False  # Show detailed progress information

    def __post_init__(self):
        """Validate mode after initialization."""
        if self.mode not in ["timecode", "frame"]:
            raise ValueError(f"Invalid mode: {self.mode}. Must be 'timecode' or 'frame'")


def parse_cli_metadata(
    prog_before: str | None,
    prog_after: str | None,
    adverts_cli: list[str] | None,
) -> AdBreakMetadata | None:
    """Parse metadata from CLI arguments.
    
    Args:
        prog_before: "Title,Channel" string.
        prog_after: "Title,Channel" string.
        adverts_cli: List of "id|advertiser|brand|category|duration" strings.
        
    Returns:
        AdBreakMetadata or None if incomplete.
    """
    if not prog_before or not prog_after:
        return None
    
    prog_before_parts = prog_before.split(",", 1)
    if len(prog_before_parts) != 2:
        raise ValueError(f"Invalid --prog-before format: {prog_before}")
    
    programme_before = ProgrammeMetadata(
        title=prog_before_parts[0].strip(),
        channel=prog_before_parts[1].strip(),
    )
    
    prog_after_parts = prog_after.split(",", 1)
    if len(prog_after_parts) != 2:
        raise ValueError(f"Invalid --prog-after format: {prog_after}")
    
    programme_after = ProgrammeMetadata(
        title=prog_after_parts[0].strip(),
        channel=prog_after_parts[1].strip(),
    )
    
    adverts = []
    if adverts_cli:
        for advert_str in adverts_cli:
            parts = advert_str.split("|")
            if len(parts) != 5:
                raise ValueError(f"Invalid --advert format: {advert_str}")
            
            adverts.append(AdvertMetadata(
                unique_id=parts[0].strip(),
                advertiser=parts[1].strip(),
                brand=parts[2].strip(),
                category=parts[3].strip(),
                duration_seconds=int(parts[4].strip()),
            ))
    
    if not adverts:
        return None
    
    return AdBreakMetadata(
        programme_before=programme_before,
        programme_after=programme_after,
        adverts=adverts,
    )


def load_metadata_from_file(file_path: str, ad_break_index: int = 0) -> AdBreakMetadata:
    """Load metadata from JSON file.
    
    Args:
        file_path: Path to JSON metadata file.
        ad_break_index: Index of ad break to load (for nested ad_breaks array format).
        
    Returns:
        AdBreakMetadata object.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Metadata file not found: {file_path}")
    
    with open(path, "r") as f:
        data = json.load(f)
    
    if "ad_breaks" in data:
        if ad_break_index >= len(data["ad_breaks"]):
            raise ValueError(f"ad_break_index {ad_break_index} out of range (max: {len(data['ad_breaks']) - 1})")
        break_data = data["ad_breaks"][ad_break_index]
    else:
        break_data = data
    
    adverts = []
    for advert in break_data.get("adverts", []):
        duration = advert.get("duration_seconds")
        if duration is not None and duration not in [10, 20, 30, 60]:
            logger.warning(
                f"Skipping advert {advert.get('unique_id', 'unknown')} "
                f"with invalid duration: {duration}"
            )
            continue
        adverts.append(AdvertMetadata(
            unique_id=advert.get("unique_id", ""),
            advertiser=advert.get("advertiser", ""),
            brand=advert.get("brand", ""),
            category=advert.get("category", ""),
            duration_seconds=duration,
        ))
    
    return AdBreakMetadata(
        programme_before=ProgrammeMetadata(**break_data["programme_before"]),
        programme_after=ProgrammeMetadata(**break_data["programme_after"]),
        adverts=adverts,
        channel_ident_expected=break_data.get("channel_ident_expected", True),
    )


def load_config(config_dict: dict | None = None) -> AdBreakConfig:
    """Load configuration from dict, environment, and defaults.
    
    Args:
        config_dict: Optional dict with configuration overrides.
        
    Returns:
        AdBreakConfig object.
    """
    config_dict = config_dict or {}
    
    return AdBreakConfig(
        video_url=config_dict.get("video_url", ""),
        metadata_file=config_dict.get("metadata_file"),
        ad_break_index=config_dict.get("ad_break_index", 0),
        prog_before=config_dict.get("prog_before"),
        prog_after=config_dict.get("prog_after"),
        adverts_cli=config_dict.get("adverts_cli"),
        api_base_url=config_dict.get("api_base_url", "http://localhost:8000/v1"),
        api_key=config_dict.get("api_key", "EMPTY"),
        model_name=config_dict.get("model_name", "Qwen/Qwen3.5-4B"),
        fps=config_dict.get("fps", 1.0),
        mode=config_dict.get("mode", "timecode"),
        enable_ensemble=config_dict.get("enable_ensemble", True),
        ensemble_size=config_dict.get("ensemble_size", 5),
        ensemble_delay=config_dict.get("ensemble_delay", 10.0),
        output_format=config_dict.get("output_format", "json"),
    )
