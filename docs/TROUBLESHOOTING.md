# Troubleshooting

Common errors and solutions for the ad break identifier system.

---

## Installation Issues

### `pip install -e .` fails

**Symptoms:**
```
ERROR: Could not find a version that satisfies the requirement...
```

**Solutions:**
1. Ensure Python 3.10+ is installed: `python3 --version`
2. Update pip: `pip install --upgrade pip`
3. Install dependencies manually: `pip install openai>=1.0.0`
4. Check requirements.txt exists and is readable

### Commands not found after installation

**Symptoms:**
```
bash: advert-identifier: command not found
```

**Solutions:**
1. Ensure you're in the virtual environment: `which python3`
2. Reinstall: `pip install -e . --force-reinstall`
3. Check PATH includes pip bin directory: `echo $PATH`
4. Use full path: `python3 -m ad_break_identifier.cli`

---

## Metadata Extraction Issues

### "No matching CSV file found"

**Symptoms:**
```
ERROR: No CSV file found matching 2024-03-26
```

**Causes:**
- CSV filename doesn't match date pattern
- CSV is in wrong directory
- Video filename missing date

**Solutions:**
1. Verify CSV naming: `YYYY-MM-DD_BFIExport.csv`
2. Check CSV folder path: `--csv-folder /correct/path`
3. Verify video filename has date: `2024-03-26_ITV1HD_13:30:00.mp4`
4. List available CSVs: `ls -la /csv/folder/*.csv`

### "No data found for channel"

**Symptoms:**
```
WARNING: No matching rows found for channel 'ITV1HD'
```

**Causes:**
- Channel name mismatch between video and CSV
- CSV uses different naming (e.g., "ITV1 HD" vs "ITV1HD")

**Solutions:**
1. Check CSV channel column for exact spelling
2. Channel matching is case-insensitive and space-tolerant
3. Try partial match: video "ITV1HD" matches CSV "ITV1 HD"

### "Failed to detect video duration"

**Symptoms:**
```
ERROR: Could not detect video duration using ffprobe
```

**Causes:**
- Video file corrupted or unreadable
- ffprobe not installed or not in PATH
- Video in unsupported format

**Solutions:**
1. Verify ffprobe: `ffprobe -version` or `$(brew --prefix)/bin/ffprobe -version` (if using Homebrew)
2. Check video plays: `ffplay video.mp4`
3. Verify file permissions: `ls -la video.mp4`
4. Re-encode if corrupted: `ffmpeg -i input.mp4 -c copy output.mp4`

---

## Video Clip Extraction Issues

### "ffmpeg not found"

**Symptoms:**
```
FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'
```

**Solutions:**
1. Install FFmpeg: `brew install ffmpeg` (macOS/Linux)
2. Use brew path in scripts: `$(brew --prefix)/bin/ffmpeg`
3. Create symlink: `ln -s $(brew --prefix)/bin/ffmpeg /usr/local/bin/ffmpeg`

### "Clip duration exceeds video length"

**Symptoms:**
```
WARNING: Clip end (14:00:00) exceeds video end (13:57:09)
```

**Causes:**
- `--after-secs` too large for remaining video
- Ad break near end of video file

**Solutions:**
1. Reduce `--after-secs` value
2. Check video end time in metadata
3. Accept partial clips (may miss some content)

---

## Advert Identification Issues

### "No ad break metadata provided"

**Symptoms:**
```
ERROR: No ad break metadata provided
```

**Causes:**
- Missing `--metadata-file` argument
- JSON file doesn't exist
- Wrong path to metadata file

**Solutions:**
1. Provide metadata file: `--metadata-file path/to/file.json`
2. Check file exists: `ls -la metadata.json`
3. Verify JSON format (see docs/ARCHITECTURE.md)
4. Extract metadata first using `advert-identifier-metadata-extract`

### "No <ad_break> XML found in response"

**Symptoms:**
```
ERROR: No <ad_break> XML found in response
```

**Causes:**
- Model didn't return expected XML format
- Video URL not accessible
- Model timeout or error

**Solutions:**
1. Verify video URL is accessible: `curl -I "http://server/video.mp4"`
2. Check frame/timecode overlay is visible in video
3. Try increasing FPS: `--fps 2.0`
4. Enable debug mode to see raw response: `--debug`
5. Check vLLM server is running: `curl http://server:8000/v1/models`

### "Invalid timecode format" or "Invalid frame format"

**Symptoms:**
```
ERROR: Invalid timecode format: '...'
```

**Causes:**
- Model returned placeholder values (e.g., "...")
- XML parsing found example data in thinking section
- Model didn't analyze actual video

**Solutions:**
1. Update to latest version (XML extraction improved)
2. Verify video content matches metadata
3. Check brand names in metadata match video
4. Try debug mode to see raw responses
5. Consider frame mode if timecode overlay unreliable: `--mode frame`

### "Invalid duration: X. Must be 10, 20, 30, or 60"

**Symptoms:**
```
ERROR: Invalid duration: 45. Must be 10, 20, 30, or 60.
```

**Causes:**
- CSV data has unusual advert durations
- Duration parsing error

**Solutions:**
1. Check CSV for correct duration values
2. Fix in source data if possible
3. Round to nearest standard: 10, 20, 30, or 60 seconds
4. Consider partial break detection for non-standard durations

---

## API Connection Issues

### "ConnectionError: Max retries exceeded"

**Symptoms:**
```
ConnectionError: HTTPConnectionPool: Max retries exceeded
```

**Causes:**
- vLLM server not running
- Wrong API endpoint
- Network connectivity issues

**Solutions:**
1. Check server status: `curl http://localhost:8000/v1/models`
2. Verify API_BASE_URL environment variable
3. Check firewall/network settings
4. Confirm server has GPU resources available

### "API timeout" or requests hang

**Symptoms:**
```
TimeoutError: Request timed out after 120 seconds
```

**Causes:**
- Video too long or high resolution
- Model overloaded
- Network latency

**Solutions:**
1. Reduce video length or resolution
2. Use smaller ensemble: `--ensemble-size 3`
3. Check GPU memory: `nvidia-smi`
4. Reduce FPS: `--fps 0.5`

### "Model not found" error

**Symptoms:**
```
Error: Model 'Qwen/Qwen3.5-4B' not found
```

**Solutions:**
1. Check available models: `curl http://server:8000/v1/models`
2. Use correct model name from list
3. Verify model is downloaded on server
4. Set MODEL_NAME environment variable correctly

---

## Performance Issues

### Ensemble taking too long

**Symptoms:**
Processing takes 5+ minutes per video

**Solutions:**
1. Reduce ensemble size: `--ensemble-size 3`
2. Reduce delay: `--ensemble-delay 5.0`
3. Disable ensemble: `--no-ensemble`
4. Check vLLM server load and GPU utilization

### High memory usage

**Symptoms:**
- System slows down
- Out of memory errors
- GPU memory exhausted

**Solutions:**
1. Process fewer videos in parallel
2. Use text-only models where possible
3. Reduce ensemble size
4. Check for memory leaks: `watch -n 1 nvidia-smi`
5. Restart vLLM server periodically

---

## Accuracy Issues

### Low match rate (found < expected adverts)

**Symptoms:**
```
Total: 3/7 adverts found
```

**Causes:**
- Brand names in metadata don't match video
- Poor video quality or overlay visibility
- Incorrect durations in metadata
- Adverts actually missing from video

**Solutions:**
1. Verify brand names match on-screen text
2. Check durations are accurate (10, 20, 30, 60 seconds)
3. Ensure video quality is sufficient (1080p+ recommended)
4. Verify timecode/frame overlay is clearly visible
5. Try debug mode to see what model detected
6. Adjust overlay position if partially obscured

### Inconsistent results between runs

**Symptoms:**
Different frame numbers each time for same video

**Causes:**
- Model randomness (temperature > 0)
- Video URL accessibility issues
- Model version changes

**Solutions:**
1. Use larger ensemble: `--ensemble-size 7` or `10`
2. Set consistent seed if model supports it
3. Verify video file hasn't changed
4. Use benchmark tool to measure variance: `advert-identifier-benchmark`

---

## Debug Mode

When troubleshooting, always use `--debug` flag:

```bash
advert-identifier \
  --video "http://server/video.mp4" \
  --metadata-file metadata.json \
  --debug
```

This creates:
- `debug.json` - Raw API responses and parsing details
- `debug.md` - Human-readable analysis

Check these files to understand:
- What the model actually saw
- Why specific frames were chosen
- Which ensemble responses were outliers

---

## Getting Help

If issues persist:

1. Check logs: `/path/to/logs/`
2. Review docs/ARCHITECTURE.md for technical details
3. Check vLLM server logs on host machine
4. Verify file permissions and paths
5. Test with sample data from examples/ directory
