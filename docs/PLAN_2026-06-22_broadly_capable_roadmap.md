# Plan — Roadmap to a Broadly Capable Computer-Use Agent

_Authored 2026-06-22. Scope: what's between today's working-but-narrow rig and a **broadly
capable** agent, and the concrete ordered steps to close it. Grounded in a static read of the
current tree (planner.py, executive.py, hindsight.py, measure.py, rig.py, the 2026-06-22 session
docs); anything touching the rig has to be re-verified live, same caveat as every prior doc._

---

## 0. The gap in one paragraph

The hard parts of the system are **done**: a hardware-injection path that drives a real desktop
undetectably, a sound planner / executive / verifier split, two orchestration loops sharing one
step chokepoint, a memory loop, and real preemption hardening. But **every reliability number we
trust comes from two keyboard-only toy tasks** (the 10/10 `measure.py` notepad+calc benchmark and
the new compute-&-transcribe pass). Both are deliberately built to **avoid the general problem** —
no arbitrary clicking, built-in apps only, vision-verifiable end state. "Broadly capable" is
exactly the territory those benchmarks exclude: clicking UI with no keyboard path, across many app
types, verified honestly at a known pass *rate*. The work ahead is not a redesign — it is
**measuring the parts we've been avoiding, then driving them up one variable at a time.**

---

## 1. Decisions locked this session (drive the ordering below)

1. **Planner end-state: HYBRID, permanent.** Local 9B (B580) for easy tasks; escalate hard
   planning to Claude / a bigger model. _Consequence:_ the open "capped `--reasoning-budget`"
   problem **dissolves** — run the 9B at budget 0 (fast) for the easy tier and route hard tasks to
   a thinking cloud planner. We never need a middle reasoning setting.
2. **Target meaning: SEQUENCED depth→breadth.** Pick representative task families, drive each to a
   high K-rep pass rate, *then* widen. Not breadth-first coverage; not a single curated set forever.
3. **First focus: EVAL HARNESS + task suite.** Build the measurement spine before improving
   anything, so every later gain is attributable. This is Phase 1 and gates the rest.

---

## 2. What's already solid (build on, don't re-litigate)

- **Hardware primitives** all act: launch/type/tap/key/click/scroll, caps-lock self-correct,
  finite recv timeout (`code.py`, `pico_client.py`).
- **Architecture** (`orchestration/`): keyboard-first executive + stateless UI-TARS grounding +
  screen-truth `Verifier`; `_run_one_step` is the single shared step chokepoint feeding both
  `run_plan` and the per-step `run_goal_step`.
- **Plan-time lint** (`validate_plan`) and **per-step frame capture** preempt whole classes of
  silent failure; the default-browser tile→flyout flow is solved.
- **Memory** (`hindsight.py`): recall → directives + machine-enforced gates → write-back, all
  fail-soft.
- **Local planner** stood up (llama.cpp Vulkan, Qwen3.5-9B, `rig.py status` health-checks it).
- **Methodological discipline** is already the house style: K-rep not single-sample, verify from
  the screen, isolate one variable. The roadmap just extends it to the parts we haven't measured.

---

## 3. Phase 1 — Evaluation harness + task suite **(do first)**

**Why first:** we cannot honestly improve grounding or planning without a rate to move. `measure.py`
is the right pattern but is hardcoded to the single `multiapp` task and calls `run_plan` with a
**fixed** plan — it measures the executive, not the agent. We need a registry-driven harness that
measures the **full planner→executive→verifier path** across **diverse** tasks, K reps each,
screen-verified.

### 3.1 Task spec (new: `kvm_agent/eval/tasks.py`)

A `Task` dataclass, declarative so verdicts are auditable and independent of what the agent claims:

```python
@dataclass(frozen=True)
class Task:
    id: str                      # "calc_transcribe"
    goal: str                    # NL string handed to the planner
    family: str                  # office | system | files | browser | multiapp
    tier: int                    # 1 easy/offline/non-mutating … 3 hard/grounding/network
    verify: dict                 # the HARNESS's own end-state check (NOT the plan's):
                                 #   {"ask": "..."} | {"text": "..."} | {"number==": "..."}
    needs_grounding: bool = False
    needs_network:   bool = False
    teardown: list | None = None # UI steps to undo side effects (mutating tasks only)
```

**The harness applies its OWN `verify` to the final frame**, regardless of whether the agent
self-verified. This structurally fixes the "success decoupled from the screen" worry the last
session flagged (the live plan that dropped the final Notepad verify): the benchmark's verdict never
depends on the agent checking itself.

### 3.2 Harness (new: `tools/bench.py`)

- Loads the registry; runs `--task ID` / `--family F` / `--tier N`, `--k` reps.
- `--mode planned` (default): real planner via `run_goal` / `run_goal_step` — end-to-end rate.
  `--mode fixed`: a `RulePlanner`/hardcoded plan — isolates **executive** reliability from planner
  variability (keeps `measure.py`'s discipline; lets us tell a planning failure from a primitive one).
- Between reps: `ex.reset_clean()`, then `teardown` if the task mutated state.
- Emits `runs/bench_<ts>/summary.json`: per-task `{k, passes, rate, mean_secs, replans}` + a
  leaderboard print + per-rep final frame (spot-audit) + the existing `planner.json` per run.
- **Reuse, don't fork:** wrap `run_goal` (planner.py) and `Verifier` (executive.py); `bench.py`
  is a registry + loop + reporter, ~150 lines. Retire `measure.py` to one registry entry.

### 3.3 Seed suite (~10 tasks, all plausibly passable today → gives a baseline now)

Tier-1 is **non-mutating or self-resetting** so reps are independent without a shell on the target:

| id | family | goal | harness verify | grounding? |
|---|---|---|---|---|
| `calc_transcribe` | multiapp | compute 47×89 in Calculator, type result in Notepad | `number==`/`text` 4183 | no |
| `calc_basic` | office | compute a random a×b in Calculator | `number==` | no |
| `notepad_list` | office | open Notepad, type a 3-item list | `text` | no |
| `notepad_replace` | office | type text, Ctrl+H replace X→Y, verify Y present | `text` | no |
| `settings_about` | system | open `ms-settings:about`, read the Windows edition | `ask` | light |
| `settings_defaultapps_read` | system | open default-apps, report the current browser | `ask` | light |
| `explorer_nav` | files | open Explorer, go to a known folder, confirm a file is listed | `ask`/`text` | light |
| `wordpad_format` | office | type a word, bold it (Ctrl+B), verify | `ask` | no |
| `clipboard_roundtrip` | multiapp | type in Notepad, copy, paste into a 2nd Notepad | `text` | no |
| `set_default_browser` | system | set default browser to X (the solved hard-GUI flow) | `ask` | yes |

**Acceptance:** `python tools/bench.py --tier 1 --k 5` prints a per-task rate table, screen-verified,
reproducible. **These baseline rates are the spine** — Phases 2–4 move them and the harness says by
how much. First two registry entries also discharge the queued NEXTs: K-rep the compute-&-transcribe
task, and the **local-vs-Claude A/B on the same task** (`--planner local|claude`).

---

## 4. Phase 2 — Hybrid planner routing

**Why second:** it unblocks the entire breadth program without waiting for a small VLM to get smart,
and Phase 1 gives us the rate to prove the router is sound.

### 4.1 `RoutedPlanner(Planner)` (new in `orchestration/planner.py`)

Wraps a `local` and a `cloud` planner, same plan schema. Two routing signals — ship both:

1. **Static tier** (deterministic, for measurement): tier-1 → local, tier-3 → cloud, tier-2 →
   local-first. Drives a clean local-only / cloud-only / routed A/B on the bench.
2. **Escalate-on-failure** (the runtime default): try local `decompose`; if `run_goal` fails after
   1 local replan, re-run with cloud. Robust without an oracle — a wasted local attempt is cheap.
   Cleanest hook: `RoutedPlanner.replan()` switches to the cloud backend after N local failures (it
   already receives the attempt `history`), so `run_goal` needs no change.

### 4.2 Local-tier improvements (only for tasks we actually route local)

- **Run the 9B at `--reasoning-budget 0`** for the easy tier (hybrid makes this correct, not a
  compromise). Thinking-on cloud handles hard.
- Few-shot / tighten the local idioms it gets wrong (the find-path "click terminal text" logic
  error from the b580 session) — but only on the routed-local task set, measured on the bench.
- **Later, optional:** bench a different ≤12GB VLM for the local tier against the 9B on the Phase-1
  suite (use the HF MCP — `hub_repo_search` / `paper_search` — to shortlist current small grounding/
  planning VLMs). Decide by rate, not vibes.

### 4.3 Decision criterion

On the bench, **routed should match cloud's pass rate at a fraction of cloud's calls.** That single
table is the apples-to-apples the docs have wanted for three sessions. Wire the chosen planner kind
into `CFG.planner_kind` / `rig.py run`.

---

## 5. Phase 3 — Grounding: measure, then harden **(the breadth unlock)**

**Why it can't be skipped:** keyboard-first is how we've *avoided* grounding. Every family past
office — browser links, toolbars, menus, canvases, custom apps — has targets with no keyboard path.
Grounding accuracy is currently **unmeasured**; UI-TARS-as-grounder has guards but no number.

### 5.1 Grounding bench first (new: `tools/bench_ground.py`, **offline, no rig**)

Generalize `tools/probe_grounding.py` (today a single saved-frame A/B) into a scored eval:

- **Dataset:** ~30–50 saved frames from `runs/` + new captures spanning Settings tiles, browser
  toolbar/links, Explorer rows, dialog buttons, menu items, small icons. Annotate each with target
  descriptions + ground-truth click-acceptable boxes (one-time; a tiny cv2/HTML box-drawer or
  hand-entered coords).
- **Score per target:** hit rate (`ground()` point ∈ GT box), **abstain-when-absent** rate
  (`_ground_ok` correctly refuses a target that isn't there — the key *safety* property), and
  false-confident rate (confident click on the wrong point).
- Runs against the laptop Ollama only → fast, repeatable, no shared-rig contention. This is
  `measure.py` for grounding.

### 5.2 Then harden / compare, measured on 5.1

- **UI-TARS q4 vs q8** for grounding accuracy on diverse targets (q4→q8 mattered in the EvoCUA
  saga; re-test for *grounding*, not arithmetic).
- **Two-stage crop-and-ground** (high-leverage; the docs pointed at it but never built it): for a
  small/dense target, coarse-localize, crop the region with the existing `Executive._crop_around`,
  ground in the *enlarged* crop, offset back. Directly attacks the standing "small right-edge target
  fails on horizontal resolution" hypothesis.
- **Quantify the guards:** how much do `_ground_ok` (pre-click vision verify) and `_click_effect`
  (localized diff) cut false-confident clicks? Keep what the bench says helps.
- **Candidate alternative grounders** to bench (verify availability/licensing via the HF MCP before
  committing — don't assume): newer UI-TARS, OS-Atlas, Aria-UI, Qwen2.5-VL grounding mode.

**Acceptance:** a grounding hit-rate + abstain-rate baseline exists; the winning grounder/config is
wired into `Executive.click_target`; abstain-on-absent stays high (never trade safety for hit rate).

---

## 6. Phase 4 — Sequenced depth→breadth: drive families to reliability

With harness + routing + grounding in place, **expand the suite one family at a time, K-rep to a bar
(≥9/10) before adding the next.** Order families by grounding load (keyboard-heavy → grounding-heavy),
matching the depth-first sequencing:

1. **Office text & math** (have it; keyboard) — calc, notepad, wordpad, find/replace, hotkey format.
2. **System / settings** (light grounding) — generalize the solved `ms-settings:` tile→flyout flow:
   default apps, display, read-a-value.
3. **Files / Explorer** (keyboard nav + some grounding) — navigate, create folder (mutating →
   `teardown`), rename, search.
4. **Browser** (grounding-heavy, network → tier-2/3) — open a page, click a link, fill a field, read
   a value. The real breadth test; kept out of the deterministic baseline.
5. **Multi-app workflows** (integration) — read A → compute → write B (compute-&-transcribe is the
   seed). These exercise the closed loop's per-observation edge.

**Per family:** add 2–4 tasks → run the bench → fix failures (planner idiom / grounder / primitive)
→ re-run → lock the rate, attributing each delta to one change (the `FINDINGS_*` discipline).

**This is also where `run_goal` vs `run_goal_step` finally gets decided** — the open A/B the last
session called "a wash" on one task. On family-5 multi-surprise tasks, with local per-step calls now
cheap (Phase 2), measure which loop wins and flip `AGENT_CLOSED_LOOP` on the data, not an anecdote.

---

## 7. Cross-cutting — robustness debt (do early; it de-noises the benchmark)

These add no capability but **multiply reliability**, and they corrupt Phase-1 numbers if left:
a hardware blip currently reads as a capability failure. From the optimization backlog:

- **R1 — per-command ACK** (`code.py` + `pico_client.py`): a 1-byte `OK` per command so a half-open
  socket is **detected and retried**, not a silent dead rollout. Highest-value robustness item; a
  half-open today costs an entire rep. **Do before trusting bench rates.**
- **R2 — wait-for-stable**: replace the ~20 fixed `time.sleep`s with frame-stability polling
  (`_frame_diff` already exists). Faster *and* less flaky → cuts both wall-time and benchmark noise.
- **R3 — verifier model-swap thrash**: confirm `tesseract.exe` is installed on the desktop so the
  deterministic OCR path is used, not the GPU-swapping `qwen2.5vl` vision path (`Verifier.__init__`
  silently falls back). Reduces both latency and verdict noise.

Together R1+R2 convert the loop from "fixed-time, fail-silent" to "event-driven, fail-detected."

---

## 8. The verification ceiling (an honest constraint, not a bug)

Because **nothing is installed on the target**, all verification is vision/OCR over the HDMI frame.
Implications for "broadly capable," and how to live with them:

- **The metric itself has an error rate** (we already saw `qwen2.5vl` false-negative "is Firefox
  open?"). Mitigate: prefer deterministic OCR for literal text; for benchmark verdicts, require **two
  verifier models to agree** (not for the live loop — too slow); keep per-rep final frames for human
  spot-audit (the harness already saves them).
- **Some end states aren't visible** (a file's bytes, a registry value). From the rig alone you
  cannot verify them. **Task-design rule:** make success *visible* — e.g. verify a written file by
  opening it in Notepad and reading it. Bake this into the `Task.verify` design so the suite stays
  honestly checkable.

---

## 9. Do-this-first checklist (the concrete start)

1. **R1 + R3** (ACK + confirm tesseract) — quick, and they make every subsequent number trustworthy.
2. **`kvm_agent/eval/tasks.py` + `tools/bench.py`** — registry + loop + reporter; port `measure.py`'s
   `multiapp` in as entry #1.
3. **Seed the 10-task tier-1 suite** (§3.3); run `python tools/bench.py --tier 1 --k 5`; **commit the
   baseline `summary.json`.** This is the number the whole roadmap moves.
4. **`RoutedPlanner`** (static-tier + escalate-on-failure); run local-only / cloud-only / routed on the
   bench; lock the routing that matches cloud's rate at lower cost.
5. **`tools/bench_ground.py`** + annotate ~30 frames; baseline grounding hit/abstain rates; then the
   crop-and-ground two-stage and the q4/q8 compare.

Then Phase 4: widen family by family, K-rep to the bar, one variable at a time.

---

### Appendix — explicitly out of scope (and why)

- **Solving the all-local hard-planning problem** — the hybrid decision routes around it; revisit
  only if a future ≤12GB VLM benches well on §4.2.
- **Capped `--reasoning-budget`** — moot under hybrid (budget 0 local, thinking cloud).
- **An OSWorld-style automated VM benchmark** — the rig is real-hardware with vision-only verify;
  our suite must be screen-checkable (§8), so we build a curated real-desktop suite, not a port.
- **Async / multi-rig** — one physical rig, one task at a time stays correct.
