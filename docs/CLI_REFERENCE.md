# CLI Reference

Complete documentation for all ad break identifier commands.

---

## advert-identifier

Identify individual adverts within video clips using vision-language models.

### Usage

```bash
advert-identifier --video URL --metadata-file PATH [OPTIONS]
```

### Arguments

| Argument | Short | Required | Default | Description |
|----------|-------|----------|---------|-------------|
| `--video` | `-v` | Yes | - | Video URL with frame/timecode overlay |
| `--metadata-file` | `-m` | Yes | - | JSON file with ad break metadata |
| `--mode` | | No | `timecode` | Analysis mode: `timecode` (MM:SS) or `frame` (frame count) |
| `--ensemble-size` | | No | 5 | Number of ensemble API calls |
| `--ensemble-delay` | | No | 10.0 | Seconds between ensemble requests |
| `--no-ensemble` | | No | False | Disable ensemble, single API call |
| `--output-format` | `-o` | No | `json` | Output format: `json` or `text` |
| `--debug` | | No | False | Save debug.json and debug.md |
| `--fps` | `-f` | No | 1.0 | Frame sampling rate for vLLM |
| `--api-base-url` | | No | env var | vLLM API endpoint |
| `--model` | | No | `Qwen/Qwen3.5-4B` | Model name to use |
| `--refine` | | No | False | Run frame refinement stage after detection |

### Examples

```bash
# Basic usage with ensemble voting (default)
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json

# Frame mode (outputs frame numbers instead of timecodes)
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json \
  --mode frame

# Fast single call without ensemble
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json \
  --no-ensemble

# Debug mode with detailed output
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json \
  --debug

# Text output instead of JSON
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json \
  --output-format text
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
| `--before-secs` | No | 60.0 | Seconds before ad break to include |
| `--after-secs` | No | 360.0 | Seconds after ad break to include |
| `--dry-run` | No | False | Preview what would be processed |
| `--log-level` | No | INFO | DEBUG, INFO, WARNING, ERROR |

### Examples

```bash
# Basic usage
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv

# Custom clip duration (30s before, 180s after)
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv \
  --before-secs 30.0 \
  --after-secs 180.0

# Dry run to preview without executing
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv \
  --dry-run

# Debug logging
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv \
  --log-level DEBUG
```

### Pipeline Workflow

For each video file, the pipeline:

1. **Metadata Extraction**: Finds matching CSV, converts to JSON
2. **Clip Extraction**: Extracts clips using FFmpeg
3. **Advert Identification**: Runs AI analysis with ensemble voting
4. **Results Collection**: Parses XML outputs
5. **Summary Report**: Creates `advert_identifier_pipeline_summary.txt`

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
| `--csv-folder` | No | `/path/to/csv/files` | Folder containing CSV files |
| `--output-dir` | No | Video directory | Where to save JSON metadata |

### CSV Matching

The extractor parses the video filename to find matching CSV data:

- **Video:** `2024-03-26_ITV1HD_13:30:00.mp4`
- **Extracts:** Date: `2024-03-26`, Channel: `ITV1HD`
- **Finds CSV:** `2024-03-26_BFIExport.csv` (date-based)
- **Filters:** Rows matching channel and time range

Channel matching is case-insensitive and ignores spaces.

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
```

### Output Format

Creates `{video_name}_metadata.json` with:

```json
{
  "video_info": {
    "filepath": "video/2024-03-26_ITV1HD_13:30:00.mp4",
    "date": "2024-03-26",
    "channel": "ITV1HD",
    "start_time": "13:30:00",
    "duration_seconds": 1629.12
  },
  "ad_breaks": [
    {
      "index": 0,
      "programme_before": {"title": "ITV LUNCHTIME NEWS", "channel": "ITV1 HD"},
      "programme_after": {"title": "ITV LUNCHTIME NEWS", "channel": "ITV1 HD"},
      "start_time": "13:52:05",
      "adverts": [
        {
          "unique_id": "BBHTCPT536010",
          "advertiser": "Tesco stores",
          "brand": "Tesco easter",
          "duration_seconds": 10
        }
      ]
    }
  ]
}
```

---

## advert-identifier-clip

Extract video clips centered on ad break timestamps.

### Usage

```bash
advert-identifier-clip --json-file PATH [OPTIONS]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--json-file` | Yes | - | Path to JSON metadata file |
| `--video-path` | No | From JSON | Override video path |
| `--before-secs` | No | 3.0 | Seconds before ad break to include |
| `--after-secs` | No | 1.0 | Seconds after ad break to include |
| `--overlay-type` | No | `none` | Clip overlay: `none`, `timecode`, or `frame` |
| `--clip-prefix` | No | `advert` | Prefix for output filenames |
| `--log-level` | No | INFO | DEBUG, INFO, WARNING, ERROR |

### Examples

```bash
# Basic usage - no overlay on clips
advert-identifier-clip \
  --json-file metadata/2024-03-26_ITV1HD_13:30:00_metadata.json

# With timecode overlay (HH:MM:SS.mmm)
advert-identifier-clip \
  --json-file metadata/ad_breaks.json \
  --overlay-type timecode

# With frame count overlay
advert-identifier-clip \
  --json-file metadata/ad_breaks.json \
  --overlay-type frame

# Custom clip duration (60s before, 360s after)
advert-identifier-clip \
  --json-file metadata/ad_breaks.json \
  --before-secs 60.0 \
  --after-secs 360.0

# Custom output prefix
advert-identifier-clip \
  --json-file metadata/ad_breaks.json \
  --clip-prefix advert
```

### Output

Creates clips named `{video_name}_{timecode}_CLIP.mp4` in the video directory.

---

## advert-identifier-refine

Refine advert boundaries to frame-accurate precision using 25 FPS (configurable) analysis.

### Usage

```bash
advert-identifier-refine --xml-file PATH --video-url URL [OPTIONS]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--xml-file` | Yes | - | Path to XML from primary detection |
| `--video-url` | Yes | - | URL to full video (for clip extraction) |
| `--json-file` | No | - | Original metadata JSON (for brand/advertiser/category enrichment) |
| `--output` | No | `{xml}_refined.xml` | Output XML path |
| `--api-base-url` | No | `http://localhost:8000/v1` | vLLM API endpoint |
| `--model` | No | `Qwen/Qwen3.5-4B` | Model name |
| `--ensemble-size` | No | 3 | Ensemble calls per advert |
| `--ensemble-delay` | No | 5.0 | Delay between ensemble requests |
| `--refine-fps` | No | 25.0 | FPS for refinement stage (25 for PAL, 24 for NTSC) |
| `--verbose` | No | False | Show detailed progress |
| `--debug` | No | False | Save debug_refine.json with raw responses |
| `--log-level` | No | INFO | DEBUG, INFO, WARNING, ERROR |

### How It Works

1. For each advert in the input XML, extracts a 3-second clip centered on the expected end timecode
2. Sends the clip to VLLM at 25 FPS (75 frames) with advert brand/advertiser/category context
3. Ensemble of 3 calls vote on the precise last frame (0-74 at 25fps)
4. Calculates refined `HH:MM:SS.mmm` timecode from clip start + (frame/fps)
5. Falls back to original timecode on failure

### Examples

```bash
# Basic usage
advert-identifier-refine \
  --xml-file results/video_clip.xml \
  --video-url "http://server/video.mp4"

# With metadata enrichment
advert-identifier-refine \
  --xml-file results/video_clip.xml \
  --video-url "http://server/video.mp4" \
  --json-file metadata/video_metadata.json

# Custom ensemble and FPS settings
advert-identifier-refine \
  --xml-file results/video_clip.xml \
  --video-url "http://server/video.mp4" \
  --json-file metadata/video_metadata.json \
  --ensemble-size 5 \
  --ensemble-delay 3.0 \
  --refine-fps 24.0

# Override FPS for NTSC video sources
advert-identifier-refine \
  --xml-file results/video_clip.xml \
  --video-url "http://server/video.mp4" \
  --refine-fps 24.0
```

### Output

Creates `{original}_refined.xml` with enhanced advert elements:

```xml
<advert>
  <unique_id>BBHTCPT536010</unique_id>
  <brand>Tesco</brand>
  <advertiser>Tesco stores</advertiser>
  <category>retail</category>
  <duration_seconds>20</duration_seconds>
  <last_timecode>09:30</last_timecode>           <!-- coarse 1-FPS -->
  <refined_timecode>09:31.416</refined_timecode>  <!-- precise 25-FPS -->
  <refined_clip_frame>43</refined_clip_frame>     <!-- 0-74 within clip at 25fps -->
  <refinement_status>success</refinement_status>
  <description>Brand visible in frames 40-43</description>
</advert>
```

---

## advert-identifier-benchmark

Run multiple detections and analyze accuracy against ground truth.

### Usage

```bash
advert-identifier-benchmark --video-url URL --metadata-file PATH [OPTIONS]
```

### Arguments

| Argument | Short | Required | Default | Description |
|----------|-------|----------|---------|-------------|
| `--video-url` | `-v` | Yes | - | Video URL to analyze |
| `--metadata-file` | `-m` | Yes | - | JSON metadata file |
| `--mode` | | No | `timecode` | Analysis mode: `timecode` or `frame` |
| `--ground-truth-first` | | No | - | Ground truth for first advert |
| `--num-runs` | `-n` | No | 10 | Number of benchmark runs |
| `--ensemble-size` | | No | 5 | Ensemble members per run |
| `--ensemble-delay` | | No | 10.0 | Delay between requests (seconds) |
| `--output-csv` | `-o` | No | auto | Output CSV file path |

### Examples

```bash
# Timecode mode with ground truth
advert-identifier-benchmark \
  --video-url "http://server/video.mp4" \
  --metadata-file metadata.json \
  --mode timecode \
  --ground-truth-first 01:56 \
  --num-runs 10

# Frame mode with ground truth
advert-identifier-benchmark \
  --video-url "http://server/video.mp4" \
  --metadata-file metadata.json \
  --mode frame \
  --ground-truth-first 116 \
  --num-runs 10

# Custom ensemble settings
advert-identifier-benchmark \
  --video-url "http://server/video.mp4" \
  --metadata-file metadata.json \
  --ground-truth-first 01:56 \
  --num-runs 20 \
  --ensemble-size 3 \
  --ensemble-delay 5
```

### Output

Creates CSV file with detailed results including:
- Accuracy statistics vs ground truth
- Mean/median/min/max differences
- Closest and furthest runs

---

## advert-identifier-describe

Generate natural language descriptions of video content.

### Usage

```bash
advert-identifier-describe [OPTIONS]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--video` | Yes | - | Path to video file |
| `--prompt` | No | Built-in | Custom description prompt |

### Examples

```bash
# Describe a video clip
advert-identifier-describe \
  --video /path/to/video.mp4

# Custom prompt
advert-identifier-describe \
  --video /path/to/video.mp4 \
  --prompt "What products are advertised in this video?"
```

---

## Environment Variables

All commands respect these environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_BASE_URL` | `http://localhost:8000/v1` | vLLM API endpoint |
| `API_KEY` | `EMPTY` | API authentication key |
| `MODEL_NAME` | `Qwen/Qwen3.5-4B` | Model to use |
| `FPS` | `1.0` | Frame sampling rate |
| `ENSEMBLE_SIZE` | `5` | Default ensemble size |
| `ENSEMBLE_DELAY` | `10.0` | Default ensemble delay |

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

Note: CSV files are date-based and contain data for multiple channels. The tools filter by channel and time range.
