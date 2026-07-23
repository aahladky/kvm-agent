"""Serving-layer introspection — what is ACTUALLY serving the model right now.

Why this exists (2026-07-23): the model server lives outside this repo
(llama-swap + modelctl, `~/services/llama-swap/config.yaml`), and the project had
no visibility into it at all. Two things that discipline is meant to catch, both
found by inspection the day this module was written:

1. **The serving config carries model-input knobs the project doesn't set.**
   holo3.1 is launched with `--image-min-tokens 1024` (a server-side FLOOR on image
   tokens) and `--cache-type-v q4_0`. `kvm_agent/config.py` documents a client-side
   resolution A/B (1080 vs 720: -24% prompt tokens, -33% wall) as though model input
   were ours alone to control. It isn't. `meta.json` already travels the system prompt
   with every run (second review #7, same reasoning) — it should travel these too, or
   the evidence trail describes only half of what the model saw.

2. **The model can be evicted out from under a run.** llama-swap decides residency
   with its `matrix:` solver, and holo3.1 is absent from it — reproduced live: one
   unrelated request to another model evicted holo3.1 (`['holo3.1']` -> `['fast-7b']`).
   The reload costs ~17s, and since the client timeout is 180s that surfaces as
   LATENCY, never an error. The archive shows no case of this yet (every >median+12s
   step in the recorded batteries is step 0, a cold load), but nothing would have
   noticed if there had been.

The discipline is `verify_hid`'s, one layer up: the config file says what the server
WOULD launch; `/running` says what it IS running. Prefer the observation over the
declaration -- the same reason the camera outranks the firmware's online flags.

Everything here is FAIL-SOFT. A serving probe that raises would be a new way to kill
a run, which is precisely the opposite of the point: an unreachable or unfamiliar
endpoint records `reachable: False` with the reason and the caller carries on.
Nothing in this module is required for the loop to work; it only makes the loop's
context legible.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from kvm_agent.config import CFG

# Flags worth capturing from a launch command: the ones that change what the model
# SEES or how much of it it can remember. Mapped to stable snapshot keys so a
# vLLM/OVMS/llama-server entry all normalize into the same shape where they overlap.
_CMD_FLAGS = {
    "--model": "model_path",
    "--mmproj": "mmproj_path",
    "-c": "ctx",
    "--ctx-size": "ctx",
    "--parallel": "parallel",
    "-ngl": "n_gpu_layers",
    "--n-gpu-layers": "n_gpu_layers",
    "--split-mode": "split_mode",
    "--tensor-split": "tensor_split",
    "--main-gpu": "main_gpu",
    "--cache-type-k": "cache_type_k",
    "--cache-type-v": "cache_type_v",
    "--image-min-tokens": "image_min_tokens",
    "--image-max-tokens": "image_max_tokens",
    "--flash-attn": "flash_attn",
    "--target-device": "target_device",     # OVMS-backed entries
    "--source-model": "source_model",       # OVMS-backed entries
}
_INT_KEYS = {"ctx", "parallel", "n_gpu_layers", "image_min_tokens", "image_max_tokens",
             "main_gpu"}


def parse_serving_cmd(cmd: str) -> dict:
    """Launch command -> the serving params that shape what the model sees.

    Pure and defensive: llama-swap stores the command as a shell string with escaped
    newlines, and entries differ per backend (llama-server vs an OVMS proxy). Unknown
    flags are ignored rather than guessed at; a flag whose value is missing is dropped
    rather than recorded as a lie.
    """
    if not cmd:
        return {}
    tokens = [t for t in cmd.replace("\\\n", " ").replace("\\", " ").split() if t]
    out: dict = {}
    for i, tok in enumerate(tokens):
        key = _CMD_FLAGS.get(tok)
        if not key:
            continue
        value = tokens[i + 1] if i + 1 < len(tokens) else None
        if value is None or value.startswith("-"):
            continue        # a flag with no value: record nothing rather than a wrong thing
        if key in _INT_KEYS:
            try:
                value = int(value)
            except ValueError:
                continue
        out[key] = value
    if "model_path" in out:
        out["model_file"] = out["model_path"].rsplit("/", 1)[-1]
        out["quant"] = _quant_from_filename(out["model_file"])
    out["has_mmproj"] = "mmproj_path" in out
    return out


def _quant_from_filename(name: str) -> str | None:
    """Best-effort GGUF quant tag ('Q4_K_M', 'IQ4_XS', ...) from a weights filename.
    None when the name doesn't carry one -- an OVMS/OpenVINO entry, say."""
    for part in name.replace(".gguf", "").split("."):
        p = part.upper()
        if p.startswith(("Q", "IQ", "BF", "F")) and any(c.isdigit() for c in p):
            return p
    return None


def _root(base_url: str | None = None) -> str:
    """llama-swap's admin root: CFG.holo_local_url without the OpenAI '/v1' suffix.
    /running and /v1/models live at different levels of the same server."""
    url = (base_url or CFG.holo_local_url).rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def _get_json(url: str, timeout_s: float):
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())


def serving_snapshot(model: str | None = None, base_url: str | None = None,
                     timeout_s: float = 3.0) -> dict:
    """What is serving `model` right now. Never raises.

    Keys: endpoint, model, reachable, configured (listed in /v1/models), resident
    (loaded per /running), state, ttl, params (parse_serving_cmd of the live command),
    co_resident (what ELSE is loaded -- the eviction-risk signal), error.

    `configured` and `resident` are deliberately separate: a model can be perfectly
    configured and simply not loaded, which costs a cold start (~17s for holo3.1) but
    is not a fault. Conflating them would either cry wolf or hide a real absence.
    """
    model = model or CFG.holo_model
    root = _root(base_url)
    snap = {"endpoint": root, "model": model, "reachable": False, "configured": None,
            "resident": None, "state": None, "ttl": None, "params": {},
            "co_resident": [], "error": None}
    try:
        models = _get_json(f"{root}/v1/models", timeout_s)
        ids = [m.get("id") for m in (models.get("data") or [])]
        snap["reachable"] = True
        snap["configured"] = model in ids
    except Exception as e:                      # noqa: BLE001 -- fail-soft by contract
        snap["error"] = f"{type(e).__name__}: {e}"
        return snap
    try:
        running = (_get_json(f"{root}/running", timeout_s).get("running") or [])
    except Exception as e:                      # noqa: BLE001
        # Reachable but no /running: a plain llama-server or another OpenAI-compatible
        # server. Residency is simply unknowable there -- say so, don't invent it.
        snap["error"] = f"no residency info: {type(e).__name__}: {e}"
        return snap
    snap["resident"] = False
    for entry in running:
        if entry.get("model") == model:
            snap["resident"] = True
            snap["state"] = entry.get("state")
            snap["ttl"] = entry.get("ttl")
            snap["params"] = parse_serving_cmd(entry.get("cmd") or "")
        else:
            snap["co_resident"].append(entry.get("model"))
    return snap


def describe(snap: dict) -> str:
    """One line for a boot/preflight log."""
    if not snap.get("reachable"):
        return f"UNREACHABLE {snap.get('endpoint')} ({snap.get('error')})"
    model = snap.get("model")
    if snap.get("configured") is False:
        return f"model {model!r} NOT CONFIGURED at {snap.get('endpoint')}"
    if snap.get("resident") is None:
        return f"model {model!r} configured; residency unknown ({snap.get('error')})"
    p = snap.get("params") or {}
    bits = [f"{model} {'resident' if snap['resident'] else 'NOT resident (cold start ahead)'}"]
    if snap["resident"]:
        for label, key in (("quant", "quant"), ("ctx", "ctx"), ("parallel", "parallel"),
                           ("image_min_tokens", "image_min_tokens"),
                           ("cache_v", "cache_type_v")):
            if p.get(key) is not None:
                bits.append(f"{label}={p[key]}")
        bits.append("mmproj=" + ("yes" if p.get("has_mmproj") else "NO"))
    if snap.get("co_resident"):
        bits.append(f"co-resident={','.join(str(m) for m in snap['co_resident'])}")
    return ", ".join(bits)
