# SESSION 2026-07-22 — Roadmap Phase 1: the model seam (Slice C)

## What this session was

Slice C from `docs/PLAN_2026-07-22_roadmap_alignment_slices.md` Part 3: the
roadmap's Phase 1 ("seal the model seam, no new model"). Pure refactor, fully
offline, no rig time — the other pending item (Slice B, firmware Phase 0
hardening) needs BOOTSEL flashing + an overnight soak and stays on its own
branch for when the operator is at the rig.

## Changes

- **`kvm_agent/models/base.py`** (new): the model-neutral contract the loop now
  speaks. `StepDecision` (actions/note/thought/error as properties over the same
  mutable `step` dict `parse_response` already produces — kept as one object, not
  copied, because the harness attaches `warnings` to it during execution and the
  recorder logs that same object) plus `ModelSession`, a `runtime_checkable`
  Protocol with `decide()` / `commit()` / `tool_name()` / `reset()`. Deliberately
  two methods, not three: Holo fuses propose+ground into one structured-output
  call, and no `verify()` exists anywhere yet (the battery's graders are the
  human operator) — `verify()` joins the Protocol in Phase 2 alongside a real
  postcondition oracle, not before.
- **`kvm_agent/models/holo.py`**: `HoloSession(ModelSession)` — owns conversation
  history, `<observation>`/`<tool_output>` message construction, image trim, and
  the normalized-action-kind → native-tool-name map (`ACTION_TO_TOOL_NAME`, moved
  out of the loop, where it was an inline dict literal). `call_fn` is
  constructor-injected (defaults to `call_holo_full`) specifically so
  `agent_loop_holo.run()` can pass its OWN `call_holo_full` module-global each
  call — Python resolves that bare name at call time, so the existing tests that
  do `al.call_holo_full = fake` keep working through the session with zero test
  changes.
- **`agent_loop_holo.py`**: `run()` talks only to a `ModelSession` — `session.decide()`
  replaces the direct `call_holo_full(...)` call, `session.commit(decision, results)`
  replaces the manual `history.append(observation_message(...))` /
  `history.append({"role":"assistant",...})` / `history.extend(tool_outputs)` /
  `trim_to_last_n_images(...)` block, and `session.tool_name(kind)` replaces the
  inline tool-name dict. `run()` gained an optional `session=` param (default: a
  fresh `HoloSession`) — the Phase-1 gate ("stub a second implementation without
  touching the loop") is now something a test actually exercises, not just prose:
  `test_run_accepts_a_non_holo_model_session` hands `run()` a non-Holo stub
  session and asserts `call_holo_full` is never touched.
- **Golden-transcript equivalence** (the plan's own verification method): a
  6-step / 7-tool-call scripted scenario (click, type, an `update_plan`+`hotkey`
  batch, scroll, drag_to, finished — every native tool name at least once, and
  enough steps to force `trim_to_last_n_images` to evict more than once) was run
  against the PRE-refactor code, and its resulting history (image payloads
  reduced to a length marker, to keep the fixture small and reviewable) saved as
  `tests/_fixtures/golden_transcript_history.json`. `tests/test_model_seam.py`
  re-runs the identical scenario post-refactor and asserts byte-identical output —
  proving the double-trim behavior (the persistent session history trimmed in
  `commit()`, the per-request `messages` list trimmed again inside
  `call_holo_full`) round-trips unchanged.
- Tests: 71 → **78 green** (offline; no rig, no runs/ evidence — this slice never
  touches hardware). New: `tests/test_model_seam.py` (7 tests — Protocol
  conformance, `decide()`/`commit()` in isolation including the trim-across-commits
  case, the golden-transcript equivalence, and the injectable-session gate).

## Verification

- `python -m pytest tests/` — 78 passed (was 71 pre-session).
- `python -m kvm_agent.models.holo` self-test — 11/11 fixtures, all 10 tools
  covered, coordinate projection check OK (unchanged by this refactor).
- Battery rerun: not run this session (pure refactor, no model/prompt/schema
  change) — scores are expected unchanged per the plan's gate; next rig session
  can confirm alongside whatever else needs the rig.

## Follow-ups

- **Slice B — Phase 0 firmware hardening** is still the only item left from the
  2026-07-22 roadmap-alignment plan; own branch, needs BOOTSEL flash + Pi 5
  deploy + an overnight ≥8h soak (`docs/PLAN_2026-07-22_roadmap_alignment_slices.md`
  Part 3).
- Phase 2 (subgoal unit + independent verification, `docs/ROADMAP.md` §4) is the
  next roadmap phase once Slice B lands; `verify()` joins the `ModelSession`
  Protocol there, once there's an actual postcondition oracle to implement it
  against.
