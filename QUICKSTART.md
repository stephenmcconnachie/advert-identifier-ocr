# Quick Start Guide

Get up and running with ad break identification in 5 minutes.

---

## Prerequisites

1. Python 3.10+ installed
2. vLLM server running with vision-language model
3. FFmpeg installed (for video clipping)
4. CSV scheduling data in correct format

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
  --video-server-url http://your-server:1100
```

**What happens:**
1. Extracts metadata from CSV → JSON + pipeline state file
2. Extracts video clips centered on ad breaks
3. Runs AI analysis to identify adverts at 1 FPS (updates pipeline state)
4. Refines boundaries to frame accuracy at 25 FPS (updates pipeline state)
5. Extracts individual advert clips (lossless, reads adjusted_start from state)
6. Generates summary report

**Output:**
- `{video}_metadata.json` — scheduling metadata
- `{video}_pipeline_state.json` — advert tracking across all stages
- `{video}_{timecode}_CLIP.mp4` — 6-minute ad break clips
- `{video}.xml` — 1 FPS detection results
- `{video}_refined.xml` — 25 FPS refinement results (auto-generated)
- `{unique_id}_{category}_{brand}.mp4` — individual advert clips
- `advert_identifier_pipeline_summary.txt` — pipeline report

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

The pipeline state file is created alongside the metadata JSON. It computes
`clip_offset` per ad break and tracks advert data through all subsequent stages.

#### Extract Clips

```bash
advert-identifier-clip \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --before-secs 10 \
  --after-secs 360
```

Creates: `video/2024-03-26_ITV1HD_13:30:00_01ofXX.mp4` (one per ad break)

#### Identify Adverts

```bash
advert-identifier \
  --video "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_01ofXX.mp4" \
  --metadata-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --ad-break-index 1
```

Creates: `metadata/2024-03-26_ITV1HD_13:30:00_01ofXX.xml`
Also updates: pipeline state with `coarse_1fps` data (status → `identified`)

#### (Optional) Refine to Frame-Accurate Boundaries

If you need frame-accurate advert boundaries (rather than 1-second resolution), run the refinement stage:

```bash
advert-identifier-refine \
  --xml-file metadata/2024-03-26_ITV1HD_13:30:00_01ofXX.xml \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_01ofXX.mp4" \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --ensemble-filter mad
```

Creates: `metadata/2024-03-26_ITV1HD_13:30:00_01ofXX_refined.xml`
Also updates: pipeline state with `refined_25fps` data including
`adjusted_start_broadcast` (status → `refined`), millisecond precision.

The refinement stage extracts 3-second clips (1.5s before/after each advert's expected
end), analyzes them at 25 FPS with ensemble voting, and outputs precise `HH:MM:SS.mmm`
timecodes. Use `--refine-fps 24.0` for NTSC video sources.

#### Extract Individual Advert Clips

```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13:30:00_01ofXX_refined.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --output-dir clips/
```

Uses `refined_timecode` from the XML when available, then applies `--clip-offset`
to convert from clip-relative to broadcast-absolute coordinates.

**With pipeline state file (preferred — auto-computes seek offset):**

```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13:30:00_01ofXX_refined.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00.mp4" \
  --output-dir clips/ \
  --state-file metadata/2024-03-26_ITV1HD_13:30:00_pipeline_state.json
```

Reads `adjusted_start_broadcast` from the pipeline state file — no manual
`clip_offset` computation needed. Falls back to XML + clip_offset if refinement
data isn't available yet.

**With trimming (remove seconds from clip start/end):**
```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/video_clip.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://your-vllm-server:8000/video/video.mp4" \
  --output-dir clips/ \
  --trim 0.5
```

**With padding (add seconds to clip start/end):**
```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/video_clip.xml \
  --json-file metadata/video_metadata.json \
  --video-url "http://your-vllm-server:8000/video/video.mp4" \
  --output-dir clips/ \
  --pad 0.5
```

---

## Quick Options Reference

### Common Flags

| Flag | Purpose | Example |
|------|---------|---------|
| `--model NAME` | Override default model (e.g., Qwen/Qwen3.5-9B) | `--model Qwen/Qwen3.5-9B` |
| `--mode frame` | Use frame numbers instead of timecodes | `--mode frame` |
| `--no-ensemble` | Single API call (faster, less accurate) | `--no-ensemble` |
| `--no-thinking` | Disable model reasoning (much faster) | `--no-thinking` |
| `--debug` | Save debug info | `--debug` |
| `--dry-run` | Preview without executing | `--dry-run` |
| `--trim` | Trim seconds from clip start/end | `--trim 0.5` |
| `--pad` | Add seconds to clip start/end | `--pad 0.5` |
| `--clip-offset` | Broadcast-absolute offset for clip start | `--clip-offset 200.0` |
| `--ensemble-filter` | Outlier rejection method for votes | `--ensemble-filter mad` |

### Speed vs Accuracy

**Fastest (testing):**
```bash
advert-identifier ... --no-ensemble --no-thinking
```

**Balanced (default):**
```bash
advert-identifier ... --ensemble-size 5
```

**Most Accurate:**
```bash
advert-identifier ... --ensemble-size 10 --ensemble-delay 5 --refine --ensemble-filter mad
```

---

## Next Steps

- Read [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) for complete options
- Check [ARCHITECTURE.md](docs/ARCHITECTURE.md) to understand detection
- See [Coordinate Systems](docs/coordinate-systems.md) for timecode reference frames
- Check [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) if you encounter issues
- Use `advert-identifier-benchmark` to test accuracy
