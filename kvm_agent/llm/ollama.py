"""Shared model-endpoint plumbing (Ollama /api/generate + OpenAI /v1).

ONE reusable client instead of constructing one per call (PLAN P2). NOT yet wired
into verifier/uitars/planner - that swap is a separate, rig-tested step so the
package move stays behavior-identical. Endpoints/keys come from config.CFG.
"""
import json
import urllib.request
from functools import lru_cache

from kvm_agent.config import CFG


@lru_cache(maxsize=8)
def openai_client(base_url: str = None, api_key: str = None, timeout: float = 180.0):
    """Cached OpenAI-compatible client (one per distinct (base_url, key, timeout))."""
    import openai
    return openai.OpenAI(base_url=base_url or CFG.openai_base,
                         api_key=api_key or CFG.openai_key, timeout=timeout)


def ollama_generate(prompt: str, images=None, model: str = None, host: str = None,
                    timeout: float = 60.0, options: dict = None) -> str:
    """One-shot Ollama /api/generate call (used by the Verifier vision path)."""
    body = {"model": model or CFG.verifier_model, "prompt": prompt,
            "stream": False, "options": options or {"temperature": 0}}
    if images:
        body["images"] = images
    req = urllib.request.Request((host or CFG.ollama_base) + "/api/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout)).get("response", "").strip()
