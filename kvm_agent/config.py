"""Central configuration for the KVM-over-IP agent.

Every IP, port, endpoint, model name, and screen dim the rig uses lives HERE, and
every default equals the literal value the modules hardcoded before consolidation,
so importing through the package is behavior-identical. Override any field via the
matching environment variable (the modules already read these same env vars).
"""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_local_env():
    """Load KEY=VALUE lines from .env.local (repo root, gitignored) into os.environ,
    without overriding anything already set. Keeps secrets (e.g. ANTHROPIC_API_KEY)
    out of source while requiring no new dependency."""
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
    # --- hardware: Pico HID injector + HDMI capture ---
    pico_ip: str = _env("PICO_IP", "192.168.0.224")  # was .183; drifted once already (DHCP, not reserved) — see FINDINGS_integration.md
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
    planner_model: str = _env("AGENT_PLANNER_MODEL", "Qwen/Qwen3-VL-30B-A3B-Thinking")

    # --- planner (server-side orchestration brain) ---
    planner_kind: str = _env("AGENT_PLANNER", "local")           # hf | claude | rule | local
    planner_local_url: str = _env("AGENT_PLANNER_BASE_URL", "http://127.0.0.1:8090/v1")  # 'local': B580 llama-server (port 8090, NOT 8080 — Docker/SearXNG binds 127.0.0.1:8080 specifically and shadows llama-server's 0.0.0.0:8080 on loopback; serve llama-server on 127.0.0.1:8090)
    send_image: bool = _env("AGENT_SEND_IMAGE", "1") != "0"   # send the screenshot to the planner
    anthropic_key: str = _env("ANTHROPIC_API_KEY", "")        # ClaudePlanner credential; set via env or .env.local ("" -> unset)
    planner_thinking: bool = _env("AGENT_PLANNER_THINKING", "0") != "0"  # reasoning mode (<think>…</think>)
    planner_max_tokens: int = int(_env("AGENT_PLANNER_MAX_TOKENS", "0")) # 0 -> auto (see property)

    # --- closed-loop execution (per-step observe->act, vs the default decompose-then-run-blind) ---
    closed_loop: bool = _env("AGENT_CLOSED_LOOP", "1") != "0"            # opt-in; default = run_goal
    closed_loop_max_steps: int = int(_env("AGENT_CLOSED_LOOP_MAX_STEPS", "16"))

    # --- orchestration / IO ---
    runs_dir: str = _env("RUNS_DIR", r"C:\Dev\vllm\runs")     # per-run frames + JSON logs

    # --- verify ---
    tesseract_cmd: str = _env("TESSERACT_CMD", "")  # explicit tesseract.exe path; "" -> auto-discover

    # --- Holo3.1 grounding model (llama.cpp SYCL on the Arc Pro B70, port 9292;
    #     hosted reference API for local-vs-hosted diffing, per HOLO_INTEGRATION_PLAN.md) ---
    holo_local_url: str = _env("HOLO_LOCAL_URL", "http://127.0.0.1:9292/v1")
    holo_hosted_url: str = _env("HOLO_HOSTED_URL", "https://api.hcompany.ai/v1")
    holo_model: str = _env("HOLO_MODEL", "holo3.1")
    holo_hosted_model: str = _env("HOLO_HOSTED_MODEL", "holo3-1-35b-a3b")
    hai_api_key: str = _env("HAI_API_KEY", "")   # hosted Holo API credential ("" -> unset)

    # --- memory (Hindsight semantic memory server: vectorize-io/hindsight) ---
    hindsight_url: str = _env("HINDSIGHT_URL", "http://192.168.0.184:8888")
    hindsight_bank: str = _env("HINDSIGHT_BANK", "TARS")
    hindsight_enabled: bool = _env("AGENT_HINDSIGHT", "1") != "0"  # opt-in; preserves no-memory A/B baseline
    hindsight_write: bool = _env("AGENT_HINDSIGHT_WRITE", "0") != "0"  # write-back: retain the recipe on success

    @property
    def screen_size(self):
        return (self.screen_w, self.screen_h)

    @property
    def planner_effective_max_tokens(self) -> int:
        """Output budget for the planner. Explicit AGENT_PLANNER_MAX_TOKENS wins; otherwise
        auto: a reasoning model needs a large budget so its <think> trace doesn't crowd out the
        JSON plan (4000 was fine for the non-thinking Instruct model, far too small once the
        model thinks). 'thinking' in the model name (the dedicated Qwen3-VL-*-Thinking
        checkpoint) auto-bumps even without the flag."""
        if self.planner_max_tokens > 0:
            return self.planner_max_tokens
        thinking = self.planner_thinking or "thinking" in self.planner_model.lower()
        return 16000 if thinking else 4000


CFG = Config()
