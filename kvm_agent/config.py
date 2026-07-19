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
    pico_ip: str = _env("PICO_IP", "192.168.0.224")  # LEGACY WiFi Pico (retired); see hid_kind
    pico_port: int = int(_env("PICO_PORT", "8000"))
    # --- HID action channel: 'appliance' = Pi 5 + Pico over wired UART + HTTP bridge
    #     (docs/PLAN_2026-07-18_pi5_pico_appliance.md); 'wifi' = the retired R4 WiFi Pico ---
    hid_kind: str = _env("HID_KIND", "appliance")
    appliance_url: str = _env("APPLIANCE_URL", "http://192.168.0.29:8080")  # Pi 5 hid_bridge
    cam_index: int = int(_env("CAM_INDEX", "0"))
    screen_w: int = int(_env("SCREEN_W", "1920"))
    screen_h: int = int(_env("SCREEN_H", "1080"))

    # --- target VM reset (flaw #7): libvirt snapshot revert + a forced cold reboot between
    #     battery tasks so each task starts from a byte-identical clean desktop AND with USB
    #     HID passthrough guaranteed working (warm revert alone reliably breaks HID -- see
    #     kvm_agent/hardware/vm.py's module docstring). ---
    vm_domain: str = _env("VM_DOMAIN", "win11-agent")
    vm_reset: bool = _env("VM_RESET", "1") != "0"        # revert to the clean snapshot per task
    vm_snapshot: str = _env("VM_SNAPSHOT", "clean-desktop")  # baseline snapshot name
    vm_revert_settle: float = float(_env("VM_REVERT_SETTLE", "8"))  # secs to settle after revert
    vm_boot_wait: float = float(_env("VM_BOOT_WAIT", "35"))  # secs for Windows to reach the
                                                              # desktop after the forced cold boot

    # --- model endpoints (laptop Ollama / OpenAI-compatible shim) ---
    # NOTE 2026-07-18: the laptop's IP is 192.168.0.76, not .155 (corrected by the user --
    # the old .155 default was stale/wrong). Ollama itself is no longer installed on the
    # laptop at all (moved to llama.cpp-server + LocalAI); these fields are kept for the
    # older EvoCUA/UI-TARS tooling (rig.py, live_ctl.py, tools/preflight.py) that still
    # references them, but kvm_agent.orchestration.executive.Verifier -- the grader the
    # CURRENT Holo battery actually uses -- was repointed at the local llama-swap instance
    # instead (verifier_model/verifier_local_url below), which needs no laptop at all.
    ollama_base: str = _env("OLLAMA_HOST", "http://192.168.0.76:11434")
    openai_base: str = _env("OPENAI_BASE_URL", "http://192.168.0.76:11434/v1")
    openai_key: str = _env("OPENAI_API_KEY", "ollama")

    # --- model NAMES (unchanged set; centralized, never swapped) ---
    executor_model: str = _env("EXECUTOR_MODEL", "uitars-q4")
    verifier_model: str = _env("VERIFIER_MODEL", "qwen2.5vl:7b")   # older Ollama-based pipeline only
    planner_model: str = _env("AGENT_PLANNER_MODEL", "Qwen/Qwen3-VL-30B-A3B-Thinking")

    # --- Holo battery's Verifier grading backend (kvm_agent.orchestration.executive) ---
    # local llama-swap (same host/server as holo_local_url below), NOT the laptop -- see the
    # NOTE above ollama_base. Grade with holo3.1 ITSELF: it is vision-capable and already
    # resident during a battery run, so grading costs ZERO model swaps. The 2026-07-18
    # battery run proved the alternative self-defeating -- with the verifier on gemma4-dense
    # (a 31B model that cannot share the B70 with holo3.1), llama-swap performed 16 full
    # model evictions/reloads in 45 minutes (one per grading call + one per task start),
    # inflating every first step to ~32s and every grading call to ~52s. Grading remains an
    # INDEPENDENT screen check: the verifier reads the final frame with a fresh prompt and
    # never sees the agent's own self-report. OCR (pytesseract) covers text graders with no
    # GPU at all. Override with VERIFIER_LOCAL_MODEL if a separate grader is ever wanted.
    verifier_local_model: str = _env("VERIFIER_LOCAL_MODEL", "holo3.1")
    verifier_max_tokens: int = int(_env("VERIFIER_MAX_TOKENS", "800"))

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
    # was a Windows path (r"C:\Dev\vllm\runs") from the pre-Holo topology where the rig
    # ran on the Windows target box; the rig is Linux-hosted now (see FINDINGS_integration.md)
    runs_dir: str = _env("RUNS_DIR", str(Path(__file__).resolve().parent.parent / "runs"))

    # --- verify ---
    tesseract_cmd: str = _env("TESSERACT_CMD", "")  # explicit tesseract.exe path; "" -> auto-discover

    # --- Holo3.1 grounding model (llama.cpp SYCL on the Arc Pro B70, port 9292;
    #     hosted reference API for local-vs-hosted diffing, per HOLO_INTEGRATION_PLAN.md) ---
    holo_local_url: str = _env("HOLO_LOCAL_URL", "http://127.0.0.1:9292/v1")
    holo_hosted_url: str = _env("HOLO_HOSTED_URL", "https://api.hcompany.ai/v1")
    holo_model: str = _env("HOLO_MODEL", "holo3.1")
    holo_hosted_model: str = _env("HOLO_HOSTED_MODEL", "holo3-1-35b-a3b")
    hai_api_key: str = _env("HAI_API_KEY", "")   # hosted Holo API credential ("" -> unset)
    # "goldfish memory" (2026-07-18): screenshots kept in the agent_loop_holo.py history, evicting
    # older frames to text. Default 1 (current behavior, unchanged) -- REPORT_2026-07-19_problems.md
    # flagged M3 (double-click/re-click on the same element) as plausibly a history-starvation
    # artifact, matching the EvoCUA-era precedent where history depth 1->2 fixed an identical
    # re-click pathology (SESSION notes, 2026-06-18 hard-ladder). Override for the A/B test.
    holo_history_images: int = int(_env("HOLO_HISTORY_IMAGES", "1"))
    # Model-input capture resolution. Default False = 720p downscale (2026-07-17: -35%
    # prompt tokens, measured with no grounding cost -- but that A/B only tested LARGE,
    # sparse targets (Start-menu icons). 2026-07-19: a calendar date-picker task showed
    # real coordinate misses (clicked "1980", landed on 1976; clicked day 8, landed on
    # day 21) that a native 1920x1080-fed run of the same model/task did not reproduce --
    # a day-cell shrinks from ~25-35px to ~17-23px at 720p in a dense 7-column grid. Test
    # via HOLO_MODEL_INPUT_FULL_RES=1 before making this the default (token-cost tradeoff).
    holo_model_input_full_res: bool = _env("HOLO_MODEL_INPUT_FULL_RES", "0") != "0"

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
