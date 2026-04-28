# Ad Break Identifier

AI-powered advert identification in TV broadcast videos using vision-language models.

## Quick Start

Four commands to process a broadcast video:

```bash
# 1. Extract metadata from CSV scheduling data
advert-identifier-metadata-extract \
  --video video/2024-03-26_ITV1HD_13:30:00.mp4 \
  --output-dir video/

# 2. Extract video clips centered on ad break timestamps
advert-identifier-clip \
  --json-file video/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --before-secs 60 \
  --after-secs 360

# 3. Identify individual adverts using AI (1 FPS detection)
advert-identifier \
  --video "http://your-server:8000/video/2024-03-26_ITV1HD_13:30:00_13-52-05.000_CLIP.mp4" \
  --metadata-file video/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --ad-break-index 0

# 4. (Optional) Refine to frame-accurate boundaries using 25 FPS analysis
advert-identifier-refine \
  --xml-file video/2024-03-26_ITV1HD_13-52-05.000_CLIP.xml \
  --video-url "http://your-server:8000/video/2024-03-26_ITV1HD_13:30:00_01of01.mp4" \
  --json-file video/2024-03-26_ITV1HD_13:30:00_metadata.json

# 5. Extract individual advert clips (lossless)
advert-identifier-single-advert-clip \
  --xml-file video/2024-03-26_ITV1HD_13-52-05.000_CLIP_refined.xml \
  --json-file video/2024-03-26_ITV1HD_13:30:00_metadata.json \
  --video-url "http://your-server:8000/video/2024-03-26_ITV1HD_13:30:00_01of01.mp4" \
  --output-dir video/clips
```

## One-Command Alternative (NOT WORKING YET - DEBUGGING REQUIRED)

Process entire folders automatically:

```bash
advert-identifier-pipeline \
  --input-folder /path/to/videos \
  --csv-folder /path/to/csv/files \
  --before-secs 60 \
  --after-secs 360 \
  --video-server-url http://your-server:1100
```

## Installation

### Option 1: Using uv (Recommended)

```bash
# Clone the repository
cd /path/to/advert-identifier

# Install in editable mode with uv
uv pip install -e .

# Commands are now available in your PATH
advert-identifier --help
```

### Option 2: Using pip

```bash
# Clone the repository
cd /path/to/advert-identifier

# Install in editable mode
pip install -e .

# Commands are now available in your PATH
advert-identifier --help
```

### Option 3: Direct Execution (No Installation)

If you don't want to install the package, you can run scripts directly:

```bash
cd /path/to/advert-identifier

# Run any command directly
python3 bin/advert-identifier --help
python3 bin/advert-identifier-metadata-extract --help
```

## Commands Reference

| Command | Purpose | Key Options |
|---------|---------|-------------|
| `advert-identifier` | Identify adverts in clips | `--mode timecode\|frame`, `--ad-break-index`, `--verbose`, `--debug`, `--refine` |
| `advert-identifier-pipeline` | Full folder automation | `--input-folder`, `--csv-folder` |
| `advert-identifier-metadata-extract` | CSV → JSON metadata | `--video`, `--csv-folder` |
| `advert-identifier-clip` | Extract video clips | `--before-secs`, `--after-secs` |
| `advert-identifier-single-advert-clip` | Extract lossless advert clips from XML | `--xml-file`, `--video-url`, `--index`, `--trim`, `--pad` |
| `advert-identifier-refine` | Frame-accurate boundary refinement | `--xml-file`, `--video-url`, `--json-file` |
| `advert-identifier-benchmark` | Test accuracy vs ground truth | `--ground-truth-first`, `-n 10` |
| `advert-identifier-describe` | Generate video descriptions | `--video`, `--prompt` |

See [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) for complete documentation.

## How It Works

1. **Metadata Extraction**: Parses video filenames to find matching CSV scheduling data
2. **Clip Extraction**: Uses FFmpeg to extract clips centered on ad break timestamps
3. **AI Analysis**: Vision-language model identifies last frame of each advert
4. **Ensemble Voting**: Multiple API calls with median voting for accuracy
5. **Advert Clip Extraction**: Extracts individual advert clips using last timecode and duration

## Example Output

```json
{
  "success": true,
  "adverts": [
    {
      "timecode": "13:52:05",
      "advert_id": "BBHTCPT536010",
      "brand": "Tesco",
      "duration_seconds": 10,
      "description": "Tesco logo visible in final frame"
    }
  ],
  "total_found": 1,
  "total_expected": 1
}
```

### Multiple Ad Breaks

When your metadata JSON contains multiple ad breaks, use `--ad-break-index` to select which one to process:

```bash
# Process the first ad break (index 0, default)
advert-identifier -v video.mp4 --metadata-file meta.json --ad-break-index 0

# Process the second ad break (index 1)
advert-identifier -v video.mp4 --metadata-file meta.json --ad-break-index 1
```

### Debugging with Verbose Mode

If the tool appears to hang or produces no output, use `--verbose` to see detailed progress:

```bash
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json \
  --verbose
```

This shows real-time progress through each stage:
- Step 1/5: Loading metadata
- Step 2/5: Building prompt  
- Step 3/5: Initializing API client
- Step 4/5: Making API calls

For even more detail, combine with `--debug` to save raw API responses to `debug.json`.

## Requirements

- Python 3.10+
- vLLM server with vision-language model (Qwen3.5 recommended)
- FFmpeg (for video clip extraction)
- TV broadcast videos with timecode or frame overlays

## Documentation

- [CLI Reference](docs/CLI_REFERENCE.md) - Complete command documentation
- [Architecture](docs/ARCHITECTURE.md) - How the detection works
- [Troubleshooting](docs/TROUBLESHOOTING.md) - Common errors and solutions

## File Naming Conventions

**Videos:** `YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4`  
**CSV Data:** `YYYY-MM-DD_BFIExport.csv` (multiple channels per file)

## License

MIT License - See LICENSE file for details.
