# Architecture

How the ad break identifier system works.

---

## System Overview

The ad break identifier uses a vision-language model (VLM) to analyze TV broadcast videos and identify the last frame of each advertisement in an ad break sequence.

### Key Components

1. **Metadata Extractor** - Parses CSV scheduling data
2. **Video Clipper** - Extracts clips using FFmpeg
3. **AI Analyzer** - Vision-language model for advert detection
4. **Ensemble Voter** - Combines multiple API responses
5. **Frame Refiner** - High-precision boundary detection using 25 FPS analysis

---

## Detection Strategy

### What the Model Analyzes

The VLM receives:
1. **Video stream** served via HTTP (sampled at 1 FPS)
2. **Metadata context** including:
   - Programme before and after the ad break
   - List of adverts with brands, durations, and categories

### How Detection Works

1. **Frame-by-frame analysis**: Reviews all frames in the video clip
2. **Brand logo detection**: Looks for brand names/logos in final frames of each advert
3. **Duration-based sequencing**: Uses provided durations to identify advert boundaries
4. **Last frame identification**: Reports the frame/timecode where each advert ends

The model focuses on finding where adverts end (brand logos typically appear in the last 2-3 seconds), not where they begin.

---

## Analysis Modes

### Timecode Mode (Default)

Uses MM:SS elapsed time format.

**Output format:** `"09:30"` (minutes:seconds)  
**XML element:** `<last_timecode>`  
**Ensemble voting:** Median timecode calculation

Use when video displays elapsed time overlay (MM:SS).

### Frame Mode

Uses 0-based frame numbers (integers).

**Output format:** `570` (frame count)  
**XML element:** `<last_frame>`  
**Ensemble voting:** Median frame calculation

Use when video displays frame numbers (integers starting from 0).

---

## Ensemble Voting

With ensemble enabled (default: 5 calls):

1. **Multiple API calls** with identical video and prompt
2. **Independent parsing** of each response
3. **Voting algorithms**:
   - **Timecodes/Frames**: Median value from all valid responses
   - **Brands/IDs**: Majority voting
   - **Descriptions**: First valid description

4. **Outlier rejection**: Invalid responses are filtered and reported

### Why Ensemble?

- Reduces impact of model hallucinations
- Handles occasional API timeouts
- Provides confidence metrics
- Improves accuracy for ambiguous content

---

## Output Structure

### JSON Output (Timecode Mode)

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
  "total_expected": 1,
  "ensemble": {
    "total_responses": 5,
    "valid_responses": 5,
    "voting_method": "median_timecode_majority_brand"
  }
}
```

### XML Output

```xml
<ad_break>
  <advert>
    <unique_id>BBHTCPT536010</unique_id>
    <brand>Tesco</brand>
    <duration_seconds>10</duration_seconds>
    <last_timecode>13:52:05</last_timecode>
    <description>Tesco logo visible in final frame</description>
  </advert>
</ad_break>
```

---

## Pipeline Workflow

### Stage 1: Metadata Extraction

```
Video filename → Parse date/channel/time → Find CSV → Filter rows → Output JSON
```

- Extracts date, channel, time from filename pattern
- Searches for `YYYY-MM-DD_BFIExport.csv`
- Filters CSV rows by channel (case-insensitive) and time range
- Groups matching adverts into ad breaks

### Stage 2: Video Clipping

```
JSON metadata → Extract timestamps → FFmpeg → Video clips
```

- Reads ad break start times from JSON
- Calculates clip boundaries (before/after seconds)
- Runs FFmpeg to extract clips
- Optional: adds timecode or frame overlay

### Stage 3: Advert Identification

```
Video clip + Metadata → vLLM API → Parse XML → Vote → Results
```

- Sends video to vLLM with metadata context
- Receives XML response for each advert
- Ensemble voting combines multiple responses
- Outputs final JSON/XML results

### Stage 4: Frame Refinement (Optional)

For frame-accurate advert boundaries, the refinement stage runs after primary detection:

```
Coarse XML + Video URL → FFmpeg clip → 25 FPS VLLM → Ensemble vote → Refined XML
```

**Process per advert:**
1. Extract 3-second clip centered on coarse timecode (1.5s before/after)
2. Send clip to VLLM at 25 FPS (75 frames) with brand/advertiser/category context
3. Ensemble of 3 calls vote on precise last frame (0-74 within clip at 25fps)
4. Calculate `refined_timecode = floor(clip_start + (frame / fps))` snapped to nearest frame boundary
5. Millisecond value is always a clean multiple of `1/fps` (e.g., `.000`, `.040`, `.080` at 25 FPS)
6. Floor semantics ensure timecode never advances into the next advert
7. Fall back to coarse timecode on failure

**FPS Configuration:**
- Default: 25 FPS (PAL video sources)
- Override with `--refine-fps` flag (e.g., 24 FPS for NTSC)

**Refinement output:**
```xml
<advert>
  <unique_id>BBHTCPT536010</unique_id>
  <brand>Tesco</brand>
  <advertiser>Tesco stores</advertiser>
  <category>retail</category>
  <duration_seconds>20</duration_seconds>
  <last_timecode>09:30</last_timecode>           <!-- coarse 1-FPS -->
  <refined_timecode>09:31.400</refined_timecode>  <!-- precise 25-FPS, floor-snapped -->
  <refined_clip_frame>43</refined_clip_frame>     <!-- 0-74 within clip at 25fps -->
  <refinement_status>success</refinement_status>
  <description>Brand visible in frames 40-43</description>
</advert>
```

**Comparison with Primary Detection:**

| Aspect | Primary Detection | Refinement |
|--------|-------------------|------------|
| FPS | 1 FPS | 25 FPS (configurable) |
| Clip duration | Full ad break | 3 seconds per advert |
| Ensemble size | 5 calls | 3 calls |
| Context | Full advert sequence | Single advert with brand info |
| Output | MM:SS timecode | HH:MM:SS.mmm (floor-snapped to 1/fps) |

---

## Metadata JSON Format

### Structure

```json
{
  "video_info": {
    "filepath": "video/2024-03-26_ITV1HD_13:30:00.mp4",
    "date": "2024-03-26",
    "channel": "ITV1HD",
    "start_time": "13:30:00",
    "duration_seconds": 1629.12,
    "end_time": "13:57:09"
  },
  "ad_breaks": [
    {
      "index": 0,
      "is_partial": false,
      "programme_before": {
        "title": "ITV LUNCHTIME NEWS",
        "channel": "ITV1 HD"
      },
      "programme_after": {
        "title": "ITV LUNCHTIME NEWS",
        "channel": "ITV1 HD"
      },
      "start_time": "13:52:05",
      "end_time": "13:55:35",
      "adverts": [
        {
          "unique_id": "BBHTCPT536010",
          "advertiser": "Tesco stores",
          "brand": "Tesco easter",
          "category": "Food & drink",
          "duration_seconds": 10,
          "start_time": "13:52:05",
          "position_in_break": "First"
        }
      ]
    }
  ],
  "summary": {
    "total_ad_breaks": 1,
    "partial_breaks": 0,
    "total_adverts": 7
  }
}
```

### Field Descriptions

**video_info**
- `filepath`: Path to source video
- `date`: Broadcast date (YYYY-MM-DD)
- `channel`: Channel name
- `start_time`: Video start time (HH:MM:SS)
- `duration_seconds`: Video length in seconds

**ad_breaks**
- `index`: Position in sequence
- `is_partial`: True if ad break starts/ends outside video
- `programme_before/after`: Sandwiching programmes
- `start_time/end_time`: Ad break boundaries
- `adverts`: List of adverts in sequence order

**adverts**
- `unique_id`: Unique identifier from CSV
- `advertiser`: Company name
- `brand`: Brand as appears on screen
- `category`: Advert category
- `duration_seconds`: Length (10, 20, 30, or 60)
- `position_in_break`: First, 2nd, 3rd, Middle, 3rd last, 2nd last, Last

---

## Model Configuration

### vLLM Settings

**Video Sampling:**
- Frame rate: 1 FPS (configurable)
- Resolution: 448x256 (for performance)
- Max frames: 64 for 4B model, 30 for longer videos

**Model Parameters:**
- Temperature: 1.0
- Top-p: 0.8
- Top-k: 20
- Max tokens: 10000
- Enable thinking: true (for quality)

### Recommended Models

| Model | Use Case | Memory | Speed |
|-------|----------|--------|-------|
| Qwen3.5-4B | 60s videos, 1 FPS | ~70% | Fast |
| Qwen3.5-9B | Medium videos, quality | ~90% | Medium |
| Qwen3.5-27B-FP8 | Text-only, throughput | ~50% | Very Fast |

---

## Debug Mode

When using `--debug`, the system saves:

### debug.json
- Complete API responses
- Parsed results per response
- Voting breakdown
- Ensemble statistics

### debug.md
- Human-readable analysis
- Per-response details
- Voting calculation steps
- Outlier identification

Use debug mode to understand why the model detected specific frames or to tune ensemble settings.
