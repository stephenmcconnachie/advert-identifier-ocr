#!/usr/bin/env python3
"""OCR API client for vLLM-hosted OCR models.

Sends individual image frames to a vLLM endpoint using the OpenAI chat
completions format with base64-encoded image data.  Mirrors the pattern
from ``src/ad_break_identifier/api_client.py`` but for image-based OCR
instead of video-based VLM.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger("ocr_api_client")

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
}

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
DEFAULT_MODEL = "lightonai/LightOnOCR-2-1B"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_P = 0.9
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_DELAY = 2


# ── Helpers ─────────────────────────────────────────────────────────────


def _get_mime_type(image_path: Path) -> str:
    """Derive MIME type from file extension."""
    return MIME_TYPE_MAP.get(image_path.suffix.lower(), "image/png")


def _encode_image(image_path: Path) -> str:
    """Read an image file and return its base64-encoded content."""
    return base64.b64encode(image_path.read_bytes()).decode("utf-8")


# ── Single-frame OCR ────────────────────────────────────────────────────


def ocr_image(
    image_path: Path,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    top_p: float = DEFAULT_TOP_P,
    timeout: int = DEFAULT_TIMEOUT,
    system_prompt: str | None = None,
) -> str:
    """Send a single image frame to the OCR model and return extracted text.

    Args:
        image_path: Path to the image file.
        endpoint: vLLM chat completions URL.
        model: OCR model name.
        max_tokens: Max tokens in the response.
        temperature: Sampling temperature (low = more deterministic).
        top_p: Nucleus sampling parameter.
        timeout: HTTP request timeout in seconds.
        system_prompt: Optional system prompt for context.

    Returns:
        Extracted text from the image.

    Raises:
        FileNotFoundError: If image_path does not exist.
        ValueError: If the image format is unsupported.
        RuntimeError: If the API call fails after retries.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    ext = image_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported extension %s, trying PNG", ext)

    image_b64 = _encode_image(image_path)
    mime_type = _get_mime_type(image_path)

    messages: list[dict] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{image_b64}",
                },
            },
        ],
    })

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
            response = requests.post(
                endpoint,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()

            text = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            logger.debug(
                "OCR %s → %d chars", image_path.name, len(text)
            )
            return text

        except requests.RequestException as e:
            last_error = e
            logger.warning(
                "OCR attempt %d/%d failed for %s: %s",
                attempt + 1,
                MAX_RETRIES,
                image_path.name,
                e,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))

    raise RuntimeError(
        f"OCR failed for {image_path.name} after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


# ── Batch OCR ────────────────────────────────────────────────────────────


def ocr_batch(
    image_paths: list[Path],
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    top_p: float = DEFAULT_TOP_P,
    timeout: int = DEFAULT_TIMEOUT,
    system_prompt: str | None = None,
    progress_callback=None,
) -> list[dict]:
    """Run OCR on multiple images sequentially.

    Each result dict has keys: ``frame_index``, ``frame_name``, ``text``,
    ``path``, ``error`` (or ``None`` on success).

    Args:
        image_paths: Sorted list of image paths.
        endpoint: vLLM chat completions URL.
        model: OCR model name.
        max_tokens: Max tokens in the response.
        temperature: Sampling temperature.
        top_p: Nucleus sampling parameter.
        timeout: HTTP request timeout in seconds.
        system_prompt: Optional system prompt.
        progress_callback: Optional ``callable(current, total)``.

    Returns:
        List of result dicts in frame order.
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
                system_prompt=system_prompt,
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


# ── CLI test harness ─────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Test OCR on a single image via vLLM"
    )
    parser.add_argument("image", type=str, help="Path to image file")
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"vLLM endpoint (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OCR model (default: {DEFAULT_MODEL})",
    )

    args = parser.parse_args()
    img_path = Path(args.image)

    if not img_path.exists():
        print(f"ERROR: {img_path} not found")
        sys.exit(1)

    print(f"OCR: {img_path}")
    print(f"Model: {args.model}")
    print(f"Endpoint: {args.endpoint}")
    print("─" * 50)

    try:
        text = ocr_image(
            image_path=img_path,
            endpoint=args.endpoint,
            model=args.model,
        )
        print(text)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
