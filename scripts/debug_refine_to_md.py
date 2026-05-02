#!/usr/bin/env python3
"""Convert debug_refine.json to a human-readable markdown file.

Parses the refinement stage debug output and generates markdown with
collapsible sections for prompts and raw model responses.

Usage:
    python scripts/debug_refine_to_md.py                         # Output to stdout
    python scripts/debug_refine_to_md.py -i debug_refine.json -o debug_refine.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def decode_escapes(text: str) -> str:
    """Decode JSON-escaped strings to real characters (e.g. \\n -> newline).

    Args:
        text: String with JSON escape sequences.

    Returns:
        String with escape sequences converted to real characters.
    """
    if not text or text == "N/A":
        return text
    try:
        return json.loads(f'"{text}"')
    except (json.JSONDecodeError, TypeError):
        return text


def wrap_collapsible(summary: str, content: str, lang: str = "") -> str:
    """Wrap content in HTML <details>/<summary> tags for collapsible display.

    Args:
        summary: Short label shown when collapsed.
        content: Full content shown when expanded.
        lang: Language hint for code blocks inside content.

    Returns:
        HTML/collapsible markdown string.
    """
    code_block = f"```{lang}\n{content}\n```"
    return f"<details>\n<summary>{summary}</summary>\n\n{code_block}\n</details>"


def convert_debug_refine_to_markdown(data: dict) -> str:
    """Convert debug refinement JSON to markdown format.

    Args:
        data: Parsed debug_refine.json contents.

    Returns:
        Formatted markdown string.
    """
    lines: list[str] = []

    lines.append("# Refinement Stage Debug Log\n")

    # Result summary
    result_section = data.get("result", {})
    lines.append("## Result Summary\n")
    lines.append(f"- **Success**: {result_section.get('success', 'N/A')}")
    lines.append(f"- **Total Refined**: {result_section.get('total_refined', 'N/A')}")
    lines.append(f"- **Total Fallback**: {result_section.get('total_fallback', 'N/A')}")
    lines.append("")

    # Refinement statistics
    refine_stats = data.get("refinement_stats", {})
    lines.append("## Refinement Statistics\n")
    lines.append(f"- **Total Responses**: {refine_stats.get('total_responses', 'N/A')}")
    lines.append(f"- **Valid Responses**: {refine_stats.get('valid_responses', 'N/A')}")
    lines.append(f"- **Invalid Responses**: {refine_stats.get('invalid_responses', 'N/A')}")
    lines.append("")

    # Per-advert voting details
    advert_details = refine_stats.get("advert_voting_details", [])
    if advert_details:
        lines.append("## Advert Voting Details\n")

        for detail in advert_details:
            pos = detail.get("advert_position", "?")
            advert_id = detail.get("advert_id", "Unknown")
            brand = detail.get("brand", "Unknown")
            value_type = detail.get("value_type", "frame")
            voted_value = detail.get("voted_value", "N/A")

            # Build the advert body content (everything inside the collapsible)
            advert_body: list[str] = []
            advert_body.append(f"**Voted {value_type}**: `{voted_value}`\n")

            # Individual responses
            response_values = detail.get("response_values", [])
            if response_values:
                advert_body.append(f"**Individual responses** (ensemble of {len(response_values)}):\n")
                for rv in response_values:
                    resp_num = rv.get("response_num", "?")
                    value = rv.get("value", "N/A")
                    error = rv.get("error")
                    if error:
                        advert_body.append(f"  - Response {resp_num}: {value_type}={value} (error: {error})")
                    else:
                        advert_body.append(f"  - Response {resp_num}: {value_type}={value}")
                advert_body.append("")

            # Per-advert prompt
            prompt_raw = detail.get("prompt", "")
            if prompt_raw:
                prompt_text = decode_escapes(prompt_raw)
                summary = f"Click to expand prompt for advert {pos}"
                advert_body.append(f"**Prompt**:\n")
                advert_body.append(wrap_collapsible(summary, prompt_text))
                advert_body.append("")

            # Raw model responses
            if response_values:
                advert_body.append("**Raw Model Responses**:\n")
                for rv in response_values:
                    resp_num = rv.get("response_num", "?")
                    raw = rv.get("raw_response", "")
                    if raw:
                        raw_decoded = decode_escapes(raw)
                        preview = raw_decoded[:80].replace("\n", "\\n")
                        summary_text = f"Response {resp_num} ({len(raw_decoded)} chars) - {preview}..."
                        advert_body.append(wrap_collapsible(summary_text, raw_decoded))

            # Wrap entire advert in its own collapsible
            advert_summary = (
                f"Advert {pos}: {brand} ({advert_id}) "
                f"| voted {value_type}={voted_value} "
                f"| {len(response_values)} response(s)"
            )
            lines.append(wrap_collapsible(advert_summary, "\n".join(advert_body)))
            lines.append("")

    return "\n".join(lines)

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert debug_refine.json to human-readable markdown"
    )
    parser.add_argument(
        "-i", "--input",
        type=Path,
        default=Path("debug_refine.json"),
        help="Input JSON file (default: debug_refine.json)"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output markdown file (default: stdout)"
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.is_file():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading {input_path}: {e}", file=sys.stderr)
        return 1

    markdown = convert_debug_refine_to_markdown(data)

    if args.output:
        out_path = args.output.resolve()
        out_path.write_text(markdown, encoding="utf-8")
        print(f"Written {len(markdown)} bytes to {out_path}")
    else:
        print(markdown, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
