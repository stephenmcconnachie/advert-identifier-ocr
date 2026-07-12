# Advert Identifier

OCR-based advert identification in TV broadcast videos. Uses PaddleOCR-VL via vLLM to read text from video frames and match against brand/advertiser metadata.

Requires basic description of each advert with approximate start time and duration. It doesn't work without that prior descriptive metadata, so it won't identify adverts from a cold start.

## Quick Start

Three commands to process a broadcast video:

```bash
# 1. Extract metadata from CSV scheduling data
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4 \
  --output-dir video/

# 2. OCR detection (5 FPS frame extraction + PaddleOCR-VL + brand search)
advert-identifier \
  -v "video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --metadata-file video/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --ad-break-index 1 \
  --before-secs 10 \
  --after-secs 360

# 3. Extract individual advert clips (lossless)
advert-identifier-single-advert-clip \
  --xml-file video/2024-03-26_ITV1HD_13:30:00.xml \
  --json-file video/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --output-dir video/clips

# 4. Generate reference HTML grid from clipped videos (optional)
advert-identifier-reference \
  --clips-dir video/clips \
  --output-dir video/ \
  --video-stem "2024-03-26_ITV1HD_13:30:00"
```

## One-Command Pipeline

Process entire folders automatically:

```bash
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv/files \
  --before-secs 10 \
  --after-secs 360 \
  --ocr-endpoint http://localhost:8000/v1/chat/completions \
  --ocr-model PaddlePaddle/PaddleOCR-VL
```

Alternatively, set `--input-folder` and `--csv-folder` via environment variables:

```bash
export INPUT_FOLDER=/path/to/videos
export CSV_FOLDER=/path/to/csv/files
advert-identifier-pipeline --before-secs 10 --after-secs 360
```

CLI flags always take precedence over environment variables.

The pipeline creates a **pipeline state file** (`{video}_pipeline_state.json`) at Stage 1
and threads it through every subsequent stage. It runs two stages per ad break:
**OCR detection** → **lossless clip extraction**, with full coordinate transformation
from clip-relative to broadcast-absolute timecodes.

**Single-advert breaks** (only 1 advert in a break) are detected from the metadata JSON
and skipped entirely — no OCR, no clipping. A row is appended to
`single_advert_breaks.csv` in the repo root.

## vLLM Server Setup

Deploy PaddleOCR-VL via vLLM:

```bash
vllm serve PaddlePaddle/PaddleOCR-VL \
    --trust-remote-code \
    --max-num-batched-tokens 16384 \
    --no-enable-prefix-caching \
    --mm-processor-cache-gb 0
```

## Installation

### Option 1: Using uv (Recommended)

```bash
cd /path/to/advert-identifier
uv pip install -e .
advert-identifier --help
```

### Option 2: Using pip

```bash
cd /path/to/advert-identifier
pip install -e .
advert-identifier --help
```

### Option 3: Direct Execution (No Installation)

```bash
cd /path/to/advert-identifier
python3 bin/advert-identifier --help
python3 bin/advert-identifier-metadata-extract --help
```

## Commands Reference

| Command | Purpose | Key Options |
|---------|---------|-------------|
| `advert-identifier` | OCR detection (5 FPS + PaddleOCR-VL) | `--video-url` (local path or URL), `--metadata-file`, `--ad-break-index`, `--before-secs`, `--after-secs`, `--fps`, `--ocr-endpoint`, `--ocr-model`, `--anchor-threshold`, `--clamp-majority-rule`, `--verbose`, `--dry-run` |
| `advert-identifier-pipeline` | Full folder automation | `--input-folder`, `--csv-folder`, `--before-secs`, `--after-secs`, `--fps`, `--ocr-endpoint`, `--ocr-model`, `--anchor-threshold`, `--clamp-majority-rule`, `--max-workers`, `--min-break-confidence` |
| `advert-identifier-metadata-extract` | CSV → JSON metadata | `--video`, `--csv-folder`, `--before-secs` |
| `advert-identifier-single-advert-clip` | Extract lossless advert clips from XML | `--xml-file`, `--video-url`, `--json-file`, `--index`, `--trim`, `--pad`, `--clip-offset`, `--ad-break-index`, `--state-file`, `--max-workers` |
| `advert-identifier-reference` | Generate reference HTML grid from clipped videos | `--clips-dir`, `--output-dir`, `--video-stem` |
| `advert-identifier-analysis` | Post-run analysis HTML report | `--video-dir`, `--pipeline-log`, `--output` |

See [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) for complete documentation.

## How It Works

1. **Metadata Extraction**: Parses video filenames to find matching CSV scheduling data
2. **OCR Detection**: Extracts frames at 5 FPS from the original broadcast video around each ad break, runs PaddleOCR-VL on every frame, stores results in a queryable JSON file
3. **Two-Tier Brand Search**: For each advert in order, searches OCR text for brand/advertiser/category using:
   - **Tier 1 (exact)**: Word-boundary regex (`\bgalaxy\b`) for precise matches
   - **Tier 2 (substring)**: Unbounded regex (`galaxy`) as fallback for concatenated forms like `galaxychocolate.com`
4. **Ordering Enforcement**: Each advert's last matching frame must be after the previous advert's last matching frame
5. **Anchor Re-estimation** (optional): For low-confidence breaks, selects the strongest OCR match as an anchor, then computes all other advert positions from known durations. Matched adverts are preserved at their raw OCR positions; unmatched adverts get schedule-based estimates.
6. **25fps End-Frame Refinement**: Extracts 5 source-rate frames (25 FPS) starting at each advert's detected 5fps match frame. Uses **boundary detection** — compares each refinement frame's OCR text to the current and next 5fps frame's text — to find the exact advert boundary at sub-frame precision (up to 4 source frames = 0.16s). This works even when brand text is not visible at the match position (e.g. sponsorship endcards).
7. **Clamp/Cage Correction** (after refinement): Detects pattern anomalies in refined 25fps end positions by comparing each advert's `sec_digit.mmm` pattern against the majority across the break. Snaps anomalous matches to the nearest frame matching the majority sec_digit pattern using scheduled durations. Running after refinement gives 25x more pattern precision, making coincidental matches far less likely.
8. **Advert Clip Extraction**: Extracts individual advert clips using refined timecodes and durations (parallel with `--max-workers`)
9. **Post-Run Analysis**: Generates a themed HTML report with per-break success metrics, unmatched/weak advert details, clamp activity, anchor events, and confidence scores
10. **Reference HTML Grid**: Post-run tool that scans clipped advert videos, extracts the first frame of each, and generates a themed responsive HTML grid with unique ID, category, and brand labels
11. **Pipeline State Tracking**: A persistent state file (`_pipeline_state.json`) tracks every advert through all stages

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a detailed breakdown.

## Coordinate Systems

The pipeline uses three coordinate frames. Understanding them is critical for correct
clip extraction:

| Frame | Origin | Used by |
|-------|--------|---------|
| **Broadcast-absolute** | Start of full broadcast `.mp4` | FFmpeg `-ss` seeks |
| **Clip-relative** | Start of the extracted frame range | OCR detection output |
| **Time-of-day** | Wall clock HH:MM:SS | CSV metadata |

The pipeline state file automatically converts between these frames. See
[docs/coordinate-systems.md](docs/coordinate-systems.md) for the full reference.

## Example Output

```xml
<ad_break>
    <!-- OCR-based detection (5 FPS, PaddleOCR-VL) -->
    <advert>
        <unique_id>BBHTCPT536010</unique_id>
        <brand>Tesco</brand>
        <advertiser>Tesco PLC</advertiser>
        <category>retail</category>
        <duration_seconds>10</duration_seconds>
        <start_timecode>02:05.160</start_timecode>
        <last_timecode>02:15.160</last_timecode>
        <match_tier>exact</match_tier>
        <matched_terms>tesco</matched_terms>
    </advert>
</ad_break>
```

## Pipeline State File

When you run `advert-identifier-metadata-extract` with `--before-secs` (default 10.0),
it creates a `{video}_pipeline_state.json` alongside the metadata JSON. This file
is progressively updated by each stage:

```json
{
  "pipeline_version": 2,
  "video_info": {
    "filepath": "video/2024-03-26_ITV1HD_13:30:00.mp4",
    "start_time": "13:30:00"
  },
  "ad_breaks": [
    {
      "index": 1,
      "start_time": "13:52:05",
      "clip_offset": 1295.0,
      "adverts": [
        {
          "unique_id": "BBHTCPT536010",
          "brand": "Tesco",
          "status": "detected",
          "detection": {
            "last_timecode": "02:15.000",
            "last_seconds_clip": 135.0,
            "last_frame": 675,
            "match_tier": "exact",
            "matched_terms": ["tesco"],
            "last_seconds_broadcast": 1430.0,
            "adjusted_start_broadcast": 1420.0
          }
        }
      ]
    }
  ]
}
```

The `adjusted_start_broadcast` field is the final value used for FFmpeg seek offsets
in `advert-identifier-single-advert-clip` (via `--state-file`).

## Requirements

- Python 3.10+
- **vLLM server** with PaddleOCR-VL model running (local or remote)
- **FFmpeg** (for frame extraction and clip extraction)
- **`requests`** Python package (for OCR API calls)
- TV broadcast videos

## Documentation

- [CLI Reference](docs/CLI_REFERENCE.md) — Complete command documentation
- [Architecture](docs/ARCHITECTURE.md) — How the detection works
- [Coordinate Systems](docs/coordinate-systems.md) — Timecode reference frames
- [Troubleshooting](docs/TROUBLESHOOTING.md) — Common errors and solutions

## File Naming Conventions

**Videos:** `YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4`  
**CSV Data:** `YYYY-MM-DD_BFIExport.csv` (multiple channels per file)

## License

MIT License — See LICENSE file for details.
