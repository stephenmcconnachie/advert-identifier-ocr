"""vLLM API client with ensemble support."""

import asyncio
import json
from openai import OpenAI
from typing import Any


def create_vllm_client(
    base_url: str = "http://localhost:8000/v1",
    api_key: str = "EMPTY",
) -> OpenAI:
    """Create vLLM API client.
    
    Args:
        base_url: vLLM server URL.
        api_key: API key (often "EMPTY" for local servers).
        
    Returns:
        Configured OpenAI client.
    """
    return OpenAI(base_url=base_url, api_key=api_key)


async def call_vllm_single(
    client: OpenAI,
    video_url: str,
    prompt: str,
    model: str,
    fps: float,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Single VLM call (async).
    
    Args:
        client: OpenAI client.
        video_url: URL to video.
        prompt: Prompt text.
        model: Model name.
        fps: Sampling rate.
        
    Returns:
        Tuple of (response_text, error_message, raw_response_dict).
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "video_url", "video_url": {"url": video_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=60000,
            temperature=1.0,
            top_p=0.95,
            presence_penalty=1.5,
            extra_body={
                "top_k": 20,
                "min_p": 0.0,
                "repetition_penalty": 1.0,
                "chat_template_kwargs": {"enable_thinking": True},
                "mm_processor_kwargs": {"fps": fps, "do_sample_frames": True},
            },
        )
        message = response.choices[0].message
        
        # Capture both content and reasoning (for thinking mode)
        content = message.content or ""
        reasoning = getattr(message, "reasoning", None)
        
        if reasoning:
            # Combine reasoning and content (reasoning first, then final answer)
            full_response = f"[THINKING]\n{reasoning}\n\n[RESPONSE]\n{content}"
        else:
            full_response = content
        
        # Convert full response object to dict for debugging
        raw_response_dict = response.model_dump()
        
        return full_response, None, raw_response_dict
    except Exception as e:
        return None, str(e), None


async def call_vllm_ensemble(
    client: OpenAI,
    video_url: str,
    prompt: str,
    model: str,
    fps: float,
    ensemble_size: int = 5,
    ensemble_delay: float = 10.0,
) -> list[tuple[str | None, str | None, dict[str, Any] | None]]:
    """Run ensemble of VLM calls with delay between requests.
    
    Args:
        client: OpenAI client.
        video_url: URL to video.
        prompt: Prompt text.
        model: Model name.
        fps: Sampling rate.
        ensemble_size: Number of ensemble members (default: 5).
        ensemble_delay: Delay between requests in seconds (default: 10.0).
        
    Returns:
        List of (response_text, error, raw_response_dict) tuples.
    """
    results = []
    
    for i in range(ensemble_size):
        response, error, raw_dict = await call_vllm_single(client, video_url, prompt, model, fps)
        results.append((response, error, raw_dict))
        
        if i < ensemble_size - 1:
            await asyncio.sleep(ensemble_delay)
    
    return results


def run_ensemble_sync(
    client: OpenAI,
    video_url: str,
    prompt: str,
    model: str = "Qwen/Qwen3.5-4B",
    fps: float = 1.0,
    ensemble_size: int = 5,
    ensemble_delay: float = 10.0,
) -> list[tuple[str | None, str | None, dict[str, Any] | None]]:
    """Synchronous wrapper for ensemble calls.
    
    Args:
        client: OpenAI client.
        video_url: URL to video.
        prompt: Prompt text.
        model: Model name.
        fps: Sampling rate.
        ensemble_size: Number of ensemble members.
        ensemble_delay: Delay between requests.
        
    Returns:
        List of (response_text, error, raw_response_dict) tuples.
    """
    return asyncio.run(call_vllm_ensemble(
        client, video_url, prompt, model, fps, ensemble_size, ensemble_delay
    ))
