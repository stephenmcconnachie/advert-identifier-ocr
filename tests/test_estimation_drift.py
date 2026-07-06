#!/usr/bin/env python3
"""Test: estimation drift limit stops cascade into programme content.

Fail case: 21:00:00 break 4. Only 1 of 8 adverts matched (Marks & Spencer
at frame 471). The remaining adverts were estimated by cascading 20-30s
each, drifting into programme content. The fix should stop estimation
after 180s from the last matched advert.

Run: python3 tests/test_estimation_drift.py
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
from ad_break_identifier.pipeline_state import (
    create_initial_state,
    update_break_adverts,
    read_state,
    write_state,
)

PIPELINE_DIR = Path("video/pipeline_20260705_162406")
VIDEO = "2024-03-26_ITV1HD_21:00:00"
BREAK = 4
MAX_DRIFT = 180.0  # seconds from last matched advert


def main():
    metadata_path = PIPELINE_DIR / f"{VIDEO}_metadata.json"
    ocr_path = PIPELINE_DIR / f"{VIDEO}_break{BREAK}_ocr.json"
    state_path = PIPELINE_DIR / f"{VIDEO}_pipeline_state.json"

    if not metadata_path.exists():
        print(f"SKIP: {metadata_path} not found")
        return 0

    meta = load_metadata_from_file(str(metadata_path), BREAK)

    # Load OCR if available (for clamp_correct)
    ocr_results = None
    if ocr_path.exists():
        with open(ocr_path) as f:
            ocr_data = json.load(f)
        ocr_results = ocr_data["frames"]

    # Run detection
    results = search_with_ordering(
        ocr_results or [], meta.adverts, fps=5.0
    )
    anomalies = clamp_check(results)
    if any(anomalies):
        results = clamp_correct(results, meta.adverts, 5.0)

    # Build updates for pipeline state
    updates = []
    for i, r in enumerate(results):
        secs_clip = None
        if r.refined_end_seconds is not None:
            secs_clip = r.refined_end_seconds
        elif r.last_match_seconds is not None:
            secs_clip = r.last_match_seconds

        tc = ""
        if secs_clip is not None:
            from ad_break_identifier.detect import seconds_to_timecode
            tc = seconds_to_timecode(secs_clip)

        updates.append({
            "status": "detected",
            "detection": {
                "last_timecode": tc,
                "last_seconds_clip": secs_clip,
                "last_frame": r.last_match_frame,
                "match_tier": r.match_tier,
                "matched_terms": r.matched_terms,
            },
        })

    # Create fresh state and run update_break_adverts
    state = create_initial_state(str(metadata_path), before_secs=10.0)
    update_break_adverts(state, BREAK, updates, fps=5.0)

    # Check results
    break_data = state["ad_breaks"][BREAK - 1]
    clip_offset = break_data["clip_offset"]

    print(f"Break {BREAK}: clip_offset={clip_offset}")
    print(f"{'#':>2} {'Brand':40s} {'tier':>12} {'last_sec':>10} {'start_sec':>10} {'adj_bc':>10}")
    print("-" * 90)

    passed = True
    for i, adv in enumerate(break_data["adverts"]):
        d = adv.get("detection", {})
        tier = d.get("match_tier", "")
        lsc = d.get("last_seconds_clip")
        ssc = d.get("start_seconds_clip")
        asb = d.get("adjusted_start_broadcast")

        print(f"{i+1:>2} {adv['brand']:40s} {tier:>12} {str(lsc):>10} {str(ssc):>10} {str(asb):>10}")

        # Check: estimated adverts beyond 180s drift should NOT have
        # adjusted_start_broadcast set
        if tier == "estimated" and lsc is not None and ssc is not None:
            # Find the last matched advert's position
            last_matched_sec = None
            for j in range(i - 1, -1, -1):
                jd = break_data["adverts"][j].get("detection", {})
                if jd.get("match_tier", "") not in ("", "estimated", "fallback"):
                    last_matched_sec = jd.get("last_seconds_clip")
                    break

            if last_matched_sec is not None:
                drift = lsc - last_matched_sec
                if drift > MAX_DRIFT and asb is not None:
                    print(f"  FAIL: {adv['brand']} estimated {drift:.0f}s from "
                          f"last matched, but still has adjusted_start_broadcast")
                    passed = False
                elif drift > MAX_DRIFT and asb is None:
                    print(f"  OK: {adv['brand']} skipped (drift {drift:.0f}s > {MAX_DRIFT}s)")

    if passed:
        print("\nPASS: estimation drift limit working correctly")
        return 0
    else:
        print("\nFAIL: estimation drift limit not working")
        return 1


if __name__ == "__main__":
    sys.exit(main())
