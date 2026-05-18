# Architecture

How the ad break identifier system works.

---

## System Overview

The ad break identifier supports two approaches for advert boundary detection:

- **VLM-based** (default): Uses a vision-language model to analyze video streams
- **OCR-based** (experimental): Uses OCR models to read text from extracted frames and match against metadata

### Key Components (VLM)

1. **Metadata Extractor** — Parses CSV scheduling data
2. **Video Clipper** — Extracts clips using FFmpeg
3. **AI Analyzer** — Vision-language model for advert detection
4. **Ensemble Voter** — Combines multiple API responses
5. **Frame Refiner** — High-precision boundary detection using 25 FPS analysis
6. **Pipeline State Manager** — Persistent JSON tracking across all stages

### Key Components (OCR)

1. **Metadata Extractor** — Same as VLM pipeline
2. **Video Clipper** — Same as VLM pipeline
3. **Frame Extractor** — FFmpeg-based frame extraction at 1 FPS and 25 FPS
4. **OCR Client** — vLLM chat completions with base64-encoded images
5. **Text Matcher** — Regex-based matching of OCR output against brand/advertiser metadata
6. **OCR Scanner (Stage 1)** — 1 FPS frame-by-frame OCR scan for coarse boundaries
7. **OCR Refiner (Stage 2)** — 25 FPS frame-by-frame OCR scan for precise boundaries
8. **Pipeline State Manager** — Same as VLM pipeline

---

## Detection Strategy

### What the Model Analyzes

The VLM receives:
1. **Video stream** served via HTTP (sampled at 1 FPS)
2. **Metadata context** including:
   - Programme before and after the ad break
   - List of adverts with brands, durations, and categories

### OCR Detection Strategy

The OCR approach replaces the VLM with frame-by-frame text extraction and matching:

1. **Frame extraction**: FFmpeg extracts PNG frames at the required FPS (1 or 25)
2. **OCR inference**: Each frame is base64-encoded and sent to a vLLM-hosted OCR model via OpenAI-compatible chat completions
3. **Text matching**: The OCR output text is regex-matched against each advert's brand, advertiser, and category fields
4. **Boundary detection**: The *last* frame where the brand/advertiser text appears is the boundary
5. **Advert ordering**: Enforced — advert N's last match must come before advert N+1's first match

### Why OCR?

- OCR models are much smaller than VLMs (e.g., LightOnOCR-2-1B at 2.1B params vs Qwen3.5-4B at 4B)
- Direct text matching eliminates hallucination about frame content
- No need for prompt engineering — the model just reads text
- Each frame is an independent API call (can be parallelised or batched)
- Grid images (multiple frames in one image) can be used for efficiency

### Limitations

- Requires brand/advertiser text to be visible in the video (not all adverts show text)
- Short brand names (<3 chars) are excluded from multi-word matching
- Punctuation and OCR errors can reduce match accuracy
- Each frame = one API call (75 calls per advert at 25 FPS for 3s)
- No ensemble voting (each frame is deterministic)

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

### MAD-Based Outlier Filter (Optional)

When `--ensemble-filter mad` is enabled, the system applies Median Absolute Deviation
rejection before the final median vote:

1. Compute median of all valid votes
2. Compute absolute deviations from the median
3. Compute MAD (median of absolute deviations)
4. Discard votes where deviation > `threshold × MAD` (default threshold: 3.0)
5. Compute median of remaining votes

This filters out sporadic model outputs while keeping the ensemble's consensus.

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

### XML Output (1 FPS — Primary Detection)

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

### XML Output (25 FPS — Refined)

```xml
<ad_break>
  <advert>
    <unique_id>BBHTCPT536010</unique_id>
    <brand>Tesco</brand>
    <advertiser>Tesco stores</advertiser>
    <category>retail</category>
    <duration_seconds>20</duration_seconds>
    <last_timecode>09:30</last_timecode>             <!-- coarse 1-FPS -->
    <refined_timecode>00:09:31.400</refined_timecode> <!-- precise 25-FPS -->
    <refined_clip_frame>43</refined_clip_frame>       <!-- 0-74 within 3s clip -->
    <refinement_status>success</refinement_status>
    <description>Brand visible in frames 40-43</description>
  </advert>
</ad_break>
```

---

## Pipeline Workflow

### VLM Pipeline

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

### Stage 2: Video Clipping

```
JSON metadata → Extract timestamps → FFmpeg → Video clips
```

- Reads ad break start times from JSON
- Calculates clip boundaries (before/after seconds)
- Runs FFmpeg to extract clips
- Optional: adds timecode or frame overlay

### Stage 3: Advert Identification (1 FPS)

```
Video clip + Metadata → vLLM API → Parse XML → Vote → XML results
                                                        └→ Pipeline state update (coarse_1fps)
```

- Sends video to vLLM with metadata context
- Receives XML response for each advert
- Ensemble voting combines multiple responses
- Outputs XML results
- Updates pipeline state with `coarse_1fps` data

### Stage 4: Frame Refinement (25 FPS)

The pipeline automatically runs frame-accurate refinement after 1 FPS detection:

```
Coarse XML + Clip URL → FFmpeg clip → 25 FPS VLLM → Ensemble vote → Refined XML
                                                                    └→ Pipeline state update (refined_25fps)
```

**Process per advert:**
1. Extract 3-second clip centered on coarse timecode (1.5s before/after)
2. Send clip to VLLM at 25 FPS (75 frames) with brand/advertiser/category context
3. Ensemble of 3 calls vote on precise last frame (0-74 within clip at 25fps)
4. Compute refined HH:MM:SS.mmm timecode, floor-snapped to 1/fps boundary
5. Updates pipeline state with `refined_25fps` including `adjusted_start_broadcast`

**FPS Configuration:**
- Default: 25 FPS (PAL video sources)
- Override with `--refine-fps` flag (e.g., 24 FPS for NTSC)

**Comparison with Primary Detection:**

| Aspect | Primary Detection | Refinement |
|--------|-------------------|------------|
| FPS | 1 FPS | 25 FPS (configurable) |
| Clip duration | Full ad break | 3 seconds per advert |
| Ensemble size | 5 calls | 3 calls |
| Context | Full advert sequence | Single advert with brand info |
| Output | MM:SS timecode | HH:MM:SS.mmm (floor-snapped to 1/fps) |

### Stage 5: Advert Clip Extraction

Uses the refined XML (or coarse XML if refinement unavailable) to extract
lossless H.264 clips of each individual advert from the full broadcast video.

The `adjusted_start_broadcast` value from the pipeline state file (or computed
from `refined_timecode + clip_offset - duration`) determines the FFmpeg seek offset.

### OCR Pipeline

The OCR pipeline reuses Stages 1 (metadata), 2 (video clipping), and 5 (advert clip extraction)
from the VLM pipeline. Only stages 3 and 4 are replaced:

#### Stage 3 (OCR): Advert Identification (1 FPS)

```                                                 
Metadata JSON → Compute advert windows → FFmpeg 1 FPS → OCR all frames → Regex match per advert → XML
                                ↓                                                        ↓
                          Pipeline state                                         Pipeline state update
```

1. Load metadata JSON → compute per-advert scan windows from scheduled durations
2. Extract frames at 1 FPS across the full clip via FFmpeg
3. Send each frame to OCR model via vLLM chat completions (base64 PNG)
4. For each advert (in order):
   - Build regex patterns from brand + advertiser + category
   - Scan OCR results within the advert's window
   - Find the *last* frame where text matches
5. Enforce ordering: advert N's last match < advert N+1's last match
6. Output XML with same `<ad_break>`/`<advert>`/`<last_timecode>` schema
7. Update pipeline state with `coarse_1fps` data

#### Stage 4 (OCR): Frame Refinement (25 FPS)

```                                                    
Coarse XML + Metadata → For each advert: → FFmpeg 3s/25 FPS → OCR frames → Regex match → Refined XML
                                                                                         ↓
                                                                                  Pipeline state update
```

Per-advert process:

1. Read coarse timecode from 1 FPS XML
2. Extract 3-second clip at 25 FPS centred on coarse timecode
3. OCR each frame (up to 75 frames)
4. Find the last frame matching brand/advertiser text
5. Compute refined timecode: `window_start + (last_match_index / 25)`
6. Output refined XML with `<refined_timecode>` and `<refined_clip_frame>`
7. Update pipeline state with `refined_25fps` data

#### Comparison: VLM vs OCR

| Aspect | VLM Pipeline | OCR Pipeline |
|--------|-------------|--------------|
| Stage 3 model | Qwen3.5-4B (VLM) | LightOnOCR-2-1B (OCR) |
| Stage 4 model | Qwen3.5-4B (VLM) | LightOnOCR-2-1B (OCR) |
| Stage 3 input | Video URL (1 FPS) | 1 FPS PNG frames |
| Stage 4 input | 3s video clip (25 FPS) | 25 FPS PNG frames |
| Boundary detection | VLM analyses frames visually | Regex matches OCR text against metadata |
| Ensemble voting | Yes (5 calls Stage 3, 3 calls Stage 4) | No (deterministic per frame) |
| Prompt engineering | Required (complex ad_break prompt) | None needed |
| Hallucination risk | Moderate | Low (direct text matching) |
| API calls per advert | 5 (Stage 3) + 3 (Stage 4) = 8 | ~frames_in_clip (180–480 for Stage 3, 75 for Stage 4) |
| Fallback behaviour | Uses original timecode from CSV | `<ocr_match_fallback>true</ocr_match_fallback>` |
| Output XML schema | Identical | Identical (+ fallback element) |
| Pipeline state updates | Identical | Identical |

---

## Pipeline State File

The pipeline state file (`{video}_pipeline_state.json`) is the system of record
for advert progression through all stages. It is created at Stage 1 and updated
by each subsequent stage.

### State Machine

```
metadata_extracted → clip_extracted → identified → refined → clipped
```

### Adjusted Start Computation

When the refinement stage writes `refined_25fps` data, the state manager
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
  "pipeline_version": 1,
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
          "advertiser": "Tesco stores",
          "category": "retail",
          "scheduled_duration_seconds": 10,
          "status": "refined",
          "coarse_1fps": {
            "last_timecode": "22:05",
            "last_seconds_clip": 1325.0
          },
          "refined_25fps": {
            "last_timecode": "00:22:05.080",
            "last_seconds_clip": 1325.08,
            "clip_frame": 42,
            "last_seconds_broadcast": 2620.08,
            "adjusted_start_broadcast": 2610.08
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
| **Clip-relative** | `t_clip` | Start of 6-minute extracted clip | 1 FPS LLM output, 25 FPS refinement |
| **Time-of-day** | `t_tod` | Wall clock HH:MM:SS | CSV metadata |

The pipeline state file bridges these frames using `clip_offset = (break_start_tod - video_start_tod) - before_secs`.

See [coordinate-systems.md](coordinate-systems.md) for the complete reference diagram
including known coordinate bugs and their fixes.

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
- Enable thinking: true by default (disable with `--no-thinking` for faster results)

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

Use debug mode to understand why the model detected specific frames or to tune
ensemble settings.
