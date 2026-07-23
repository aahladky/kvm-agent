"""Model-neutral seam (roadmap Phase 1, docs/ROADMAP.md Part 3 Slice C): the loop
speaks this vocabulary, never Holo's native chat-mapper layout directly.

Deliberately TWO methods, not three: `decide` / `commit`. Holo3.1 fuses propose+ground
into one structured-output call, and no verify() call exists yet anywhere in this
harness (the battery's graders are the human operator -- PROJECT_STATE.md); the
roadmap's `verify()` joins this Protocol only in Phase 2 alongside a real postcondition
oracle. Adding a stub verify() now would be a decorative extra method with no
implementation to seal behind it -- the opposite of "seal the seam."

A conforming session owns everything model-specific: history, the observation/
tool_output message shapes, image trim, data-URL encoding, and the map from our
normalized action kinds to whatever tool-name vocabulary the model's protocol uses.
The harness (agent_loop_holo.py) only ever sees StepDecision.actions/note/thought and
calls tool_name()/commit() -- it never touches history or message construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class StepDecision:
    """One step's parsed proposal, plus everything commit() needs to thread it into
    session history afterward.

    `step` is the SAME mutable dict `parse_response`/call_holo_full produces
    ({"actions": [...], "note": ..., "thought": ..., possibly "error"/"raw"/"warnings"})
    -- kept as one object, not copied, because the harness attaches warnings to it
    during execution (guard refusals, no-op notices) and the recorder logs that same
    object. `data_url` and `instruction` are cached from the decide() call so commit()
    doesn't need them passed again.
    """
    step: dict
    message: dict
    usage: dict
    data_url: str
    instruction: str

    @property
    def actions(self) -> list[dict]:
        return self.step.get("actions", [])

    @property
    def note(self):
        return self.step.get("note")

    @property
    def thought(self):
        return self.step.get("thought")

    @property
    def error(self):
        return self.step.get("error")


@runtime_checkable
class ModelSession(Protocol):
    """One implementation per model. The harness constructs one per run() and talks
    to it exclusively through this Protocol -- stubbing a second implementation
    (roadmap Phase 1's gate) means writing a class that satisfies this shape, no loop
    changes."""

    def decide(self, data_url: str, w: int, h: int, instruction: str) -> StepDecision:
        """One step: build the request from (session history + this observation),
        call the model, parse the response. Does NOT mutate session history -- that
        only happens in commit(), so a step the harness decides not to thread (a
        parse failure) never touches history."""
        ...

    def commit(self, decision: StepDecision, results: list[tuple[str, str]]) -> None:
        """Thread a decided-and-executed step into session history: this step's own
        observation, the model's raw assistant turn, then one tool-result entry per
        `results` pair (tool_name, result_text) in execution order. Called for every
        step the harness didn't drop at parse time -- including guard-refused and
        exec-error steps, whose partial `results` list is exactly what the model
        needs to see to stop repeating the rejected action."""
        ...

    def tool_name(self, action_kind: str) -> str:
        """Map our normalized action kind (e.g. "left_click") to this model's own
        tool-name vocabulary (e.g. "click_desktop"), for building `results` entries."""
        ...

    def reset(self) -> None:
        """Clear session history. Called at the start of every run() -- a battery
        reboots the target between tasks, so history from the previous task is wrong
        by definition (same reasoning as CURSOR/PLAN resetting, agent_loop_holo.py)."""
        ...
