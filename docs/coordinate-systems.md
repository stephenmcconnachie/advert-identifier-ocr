# Pipeline Coordinate Systems

> **Last updated:** 2026-05-06
>
> **Status:** Bug fixes for coordinate transformations applied on
> `fix/coordinate-systems` branch (not yet merged to master).
>
> Changes in this branch:
> - `single_advert_clip.py`: reads `refined_timecode` from refined XML, applies
>   `--clip-offset` to convert clip-relative timecodes to broadcast-absolute,
>   handles HH:MM:SS.mmm format with millisecond precision
> - `advert-identifier-pipeline`: computes and passes `--clip-offset` and
>   `--json-file` to `single_advert_clip`
>
> **TODO after merge:** Update this header to remove branch note.

## Overview

The advert-identifier pipeline processes video through multiple stages, each operating in
a different coordinate system. Understanding which reference frame each stage uses is
critical for correctly connecting refined timecodes back to the original source media.

### Key Coordinate Systems

| Frame | Symbol | Origin | Used by |
|-------|--------|--------|---------|
| **Broadcast-absolute** | `t_bcast` | Start of the full broadcast `.mp4` file | `single_advert_clip` (FFmpeg `-ss`), pipeline summary |
| **Clip-relative** | `t_clip` | Start of the 6-minute extracted clip | 1 FPS identifier (LLM input → XML output), 25 FPS refinement |
| **3s-clip-relative** | `t_3s` | Start of the 3-second refinement sub-clip | 25 FPS LLM (outputs frame numbers within this clip) |
| **Time-of-day** | `t_tod` | Wall-clock time (HH:MM:SS) from CSV metadata | `clip` script (time-of-day mode), metadata JSON |

---

## Stage-by-Stage Breakdown

### Stage 0: Source Media

```
File:  2024-03-26_ITV1HD_09:45:00.mp4
```

- Duration: N seconds (e.g., 6 hours = 21600 s)
- The filename embeds the wall-clock start time: `09:45:00`
- All coordinates in this document use this file as the broadcast-absolute frame `t_bcast`

---

### Stage 1: Metadata Extraction

**Script:** `advert-identifier-metadata-extract`

**Inputs:**
- Full broadcast video file (filename-parsed for date/channel/start_time)
- CSV schedule file (`{date}_BFIExport.csv`)

**Output:** `{video_stem}_metadata.json`

**Coordinate system:** Time-of-day (`t_tod`)

The JSON contains:
```json
{
  "video_info": {
    "start_time": "09:45:00",           // t_tod — parsed from filename
    "filepath": "/path/to/2024-03-26_ITV1HD_09:45:00.mp4"
  },
  "ad_breaks": [
    {
      "index": 1,
      "start_time": "09:48:30",         // t_tod — from CSV "Start time" column
      "adverts": [
        {
          "unique_id": "BBHTCPT536010",
          "start_time": "09:48:30",     // t_tod — first advert starts at break time
          "duration_seconds": 30,       // Computed from next advert start
          "brand": "...",
          "advertiser": "..."
        }
      ]
    }
  ]
}
```

**Key formula:** `t_bcast = t_tod - video_start_time`

For the example above:
- `t_bcast` = `09:48:30` − `09:45:00` = **210 s** from broadcast start

---

### Stage 2: Clip Extraction

**Script:** `advert-identifier-clip`

**Inputs:** `_metadata.json` + full broadcast video

**Output:** 6-minute clips named `{video_stem}_{padded_index}of{padded_total}.mp4`

**Coordinate system:** Clip-relative (`t_clip`) — the extracted clip has its own time zero.

The clip tool receives `video_start_time` from the JSON and operates in **time-of-day mode**
(`extract_clip_with_timecode`, lines 303-313 in `advert-identifier-clip`):

```python
center_time_secs = timecode_to_seconds(center_timecode)      # e.g., "09:48:30" → 35310 s
video_start_secs = timecode_to_seconds(normalize_timecode(video_start_time))  # "09:45:00" → 35100 s
center_seconds = center_time_secs - video_start_secs          # 35310 - 35100 = 210 s from bcast start
```

**The FFmpeg seek** uses the **broadcast-relative** offset:

```
start_seconds = center_seconds - before_secs    # 210 - 10 = 200 s from broadcast start
duration     = before_secs + after_secs          # 10 + 360 = 370 s
```

**Output clip:**
```
File: 2024-03-26_ITV1HD_09:45:00_01of04.mp4
Contents: Broadcast range [200, 570] s  (i.e., 09:48:20 to 09:54:30)
Duration: 370 s (~6 min)
```

**Coordinate boundaries:** The clip spans `t_clip ∈ [0, 370]`, corresponding to
`t_bcast ∈ [200, 570]`.

**Relationship to broadcast:**
```
t_bcast = t_clip + clip_offset
clip_offset = 200 s
```

Where `clip_offset` = `(ad_break_start_time - video_start_time) - before_secs`.

---

### Stage 3: 1 FPS Advert Identification

**Script:** `advert-identifier` (entry point → `main.py`)

**Inputs:**
- Local video path (e.g., `video/2024-03-26_ITV1HD_13:30:00_01of04.mp4`) — auto-served via temporary HTTP server
- `_metadata.json` (for advert metadata + brand/advertiser/category)

**Output:** `{clip_stem}.xml`

**Coordinate system:** ❗ Clip-relative (`t_clip`)

The LLM receives the 6-minute clip and is prompted to output timecodes in MM:SS format.
The prompt (from `prompts.py`) says:

```
"Provide the timecode in MM:SS format for the last frame"
"Video is sampled at 1 FPS (each frame = 1 second apart)"
```

The LLM naturally treats 00:00 as the first frame of the clip it sees.

Parsing (`response_parser.py` → `ensemble.py`):
```python
# ensemble.py line 80-82
seconds_list = [timecode_to_seconds(tc) for tc in timecodes]
median_seconds = median(seconds_list)
```

Where `timecode_to_seconds("04:30")` = 4×60 + 30 = **270 s**.

**XML output:**
```xml
<ad_break>
    <advert>
        <unique_id>BBHTCPT536010</unique_id>
        <brand>BRAND</brand>
        <duration_seconds>30</duration_seconds>
        <last_timecode>04:30</last_timecode>
        <description>...</description>
    </advert>
</ad_break>
```

**Key fact:** `<last_timecode>04:30</last_timecode>` means 270 s from **clip** start,
not from broadcast start.

**In broadcast coordinates:** `t_bcast = 270 + 200 = 470 s` (09:52:50).

---

### Stage 4: 25 FPS Frame Refinement

**Script:** `advert-identifier-refine` (→ `refinement_cli.py` → `refinement.py`)

**Inputs:**
- XML from Stage 3 (contains clip-relative `last_timecode`)
- **Same clip video** as Stage 3

**Output:** `{clip_stem}_refined.xml`

**Coordinate system:** Clip-relative (`t_clip`)

In `refine_single_advert` (`refinement.py` lines 348-436):

```python
# advert.timecode = "04:30" = 270 s from clip start
coarse_seconds = timecode_to_seconds(advert.timecode)       # 270 s (clip-relative)
clip_center = coarse_seconds                                  # 270
clip_start = clip_center - 1.5                                # 268.5 s from clip start
clip_duration = 3.0

# Extracts a 3-second clip from [268.5, 271.5] in clip-relative coordinates
# via FFmpeg -ss 268.5 on the CLIP video
extract_clip(video_path, clip_start, clip_duration, clip_path)
```

The LLM outputs a frame number within this 3-second clip (at 25 FPS, so 75 frames):

```python
median_frame = ensemble_vote...  # e.g., frame 40
raw_refined_seconds = clip_start + (median_frame / fps)      # 268.5 + 40/25 = 270.1 s
refined_seconds = int(raw_refined_seconds * fps) / fps        # Floored to 1/fps boundary
refined_timecode = seconds_to_timecode(refined_seconds)       # "00:04:30.080"
```

**Refined XML output:**
```xml
<ad_break>
    <advert>
        <unique_id>BBHTCPT536010</unique_id>
        <brand>BRAND</brand>
        <advertiser>ADVERTISER</advertiser>
        <category>CATEGORY</category>
        <duration_seconds>30</duration_seconds>
        <last_timecode>04:30</last_timecode>                     <!-- clip-relative, 1 FPS -->
        <refined_timecode>00:04:30.080</refined_timecode>       <!-- clip-relative, 25 FPS -->
        <refined_clip_frame>40</refined_clip_frame>
        <refinement_status>success</refinement_status>
        <description>...</description>
    </advert>
</ad_break>
```

**Key fact:** Both `<last_timecode>` and `<refined_timecode>` are **clip-relative**.
`00:04:30.080` = 270.08 s from clip start (frame 40 at 25 FPS within the 3-second
window centered at 270 s).

**In broadcast coordinates:** `t_bcast = 270.08 + 200 = 470.08 s`.

---

### Stage 5: Single Advert Clip Extraction

**Script:** `advert-identifier-single-advert-clip`

**Inputs:**
- XML from Stage 3 (NOT Stage 4 in current pipeline)
- Full broadcast video URL
- JSON metadata

**Output:** Lossless advert clips (`.mp4`)

**Coordinate system:** ❗ **BUG — mixture of clip-relative and broadcast-absolute**

The script reads `last_timecode` from the XML:

```python
# single_advert_clip.py line 399-401
last_tc = advert["last_timecode"]   # "04:30" — this is CLIP-RELATIVE
last_secs = timecode_to_seconds(last_tc)  # 270 s
start_secs = last_secs - duration         # 270 - 30 = 240 s
```

Then uses it as an **FFmpeg seek offset** on the **full broadcast video**:

```python
# single_advert_clip.py line 268-288
cmd = [
    "ffmpeg",
    "-ss", str(start_seconds),    # 240 s (clip-relative, used as broadcast-absolute)
    "-i", video_input,             # Full broadcast video URL
    "-t", str(duration_seconds),   # 30 s
    ...
]
```

### The Bug

| Value | Intended interpretation | Actual usage | Correct value |
|-------|------------------------|--------------|---------------|
| `last_timecode` = "04:30" | 270 s from clip start | 270 s from broadcast start | — |
| `start_secs` = 240 | 240 s from clip start into advert | 240 s from broadcast start = **09:49:00** | **440 s** = 09:52:20 |

The extracted clip starts 200 seconds too early. (Clip start at 09:49:00 instead of ~09:52:20.)

**Root cause:** The pipeline passes the full broadcast URL to `single_advert_clip` but
the XML timecodes are clip-relative. No clip_offset is applied.

---

## Coordinate Transformation Reference

### Transformations Required Per Stage

```
TOD (t_tod)          CSV schedule data
  │
  │  t_bcast = t_tod - video_start_time
  ▼
BROADCAST (t_bcast)  FFmpeg seeks on full video
  │
  │  t_clip = t_bcast - clip_offset
  │  clip_offset = (ad_break_start - video_start) - before_secs
  ▼
CLIP (t_clip)        1 FPS identifier, 25 FPS refinement
  │
  │  t_3s = t_clip - (coarse_seconds - 1.5)
  ▼
3S-CLIP (t_3s)       25 FPS LLM output (frame numbers)
                     refined_frame ∈ [0, 74] at 25 FPS
```

### Inverse

```
refined_frame  →  t_3s = refined_frame / fps
                →  t_clip = (coarse_seconds - 1.5) + refined_frame / fps
                →  t_bcast = clip_offset + (coarse_seconds - 1.5) + refined_frame / fps
```

### Concrete Example

Using the running example throughout this document:

| Stage | Variable | Value | Frame |
|-------|----------|-------|-------|
| Metadata | `video_start_time` | 09:45:00 | t_tod |
| Metadata | `break_start_time` | 09:48:30 | t_tod |
| Clip | `clip_offset` | 200 s | t_bcast |
| Clip | Clip range | [200, 570] s | t_bcast |
| Clip | Clip range | [0, 370] s | t_clip |
| 1 FPS | `last_timecode` | 04:30 (= 270 s) | t_clip |
| 25 FPS | `refined_frame` | 40 | frame |
| 25 FPS | `refined_seconds` | 270.08 s | t_clip |
| **Target** | **Advert last frame** | **470.08 s** | **t_bcast** |
| **Target** | **Advert start** | **440.08 s** | **t_bcast** |

---

## Summary of Known Issues

| # | Issue | Stage | Impact | Status |
|---|-------|-------|--------|--------|
| 1 | `single_advert_clip` treated clip-relative `last_timecode` as broadcast-absolute | Stage 5 | Extracted advert clips were ~200 s too early | **FIXED** — `--clip-offset` parameter applied |
| 2 | Refined XML `refined_timecode` is never read by `single_advert_clip` | Stage 5 → Stage 4 | Precision refinement had no effect on final output | **FIXED** — `parse_advert_xml` reads `refined_timecode`, `main()` prefers it |
| 3 | Missing `--json-file` in pipeline `run_single_advert_clipper()` call | Pipeline | Pipeline would fail at clip extraction | **FIXED** — `--json-file` and `--clip-offset` both passed |
| 4 | `timecode_to_seconds` used `int()` truncation, losing ms precision | Stage 5 | Refined timecodes with `.080` would raise ValueError | **FIXED** — uses `float()` preserving ms |
| 5 | No clip_offset propagated through pipeline | All | Each stage independently guessed the coordinate system | **PARTIAL** — Pipeline now computes and passes clip_offset. Option D will add persistent tracking. |

---

## Future State (Post-Option D)

After the pipeline state file implementation, the coordinate flow will be:

```
Stage 1 (metadata-extract):
  └─→ pipeline_state.json:  { clip_offset, scheduled_start_tod, duration_seconds }

Stage 2 (clip):
  └─→ pipeline_state.json:  { clip_path, clip_offset (unchanged) }

Stage 3 (1 FPS):
  └─→ pipeline_state.json:  {
         coarse_1fps: { last_timecode, last_seconds_clip }
       }

Stage 4 (25 FPS):
  └─→ pipeline_state.json:  {
         refined_25fps: {
           last_timecode, last_seconds_clip, clip_frame,
           last_seconds_broadcast  (= clip_offset + last_seconds_clip),
           adjusted_start_broadcast (= last_seconds_broadcast - duration_seconds)
         }
       }

Stage 5 (single_advert_clip):
  └─→ Reads adjusted_start_broadcast from pipeline_state.json
  └─→ Uses FFmpeg -ss adjusted_start_broadcast on full broadcast video
```

### Already Implemented (on `fix/coordinate-systems` branch)

| Feature | Implementation |
|---------|---------------|
| `--clip-offset` parameter | Added to `single_advert_clip.py` CLI |
| Refined timecode preference | `parse_advert_xml` reads `refined_timecode`, `main()` prefers it over `last_timecode` |
| ms precision | `timecode_to_seconds` uses `float()` for fractional seconds |
| Pipeline clip_offset computation | `run_single_advert_clipper()` reads JSON metadata, computes and passes `--clip-offset` |
| Pipeline `--json-file` pass-through | Added to `single_advert_clip` invocation |

### Still Needed (Option D)

- Persistent per-video pipeline state file (tracks all stages)
- Refinement step integrated into the pipeline
- `adjusted_start_broadcast` written to state file for future analytics
