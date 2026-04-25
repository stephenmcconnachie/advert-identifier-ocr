#!/usr/bin/env python3
"""CLI for frame refinement stage."""

import argparse
import logging
import sys
from pathlib import Path

from .refinement import refine_advert_timecodes


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Refine advert timecodes to frame-accurate boundaries using 24fps analysis"
    )

    parser.add_argument(
        "--xml-file",
        required=True,
        help="Path to XML from primary detection",
    )
    parser.add_argument(
        "--video-url",
        required=True,
        help="URL to video",
    )
    parser.add_argument(
        "--json-file",
        help="Original metadata JSON (for advert advertiser/category lookup)",
    )
    parser.add_argument(
        "--output",
        help="Output XML path (default: <xml_file>_refined.xml)",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:8000/v1",
        help="vLLM API base URL (default: http://localhost:8000/v1)",
    )
    parser.add_argument(
        "--api-key",
        default="EMPTY",
        help="API key (default: EMPTY)",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3.5-4B",
        help="Model name (default: Qwen/Qwen3.5-4B)",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=3,
        help="Number of ensemble calls per advert (default: 3)",
    )
    parser.add_argument(
        "--ensemble-delay",
        type=float,
        default=5.0,
        help="Delay between ensemble calls in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    return parser


def main(args: list[str] | None = None) -> int:
    """Main entry point."""
    parser = create_parser()
    parsed_args = parser.parse_args(args)

    logging.basicConfig(
        level=getattr(logging, parsed_args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )
    logger = logging.getLogger(__name__)

    xml_path = Path(parsed_args.xml_file)
    if not xml_path.exists():
        logger.error(f"XML file not found: {xml_path}")
        return 1

    output_path = parsed_args.output
    if output_path is None:
        output_path = str(xml_path.parent / f"{xml_path.stem}_refined.xml")

    logger.info("=" * 60)
    logger.info("Frame Refinement Stage Starting")
    logger.info(f"Input XML: {xml_path}")
    logger.info(f"Video URL: {parsed_args.video_url}")
    logger.info(f"Output XML: {output_path}")
    logger.info(f"Ensemble: {parsed_args.ensemble_size} calls per advert")
    logger.info("=" * 60)

    result = refine_advert_timecodes(
        xml_path=str(xml_path),
        video_url=parsed_args.video_url,
        output_path=output_path,
        metadata_json=parsed_args.json_file,
        api_base_url=parsed_args.api_base_url,
        api_key=parsed_args.api_key,
        model=parsed_args.model,
        ensemble_size=parsed_args.ensemble_size,
        ensemble_delay=parsed_args.ensemble_delay,
    )

    if result.success:
        logger.info("=" * 60)
        logger.info(f"Refinement complete: {result.total_refined} refined, {result.total_fallback} fallback")
        logger.info(f"Output: {output_path}")
        logger.info("=" * 60)
        return 0
    else:
        logger.error(f"Refinement failed: {result.error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())