"""Per-run instrumentation for the Holo agent loop.

AGENTS.md §1 (all artifacts in runs/) and the 2026-07-18 harness review: "record every battery run: each step's frame, the model's
raw response, token usage (prompt_tokens/completion_tokens), wall time, and the action
dispatched... converts the battery into a permanent regression suite." This module is
the write side of that; analysis (grounding rate, completion-signaling rate,
steps-to-completion, latency distribution) reads the same files back later.

Layout: CFG.runs_dir/<tag>_<YYYYMMDD_HHMMSS>/
    meta.json      goal, target, started, and any caller-supplied config snapshot
    step_NN.png    the frame the model saw BEFORE deciding step NN's action
    step_NN.json   raw assistant message, parsed action, token usage, wall time, executed?
    summary.json   success, steps_taken, total wall time, per-step latency/token lists
"""
import json
import os
import time

from kvm_agent.config import CFG


class RunRecorder:
    def __init__(self, tag: str, goal: str, target: str = "local", meta: dict | None = None):
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(CFG.runs_dir, f"{tag}_{ts}")
        os.makedirs(self.dir, exist_ok=True)
        self.steps = []
        self._t_start = time.time()
        info = {"goal": goal, "target": target, "started": ts}
        if meta:
            info.update(meta)
        self._write_json("meta.json", info)
        print(f"[run_log] recording to {self.dir}")

    def _write_json(self, name: str, obj: dict):
        with open(os.path.join(self.dir, name), "w") as f:
            json.dump(obj, f, indent=2, default=str)

    def log_step(self, step_idx: int, png: bytes, message: dict, action: dict,
                 usage: dict | None, wall_time_s: float, executed: bool = True):
        with open(os.path.join(self.dir, f"step_{step_idx:02d}.png"), "wb") as f:
            f.write(png)
        record = {
            "step": step_idx,
            "message": message,
            "action": action,
            "usage": usage or {},
            "wall_time_s": wall_time_s,
            "executed": executed,
        }
        self._write_json(f"step_{step_idx:02d}.json", record)
        self.steps.append(record)

    def finish(self, success: bool, note: str = "") -> dict:
        summary = {
            "success": success,
            "note": note,
            "steps_taken": len(self.steps),
            "total_wall_time_s": time.time() - self._t_start,
            "per_step_wall_time_s": [s["wall_time_s"] for s in self.steps],
            "per_step_prompt_tokens": [s["usage"].get("prompt_tokens") for s in self.steps],
            "per_step_completion_tokens": [s["usage"].get("completion_tokens") for s in self.steps],
            "actions": [s["action"].get("action") for s in self.steps],
            "final_action": self.steps[-1]["action"] if self.steps else None,
        }
        self._write_json("summary.json", summary)
        print(f"[run_log] {'OK' if success else 'FAIL'} in {summary['steps_taken']} steps, "
              f"{summary['total_wall_time_s']:.1f}s -> {self.dir}/summary.json")
        return summary
