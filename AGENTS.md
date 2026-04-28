# AGENTS.md - Coding Guidelines for Ad Break Identifier

Instructions for AI agents working in this repository.

## Project Overview

AI-powered ad break sequence identification in TV broadcast videos using Qwen3.5 vision-language model. The tool maps entire ad break sequences including channel idents and multiple sequential adverts, using ensemble voting (5 parallel API calls) with median voting to identify:
- Channel ident end frame
- First frame of each advert in sequence

## Build/Run Commands

```bash
# Install dependencies
pip install -r requirements.txt
pip install -e .                    # Editable install

# Run main application
python ad_break_identifier.py -v <video_url> --metadata-file <json_file>

# Run with CLI metadata instead of JSON file
python ad_break_identifier.py \
  -v "http://server/video.mp4" \
  --prog-before "Lorraine,ITV1" \
  --prog-after "Daybreak,ITV1" \
  --advert "adv_001|Tesco|Tesco|retail|20"

# Use frame count mode instead of timecode (default)
python ad_break_identifier.py -v <video_url> --metadata-file <json_file> --mode frame

# Run with human-readable output
python ad_break_identifier.py -v <video_url> --metadata-file <json_file> -o text

# Run benchmark script
python ad_break_identifier_benchmark.py

# Run ad break clip extractor
python ad_break_extractor.py --json-file <ad_breaks.json>
```

## Testing

**No test suite exists.** To add tests:
- Create test files in `tests/` directory
- Use pytest: `pytest tests/`
- Run single test: `pytest tests/test_file.py::test_function -v`

## Code Style Guidelines

### Python Version
- **Python 3.10+** required
- Use union syntax: `str | None`, `list[dict[str, Any]]`

### Formatting & Linting
- **No linter configured** - follow PEP 8 manually
- 4 spaces indentation
- Max line length: ~100 characters
- Use double quotes for strings

### Imports (Group Order)
1. Stdlib imports (sorted)
2. Third-party imports (sorted)
3. Local imports with `from .module import ...`

```python
import json
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from .models import AdBreakConfig
from .prompts import build_ad_break_prompt
```

### Naming Conventions
- **Functions/variables**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private**: `_leading_underscore`

### Type Hints (Required)
- Use for all function parameters and return values
- Use modern syntax: `str | None` instead of `Optional[str]`

```python
def process_data(data: dict[str, Any]) -> list[str] | None:
    ...
```

### Docstrings (Google Style)
- Short summary on first line
- Args and Returns sections
- Raises section for exceptions

```python
def normalize_timecode(timecode: str) -> str:
    """Normalize timecode to HH:MM:SS.mmm format.
    
    Args:
        timecode: Timecode string in various formats.
        
    Returns:
        Normalized timecode in HH:MM:SS.mmm format.
    """
```

### Error Handling
- Raise specific exceptions with clear messages
- Validate inputs early (prefer `__post_init__` in dataclasses)
- Use `try/except/finally` for resource cleanup
- Log errors before raising when appropriate

### Logging
- Use module-level logger: `logger = logging.getLogger(__name__)`
- Levels: INFO (flow), DEBUG (details), ERROR (failures)
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

### Data Classes
- Use `@dataclass` for configuration and result objects
- Validate in `__post_init__` when needed
- Provide default values for optional fields

```python
@dataclass
class AdBreakConfig:
    video_url: str
    fps: float = 1.0
    mode: Literal["timecode", "frame"] = "timecode"
    
    def __post_init__(self):
        if self.mode not in ["timecode", "frame"]:
            raise ValueError(f"Invalid mode: {self.mode}")
```

### Configuration Pattern
- Environment variables for defaults
- CLI arguments override env vars
- Hierarchy: CLI > env vars > hardcoded defaults

## Project Structure

```
ad_break_identifier/
├── src/ad_break_identifier/          # Main package
│   ├── __init__.py                   # Public API exports
│   ├── main.py                       # Entry point & CLI
│   ├── config.py                     # Configuration & metadata loading
│   ├── api_client.py                 # vLLM API client with ensemble
│   ├── prompts.py                    # Prompt templates (timecode + frame modes)
│   ├── response_parser.py            # XML response parsing
│   ├── models.py                     # Dataclasses (AdBreakResult, etc.)
│   ├── ensemble.py                   # Ensemble voting logic
│   └── video_processor.py            # FFmpeg utilities
├── ad_break_identifier.py            # Root entry point
├── ad_break_extractor.py             # Video clip extraction with FFmpeg
├── ad_break_identifier_benchmark.py  # Benchmarking script
├── ad_break_identifier_silence.py    # Silence detection utility
├── advert_describer.py               # Standalone description script
├── pyproject.toml                    # Package metadata
└── requirements.txt                  # Dependencies (openai>=1.0.0)
```

## Key Patterns

### Ensemble Voting
```python
from ad_break_identifier import run_ad_break_analysis, AdBreakConfig

config = AdBreakConfig(
    video_url="http://server/video.mp4",
    ad_break_metadata=metadata,
    enable_ensemble=True,
    ensemble_size=5,
    mode="timecode",  # or "frame"
)
result, stats, prompt, responses = run_ad_break_analysis(config)
```

### Mode-Aware Processing
- Timecode mode: XML uses `<timecode>` tags, HH:MM:SS.mmm format
- Frame mode: XML uses `<frame>` tags, integer format (0-based)
- Both modes use median ensemble voting

## Environment Variables

Key env vars (see config.py):
- `API_BASE_URL` - vLLM endpoint (default: `http://localhost:8000/v1`)
- `API_KEY` - Authentication (default: `EMPTY`)
- `MODEL_NAME` - Model to use (default: `Qwen/Qwen3.5-4B`)
- `FPS` - Sampling rate (default: `1.0`)
- `ENABLE_ENSEMBLE` - Enable ensemble voting (default: `true`)
- `ENSEMBLE_SIZE` - Number of parallel calls (default: `5`)
- `REFINE_FPS` - FPS for refinement stage (default: `25.0`)

## Refinement Stage

The refinement stage provides frame-accurate advert boundaries:
- Extracts 3-second clips centered on each advert's expected end timecode
- Analyzes at configurable FPS (default 25.0, use `--refine-fps` for NTSC at 24.0)
- Uses ensemble voting (default 3 calls) to determine precise last frame
- Outputs `HH:MM:SS.mmm` timecodes from `clip_start + (frame / fps)`

## Dependencies

- Python 3.10+
- openai>=1.0.0
- FFmpeg (for video clip extraction in ad_break_extractor.py)
- curl (for downloading videos from URLs)

## Notes

- No linting/formatting tools configured (no black, ruff, or flake8)
- No CI/CD configuration present
- No Cursor rules or Copilot instructions found
- Ensemble voting defaults to 5 parallel API calls
- Video URLs are downloaded to temp files for processing
