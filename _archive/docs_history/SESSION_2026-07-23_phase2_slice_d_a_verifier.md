# SESSION 2026-07-23 — Phase 2 slice D-a: the postcondition oracle, offline-validated

## What this session was

Slice D-a of `docs/PLAN_2026-07-22_phase2_subgoal_verification.md`: build the
postcondition oracle and measure it against the **already-graded run archive** before
wiring it into anything. No rig time, no loop changes — the go/no-go on whether a
Holo-backed oracle works at all. It does.

## Result — D-a's gate PASSES

`runs/verify_replay_20260723_000637/results.json` (94 cases, 428s total, 0 unanswered):

| bucket | correct | note |
|---|---|---|
| **positives** (human-graded pass + finished, final frame) | **14/14** | **false-refusal rate 0.0** — the number gating D-c |
| **negatives** (graded: step_00 + never-claimed-done finals) | **15/15** | 0 false confirmations |
| inferred negatives (ungraded failed runs, incl. the Windows/WAA era) | 64/65 | label inferred, scored separately |

**29/29 on the human-graded set.** Latency: median 4.2s/check (min 3.8, max 7.8),
median 1406 prompt / 69 completion tokens — cheap enough for a per-subgoal gate, and
roughly a third of an actor step (~15s).

## Claim-resistance: the confound, and closing it

The table above has a hole that a review caught: **every positive carries the real
`answer_text` claim and every negative carries `claim=""`**, so a clean separation could
partly be reading *claim present vs absent* rather than *pixels show it vs don't*. Worse,
the confound hides the case that actually gates D-c — an unfinished screen **plus** a
confident "I'm done" claim is what a false confirmation IS.

Testable offline, from data already on disk. `--cases adversarial` pairs every
unfinished-screen frame with a confident false claim (borrowed from a successful run of
the *same* task where one exists — the most plausible lie available — else a generic
completion boilerplate), expected answer unchanged: False.

**Result: 80/80 correct, 0 false confirmations** (`runs/verify_replay_20260723_002007/`).
The oracle does not fold to a confident false claim. The D-c gate criterion
(false-refusal ≈ 0) is therefore not resting on a confounded number.

## What the eval set is, and its honest limit

Built entirely from `runs/`: `runs/battery_<ts>/results.json` carries `run_tag` + human
grade + `answer_text` + instruction; the per-task `runs/<run_tag>_<ts>/` carries the
frames. Positives are the last `step_NN.png` of every graded-pass run that ended
`finished=True`. Negatives come from `step_00.png` (the pre-task desktop — a free,
correctly-labelled "not done yet" frame for every task) and the final frame of runs that
ended `finished=False`.

**The limit, stated in the artifact itself:** no archived run contains a *false*
`finished` claim — the model has never claimed done and been wrong on a graded task. So
the negatives measure *unfinished-screen recognition*, **not** a true false-confirmation
rate. That number needs slice D-b's harder tasks. Recorded in `results.json` under
`limitation` so the file can't be read as claiming more than it measured.

## Three "misses" that were the LABEL being wrong, not the oracle

The first replay run (`runs/verify_replay_20260722_235815/`) scored 3 step_00 misses.
All three were the clock tasks — `taskbar_clock`, `top_bar_clock` ×2 — and the oracle's
evidence was impeccable each time ("The top bar of the screen displays the date and time
as 'Jul 22 17:56'").

It was right and the label was wrong. An **observation task**'s postcondition ("the
information is on screen and reported") already holds on the pre-task desktop, so
`step_00` is not evidence of an unfinished screen. Scoring it as a negative punishes the
oracle for being correct. Now excluded by `OBSERVATION_TASK_RE`, with the reason recorded
in `results.json["skipped"]` rather than silently dropped.

Worth carrying into D-b: for a read-only task, "is it done" is not really a screen
question — the checkable thing is whether the *claim* matches the screen, which is why
`Verifier.check` takes `claim`.

## The one real miss, and what it teaches

`battery_20260718_112611__small_target_tray` (Windows era): task *"Click the small
network/WiFi icon in the system tray"*. The oracle answered satisfied=True with evidence
*"the network/WiFi icon is visible next to the volume and battery icons"* — it confirmed
**the target exists** rather than **the action's effect**.

The task is phrased as an *action*, not an *end state*, so there was no checkable
postcondition to find. This is the project's **one empirical false confirmation** — and
note it needed no misleading claim to produce: `claim=""`, pure pixel misjudgment.

The adversarial run sharpened it further. The same frame and task, with a confident false
claim attached, answered **False**: *"there is no visible network/WiFi icon that has been
**clicked or activated**"*. Having a claim to check against pushed the oracle to look for
the action's effect instead of the target's existence. So the failure mode is specifically
**action-phrased postcondition + no claim** — and that is exactly the configuration a
subgoal check runs in, because there is no `answer` text at a subgoal boundary.

Consequences, one per slice:
- **D-b task authoring**: new battery tasks state an end state ("the network flyout is
  open"), never a bare action ("click the icon").
- **D-d, upgraded from preference to correctness requirement**: subgoal postconditions
  must be *rejected or rewritten* when action-phrased, enforced at the point the plan is
  harvested. An `update_plan` goal titled "Click the Save button" would be systematically
  false-confirmed by a claimless check — and native's prompt asks for verb-first titles,
  so action phrasing is the *default* output, not an edge case.

Note the failure was *legible* — the evidence string names exactly the wrong reason. That
is the property that makes an automated grade auditable, and it's why `evidence` is
recorded for every verdict including the passing ones.

## Changes

- **`kvm_agent/models/base.py`**: `Verdict` (`satisfied: bool | None`, `evidence`, `raw`,
  `usage`, `wall_time_s`, `.answered`) + `Verifier` Protocol (`check(data_url, w, h,
  question, claim="")`). `satisfied=None` is a deliberate THIRD outcome — model error,
  timeout, unparseable — never a False and never a True; finding #8's fail-closed rule
  applied to the oracle itself.
  **Docstring corrected**: it promised `verify()` would join `ModelSession` in Phase 2.
  It doesn't, and the reason is in the file — statelessness is the property being bought
  (a verify() on the object owning conversation history is one line from reading it, and
  then the oracle judges its actor's story instead of the pixels), and Phase 5 relocates
  a separately-injected object by swapping a constructor argument.
- **`kvm_agent/models/holo.py`**: `VERIFIER_PROMPT`, `VERIFY_SCHEMA`, `verify_message`,
  `call_holo_verify`, `parse_verdict`, `HoloVerifier`. Same model id on the same
  llama-swap endpoint (a different id would swap the model on the B70 per check), but
  temperature 0.0 and thinking OFF. **Its own message list — deliberately NOT routed
  through `build_messages`/`call_holo_full`**, which hardcode the actor's `SYSTEM_PROMPT`
  and `RESPONSE_SCHEMA` (whose `tool_calls: minItems 1` would force the oracle to emit a
  desktop action). Keeping the actor path byte-untouched is also why the golden-transcript
  test still passes unchanged. Schema puts `evidence` BEFORE `satisfied`: generation
  follows property order and thinking is off, so that ordering is the oracle's only
  chance to observe before committing. Logged through the existing `REQUEST_LOG` tagged
  `kind="verify"`, so verification tokens/latency are captured from day one.
- **`tools/verify_replay.py`** (new): the offline eval. Incremental writes so an
  interrupt never loses calls already paid for; per-source score breakdown; inferred
  negatives kept out of the headline numbers.
- **Tests 86 → 116 green** (offline, endpoint never touched by the suite):
  `tests/test_verifier.py` (18 — Protocol conformance, message statelessness, the
  untrusted-claim block, and the whole fail-visible surface incl. `{"satisfied": "false"}`
  never coercing to True) and `tests/test_verify_replay.py` (12 — the labelling logic,
  since a mislabelled eval set produces confident nonsense, as this session demonstrated).

**A test found a real contract violation.** `call_holo_verify` promises never to raise for
a model-side failure, but constructed the OpenAI client *outside* its try/except — so a
client-construction failure (import, bad base_url, llama-swap mid-swap) propagated instead
of becoming `satisfied=None`. That is the path most likely to fire in a long unattended
run. Construction moved inside the guard; `_target_config` deliberately stays outside
(an unknown target is a caller bug, not a model-side failure).

## Verification

- `python -m pytest tests/` — 110 passed (was 86).
- `python tests/test_verifier.py` / `test_verify_replay.py` — dual-mode script runs pass.
- `python -m kvm_agent.models.holo` self-test — 11/11 fixtures, all 10 tools, projection
  check OK (unchanged: the actor path was not touched).
- `python tools/verify_replay.py` — `runs/verify_replay_20260723_000637/` (main eval).
- `python tools/verify_replay.py --cases adversarial` —
  `runs/verify_replay_20260723_002007/` (claim-resistance, 80/80).

## Follow-ups

- **Slice D-b** is next and needs the rig: shadow wiring into `run()`, 3-4 longer battery
  tasks (**phrased as end states**, per the `small_target_tray` lesson), and
  `tools/battery_metrics.py`. One battery run buys the flat-loop baseline, the live
  false-refusal rate, the grading-agreement number, and the does-it-plan-on-long-tasks
  probe.
- D-c's gate (false-refusal ≈ 0) is **met offline** but not yet live — D-b's shadow run is
  what confirms it on fresh frames rather than replayed ones.
