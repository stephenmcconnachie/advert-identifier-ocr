"""OCR API client for vLLM-hosted PaddleOCR-VL model.

Sends individual image frames to a vLLM endpoint using the OpenAI chat
completions format with base64-encoded image data and an "OCR:" text prompt.
"""

from __future__ import annotations

import base64
import concurrent.futures
import logging
import threading
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
    session: requests.Session | None = None,
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
            http = session.post if session else requests.post
            response = http(endpoint, json=payload, timeout=timeout, allow_redirects=False)
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


def _ocr_single_worker(
    idx: int,
    path: Path,
    session: requests.Session,
    semaphore: threading.Semaphore,
    endpoint: str,
    model: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    timeout: int,
    prompt: str,
) -> dict:
    """Run OCR on a single frame inside a thread pool worker."""
    with semaphore:
        try:
            text = ocr_image(
                image_path=path,
                session=session,
                endpoint=endpoint,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                timeout=timeout,
                prompt=prompt,
            )
            return {
                "frame_index": idx,
                "frame_name": path.name,
                "path": str(path),
                "text": text,
                "error": None,
            }
        except Exception as e:
            logger.error("OCR failed for %s: %s", path.name, e)
            return {
                "frame_index": idx,
                "frame_name": path.name,
                "path": str(path),
                "text": "",
                "error": str(e),
            }


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
    max_workers: int = 4,
) -> list[dict]:
    """Run OCR on multiple images concurrently using a thread pool.

    Uses ``concurrent.futures.ThreadPoolExecutor`` to parallelise HTTP
    requests and a ``requests.Session`` for TCP connection reuse.  A
    semaphore caps the number of in-flight requests to ``max_workers``
    to avoid overwhelming the vLLM server.

    Args:
        image_paths: List of paths to image frames.
        endpoint: vLLM OCR endpoint URL.
        model: OCR model name.
        max_tokens: Maximum tokens in OCR response.
        temperature: Sampling temperature (0.0 = deterministic).
        top_p: Nucleus sampling parameter.
        timeout: HTTP request timeout in seconds.
        prompt: Text prompt sent alongside each image.
        progress_callback: Optional ``callable(current, total)``.
        max_workers: Maximum concurrent OCR requests (default 4).

    Returns:
        List of dicts sorted by ``frame_index``, each with keys:
        ``frame_index``, ``frame_name``, ``path``, ``text``, ``error``.
    """
    total = len(image_paths)
    if total == 0:
        return []

    semaphore = threading.Semaphore(max_workers)
    with requests.Session() as session:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: dict[concurrent.futures.Future, int] = {}
            for idx, path in enumerate(image_paths):
                future = executor.submit(
                    _ocr_single_worker,
                    idx, path, session, semaphore,
                    endpoint, model, max_tokens,
                    temperature, top_p, timeout, prompt,
                )
                futures[future] = idx

            results: list[dict] = [None] * total
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

    return results
