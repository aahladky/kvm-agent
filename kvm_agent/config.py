"""Central configuration for the KVM-over-IP agent.

Every IP, port, endpoint, model name, and screen dim the rig uses lives HERE.
Override any field via the matching environment variable.

2026-07-20: the retired stack's fields (WiFi Pico, libvirt VM, Ollama verifier,
planner, hindsight, closed-loop, tesseract) were removed in the physical-target
sweep — their only consumers are in _archive/old-stack/. History: git log.
"""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_local_env():
    """Load KEY=VALUE lines from .env.local (repo root, gitignored) into os.environ,
    without overriding anything already set. Keeps secrets (e.g. HAI_API_KEY) out of
    source while requiring no new dependency."""
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_local_env()


def _env(key, default):
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Config:
    # --- HID action channel: Pi 5 + Pico over wired UART + HTTP bridge ---
    appliance_url: str = _env("APPLIANCE_URL", "http://192.168.0.29:8080")  # Pi 5 hid_bridge
    cam_index: int = int(_env("CAM_INDEX", "0"))
    screen_w: int = int(_env("SCREEN_W", "1920"))
    screen_h: int = int(_env("SCREEN_H", "1080"))

    # --- orchestration / IO: all run artifacts live under runs/ (AGENTS.md §1) ---
    runs_dir: str = _env("RUNS_DIR", str(Path(__file__).resolve().parent.parent / "runs"))

    # --- Holo3.1 grounding model (llama.cpp SYCL on the Arc Pro B70, port 9292;
    #     hosted reference API for local-vs-hosted diffing) ---
    holo_local_url: str = _env("HOLO_LOCAL_URL", "http://127.0.0.1:9292/v1")
    holo_hosted_url: str = _env("HOLO_HOSTED_URL", "https://api.hcompany.ai/v1")
    holo_model: str = _env("HOLO_MODEL", "holo3.1")
    holo_hosted_model: str = _env("HOLO_HOSTED_MODEL", "holo3-1-35b-a3b")
    hai_api_key: str = _env("HAI_API_KEY", "")   # hosted Holo API credential ("" -> unset)
    # "goldfish memory" (2026-07-18): screenshots kept in the agent_loop_holo.py history,
    # evicting older frames to text. Each kept screenshot re-pays its vision tokens on
    # EVERY step, and text history already carries the narrative.
    holo_history_images: int = int(_env("HOLO_HISTORY_IMAGES", "1"))
    # Model-input capture resolution. Default False = 720p downscale (-35% prompt tokens,
    # measured 2026-07-17 with no grounding cost on LARGE, sparse targets). 2026-07-19: a
    # dense calendar date-picker showed real coordinate misses at 720p that a native
    # 1080p-fed run did not reproduce -- test via HOLO_MODEL_INPUT_FULL_RES=1 on dense UIs.
    holo_model_input_full_res: bool = _env("HOLO_MODEL_INPUT_FULL_RES", "0") != "0"

    @property
    def screen_size(self):
        return (self.screen_w, self.screen_h)


CFG = Config()
