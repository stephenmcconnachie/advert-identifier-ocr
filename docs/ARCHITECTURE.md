# Architecture

How the OCR-based ad break identifier system works.

---

## System Overview

The ad break identifier uses PaddleOCR-VL via vLLM to read text from video frames and match against brand/advertiser metadata.

### Key Components

1. **Metadata Extractor** — Parses CSV scheduling data into JSON + pipeline state
2. **Frame Extractor** — FFmpeg-based frame extraction at 5 FPS from the original video
3. **OCR Client** — Sends base64-encoded frames to PaddleOCR-VL via vLLM chat completions
4. **Two-Tier Brand Search** — Regex matching with exact word boundaries and substring fallback
5. **Pipeline State Manager** — Persistent JSON tracking across all stages

---

## Detection Strategy

### Overview

1. **Frame extraction**: FFmpeg extracts PNG frames at 5 FPS from a time range around each ad break
2. **OCR inference**: Each frame is base64-encoded and sent to PaddleOCR-VL via vLLM's OpenAI-compatible chat completions endpoint with an `"OCR:"` text prompt
3. **OCR results storage**: All per-frame OCR text is saved to a queryable JSON file (`{video_stem}_ocr.json`)
4. **Two-tier text matching**: For each advert in order, search OCR text using:
   - **Tier 1 (exact)**: Word-boundary regex (`\bgalaxy\b`) for precise matches
   - **Tier 2 (substring)**: Unbounded regex (`galaxy`) as fallback for concatenated forms like `galaxychocolate.com`
5. **Ordering enforcement**: Each advert's last matching frame must be after the previous advert's last matching frame
6. **Boundary detection**: The *last* frame where the brand text appears is the advert's end boundary

### Why 5 FPS?

- 5 FPS provides 200ms resolution — sufficient for advert boundaries (adverts are 10-60 seconds)
- A single pass at 5 FPS replaces the old two-stage (1 FPS coarse + 25 FPS refine) approach
- At 5 FPS, a 6-minute ad break produces ~1800 frames (vs ~360 at 1 FPS or ~9000 at 25 FPS)

### Why PaddleOCR-VL?

- Compact 0.9B parameter model with SOTA accuracy (96.33% on OmniDocBench v1.6)
- Handles text, formulas, tables, and complex layouts
- Deployed via vLLM with standard OpenAI-compatible API
- No prompt engineering needed — just sends `"OCR:"` text prompt with image

### Limitations

- Requires brand/advertiser text to be visible in the video (not all adverts show text)
- Short brand names (<3 chars) are excluded from multi-word matching
- Punctuation and OCR errors can reduce match accuracy

---

## Output Structure

### XML Output (Detection)

```xml
<ad_break>
    <!-- OCR-based detection (5 FPS, PaddleOCR-VL) -->
    <!-- Generated: 2024-03-26T14:05:00 -->
    <advert>
        <unique_id>BBHTCPT536010</unique_id>
        <brand>Tesco</brand>
        <advertiser>Tesco PLC</advertiser>
        <category>retail</category>
        <duration_seconds>10</duration_seconds>
        <last_timecode>02:15.000</last_timecode>
        <match_tier>exact</match_tier>
        <matched_terms>tesco</matched_terms>
    </advert>
    <advert>
        <unique_id>XYZ123</unique_id>
        <brand>Unknown Brand</brand>
        <advertiser>Unknown Co</advertiser>
        <category>misc</category>
        <last_timecode></last_timecode>
        <ocr_match_fallback>true</ocr_match_fallback>
        <description>OCR: no text match for Unknown Brand/Unknown Co</description>
    </advert>
</ad_break>
```

### OCR Results JSON (`{video_stem}_ocr.json`)

```json
{
  "video_url": "/path/to/video.mp4",
  "fps": 5.0,
  "start_seconds": 1285.0,
  "frame_count": 1850,
  "frames": [
    {
      "frame_index": 0,
      "frame_name": "frame_00001.png",
      "timestamp_clip": 0.0,
      "timestamp_broadcast": 1285.0,
      "text": "ITV News",
      "error": null
    },
    {
      "frame_index": 1,
      "frame_name": "frame_00002.png",
      "timestamp_clip": 0.2,
      "timestamp_broadcast": 1285.2,
      "text": "Tesco Easter Sale now on",
      "error": null
    }
  ]
}
```

---

## Pipeline Workflow

### Stage 1: Metadata Extraction

```
Video filename → Parse date/channel/time → Find CSV → Filter rows → Output JSON
                                                                     └→ Pipeline state file
```

- Extracts date, channel, time from filename pattern
- Searches for `YYYY-MM-DD_BFIExport.csv`
- Filters CSV rows by channel (case-insensitive) and time range
- Groups matching adverts into ad breaks
- Creates pipeline state file with per-break `clip_offset` values

### Stage 2: OCR Detection (5 FPS)

```
Original video + Metadata → FFmpeg 5 FPS frames → PaddleOCR-VL → Two-tier brand search → XML
         ↓                                                                        ↓
   Download to temp                                                        Pipeline state update
```

1. Load metadata JSON → get ad break start time and advert list
2. Download original video to temp file
3. Compute extraction range: `[break_start - before_secs, break_start + after_secs]`
4. Extract frames at 5 FPS via FFmpeg
5. OCR all frames via PaddleOCR-VL (base64 PNG + "OCR:" prompt)
6. Save OCR results to `{video_stem}_ocr.json`
7. For each advert in order:
   - Build exact word-boundary patterns (Tier 1)
   - Build substring patterns (Tier 2)
   - Search frames in range `(prev_last_frame, end]`
   - Try Tier 1 first; if no match, try Tier 2
   - Record last matching frame as the advert's end boundary
8. Output XML with `<last_timecode>` (clip-relative MM:SS.mmm)
9. Update pipeline state with `detection` data

### Stage 3: Advert Clip Extraction

Uses the detection XML to extract lossless H.264 clips of each individual advert from the original broadcast video.

The `adjusted_start_broadcast` value from the pipeline state file (or computed from `last_timecode + clip_offset - duration`) determines the FFmpeg seek offset.

---

## Pipeline State File

The pipeline state file (`{video}_pipeline_state.json`) is the system of record
for advert progression through all stages. It is created at Stage 1 and updated
by each subsequent stage.

### State Machine

```
metadata_extracted → detected → clipped
```

### Adjusted Start Computation

When the detection stage writes `detection` data, the state manager
automatically computes:

```
last_seconds_broadcast    = clip_offset + last_seconds_clip
adjusted_start_broadcast  = last_seconds_broadcast - scheduled_duration_seconds
```

These broadcast-absolute values are what `advert-identifier-single-advert-clip`
uses for its FFmpeg seek offset when `--state-file` is provided.

### State File Format

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
          "advertiser": "Tesco PLC",
          "category": "retail",
          "scheduled_duration_seconds": 10,
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

---

## Coordinate Systems

The pipeline operates in three coordinate frames:

| Frame | Symbol | Origin | Used by |
|-------|--------|--------|---------|
| **Broadcast-absolute** | `t_bcast` | Start of full broadcast `.mp4` | FFmpeg `-ss` seeks |
| **Clip-relative** | `t_clip` | Start of the extracted frame range | OCR detection output |
| **Time-of-day** | `t_tod` | Wall clock HH:MM:SS | CSV metadata |

The pipeline state file bridges these frames using `clip_offset = (break_start_tod - video_start_tod) - before_secs`.

See [coordinate-systems.md](coordinate-systems.md) for the complete reference.

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

## vLLM Server Configuration

### PaddleOCR-VL Deployment

```bash
vllm serve PaddlePaddle/PaddleOCR-VL \
    --trust-remote-code \
    --max-num-batched-tokens 16384 \
    --no-enable-prefix-caching \
    --mm-processor-cache-gb 0
```

### OCR Client Settings

- Endpoint: `http://localhost:8000/v1/chat/completions` (configurable via `OCR_ENDPOINT`)
- Model: `PaddlePaddle/PaddleOCR-VL` (configurable via `OCR_MODEL`)
- Prompt: `"OCR:"` (text content alongside image)
- Temperature: `0.0` (deterministic)
- Encoding: base64 PNG via `requests` HTTP POST
