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
    # Screenshots kept in the agent_loop_holo.py history, older frames evicted to text.
    # Default 3 = native holo-desktop-cli's max_images (docs/native/*.yaml, recovered
    # 2026-07-21; hub.hcompany.ai/agent-loop warns MORE than 3 degrades accuracy). The
    # 2026-07-18 "goldfish memory" default of 1 was a token-saving deviation from native;
    # retired on the native-verbatim line. Override for A/B tests.
    holo_history_images: int = int(_env("HOLO_HISTORY_IMAGES", "3"))
    # Model-input resolution (the height the capture is scaled to before sending to the
    # model). Default 1080 = native behavior (full-res JPEG, clamped at 1920w). 720 = the
    # old downscale: live A/B 2026-07-21 at history=3 (tools/probe_resolution_ab.py):
    # 8,988 vs 11,883 prompt tok/step (-24%), ~9.1s vs ~13.6s steady-state wall/step
    # (-33%) -- but real coordinate misses on dense UIs at 720p (2026-07-19). Replaces
    # the old HOLO_MODEL_INPUT_FULL_RES bool.
    holo_model_input_res: int = int(_env("HOLO_MODEL_INPUT_RES", "1080"))

    @property
    def screen_size(self):
        return (self.screen_w, self.screen_h)

    @property
    def logs_dir(self) -> str:
        """Wire-level request/response logs (holo_requests.jsonl) -- artifacts, so they
        live under runs/ (AGENTS.md §1), which is already gitignored."""
        return _env("LOGS_DIR", os.path.join(self.runs_dir, "logs"))


CFG = Config()
