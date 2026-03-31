"""Ad Break Identifier - Ad break sequence identification package."""

from .main import main, create_parser, run_ad_break_analysis
from .config import load_config, AdBreakConfig
from .models import (
    ProgrammeMetadata,
    AdvertMetadata,
    AdBreakMetadata,
    AdvertResult,
    AdBreakResult,
    EnsembleStats,
)
from .prompts import build_ad_break_prompt
from .response_parser import parse_ad_break_response

__all__ = [
    "main",
    "create_parser",
    "run_ad_break_analysis",
    "load_config",
    "AdBreakConfig",
    "ProgrammeMetadata",
    "AdvertMetadata",
    "AdBreakMetadata",
    "AdvertResult",
    "AdBreakResult",
    "EnsembleStats",
    "build_ad_break_prompt",
    "parse_ad_break_response",
]
