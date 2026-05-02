# AGENTS.md - Ad Break Identifier

AI-powered ad break sequence identification in TV broadcast videos using vision-language models via vLLM.

## Install & Run

```bash
pip install -e .          # Installs CLI commands into PATH
# or
uv pip install -e .       # Same with uv
```

**No root-level `.py` files exist.** All code is in `src/ad_break_identifier/` (package) and `bin/` (standalone scripts). References to `ad_break_identifier.py`, `ad_break_extractor.py`, etc. at repo root are stale.

## CLI Commands (post-install)

| Command | Main file | Purpose |
|---------|-----------|---------|
| `advert-identifier` | `src/ad_break_identifier/main.py` | Identify adverts in a clip |
| `advert-identifier-refine` | `src/ad_break_identifier/refinement_cli.py` | Frame-accurate refinement |
| `advert-identifier-clip` | `bin/advert-identifier-clip` | Extract ad break clips (FFmpeg) |
| `advert-identifier-metadata-extract` | `bin/advert-identifier-metadata-extract` | CSV → JSON metadata |
| `advert-identifier-single-advert-clip` | `src/ad_break_identifier/single_advert_clip.py` | Extract individual advert clips |
| `advert-identifier-pipeline` | `bin/advert-identifier-pipeline` | Full automation (NOT WORKING YET) |
| `advert-identifier-benchmark` | `bin/advert-identifier-benchmark` | Accuracy vs ground truth |
| `advert-identifier-describe` | `bin/advert-identifier-describe` | Generate video descriptions |

Entry points are wired in `pyproject.toml` through `src/ad_break_identifier/cli.py`. Most `cli.py` functions `subprocess` into `bin/` scripts; only `identifier_main` and `refine_main` import directly from the package.

## Pipeline Order (manual workflow)

1. `advert-identifier-metadata-extract` — extract CSV scheduling data to JSON
2. `advert-identifier-clip` — extract video clips around ad break timestamps
3. `advert-identifier` — AI detection (1 FPS sweep, ensemble voting)
4. `advert-identifier-refine` — frame-accurate boundaries (25 FPS, 3s clips)
5. `advert-identifier-single-advert-clip` — lossless individual advert extraction

## Environment Variables

All read by `src/ad_break_identifier/config.py`. CLI flags override these:

| Variable | Default |
|----------|---------|
| `API_BASE_URL` | `http://localhost:8000/v1` |
| `API_KEY` | `EMPTY` |
| `MODEL_NAME` | `Qwen/Qwen3.5-4B` |
| `FPS` | `1.0` (primary detection sampling) |
| `ENABLE_ENSEMBLE` | `true` |
| `ENSEMBLE_SIZE` | `5` |
| `ENSEMBLE_DELAY` | `10.0` (seconds between requests) |
| `REFINE_FPS` | `25.0` (refinement stage, set 24.0 for NTSC) |

## Key Gotchas

- **`ad_break_index` is 1-based** in `config.py` (`load_metadata_from_file`), not 0-based. The CLI flag `--ad-break-index` also uses 1-based indexing.
- **Two analysis modes**: `timecode` (default, MM:SS output) and `frame` (0-based frame numbers). Affects both prompt templates and XML output tags.
- **Ensemble voting**: Default 5 parallel API calls with median voting. Use `--no-ensemble` for single-call debugging.
- **Refinement** narrows coarse timecodes to frame-level precision using 25 FPS 3-second clips. Output timecodes are floor-snapped to nearest `1/fps` boundary.
- **`prompts_v1.py`** exists alongside `prompts.py`. The v1 module is legacy; active code imports from `prompts.py`.
- **Video URLs** must be served via HTTP (FFmpeg extracts to temp files for processing).
- **`debug.json` / `debug.md`** are gitignored. Use `--debug` flag to generate them.

## No Testing, No Linting

**No test suite, no linting, no CI.** If adding tests, create `tests/` directory and use pytest. Python 3.10+ required (uses modern union syntax: `str | None`).

## External Dependencies

- **vLLM server** with a vision-language model running (local or remote)
- **FFmpeg** (binary must be on PATH for clip extraction)

## Utility Scripts

`scripts/` contains ad-hoc helpers:
- `debug_refine_to_md.py` — convert refinement debug JSON to markdown
- `rename_advert_clips.py` — rename extracted advert clips
- `fix_xml_escaping.py` — fix XML entity escaping issues
