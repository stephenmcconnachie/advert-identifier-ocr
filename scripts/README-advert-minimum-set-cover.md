# advert-minimum-set-cover.py

Find the **smallest set of ad breaks** that contains every unique advert (`Film Code`) across all BFI CSV export files.

This is the classic **Minimum Set Cover** problem (NP-hard). The implementation uses a **greedy approximation** with a max-heap and lazy-update strategy, guaranteeing coverage within O(log n) of optimal.

## Usage

```bash
python scripts/advert-minimum-set-cover.py --csv-folder /path/to/csvs
```

### Full dataset

```bash
python scripts/advert-minimum-set-cover.py \
    --csv-folder /mnt/qnap_04/Admin/datasets/adverts_techedge \
    --output-dir advert_min_cover_output \
    --hd-channels-file scripts/hd_channels.txt \
    | tee advert_min_cover_output/run.log
```

### Re-run (skip slow CSV parsing)

Parsing all 3,810 CSVs takes ~3–4 hours. If you only changed the HD channels list, reuse the cached SQLite database:

```bash
python scripts/advert-minimum-set-cover.py \
    --csv-folder /mnt/qnap_04/Admin/datasets/adverts_techedge \
    --skip-parsing \
    --hd-channels-file scripts/hd_channels.txt
```

## Two-Phase HD-Priority Algorithm

The algorithm respects your picture-quality preference by running in two phases:

| Phase | Scope | Behaviour |
|-------|-------|-----------|
| **3a — HD** | Channels listed in `hd_channels.txt` | Greedy set cover to cover all unique adverts using only HD breaks |
| **3b — SD** | All remaining channels | Greedy set cover seeded with already-covered adverts; only picks SD breaks for adverts not found on HD |

The output table tags each selected break with `HD` or `SD`.

### HD channels file

Edit `scripts/hd_channels.txt` to control which channels are considered HD. One channel per line, case-insensitive, `#` for comments:

```
# HD Channels
ITV1 HD
CH4
Channel 5
ITV1
5
```

## Algorithm Details

1. **Parse** — reads each CSV, detects ad breaks using programme context (`BARB Prog Before` / `BARB Prog After`) and `All PIB rel == "Last"` as break delimiters. Writes `(break_id, film_code)` pairs to a SQLite cache.
2. **Load** — remaps film codes and break IDs to dense integer indices for memory efficiency. Builds forward (`break → films`) and inverted (`film → breaks`) indexes using `array('I')` (~200 MB for the full 10-year dataset).
3. **Greedy set cover** — repeatedly picks the break covering the most still-uncovered adverts. Lazy-update heap ensures stale entries are skipped. HD-first then SD fallback.
4. **Output** — console table, `minimum_cover_result.json`, and `minimum_cover_result.csv`.

## Output

### Console

```
  HD breaks selected:    326  (covering 1,083 of 1,556 adverts)
  SD breaks selected:    234  (covering remaining 473)
  Total selected:        560  (100.0% coverage)

Rank  Date          Time      Channel         Phs  Programmes                        Ads  New  Cov%
   1  06/01/2016    16:03:51  Channel 5       HD   AMISH GRACE→AMISH GRACE          20   20   1.3%
   2  02/01/2016    19:14:29  Channel 5       HD   BEN FOGLE: NEW L→BEN FOGLE:...   18   18   2.4%
  ...
 327  01/01/2016    04:36:37  ITV2            SD   THE BIG QUIZ→THE BIG QUIZ         9    9    69.6%
  ...
```

### JSON (`minimum_cover_result.json`)

Machine-readable with summary stats and per-break details including `"phase": "HD"` / `"phase": "SD"`.

### CSV (`minimum_cover_result.csv`)

One row per selected break, same fields as JSON.

## CLI Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--csv-folder` | *(required)* | Path to folder containing BFI CSV exports |
| `--output-dir` | `advert_min_cover_output` | Directory for output files |
| `--db-path` | `advert_cover_cache.db` | SQLite cache file (reused with `--skip-parsing`) |
| `--skip-parsing` | *(off)* | Skip CSV parsing; reuse existing SQLite cache |
| `--hd-channels-file` | `scripts/hd_channels.txt` | Path to HD channels list file |

## Requirements

- Python 3.10+ (stdlib only — no external dependencies)
- Enough RAM for the inverted index (~200–400 MB for the full dataset)

## Performance

| Dataset | CSVs | Breaks | Unique adverts | Parse time | Solve time |
|---------|------|--------|----------------|------------|------------|
| 10 days | 10 | 5,578 | 1,556 | ~31 s | ~0.1 s |
| Full (est.) | 3,810 | ~2.1M | ~50–100K | ~3–4 h | ~1–5 min |
