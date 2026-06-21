"""
cua_agent.py — backend selector for the rig's agents.

The harness contract every agent satisfies (duck-typed; EvoCUAAgent and UITARSAgent both do):
    agent.reset()
    text, actions = agent.predict(instruction, obs)   # obs["screenshot"] = PNG bytes
        -> actions: list[str] of pyautogui code and/or control tokens "WAIT"/"DONE"/"FAIL"/"ANSWER"
    agent.last_answer   # optional str the model surfaced (finished/answer), else None

`make_agent(backend, ...)` builds the right one with sane per-backend defaults so operate.py /
run_probe.py can switch with a single --backend flag. The EvoCUA construction here is copied
verbatim from operate.py's original call, so backend="evocua" is behaviorally unchanged.

Imports are lazy (inside the branches) so callers that set sys.path for the `evocua/` package
(operate.py does) are honored, and the UI-TARS path doesn't drag in EvoCUA deps or vice versa.
"""
from typing import Tuple

_EVOCUA_DEFAULT = "evocua-8b-q5-clean"
_UITARS_DEFAULT = "uitars-q4"


def make_agent(
    backend: str,
    *,
    model: str = None,
    history: int = 4,
    temperature: float = 0.01,
    max_tokens: int = 2048,
    top_p: float = 0.9,
    max_pixels: int = 16 * 16 * 4 * 12800,
    history_max_pixels: int = None,
    max_steps: int = 25,
    screen_size: Tuple[int, int] = (1920, 1080),
):
    backend = (backend or "evocua").lower()

    if backend == "evocua":
        from evocua_agent import EvoCUAAgent
        return EvoCUAAgent(
            model=model or _EVOCUA_DEFAULT, max_tokens=max_tokens, top_p=top_p,
            temperature=temperature, action_space="pyautogui", observation_type="screenshot",
            max_steps=max_steps, prompt_style="S2", max_history_turns=history,
            screen_size=screen_size, coordinate_type="relative", resize_factor=32,
            max_pixels=max_pixels, history_max_pixels=history_max_pixels,
        )

    if backend == "uitars":
        from uitars_agent import UITARSAgent
        return UITARSAgent(
            model=model or _UITARS_DEFAULT,
            max_tokens=min(max_tokens, 1024),   # UI-TARS replies are short (Thought + one Action)
            temperature=temperature, top_p=top_p,
            max_history_turns=history, screen_size=screen_size,
        )

    raise ValueError(f"unknown backend {backend!r} (expected 'evocua' or 'uitars')")
