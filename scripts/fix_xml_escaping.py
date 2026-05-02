#!/usr/bin/env python3
"""Escape illegal unescaped XML characters in .xml files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple


ILLEGAL_AMP_PATTERN = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)")


def escape_xml_ampersands(content: str) -> Tuple[str, List[Tuple[int, str, str]]]:
    """Escape unescaped & characters in XML content.

    Args:
        content: Original XML content.

    Returns:
        Tuple of (fixed content, list of changes as (line_no, original, fixed)).
    """
    changes: List[Tuple[int, str, str]] = []
    lines = content.split('\n')
    fixed_lines = []

    for line_no, line in enumerate(lines, 1):
        fixed_line = ILLEGAL_AMP_PATTERN.sub('&amp;', line)
        fixed_lines.append(fixed_line)
        if fixed_line != line:
            orig_match = ILLEGAL_AMP_PATTERN.search(line)
            while orig_match:
                changes.append((line_no, orig_match.group(), '&amp;'))
                orig_match = ILLEGAL_AMP_PATTERN.search(line, orig_match.end())

    return '\n'.join(fixed_lines), changes


def process_file(filepath: Path, dry_run: bool = False) -> bool:
    """Process a single XML file, escaping illegal characters.

    Args:
        filepath: Path to the XML file.
        dry_run: If True, only report changes without modifying.

    Returns:
        True if changes were made (or would be made in dry-run), False otherwise.
    """
    content = filepath.read_text(encoding='utf-8')
    fixed_content, changes = escape_xml_ampersands(content)

    if not changes:
        return False

    if dry_run:
        print(f"Would fix: {filepath}")
        for line_no, old, new in changes:
            print(f"  Line {line_no}: {old!r} -> {new!r}")
        return True

    temp_path = filepath.with_suffix(filepath.suffix + '.tmp')
    temp_path.write_text(fixed_content, encoding='utf-8')
    filepath.unlink()
    temp_path.rename(filepath)
    print(f"Fixed: {filepath} ({len(changes)} change(s))")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Escape illegal unescaped XML characters in .xml files."
    )
    parser.add_argument(
        '-f', '--folder',
        type=Path,
        default=Path('./video'),
        help="Folder to search for XML files (default: ./video)"
    )
    parser.add_argument(
        '-n', '--dry-run',
        action='store_true',
        help="Preview changes without modifying files"
    )
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"Error: Folder not found: {folder}", file=sys.stderr)
        return 1

    xml_files = list(folder.rglob('*.xml'))
    if not xml_files:
        print(f"No XML files found in: {folder}")
        return 0

    print(f"Found {len(xml_files)} XML file(s)")
    if args.dry_run:
        print("DRY RUN - no files will be modified\n")

    total_fixed = 0
    for filepath in sorted(xml_files):
        if process_file(filepath, dry_run=args.dry_run):
            total_fixed += 1

    if args.dry_run:
        print(f"\n{total_fixed} file(s) would be fixed")
    else:
        print(f"\n{total_fixed} file(s) fixed")

    return 0


if __name__ == '__main__':
    sys.exit(main())
