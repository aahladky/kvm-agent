"""Shared OpenAI-compatible client factory.

One cached client per (base_url, key, timeout) instead of constructing one per call.
The only live consumer is kvm_agent.models.holo (holo.py:457, which passes base_url
and api_key explicitly). The Ollama /api/generate helper this module also carried was
archived 2026-07-20 with the Ollama-based verifier; the filename survives to keep the
import path stable.
"""
import os
from functools import lru_cache


@lru_cache(maxsize=8)
def openai_client(base_url: str = None, api_key: str = None, timeout: float = 180.0):
    """Cached OpenAI-compatible client (one per distinct (base_url, key, timeout))."""
    import openai
    return openai.OpenAI(base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
                         api_key=api_key or os.environ.get("OPENAI_API_KEY", "unused"),
                         timeout=timeout)
