# Quick Start Guide

Get up and running with OCR-based ad break identification in 5 minutes.

---

## Prerequisites

1. Python 3.10+ installed
2. vLLM server running with PaddleOCR-VL model
3. FFmpeg installed (for frame extraction and clip extraction)
4. CSV scheduling data in correct format

---

## vLLM Server Setup

Deploy PaddleOCR-VL via vLLM:

```bash
vllm serve PaddlePaddle/PaddleOCR-VL \
    --trust-remote-code \
    --max-num-batched-tokens 16384 \
    --no-enable-prefix-caching \
    --mm-processor-cache-gb 0
```

---

## Installation

```bash
cd /path/to/advert-identifier

# Install in editable mode with uv (recommended)
uv pip install -e .

# Verify commands are available
advert-identifier --help
advert-identifier-metadata-extract --help
```

---

## Example Workflow

### Step 1: Prepare Your Data

**Video file naming:**
```
2024-03-26_ITV1HD_13:30:00.mp4
YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4
```

**CSV file naming:**
```
2024-03-26_BFIExport.csv
YYYY-MM-DD_BFIExport.csv
```

Place videos in one folder, CSVs in another.

---

### Step 2: Full Pipeline (Recommended)

Process all videos with one command:

```bash
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv/files \
  --before-secs 10 \
  --after-secs 360 \
  --video-server-url http://your-server:1100 \
  --ocr-endpoint http://localhost:8000/v1/chat/completions \
  --ocr-model PaddlePaddle/PaddleOCR-VL
```

**What happens:**
1. Extracts metadata from CSV → JSON + pipeline state file
2. Runs OCR detection at 5 FPS on the original video (updates pipeline state)
3. Extracts individual advert clips (lossless, reads adjusted_start from state)
4. Generates summary report

**Output:**
- `{video}_metadata.json` — scheduling metadata
- `{video}_pipeline_state.json` — advert tracking across all stages
- `{video}_ocr.json` — per-frame OCR results (queryable)
- `{video}.xml` — detection results
- `{unique_id}_{category}_{brand}.mp4` — individual advert clips
- `advert-identifier-pipeline-summary.txt` — pipeline report

---

### Step 3: Manual Workflow (More Control)

If you need more control, run steps individually.

#### Extract Metadata

```bash
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4 \
  --output-dir metadata/ \
  --before-secs 10
```

Creates: `metadata/2024-03-26_ITV1HD_13:30:00_metadata.json`
Also creates: `metadata/2024-03-26_ITV1HD_13:30:00_pipeline_state.json`

#### OCR Detection

```bash
advert-identifier \
  -v "http://your-server:8000/video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --metadata-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --ad-break-index 1 \
  --before-secs 10 \
  --after-secs 360 \
  --ocr-endpoint http://localhost:8000/v1/chat/completions \
  --ocr-model PaddlePaddle/PaddleOCR-VL
```

Creates: `metadata/2024-03-26_ITV1HD_13:30:00.xml`
Also creates: `metadata/2024-03-26_ITV1HD_13:30:00_ocr.json` (per-frame OCR results)
Also updates: pipeline state with `detection` data (status → `detected`)

Use `--dry-run` to test frame extraction without making OCR API calls.

#### Extract Individual Advert Clips

```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13:30:00.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-server:8000/video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --output-dir clips/
```

**With pipeline state file (preferred — auto-computes seek offset):**

```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13:30:00.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-server:8000/video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --output-dir clips/ \
  --state-file metadata/2024-03-26_ITV1HD_13:30:00_pipeline_state.json
```

Reads `adjusted_start_broadcast` from the pipeline state file — no manual
`clip_offset` computation needed.

**With trimming (remove seconds from clip start/end):**
```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/video.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://your-server:8000/video/video.mp4" \
  --output-dir clips/ \
  --trim 0.5
```

---

## Quick Options Reference

### Common Flags

| Flag | Purpose | Example |
|------|---------|---------|
| `--fps` | Frame extraction rate (default 5.0) | `--fps 5.0` |
| `--before-secs` | Seconds before ad break start | `--before-secs 10` |
| `--after-secs` | Seconds after ad break start | `--after-secs 360` |
| `--ocr-endpoint` | vLLM OCR endpoint URL | `--ocr-endpoint http://localhost:8000/v1/chat/completions` |
| `--ocr-model` | OCR model name | `--ocr-model PaddlePaddle/PaddleOCR-VL` |
| `--verbose` | Show detailed progress | `--verbose` |
| `--dry-run` | Preview without OCR API calls | `--dry-run` |
| `--trim` | Trim seconds from clip start/end | `--trim 0.5` |
| `--pad` | Add seconds to clip start/end | `--pad 0.5` |
| `--clip-offset` | Broadcast-absolute offset for clip start | `--clip-offset 200.0` |

---

## Next Steps

- Read [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) for complete options
- Check [ARCHITECTURE.md](docs/ARCHITECTURE.md) to understand detection
- See [Coordinate Systems](docs/coordinate-systems.md) for timecode reference frames
- Check [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) if you encounter issues
