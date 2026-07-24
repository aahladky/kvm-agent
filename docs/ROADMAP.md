# Roadmap — kvm-agent

_Current direction: 2026-07-23._

## Goal

Build a dependable local computer-use agent across a real HDMI/USB boundary while
keeping the implementation small enough to inspect end to end. The system earns
additional autonomy only when the current layer is observable and fails loudly.

## Design rules

- The camera is the authority for target state; transport ACKs prove delivery only.
- Capture freshness, prompt construction, parsing, action delivery, and verification
  remain separately inspectable.
- The actor proposes actions. The harness enforces safety and evidence boundaries but
  does not invent task actions or retries.
- Perceptual checks are stateless `(frame, narrow question) → answer` calls.
- Terminal and subgoal progression fail closed on false or unanswered verification.
- Use focused deterministic gates for integration. Real tasks answer explicit
  capability questions; broad batteries are not standing rituals.
- A maintenance feature needs a recurring observed trigger. A model failure does not
  need to be reproduced for hours before a bounded architectural experiment can begin.
- New control-flow generations are forbidden. Extend the existing loop or archive its
  predecessor in the same commit.

## Phase status

| Phase | State | Next decision |
|---|---|---|
| 0 — capture/HID primitives | Functionally validated; long-idle soak postponed | Run the soak only when the rig is convenient or long-idle death recurs. |
| 1 — model seam | Complete | Add a model only behind the existing session contract. |
| 2 — independent verification | D-a/D-b/D-c complete; D-d next | Add the verified subgoal unit as a bounded A/B. |
| 3 — hierarchical working memory | Not started | Start only after real subgoal boundaries exist and context degradation is measured. |
| 4 — oversight dial/macros | Not started | Requires a reliable subgoal unit and per-type verifier data. |
| 5 — multi-model decomposition | Serving co-residency ready; model-fit unmeasured | Measure grounding/latency before enrolling another model. |
| 6 — external tools | Not started | Add one scoped tool only when a selected task requires it. |

## Immediate sequence

### 1. Keep evidence self-contained

Every recorded loop run owns:

- exact pre-decision frames;
- raw assistant messages and parsed actions;
- exact tool-output text returned to the actor;
- host-observed HTTP/Pico responses for every HID request;
- actor and verifier requests/responses;
- timings, token usage, serving/input configuration, verdicts, and summary.

Routine tests use `python -B tools/run_tests.py`; Pico firmware builds place all output
under `runs/pico_fw_build_<timestamp>/`. Hidden session, cache, dependency, and build
directories are not valid project state.

### 2. Run the smallest affected gate

| Change | Required check |
|---|---|
| Ordinary deterministic code | Focused tests, then `python -B tools/run_tests.py`. |
| Prompt, image preparation, model adapter, parser, history, termination | Offline suite plus `tools/model_contract_smoke.py`. |
| Capture, projection, HID, focus, closed-loop control | Offline suite, live model contract, then `tools/physical_calibration_smoke.py`. |
| Real application capability | One task selected for that exact claim with complete run evidence. |

Turn escaped integration defects into minimal fixtures only when the existing
four-frame/one-physical set could not expose them.

### 3. Build D-d without an open-ended failure hunt

The mechanism is settled: use an explicit planner call because native `update_plan`
was never emitted in recorded long tasks.

D-d stays inside `agent_loop_holo.py` and adds:

1. a short plan of subgoals with visible postconditions;
2. one active subgoal at a time;
3. verifier-gated subgoal completion;
4. active-subgoal instruction scoping for the actor;
5. per-subgoal step budgets and stuck/guard/refusal counters; and
6. an abort that names the failed subgoal.

The pre-build discovery probe is at most three 12-step tasks in
`tools/battery_tasks_d_d_trigger.json`, run individually with `--task-id`. Stop after
the first confident-wrong intermediate transition. If all three are clean, proceed;
the probe has reached its cost cap.

Acceptance:

- deterministic tests cover planning failure, false/unanswered subgoal verdicts,
  refusal-to-advance, budget exhaustion, and evidence recording;
- `verify_mode="off"` retains its existing transcript/return compatibility;
- the same three tasks run flat-loop versus D-d with identical task/reset conditions;
- no completion or terminal-verification regression is accepted;
- every transition can be reconstructed from its frame, postcondition, actor output,
  verifier output, and control decision;
- if active-subgoal instruction scoping regresses behavior, retain the plan as a pure
  sequencing/verification gate and leave the actor instruction stream unchanged.

Recovery remains abort-only in D-d. Demotion/escalation belongs to Phase 4; memory
chunking belongs to Phase 3.

### 4. Use the system

After D-d, select bounded useful work. Promote only recurring failure patterns:

- context loss across verified subgoals → Phase 3 memory;
- repeatable low-risk subgoal with reliable verification → Phase 4 manager/macro mode;
- grounding dominates failures and B580 meets measured fit/latency → Phase 5;
- a task needs external read-only information → one scoped Phase 6 tool.

## Triggered maintenance

- Overnight HID soak: when the rig can be spared or long-idle mouse death recurs.
- Power control: when manual full shutdown/boot repeatedly blocks evaluation.
- Bridge keep-alive: only if the deployed suspend fix still fails.
- Timed drag: when a selected app proves teleport drag insufficient.
- Multi-monitor pointing: when a selected target requires it.

These are not prerequisites for D-d.
