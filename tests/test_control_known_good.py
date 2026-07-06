#!/usr/bin/env python3
"""Test: control case — known-good detection still works correctly.

Control case: 09:00:00 break 2. All 8 adverts matched, clamp corrections
applied successfully. The safe changes (after_secs reduction + estimation
drift limit) should have ZERO impact on this detection.

Run: python3 tests/test_control_known_good.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ad_break_identifier.detect import (
    search_with_ordering,
    clamp_check,
    clamp_correct,
)
from ad_break_identifier.config import load_metadata_from_file

PIPELINE_DIR = Path("video/pipeline_20260705_162406")
VIDEO = "2024-03-26_ITV1HD_09:00:00"
BREAK = 2

# Expected frames AFTER clamp correction (from the working pipeline run)
EXPECTED_FRAMES = {
    "Mars maltesers": 616,
    "Heinz baked beans": 716,
    "Butlins holiday worlds drtv": 766,
    "Fast cash property": 916,
    "Skechers sport shoes": 1016,
    "Voltarol pain relief": 1116,
    "Media 10 ideal home show": 1266,
    "Age uk weekly lottery": 1516,
}


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

    print(f"Control: {VIDEO} break {BREAK} ({len(meta.adverts)} adverts)")
    print(f"OCR frames: {len(ocr_results)}")
    print()

    # Run detection pipeline (no ocr_results passed to clamp — original behaviour)
    results = search_with_ordering(ocr_results, meta.adverts, fps=5.0)

    print("=== Pre-clamp ===")
    for i, (adv, r) in enumerate(zip(meta.adverts, results)):
        if r.matched:
            print(f"  {i+1}. {adv.brand:35s} frame={r.last_match_frame:>4} tier={r.match_tier}")
        else:
            print(f"  {i+1}. {adv.brand:35s} NO MATCH")

    anomalies = clamp_check(results)
    print(f"\nClamp anomalies: {sum(anomalies)}")

    results = clamp_correct(results, meta.adverts, 5.0)

    print("\n=== Post-clamp ===")
    passed = True
    for i, (adv, r) in enumerate(zip(meta.adverts, results)):
        if r.matched:
            print(f"  {i+1}. {adv.brand:35s} frame={r.last_match_frame:>4} tier={r.match_tier}")

            for brand_key, expected_frame in EXPECTED_FRAMES.items():
                if brand_key in adv.brand:
                    if r.last_match_frame != expected_frame:
                        print(f"     FAIL: expected frame {expected_frame}, "
                              f"got {r.last_match_frame}")
                        passed = False
                    else:
                        print(f"     OK: matches expected frame {expected_frame}")
        else:
            print(f"  {i+1}. {adv.brand:35s} NO MATCH")
            passed = False

    print()
    if passed:
        print("PASS: control case — all expected matches and corrections intact")
        return 0
    else:
        print("FAIL: control case — some expected results changed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
