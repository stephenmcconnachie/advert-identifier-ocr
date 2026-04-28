#!/usr/bin/env python3
"""CLI for frame refinement stage."""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .refinement import refine_advert_timecodes


def _log_verbose(verbose: bool, message: str) -> None:
    """Print verbose message with timestamp to stderr."""
    if verbose:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


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
        "--refine-fps",
        type=float,
        default=25.0,
        help="FPS for refinement stage (default: 25.0)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed progress information during execution",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print raw model responses and save to debug_refine.json",
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

    verbose = parsed_args.verbose
    debug_mode = parsed_args.debug

    _log_verbose(verbose, "=" * 60)
    _log_verbose(verbose, "Frame Refinement Stage Starting")
    _log_verbose(verbose, f"Input XML: {xml_path}")
    _log_verbose(verbose, f"Video URL: {parsed_args.video_url}")
    _log_verbose(verbose, f"Output XML: {output_path}")
    _log_verbose(verbose, f"Ensemble: {parsed_args.ensemble_size} calls per advert")
    _log_verbose(verbose, "=" * 60)

    result, stats = refine_advert_timecodes(
        xml_path=str(xml_path),
        video_url=parsed_args.video_url,
        output_path=output_path,
        metadata_json=parsed_args.json_file,
        api_base_url=parsed_args.api_base_url,
        api_key=parsed_args.api_key,
        model=parsed_args.model,
        ensemble_size=parsed_args.ensemble_size,
        ensemble_delay=parsed_args.ensemble_delay,
        fps=parsed_args.refine_fps,
        verbose=verbose,
        debug_mode=debug_mode,
    )

    if debug_mode and stats:
        debug_output = {
            "result": {
                "success": result.success,
                "total_refined": result.total_refined,
                "total_fallback": result.total_fallback,
            },
            "refinement_stats": {
                "total_responses": stats.total_responses,
                "valid_responses": stats.valid_responses,
                "invalid_responses": stats.invalid_responses,
                "advert_voting_details": stats.advert_voting_details,
            },
        }

        debug_path = Path("debug_refine.json")
        with open(debug_path, "w") as f:
            json.dump(debug_output, f, indent=2)

        _log_verbose(verbose, f"\n{'='*80}")
        _log_verbose(verbose, "DEBUG MODE: Saved debug_refine.json with refinement data")
        _log_verbose(verbose, f"{'='*80}\n")

        if stats.advert_voting_details:
            _log_verbose(verbose, f"\n{'='*80}")
            _log_verbose(verbose, "ENSEMBLE VOTING BREAKDOWN:")
            _log_verbose(verbose, f"{'='*80}")
            for detail in stats.advert_voting_details:
                pos = detail.get("advert_position", "?")
                brand = detail.get("brand", "Unknown")
                advert_id = detail.get("advert_id", "Unknown")
                voted_value = detail.get("voted_value", "N/A")

                _log_verbose(verbose, f"\nAdvert {pos}: {brand} ({advert_id})")
                _log_verbose(verbose, f"  Final voted frame: {voted_value}")

                response_values = detail.get("response_values", [])
                if response_values:
                    _log_verbose(verbose, "  Individual responses:")
                    for rv in response_values:
                        resp_num = rv.get("response_num", "?")
                        value = rv.get("value", "N/A")
                        _log_verbose(verbose, f"    - Response {resp_num}: {value}")
            _log_verbose(verbose, f"\n{'='*80}\n")

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