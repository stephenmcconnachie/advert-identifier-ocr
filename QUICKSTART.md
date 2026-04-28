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

# Install in editable mode
pip install -e .

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

### Step 2: Full Pipeline (Easiest) - NOT WORKING YET - DEBUGGING REQUIRED

Process all videos with one command:

```bash
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv/files \
  --before-secs 60 \
  --after-secs 360
```

**What happens:**
1. Extracts metadata from CSV → JSON
2. Extracts video clips centered on ad breaks
3. Runs AI analysis to identify adverts
4. Extracts individual advert clips (lossless)
5. Generates summary report

**Output:**
- `{video}_metadata.json` files
- `{video}_{timecode}_CLIP.mp4` clips
- `{video}.xml` results
- `{unique_id}_{category}_{brand}.mp4` individual advert clips
- `advert_identifier_pipeline_summary.txt` report

---

### Step 3: Manual Workflow (More Control)

If you need more control, run steps individually:

#### Extract Metadata

```bash
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4 \
  --output-dir metadata/
```

Creates: `metadata/2024-03-26_ITV1HD_13:30:00_metadata.json`

#### Extract Clips

```bash
advert-identifier-clip \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --before-secs 60 \
  --after-secs 360
```

Creates: `video/2024-03-26_ITV1HD_13:30:00_13-52-05.000_CLIP.mp4`

#### Identify Adverts

```bash
advert-identifier \
  --video "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_13-52-05.000_CLIP.mp4" \
  --metadata-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json
```

Creates: `metadata/2024-03-26_ITV1HD_13-52-05.000_CLIP.xml`

#### (Optional) Refine to Frame-Accurate Boundaries

If you need frame-accurate advert boundaries (rather than 1-second resolution), run the refinement stage:

```bash
advert-identifier-refine \
  --xml-file metadata/2024-03-26_ITV1HD_13-52-05.000_CLIP.xml \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_01of01.mp4" \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json
```

Creates: `metadata/2024-03-26_ITV1HD_13-52-05.000_CLIP_refined.xml`

The refinement stage extracts 3-second clips (1.5s before/after each advert's expected end), analyzes them at 25 FPS with ensemble voting, and outputs precise `HH:MM:SS.mmm` timecodes. Use `--refine-fps 24.0` for NTSC video sources.

#### Extract Individual Advert Clips

```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13-52-05.000_CLIP.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_01of01.mp4" \
  --output-dir clips/
```

Creates: `{unique_id}_{category}_{brand}.mp4` for each advert (lossless H.264)

**With trimming (remove seconds from clip start/end):**
```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13-52-05.000_CLIP.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_01of01.mp4" \
  --output-dir clips/ \
  --trim 0.5
```

**With padding (add seconds to clip start/end):**
```bash
advert-identifier-single-advert-clip \
  --xml-file metadata/2024-03-26_ITV1HD_13-52-05.000_CLIP.xml \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-vllm-server:8000/video/2024-03-26_ITV1HD_13:30:00_01of01.mp4" \
  --output-dir clips/ \
  --pad 0.5
```

---

## Quick Options Reference

### Common Flags

| Flag | Purpose | Example |
|------|---------|---------|
| `--mode frame` | Use frame numbers instead of timecodes | `--mode frame` |
| `--no-ensemble` | Single API call (faster, less accurate) | `--no-ensemble` |
| `--debug` | Save debug info | `--debug` |
| `--dry-run` | Preview without executing | `--dry-run` |
| `--trim` | Trim seconds from clip start/end | `--trim 0.5` |
| `--pad` | Add seconds to clip start/end | `--pad 0.5` |

### Speed vs Accuracy

**Fastest (testing):**
```bash
advert-identifier ... --no-ensemble
```

**Balanced (default):**
```bash
advert-identifier ... --ensemble-size 5
```

**Most Accurate:**
```bash
advert-identifier ... --ensemble-size 10 --ensemble-delay 5
```

---

## Next Steps

- Read [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) for complete options
- Check [ARCHITECTURE.md](docs/ARCHITECTURE.md) to understand detection
- See [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) if you encounter issues
- Use `advert-identifier-benchmark` to test accuracy
