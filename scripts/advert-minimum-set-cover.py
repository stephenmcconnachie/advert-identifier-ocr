#!/usr/bin/env python3
"""Minimum Set Cover for TV Advert Ad Breaks.

Finds the smallest set of ad breaks that contains every unique advert
(Film Code) across all BFI CSV export files.

This is a classic Minimum Set Cover problem (NP-hard). This implementation
uses the greedy approximation algorithm with a max-heap and lazy-update
strategy, which guarantees coverage within O(log n) of optimal.

Usage:
    python scripts/advert-minimum-set-cover.py --csv-folder /path/to/csvs

HD channels are defined in scripts/hd_channels.txt. The algorithm
runs in two phases: HD channels first, then SD fallback for any
adverts not found on HD.

Output:
    - advert_min_cover_output/minimum_cover_result.json
    - advert_min_cover_output/minimum_cover_result.csv
"""

import argparse
import csv
import json
import sqlite3
import sys
import time
from array import array
from heapq import heappush, heappop
from pathlib import Path


DB_BATCH_SIZE = 500


def log(msg: str) -> None:
    """Print timestamped message to stderr."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def load_hd_channels(path: str) -> set[str]:
    """Load HD channel names from a text file (case-insensitive).

    Blank lines and lines starting with '#' are ignored.

    Returns:
        Set of lowercased channel names.
    """
    p = Path(path)
    if not p.exists():
        print(f"Error: HD channels file not found: {path}", file=sys.stderr)
        sys.exit(1)

    channels = set()
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            channels.add(line.lower())
    log(f"Loaded {len(channels)} HD channels from {path}")
    return channels


def create_db(conn: sqlite3.Connection) -> None:
    """Create database schema for caching parsed CSV data."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS breaks (
            break_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            channel TEXT NOT NULL,
            start_time TEXT NOT NULL,
            programme_before TEXT NOT NULL,
            programme_after TEXT NOT NULL,
            break_code TEXT NOT NULL,
            num_adverts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS break_films (
            break_id TEXT NOT NULL,
            film_code TEXT NOT NULL,
            PRIMARY KEY (break_id, film_code)
        );
        CREATE INDEX IF NOT EXISTS idx_break_films_film ON break_films(film_code);
    """)
    conn.commit()


def _flush_batch(cursor, break_rows, film_rows):
    cursor.executemany(
        """INSERT OR IGNORE INTO breaks
           (break_id, date, channel, start_time, programme_before,
            programme_after, break_code, num_adverts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        break_rows,
    )
    cursor.executemany(
        "INSERT OR IGNORE INTO break_films (break_id, film_code) VALUES (?, ?)",
        film_rows,
    )


def parse_csvs_to_db(csv_folder: Path, conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Parse all CSV files in folder and write breaks + films to SQLite.

    Ad breaks are detected using the same logic as the existing metadata
    extractor: grouped by (channel, programme_before, programme_after) and
    delimited by All PIB rel == "Last".

    Returns:
        Tuple of (total_breaks, total_deduplicated_film_refs, csv_file_count).
    """
    cursor = conn.cursor()
    csv_files = sorted(csv_folder.glob("*.csv"))
    total_breaks = 0
    total_film_refs = 0
    break_buffer = []
    film_buffer = []

    for file_idx, csv_file in enumerate(csv_files):
        filename = csv_file.name
        try:
            with open(csv_file, "r", newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        except UnicodeDecodeError:
            try:
                with open(csv_file, "r", newline="", encoding="latin-1") as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
                log(f"  NOTE: {filename} uses latin-1 encoding")
            except Exception as e:
                log(f"  WARNING: Skipping {filename}: {e}")
                continue
        except Exception as e:
            log(f"  WARNING: Skipping {filename}: {e}")
            continue

        if not rows:
            continue

        rows.sort(key=lambda r: (r.get("Channel", ""), r.get("Start time", "")))

        current_key = None
        break_films = set()
        break_start_time = ""
        break_channel = ""
        break_prog_before = ""
        break_prog_after = ""
        break_code = ""
        break_date = ""
        break_index = 0
        last_was_last = False

        for row in rows:
            channel = row.get("Channel", "").strip()
            prog_before = row.get("BARB Prog Before", "").strip()
            prog_after = row.get("BARB Prog After", "").strip()
            pib_rel = row.get("All PIB rel", "").strip()
            is_last = pib_rel == "Last"
            film = row.get("Film Code", "").strip()
            start = row.get("Start time", "").strip()
            code = row.get("Break Code", "").strip()
            date = row.get("Date", "").strip()

            if not film or not channel:
                continue

            key = (channel, prog_before, prog_after)

            if current_key is None or current_key != key or last_was_last:
                if current_key is not None and break_films:
                    bid = f"{filename}|{break_channel}|{break_index}"
                    break_buffer.append(
                        (
                            bid,
                            break_date,
                            break_channel,
                            break_start_time,
                            break_prog_before,
                            break_prog_after,
                            break_code,
                            len(break_films),
                        )
                    )
                    for f_code in break_films:
                        film_buffer.append((bid, f_code))
                    total_film_refs += len(break_films)
                    total_breaks += 1

                break_index += 1
                break_films = set()
                break_start_time = start
                break_channel = channel
                break_prog_before = prog_before
                break_prog_after = prog_after
                break_code = code
                break_date = date
                current_key = key
                last_was_last = False

            break_films.add(film)
            last_was_last = is_last

        if break_films:
            bid = f"{filename}|{break_channel}|{break_index}"
            break_buffer.append(
                (
                    bid,
                    break_date,
                    break_channel,
                    break_start_time,
                    break_prog_before,
                    break_prog_after,
                    break_code,
                    len(break_films),
                )
            )
            for f_code in break_films:
                film_buffer.append((bid, f_code))
            total_film_refs += len(break_films)
            total_breaks += 1

        if len(break_buffer) >= DB_BATCH_SIZE:
            _flush_batch(cursor, break_buffer, film_buffer)
            conn.commit()
            break_buffer.clear()
            film_buffer.clear()

        if (file_idx + 1) % 100 == 0 or file_idx == len(csv_files) - 1:
            log(
                f"  Parsed {file_idx + 1}/{len(csv_files)} CSVs"
                f" ({total_breaks} breaks, {total_film_refs} film refs)"
            )

    if break_buffer:
        _flush_batch(cursor, break_buffer, film_buffer)
        conn.commit()

    return total_breaks, total_film_refs, len(csv_files)


def load_from_db(
    conn: sqlite3.Connection,
) -> tuple[list, list, list, list[str], int]:
    """Load break/film data from SQLite into compact in-memory structures.

    Film codes and break IDs are remapped to dense integer indices for
    memory efficiency.

    Returns:
        (break_to_films, film_to_breaks, break_info, film_codes, total_films)
        where each is:
        - break_to_films: list[array('I')] indexed by break_int
        - film_to_breaks: list[array('I')] indexed by film_int
        - break_info: list[dict] indexed by break_int
        - film_codes: list[str] indexed by film_int
        - total_films: int
    """
    cursor = conn.cursor()

    log("  Loading film codes...")
    cursor.execute(
        "SELECT DISTINCT film_code FROM break_films ORDER BY film_code"
    )
    film_codes = [row[0] for row in cursor.fetchall()]
    film_to_id = {code: idx for idx, code in enumerate(film_codes)}
    total_films = len(film_codes)
    log(f"  {total_films} unique film codes loaded")

    log("  Loading breaks...")
    cursor.execute(
        """SELECT break_id, date, channel, start_time,
                  programme_before, programme_after, break_code, num_adverts
           FROM breaks ORDER BY break_id"""
    )
    break_info = []
    bid_to_int = {}
    for row in cursor.fetchall():
        bid_str = row[0]
        bid_to_int[bid_str] = len(break_info)
        break_info.append(
            {
                "break_id": bid_str,
                "date": row[1],
                "channel": row[2],
                "start_time": row[3],
                "programme_before": row[4],
                "programme_after": row[5],
                "break_code": row[6],
                "num_adverts": row[7],
            }
        )
    total_breaks = len(break_info)
    log(f"  {total_breaks} breaks loaded")

    log("  Building break-to-film mapping...")
    break_film_lists = [[] for _ in range(total_breaks)]
    cursor.execute(
        "SELECT break_id, film_code FROM break_films ORDER BY break_id"
    )
    for bid_str, film_code in cursor:
        break_int = bid_to_int[bid_str]
        film_int = film_to_id[film_code]
        break_film_lists[break_int].append(film_int)

    break_to_films = [array("I", sorted(f)) for f in break_film_lists]
    del break_film_lists

    log("  Building film-to-break inverted index...")
    film_appearances = [[] for _ in range(total_films)]
    for break_int, films in enumerate(break_to_films):
        for film_int in films:
            film_appearances[film_int].append(break_int)

    film_to_breaks = [array("I", sorted(a)) for a in film_appearances]
    del film_appearances

    return break_to_films, film_to_breaks, break_info, film_codes, total_films


def greedy_set_cover(
    break_to_films: list,
    film_to_breaks: list,
    total_films: int,
    break_filter: set[int] | None = None,
    initial_covered: set[int] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Run greedy set cover with a max-heap and lazy-update strategy.

    Args:
        break_to_films: list[array('I')] indexed by break_int.
        film_to_breaks: list[array('I')] indexed by film_int.
        total_films: Number of unique films to cover.
        break_filter: If set, only consider breaks whose break_int is in
            this set.
        initial_covered: If set, start with these film_ints already marked
            as covered.

    Returns:
        List of (break_int, newly_covered, cumulative, iteration_in_phase).
    """
    num_breaks = len(break_to_films)
    covered = set(initial_covered) if initial_covered else set()
    selected = []
    selected_set = set()
    version = [0] * num_breaks
    heap = []

    break_range = break_filter if break_filter is not None else range(num_breaks)

    log("  Building initial heap...")
    for bid in break_range:
        count = sum(1 for f in break_to_films[bid] if f not in covered)
        if count > 0:
            heappush(heap, (-count, bid, 0))
    log(f"  Heap initialised with {len(heap)} entries")

    iteration = 0

    while len(covered) < total_films and heap:
        neg_count, bid, ver = heappop(heap)
        if ver != version[bid]:
            continue

        actual = sum(1 for f in break_to_films[bid] if f not in covered)
        if actual != -neg_count:
            if actual > 0:
                version[bid] += 1
                heappush(heap, (-actual, bid, version[bid]))
            continue

        if actual == 0:
            continue

        iteration += 1
        selected_set.add(bid)
        newly_covered = [f for f in break_to_films[bid] if f not in covered]
        covered.update(newly_covered)
        selected.append((bid, len(newly_covered), len(covered), iteration))

        updated_breaks = set()
        for f in newly_covered:
            for other_bid in film_to_breaks[f]:
                if other_bid != bid and other_bid not in selected_set:
                    if break_filter is None or other_bid in break_filter:
                        updated_breaks.add(other_bid)

        for other_bid in updated_breaks:
            version[other_bid] += 1
            remaining = sum(
                1 for f in break_to_films[other_bid] if f not in covered
            )
            if remaining > 0:
                heappush(heap, (-remaining, other_bid, version[other_bid]))

        if iteration % 50 == 0:
            log(
                f"  Iteration {iteration}: {len(covered)}/{total_films}"
                f" films covered ({len(selected)} breaks)"
            )

    if len(covered) < total_films:
        log(
            f"  WARNING: Only covered {len(covered)}/{total_films} films"
            f" — {total_films - len(covered)} films have no containing breaks"
        )

    return selected


def output_results(
    all_selected: list,
    break_info: list,
    output_dir: str,
    parse_time: float,
    solve_time: float,
    total_breaks: int,
    total_films: int,
    csv_count: int,
) -> tuple[Path, Path]:
    """Write results to console, JSON, and CSV.

    all_selected entries: (break_int, newly_covered, cumulative, rank, phase).
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    hd_count = 0
    sd_count = 0

    selected_data = []
    for bid, new_count, cumulative, rank, phase in all_selected:
        meta = break_info[bid]
        if phase == "HD":
            hd_count += 1
        else:
            sd_count += 1
        selected_data.append(
            {
                "rank": rank,
                "phase": phase,
                "break_id": meta["break_id"],
                "date": meta["date"],
                "channel": meta["channel"],
                "start_time": meta["start_time"],
                "programme_before": meta["programme_before"],
                "programme_after": meta["programme_after"],
                "break_code": meta["break_code"],
                "num_adverts_in_break": meta["num_adverts"],
                "newly_covered": new_count,
                "cumulative_coverage": cumulative,
            }
        )

    total_time = parse_time + solve_time
    coverage_pct = (
        cumulative / total_films * 100 if all_selected and total_films > 0 else 0
    )
    hd_covered = all_selected[-1][2] if all_selected else 0
    sd_uncovered = total_films - hd_covered

    print()
    print("=" * 80)
    print("  Minimum Ad Break Set Cover \u2014 Results")
    print("=" * 80)
    print(f"  CSV files processed:   {csv_count:,}")
    print(f"  Total ad breaks:       {total_breaks:,}")
    print(f"  Unique adverts:        {total_films:,}")
    print(f"  HD breaks selected:    {hd_count:,}")
    print(f"  SD breaks selected:    {sd_count:,}")
    print(f"  Total selected:        {len(selected_data):,}")
    print(f"  Coverage:              {coverage_pct:.1f}%")
    print(f"  Parse time:            {parse_time:.1f}s")
    print(f"  Solve time:            {solve_time:.1f}s")
    print(f"  Total time:            {total_time:.1f}s")
    print()

    if selected_data:
        programmes_width = 36
        header = (
            f"{'Rank':>4}  {'Date':<12}  {'Time':<8}  "
            f"{'Channel':<14}  {'Phs':<3}  "
            f"{'Programmes':<{programmes_width}}  "
            f"{'Ads':<4}  {'New':<4}  {'Cov%':<5}"
        )
        print(header)
        print("-" * len(header))

        for d in selected_data[:60]:
            prog = (
                f"{d['programme_before'][:16].strip()}"
                f"\u2192"
                f"{d['programme_after'][:16].strip()}"
            )
            cov_pct = d["cumulative_coverage"] / total_films * 100
            print(
                f"{d['rank']:>4}  {d['date']:<12}  {d['start_time']:<8}  "
                f"{d['channel']:<14}  {d['phase']:<3}  "
                f"{prog:<{programmes_width}}  "
                f"{d['num_adverts_in_break']:<4}  {d['newly_covered']:<4}  "
                f"{cov_pct:>5.1f}%"
            )

        if len(selected_data) > 60:
            print(f"  ... and {len(selected_data) - 60} more breaks")

    json_path = out_path / "minimum_cover_result.json"
    with open(json_path, "w") as f:
        json.dump(
            {
                "summary": {
                    "csv_files": csv_count,
                    "total_breaks": total_breaks,
                    "unique_adverts": total_films,
                    "hd_selected": hd_count,
                    "sd_selected": sd_count,
                    "selected_breaks": len(selected_data),
                    "coverage_percent": round(coverage_pct, 1),
                    "algorithm": "two_phase_greedy_set_cover",
                    "runtime_seconds": {
                        "parse": round(parse_time, 1),
                        "solve": round(solve_time, 1),
                        "total": round(total_time, 1),
                    },
                },
                "selected_breaks": selected_data,
            },
            f,
            indent=2,
        )
    print(f"\n  JSON output: {json_path}")

    csv_path = out_path / "minimum_cover_result.csv"
    if selected_data:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(selected_data[0].keys()))
            writer.writeheader()
            writer.writerows(selected_data)
        print(f"  CSV output:  {csv_path}")
    else:
        csv_path.write_text(
            "rank,phase,break_id,date,start_time,channel,programme_before,"
            "programme_after,break_code,num_adverts_in_break,newly_covered,"
            "cumulative_coverage\n"
        )
        print(f"  CSV output:  {csv_path} (no breaks selected)")

    return json_path, csv_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Find the minimum number of ad breaks that contain every "
            "unique advert (Film Code) across BFI CSV exports."
        )
    )
    parser.add_argument(
        "--csv-folder",
        type=str,
        required=True,
        help="Path to folder containing BFI CSV export files",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="advert_cover_cache.db",
        help="SQLite cache file path (default: advert_cover_cache.db)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="advert_min_cover_output",
        help="Directory for output files (default: advert_min_cover_output)",
    )
    parser.add_argument(
        "--skip-parsing",
        action="store_true",
        help="Skip CSV parsing; reuse existing SQLite cache",
    )
    parser.add_argument(
        "--hd-channels-file",
        type=str,
        default=Path(__file__).parent / "hd_channels.txt",
        help="Path to HD channels list file (default: scripts/hd_channels.txt)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv or sys.argv[1:])

    csv_folder = Path(args.csv_folder)
    if not csv_folder.is_dir():
        print(f"Error: CSV folder not found: {csv_folder}", file=sys.stderr)
        return 1

    hd_channels = load_hd_channels(args.hd_channels_file)

    db_path = args.db_path
    output_dir = args.output_dir
    t_start = time.time()

    csv_count = 0
    total_breaks = 0
    parse_time = 0.0

    if args.skip_parsing and Path(db_path).exists():
        log("Skipping parsing (--skip-parsing), reusing existing DB")
        conn = sqlite3.connect(db_path)
    else:
        log(f"Phase 1: Parsing CSVs from {csv_folder}")
        conn = sqlite3.connect(db_path)
        create_db(conn)
        total_breaks, _, csv_count = parse_csvs_to_db(csv_folder, conn)
        parse_time = time.time() - t_start
        log(
            f"Phase 1 complete: {total_breaks} breaks"
            f" from {csv_count} CSVs in {parse_time:.1f}s"
        )

    t_solve = time.time()
    log("Phase 2: Loading data from SQLite")
    break_to_films, film_to_breaks, break_info, film_codes, total_films = (
        load_from_db(conn)
    )
    conn.close()

    log(
        f"Loaded {len(break_to_films)} breaks, {total_films} films — "
        f"classifying channels as HD/SD..."
    )

    hd_break_set = set()
    sd_break_set = set()
    for bid, info in enumerate(break_info):
        channel_lower = info["channel"].strip().lower()
        if channel_lower in hd_channels:
            hd_break_set.add(bid)
        else:
            sd_break_set.add(bid)

    log(
        f"  {len(hd_break_set)} HD breaks, "
        f"{len(sd_break_set)} SD breaks"
    )

    all_selected = []
    rank = 0

    log("Phase 3a: Greedy set cover on HD channels only")
    raw_hd = greedy_set_cover(
        break_to_films,
        film_to_breaks,
        total_films,
        break_filter=hd_break_set,
    )

    hd_covered = raw_hd[-1][2] if raw_hd else 0
    for bid, new_count, cumulative, iteration in raw_hd:
        rank += 1
        all_selected.append((bid, new_count, cumulative, rank, "HD"))
    log(
        f"  HD phase: {len(raw_hd)} breaks cover {hd_covered}/{total_films} films"
    )

    if hd_covered < total_films:
        log(
            f"Phase 3b: SD fallback for remaining "
            f"{total_films - hd_covered} adverts"
        )
        raw_sd = greedy_set_cover(
            break_to_films,
            film_to_breaks,
            total_films,
            break_filter=sd_break_set,
            initial_covered=set().union(
                *(break_to_films[b] for b, _, _, _ in raw_hd)
            ),
        )

        for bid, new_count, cumulative, iteration in raw_sd:
            rank += 1
            all_selected.append((bid, new_count, cumulative, rank, "SD"))
        log(
            f"  SD phase: {len(raw_sd)} breaks complete coverage"
        )
    else:
        log("  All adverts covered by HD breaks — no SD fallback needed")

    solve_time = time.time() - t_solve

    if all_selected:
        log(
            f"Selected {len(all_selected)} breaks total"
            f" (HD: {len(raw_hd)}, SD: {len(all_selected) - len(raw_hd)})"
        )
    else:
        log("No breaks selected (empty dataset?)")

    log("Phase 4: Writing output")
    output_results(
        all_selected,
        break_info,
        output_dir,
        parse_time,
        solve_time,
        len(break_to_films),
        total_films,
        csv_count,
    )

    total_time = time.time() - t_start
    log(f"Total time: {total_time:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
