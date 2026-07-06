#!/usr/bin/env python3
"""Test: clamp brand-text verification rejects corrections to wrong content.

Fail case: Lloyds bank (18:30:00 break 1). The clamp previously moved the
match from frame 1133 (OCR: "LLOYDS BANK") to frame 1143 (OCR: "Itv NEWS").
The fix should reject this correction and keep the original at 1133.

Run: python3 tests/test_clamp_verification.py
"""
import json
import sys
from pathlib import Path

# Allow import of package modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ad_break_identifier.detect import (
    search_with_ordering,
    clamp_check,
    clamp_correct,
)
from ad_break_identifier.config import load_metadata_from_file

PIPELINE_DIR = Path("video/pipeline_20260705_162406")
VIDEO = "2024-03-26_ITV1HD_18:30:00"
BREAK = 1
BRAND_TO_CHECK = "Lloyds bank"
ORIGINAL_FRAME = 1133  # where "LLOYDS BANK" actually appears
WRONG_FRAME = 1143     # where clamp moved it (OCR: "Itv NEWS")


def main():
    metadata_path = PIPELINE_DIR / f"{VIDEO}_metadata.json"
    ocr_path = PIPELINE_DIR / f"{VIDEO}_break{BREAK}_ocr.json"

    if not metadata_path.exists():
        print(f"SKIP: {metadata_path} not found")
        return 0
    if not ocr_path.exists():
        print(f"SKIP: {ocr_path} not found")
        return 0

    meta = load_metadata_from_file(str(metadata_path), BREAK)
    with open(ocr_path) as f:
        ocr_data = json.load(f)
    ocr_results = ocr_data["frames"]

    # Run detection pipeline (search + clamp)
    results = search_with_ordering(ocr_results, meta.adverts, fps=5.0)

    # Find Lloyds bank in results
    lloyds_idx = None
    for i, adv in enumerate(meta.adverts):
        if "Lloyds" in adv.brand:
            lloyds_idx = i
            break

    if lloyds_idx is None:
        print(f"FAIL: {BRAND_TO_CHECK} not found in metadata")
        return 1

    pre_clamp_frame = results[lloyds_idx].last_match_frame
    print(f"Pre-clamp: {BRAND_TO_CHECK} at frame {pre_clamp_frame}")

    # Run clamp (original 3-arg call — no ocr_results)
    anomalies = clamp_check(results)
    results = clamp_correct(results, meta.adverts, 5.0)

    post_clamp_frame = results[lloyds_idx].last_match_frame
    print(f"Post-clamp: {BRAND_TO_CHECK} at frame {post_clamp_frame}")

    # Verify: clamp should NOT have moved the match to 1143
    if post_clamp_frame == WRONG_FRAME:
        ocr_text = ocr_results[WRONG_FRAME].get("text", "")
        print(f"FAIL: clamp moved to frame {WRONG_FRAME} (OCR: {ocr_text!r})")
        print("  The brand-text verification did not reject this correction.")
        return 1

    # Verify: the match should still be at or near the original position
    if abs(post_clamp_frame - ORIGINAL_FRAME) <= 2:
        print(f"PASS: {BRAND_TO_CHECK} kept at frame {post_clamp_frame} "
              f"(near original {ORIGINAL_FRAME})")
        return 0
    else:
        print(f"UNEXPECTED: {BRAND_TO_CHECK} at frame {post_clamp_frame} "
              f"(expected near {ORIGINAL_FRAME})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
