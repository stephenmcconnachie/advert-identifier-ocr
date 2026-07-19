#!/usr/bin/env python3
"""Fetch video filepaths for minimum cover results via two-phase CID API lookup.

Phase 1 — Manifestations: query by channel + date, match ad break start_time
against programme transmission_start_time to find the containing programme's
object_number.

Phase 2 — Media: use the object_number to fetch access_rendition.mp4 and
input.date, then construct the final filepath.

Output: enriched copies of the input CSV and JSON with ``object_number`` and
``filepath`` fields appended (``_filepath`` suffix in filename).

Usage:
    python3 scripts/fetch_filepaths.py \\
        --input-dir advert_min_cover_output \\
        --api-base "http://[CID_API_HOST]/CIDDataSandbox/wwwopac.ashx" \\
        [--rate-limit 1.0] [--resume]
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("fetch_filepaths")

# ── Error codes ──────────────────────────────────────────────────────────────

NO_CID_RESPONSE = "NO_CID_RESPONSE"
NO_CID_PROG_MATCH = "NO_CID_PROG_MATCH"
NO_CID_MEDIA_RECORD = "NO_CID_MEDIA_RECORD"
NO_CID_ACCESS_RENDITION = "NO_CID_ACCESS_RENDITION"
NO_CID_INPUT_DATE = "NO_CID_INPUT_DATE"

# ── Channel lookup ──────────────────────────────────────────────────────────
# Maps CSV channel names to their form in the CID database so the wildcard
# search broadcast_channel='*{db_term}*' reliably matches.

_CHANNEL_LOOKUP: dict[str, str] = {
    "5": "Channel 5 HD",
    "5STAR": "5STAR",
    "CH4": "Channel 4 HD",
    "Channel 5": "Channel 5 HD",
    "E4": "E4",
    "Film4": "Film4",
    "ITV1": "ITV HD",
    "ITV1 HD": "ITV HD",
    "ITV2": "ITV2",
    "ITV3": "ITV3",
    "ITV4": "ITV4",
    "ITVBe": "ITV Be",
    "ITVQuiz": "ITV Quiz",
    "More4": "More4",
}

# ── Helpers ──────────────────────────────────────────────────────────────────


def date_csv_to_iso(d: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD."""
    parts = d.strip().split("/")
    return f"{parts[2]}-{parts[1]}-{parts[0]}"


def time_to_seconds(t: str) -> int:
    """Convert HH:MM:SS to seconds since midnight."""
    parts = t.strip().split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def build_manifestations_url(api_base: str, channel: str, date_iso: str) -> str:
    """Build the Phase 1 URL to query manifestations by channel + date."""
    db_channel = _CHANNEL_LOOKUP.get(channel, channel)
    search = (
        f"broadcast_channel='*{db_channel}*' "
        f"AND transmission_date='{date_iso}'"
    )
    params = (
        f"database=manifestations"
        f"&search={requests.utils.quote(search)}"
        f"&fields=transmission_date,transmission_start_time,title,parts_reference"
        f"&limit=0&output=json"
    )
    return f"{api_base}?{params}"


def build_media_url(api_base: str, object_number: str) -> str:
    """Build the Phase 2 URL to query media by object_number."""
    search = f"object.object_number='{object_number}'"
    params = (
        f"database=media"
        f"&search={requests.utils.quote(search)}"
        f"&fields=access_rendition.mp4,input.date"
        f"&limit=0&output=json"
    )
    return f"{api_base}?{params}"


def parse_manifestations(response_data: dict) -> list[dict]:
    """Extract records from a manifestations API response.

    Returns records sorted by transmission_start_time ascending.
    """
    try:
        records = response_data["adlibJSON"]["recordList"]["record"]
    except (KeyError, TypeError):
        return []
    records.sort(key=lambda r: r.get("transmission_start_time", [""])[0])
    return records


def get_object_number(record: dict) -> str | None:
    """Extract object_number from a manifestations record's Parts."""
    try:
        return record["Parts"][0]["parts_reference"][0]["object_number"][0]
    except (KeyError, IndexError, TypeError):
        return None


def parse_media(response_data: dict) -> dict | None:
    """Extract the media record from a media API response."""
    try:
        records = response_data["adlibJSON"]["recordList"]["record"]
        if records:
            return records[0]
    except (KeyError, TypeError):
        pass
    return None


def build_filepath(media_record: dict) -> str:
    """Construct the filepath from a media record.

    Format: [CID_FILEPATH_PREFIX]/{yyyymm}/{rendition}.mp4
    """
    try:
        rendition = media_record["access_rendition.mp4"][0]
    except (KeyError, IndexError, TypeError):
        return NO_CID_ACCESS_RENDITION

    try:
        date_str = media_record["input.date"][0]
    except (KeyError, IndexError, TypeError):
        return NO_CID_INPUT_DATE

    folder = date_str.replace("-", "")[:6]  # yyyymm
    return f"[CID_FILEPATH_PREFIX]/{folder}/{rendition}.mp4"


def find_matching_record(
    csv_seconds: int, records: list[dict]
) -> dict | None:
    """Find the programme record that was airing at *csv_seconds*.

    The ad break's start time falls within a programme's slot:
        programme.start_time <= ad_break_time < next_programme.start_time

    Returns the latest record whose transmission_start_time is <= csv_seconds.
    """
    best = None
    for record in records:
        try:
            rec_secs = time_to_seconds(record["transmission_start_time"][0])
        except (KeyError, IndexError, TypeError):
            continue
        if rec_secs <= csv_seconds:
            best = record
    return best


# ── Main ────────────────────────────────────────────────────────────────────


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch video filepaths for minimum cover results"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default="advert_min_cover_output",
        help="Directory containing minimum_cover_result.csv/json (default: "
        "advert_min_cover_output)",
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default="http://[CID_API_HOST]/CIDDataSandbox/wwwopac.ashx",
        help="Base URL for the CID API (default: "
        "http://[CID_API_HOST]/CIDDataSandbox/wwwopac.ashx)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Minimum seconds between API calls (default: 1.0)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Skip rows that already have a filepath in the output files",
    )
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    csv_path = input_dir / "minimum_cover_result.csv"
    json_path = input_dir / "minimum_cover_result.json"
    out_csv = input_dir / "minimum_cover_result_filepath.csv"
    out_json = input_dir / "minimum_cover_result_filepath.json"

    if not csv_path.is_file():
        logger.error("Input CSV not found: %s", csv_path)
        return 1
    if not json_path.is_file():
        logger.error("Input JSON not found: %s", json_path)
        return 1

    api_base = args.api_base.rstrip("/")
    rate_limit = args.rate_limit
    resume = args.resume
    last_api_time = 0.0

    def rate_limit_wait():
        nonlocal last_api_time
        now = time.time()
        elapsed = now - last_api_time
        if elapsed < rate_limit:
            time.sleep(rate_limit - elapsed)
        last_api_time = time.time()

    # ── Load and sort CSV rows ──
    logger.info("Reading CSV: %s", csv_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    logger.info("Loaded %d CSV rows", len(rows))

    # Sort by date descending (newest first)
    rows.sort(key=lambda r: r["date"], reverse=True)

    # Load existing output if resuming
    existing_filepaths: dict[int, str] = {}
    if resume and out_csv.is_file():
        with open(out_csv, newline="", encoding="utf-8") as f:
            for existing in csv.DictReader(f):
                try:
                    idx = int(existing.get("rank", 0))
                    fp = existing.get("filepath", "")
                    if fp:
                        existing_filepaths[idx] = fp
                except (ValueError, KeyError):
                    pass
        logger.info("Resume mode: %d existing filepaths loaded", len(existing_filepaths))

    # ── Group rows by (channel, date) for Phase 1 ──
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["channel"], row["date"])
        groups[key].append(row)

    logger.info("Grouped into %d unique (channel, date) pairs", len(groups))

    # ── Caches ──
    # (channel, date) -> list of sorted programme records (or None if failed)
    programme_cache: dict[tuple[str, str], list[dict] | None] = {}
    # object_number -> filepath string
    filepath_cache: dict[str, str] = {}

    # ── Process each group ──
    results: list[dict] = []
    processed = 0
    total = len(rows)
    phase1_errors = 0
    phase2_errors = 0
    success_count = 0
    start_time = time.time()

    for key, group_rows in sorted(groups.items(), key=lambda x: x[0][1], reverse=True):
        channel, date_csv = key
        date_iso = date_csv_to_iso(date_csv)

        # Build Phase 1 URL
        prog_url = build_manifestations_url(api_base, channel, date_iso)
        rate_limit_wait()

        # Check cache first
        programmes: list[dict] | None = programme_cache.get(key)
        if programmes is None and key not in programme_cache:
            # Not cached — fetch
            try:
                resp = requests.get(prog_url, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    programmes = parse_manifestations(data)
                    logger.info(
                        "  Phase 1 [%s %s] → %d programmes",
                        channel, date_iso,
                        len(programmes) if programmes else 0,
                    )
                else:
                    logger.warning(
                        "  Phase 1 [%s %s] → HTTP %d",
                        channel, date_iso, resp.status_code,
                    )
                    programmes = None
            except requests.RequestException as e:
                logger.warning("  Phase 1 [%s %s] → request failed: %s", channel, date_iso, e)
                programmes = None
            programme_cache[key] = programmes
        elif key in programme_cache:
            programmes = programme_cache[key]

        for row in group_rows:
            rank = int(row["rank"])
            processed += 1

            # Resume: skip if already has a filepath
            if resume and rank in existing_filepaths:
                row["object_number"] = existing_filepaths.get(rank, "")
                row["filepath"] = existing_filepaths[rank]
                results.append(row)
                continue

            # Phase 1 failed (no API response)
            if programmes is None:
                row["object_number"] = NO_CID_RESPONSE
                row["filepath"] = NO_CID_RESPONSE
                results.append(row)
                phase1_errors += 1
                continue

            # Match programme
            csv_seconds = time_to_seconds(row["start_time"])
            matched = find_matching_record(csv_seconds, programmes)

            if matched is None:
                row["object_number"] = NO_CID_PROG_MATCH
                row["filepath"] = NO_CID_PROG_MATCH
                results.append(row)
                phase1_errors += 1
                continue

            object_number = get_object_number(matched)
            if object_number is None:
                row["object_number"] = NO_CID_PROG_MATCH
                row["filepath"] = NO_CID_PROG_MATCH
                results.append(row)
                phase1_errors += 1
                continue

            row["object_number"] = object_number

            # Phase 2 — fetch media by object_number (cached)
            if object_number in filepath_cache:
                row["filepath"] = filepath_cache[object_number]
                results.append(row)
                if "NO_CID" not in filepath_cache[object_number]:
                    success_count += 1
                else:
                    phase2_errors += 1
                continue

            med_url = build_media_url(api_base, object_number)
            rate_limit_wait()

            try:
                resp = requests.get(med_url, timeout=30)
                if resp.status_code != 200:
                    fp = NO_CID_RESPONSE
                else:
                    media_rec = parse_media(resp.json())
                    if media_rec is None:
                        fp = NO_CID_MEDIA_RECORD
                    else:
                        fp = build_filepath(media_rec)
            except requests.RequestException as e:
                logger.warning(
                    "  Phase 2 [%s] → request failed: %s", object_number, e
                )
                fp = NO_CID_RESPONSE

            filepath_cache[object_number] = fp
            row["filepath"] = fp

            if "NO_CID" not in fp:
                success_count += 1
            else:
                phase2_errors += 1

            results.append(row)

        # Progress
        if processed % 100 == 0 or processed == total:
            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (total - processed) / rate if rate > 0 else 0
            logger.info(
                "Progress: %d/%d rows (%.1f%%)  |  %d OK, %d P1 err, %d P2 err  "
                "|  %.1f rows/s  |  ETA: %.0f min",
                processed, total,
                processed / total * 100,
                success_count, phase1_errors, phase2_errors,
                rate, eta / 60,
            )

    # ── Write enriched CSV ──
    if results:
        fieldnames = list(results[0].keys())
        if "object_number" not in fieldnames:
            fieldnames.append("object_number")
        if "filepath" not in fieldnames:
            fieldnames.append("filepath")

        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        logger.info("Enriched CSV written: %s (%d rows)", out_csv, len(results))

    # ── Write enriched JSON ──
    logger.info("Reading JSON for structure: %s", json_path)
    with open(json_path, encoding="utf-8") as f:
        json_data: dict = json.load(f)

    # Build lookup by rank for fast access
    results_by_rank = {int(r["rank"]): r for r in results}

    for brk in json_data.get("selected_breaks", []):
        rank = brk.get("rank", 0)
        enriched = results_by_rank.get(rank, {})
        brk["object_number"] = enriched.get("object_number", NO_CID_PROG_MATCH)
        brk["filepath"] = enriched.get("filepath", NO_CID_PROG_MATCH)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    logger.info("Enriched JSON written: %s", out_json)

    # ── Summary ──
    elapsed = time.time() - start_time
    logger.info(
        "Done: %d rows processed in %.0f min. "
        "%d OK, %d Phase 1 errors, %d Phase 2 errors",
        total,
        elapsed / 60,
        success_count,
        phase1_errors,
        phase2_errors,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
