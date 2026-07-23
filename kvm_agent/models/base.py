"""Model-neutral seam (roadmap Phase 1, docs/ROADMAP.md Part 3 Slice C): the loop
speaks this vocabulary, never Holo's native chat-mapper layout directly.

Deliberately TWO methods, not three: `decide` / `commit`. Holo3.1 fuses propose+ground
into one structured-output call, and no verify() call existed anywhere in this harness
when the seam landed (the battery's graders were the human operator -- PROJECT_STATE.md).
Adding a stub verify() then would have been a decorative extra method with no
implementation to seal behind it -- the opposite of "seal the seam."

[correction 2026-07-22, Phase 2 slice D-a] That paragraph used to promise `verify()`
would join THIS Protocol in Phase 2. It doesn't, and the reason is the roadmap's own
tier split (docs/ROADMAP.md §3): the planner is the stateful tier, the grounder/verifier
are "pure perceptual functions -- (frame, narrow question) -> answer, no memory, no task
awareness." Verification is therefore a SEPARATE Protocol (`Verifier`, below) held by a
separate injected object, not a method on the stateful session:

  - statelessness is the property being bought. A verify() hanging off the object that
    owns conversation history is one careless line away from reading that history, and
    then the oracle is judging its own actor's story instead of the pixels.
  - Phase 5 wants the verifier on a different card (B580) or a different model entirely.
    A separate injectable object relocates by swapping one constructor argument; a method
    on HoloSession would have to be torn back out first.

So the seam is now two Protocols with one rule each: `ModelSession` acts and remembers,
`Verifier` looks and answers. `agent_loop_holo.run()` takes both (`session=`, `verifier=`).

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


@dataclass
class Verdict:
    """One postcondition check's answer.

    `satisfied` is deliberately `bool | None`, NOT `bool`. None means the oracle did not
    answer -- model error, timeout, unparseable response -- and it is a THIRD outcome, not
    a False and never a True. Every consumer must handle it explicitly:

      - the loop records it loudly and does not treat it as a passed postcondition
      - the battery grades it fail, never pass

    This is finding #8's rule ("fail-open grading is the anti-pattern this project exists
    to kill", tools/battery.py) applied to the oracle itself: an oracle that silently
    answers True when it is actually broken is worse than no oracle, because it launders
    a missing check into a passing one.

    `evidence` is the oracle's own justification -- what it claims to see on screen. It
    is what makes a wrong verdict auditable after the fact, so it is recorded whether the
    verdict is True, False, or None (on None it carries the error text).
    """
    satisfied: bool | None
    evidence: str
    raw: dict
    usage: dict
    wall_time_s: float

    @property
    def answered(self) -> bool:
        """False when the oracle failed to answer (satisfied is None)."""
        return self.satisfied is not None

    def to_dict(self) -> dict:
        """The compact, JSON-safe record kept alongside a run (RunRecorder step/summary
        entries, tools/battery.py's results row). Deliberately excludes `raw` (the full
        model message -- already captured in kvm_agent.models.holo.REQUEST_LOG, tagged
        kind="verify"; duplicating it here would just be a second, driftable copy)."""
        return {"satisfied": self.satisfied, "evidence": self.evidence,
                "wall_time_s": round(self.wall_time_s, 2), "usage": self.usage}


@runtime_checkable
class Verifier(Protocol):
    """A postcondition oracle: (frame, question) -> Verdict. One call, no memory.

    A conforming verifier MUST be stateless across calls -- no conversation history, no
    task awareness, no knowledge of what the actor did or intended. It sees pixels and a
    narrow question, and answers about the pixels. That is the whole contract, and it is
    what lets the same object serve two different consumers (the loop's progression gate
    and the battery's grader) and later move to another card (roadmap Phase 5).

    A conforming verifier MUST NOT raise for a model-side failure: it returns
    `Verdict(satisfied=None, evidence=<why>)` instead. Callers gate on `satisfied`, and a
    raise would force every call site to grow its own try/except with its own opinion
    about what a failure means -- exactly the divergence `satisfied=None` exists to
    prevent.
    """

    def check(self, data_url: str, w: int, h: int, question: str,
              claim: str = "") -> Verdict:
        """Answer one question about one frame.

        data_url: the frame, encoded as the model expects (JPEG data URL).
        w, h:     the projection basis (real screen pixels), for parity with decide().
        question: the postcondition, in plain language -- a task instruction or a
                  subgoal's success criterion.
        claim:    OPTIONAL. What the actor asserted, e.g. the `answer` tool's text.
                  Some postconditions are only checkable against the claim (the battery's
                  top_bar_clock answers with a time that must match the clock on screen);
                  others are pure screen state and ignore it. Passed as data to be
                  judged, never as an instruction to be obeyed -- the epistemic firewall
                  (docs/ROADMAP.md §3): the claim tells you what to look for, the pixels
                  tell you whether it is there.
        """
        ...
