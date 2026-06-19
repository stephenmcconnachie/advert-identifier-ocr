# CLI Reference

Complete documentation for all ad break identifier commands.

---

## advert-identifier

OCR-based advert boundary detection using PaddleOCR-VL at 5 FPS.

### Usage

```bash
advert-identifier -v URL --metadata-file PATH [OPTIONS]
```

### Arguments

| Argument | Short | Required | Default | Description |
|----------|-------|----------|---------|-------------|
| `--video-url` | `-v` | Yes | - | URL to the original broadcast video |
| `--metadata-file` | | No* | - | JSON file with ad break metadata |
| `--ad-break-index` | | No | auto | 1-based ad break index (auto-detected from filename) |
| `--prog-before` | | No* | - | Programme before ad break: "Title,Channel" |
| `--prog-after` | | No* | - | Programme after ad break: "Title,Channel" |
| `--advert` | | No* | - | Advert: "ID\|ADVERTISER\|BRAND\|CATEGORY\|DURATION" (can specify multiple) |
| `--before-secs` | | No | 10.0 | Seconds before ad break start for frame extraction |
| `--after-secs` | | No | 360.0 | Seconds after ad break start for frame extraction |
| `--fps` | | No | 5.0 | Frame extraction rate |
| `--ocr-endpoint` | | No | `http://localhost:8000/v1/chat/completions` | vLLM OCR endpoint |
| `--ocr-model` | | No | `PaddlePaddle/PaddleOCR-VL` | OCR model name |
| `--output-dir` | | No | - | Directory for frame images and OCR results JSON |
| `--output` | `-o` | No | auto | Output XML path (default: beside metadata file) |
| `--verbose` | | No | False | Show detailed progress |
| `--dry-run` | | No | False | Skip OCR API calls (test frame extraction only) |

*Either `--metadata-file` or (`--prog-before` + `--prog-after` + `--advert`) is required.

The identifier automatically updates the pipeline state file with `detection`
results when `--metadata-file` has a corresponding `_pipeline_state.json`.

### How It Works

1. Downloads the original video to a temp file
2. Extracts frames at 5 FPS from `[break_start - before_secs, break_start + after_secs]`
3. OCRs every frame via PaddleOCR-VL (base64 PNG + "OCR:" prompt)
4. Saves OCR results to `{video_stem}_ocr.json` (queryable)
5. For each advert in order, performs two-tier brand search:
   - **Tier 1 (exact)**: Word-boundary regex (`\bgalaxy\b`)
   - **Tier 2 (substring)**: Unbounded regex (`galaxy`) as fallback
6. Enforces ordering: each advert's last frame > previous advert's last frame
7. Outputs XML with `<last_timecode>` (clip-relative MM:SS.mmm)

### Examples

```bash
# Basic usage
advert-identifier \
  -v "http://server/video.mp4" \
  --metadata-file metadata.json \
  --ad-break-index 1

# Custom frame range and OCR settings
advert-identifier \
  -v "http://server/video.mp4" \
  --metadata-file metadata.json \
  --before-secs 30 \
  --after-secs 180 \
  --ocr-endpoint http://localhost:8000/v1/chat/completions \
  --ocr-model PaddlePaddle/PaddleOCR-VL \
  --verbose

# Dry-run (frame extraction only, no OCR API calls)
advert-identifier \
  -v "http://server/video.mp4" \
  --metadata-file metadata.json \
  --dry-run

# Save frame images and OCR results for debugging
advert-identifier \
  -v "http://server/video.mp4" \
  --metadata-file metadata.json \
  --output-dir debug_output/
```

---

## advert-identifier-pipeline

Full automation: process entire folders of videos end-to-end.

### Usage

```bash
advert-identifier-pipeline --input-folder PATH --csv-folder PATH [OPTIONS]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--input-folder` | Yes | - | Folder containing .mp4 video files |
| `--csv-folder` | Yes | - | Folder containing CSV files |
| `--before-secs` | No | 10.0 | Seconds before ad break for frame extraction |
| `--after-secs` | No | 360.0 | Seconds after ad break for frame extraction |
| `--fps` | No | 5.0 | Frame extraction rate for OCR detection |
| `--ocr-endpoint` | No | `http://localhost:8000/v1/chat/completions` | vLLM OCR endpoint |
| `--ocr-model` | No | `PaddlePaddle/PaddleOCR-VL` | OCR model name |
| `--video-server-url` | No | `http://172.18.7.236:1100` | Base URL for video server |
| `--dry-run` | No | False | Preview what would be processed |
| `--log-level` | No | INFO | DEBUG, INFO, WARNING, ERROR |
| `--verbose` | No | False | Show detailed progress |

### Pipeline Workflow

For each video file, the pipeline:

1. **Metadata Extraction**: Finds matching CSV, converts to JSON + pipeline state
2. **OCR Detection**: Runs PaddleOCR-VL at 5 FPS on the original video (updates state)
3. **Clip Extraction**: Extracts individual advert clips using detected timecodes
4. **Summary Report**: Creates `advert-identifier-pipeline-summary.txt`

### Examples

```bash
# Basic usage
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv

# Custom frame range and OCR settings
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv \
  --before-secs 30 \
  --after-secs 180 \
  --ocr-endpoint http://localhost:8000/v1/chat/completions \
  --ocr-model PaddlePaddle/PaddleOCR-VL

# Dry run to preview without executing
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv \
  --dry-run
```

---

## advert-identifier-metadata-extract

Extract ad break metadata from CSV scheduling data based on video filename.

### Usage

```bash
advert-identifier-metadata-extract --video PATH [OPTIONS]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--video` | Yes | - | Path to video file |
| `--csv-folder` | No | `/mnt/qnap_04/Admin/datasets/adverts_techedge_no_dupes` | Folder containing CSV files |
| `--output-dir` | No | Video directory | Where to save JSON metadata |
| `--before-secs` | No | 10.0 | Seconds before ad break start (affects clip_offset in pipeline state) |

### Output

Creates two files:
- `{video_name}_metadata.json` — scheduling metadata from CSV
- `{video_name}_pipeline_state.json` — per-advert tracking with `clip_offset`

### Examples

```bash
# Using default CSV folder
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4

# Custom CSV folder and output directory
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4 \
  --csv-folder /custom/path/to/csv \
  --output-dir metadata/

# Custom before-secs for pipeline state clip_offset
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4 \
  --before-secs 30.0
```

---

## advert-identifier-single-advert-clip

Extract individual advert clips from XML analysis results.

### Usage

```bash
advert-identifier-single-advert-clip --xml-file PATH --video-url URL --json-file PATH [OPTIONS]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--xml-file` | Yes | - | Path to XML with advert detection results |
| `--video-url` | Yes | - | URL or path to the **full broadcast** video |
| `--json-file` | Yes | - | JSON metadata file (for category extraction) |
| `--output-dir` | No | `.` | Output directory for clips |
| `--index` | No | all | 1-based advert index to process |
| `--trim` | No | 0.0 | Seconds to trim from start and end of each clip |
| `--pad` | No | 0.0 | Seconds to add to start and end of each clip |
| `--clip-offset` | No | 0.0 | Seconds from broadcast start to clip start |
| `--state-file` | No | - | Path to pipeline state file. Reads `adjusted_start_broadcast` directly |
| `--log-level` | No | INFO | DEBUG, INFO, WARNING, ERROR |

### Timecode Resolution

The tool selects the most precise timecode available using this priority:

1. **Pipeline state file** (`--state-file`): Uses `adjusted_start_broadcast` directly.
   No `clip_offset` computation needed.
2. **XML** (`<last_timecode>`): Uses the detection timecode, applies `--clip-offset`.

### Examples

```bash
# Basic usage with clip offset
advert-identifier-single-advert-clip \
  --xml-file results/video.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://server/full_broadcast.mp4" \
  --output-dir clips/ \
  --clip-offset 200.0

# Using pipeline state file (auto-computes seek offset)
advert-identifier-single-advert-clip \
  --xml-file results/video.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://server/full_broadcast.mp4" \
  --output-dir clips/ \
  --state-file metadata/video_pipeline_state.json

# Extract a single advert by index
advert-identifier-single-advert-clip \
  --xml-file results/video.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://server/full_broadcast.mp4" \
  --output-dir clips/ \
  --index 1

# With trimming (remove 0.5s from start and end)
advert-identifier-single-advert-clip \
  --xml-file results/video.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://server/full_broadcast.mp4" \
  --output-dir clips/ \
  --trim 0.5
```

---

## Environment Variables

All commands respect these environment variables (CLI flags override):

| Variable | Default | Description |
|----------|---------|-------------|
| `OCR_ENDPOINT` | `http://localhost:8000/v1/chat/completions` | vLLM OCR endpoint |
| `OCR_MODEL` | `PaddlePaddle/PaddleOCR-VL` | OCR model name |
| `DETECTION_FPS` | `5.0` | Frame extraction rate |
| `BEFORE_SECS` | `10.0` | Seconds before ad break start |
| `AFTER_SECS` | `360.0` | Seconds after ad break start |

---

## File Naming Conventions

### Video Files
Format: `YYYY-MM-DD_CHANNEL_HH:MM:SS[_anything].ext`

Examples:
- `2024-03-26_ITV1HD_13:30:00.mp4`
- `2024-03-26_ITV1HD_09:00:00_TIMECODE_OVERLAY.mp4`

### CSV Files
Format: `YYYY-MM-DD_BFIExport.csv`

Examples:
- `2024-03-26_BFIExport.csv`
- `2024-03-27_BFIExport.csv`

### Pipeline State Files
Format: `{video_stem}_pipeline_state.json`

Created by `advert-identifier-metadata-extract` alongside the metadata JSON.
Updated by `advert-identifier` (detection). Read by `advert-identifier-single-advert-clip` for
broadcast-absolute seek offsets.

### OCR Results Files
Format: `{video_stem}_ocr.json`

Created by `advert-identifier` alongside the metadata JSON.
Contains per-frame OCR text, timestamps, and frame indices. Queryable for debugging.
