#!/usr/bin/env python3
"""Rename old advert clips to include category from JSON metadata.

This script processes MP4 files in video/adverts/ that don't have '---' in the filename
(to distinguish old format from new format). It looks up the category from the JSON
metadata files and creates a new filename with the category included.

Usage:
    python scripts/rename_advert_clips.py           # Dry-run (show what would happen)
    python scripts/rename_advert_clips.py --execute  # Actually rename files
"""

import argparse
import json
import re
from pathlib import Path


def sanitize(text: str) -> str:
    """Remove unsafe characters, replace spaces and ampersands with hyphens.

    Args:
        text: String to sanitize.

    Returns:
        Sanitized string safe for use in filenames.
    """
    text = text.replace(" ", "-")
    text = text.replace("&", "and")
    text = re.sub(r'[\\/:*?"<>|]', '', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def lookup_category(unique_id: str, json_dir: Path) -> str:
    """Search JSON metadata files for unique_id and return sanitized category.

    Args:
        unique_id: The advert unique identifier.
        json_dir: Directory containing JSON metadata files.

    Returns:
        Sanitized category string, or 'unknown' if not found.
    """
    for json_file in sorted(json_dir.glob("*_metadata.json")):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)

            for ad_break in data.get('ad_breaks', []):
                for advert in ad_break.get('adverts', []):
                    if advert.get('unique_id') == unique_id:
                        category = advert.get('category', 'unknown')
                        if category:
                            return sanitize(category)
                        return 'unknown'
        except (json.JSONDecodeError, IOError) as e:
            print(f"  Warning: Could not read {json_file}: {e}")
            continue

    return 'unknown'


def extract_parts(filename: str) -> tuple[str, str]:
    """Extract unique_id and brand from old-format filename.

    Args:
        filename: MP4 filename without path (e.g., 'ACBLIUK090020_Lidl.mp4').

    Returns:
        Tuple of (unique_id, brand).
    """
    stem = Path(filename).stem
    parts = stem.split('_', 1)
    unique_id = parts[0]
    brand = parts[1] if len(parts) > 1 else ''
    return unique_id, brand


def main():
    parser = argparse.ArgumentParser(
        description="Rename old advert clips to include category from JSON metadata"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually rename files (default is dry-run)"
    )
    parser.add_argument(
        "--advert-dir",
        type=str,
        default="video/adverts",
        help="Directory containing advert MP4 files (default: video/adverts)"
    )
    parser.add_argument(
        "--json-dir",
        type=str,
        default="video",
        help="Directory containing JSON metadata files (default: video)"
    )
    args = parser.parse_args()

    adverts_dir = Path(args.advert_dir)
    json_dir = Path(args.json_dir)

    if not adverts_dir.exists():
        print(f"Error: Advert directory not found: {adverts_dir}")
        return 1

    if not json_dir.exists():
        print(f"Error: JSON directory not found: {json_dir}")
        return 1

    mp4_files = list(adverts_dir.glob("*.mp4"))
    old_format_files = [f for f in mp4_files if "---" not in f.name]

    print(f"Found {len(mp4_files)} MP4 files total")
    print(f"Found {len(old_format_files)} files to process (without '---' in filename)")
    print()

    if not old_format_files:
        print("No files to process.")
        return 0

    to_rename = []

    for mp4 in sorted(old_format_files):
        unique_id, brand = extract_parts(mp4.name)
        category = lookup_category(unique_id, json_dir)

        if category == 'unknown':
            print(f"  WARNING: Could not find category for {unique_id} in any JSON file")

        old_name = mp4.name
        sanitized_brand = sanitize(brand)
        new_stem = f"{unique_id}_{category}_{sanitized_brand}"
        new_name = f"{new_stem}.mp4"

        to_rename.append((mp4, old_name, new_name))
        print(f"  {old_name}")
        print(f"    -> {new_name}")

    print()
    print(f"{'Would rename' if not args.execute else 'Renaming'} {len(to_rename)} file(s)")

    if args.execute:
        success_count = 0
        error_count = 0
        for mp4, old_name, new_name in to_rename:
            try:
                new_path = adverts_dir / new_name
                if new_path.exists():
                    print(f"  WARNING: Target already exists: {new_name}")
                    error_count += 1
                    continue
                mp4.rename(new_path)
                success_count += 1
            except OSError as e:
                print(f"  ERROR: Failed to rename {old_name}: {e}")
                error_count += 1

        print(f"\nRenamed {success_count} file(s)" + (f", {error_count} error(s)" if error_count else ""))
    else:
        print()
        print("Run with --execute to perform the rename operation")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())