# Session 2026-06-22 — per-step closed loop + hard-fact constraints

Picks up the two highest-leverage levers from `SESSION_2026-06-21_replan_feedback.md`'s closing
TAKEAWAY: *"every mechanism fires correctly; the remaining wall is the planner not ACTING on
injected knowledge."* Both shipped + fully offline-tested. **Not yet run live** (rig is a shared
physical resource) — the live validation is the next step.

Default behavior is **unchanged**: the closed loop is opt-in (`AGENT_CLOSED_LOOP=0` default → still
`run_goal`), and the `run_plan` refactor is behavior-preserving (all prior offline tests green), so
the 10/10 keyboard benchmark path and every existing run are untouched.

## 1. Per-step closed loop — `run_goal_step` (a different `run_goal`, not a framework)

Stop emitting an N-step plan and running it blind-then-replan. Instead, every turn:
**observe → ask the planner for the SINGLE next action (live screen + goal + short history) →
execute that ONE step → observe → repeat**, until done / stuck / max_steps. This is what lets it Esc
the broken-shortcut dialog the turn it appears instead of running a stale plan into it.

- `planner.py`:
  - `Planner.next_step(goal, screen_png, history)` — one-object planning. Reuses `_complete`
    (so `_inject` still prepends recalled memory). Prompt asks for ONE JSON object, not an array.
  - `_extract_step` / `_first_json_object` — single-object parser (bare object, 1-element array,
    object-in-prose, `<think>`/fence stripping, brace-in-string aware). Shared
    `_strip_reasoning_and_fences` factored out of `_extract_json` (unchanged behavior).
  - `validate_step(step)` — per-step lint mirroring `validate_plan` (claim-`expect`→`ask`, drops
    malformed/unknown → the loop re-asks instead of silently treating it as `done`).
  - `run_goal_step(...)` — the loop. Guards: **premature-`done`** (a `done` before any action ran is
    ignored once — kills the silent-success class), **stuck limit** (N consecutive fails → `stuck`),
    invalid-action rejection, planner-error capture. One `run_dir`, per-step frames via the executive.
- `executive.py`:
  - `_run_one_step(step,i,t0,run_dir,on_event) -> (rec, control)` — the **lossless extraction** of
    `run_plan`'s per-step body (same guards/dispatch/frames/events). `run_plan` now loops over it →
    behavior-identical. `run_step(...)` (single-step, returns `{status, op, rec, failure_summary?}`)
    reuses the EXACT same semantics for the closed loop. This shared chokepoint is why the constraint
    gate (below) protects BOTH orchestrators for free.

## 2. Hard-fact constraints (retrieval ≠ utilization → the gap is **code**)

`recall_block` was soft ("use them to plan, but verify") so the 30B ignored "the FF shortcut is
broken." Now a blocking recalled fact is enforced two ways:

- **Directive at the top of the turn.** `hindsight.classify_facts(facts)` splits recalled facts into
  imperative **directives** (prohibition/breakage cues: don't/never/avoid + broken/doesn't work/
  moved/unavailable…) vs soft **facts**, and derives machine-enforceable **gates**
  `{op, match, reason}` where an op-verb + target parse out (`_govern_op` picks the verb nearest the
  target, so "first **run**" doesn't hijack "**clicking** 'Set as default'"). `recall_constraints()`
  returns `{directives, facts, gates}`. `planner._memory_block` renders **HARD CONSTRAINTS first**
  ("…OVERRIDE the idioms; a violating step is REJECTED before it runs…"), soft facts after.
- **Gate in the executive.** `Executive.set_constraints(gates)` + `_blocked_by_constraint(step)`;
  `_run_one_step` blocks a violating step BEFORE it acts (fails it with the reason → fed back to the
  planner / replan). Default `hard_constraints=[]` → pure no-op. So even if the planner ignores the
  directive, the dead action never executes.
- `planner._arm_memory / _disarm_memory` unify memory arming for **both** `run_goal` and
  `run_goal_step`: recall → classify → arm `planner.context` (directives-first) + executive gates;
  clear after the goal (no cross-task leak). Fail-soft: any error → soft block / no memory.

## Wiring
- `config.py`: `AGENT_CLOSED_LOOP` (`closed_loop`, default 0) + `AGENT_CLOSED_LOOP_MAX_STEPS`
  (`closed_loop_max_steps`, 12).
- `server/app.py`: routes to `run_goal_step` when `CFG.closed_loop`, else `run_goal`; `/health`
  surfaces both; summary line is loop-aware.

## Tested (offline, Windows-side — bash mount lags fresh writes)
`py_compile` clean; `import kvm_agent.server.app` OK (`closed_loop=False max_steps=12 run_step=True`).
Full suite green: **test_closed_loop_step**, **test_hard_constraints** (new) +
test_closed_loop / test_memory / test_replan_feedback / test_click_verify / test_scroll_op /
test_launch_routing / test_uitars_adapter (regressions, all EXIT=0). The new tests cover: single-step
parsing, validate_step, next_step prompt, the loop (happy/premature-done/invalid/stuck), the gate
firing in-loop (a blocked launch never reaches the HID), classify_facts/gate derivation,
_memory_block ordering, _arm/_disarm (incl. fail-soft), recall_constraints.

## NOT done / NEXT (needs the live rig)
1. **Live-run the closed loop**: `set AGENT_CLOSED_LOOP=1` (+ `AGENT_HINDSIGHT=1`,
   `AGENT_PLANNER_MODEL=Qwen/Qwen3-VL-30B-A3B-Thinking`), run the firefox/default-browser goal via
   `tools/run_goal_once.py`. Watch: does re-asking from the live frame Esc the broken-shortcut dialog
   the turn it appears, and does the gate BLOCK `launch Firefox` (recalled broken-shortcut fact) →
   force a different route? Compare step count / outcome vs `run_goal` (A/B with `AGENT_CLOSED_LOOP`).
2. The target's broken FF Start shortcut (`private_browsing.exe` moved) is still a target-machine
   issue — the gate routes AROUND launching it, but completing "set FF default" still needs FF to
   actually open (fix the shortcut, or have the planner register FF another way).
3. Tune `closed_loop_max_steps` / `stuck_limit` and the `next_step` prompt from the first live runs
   (per-turn latency × max_steps is the cost; the 30B-Thinking turn is ~10–13s).
4. `measure.py --k 10` sanity (should be untouched: closed loop is opt-in, `run_plan` preserved).

## Live run #1 (HID, 2026-06-22) — closed loop runs end-to-end; 2 fixes from it

`run_goal_once.py --kind hf --closed-loop` on the firefox goal, `Qwen3-VL-30B-A3B-Thinking`, memory on.
The per-step loop ran for real: 11 turns, `next:` per turn, winget-install path executed (launch cmd →
type winget → enter → sleep), history threaded. Two concrete bugs surfaced + fixed (all offline-tested):

1. **A transient `APIConnectionError` killed a 277s run.** The closed loop makes one planner call per
   step, so a single router blip aborting the whole goal is unacceptable. `run_goal_step` now retries
   the planner call (1 + 2 retries, 2s/4s backoff) on transient errors (`_is_transient`); non-transient
   errors still fail fast.
2. **The launch gate didn't fire — wrong target.** Diagnosed (not guessed) with a throwaway
   `recall_constraints` dump: the recalled broken-shortcut fact QUOTES the dialog name
   (`'Problem with Shortcut'`), and `_gate_target` preferred the quoted phrase → gate was
   `{launch, 'problem with shortcut'}`, which never matches a `launch Firefox` step. Fix: derive the
   gate target as the **object of the action verb** (`_gate_for`/`_object_after`: `…launching Firefox`
   → `firefox`), not any quoted phrase. Verified against live recall → gates now `{launch, firefox}`,
   `launch Firefox` → blocked.
   - **Nuance:** a launch gate blocks only the **bare-name** launch (Start-menu search → the broken
     shortcut); a full-path / `.exe` / `ms-settings:` URI launch is the legitimate workaround and is
     NOT blocked (`_blocked_by_constraint` reuses `_is_winr_target`) — else Firefox could never open
     and the task could never complete.

## Live run #2 (HID, 2026-06-22, `--kind claude`) — GATE FIRES LIVE; launch primitive hardened

12-turn closed-loop run. **The hard-fact gate worked end-to-end live** (what 2026-06-21 couldn't do):
the planner proposed `launch Firefox`, the executive **BLOCKED it in code** (`gate: BLOCKED launch —
…Firefox Start-menu shortcut is broken…`) and threaded the reason into the next turn. Claude even
adopted the `precondition` idiom on its own. Task still didn't COMPLETE — root-caused from the per-step
frames (`runs/firefox_073814/`), and it was the **launch primitive**, not the new code:

- `launch C:\…\firefox.exe` (the workaround the gate steers toward) went into **Start-menu search**,
  not the Run dialog (`06_launch.png`: *"No results found for 'C:\…firefox.exe'"*) — and the search
  panel appearing tripped the frame-diff so `launch()` reported **ok** (a false positive). Pressing
  Enter on the no-results search kicked a Bing query into Edge → full-screen Edge ate the next
  `launch`'s Win+R → the path typed into Edge's bar → another Bing search (`09_launch.png`).
- Win+R is unreliable for a spaced/quoted full path (intermittent — `cmd`/`ms-settings:` worked the
  same run); the agent has no shell on the target, so the reliable channel is **cmd**.

**Launch hardening (executive.py):**
- New EXE-PATH route: `launch C:\…\app.exe` → `_launch_exe_path` opens cmd (`_open_cmd`, Win+R then
  Start-search fallback, vision-confirmed) and runs `start "" "<path>" & exit`, then confirms the app
  via vision (`_exe_friendly_name`: firefox.exe → "Firefox"). `_is_exe_path` splits a full path out of
  the Win+R routing.
- **Kill the false-positive:** `_launch_misfired` (OCR for 'No results' / a web search echoing the
  launch string) runs BEFORE the frame-diff fast-path — a Start-search/browser panel is now a launch
  FAILURE (Esc + retry), never success. Cheap tesseract read; returns False on real app windows
  (Notepad/Calc/Firefox), so the keyboard benchmark is unaffected (re-check `measure.py --k 10`).
- Planner idiom: if launching by name is blocked/broken, launch the FULL EXE PATH (executive opens it
  via cmd) — don't retry the bare name or type the path into a browser.
- Tests: `tests/test_launch_routing.py` extended (exe-path routing, friendly-name, misfire kills the
  false-positive) + full suite green.

NEXT: re-run `--kind claude --closed-loop`. Expect `launch Firefox` → BLOCKED → planner switches to
`launch C:\Program Files\Mozilla Firefox\firefox.exe` → executive opens it via cmd (Firefox registers)
→ `ms-settings:defaultapps` → set default. Watch the per-step frames to confirm the cmd-launch opens a
real Firefox window (not a search panel). Secondary: the planner opened `ms-settings:defaultapps` too
early last run and didn't return to set the default — a strategy/ordering issue, separate from the
primitive.

### Interlude — the firefox.exe path (runs/firefox_110253)
The full-path launch then FAILED honestly (no false-positive — the hardening working): cmd reported
*"The system cannot find the file C:\Program Files\Mozilla Firefox\firefox.exe"*, and `dir /s /b` +
`where /r` under both Program Files dirs found nothing — Firefox was a USER-scope winget install, not
in Program Files. Added a planner idiom: resolve an installed app's REAL path via the App Paths
registry (`reg query …\App Paths\firefox.exe /ve`, HKCU+HKLM) then a `%LOCALAPPDATA%`/whole-drive
`where`, and launch THAT path (don't assume Program Files). Bumped `closed_loop_max_steps` 12→16.

## Live run #3 (HID, 2026-06-22, `--kind claude`, goal "Open Firefox and set it as the default browser") — COMPLETED ✓
`status=done` in 89s, **9 steps, SCREEN-VERIFIED** (`runs/firefox_124412/08_verify.png`: Default apps →
Web browser = **Firefox**, was Chrome). Trajectory: launch cmd → `reg query …\App Paths\firefox.exe` →
type the firefox path + enter (launches it via cmd) → `ms-settings:defaultapps` → click "Google Chrome"
(the current-default tile) → click "firefox" in the flyout → verify ask "Is firefox the default?" → done.
- **The hard-fact DIRECTIVE steered the planner by itself** — it went straight to reg-query-the-path
  and never attempted the broken bare-name launch, so the executive gate (armed) didn't need to fire.
  Prompt-level utilization AND the code backstop, both in place — the retrieval→utilization gap the
  2026-06-21 session hit is closed.
- The find-path idiom worked; Firefox resolved at the standard path this run (the prior run's
  `winget install --force` evidently repaired the install into `C:\Program Files\Mozilla Firefox\`).
- The Win10 tile→flyout→pick set-default mechanic (the long-standing 2026-06-21 hard-GUI blocker)
  completed, grounded correctly, and verify-before-done confirmed the REAL state (no false positive).

End-to-end win on the per-step closed loop.

## Verification + write-back — DONE (2026-06-22)
- **Benchmark not regressed:** `measure.py --k 10` = **10/10 verified PASS, mean 17.6s/task**
  (`runs/measure_20260622_125133`). The `run_plan` refactor (`_run_one_step`) and the new `launch()`
  misfire OCR cost nothing on the keyboard path — behavior-preserving, now confirmed LIVE, not just offline.
- **Write-back confirmed end-to-end:** re-ran the firefox goal with `--write` (`runs/firefox_125900`,
  `status=done`, 10 steps). `retain_recipe` fired and a recall of the goal now returns the working
  sequence ("…launching the Command Prompt, querying the App Paths registry…"). The carry-forward loop
  is closed for this task. Also notable: the step-3 `verify "Is Firefox open?"` FALSE-NEGATIVED (Firefox
  was open — its new-tab page — but qwen2.5vl said no); the closed loop tolerated it (fed the miss to
  history, continued) and still completed, incl. dismissing the Win10 "No, thanks" keep-Chrome nag.
- KNOWN debt (server-side): Hindsight's extractor emits a Chinese duplicate of each English fact →
  recall bloat over time. Dedup-on-write catches near-identical English recipes, not the cross-lang dups.

## A/B: closed loop vs plan-then-replan (firefox, --kind claude, 2026-06-22)
Added `--plan` to `run_goal_once.py` to force `run_goal` regardless of `AGENT_CLOSED_LOOP` (the env was
silently forcing the closed loop on — the `[run] loop=…` line is the tell). Clean A/B, SAME goal/planner:
- **closed loop** (`run_goal_step`): done, **7 steps, 48s, 0 replans**, 7 Opus calls (one per step).
  Picked the Win10 "No, thanks" nag button correctly first try by observing that turn.
- **plan+replan** (`run_goal`): done, **27.7s, 1 replan**, 2 Opus calls. Built a sharp 19-step plan
  (reg-query both hives, preconditions, ms-settings flow) but GUESSED the nag button = "Switch anyway"
  (it's "No, thanks") → step 16 failed → replanned from the live screen → "No, thanks" → done.
VERDICT: a **wash on this task** — both complete. The closed loop's per-observation edge showed up
exactly where the blind plan stumbled (an unknowable dialog button), but `run_goal`'s replan recovered.
It's a **latency/robustness tradeoff, planner-coupled**: run_goal = 2 calls (cheap with Opus), closed
loop = N calls (only cheap once the planner is LOCAL). One surprise can't separate them — run_goal
re-plans the whole remaining plan per failure while the loop adjusts one step, so the closed loop pulls
ahead only on a task with SEVERAL mid-stream surprises.

NEXT (reordered): (4) stand up the B580 `LocalPlanner` FIRST — it changes the closed-loop economics
(per-step calls go ~free) and is the all-local end-state; (3) THEN re-run the A/B on a HARDER task
(multiple recoverable dialogs/state changes) against the local planner to decide whether to flip
`AGENT_CLOSED_LOOP` to default. Minor cleanup: prune the now-dead `_govern_op` in `hindsight.py`.
