"""OCR API client for vLLM-hosted PaddleOCR-VL model.

Sends individual image frames to a vLLM endpoint using the OpenAI chat
completions format with base64-encoded image data and an "OCR:" text prompt.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif",
})

MIME_TYPE_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

DEFAULT_ENDPOINT = "http://localhost:8000/v1/chat/completions"
DEFAULT_MODEL = "PaddlePaddle/PaddleOCR-VL"
DEFAULT_OCR_PROMPT = "OCR:"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 1.0
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2


def _validate_endpoint(endpoint: str) -> None:
    """Validate that the endpoint URL uses an allowed scheme."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid endpoint URL scheme: {parsed.scheme}. Only http and https are allowed."
        )


def _get_mime_type(image_path: Path) -> str:
    return MIME_TYPE_MAP.get(image_path.suffix.lower(), "image/png")


def _encode_image(image_path: Path) -> str:
    return base64.b64encode(image_path.read_bytes()).decode("utf-8")


def ocr_image(
    image_path: Path,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    top_p: float = DEFAULT_TOP_P,
    timeout: int = DEFAULT_TIMEOUT,
    prompt: str = DEFAULT_OCR_PROMPT,
) -> str:
    """Send a single image frame to the OCR model and return extracted text.

    Raises:
        FileNotFoundError: If image_path does not exist.
        RuntimeError: If the API call fails after retries.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    _validate_endpoint(endpoint)

    ext = image_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported extension %s, trying PNG", ext)

    image_b64 = _encode_image(image_path)
    mime_type = _get_mime_type(image_path)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                },
                {
                    "type": "text",
                    "text": prompt,
                },
            ],
        },
    ]

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(endpoint, json=payload, timeout=timeout, allow_redirects=False)
            response.raise_for_status()
            result = response.json()
            text = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            logger.debug("OCR %s → %d chars", image_path.name, len(text))
            return text

        except requests.RequestException as e:
            last_error = e
            logger.warning(
                "OCR attempt %d/%d failed for %s: %s",
                attempt + 1, MAX_RETRIES, image_path.name, e,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY * (attempt + 1))

    raise RuntimeError(
        f"OCR failed for {image_path.name} after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


def ocr_batch(
    image_paths: list[Path],
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    top_p: float = DEFAULT_TOP_P,
    timeout: int = DEFAULT_TIMEOUT,
    prompt: str = DEFAULT_OCR_PROMPT,
    progress_callback=None,
) -> list[dict]:
    """Run OCR on multiple images sequentially.

    Returns list of dicts: ``frame_index``, ``frame_name``, ``text``,
    ``path``, ``error``.
    """
    results: list[dict] = []
    total = len(image_paths)

    for idx, path in enumerate(image_paths):
        logger.info("OCR [%d/%d] %s", idx + 1, total, path.name)
        try:
            text = ocr_image(
                image_path=path,
                endpoint=endpoint,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                timeout=timeout,
                prompt=prompt,
            )
            results.append({
                "frame_index": idx,
                "frame_name": path.name,
                "path": str(path),
                "text": text,
                "error": None,
            })
        except Exception as e:
            logger.error("OCR failed for %s: %s", path.name, e)
            results.append({
                "frame_index": idx,
                "frame_name": path.name,
                "path": str(path),
                "text": "",
                "error": str(e),
            })

        if progress_callback:
            progress_callback(idx + 1, total)

    return results
