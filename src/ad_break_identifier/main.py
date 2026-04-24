"""Main entry point for ad break sequence identification."""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .api_client import create_vllm_client, run_ensemble_sync
from .config import (
    AdBreakConfig,
    load_config,
    load_metadata_from_file,
    parse_cli_metadata,
)
from .ensemble import apply_ensemble_voting
from .models import AdBreakResult, EnsembleStats
from .prompts import build_ad_break_prompt


def _log_verbose(config: AdBreakConfig, message: str) -> None:
    """Print verbose message with timestamp to stderr."""
    if config.verbose:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def _decode_escapes(text: str) -> str:
    """Decode escaped newlines and other escape sequences in text."""
    if not text:
        return text
    # Handle both actual newlines and escaped newlines
    return text.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')


def _convert_debug_to_markdown(debug_output: dict) -> str:
    """Convert debug output to markdown format."""
    lines = []
    
    lines.append("# Ad Break Analysis Debug Log\n")
    
    # Result summary
    lines.append("## Result Summary\n")
    result = debug_output.get("result", {})
    lines.append(f"- **Success**: {result.get('success', 'N/A')}")
    if result.get('error'):
        lines.append(f"- **Error**: {result.get('error')}")
    lines.append(f"- **Total Found**: {result.get('total_found', 'N/A')}")
    lines.append(f"- **Total Expected**: {result.get('total_expected', 'N/A')}")
    lines.append("")
    
    # Ensemble stats
    if debug_output.get("ensemble_stats"):
        lines.append("## Ensemble Statistics\n")
        stats = debug_output["ensemble_stats"]
        lines.append(f"- **Total Responses**: {stats.get('total_responses', 'N/A')}")
        lines.append(f"- **Valid Responses**: {stats.get('valid_responses', 'N/A')}")
        lines.append(f"- **Invalid Responses**: {stats.get('invalid_responses', 'N/A')}")
        lines.append(f"- **Voting Method**: {stats.get('voting_method', 'N/A')}")
        lines.append("")
        
        # Voting breakdown
        voting_details = stats.get("advert_voting_details", [])
        if voting_details:
            lines.append("## Ensemble Voting Breakdown\n")
            for detail in voting_details:
                pos = detail.get("advert_position", "?")
                advert_id = detail.get("advert_id", "Unknown")
                brand = detail.get("brand", "Unknown")
                value_type = detail.get("value_type", "value")
                voted_value = detail.get("voted_value", "N/A")
                
                lines.append(f"### Advert {pos}: {brand} ({advert_id})\n")
                lines.append(f"**Final voted {value_type}**: `{voted_value}`\n")
                
                # Show each response's contribution
                response_values = detail.get("response_values", [])
                if response_values:
                    lines.append("**Individual responses**:\n")
                    for rv in response_values:
                        resp_num = rv.get("response_num", "?")
                        value = rv.get("value", "N/A")
                        lines.append(f"  - Response {resp_num}: {value_type}={value}")
                    lines.append("")
                    
                    # Show what was used for voting
                    used_values = detail.get("used_for_voting", [])
                    if used_values:
                        lines.append(f"**Values used for voting** (median of {len(used_values)} responses): {used_values}\n")
                lines.append("")
    
    # Prompt
    lines.append("## Prompt\n")
    lines.append("```")
    prompt_text = _decode_escapes(debug_output.get("prompt", "N/A"))
    lines.append(prompt_text)
    lines.append("```\n")
    
    # Raw responses
    raw_responses = debug_output.get("raw_responses", [])
    if raw_responses:
        lines.append("## Raw Model Responses\n")
        
        for i, response in enumerate(raw_responses, 1):
            lines.append(f"### Response {i}\n")
            
            if isinstance(response, list) and len(response) >= 3:
                resp_text, resp_error, resp_dict = response
                
                if resp_error:
                    lines.append(f"**Error**: {_decode_escapes(resp_error)}\n")
                elif resp_dict and isinstance(resp_dict, dict):
                    # Extract useful parts from the response dict
                    choices = resp_dict.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        
                        # Content (FULL - not truncated)
                        content = message.get("content", "")
                        if content:
                            lines.append("**Content**:\n")
                            lines.append("```xml")
                            lines.append(_decode_escapes(content))
                            lines.append("```\n")
                        
                        # Reasoning (FULL - not truncated)
                        reasoning = message.get("reasoning", "")
                        if reasoning:
                            lines.append("**Reasoning**:\n")
                            lines.append("```")
                            lines.append(_decode_escapes(reasoning))
                            lines.append("```\n")
                    
                    # Token usage
                    usage = resp_dict.get("usage", {})
                    if usage:
                        lines.append("**Token Usage**:\n")
                        lines.append(f"- Prompt tokens: {usage.get('prompt_tokens', 'N/A')}")
                        lines.append(f"- Completion tokens: {usage.get('completion_tokens', 'N/A')}")
                        lines.append(f"- Total tokens: {usage.get('total_tokens', 'N/A')}")
                        lines.append("")
                elif resp_text:
                    lines.append("**Text Response**:\n")
                    lines.append("```")
                    lines.append(_decode_escapes(resp_text))
                    lines.append("```\n")
                else:
                    lines.append("*(empty response)*\n")
            else:
                lines.append("```")
                lines.append(_decode_escapes(str(response)))
                lines.append("```\n")
    
    return "\n".join(lines)


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Analyze advertisement break sequences in TV broadcast videos"
    )
    
    parser.add_argument(
        "-v", "--video-url",
        required=True,
        help="URL to video with timecode overlay"
    )
    
    metadata_group = parser.add_argument_group("Metadata (JSON file or CLI args)")
    metadata_group.add_argument(
        "--metadata-file",
        type=str,
        help="JSON file with complete ad break metadata"
    )
    metadata_group.add_argument(
        "--ad-break-index",
        type=int,
        default=None,
        help="Index of ad break to process when metadata file contains multiple breaks (1-based, auto-detected from filename pattern XXofYY.mp4, default: 1)"
    )
    metadata_group.add_argument(
        "--prog-before",
        type=str,
        metavar="TITLE,CHANNEL",
        help="Programme before ad break: 'Lorraine,ITV1'"
    )
    metadata_group.add_argument(
        "--prog-after",
        type=str,
        metavar="TITLE,CHANNEL",
        help="Programme after ad break: 'Daybreak,ITV1'"
    )
    metadata_group.add_argument(
        "--advert",
        type=str,
        action="append",
        metavar="ID|ADVERTISER|BRAND|CATEGORY|DURATION",
        help="Advert: 'adv_001|Tesco PLC|Tesco|retail|20' (can specify multiple)"
    )
    
    ensemble_group = parser.add_argument_group("Ensemble voting (enabled by default)")
    ensemble_group.add_argument(
        "--no-ensemble",
        action="store_true",
        help="Disable ensemble voting (use single API call)"
    )
    ensemble_group.add_argument(
        "--ensemble-size",
        type=int,
        default=5,
        help="Number of ensemble members (default: 5)"
    )
    ensemble_group.add_argument(
        "--ensemble-delay",
        type=float,
        default=10.0,
        help="Delay between ensemble requests in seconds (default: 10.0)"
    )
    
    parser.add_argument(
        "--api-base-url",
        type=str,
        help="vLLM API base URL"
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model name (default: Qwen/Qwen3.5-4B)"
    )
    parser.add_argument(
        "-f", "--fps",
        type=float,
        default=1.0,
        help="Frame sampling rate (default: 1.0)"
    )
    parser.add_argument(
        "--mode",
        choices=["timecode", "frame"],
        default="timecode",
        help="Analysis mode: timecode (HH:MM:SS.mmm) or frame count (default: timecode)"
    )
    parser.add_argument(
        "-o", "--output-format",
        choices=["json", "text"],
        default="json",
        help="Output format (default: json)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print raw model responses and save to debug.json"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed progress information during execution"
    )
    
    return parser


def run_ad_break_analysis(
    config: AdBreakConfig,
    debug_mode: bool = False
) -> tuple[AdBreakResult, EnsembleStats | None, str | None, list[tuple[str | None, str | None, dict[str, Any] | None]] | None]:
    """Run complete ad break analysis pipeline.
    
    Args:
        config: Configuration with video URL and metadata.
        debug_mode: If True, return raw responses for debugging.
        
    Returns:
        Tuple of (AdBreakResult, EnsembleStats or None, prompt, raw_responses).
    """
    _log_verbose(config, "Step 1/5: Loading metadata...")
    if config.metadata_file:
        _log_verbose(config, f"  Loading from file: {config.metadata_file}")
        _log_verbose(config, f"  Ad break index: {config.ad_break_index}")
        config.ad_break_metadata = load_metadata_from_file(config.metadata_file, config.ad_break_index)
    elif config.prog_before and config.prog_after:
        _log_verbose(config, "  Loading from CLI arguments...")
        config.ad_break_metadata = parse_cli_metadata(
            config.prog_before,
            config.prog_after,
            config.adverts_cli,
        )
    
    if not config.ad_break_metadata:
        return AdBreakResult(
            success=False,
            error="No ad break metadata provided. Use --metadata-file or --prog-before/--prog-after/--advert",
        ), None, None, None
    
    _log_verbose(config, f"  Loaded {len(config.ad_break_metadata.adverts)} adverts")
    
    _log_verbose(config, "Step 2/5: Building prompt...")
    prompt = build_ad_break_prompt(config.ad_break_metadata, mode=config.mode)
    _log_verbose(config, f"  Prompt length: {len(prompt)} characters")
    _log_verbose(config, "  === PROMPT START ===")
    for line in prompt.split('\n'):
        _log_verbose(config, f"    {line}")
    _log_verbose(config, "  === PROMPT END ===")
    
    _log_verbose(config, "Step 3/5: Initializing API client...")
    _log_verbose(config, f"  API URL: {config.api_base_url}")
    _log_verbose(config, f"  Model: {config.model_name}")
    _log_verbose(config, f"  FPS: {config.fps}")
    client = create_vllm_client(
        base_url=config.api_base_url,
        api_key=config.api_key,
    )
    _log_verbose(config, "  API client initialized")
    
    _log_verbose(config, "Step 4/5: Making API calls...")
    raw_responses = None
    
    try:
        if config.enable_ensemble:
            _log_verbose(config, f"  Ensemble enabled: {config.ensemble_size} calls")
            _log_verbose(config, f"  Delay between calls: {config.ensemble_delay}s")
            raw_responses = run_ensemble_sync(
                client=client,
                video_url=config.video_url,
                prompt=prompt,
                model=config.model_name,
                fps=config.fps,
                ensemble_size=config.ensemble_size,
                ensemble_delay=config.ensemble_delay,
            )
            _log_verbose(config, f"  Received {len(raw_responses)} responses")
            result, stats = apply_ensemble_voting(
                raw_responses,
                expected_advert_count=len(config.ad_break_metadata.adverts),
                expected_adverts=config.ad_break_metadata.adverts,
                mode=config.mode,
            )
            return result, stats, prompt, raw_responses
        else:
            _log_verbose(config, "  Single API call (ensemble disabled)")
            from .api_client import call_vllm_single
            import asyncio
            
            response_text, error, raw_dict = asyncio.run(call_vllm_single(
                client, config.video_url, prompt, config.model_name, config.fps
            ))
            
            if error:
                _log_verbose(config, f"  API call failed: {error}")
                return AdBreakResult(
                    success=False,
                    error=f"API call failed: {error}",
                ), None, prompt, [(None, error, None)]
            
            _log_verbose(config, "  API call completed successfully")
            from .response_parser import parse_ad_break_response
            result = parse_ad_break_response(
                response_text or "",
                expected_adverts=config.ad_break_metadata.adverts,
                mode=config.mode,
            )
            result.total_expected = len(config.ad_break_metadata.adverts)
            return result, None, prompt, [(response_text or "", None, raw_dict)]
    
    except Exception as e:
        _log_verbose(config, f"  ERROR: Analysis failed: {e}")
        return AdBreakResult(
            success=False,
            error=f"Analysis failed: {e}",
        ), None, prompt, raw_responses


def format_json(result: AdBreakResult, ensemble_stats: EnsembleStats | None = None, mode: str = "timecode") -> str:
    """Format result as JSON with optional ensemble stats."""
    output = {
        "success": result.success,
        "error": result.error,
        "total_found": result.total_found,
        "total_expected": result.total_expected,
    }
    
    # Add mode-appropriate fields
    if mode == "frame":
        output["ident_end_frame"] = result.ident_end_frame
        output["ident_description"] = result.ident_description
        output["adverts"] = [
            {
                "frame": a.frame,
                "advert_id": a.advert_id,
                "brand": a.brand,
                "description": a.description,
                "duration_seconds": a.duration_seconds,
            }
            for a in result.adverts
        ]
    else:
        output["ident_end_timecode"] = result.ident_end_timecode
        output["ident_description"] = result.ident_description
        output["adverts"] = [
            {
                "timecode": a.timecode,
                "advert_id": a.advert_id,
                "brand": a.brand,
                "description": a.description,
                "duration_seconds": a.duration_seconds,
            }
            for a in result.adverts
        ]
    
    if ensemble_stats:
        output["ensemble"] = {
            "total_responses": ensemble_stats.total_responses,
            "valid_responses": ensemble_stats.valid_responses,
            "invalid_responses": ensemble_stats.invalid_responses,
            "voting_method": ensemble_stats.voting_method,
        }
    
    return json.dumps(output, indent=2)


def format_xml(result: AdBreakResult, mode: str = "timecode") -> str:
    """Format result as XML with just the adverts array.
    
    Args:
        result: The ad break analysis result.
        mode: Analysis mode - "timecode" or "frame".
        
    Returns:
        XML string containing the adverts in ad_break wrapper.
    """
    lines = []
    lines.append("<ad_break>")
    
    for advert in result.adverts:
        lines.append("    <advert>")
        lines.append(f"        <unique_id>{advert.advert_id}</unique_id>")
        lines.append(f"        <brand>{advert.brand}</brand>")
        if advert.duration_seconds:
            lines.append(f"        <duration_seconds>{advert.duration_seconds}</duration_seconds>")
        if mode == "frame":
            lines.append(f"        <last_frame>{advert.frame}</last_frame>")
        else:
            lines.append(f"        <last_timecode>{advert.timecode}</last_timecode>")
        if advert.description:
            lines.append(f"        <description>{advert.description}</description>")
        lines.append("    </advert>")
    
    lines.append("</ad_break>")
    return "\n".join(lines)


def _get_output_path(video_url: str, metadata_file: str | None) -> Path:
    """Generate output XML file path from video URL and metadata location.
    
    Args:
        video_url: URL or path to the video file.
        metadata_file: Path to metadata JSON file (optional).
        
    Returns:
        Path object for the output XML file.
    """
    # Extract filename from video URL
    video_path = Path(video_url.split("?")[0])  # Remove query params
    video_name = video_path.name
    
    # Replace any extension with .xml
    if "." in video_name:
        base_name = video_name.rsplit(".", 1)[0]
    else:
        base_name = video_name
    
    xml_filename = f"{base_name}.xml"
    
    # Determine output directory
    if metadata_file:
        metadata_path = Path(metadata_file)
        output_dir = metadata_path.parent
    else:
        output_dir = Path(".")
    
    return output_dir / xml_filename
    lines = []
    
    if result.success:
        if mode == "frame":
            lines.append(f"Ident End Frame: {result.ident_end_frame}")
        else:
            lines.append(f"Ident End Timecode: {result.ident_end_timecode}")
        if result.ident_description:
            lines.append(f"  Description: {result.ident_description}")
        lines.append("")
        lines.append("Adverts:")
        for i, advert in enumerate(result.adverts, 1):
            if mode == "frame":
                lines.append(f"  {i}. {advert.brand} ({advert.advert_id}): frame {advert.frame}")
            else:
                lines.append(f"  {i}. {advert.brand} ({advert.advert_id}): {advert.timecode}")
            if advert.description:
                lines.append(f"     {advert.description}")
    else:
        lines.append(f"Error: {result.error}")
    
    lines.append("")
    lines.append(f"Total: {result.total_found}/{result.total_expected} adverts found")
    
    if ensemble_stats:
        lines.append("")
        lines.append(f"Ensemble: {ensemble_stats.valid_responses}/{ensemble_stats.total_responses} valid responses")
        lines.append(f"Voting: {ensemble_stats.voting_method}")
    
    return "\n".join(lines)


def main(args: list[str] | None = None) -> int:
    """Main entry point.
    
    Args:
        args: Command line arguments (defaults to sys.argv).
        
    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = create_parser()
    parsed_args = parser.parse_args(args)
    
    # Auto-detect ad_break_index from filename if not explicitly provided
    if parsed_args.ad_break_index is None:
        video_path = Path(parsed_args.video_url.split("?")[0])  # Remove query params
        match = re.search(r'(\d{2})of(\d{2})\.mp4$', video_path.name, re.IGNORECASE)
        if match:
            parsed_args.ad_break_index = int(match.group(1))  # Already 1-based
        else:
            parsed_args.ad_break_index = 1  # Default to first ad break
    
    config = AdBreakConfig(
        video_url=parsed_args.video_url,
        metadata_file=parsed_args.metadata_file,
        ad_break_index=parsed_args.ad_break_index,
        prog_before=parsed_args.prog_before,
        prog_after=parsed_args.prog_after,
        adverts_cli=parsed_args.advert,
        api_base_url=parsed_args.api_base_url or "http://localhost:8000/v1",
        model_name=parsed_args.model or "Qwen/Qwen3.5-4B",
        fps=parsed_args.fps,
        mode=parsed_args.mode,
        enable_ensemble=not parsed_args.no_ensemble,
        ensemble_size=parsed_args.ensemble_size,
        ensemble_delay=parsed_args.ensemble_delay,
        output_format=parsed_args.output_format,
        verbose=parsed_args.verbose,
    )
    
    # Initial verbose logging
    _log_verbose(config, "=" * 60)
    _log_verbose(config, "Ad Break Identifier Starting")
    _log_verbose(config, f"Video URL: {config.video_url}")
    _log_verbose(config, f"Mode: {config.mode}")
    _log_verbose(config, f"Ensemble: {'enabled' if config.enable_ensemble else 'disabled'}")
    if config.enable_ensemble:
        _log_verbose(config, f"  Size: {config.ensemble_size}")
    _log_verbose(config, "=" * 60)
    
    debug_mode = parsed_args.debug
    
    result, ensemble_stats, prompt, raw_responses = run_ad_break_analysis(config, debug_mode)
    
    # Always write results to XML file (for pipeline automation)
    if result.success:
        xml_output = format_xml(result, mode=config.mode)
        xml_path = _get_output_path(config.video_url, config.metadata_file)
        
        try:
            with open(xml_path, "w") as f:
                f.write(xml_output)
            print(f"Results saved to: {xml_path}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not save XML output to {xml_path}: {e}", file=sys.stderr)
    
    if debug_mode and prompt is not None:
        debug_output = {
            "prompt": prompt,
            "raw_responses": raw_responses or [],
            "result": {
                "success": result.success,
                "error": result.error,
                "total_found": result.total_found,
                "total_expected": result.total_expected,
            },
        }
        if ensemble_stats:
            debug_output["ensemble_stats"] = {
                "total_responses": ensemble_stats.total_responses,
                "valid_responses": ensemble_stats.valid_responses,
                "invalid_responses": ensemble_stats.invalid_responses,
                "voting_method": ensemble_stats.voting_method,
                "advert_voting_details": ensemble_stats.advert_voting_details,
            }
        
        debug_path = Path("debug.json")
        with open(debug_path, "w") as f:
            json.dump(debug_output, f, indent=2)
        
        # Also save markdown version
        markdown_content = _convert_debug_to_markdown(debug_output)
        markdown_path = Path("debug.md")
        with open(markdown_path, "w") as f:
            f.write(markdown_content)
        
        print(f"\n{'='*80}", file=sys.stderr)
        print("DEBUG MODE: Saved debug.json and debug.md with prompt and raw responses", file=sys.stderr)
        print(f"{'='*80}\n", file=sys.stderr)
        
        if raw_responses:
            print(f"\n{'='*80}", file=sys.stderr)
            print("RAW MODEL RESPONSES:", file=sys.stderr)
            print(f"{'='*80}", file=sys.stderr)
            for i, (resp_text, resp_error, resp_dict) in enumerate(raw_responses, 1):
                print(f"\n--- Response {i} ---", file=sys.stderr)
                if resp_error:
                    print(f"ERROR: {resp_error}", file=sys.stderr)
                elif resp_dict:
                    # Print complete raw response dict with token usage
                    print(json.dumps(resp_dict, indent=2)[:5000], file=sys.stderr)
                    if len(json.dumps(resp_dict, indent=2)) > 5000:
                        print("... (truncated)", file=sys.stderr)
                elif resp_text:
                    print(resp_text[:2000], file=sys.stderr)
                    if len(resp_text) > 2000:
                        print("... (truncated)", file=sys.stderr)
                else:
                    print("(empty response)", file=sys.stderr)
            print(f"\n{'='*80}\n", file=sys.stderr)
        
        if ensemble_stats and ensemble_stats.advert_voting_details:
            print(f"\n{'='*80}", file=sys.stderr)
            print("ENSEMBLE VOTING BREAKDOWN:", file=sys.stderr)
            print(f"{'='*80}", file=sys.stderr)
            for detail in ensemble_stats.advert_voting_details:
                pos = detail.get("advert_position", "?")
                brand = detail.get("brand", "Unknown")
                advert_id = detail.get("advert_id", "Unknown")
                value_type = detail.get("value_type", "value")
                voted_value = detail.get("voted_value", "N/A")
                
                print(f"\nAdvert {pos}: {brand} ({advert_id})", file=sys.stderr)
                print(f"  Final voted {value_type}: {voted_value}", file=sys.stderr)
                
                response_values = detail.get("response_values", [])
                if response_values:
                    print("  Individual responses:", file=sys.stderr)
                    for rv in response_values:
                        resp_num = rv.get("response_num", "?")
                        value = rv.get("value", "N/A")
                        print(f"    - Response {resp_num}: {value_type}={value}", file=sys.stderr)
                
                used_values = detail.get("used_for_voting", [])
                if used_values:
                    print(f"  Values used for voting: {used_values}", file=sys.stderr)
                    print(f"  → Median = {voted_value}", file=sys.stderr)
            print(f"\n{'='*80}\n", file=sys.stderr)
    
    if config.output_format == "json":
        output = format_json(result, ensemble_stats, mode=config.mode)
    else:
        output = format_text(result, ensemble_stats, mode=config.mode)
    
    print(output)
    
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
