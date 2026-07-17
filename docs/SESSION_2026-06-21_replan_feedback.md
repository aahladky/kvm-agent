# Session 2026-06-21 — replan feedback rework + planner reasoning mode

Picks up from the Firefox run. Two asks: (1) feed real failure context back to the planner on a
failed step, and (2) be able to turn on reasoning for the planner. Both done, all offline-verified
(`tests/test_replan_feedback.py` = 27/27 PASS; `py_compile` + `import kvm_agent.server.app` clean →
`IMPORT_OK hf thinking=False maxtok=4000`). Defaults are byte-identical to before — reasoning and the
richer-context behavior are opt-in / automatic-on-failure only, so the 10/10 keyboard benchmark path
is untouched.

## What was wrong (answers to the two questions)

**Failure feedback existed but was thin and amnesiac.** `run_goal` did call
`planner.replan(goal, result, screen)`, which sent the goal + status string + `json.dumps(log[-6:])[:1500]`
+ the live screenshot. But: (a) each `_complete` is an independent single-turn call, so replan #2 saw
only replan #1's result — **not** the original plan or what attempt #1 already tried → nothing stopped
it re-emitting the same broken step (the Win+R relaunch loop); (b) the signal was a coarse
`failed@i:op` + raw JSON truncated to 1500 chars (clips the most relevant record); (c) when the
executive false-confirmed (Firefox "launch ok" on an error dialog) the **text** log actively misled
the planner.

**Reasoning was never enabled.** Planner = `Qwen/Qwen3-VL-8B-Instruct` (the non-thinking checkpoint),
called via `chat.completions` with `max_tokens=4000` and a greedy `\[.*\]` JSON extractor that a
`<think>` trace would break.

## Changes

### Reasoning mode (opt-in)
- **`config.py`**: `planner_thinking` (`AGENT_PLANNER_THINKING`), `planner_max_tokens`
  (`AGENT_PLANNER_MAX_TOKENS`), and `planner_effective_max_tokens` property — explicit budget wins,
  else auto **16000 if thinking else 4000** (also auto-bumps when `"thinking"` is in the model name).
- **`planner.py`**: `_extract_json` strips `<think>…</think>` (and a stray split `</think>`) **before**
  the array match — brackets inside the trace no longer corrupt the parse. `ClaudePlanner` /
  `LocalPlanner` / `HFPlanner` take `thinking=` and capture `last_reasoning`; Claude uses Anthropic
  extended thinking (budget < max_tokens, text pulled from the right block); Local/HF send
  `extra_body={"chat_template_kwargs":{"enable_thinking":True}}` with a no-flag retry, and read a
  split `reasoning_content`. `run_goal` now logs `reasoning` into `planner.json`.
- **The reliable lever is the model name.** Set `AGENT_PLANNER_MODEL=Qwen/Qwen3-VL-8B-Thinking`
  (dedicated reasoning checkpoint — reasons by default; the `enable_thinking` extra_body is only for
  hybrid Instruct models). The `Thinking` GGUF exists too if/when this moves local to the B580.

### Stateful, richer replan (the failure-feedback fix)
- **`planner.py` `summarize_result(result)`**: one compact, model-readable line per failed attempt;
  prefers the executive's `failure_summary`, falls back to synthesizing from the log; handles the
  no-op-guard case.
- **`planner.py` `Planner.replan(..., history=None)`**: builds a prompt with **PREVIOUS ATTEMPTS
  (already failed — do NOT repeat)** + **MOST RECENT FAILURE**, and asks the model to diagnose *why*
  before planning a *different* approach.
- **`run_goal`**: accumulates a `history` list across attempts and threads the full list into each
  replan (the cross-attempt memory fix). Streams a `recall:` event; returns `result["history"]`.
- **`executive.py` `_failure_summary(rec)`**: real diagnosis per op (click coord that didn't move
  the screen / grounder refused / launch unconfirmed / verify read-vs-expected) **plus the current
  on-screen text** (cheap tesseract read, once, on failure). `run_plan` attaches it as
  `result["failure_summary"]`. This is the "negative observation" — the planner now recovers from
  what's actually on screen, and a verify failure reports what it saw, not just a bool.

### Wiring
`server/app.py build_planner`, `tools/run_goal_once.py`, `tools/probe_planner.py` all pass
`max_tokens=CFG.planner_effective_max_tokens` + `thinking=CFG.planner_thinking`. `/health` surfaces
both.

## How to use
- Reasoning planner (cloud, no local VRAM):
  `set AGENT_PLANNER_MODEL=Qwen/Qwen3-VL-8B-Thinking` (+ optional `AGENT_PLANNER_THINKING=1`), restart
  the server / run `tools/run_goal_once.py`. Budget auto-bumps to 16000.
- The richer replan history is always on (no flag) and visible in the `recall:` stream + `planner.json`.

## Not done / next (needs the live rig — unchanged from the Firefox doc)
1. **Launch installed GUI apps via Start-menu search** (tap Win → type → Enter), keep Win+R for
   `cmd`/`ms-settings:` URIs.
2. **Harden `Executive.launch()` confirm**: detect a "Windows cannot find '<x>'" dialog, Esc it,
   return False — kills the silent false-positive that still poisons the *first* observation (the
   richer summary mitigates downstream, but fixing it at the source is better).
3. `scroll` plan op (firmware wheel v5 works). 4. Win10 default-browser idiom.
5. **Re-run firefox live** with `AGENT_PLANNER_MODEL=…-Thinking` and watch the `recall:`/PREVIOUS
   ATTEMPTS lines actually break the relaunch loop. Then re-run `measure.py --k 10` to confirm no
   keyboard-benchmark regression (guard/lint/feedback all live in `run_goal`, not `run_plan`, so it
   should be untouched).

## Live probe findings (no HID, 2026-06-21)

- Edited code runs live: the Instruct planner returns the same clean keyboard-first firefox plan,
  parses + lints clean (`tools/probe_planner.py`).
- **Reasoning-model availability gotcha:** `Qwen/Qwen3-VL-8B-Thinking` is NOT in the HF router's
  served catalog for this token — a bare call 400s ("no provider you have enabled") and a
  `:featherless-ai` pin returns an HTML error page. The model CARD's `inferenceProviderMapping` claims
  featherless serves it, but that mapping is stale; the **router catalog is ground truth**. featherless
  IS enabled (73 catalog entries name it) — the 8B-Thinking just isn't actually deployed. Diagnosed
  with the new `tools/diag_provider.py` + `tools/diag_router.py` (token identity via whoami = aaronh99;
  catalog listing; full pinned-error body).
- Thinking variants that ARE served to this token: **`Qwen/Qwen3-VL-30B-A3B-Thinking`** (MoE, ~3B
  active — the practical pick) and `Qwen/Qwen3-VL-235B-A22B-Thinking`.
- **Validated reasoning live** with `AGENT_PLANNER_MODEL=Qwen/Qwen3-VL-30B-A3B-Thinking`: the
  `<think>`-stripping + 16k budget gave a clean parse, and the plan was materially MORE complete than
  Instruct — it added `alt+f4` (close the terminal), launch-to-register, a click **Make Default**, AND
  the `ms-settings:defaultapps` flow (Instruct stopped at a bare, broken `launch Mozilla.Firefox`). Lint
  correctly appended the missing `done`. Still text-only (no frame); the bare-name `launch Firefox` +
  ms-settings grounding remain the live-rig unknowns.
- To use it: set `AGENT_PLANNER_MODEL=Qwen/Qwen3-VL-30B-A3B-Thinking` (budget auto-bumps via the name);
  `setx` it for persistence. **Default stays Instruct** (unchanged) — reasoning is opt-in.

## Live RIG run (HID, 2026-06-21) — feedback loop VALIDATED; launch primitive is the blocker

`tools/run_goal_once.py` with `Qwen3-VL-30B-A3B-Thinking`, firefox goal, max-replans 2. Final
`status=failed@6:launch`, 2 replans, ~5–6 min wall. run_dir `runs/firefox_re2_140238` (7 step frames +
planner.json with a **1041-char reasoning trace captured live**).
- ✅ Thinking planner ran end-to-end; reasoning_content captured; plan parsed clean (no `<think>`
  leakage); lint appended the missing `done`.
- ✅ **Replan feedback works live.** winget install succeeded; `launch Firefox` FAILED (Win+R bare
  name). `_failure_summary` OCR'd the real dialog — *"Windows cannot find 'Firefox'…"* — and streamed it
  as a `recall:` event; the planner responded by **clicking OK to dismiss that dialog** before
  re-approaching. Negative-observation → recovery is real, end-to-end.
- ❌ Task didn't complete: the planner kept re-emitting `launch Firefox`, which loops because `launch`
  is Win+R-only and a winget-installed app isn't bare-name runnable there. The planner even appended a
  smarter `ms-settings:defaultapps → click Firefox → verify` tail in attempt 2, but the early
  `launch Firefox` step aborted the plan before it got there. **Primitive gap, not a feedback gap** —
  confirms next-step #1 (launch installed apps via Start-menu search: tap Win → type → Enter) and #2
  (treat a "Windows cannot find '<x>'" dialog as a launch FAILURE) are the real unblockers.
- Repro: `runs/run_firefox.bat` (detached) → `runs/live_firefox.log`. Pre-flight: `tools/preflight.py`.

## Launch fix + live RE-RUN (HID, 2026-06-21) — default-browser flow UNBLOCKED

Implemented the executive launch fix (`kvm_agent/orchestration/executive.py`):
- `_is_winr_target` + `_WINR_COMMANDS`: route SYSTEM commands / `.exe` / `ms-settings:` URIs to Win+R,
  but an INSTALLED GUI app name (Firefox, Chrome…) to **Start-menu search** (`key 'win'` → type → Enter).
  Win+R can't run an installed app by bare name — that was the whole blocker.
- `_launch_error_dialog`: OCR-detect a "Windows cannot find '<x>'" dialog and treat it as a launch
  FAILURE (Esc it, fall back to Start search) — kills the false-positive where the dialog's title read
  the app name and `_app_open` confirmed it as "open".
- Planner idiom nudged to launch installed apps by friendly name (Firefox, not the winget id).
- Offline tests `tests/test_launch_routing.py` (28/28) + replan regression (27/27); compiles clean.
  Firmware check: `code.py` `_NAMED` has `win`/`esc`/`enter`, so `key('win')` taps Start.

Live re-run (`Qwen3-VL-30B-A3B-Thinking`, max-replans 2): final `status=failed@11:click`, but the fix
**unblocked the entire default-browser flow** that was 100% stuck before:
- `launch Firefox` now **ok** every attempt (Start search opened it; the cannot-find guard didn't even
  need to fire). The cascade that previously never ran: `click 'Set as default' ok` (Firefox first-run)
  → `launch ms-settings:defaultapps ok` → `click 'Web browser dropdown' ok` → `click Firefox ok`.
- Recovery quality: across 3 attempts the planner tried **different** approaches (clicks, then keyboard
  `ctrl+down`/`enter` nav in Settings); `recall:` correctly captured the live screen each time (a Firefox
  new-tab page on one fail, the Win10 *Default apps* page on the next).
- Remaining blocker (now isolated): the **Win10 set-default mechanic** — tile → flyout → "before you
  switch" nag → confirm, grounding the final pick, and a verify that reads the below-the-fold *Web
  browser* row. Maps to next-steps #3 (`scroll` plan op; firmware wheel v5 works) + #4 (encode the
  default-browser idiom, reusing `tools/isolate_default_browser.py`).
- Progress: pre-fix `failed@6:launch` (install only, looping on launch) → post-fix `failed@11:click`
  (install + open + register + into the default-apps flow). The launch primitive was the gating fix.

## General `scroll` plan op (2026-06-21)

A reach primitive — deliberately GENERAL, not the task-specific default-browser idiom (the planner
couldn't express "reach a below-the-fold target" for ANY task; that was a capability gap like `launch`):
- Schema `{"op":"scroll","direction":"down|up","amount":<notches>}` (amount = magnitude; sign from
  direction). Added to PLAN_SCHEMA_DOC + `_OP_FIELDS`. NOT in `_ACTIONABLE` (like sleep/verify), so
  the no-op guard still requires a real action — a scroll-only plan can't "succeed".
- `Executive.scroll()` parks the cursor at screen center (so the wheel hits the scrollable pane),
  then sends notches via the firmware wheel (`r4.scroll`: +up / -down, the v5 wheel flashed +
  live-verified earlier this session). Wired into `run_plan`.
- Planner SYSTEM idiom: scroll an off-screen target into view BEFORE clicking it.
- Tests `tests/test_scroll_op.py` (11/11) + launch (28/28) + replan (27/27) regression; compiles +
  imports clean. Only the plan-layer wiring is new — the HID wheel underneath was already proven live.
- Composes with the replan loop now (scroll, next attempt re-observes). A closed-loop "scroll UNTIL
  the target is visible" belongs to the closed-loop-execution investment, not this primitive.

## Click-step grounding: diagnosis + pre-click verification (2026-06-21)

DIAGNOSIS (`tools/diag_clicks.py` over the live run_dirs — data, not guesses): across 13 live clicks
the grounder returned a coordinate **100% of the time (0 declines)**. Split: 6 "frame changed → ok",
7 "no change → failed after retries". The crosshair frame for a *reported-ok* Firefox click
(`runs/firefox_141800/10_click.png`) shows the click landed on the **taskbar** while a browser
new-tab page was foreground — NOT the Settings chooser. Conclusions:
- Grounding PRECISION is not the bottleneck (coords every time; ~50px in the keyboard work).
- The real issue: click success was judged by a GLOBAL frame-diff ("did any pixels move"), and
  UI-TARS returns a coordinate for ANY named target even when it isn't on the current screen — so a
  confident wrong-state / look-alike click gets rubber-stamped because something moved, while a
  correct-but-subtle click gets marked failed.

FIX (`executive.py`) — general, every click, every task:
- `_ground_ok(target, xy, png)`: PRE-CLICK gate. Crops around the grounded point and asks the vision
  model "is the element at the center `<target>`?". A 'no' **ABSTAINS — the click is never sent** —
  instead of firing a wrong click whose side effects then have to be undone. FAIL-OPEN if no verifier.
- `_click_effect(before, after, xy)`: LOCALIZED post-click diff (a crop around xy) instead of the
  global frame-diff; a stray background repaint no longer counts as success.
- `click_target` uses both; a rejected ground sets `last_ground['verified']=False`, and
  `_failure_summary` now says "target NOT found on the current screen — scroll to bring it into view,
  or the wrong screen is showing", which pairs with the new `scroll` op so the planner can recover.
- Tests `tests/test_click_verify.py` (14/14, incl. "rejected ground → NO click sent") + scroll/launch/
  replan regressions green; compiles + imports clean. Clicks cost +1 vision call (fail-open, bounded);
  the keyboard benchmark has no click ops, so it's unaffected.

RESIDUAL (honest): truly AMBIGUOUS short labels (a Firefox icon in the taskbar AND the chooser entry
we wanted) can't be fully disambiguated without CONTEXT. The deeper prevention is window/context
awareness — confirm the expected app is foreground / carry an expected-context per step — which is the
closed-loop investment, noted for next.

## Live validation of the click gate + scroll (HID, 2026-06-21)

Ran firefox live with all new capabilities. Final `status=failed@12:click`, but the validation is
CLEAN — the gate behaved exactly as designed (verified with `tools/diag_clicks.py`, which now reads
the `verified` flag, + frame inspection):
- **CLICK GATE**: for the 3 gate-enabled attempts — **3 abstentions, 1 verified click (Cancel) that
  worked, 0 over-blocks, 0 blind wrong-state clicks.** Every abstention was CORRECT: step 7 'Set
  default' / step 8 'Make Default' grounded onto a "Problem with Shortcut" dialog (not Firefox) →
  abstained; step 12 'Microsoft Edge' → the Default-apps page showed the current default = Google
  Chrome (no Edge tile) with the dialog overlapping (`runs/firefox_re2_150918/12_click.png`) →
  abstained. The gate DISCRIMINATES — it allowed 'Cancel' which really was there — so it is not
  blanket-rejecting.
- **SCROLL**: the planner emitted a scroll op (taking the "scroll to bring it into view" hint from a
  failure summary) and it executed — the frame confirms the Default-apps page scrolled to reveal the
  'Web browser' row.
- The negative-observation → recovery chain fired: abstain → "target not on screen / wrong window" →
  planner clicks Cancel / taps Esc and switches approach (straight to ms-settings + scroll).

WHY it still failed (all NON-grounding, newly revealed):
1. TARGET-MACHINE: `launch Firefox` hits a BROKEN "Firefox Private Browsing" Start shortcut
   (`private_browsing.exe` moved) → a "Problem with Shortcut" dialog, so Firefox never opens. The
   install left a broken/secondary shortcut as the top 'Firefox' Start hit.
2. That dialog PERSISTS and overlaps Settings; Esc/Cancel didn't fully clear it (modal-handling gap).
3. The planner ASSUMED the current default was Edge; it's actually Chrome → 'click Microsoft Edge' had
   no valid target (correctly abstained).

VERDICT: the click gate + scroll work as intended (0 blind clicks, 0 over-blocks, correct recovery).
The remaining blockers are target state + modal handling + a wrong planner assumption — i.e. the
context/closed-loop and modal-handling work, NOT click grounding.

## Hindsight memory integration (2026-06-21)

Connected the planner to the local Hindsight server (vectorize-io/hindsight, MIT) at
`192.168.0.184:8888`, bank `TARS`. Architecture = ORCHESTRATOR-SIDE RAG (the planner stays a single
completion; `run_goal` recalls and arms `planner.context`, `_inject` prepends it to the user message —
SYSTEM stays frozen).
- `kvm_agent/memory/hindsight.py`: no-dep urllib client. `recall_block(goal)` → formatted, de-duped
  fact block; `retain(content)` → store. FAIL-SOFT (any error → empty/False; a memory outage never
  breaks a run). Endpoints confirmed via the server's `/openapi.json` (`POST .../memories` with
  `{"items":[{content,context}],"async":false}`, `POST .../memories/recall` → `{"results":[{text,type}]}`).
- CFG: `hindsight_url` / `hindsight_bank` / `hindsight_enabled` (opt-in, default OFF → preserves the
  no-memory A/B baseline + the keyboard benchmark).
- `run_goal(memory=...)`: recalls for the goal, arms `planner.context`, clears after (no cross-task
  leak). Wired into `server/app.py` (CFG flag), `run_goal_once.py` (`--memory`), `probe_planner.py`
  (`--memory`, the no-HID A/B harness). Tests `tests/test_memory.py` (17/17) + regressions; clean.

FIT (validated empirically): Hindsight's `retain` is LLM-extractive and fact-oriented — world facts +
experiences round-trip cleanly; a multi-step procedural idiom barely extracted (~8 output tokens). So
point it at the experiential/world-fact layer; keep procedures in the SYSTEM prompt.

A/B RESULT (probe_planner, firefox goal, 30B-Thinking, no HID): seeded 4 facts (default=Chrome, winget
recipe, broken FF shortcut). The recall changed the plan exactly where it fixes the last live failure:
- WITHOUT memory the planner assumed the default was Edge → `click 'Microsoft Edge'` (the gate then
  abstained, because the default is Chrome).
- WITH memory → `click 'Google Chrome'` (the CORRECT current-default tile) → `click 'Firefox'`.
The default-browser world fact directly corrected the wrong assumption. (The broken-shortcut fact did
NOT fully remove the launch-Firefox step — partial.)

GOTCHAS:
- Windows console `UnicodeEncodeError` printing recalled text (non-cp1252 chars) → reconfigure
  `sys.stdout` to utf-8/replace in the console tools (`probe_planner`, `run_goal_once`). The SERVER
  path is unaffected (recall goes into the prompt over HTTP, not the console).
- The Hindsight server's extraction LLM emitted DUPLICATE facts in Chinese (在Windows上…) alongside the
  English ones — bloats recall; consider constraining extraction to English / dedup beyond exact-text.
  (Server-config issue, not our client.)

## Write-back loop + live memory validation (2026-06-21)

- `retain_recipe(goal, plan)`: on a successful run, retain the working step sequence as a natural-
  language experience (`_plan_to_text` — extracts far better than op JSON). `CFG.hindsight_write`
  (opt-in, default OFF); `run_goal(write_memory=...)` retains ONLY on `status==done` (never learn a
  broken sequence). Wired into server + `run_goal_once.py` (`--write`). Tests `test_memory.py` 24/24.
- LIVE validation (notepad goal, `AGENT_HINDSIGHT`+`--write`, 30B-Thinking, real rig): the FULL loop
  ran — `memory: recalled 6 fact(s)` → launch notepad → type → verify → **done** → `memory: retained
  the successful recipe`. The bank `TARS` went 6→8 memories; the new entries are the extracted recipe
  ("…the kvm-agent successfully completed a task involving opening Notepad, typing 'hello from memory',
  and verifying…"). The carry-forward loop is closed end-to-end on the rig — the agent now REMEMBERS
  how it did the task.
- Recall-HELP was already proven at the plan level (probe: Edge→Chrome). A live firefox run would
  confirm it through HID but is blocked by the target's broken FF shortcut (target state, not memory).
- Cosmetic: the bullet char shows as mojibake in the log (utf-8 stdout vs cp1252 Get-Content); the run
  is fine.
- KNOWN debt: repeated successes retain near-duplicate recipes (plus the server's Chinese-dup
  extraction) → recall bloat over time. Future: dedup-on-write (recall first, skip if similar) and/or
  rely on Hindsight's consolidation.

## Dedup-on-write + closed-loop guards (2026-06-21)

DEDUP-ON-WRITE (`kvm_agent/memory/hindsight.py`): `retain_recipe` now recalls first and SKIPS the
write if a sufficiently-similar recipe already exists (≥0.6 keyword overlap on DISTINCTIVE terms — op
verbs/filler are stop-listed so it keys on app names/values, with light stemming). Fail-open (recall
error → still write, don't lose a recipe). Validated LIVE against the real server: re-retaining the
notepad recipe (already in `TARS`) returned **False = skipped**.

CLOSED-LOOP GUARDS (`executive.py`, in `run_plan`, both safe/opt-in):
- `_blocking_dialog` + pre-CLICK auto-dismiss: before a click, if a blocking ERROR dialog is on
  screen (conservative phrases: 'cannot find', 'problem with shortcut', 'this shortcut', 'not
  responding', …) Esc it first — so a stale error popup (e.g. the broken-shortcut dialog) is cleared
  instead of clicked onto/under. CLICK-ONLY → the keyboard benchmark (no clicks) is untouched;
  `guard_dialogs` flag to disable. Conservative phrases so legitimate flow dialogs (first-run window,
  'before you switch' nag) are NOT dismissed.
- Optional per-step `precondition`: any action step may carry `"precondition":"<yes/no description of
  the expected window/screen>"`; `run_plan` confirms it via vision BEFORE acting and FAILS the step
  (→ replan) if unmet — so the agent never types/clicks into the wrong window. Opt-in (steps without
  it are unaffected; None-vision → fail-open). Planner SYSTEM idiom + schema teach the model to add
  preconditions on context-critical steps.
- Tests `tests/test_closed_loop.py` (14/14); `tests/test_memory.py` now 28/28; all regressions green.

## Live firefox run, FULL stack (HID, 2026-06-21)

Run with memory (recall+write) + Thinking planner + scroll + click gate + closed-loop guards. Final
`status=failed@6:click`, 2 replans.

WORKS (validated live):
- Memory recall fired (6 facts); the plan was more COMPACT than the no-memory baseline (9 vs 13 steps).
- ERROR-DIALOG GUARD fired **twice** ("cleared a blocking error dialog before clicking") — it detected
  the broken-shortcut popup and Esc'd it before the click. The closed-loop guard works on the rig.
- The click gate kept abstaining correctly on wrong-state targets (Make Default / Set as default).

GAPS (honest):
- The planner did NOT adopt `precondition` (the idiom is in the prompt; the 30B emitted none). Likely
  needs a few-shot example or a stronger model to pick up a new field.
- The planner STILL launched Firefox despite the recalled fact "the FF shortcut is broken… opens a
  Problem-with-Shortcut dialog." Memory was injected but the model didn't REASON over it to avoid the
  dead path — a planner-capability gap, not a memory-plumbing gap. (Also: the guard's pre-click OCR
  occasionally misses the dialog at guard-time before the click gate catches it — best-effort.)
- Task blocked by the target's broken FF shortcut (target-machine), so no completion → no write-back.

TAKEAWAY: every mechanism built this session fires correctly (recall, dedup, error-dialog guard, click
gate, scroll). The remaining wall is the planner not ACTING on injected knowledge (and the target
shortcut). Highest-leverage next levers: make the planner USE recalled facts — few-shot the
precondition idiom, surface blocking facts (like "shortcut broken") more imperatively, or have the
planner explicitly reason about which recalled facts invalidate a step before planning it; and fix the
target's Firefox shortcut so the task can actually complete.
