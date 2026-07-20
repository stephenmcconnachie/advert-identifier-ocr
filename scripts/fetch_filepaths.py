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


def build_filepath(media_record: dict, filepath_prefix: str) -> str:
    """Construct the filepath from a media record.

    Format: {prefix}/{yyyymm}/{rendition}
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
    return f"{filepath_prefix.rstrip('/')}/{folder}/{rendition}"


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
        default=None,
        help="Base URL for the CID API. If omitted, falls back to "
        "CID_API_BASE env var (required).",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Minimum seconds between API calls (default: 1.0)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file. All output is written here in addition "
        "to stdout (default: input-dir/fetch_filepaths_TIMESTAMP.log)",
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

    # Configure log file
    log_file = args.log_file
    if not log_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = str(input_dir / f"fetch_filepaths_{timestamp}.log")
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Add file handler to the root logger
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    logging.getLogger().addHandler(file_handler)
    logger.info("Logging to: %s", log_path)

    if not csv_path.is_file():
        logger.error("Input CSV not found: %s", csv_path)
        return 1
    if not json_path.is_file():
        logger.error("Input JSON not found: %s", json_path)
        return 1

    api_base = args.api_base or os.environ.get("CID_API_BASE")
    if not api_base:
        logger.error(
            "CID API base URL not set. Pass --api-base or set CID_API_BASE env var."
        )
        return 1
    api_base = api_base.rstrip("/")

    filepath_prefix = os.environ.get("CID_FILEPATH_PREFIX")
    if not filepath_prefix:
        logger.error(
            "CID filepath prefix not set. Set CID_FILEPATH_PREFIX env var "
            "(e.g. /mnt/bp_nas/access_renditions/bfi)."
        )
        return 1

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

    # ── Open output CSV for incremental writing ──
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    csv_fieldnames = list(rows[0].keys()) if rows else []
    for col in ("object_number", "filepath"):
        if col not in csv_fieldnames:
            csv_fieldnames.append(col)
    csv_fh = open(out_csv, "w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_fh, fieldnames=csv_fieldnames)
    csv_writer.writeheader()
    csv_fh.flush()
    logger.info("Opened CSV for incremental writing: %s", out_csv)

    # Load existing `rank_to_result` for resume
    rank_to_result: dict[int, dict[str, str]] = {}
    if resume and out_csv.stat().st_size > csv_fh.tell():
        with open(out_csv, newline="", encoding="utf-8") as f:
            for existing in csv.DictReader(f):
                try:
                    r = int(existing.get("rank", 0))
                    fp = existing.get("filepath", "")
                    if fp:
                        rank_to_result[r] = {
                            "object_number": existing.get("object_number", ""),
                            "filepath": fp,
                        }
                except (ValueError, KeyError):
                    pass
        logger.info("Resume mode: %d existing filepaths loaded", len(rank_to_result))

    # ── Group rows by (channel, date) for Phase 1 ──
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["channel"], row["date"])
        groups[key].append(row)

    logger.info("Grouped into %d unique (channel, date) pairs", len(groups))

    # ── Caches ──
    programme_cache: dict[tuple[str, str], list[dict] | None] = {}
    filepath_cache: dict[str, str] = {}

    # ── Process each group ──
    processed = 0
    total = len(rows)
    phase1_errors = 0
    phase2_errors = 0
    success_count = 0
    start_time = time.time()

    for key, group_rows in sorted(groups.items(), key=lambda x: x[0][1], reverse=True):
        channel, date_csv = key
        date_iso = date_csv_to_iso(date_csv)
        prog_url = build_manifestations_url(api_base, channel, date_iso)

        programmes = programme_cache.get(key)
        if programmes is None and key not in programme_cache:
            rate_limit_wait()
            try:
                resp = requests.get(prog_url, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    programmes = parse_manifestations(data)
                    if not programmes:
                        logger.info(
                            "  Phase 1 [%s %s] → 0 records", channel, date_iso
                        )
                else:
                    logger.warning(
                        "  Phase 1 [%s %s] → HTTP %d\n    %s",
                        channel, date_iso, resp.status_code, prog_url,
                    )
                    programmes = None
            except requests.RequestException as e:
                logger.warning(
                    "  Phase 1 [%s %s] → request failed: %s\n    %s",
                    channel, date_iso, e, prog_url,
                )
                programmes = None
            programme_cache[key] = programmes
        elif key in programme_cache:
            programmes = programme_cache[key]

        for row in group_rows:
            rank = int(row["rank"])
            processed += 1

            if resume and rank in rank_to_result:
                continue

            obj_nr = ""
            fp = ""

            if programmes is None:
                obj_nr = NO_CID_RESPONSE
                fp = NO_CID_RESPONSE
                phase1_errors += 1
            else:
                csv_seconds = time_to_seconds(row["start_time"])
                matched = find_matching_record(csv_seconds, programmes)
                if matched is None:
                    obj_nr = NO_CID_PROG_MATCH
                    fp = NO_CID_PROG_MATCH
                    phase1_errors += 1
                else:
                    object_number = get_object_number(matched)
                    if object_number is None:
                        obj_nr = NO_CID_PROG_MATCH
                        fp = NO_CID_PROG_MATCH
                        phase1_errors += 1
                    else:
                        obj_nr = object_number
                        if object_number in filepath_cache:
                            fp = filepath_cache[object_number]
                        else:
                            med_url = build_media_url(api_base, object_number)
                            rate_limit_wait()
                            try:
                                resp = requests.get(med_url, timeout=30)
                                if resp.status_code != 200:
                                    logger.warning(
                                        "  Phase 2 [%s] → HTTP %d\n    %s",
                                        object_number, resp.status_code, med_url,
                                    )
                                    fp = NO_CID_RESPONSE
                                else:
                                    media_rec = parse_media(resp.json())
                                    if media_rec is None:
                                        logger.warning(
                                            "  Phase 2 [%s] → no media record\n    %s",
                                            object_number, med_url,
                                        )
                                        fp = NO_CID_MEDIA_RECORD
                                    else:
                                        fp = build_filepath(media_rec, filepath_prefix)
                                        if "NO_CID" in fp:
                                            logger.warning(
                                                "  Phase 2 [%s] → %s\n    %s",
                                                object_number, fp, med_url,
                                            )
                            except requests.RequestException as e:
                                logger.warning(
                                    "  Phase 2 [%s] → request failed: %s\n    %s",
                                    object_number, e, med_url,
                                )
                                fp = NO_CID_RESPONSE
                            filepath_cache[object_number] = fp

            # Write row incrementally
            row["object_number"] = obj_nr
            row["filepath"] = fp
            csv_writer.writerow(row)
            csv_fh.flush()
            rank_to_result[rank] = {"object_number": obj_nr, "filepath": fp}

            if "NO_CID" not in fp:
                success_count += 1
            elif fp in (NO_CID_RESPONSE, NO_CID_PROG_MATCH):
                phase1_errors += 1
            else:
                phase2_errors += 1

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

    csv_fh.close()
    logger.info("Enriched CSV written: %s (%d rows)", out_csv, processed)

    # ── Write enriched JSON ──
    logger.info("Reading JSON for structure: %s", json_path)
    with open(json_path, encoding="utf-8") as f:
        json_data: dict = json.load(f)

    for brk in json_data.get("selected_breaks", []):
        r = brk.get("rank", 0)
        enriched = rank_to_result.get(r, {})
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
