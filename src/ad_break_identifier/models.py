"""Data models for ad break sequence identification."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ProgrammeMetadata:
    """Metadata for a TV programme segment."""
    title: str
    channel: str


@dataclass
class AdvertMetadata:
    """Metadata for a single advertisement."""
    unique_id: str
    advertiser: str
    brand: str
    category: str
    duration_seconds: int | None = None

    def __post_init__(self):
        if self.duration_seconds is not None and self.duration_seconds not in [10, 20, 30, 60]:
            raise ValueError(
                f"Invalid duration: {self.duration_seconds}. "
                "Must be 10, 20, 30, or 60 (or None for unknown)."
            )


@dataclass
class AdBreakMetadata:
    """Complete metadata for an ad break sequence."""
    programme_before: ProgrammeMetadata
    programme_after: ProgrammeMetadata
    adverts: list[AdvertMetadata] = field(default_factory=list)
    channel_ident_expected: bool = True

    def __post_init__(self):
        if not self.adverts:
            raise ValueError("At least one advert must be specified")


@dataclass
class AdvertResult:
    """Result for a single advert detection."""
    timecode: str | None = None
    frame: int | None = None
    advert_id: str = ""
    brand: str = ""
    advertiser: str = ""
    category: str = ""
    description: str = ""
    confidence: float = 0.0
    duration_seconds: int | None = None


@dataclass
class AdBreakResult:
    """Complete result for ad break analysis."""
    success: bool = False
    error: str | None = None
    ident_end_timecode: str | None = None
    ident_end_frame: int | None = None
    ident_description: str | None = None
    adverts: list[AdvertResult] = field(default_factory=list)
    total_found: int = 0
    total_expected: int = 0


@dataclass
class EnsembleStats:
    """Statistics from ensemble voting."""
    total_responses: int
    valid_responses: int
    invalid_responses: int
    voting_method: str = "median"
    advert_voting_details: list[dict] = field(default_factory=list)


@dataclass
class RefinedAdvertResult:
    """Result for a single advert after frame refinement."""
    original_timecode: str | None = None
    refined_timecode: str | None = None
    refined_clip_frame: int | None = None
    confidence: str = "unknown"
    refinement_status: str = "pending"
    description: str = ""


@dataclass
class RefinedAdBreakResult:
    """Complete result for frame refinement stage."""
    success: bool = False
    error: str | None = None
    adverts: list[RefinedAdvertResult] = field(default_factory=list)
    total_refined: int = 0
    total_fallback: int = 0


@dataclass
class RefinementStats:
    """Statistics from refinement ensemble voting."""
    total_responses: int = 0
    valid_responses: int = 0
    invalid_responses: int = 0
    advert_voting_details: list[dict] = field(default_factory=list)
