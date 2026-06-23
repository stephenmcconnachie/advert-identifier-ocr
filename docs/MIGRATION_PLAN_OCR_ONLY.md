# Migration Plan: Strip VLM, Simplify to OCR-Only 5 FPS Pipeline

## Goal

Remove all Vision Language Model (VLM) tooling and consolidate to a single OCR-based detection method using PaddleOCR-VL via vLLM. Eliminate the two-stage (1 FPS coarse + 25 FPS refine) approach in favour of a single 5 FPS pass with two-tier brand search.

## New Pipeline (3 stages)

1. **`advert-identifier-metadata-extract`** — CSV → JSON metadata + pipeline state (unchanged)
2. **`advert-identifier`** — extracts frames at 5 FPS from the original video, OCRs all frames via PaddleOCR-VL, stores results in JSON, performs two-tier brand search with ordering enforcement, outputs XML
3. **`advert-identifier-single-advert-clip`** — lossless individual advert extraction (unchanged)

## OCR Model: PaddleOCR-VL via vLLM

PaddleOCR-VL exposes a standard OpenAI-compatible chat completions endpoint on vLLM. The existing `ocr_client.py` (which uses `requests` to hit a chat completions URL) requires only minimal changes:

| Current | New (PaddleOCR-VL) |
|---|---|
| `DEFAULT_MODEL = "lightonai/LightOnOCR-2-1B"` | `DEFAULT_MODEL = "PaddlePaddle/PaddleOCR-VL"` |
| `DEFAULT_TEMPERATURE = 0.2` | `DEFAULT_TEMPERATURE = 0.0` (deterministic) |
| Sends image only | Sends image + `"OCR:"` text prompt |
| `system_prompt` parameter | `prompt` parameter defaulting to `"OCR:"` |

vLLM server deployment:
```bash
vllm serve PaddlePaddle/PaddleOCR-VL \
    --trust-remote-code \
    --max-num-batched-tokens 16384 \
    --no-enable-prefix-caching \
    --mm-processor-cache-gb 0
```

The `openai` Python package is **not needed** — `requests` hits the same endpoint.

---

## Phase 1: Delete VLM-only and obsolete files

### `src/ad_break_identifier/` — delete 10 modules

| File | Reason |
|---|---|
| `main.py` | VLM detection entry point |
| `api_client.py` | vLLM OpenAI client (VLM) |
| `ensemble.py` | VLM ensemble voting |
| `ensemble_filter.py` | MAD outlier filter (only used by refinement.py) |
| `prompts.py` | VLM prompt templates |
| `prompts_v1.py` | Dead code (not imported anywhere) |
| `response_parser.py` | VLM XML response parser |
| `refinement.py` | VLM 25 FPS refinement |
| `refinement_cli.py` | VLM refinement CLI |
| `ocr_refine.py` | OCR 25 FPS refinement (no second pass needed) |

### `bin/` — delete 7 scripts

| File | Reason |
|---|---|
| `advert-identifier` | VLM wrapper (replaced by new wrapper) |
| `advert-identifier-refine` | VLM refinement wrapper |
| `advert-identifier-describe` | VLM describe tool (direct `openai` import) |
| `advert-identifier-benchmark` | VLM benchmark |
| `advert-identifier-ocr-scan` | Old OCR scan wrapper (replaced by new `advert-identifier`) |
| `advert-identifier-ocr-refine` | OCR refine wrapper (no second pass) |
| `advert-identifier-clip` | Video clip creation (replaced by frame extraction in detection) |

### `tests/` — delete 4 VLM test files

- `test_ensemble_filter.py`, `test_refinement_integration.py`, `test_cli_and_pipeline.py`, `test_model_vote_stats.py`

### `scripts/` — delete 3 VLM scripts

- `debug_refine_to_md.py`, `fix_xml_escaping.py`, `start_screen_sessions.sh`

### `docs/` — delete 6 VLM demo HTMLs

- `advert-identifier-{simple,complex}.{html,darker.html,highcontrast.html}`

### `experiments/` — delete

- `ocr_api_client.py` (duplicate of `ocr_client.py`), `ocr_refinement.py` (no longer needed)

---

## Phase 2: Modify `ocr_client.py` for PaddleOCR-VL

1. `DEFAULT_MODEL` → `"PaddlePaddle/PaddleOCR-VL"`
2. `DEFAULT_TEMPERATURE` → `0.0`
3. Add `DEFAULT_OCR_PROMPT = "OCR:"` constant
4. Replace `system_prompt` parameter with `prompt: str = DEFAULT_OCR_PROMPT`
5. Include the prompt as a text content item in the user message alongside the image
6. Keep `requests` as HTTP client, keep base64 encoding, keep retry logic

---

## Phase 3: Create new detection module (`detect.py`)

Replace `ocr_scan.py` with a new `src/ad_break_identifier/detect.py`.

### Flow

1. Load metadata from `--metadata-file` (or CLI args), get ad break start time + advert list
2. Download original video to temp file (curl with retry)
3. Compute time range: `start = break_start_secs - before_secs`, `duration = before_secs + after_secs` (defaults: 10s / 360s)
4. Extract frames at 5 FPS via FFmpeg from `[start, start + duration]` → temp PNGs
   - Frame N's clip-relative time = `N / 5.0`
   - Frame N's broadcast time = `start + N / 5.0`
5. OCR all frames via `ocr_client.ocr_batch()` with PaddleOCR-VL
6. Store OCR results in a queryable JSON file (`{video_stem}_ocr.json`)
7. Two-tier brand search with ordering enforcement:
   - For each advert **in order** (1, 2, 3, ...):
     - **Tier 1 — exact word match**: regex with word boundaries (`\bgalaxy\b`) for brand, advertiser, category
     - Search frames in range `(prev_advert_last_frame, end]`
     - Find the **last** matching frame
     - **Tier 2 — substring match** (only if Tier 1 found nothing): unbounded regex (`galaxy`) to catch `galaxychocolate.com`
     - Search same frame range, find last matching frame
     - If no match at all → fallback marker in XML
   - Ordering: each advert's last frame must be after the previous advert's last frame
8. Output XML — same schema as current (`<ad_break>` / `<advert>` / `<unique_id>` / `<brand>` / `<last_timecode>` / `<ocr_match_fallback>`)
   - Timecodes are clip-relative (MM:SS.mmm)
9. Update pipeline state with detection results

### CLI interface

```
advert-identifier \
  -v <video-url> \
  --metadata-file <path> \
  --ad-break-index <N> \
  --before-secs 10 \
  --after-secs 360 \
  --ocr-endpoint http://localhost:8000/v1/chat/completions \
  --ocr-model PaddlePaddle/PaddleOCR-VL \
  --output-dir <dir> \
  --verbose
```

### Code reuse from `ocr_scan.py`

- `download_video_to_temp()` — keep
- `get_video_duration()` — keep
- `seconds_to_timecode()` / `timecode_to_seconds()` — keep
- `build_match_patterns()` — modify for two-tier (exact word + substring)
- `match_ocr_text()` — modify for two-tier matching
- `format_xml()` — keep/adapt
- `update_pipeline_state()` — adapt for single-stage results
- `compute_advert_windows()` — remove (replaced by ordering enforcement)

### New functions

- `extract_5fps_frames(video_path, start_seconds, duration, output_dir)` — FFmpeg at 5 FPS from a time range
- `build_exact_patterns(brand, advertiser, category)` — word-boundary regexes (`\b...\b`)
- `build_substring_patterns(brand, advertiser, category)` — unbounded regexes
- `search_with_ordering(ocr_results, adverts, ...)` — iterates adverts in order, enforces frame ordering
- `save_ocr_results(ocr_results, path)` — writes queryable JSON

### New `bin/advert-identifier` wrapper

```python
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ad_break_identifier.detect import main
sys.exit(main())
```

---

## Phase 4: Update shared infrastructure

### `config.py`

- Remove from `AdBreakConfig`: `api_base_url`, `api_key`, `model_name`, `fps`, `mode`, `enable_ensemble`, `ensemble_size`, `ensemble_delay`, `enable_thinking`, `output_format`
- Add: `ocr_endpoint`, `ocr_model`, `detection_fps` (default `5.0`), `before_secs` (default `10.0`), `after_secs` (default `360.0`)
- Keep: `video_url`, `metadata_file`, `ad_break_index`, `prog_before`, `prog_after`, `adverts_cli`, `verbose`
- Keep `load_metadata_from_file()` and `parse_cli_metadata()` unchanged
- Add env vars: `OCR_ENDPOINT`, `OCR_MODEL`, `DETECTION_FPS` (read in `load_config`)

### `models.py`

- Remove: `EnsembleStats`, `RefinementStats`, `RefinedAdvertResult`, `RefinedAdBreakResult`
- Keep: `ProgrammeMetadata`, `AdvertMetadata`, `AdBreakMetadata`, `AdvertResult`, `AdBreakResult`

### `pipeline_state.py`

- Replace `coarse_1fps` / `refined_25fps` fields with a single `detection` field:
  ```json
  "detection": {
    "last_timecode": "04:30.200",
    "last_seconds_clip": 270.2,
    "last_frame": 1351,
    "match_tier": "exact",
    "matched_terms": ["galaxy"]
  }
  ```
- Update `create_initial_state()` to use `detection: null`
- Update `update_break_adverts()` to compute `adjusted_start_broadcast` from `detection.last_seconds_clip` + `clip_offset` + duration
- Status lifecycle: `metadata_extracted` → `detected` → `clipped`

### `cli.py`

- Remove: `identifier_main` (old VLM), `refine_main`, `benchmark_main`, `describer_main`, `ocr_scan_main`, `ocr_refine_main`, `clipper_main`
- Add: new `identifier_main` → imports from `ad_break_identifier.detect`
- Keep: `extractor_main`, `single_advert_clip_main`, `pipeline_main`

### `__init__.py`

- Remove all VLM exports
- Export: `detect` module functions, shared models, config

### `pyproject.toml`

- Remove dependency: `openai>=1.0.0`
- Add dependency: `requests>=2.0.0`
- Entry points (4 only):
  ```toml
  advert-identifier = "ad_break_identifier.cli:identifier_main"
  advert-identifier-pipeline = "ad_break_identifier.cli:pipeline_main"
  advert-identifier-metadata-extract = "ad_break_identifier.cli:extractor_main"
  advert-identifier-single-advert-clip = "ad_break_identifier.cli:single_advert_clip_main"
  ```

### `requirements.txt`

- Remove: `openai==1.54.3`
- Add: `requests>=2.0.0`

---

## Phase 5: Rewrite pipeline (`bin/advert-identifier-pipeline`)

New 3-stage flow per video:
1. Metadata extraction — `advert-identifier-metadata-extract` (unchanged)
2. OCR detection — `advert-identifier` with original video URL, metadata file, before/after secs
3. Single advert clip extraction — `advert-identifier-single-advert-clip` (unchanged)

Remove: `run_clipper()`, `run_refinement()`, `find_clip_files()`, refined XML handling.

---

## Phase 6: Update documentation

| File | Action |
|---|---|
| `AGENTS.md` | Update CLI commands (4), pipeline order (3 steps), env vars, gotchas |
| `README.md` | Remove VLM section, update requirements (vLLM + PaddleOCR-VL), update commands |
| `QUICKSTART.md` | Update command examples |
| `docs/ARCHITECTURE.md` | Rewrite for OCR-only single-stage pipeline |
| `docs/CLI_REFERENCE.md` | Remove VLM commands, update remaining |
| `docs/coordinate-systems.md` | Keep (still relevant) |
| `docs/TROUBLESHOOTING.md` | Update if it references VLM-specific issues |

---

## Phase 7: Tests

- Keep: `tests/test_pipeline_state.py`, `tests/test_single_advert_clip.py`
- Delete: 4 VLM test files
- Add: `tests/test_detect.py` — unit tests for two-tier pattern matching and ordering enforcement

---

## Environment Variables (new)

| Variable | Default | Purpose |
|---|---|---|
| `OCR_ENDPOINT` | `http://localhost:8000/v1/chat/completions` | vLLM chat completions URL |
| `OCR_MODEL` | `PaddlePaddle/PaddleOCR-VL` | Model name on vLLM server |
| `DETECTION_FPS` | `5.0` | Frame extraction rate |
| `BEFORE_SECS` | `10.0` | Seconds before ad break start |
| `AFTER_SECS` | `360.0` | Seconds after ad break start |

**Removed:** `API_BASE_URL`, `API_KEY`, `MODEL_NAME`, `FPS`, `ENABLE_ENSEMBLE`, `ENSEMBLE_SIZE`, `ENSEMBLE_DELAY`, `REFINE_FPS`

---

## Key Design Decisions

1. **Single combined command** — frame extraction + OCR + search in one `advert-identifier` command
2. **Keep `single-advert-clip`** as final pipeline step
3. **Search all frames with ordering enforcement** — advert N's last match must be after advert N-1's last match
4. **Repurpose `advert-identifier`** as the new OCR detection command name
5. **PaddleOCR-VL via direct OpenAI-compatible API** (not PaddleOCR Python API) — no PaddlePaddle client-side dependencies, just `requests` to vLLM
6. **`openai` package fully removed** — neither VLM (deleted) nor OCR (uses `requests`) need it
