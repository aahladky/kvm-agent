# PLAN 2026-07-22 — Roadmap Phase 2: subgoal unit + independent verification (APPROVED)

_Approved 2026-07-22 in Claude Code plan mode; promoted to docs/ per AGENTS.md
§6 (any approved plan is committed at approval time). Status at promotion:
**NOTHING EXECUTED** — all four slices (D-a … D-d) PENDING, each its own branch.
Operator decisions folded into the approved text below: (1) extend the battery
with longer tasks AND move grading toward automation, human out of the loop;
(2) when the verifier refuses a `finished` claim k times, the run ends as FAILED,
loudly; (3) in D-d the plan scopes what the ACTOR sees (true plan-and-execute),
not gate-only. Body below is the approved text, verbatim._

_Status update 2026-07-23: **slice D-a EXECUTED, its gate PASSES**
(`docs/SESSION_2026-07-23_phase2_slice_d_a_verifier.md`, branch `slice-d-a-verifier`).
29/29 on the human-graded set — 14/14 positives, **false-refusal rate 0.0** — plus 80/80
claim-resistance under a confident false claim, 0 unanswered, median 4.2s per check.
Evidence: `runs/verify_replay_20260723_000637/`, `runs/verify_replay_20260723_002007/`.
Two results feed forward and REFINE the slices below, which are otherwise unchanged:
(1) **D-b task authoring** — the one real oracle miss confirmed *the target exists*
rather than *the action's effect* on a task phrased as an action ("click the WiFi icon"),
so new battery tasks must state an END STATE. (2) **D-d, upgraded from preference to
correctness requirement** — that miss occurred with NO claim attached, and the same case
answered correctly once a claim was present, so the failure mode is action-phrased
postcondition + claimless check, which is exactly how a subgoal check runs. D-d must
**reject or rewrite** action-phrased subgoal postconditions at the harvest point, not
merely prefer state phrasing; native's prompt asks for verb-first goal titles, so action
phrasing is the DEFAULT output. (3) D-c's stated gate (false-refusal ≈ 0) is met offline
on replayed frames; D-b's shadow run is what confirms it live._

---


## Context

`docs/ROADMAP.md` §4 Phase 2: *"restructure the loop from flat-step to subgoal-gated.
Give the planner a real (non-decorative) plan that the harness sequences. Pull
verification into its own call/prompt — a postcondition oracle separate from the actor.
Gate progression on it (refuse-to-advance, don't inject retries)."* Gate: *"confident-wrong
progress that the old loop missed now gets caught at a subgoal boundary; battery
completion rate up, false-'finished' rate down."*

Phases 0 and 1 are closed (firmware hardening deployed; the `ModelSession` seam landed
as `decide`/`commit`, with `verify()` deliberately withheld for this phase —
`kvm_agent/models/base.py:1-16`). The tree is clean and Phase 2 is next.

Three facts found while surveying the repo shape this phase:

1. **`update_plan` has never once been emitted** — zero occurrences across all 19
   recorded battery runs (`grep '"update_plan"' runs/battery_*/step_*.json` → 0). It
   isn't "decorative" as the roadmap says; it is *unused*. The cause is benign: the
   native prompt says to plan *"within your first 2-3 steps for non-trivial tasks (>5
   steps or multiple sources)"* (`docs/native/local-desktop-2026-06-12.j2:132-142`), and
   every battery task finishes in 1-10 steps. The model is obeying its prompt. This is a
   *task-length* problem, not a prompt problem — and it's testable.
2. **The battery is at its ceiling and cannot measure this phase.** The last three
   batteries scored 5/5, 5/5, 5/5 (1 void) and every graded row is a pass. Completion
   rate has nowhere to go, and the archive contains not one false-"finished" — the model
   has never claimed done and been wrong on a graded task.
3. **Grading is the human's, every time** (`tools/battery.py:45-65`, `grader: "human"`
   hardcoded at :155). That's the bottleneck this phase can remove: the same postcondition
   oracle that gates the loop is the thing that grades the battery.

So the phase is ordered to earn its risk: build the oracle and **measure it offline
against the existing graded archive first**, extend the battery so the phase has
headroom, run it in shadow, and only then let it gate the loop and take grading off the
human. Each slice is its own branch and its own reviewable diff (AGENTS.md §4).

---

## The design

**Verifier = a stateless perceptual function, separate from the actor.**
`(frame, task, the model's claim) → {satisfied, evidence}`. No history, no task memory,
its own short prompt, its own tiny schema, temperature 0, thinking off. Same model name
(`CFG.holo_model`) on the same llama-swap endpoint — a *different* model id would swap
the model on the B70 on every check. Roadmap §3's tier split (grounder/verifier as
"pure perceptual functions — no memory, no task awareness") is honoured structurally, so
Phase 5 relocates it to the B580 by swapping one injected object.

**Two consumers, one oracle:** the loop (gating progression) and the battery (grading).
Both get measured before either is trusted.

**Gate points, in order of value:** the terminal `finished`/`answer` claim first (it
kills false-"finished", the phase's headline metric), then subgoal `done` transitions
(the keystone unit).

**Refuse-to-advance, never inject.** The pre-fire TOCTOU guard
(`agent_loop_holo.py:509-530`, post-step handling at :609-623) is the precedent to lift
one tier up: don't proceed, tell the model in `<tool_output>`, count consecutive
refusals, abort loudly at a limit. No injected retry, no suggested coordinates (the
2026-07-19 anti-contamination rule).

**Fail-visible, never fail-open.** `Verdict.satisfied` is `bool | None`; `None`
(verifier errored or unavailable) is recorded loudly and never counts as satisfied —
the same discipline that makes `grade_task` refuse an empty grade. This is the rule that
keeps automated grading compatible with finding #8 ("fail-open grading is the
anti-pattern this project exists to kill").

---

## Slice D-a — the oracle + offline replay eval (no rig time, no loop changes)

The cheapest possible go/no-go: run the oracle against the **already-graded run archive**
before wiring it into anything. This reuses AGENTS.md §2.4's offline-replay mechanism
(saved frame + prompt → model, no pipeline).

**Files**
- `kvm_agent/models/base.py` — add `Verdict` (`satisfied: bool | None`, `evidence: str`,
  `raw: dict`, `usage: dict`, `wall_time_s: float`) and a `runtime_checkable` `Verifier`
  Protocol with one method: `check(data_url, w, h, question, claim="") -> Verdict`.
  **Also update the module docstring**, which currently promises `verify()` joins
  `ModelSession` in Phase 2 — it doesn't, and the tree must not contradict itself. State
  why: statelessness is the point, a method on the stateful session invites history
  coupling, and a separately injected object is what Phase 5 needs to move the verifier
  to another card.
- `kvm_agent/models/holo.py` — `VERIFIER_PROMPT` (short, bespoke; the file already
  documents native's own precedent for a separate stateless endpoint at temperature 0.0
  with thinking off, `call_holo_full` docstring :592-597), `VERIFY_SCHEMA`
  (`{"satisfied": bool, "evidence": str}`), `call_holo_verify(...)`, and `HoloVerifier`
  implementing `Verifier`. Reuse `_target_config` (:576), `openai_client`,
  `jpeg_bytes_to_data_url` (:722), `image_path_to_data_url` (:708), and the existing
  `REQUEST_LOG` (tokens + `http_ms` come free).
  **`call_holo_verify` builds its own message list; it does NOT go through
  `build_messages`/`call_holo_full`**, which hardcode the actor's `SYSTEM_PROMPT` (:454)
  and `RESPONSE_SCHEMA` (:610) — the latter's `tool_calls: minItems 1` would force the
  verifier to emit a desktop action. Leaving the actor path untouched is also what makes
  golden-transcript equivalence trivially true. Note the shared client's 180s timeout
  (`kvm_agent/llm/ollama.py:13-19`).
- `tools/verify_replay.py` (new) — the offline eval harness; reuse
  `tools/show_reasoning.py:find_run_dir` (:74-81) for run-dir resolution. Output to
  `runs/verify_replay_<ts>/` (AGENTS.md §1).

**The eval set, built entirely from what's already on disk.** Join
`runs/battery_<ts>/results.json` (`run_tag`, `grade`, `answer_text`, `instruction`) to
each per-task `runs/<run_tag>_<ts>/step_NN.png`:
- **Positives (must be `satisfied=True`)** — the final frame of every human-graded `pass`
  run that ended `finished=True`: ~14 cases across `battery_20260721_235153`,
  `battery_20260722_173742`, `battery_20260722_222137`. These measure **false-refusal**,
  the number that gates D-c.
- **Negatives (must be `satisfied=False`)** — two sources: the final frame of every run
  that ended `finished=False` (`battery_calc_multiply_20260721_074305`'s 20-step flail,
  `battery_notepad_type_20260721_071208`, both `paint_line` max-steps runs); **and
  `step_00.png` of every run** — the pre-task desktop, a free correctly-labelled "task
  not yet complete" frame for every task in the archive, which is what makes the negative
  set big enough to mean anything. Expect `top_bar_clock` to be a legitimate exception
  (its answer is on screen at step 0) — report it, don't score it as a miss.
- Report honestly: no archived run has a *false* `finished` claim, so the negatives
  measure "does the oracle recognise an unfinished screen", not a true
  false-confirmation rate. That rate needs D-b's harder tasks.
- Also worth reporting: the oracle and the actor are the same model, so the oracle may
  share the actor's blind spots. The positive/negative separation here is the first
  evidence either way.

**Tests (offline):** `tests/test_verifier.py` — schema/parse round-trip, `satisfied=None`
on a model error (fail-visible), Protocol conformance, and a stub verifier mirroring
`tests/test_model_seam.py:256-306`'s `_StubSession` pattern.

**Gate:** clean separation of positives from negatives with false-refusal at or near
zero. If it fails, Phase 2 stops here — the problem is oracle design, not loop design,
and nothing has been risked.

---

## Slice D-b — shadow wiring, harder tasks, metrics (one rig session buys everything)

Shadow mode changes no behaviour, so **one** battery run yields the flat-loop baseline on
the harder tasks, the live false-refusal numbers, the verifier-vs-human grading agreement,
and the answer to whether the model plans on longer tasks.

**Files**
- `agent_loop_holo.py` — `run(..., verifier=None, verify_mode="off")` with
  `verify_mode ∈ {"off", "shadow", "gate"}`; `"off"` is the default and byte-identical to
  today. On a `finished` action, reuse the `after` frame the batch loop already grabs
  (:545 — `finished` falls through `_execute`'s settle path, so no extra capture) and
  call `verifier.check(...)` with the task instruction and `answer_text`. Shadow: record
  only, return exactly as today.
- `kvm_agent/instrumentation/run_log.py` — record verdicts: a `verification` entry on the
  step record, `verifications` + `verified_finish` in `summary.json`. `run()`'s return
  keys `{"finished", "answer_text"}` stay unchanged — `tools/battery.py:150-156` builds
  its row off exactly those two.
- `tools/battery.py` — record the oracle's verdict in the results row **alongside** the
  human grade, never replacing it (`grader` stays `"human"` this slice; add
  `auto_grade` + `auto_evidence`). This is the agreement data D-c needs to justify taking
  the human out.
- `tools/battery_tasks_gnome.json` — add 3-4 longer multi-stage tasks (~8-15 steps, ≥3
  natural subgoals, objectively checkable end state): e.g. create a file in Files and
  rename it; change a Settings toggle and confirm its effect elsewhere; a two-app
  copy/paste. These give Phase 2 something to improve and D-d something to decompose.
- `tools/battery_metrics.py` (new) — aggregate `runs/*/summary.json` + `results.json`
  into completion rate, steps-to-completion, false-"finished" rate, verifier
  agreement / false-refusal / false-confirmation, guard-refusal rate, per-step latency.
  Roadmap §5 lists these as the tracked metrics; none are computed today.

**Tests (offline):** extend `tests/test_agent_loop.py` using `_patch_run` (:91-101) and
the `GuardEnv` observe-queue (:546-555): shadow records a verdict and doesn't change the
return; `verify_mode="off"` never constructs or calls a verifier; a verifier that raises
yields `satisfied=None` and doesn't kill the run.

**Rig session (one):** extended GNOME battery, `verify_mode="shadow"`, human still grading.

**Gates:** the extended battery shows **headroom** (not a clean sweep) — otherwise D-d has
nothing to prove; shadow verdicts agree with the human grades; false-refusal ≈ 0.

**Free probe, decides D-d's mechanism:** whether the model spontaneously emits
`update_plan` on 8-15 step tasks. If it does, D-d harvests the plan natively; if not, D-d
needs an explicit planner call.

---

## Slice D-c — flip the gates on (loop **and** grading)

Two flips, both earned by D-b's numbers, both small diffs with real consequences.

**1. In-loop terminal gating.** `verify_mode="gate"`: an unsatisfied verdict on `finished`
does not terminate the run — the model gets a `<tool_output>` saying the answer was not
accepted and why (the verdict's evidence text), and the loop continues.
`VERIFY_REFUSE_LIMIT` (k-strikes, same family as `STUCK_LIMIT` / `NO_PROGRESS_LIMIT` /
`GUARD_REFUSE_LIMIT` — `docs/REPORT_2026-07-22_harness_pattern_inventory.md`) bounds the
livelock. **On hitting the limit the run ends as failed, loudly** —
`recorder.finish(False, note="answer refused by verifier x3")` — a distinct terminal
note, never conflated with `max_steps reached`. Tests mirror the guard's five
(`tests/test_agent_loop.py:519-694`): refuse-and-continue, accept-and-finish, k-strikes
termination, verifier-error path.

**2. Battery auto-grading — the human out of the loop.** `tools/battery.py` grades from
the oracle by default (`grader: "verifier"`), with the interactive human path retained
behind a flag and used as spot-check. The fail-closed contract is preserved exactly:
`satisfied=None` or a verifier error grades **fail**, never pass; `make_payload`'s
all-tasks denominator is untouched; every automated grade carries its evidence string so
a disagreement is auditable after the fact. Voids stay human-only (infeasibility is a
judgement about the target, not about the screen).

**The spot-check is load-bearing, not optional.** Once both switches are flipped the
oracle becomes self-referential: a run ends `finished=True` only because the verifier
accepted it, and then the same verifier grades that same settled screen with the same
question — so measured false-confirmation collapses to ≈0 by construction, exactly
where the metric is supposed to bite. (D-b's agreement number is unaffected: there the
loop runs in shadow, so the model's finish, the verifier's verdict and the human's grade
are three roughly independent signals — which is why D-b is the right basis for the D-c
go/no-go.) The post-flip number therefore needs a defined human ground-truth sample, not
an ad-hoc glance: **human-grade every run where the model's own `finished` claim and the
verifier's verdict disagree, plus a random N% of the agreements.** `battery_metrics.py`
computes false-confirmation over that sample only, and says so in its output.

**Named limit — what auto-grading does *not* unblock.** A fully unattended battery is
still blocked upstream of grading: `target.reboot()` is a blocking `input()` prompt
(`kvm_agent/hardware/target.py:13-17`, "v1 is MANUAL", power backend deferred in
PROJECT_STATE §4), and `battery.py:138-146`'s HID gate blocks on an operator replug when
the composite device comes up half-dead. So this slice ships auto-grading plus an
opt-in `--no-reboot` unattended mode (whole task list, no human) whose honest cost is
state carryover between tasks — which is precisely what the reboot exists to prevent.
Full unattended-with-reboot lands with the power-control backend, not here.

**Hard gate:** ships only if D-b measured false-refusal ≈ 0. On a near-ceiling battery a
false-refusing verifier turns a true pass into a fail *and* mis-grades it — the same
error twice. This is the phase's main downside risk and this gate is what contains it.

---

## Slice D-d — the subgoal unit, with instruction scoping (the keystone)

Only once D-b shows headroom on the harder tasks. Bigger than the others; expect a
session of its own.

- **The plan.** Cheapest mechanism first: the native prompt *already* asks for goals
  "achievable with concrete target states or success criteria" and the schema then throws
  them away (`holo.py:275-288` keeps only `title`/`status`). Add an optional
  `postcondition` string to `update_plan`'s `updates` items and postconditions arrive with
  zero prompt surgery. Only if D-b's probe shows the model still won't plan on long tasks
  does this become an explicit planner call at run start.
- **The harness sequences it.** One active subgoal; `PLAN` (`agent_loop_holo.py:102`)
  stops being print-only and becomes control state, recorded per step. `done` transitions
  are gated on the verifier against the subgoal's postcondition (refuse-to-advance, same
  skeleton as the terminal gate). Per-subgoal step budgets; per-run counters (`stuck`,
  `frozen`, `click_repeat`, `guard_refusals`) get per-subgoal scoping; aborts name the
  failed subgoal.
- **The actor sees the active subgoal** (operator decision): true plan-and-execute, not
  gate-only. The instruction stream becomes task + active subgoal rather than task-at-step-0.
  This is a real deviation from the native protocol and the riskiest change in the phase,
  so it ships **as an A/B against D-b's baseline on the same task list** with the
  disclosed-deviation discipline `holo.py:43-67` already uses. Documented fallback if it
  regresses the tasks that pass today: keep the plan as a pure gate and leave the actor's
  instruction stream alone — the verification floor and recovery unit survive either way.
- **Out of scope here, by roadmap order:** recovery stays abort-only (demote/escalate is
  Phase 4); memory chunking at subgoal boundaries is Phase 3. Built *inside*
  `agent_loop_holo.py` — no new loop generation (AGENTS.md §3).

**Gate:** on the extended battery, completion rate up and false-"finished" down vs. D-b's
baseline, with at least one caught case of confident-wrong progress the flat loop missed.

---

## Verification

- `python -m pytest tests/` green at every slice (86 tests today; each slice adds its own).
- **D-a:** `python tools/verify_replay.py` → `runs/verify_replay_<ts>/` — the go/no-go,
  entirely offline, no rig.
- **Golden-transcript purity:** regenerate the fixture from today's code *before* touching
  the loop (`tests/test_model_seam.py:109-119` documents the one-shot generation
  discipline); `verify_mode="off"` must leave it byte-identical.
- **D-b / D-c / D-d rig:** `python tools/battery.py tools/battery_tasks_gnome.json`
  (extended list), then `python tools/battery_metrics.py` for the numbers each gate names.
- Per AGENTS.md §4/§6: one branch per slice, commit-or-revert with `git status` clean,
  `PROJECT_STATE.md` + a dated `docs/SESSION_*` per slice citing its `runs/` evidence, and
  this plan committed as `docs/PLAN_2026-07-22_phase2_subgoal_verification.md` at approval.
