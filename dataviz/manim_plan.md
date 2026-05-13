# Ad Break Identifier — Pipeline Explainer Video

## Overview
- **Topic**: Automated pipeline for identifying adverts in TV broadcast videos
- **Arc**: Build-Up (stage-by-stage pipeline reveal)
- **Target audience**: Technical stakeholders, archivists, management
- **Length**: ~60-75 seconds
- **Style**: Architecture diagram, progressive build-up on a single canvas

## Color Palette
- Background: #1C1C1C (Manim default dark)
- Primary: #58C4DD (blue-cyan — overall system)
- Secondary: #83C167 (green — outputs)
- Accent: #FFFF00 (yellow — highlights, labels)
- Stage colors: amber #FFA500, rose #FF6B6B, violet #9B59B6, indigo #6366F1, emerald #10B981

## Font
- MONO = "Menlo" (monospace for all text)

## Scene: Pipeline Build-Up (~60s total)

### Phase 1: Title (~4s)
- "Ad Break Identifier" title centered
- Subtitle "Automated TV Advert Detection Pipeline"
- Fade in, hold, fade out

### Phase 2: Inputs (~6s)
- Left side: Video file label + CSV file label
- Boxes with labels
- Wait

### Phase 3: Stage 1 — Metadata Extraction (~8s)
- Arrow from inputs to Stage 1 box (amber)
- Label: "Metadata Extraction"
- Animate JSON file + state file appearing as output
- Subtitle: "Parse CSV scheduling data into structured metadata + pipeline state"

### Phase 4: Stage 2 — Clip Extraction (~8s)
- Arrow from Stage 1 to Stage 2 box (rose)
- Label: "Clip Extraction"
- Animate video clip appearing as output
- Subtitle: "Extract ad break clips from broadcast video using FFmpeg"

### Phase 5: Stage 3 — AI Detection 1 FPS (~10s)
- Arrow from Stage 2 to Stage 3 box (violet)
- Label: "AI Detection (1 FPS)"
- Show 5 small arrows converging (ensemble voting concept)
- Animate XML output appearing
- Subtitle: "Vision-language model identifies last frame of each advert with ensemble voting"

### Phase 6: Stage 4 — Frame Refinement 25 FPS (~10s)
- Arrow from Stage 3 to Stage 4 box (indigo)
- Label: "Frame Refinement (25 FPS)"
- Animate 3-second clip icon, magnifying glass concept
- Output: refined XML with millisecond precision
- Subtitle: "Zoom to 25 FPS for millisecond-precision advert boundaries"

### Phase 7: Stage 5 — Advert Clip Extraction (~8s)
- Arrow from Stage 4 to Stage 5 box (emerald)
- Label: "Advert Clip Extraction"
- Animate multiple individual clip files appearing
- Subtitle: "Extract each advert as a lossless H.264 video clip"

### Phase 8: Full Pipeline / End Card (~6s)
- All stages visible in full diagram
- Summary text: "CSV → Clips → 1 FPS → 25 FPS → Individual Adverts"
- Tech credits: "Qwen3.5 · FFmpeg · vLLM · BFI National Archive"
- Fade out
