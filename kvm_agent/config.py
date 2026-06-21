"""Central configuration for the KVM-over-IP agent.

Every IP, port, endpoint, model name, and screen dim the rig uses lives HERE, and
every default equals the literal value the modules hardcoded before consolidation,
so importing through the package is behavior-identical. Override any field via the
matching environment variable (the modules already read these same env vars).
"""
import os
from dataclasses import dataclass


def _env(key, default):
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Config:
    # --- hardware: Pico HID injector + HDMI capture ---
    pico_ip: str = _env("PICO_IP", "192.168.0.183")
    pico_port: int = int(_env("PICO_PORT", "8000"))
    cam_index: int = int(_env("CAM_INDEX", "0"))
    screen_w: int = int(_env("SCREEN_W", "1920"))
    screen_h: int = int(_env("SCREEN_H", "1080"))

    # --- model endpoints (laptop Ollama / OpenAI-compatible shim) ---
    ollama_base: str = _env("OLLAMA_HOST", "http://192.168.0.155:11434")
    openai_base: str = _env("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
    openai_key: str = _env("OPENAI_API_KEY", "ollama")

    # --- model NAMES (unchanged set; centralized, never swapped) ---
    executor_model: str = _env("EXECUTOR_MODEL", "uitars-q4")
    verifier_model: str = _env("VERIFIER_MODEL", "qwen2.5vl:7b")
    planner_model: str = _env("AGENT_PLANNER_MODEL", "Qwen/Qwen3-VL-8B-Instruct")

    # --- verify ---
    tesseract_cmd: str = _env("TESSERACT_CMD", "")  # explicit tesseract.exe path; "" -> auto-discover

    @property
    def screen_size(self):
        return (self.screen_w, self.screen_h)


CFG = Config()
