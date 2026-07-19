# fetch_filepaths.py

Fetch video filepaths for minimum set cover results via two-phase CID API lookup.

Takes the output from `advert-minimum-set-cover.py`, matches each selected ad break
to the programme that was airing at that time, then looks up the video filepath
for that programme from a media archive.

## Usage

```bash
export CID_API_BASE="http://your-cid-server/wwwopac.ashx"
export CID_FILEPATH_PREFIX="/mnt/bp_nas/access_renditions/bfi"

python3 scripts/fetch_filepaths.py \
    --input-dir advert_min_cover_output
```

## Two-Phase Lookup

| Phase | Database | Purpose |
|-------|----------|---------|
| **1 — Manifestations** | `manifestations` | Query by channel + date, get all programme records with `parts_reference`. Match the ad break `start_time` against programme `transmission_start_time` to find the containing programme's `object_number`. |
| **2 — Media** | `media` | Use the `object_number` to fetch `access_rendition.mp4` and `input.date`, then construct the filepath. |

### Matching Logic

For each ad break with `start_time=21:46:44`:
1. Fetch all programmes for that channel + date
2. Sort by `transmission_start_time` ascending
3. Find the latest programme whose start time ≤ the ad break time
4. That programme's `object_number` is used for Phase 2

Example — ad break at 21:46:44 with broadcasts starting at 19:30, 20:00, 21:30, 22:15:
→ Matches **21:30** (the programme that was airing).

### Filepath Construction

```
{prefix}/{yyyymm}/{rendition}
```

Field | Source | Example
------|--------|--------
`prefix` | `CID_FILEPATH_PREFIX` env var | `/mnt/bp_nas/access_renditions/bfi`
`yyyymm` | `input.date` from Phase 2, first 7 chars | `202606`
`rendition` | `access_rendition.mp4` from Phase 2 | `N_11351731_01of01`

Full example: `/mnt/bp_nas/access_renditions/bfi/202606/N_11351731_01of01`

## Channel Lookup

CID database channel names differ from CSV conventions. The script maps them via `_CHANNEL_LOOKUP`:

| CSV channel | DB channel term |
|-------------|-----------------|
| `5` | `Channel 5 HD` |
| `5STAR` | `5STAR` |
| `CH4` | `Channel 4 HD` |
| `Channel 5` | `Channel 5 HD` |
| `E4` | `E4` |
| `Film4` | `Film4` |
| `ITV1` | `ITV HD` |
| `ITV1 HD` | `ITV HD` |
| `ITV2` | `ITV2` |
| `ITV3` | `ITV3` |
| `ITV4` | `ITV4` |
| `ITVBe` | `ITV Be` |
| `ITVQuiz` | `ITV Quiz` |
| `More4` | `More4` |

## Output

### CSV (`minimum_cover_result_filepath.csv`)

Same as input with added columns:
- `object_number` — the CID object number for the matched programme
- `filepath` — the constructed video filepath (or error code)

### JSON (`minimum_cover_result_filepath.json`)

Same as input with `object_number` and `filepath` fields added to each `selected_breaks[i]`.

## Error Codes

| Code | Cause |
|------|-------|
| `NO_CID_RESPONSE` | API request failed or returned non-200 |
| `NO_CID_PROG_MATCH` | No programme matched the ad break time, or matched record had no `Parts` / `object_number` |
| `NO_CID_MEDIA_RECORD` | Phase 2 returned no media record for the `object_number` |
| `NO_CID_ACCESS_RENDITION` | Media record had no `access_rendition.mp4` field |
| `NO_CID_INPUT_DATE` | Media record had no `input.date` field |

## CLI Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `advert_min_cover_output` | Directory containing `minimum_cover_result.csv` and `minimum_cover_result.json` |
| `--api-base` | `CID_API_BASE` env var | CID API endpoint URL |
| `--rate-limit` | `1.0` | Minimum seconds between API calls |
| `--resume` | *(off)* | Skip rows that already have a filepath in the output files |

## Performance

~21,000 unique (channel, date) pairs across 55,000 rows. At 1 req/s:
- Phase 1: ~6 h
- Phase 2: ~1–2 h (with object_number caching)
- Total: ~7–8 h

Use `--resume` to safely continue after interruption.
