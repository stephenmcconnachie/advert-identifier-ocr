# AGENTS.md - Ad Break Identifier

OCR-based ad break sequence identification in TV broadcast videos using PaddleOCR-VL via vLLM.

## Install & Run

```bash
pip install -e .          # Installs CLI commands into PATH
# or
uv pip install -e .       # Same with uv
```

**No root-level `.py` files exist.** All code is in `src/ad_break_identifier/` (package) and `bin/` (standalone scripts).

## CLI Commands (post-install)

| Command | Main file | Purpose |
|---------|-----------|---------|
| `advert-identifier` | `src/ad_break_identifier/detect.py` | OCR detection (5 FPS frame extraction + PaddleOCR-VL + brand search) |
| `advert-identifier-metadata-extract` | `bin/advert-identifier-metadata-extract` | CSV → JSON metadata |
| `advert-identifier-single-advert-clip` | `src/ad_break_identifier/single_advert_clip.py` | Extract individual advert clips |
| `advert-identifier-pipeline` | `bin/advert-identifier-pipeline` | Full automation |

Entry points are wired in `pyproject.toml` through `src/ad_break_identifier/cli.py`. `identifier_main` and `single_advert_clip_main` import directly from the package; `pipeline_main` and `extractor_main` subprocess into `bin/` scripts.

## Pipeline Order (manual workflow)

1. `advert-identifier-metadata-extract` — extract CSV scheduling data to JSON + pipeline state
2. `advert-identifier` — OCR detection (5 FPS frames, PaddleOCR-VL, two-tier brand search with ordering enforcement)
3. `advert-identifier-single-advert-clip` — lossless individual advert extraction

## vLLM Server Setup

Deploy PaddleOCR-VL via vLLM:

```bash
vllm serve PaddlePaddle/PaddleOCR-VL \
    --trust-remote-code \
    --max-num-batched-tokens 16384 \
    --no-enable-prefix-caching \
    --mm-processor-cache-gb 0
```

The OCR client sends base64-encoded frames to the OpenAI-compatible `/v1/chat/completions` endpoint with an `"OCR:"` text prompt.

## Environment Variables

All read by `src/ad_break_identifier/config.py`. CLI flags override these:

| Variable | Default |
|----------|---------|
| `OCR_ENDPOINT` | `http://localhost:8000/v1/chat/completions` |
| `OCR_MODEL` | `PaddlePaddle/PaddleOCR-VL` |
| `DETECTION_FPS` | `5.0` (frame extraction rate) |
| `BEFORE_SECS` | `10.0` (seconds before ad break start) |
| `AFTER_SECS` | `360.0` (seconds after ad break start) |

## Key Gotchas

- **`ad_break_index` is 1-based** in `config.py` (`load_metadata_from_file`), not 0-based. The CLI flag `--ad-break-index` also uses 1-based indexing.
- **Two-tier brand search**: Tier 1 uses word-boundary regex (`\bgalaxy\b`) for exact matches. Tier 2 (fallback) uses substring regex (`galaxy`) to catch concatenated forms like `galaxychocolate.com`.
- **Ordering enforcement**: Each advert's last matching frame must be after the previous advert's last matching frame. The search range for advert N is `(prev_last_frame, end]`.
- **OCR results JSON**: Detection saves a `{video_stem}_ocr.json` file alongside the metadata with per-frame OCR text, timestamps, and frame indices. This is queryable for debugging.
- **Video URLs** must be served via HTTP (the detection command downloads the video to a temp file for FFmpeg frame extraction).
- **No clip creation step**: The new pipeline extracts frames directly from the original broadcast video — no intermediate clip files are created.
- **Pipeline state version 2**: Uses `detection` field (replacing the old `coarse_1fps` / `refined_25fps` fields). Status lifecycle: `metadata_extracted` → `detected` → `clipped`.

## No Testing, No Linting

**No linting, no CI.** Tests exist in `tests/` using pytest. Python 3.10+ required (uses modern union syntax: `str | None`).

## External Dependencies

- **vLLM server** with PaddleOCR-VL model running (local or remote)
- **FFmpeg** (binary must be on PATH for frame extraction and clip extraction)
- **`requests`** Python package (for OCR API calls)

## Utility Scripts

`scripts/` contains ad-hoc helpers:
- `rename_advert_clips.py` — rename extracted advert clips
