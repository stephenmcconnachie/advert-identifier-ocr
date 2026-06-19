"""Ad Break Identifier - OCR-based ad break sequence identification package."""

from .config import load_config, AdBreakConfig
from .models import (
    ProgrammeMetadata,
    AdvertMetadata,
    AdBreakMetadata,
    AdvertResult,
    AdBreakResult,
)

__all__ = [
    "load_config",
    "AdBreakConfig",
    "ProgrammeMetadata",
    "AdvertMetadata",
    "AdBreakMetadata",
    "AdvertResult",
    "AdBreakResult",
]
