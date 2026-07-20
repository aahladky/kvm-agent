Hardware Computer-Use Agent — Session Handoff

★★★ ALL AGENTS: read and follow AGENTS.md (Agent Working Agreement) in this repo
before touching anything. Output goes in runs/ and nowhere else; nothing
project-related in hidden dirs; the model is the last suspect. ★★★

What this project is

A KVM-over-IP-style computer-use agent where nothing is installed on the target machine. A vision model sees the target's screen via HDMI capture, decides actions, and a physical USB-HID device injects mouse/keyboard. Target sees only a monitor + a USB mouse/keyboard — undetectable, OS-agnostic. Pure curiosity project, no practical application.

Repo layout (cleaned 2026-07-20)

Code (~120M, tracked):
- kvm_agent/          — canonical package (models, hardware, orchestration, server). Active.
- agent_loop_holo.py  — CURRENT agent loop (Holo3.1 + WAA). Where new work happens.
- appliance/, waa/, tools/, tests/, docs/ — current-gen support: appliance code,
  WindowsAgentArena runner + shakedown results, harnesses/probes, unit tests, session reports.
- boot.py, code.py    — Pico firmware; stay at root (deployed to CIRCUITPY).
- cua_agent.py, evocua_agent.py, uitars_agent.py, executive.py, planner.py, pico_env.py,
  r4_client.py — BACK-COMPAT SHIMS (5-line re-exports into kvm_agent/). tools/ still
  imports them; delete only after tools/ is updated to import from kvm_agent.* directly.
- _archive/           — dead generations, kept for reference. Do not extend; add only.

Data (untracked, gitignored, moved out of the repo 2026-07-20):
- runs    -> ~/data/kvm-agent/runs      (symlink; benchmark evidence, 4.8G)
- scratch -> ~/tmp/kvm-agent-scratch    (symlink; auto-deleted after 14 days —
  nothing you want to keep goes here; use runs/ or _archive/ instead)

═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-07-19 (LATEST): REAL WIN32 FOCUS-TRANSFER BUG FOUND + FIXED, 3-DEPTH
SHAKEDOWN RUN, NATIVE HOLO-DESKTOP-CLI PROMPT PORTED — see
docs/SESSION_2026-07-19_holo_focus_bug_and_native_prompt_port.md ★★★
═══════════════════════════════════════════════════════════════
★ Architecture has moved on since the notes below this block: the project now runs on
Holo3.1 (H Company's model, native OpenAI tool-calling) driving the Pico/capture-card rig
via agent_loop_holo.py, evaluated against WindowsAgentArena tasks (waa/runner.py) —
NOT the old EvoCUA/UI-TARS/B580-planner stack the rest of this file describes. See
docs/REPORT_2026-07-19_problems.md for the state that started this session (a skeptical
review of the WAA-adoption arc) and the new session doc above for everything since.
★ THE REAL BUG (root-caused, reproduced twice, not guessed): launching an app on the
Windows target does NOT reliably transfer real Win32 keyboard focus to it.
GetForegroundWindow() confirmed focus silently stayed on the desktop (Program
Manager/FolderView) after Win+R-launching Notepad, even though Notepad rendered on top
looking focused — every keystroke went to the desktop's search-jump, not the document.
Documented, general Windows/RPA-industry gotcha (Microsoft's own Power Automate docs:
"Focus window alone is not reliable, always click afterward"). FIXED in
agent_loop_holo.py's _execute(): a `type` that produces no visible screen change now
clicks screen-center to force real focus before retrying, instead of blindly resending
into a window that was never focused. Verified by replay: two prior unguarded replays of
the same failing action sequence produced ZERO typed characters; with the fix, the text
landed correctly. This was chased down through ~40+ isolated mouse-reliability tests that
all disproved "dead mouse" (47+ clean clicks) before the real cause was found — see the
session doc's §2 for the full false-leads list, kept for honesty.
★ 3-DEPTH SHAKEDOWN (17 tasks × HOLO_HISTORY_IMAGES∈{1,2,3}, ~6.1h overnight,
tools/shakedown_ab.py): history=1 5/17, history=2 7/16, history=3 7/15 — see
waa/shakedown_results/manifest.json. windows_calc went 0/9 across all three depths, but
NOT for one reason: pulled H Company's own holo-desktop-cli, ran the identical failing
calc task natively (no Pico, no capture card) and it PASSED — same model, different
pipeline. Resolution and history-depth hypotheses for the gap were both directly tested
and DISPROVEN. Root cause: a genuinely inconsistent WinUI3 date-picker widget (live
double-reproduced) plus, in one run, a stuck-popup click bug structurally different from
the Notepad focus bug (Calculator held real Win32 foreground focus throughout — see the
session doc §4 for the full forensic trace).
★ NATIVE PROMPT PORTED (kvm_agent/models/holo.py, agent_loop_holo.py): captured
holo-desktop-cli's actual system prompt via a logging proxy (26,000 chars vs our ~700;
JSON-schema-constrained output, not tool-calling). Ported 3 adoptable wins, adapted (not
copied) for our one-tool-call-per-step architecture: an explicit loop-detection
instruction, an optional `note` param on every action tool + a persistent notes block
that survives goldfish-memory image eviction (native's actual fix for the same problem
history-depth tuning was trying to solve), and a stricter termination checklist. Verified
safe (self-tests pass); live-tested on the hardest calc task showed a real but partial
effect (loop-detection changed failure shape, notes saw zero uptake) — NOT yet validated
on the easier task class it's actually aimed at. See session doc §5, §7 for next steps.
★ WORKING TREE HAS REAL, TESTED, UNCOMMITTED CHANGES as of this block (agent_loop_holo.py,
kvm_agent/config.py, kvm_agent/hardware/env.py, kvm_agent/models/holo.py, waa/runner.py,
new tools/shakedown_ab.py + tools/show_reasoning.py) — not committed per "only commit
when explicitly asked"; see session doc §7.
═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-22: PORT FIX + REASONING-BUDGET REALITY + FIRST ALL-LOCAL PASS ON A NEW BENCHMARK — see docs/SESSION_2026-06-22_local_portfix_and_calc_benchmark.md ★★★
═══════════════════════════════════════════════════════════════
★ FIRST ALL-LOCAL END-TO-END PASS. The B580 9B (llama.cpp Vulkan, now on port 8090) planned + the executive
ran the NEW benchmark "Compute 47×89 in Calculator, then type the result in Notepad" to `done` in 19.6s, 0
replans, SCREEN-VERIFIED (Notepad shows 4183 via OCR, not self-report). Run: runs/calc_transcribe_160217.
DROPPED FIREFOX as the repeatable benchmark (network/winget + mutates persistent state + broken FF Start
shortcut on target = runs not comparable). NEW benchmark = "compute & transcribe": built-in apps, NO network,
resettable, keyboard-only (NO UI-TARS grounding) → isolates PLANNING + the keyboard executive; verifiable
(calc display + Notepad text). Goal: "Compute 47 x 89 using the Calculator app, then open Notepad and type
the result." (= 4183).
TWO ENVIRONMENT FIXES that were blocking "is the model even reachable":
  1. PORT CONFLICT. Docker/SearXNG binds 127.0.0.1:8080 SPECIFICALLY and shadows llama-server's 0.0.0.0:8080
     on loopback → the planner (base_url 127.0.0.1:8080) was hitting SearXNG (got 404 HTML through the OpenAI
     client), NOT the model. FIX: serve llama-server on 127.0.0.1:8090 (specific loopback bind → Docker can't
     shadow it); CFG.planner_local_url default is now :8090. Relaunch from C:\Users\aahla (model files there);
     logs at C:\Users\aahla\llama_8090.{out,err}.log.
  2. --reasoning-budget 256 does NOT cap thinking on build b9692 (one decompose = 3419 tok / 78.8s with
     enable_thinking=True). This build is BINARY: 0=off, non-zero=on/unbounded. The prior NEXT-step #1 ("capped
     256/512 sweet spot") is NOT achievable here — ABANDON it. budget 0 (fast ~10s) vs on (~78s/call) is the
     real choice. For the EASY new task, budget 0 plans it PERFECTLY + fast (offline probe AND live run) — so
     the prior "budget 0 plans badly" was a firefox/HARD-task finding, not universal.
GAP: the LIVE plan DROPPED the final Notepad verify (only the calc display was verified) — run-to-run variance
at budget 0; the agent did not self-check Notepad (I confirmed externally via OCR). A robust benchmark must
pin the final verify.
NEXT: (1) Claude baseline on the SAME task (scratch\run_calc.bat claude) for the head-to-head; (2) K-rep
reliability (pass N/N? plan reliably includes BOTH verifies?); (3) ratchet difficulty (a product it can't do
mentally → forces READING the display; or a click → re-adds grounding). New tooling: scratch/_probe_new_task.py,
scratch/run_calc.bat, scratch/_ocr.py. Full detail + repro in the doc above.
═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-22: LOCAL PLANNER ON THE B580 — see docs/SESSION_2026-06-22_local_planner_b580.md ★★★
═══════════════════════════════════════════════════════════════
Stood the planner up ALL-LOCAL on the desktop Arc B580: llama.cpp Vulkan `llama-server` (NOT Ollama/
IPEX-LLM — Intel archived IPEX-LLM Jan 2026) serving unsloth/Qwen3.5-9B-GGUF (a VLM; UD-Q4_K_XL + mmproj),
OpenAI endpoint :8080. Wired `--kind local` (CFG.planner_local_url / AGENT_PLANNER=local) into
run_goal_once/probe_planner/server; LocalPlanner now sends enable_thinking explicitly; probe_planner --step
probes the closed-loop next_step; run_goal_once --plan forces run_goal for a clean A/B.
STATE: vision EXCELLENT (reads 1080p screens), `--reasoning-budget 0` disables thinking + is FAST (~48 tok/s,
~138 tok/call) — BUT the 9B's multi-step PLANNING is the new bottleneck: with thinking off it makes logic
errors (tried to CLICK a firefox path in cmd output; replans DROPPED the set-default flow) and the closed
loop WANDERS (hallucinated a desktop FF shortcut, missing enter taps) → task NOT completed locally yet.
Speed solved; plan-quality-vs-latency on a 9B is the open problem. Per-step image re-encode ~22s (SWA, no
cache reuse) → run_goal >> closed loop for the local vision model. NEXT: capped --reasoning-budget 256/512
(grounded but not 6477-tok slow); tighten the find-path idiom (type `start "" "<path>"`, never click cmd
text); default local→run_goal; decide if the 9B suffices vs a hybrid (local simple / Claude hard). Baseline
to beat: Claude run_goal completes the task in ~27.7s. Full detail + repro cmds in the doc above.
═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-22: PER-STEP CLOSED LOOP + HARD-FACT CONSTRAINTS — see docs/SESSION_2026-06-22_closed_loop_and_hard_constraints.md ★★★
═══════════════════════════════════════════════════════════════
Built the two levers the 2026-06-21 session ended on (the planner not ACTING on injected knowledge).
Both shipped + FULL offline suite green; NOT yet run live (rig is shared — live A/B is the next step).
Defaults UNCHANGED: closed loop is opt-in (AGENT_CLOSED_LOOP=0 default → still run_goal), and the
run_plan refactor is behavior-preserving (all prior regressions green), so the 10/10 keyboard
benchmark path is untouched.
  1. PER-STEP CLOSED LOOP — new `run_goal_step` (planner.py): observe → ask for the SINGLE next
     action (live screen+goal+short history) → execute ONE step → observe → repeat (premature-done
     guard + stuck limit). Executive `run_plan` body factored into `_run_one_step`; new `run_step`
     reuses it (one shared step chokepoint). Adds `Planner.next_step` + `_extract_step`/`validate_step`.
  2. HARD-FACT CONSTRAINTS (retrieval≠utilization → the gap is CODE): `hindsight.classify_facts`
     splits recalled facts into imperative DIRECTIVES (top-of-prompt, via `_memory_block`) + machine-
     enforceable GATES the executive BLOCKS on (`set_constraints`/`_blocked_by_constraint` inside
     `_run_one_step`). A recalled "FF shortcut broken — don't launch it" now BOTH leads the prompt AND
     hard-blocks the launch op even if the planner ignores the text. `_arm_memory`/`_disarm_memory`
     unify this for BOTH run_goal and run_goal_step. Opt-in via AGENT_HINDSIGHT (default off).
  ENABLE LIVE: set AGENT_CLOSED_LOOP=1 (+ AGENT_HINDSIGHT=1, AGENT_PLANNER_MODEL=…-30B-A3B-Thinking),
  run tools/run_goal_once.py on the firefox goal; watch the gate block launch-Firefox + the loop Esc
  the broken-shortcut dialog the turn it appears. New tests: tests/test_closed_loop_step.py +
  tests/test_hard_constraints.py (full suite green). Full writeup + ordered next steps in the doc above.
═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-21: see docs/SESSION_2026-06-21_replan_feedback.md ★★★
═══════════════════════════════════════════════════════════════
Big session layered on the config/firefox work below. Shipped + tested (most also validated LIVE on
the rig): stateful REPLAN feedback (history of prior attempts + on-screen failure summaries threaded
into each replan); planner REASONING mode (AGENT_PLANNER_MODEL=Qwen/Qwen3-VL-30B-A3B-Thinking — the
8B-Thinking is NOT served by the HF router); LAUNCH routing (Start-menu search for installed apps +
cannot-find guard); a general `scroll` op; a pre-click GROUNDING-VERIFICATION gate (abstains on
wrong-state/look-alike clicks); the full HINDSIGHT memory loop (recall→inject + write-back recipe +
dedup-on-write; local server 192.168.0.184:8888 bank TARS; opt-in AGENT_HINDSIGHT / AGENT_HINDSIGHT_WRITE);
and CLOSED-LOOP guards (pre-click error-dialog auto-dismiss + opt-in per-step `precondition`). Offline
tests in tests/ (replan/launch/scroll/click/memory/closed_loop); regressions green. New tools/
diagnostics: diag_clicks, diag_hindsight, diag_router/diag_provider, preflight, probe_planner --memory.
NEXT: the bottleneck moved from MECHANISM (recall/guards/gate all fire correctly live) to the PLANNER
ACTING on injected knowledge — it has the recalled facts + the precondition idiom but doesn't use them
(few-shot the idiom / surface blocking facts as hard constraints / a stronger planner). Target box's
Firefox Start shortcut is broken (private_browsing.exe moved) — a target-machine issue, not code.
Full details in the doc above.
═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-21 (earlier, config session): CONFIG WIRED + LIVE run_goal — INSTALL WORKS, DEFAULT-BROWSER BLOCKED ★★★
═══════════════════════════════════════════════════════════════
First real end-to-end exercise of the LIVE planner->executive path (run_goal), not the isolated
harness. Headline: the "download+install Firefox + set it default" task gets HALFWAY autonomously —
the winget INSTALL works live; the default-browser half is blocked by a precisely-diagnosed LAUNCH
bug, NOT a weak planner. Full writeup: docs/SESSION_2026-06-21_config_and_live_firefox.md.

THE EARLIER FIREFOX "1 step, 0.0s, done" SILENT SUCCESS WAS RulePlanner, not a weak 8B. The server
had been launched with AGENT_PLANNER=rule in its shell (carried over from a measure run); RulePlanner
returns bare [{"op":"done"}] for any goal outside notepad/calculator -> validate_plan passed it ->
run_plan ran [done] -> reported success. AGENT_PLANNER is NOT globally set; default is hf. Proven by
the new planner.json (planner="RulePlanner") — see runs/goal_115333/planner.json.

FIXES LANDED THIS SESSION (verified Windows-side: py_compile + import of kvm_agent.server.app clean):
  1. NO-OP GUARD + raw planner logging (orchestration/planner.py). run_goal now REFUSES a plan with no
     state-changing op (new plan_is_actionable()) for a non-empty goal -> fails LOUD / drives a replan
     instead of a fake "done". validate_plan also warns on actions-without-a-verify. ClaudePlanner/
     LocalPlanner stash self.last_raw; run_goal writes planner.json (raw reply + parsed + validated +
     issues) NEXT TO plan.json — the raw reply used to be discarded (that's why goal_111417 couldn't say
     WHY the plan was empty). Guard unit-tested offline; the 10/10 keyboard benchmark is unaffected
     (guard lives in run_goal; measure.py calls run_plan directly with actionable plans).
  2. config.py MIGRATION FINISHED — CFG is now actually the single source (it was half-wired: hardware/
     llm helpers read CFG, but the SERVER still read os.environ + hardcoded literals). Added planner_kind,
     send_image, anthropic_key, runs_dir. Repointed server/app.py, executive.py (module endpoints +
     Executive.open cam/screen/executor), models/uitars.py, models/evocua.py, live_ctl.py at CFG.
     COLLAPSED root agent_server.py from a 208-line DUPLICATE into a thin launcher that imports
     kvm_agent.server.app (there were two divergent server impls). Defaults == old literals (executable test).
  3. PLANNER IDIOMS added (planner.py SYSTEM): install via winget (launch 'cmd' -> 'winget install
     --silent --accept-package-agreements --accept-source-agreements <Id>' -> enter -> sleep -> verify);
     set a Windows default via launch 'ms-settings:defaultapps'; REGISTER a freshly-installed browser by
     launching it once BEFORE setting default; alt+f4 the terminal so it doesn't cover Settings. The 8B
     ADOPTS all of these and emits a clean keyboard-first plan (seen via tools/probe_planner.py).

LIVE RUN RESULTS (2 runs via tools/run_goal_once.py; per-step frames + planner.json under runs/firefox*):
  WORKS: cmd -> winget install Mozilla.Firefox -> vision-verified INSTALLED. The replan loop, no-op
    guard, lint, per-step frame capture, planner.json, and alt+f4-close-terminal all functioned LIVE.
  BLOCKED (default-browser half), root-caused from the captured frames:
    (a) launch of Firefox via Win+R errors "Windows cannot find 'Firefox'" — a winget-installed app is
        NOT bare-name runnable via Win+R (it IS via Start-menu search / full path).
    (b) Executive.launch() FALSE-CONFIRMED on that error dialog: the dialog's title bar reads "Firefox",
        so the _app_open vision check ("is a Firefox window open?") answered yes -> reported
        'launch Firefox ok' when NOTHING launched. A SILENT FALSE-POSITIVE — the exact class we fight.
        Evidence: runs/firefox_re1_125356/06_launch.png (the error dialog).
    (c) => Firefox never registered => it is ABSENT from the Win10 default-browser chooser flyout
        (runs/firefox_re1_125356/09_click.png shows only Chrome/IE/Edge) => default cannot be set, period.
    Secondary (moot until a/b fixed): the Win10 Default-apps "Web browser" row is below the fold (needs
    scroll) and the set is a tile->flyout->pick sequence the planner doesn't encode + grounding struggles with.

NEXT STEPS (ordered; (1)+(2) likely get the whole task to pass):
  1. Launch installed GUI apps via START-MENU SEARCH (tap Win -> type name -> Enter), NOT Win+R. Keep
     Win+R for system stuff (cmd, ms-settings: URIs). Implement in Executive.launch() (system-command
     vs installed-app split, or always try Start search for non-system names).
  2. HARDEN Executive.launch() confirm: treat a "Windows cannot find '<x>'" dialog as FAILURE (vision/
     OCR-detect it, Esc it, return False) — kills the false-positive in (b). Do NOT trust a same-named
     dialog as "the app is open".
  3. Add a `scroll` op to the plan schema + executive (firmware wheel = v5 works) so the planner can
     reach below-the-fold targets (the "Web browser" row).
  4. Encode the Win10 default-browser idiom, reusing tools/isolate_default_browser.py's SOLVED flow:
     scroll to Web browser -> click the CURRENT default tile -> click Firefox in the flyout -> verify.
     Only works AFTER (1)/(2) make Firefox actually launch + register.
  5. Re-run firefox live; expect pass.

ENV / TOOLING GOTCHAS (cost real time this session):
  - The Linux bash mount serves STALE/truncated/null-byte snapshots of files JUST written by the file
    tools (saw MINUTES of lag; py_compile gave bogus "null bytes"/"unterminated string" errors). PROPER
    FIX: compile/run repo code via the WINDOWS-side MCP (Windows-MCP PowerShell / Desktop Commander)
    against C:\Dev\vllm — no host->guest bridge, no lag. Verify file contents with the Read tool, NOT
    bash. Keep bash only for work that lives entirely inside the sandbox.
  - New diagnostics in tools/: probe_planner.py (planner output, NO HID; --kind hf|claude|rule, --frame
    PNG), run_goal_once.py (ONE goal end-to-end on the rig; needs the rig FREE -> stop agent_server
    first, single capture card). Both default to CFG (8B HFPlanner).
  - PowerShell MCP runs on the DESKTOP orchestrator, NOT the Win10 TARGET — it can't query the target's
    registry, only see its screen via the capture card. Firefox IS now installed on the target; Chrome
    is still the default.
═══════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-21 (evening): DEFAULT-BROWSER SOLVED + PREEMPTION HARDENING ★★★
═══════════════════════════════════════════════════════════════
The "set default browser to Chrome" class — the long-standing hard-GUI failure — is SOLVED on
the rig (target is WINDOWS 10, not 11): the isolated harness runs PASS, Chrome set + vision-
verified (runs/isolate_defbrowser_20260621_091906). It was NOT a planner-size problem; it was
THREE code/execution bugs, all now fixed with the MODEL SET UNCHANGED (uitars-q4 / qwen2.5vl /
Qwen3-VL-8B). The unifying theme: every one was a SILENT failure that looked like success.

ROOT CAUSES + FIXES:
  1. GROUNDING (the real blocker). The Executive used UI-TARS as a grounder via the AGENTIC
     COMPUTER_USE prompt. Handed a VISIBLE target ("Google Chrome" in the chooser) UI-TARS
     reasoned the task was already "done" and emitted finished()/scroll instead of a click ->
     ground() got no coordinate -> a SILENT no-click (looked like a misground at the screen
     edge). FIX: UI-TARS now grounds in the click-ONLY GROUNDING_DOUBAO prompt (no
     finished/scroll/wait in its action space); the Executive forces self.agent.grounding=True.
     Files: models/uitars.py (grounding flag + _build_messages branch), orchestration/
     executive.py (force in __init__, open()). PROVEN with tools/probe_grounding.py: COMPUTER_USE
     DONE'd on the chooser frame; GROUNDING mode clicks Chrome. (Anti-"don't finish" wording was
     IGNORED by q4 — the prompt MODE is the lever, not the phrasing.)
  2. VERIFY. The verify op did a literal SUBSTRING match of the planner's sentence
     (expect="Google Chrome is now the default web browser") — a string never on screen — so
     EVERY run false-failed even when the action worked. FIX: a new {"op":"verify","ask":
     "<yes/no question>"} routes to the vision model (Executive.confirm()); planner idiom prefers
     'ask' for states not shown as literal text. orchestration/executive.py run_plan + planner.py.
  3. CLICK SUCCESS was frame-diff "pixels moved" -> false positives on a misground. Mitigated by
     grounding mode (always a real click) + per-step logging (below) makes a bad click visible.

FIRMWARE SCROLL — FIXED (v5, flashed + verified 2026-06-21): boot.py now has a relative Wheel
field and code.py's 'S' handler sends wheel notches, so r4.scroll() actually scrolls (selftest:
the top marker scrolled into view; runs/selftest_20260621_110008, all four primitives pass). It
WAS a silent no-op (v4 report had no wheel byte; code.py:265 did nothing) — below-the-fold targets
were unreachable. Flashing the new descriptor RE-ENUMERATES the HID interface, so it needs a
power-cycle (replug once more if Windows shows Code 10).

PREEMPTION HARDENING (so this CLASS of silent failure is caught, not re-discovered by hand):
  - PLAN-TIME LINT: planner.validate_plan() auto-converts sentence-like verify.expect -> ask,
    drops malformed/unknown/field-missing steps, warns on long click targets, ensures a trailing
    done. Wired into run_goal (after decompose + after replan); streams "lint:" notes. Conservative
    — short literal expects (milk/59/"Default apps") stay substring, so the keyboard benchmark is
    unaffected. Unit-tested offline.
  - ALWAYS-ON PER-STEP LOGGING: run_plan saves a per-step frame (red crosshair on clicks) + the
    grounder's raw thoughts/actions to runs/<tag>_<time>/ (Executive.capture, default ON;
    measure.py sets capture=False to keep timing clean). This is exactly what made these bugs
    visible — now standard, not a throwaway harness. Executive.ground() stashes self.last_ground.
  - PRIMITIVE SELF-TEST: tools/selftest.py exercises launch/type/scroll/click vs the live capture
    and prints a capability table — flags a dead primitive (the scroll no-op) UP FRONT, instead of
    it surfacing as a mysterious mid-task misground. Run after any firmware reflash / wiring change.

NEW DIAGNOSTIC TOOLS (reusable, in tools/): isolate_default_browser.py (deterministic
keyboard-first task harness with per-step frames + truthful vision verify; --reach/--flyout/
--tile-desc/--chrome-desc A/B knobs), probe_grounding.py (OFFLINE grounding A/B on a SAVED frame
— no rig, only the laptop Ollama; has --grounding), selftest.py.

LESSONS (generalize): UI-TARS is an AGENT that decides termination — when using it purely as a
grounder, CONSTRAIN it to grounding mode; phrase targets as visual elements ("the blue X icon"),
not task goals. Every action must ASSERT its post-condition against the SCREEN — no success signal
decoupled from reality, no primitive that silently no-ops. The fastest debugging lever was per-step
frames + the grounder's raw output + an OFFLINE probe against saved frames.

NOT YET DONE / re-verify on the rig (don't assume): (a) the LIVE run_goal path end-to-end through
Open WebUI — the building blocks are in (grounding mode + ask-verify + lint), and a rough plan
should now RECOVER, but it's untested; (b) re-run `measure.py --k 10` to confirm the executive
edits didn't regress the 10/10 keyboard benchmark; (c) run_plan per-step capture has NOT run live
yet (selftest.py HAS — all four primitives pass, scroll now real). Optional next: a Win10
default-apps idiom in the planner to make that plan deterministic; a `scroll` op in the plan schema
now that the firmware wheel works. SUPERSEDES the older "Win11 set-default-browser fails on task
difficulty/verify" note below.
═══════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-21 (late): CONSOLIDATED INTO kvm_agent/ PACKAGE ★★★
═══════════════════════════════════════════════════════════════
The flat root modules are now ONE package: `kvm_agent/`. The loose .py files this doc
references below (executive.py, planner.py, pico_env.py, r4_client.py, uitars_agent.py,
evocua_agent.py, cua_agent.py) STILL EXIST at root but are now 3-line back-compat SHIMS
that re-export from the package — the REAL code moved. Verified end-to-end: `python
measure.py --k 10` = **10/10 = 100%** on the packaged code (runs/measure_20260621_075842).

LAYOUT — canonical code now lives here:
  kvm_agent/
    config.py                  ← ALL ips/ports/endpoints/model-names/paths, env-overridable.
                                  Defaults == the old hardcoded literals, so behavior is identical.
    hardware/pico_client.py     ← was r4_client.py   (class R4 kept; PicoClient alias added)
    hardware/env.py             ← was pico_env.py    (Camera + PicoEnv)
    models/uitars.py            ← was uitars_agent.py
    models/evocua.py            ← was evocua_agent.py (imports the VENDORED osworld, not a clone)
    models/factory.py           ← was cua_agent.py   (make_agent)
    orchestration/planner.py    ← was planner.py
    orchestration/executive.py  ← was executive.py   (Executive + Verifier)
    server/app.py               ← was agent_server.py's FastAPI app (the Open WebUI server)
    llm/ollama.py               shared Ollama/OpenAI client helper (STAGED; wire at the P2 perf step)
    _vendor/osworld/mm_agents/  the 3 upstream files we actually import (utils, prompts, qwen_vl_utils)
  Root still holds: the 7 shims, ENTRY POINTS you run (agent_server.py, measure.py, live_ctl.py),
  FIRMWARE (boot.py, code.py — run on the Pico), pyproject.toml, .gitignore, CLAUDE.md, PROJECT_STATE.md.
  docs/ = all session/findings/plan notes (moved off root). tools/ = diagnostics. runs/ scratch/ models/
  = data (gitignored). The 55 MB upstream evocua/ clone was DELETED (vendored down to 3 files).

★ WHERE TO SAVE NEW FILES GOING FORWARD — do NOT recreate the flat-root pattern:
  - New LIBRARY code → kvm_agent/<area>/: hardware I/O → hardware/, model adapters → models/,
    planner/executive/verifier → orchestration/, the server → server/, model-endpoint plumbing → llm/.
  - Any IP / port / model name / path → add a field to kvm_agent/config.py (CFG). NEVER hardcode it
    in a module again — that sprawl (same endpoint in 8 files, IPs in 10) was the whole reason for this.
  - Import via the PACKAGE path, e.g. `from kvm_agent.orchestration.executive import Executive`.
    The root shims are temporary back-compat — do NOT write new imports against them.
  - Runnable ENTRY POINTS → repo root (or a cli/ folder). FIRMWARE stays boot.py/code.py at root
    (deployed to the Pico's CIRCUITPY drive). DIAGNOSTICS/harnesses → tools/. TESTS → tests/.
    NOTES/findings/plans → docs/. Throwaway / run logs → scratch/ or runs/ (both gitignored).
  - It is a GIT REPO now (was not before): baseline → cutover → cleanup commits on branch
    `refactor/packaging`. .gitignore excludes models/ (35 GB), runs/, scratch/, evocua/, __pycache__ —
    keep it that way; never `git add` the model blobs.

ALSO CHANGED THIS SESSION (all in kvm_agent/, verified on the rig):
  - VERIFY hardened (orchestration/executive.py Verifier): tesseract is AUTO-DISCOVERED
    (TESSERACT_CMD config → PATH → C:\Program Files\Tesseract-OCR\tesseract.exe), so a
    winget/UB-Mannheim install works without PATH fiddling; has_text now TRANSCRIBES + substring-
    matches (both backends) instead of a brittle literal yes/no; read_number uses the VISION model
    aimed at the display. DO NOT revert read_number to whole-screen OCR (max-by-length grabbed the
    taskbar date "2026"; max-by-glyph-height grabbed tiny stray digits — both failed, vision is right).
  - FIRMWARE (code.py — FLASHED + live): type_text reads the target's Caps-Lock LED and taps it OFF
    before typing, so text matches the requested case (was inverting, e.g. milk → MILK).
    "capslock"/"caps_lock" added to the named-key table. boot.py untouched.
  - The shims are removable later (repoint the ~8 importers — agent_server, measure, live_ctl,
    tools/{operate,run_probe,calibrate_uitars,eval_harness,demo_parser_fix} — to kvm_agent.* and
    delete the 7 root files); deliberately KEPT for now (zero runtime cost, fully reversible).
  - Full design + rationale: docs/PLAN_2026-06-21_consolidation_and_optimization.md and
    docs/PACKAGING_STATUS_2026-06-21.md.
═══════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-20: ARCHITECTURE CHANGE, GOAL ACHIEVED ★★★
═══════════════════════════════════════════════════════════════
Reliable multi-step desktop use is SOLVED for the benchmark task: the multi-app
"Notepad list + Calculator math" task that the prior notes called a "genuine
UI-TARS capability limit on long horizons" now runs **10/10 = 100% verified** over
randomized reps on the real rig (Windows target). It was an ARCHITECTURE problem,
not a model limit. Full writeup: FINDINGS_2026-06-20_executive_architecture.md.
Measurement: runs/measure_v3/summary.json.

New architecture (replaces the single-7B operate.py loop for real tasks):
  PLANNER (planner.py: ClaudePlanner now / LocalPlanner on the B580 later / RulePlanner)
    decomposes a goal into atomic steps + re-plans on failure
  EXECUTIVE (executive.py) runs each step with the RIGHT primitive:
    KEYBOARD-FIRST (Win+R launch, typed text/arithmetic) — no fragile grounding;
    UI-TARS STATELESS grounding only for visual targets (dodges the mimicry bug)
  VERIFIER (qwen2.5vl via Ollama; pytesseract if installed) checks the SCREEN, not self-report
  reset_clean is VISION-GATED (frame-diff can't tell identical stacked windows apart)
  Harness: measure.py (K-rep rates). Dev REPL driver: live_ctl.py.

Overturns the EvoCUA/UI-TARS quant saga below: the dense-keypad "+ misground"
investigation (Q4/Q5/Q8/imatrix/history-depth) optimized a channel we shouldn't use —
the calculator takes the KEYBOARD; launch via Win+R, not the taskbar. Every prior
conclusion below is a SINGLE temp-0 sample → treat as anecdote. Real bugs fixed this
session: code.py silently dropped F-keys (fixed, NEEDS REFLASH); _changed must be
perceptual not exact-hash on live capture. Old EvoCUA/quant history kept below for context.
═══════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════
★★★ READ FIRST — 2026-06-20 (later): REFLASH VERIFIED + launch() FIX ★★★
═══════════════════════════════════════════════════════════════
Pico reflashed (code.py F-key fix) + verified LIVE on the rig:
  - F-keys work now: alt+f4 closes the focused window over HID (silent no-op pre-reflash).
    Isolated test: empty Notepad open -> alt+f4 -> clean desktop. NOT yet wired into
    reset_clean (still Alt+Space->c) — that swap is DEFERRED, just noted working for now.
  - Post-reflash gotcha: TCP/firmware came up but BOTH mouse+keyboard HID were dead (zero
    screen effect, every send returned cleanly) = Windows rejected the re-enumerated HID
    interface (the v4 Code-10 case the boot.py/code.py headers warn about). A Pico power-
    cycle/replug re-enumerated it and fixed it. The PICO SIDE "LOOKS CORRECT" even in this
    state — ground-truth is the captured screen, not the Pico's self-report.

REGRESSION found + FIXED (the important bit): the first post-reflash measure run was 0/10 —
every rep Calculator opened (twice), display stuck at 0, nothing typed. NOT firmware. Root
cause in executive.py launch(): it confirmed a launch via a 160x90 whole-frame diff > 6.0,
but Calculator opening over the (now-maximized, white) Notepad moves only 4.64 — under the
gate. launch("calc") reported failure -> retried Win+R (the "opened twice") -> run_plan
aborted at the launch step BEFORE the type step -> display 0. (Notepad REMEMBERS its
maximized state, which tipped calc under the threshold; measure_v3 passed only because the
geometry happened to clear 6.0 — a latent fragility, not the reflash.)
FIX: launch() keeps the frame-diff FAST-PATH (Notepad ~75 passes instantly) and FALLS BACK
to a vision confirm (Verifier: "is <app> open?") when the change is small — robust to a
compact window over a bright one, and it kills the double-launch (vision confirms on
attempt 0). Added Executive._app_open + _APP_DISPLAY in executive.py.
RE-MEASURED: 10/10 = 100% verified, rate 1.0, mean 20.4s/task (vs 18.7 baseline; the +1.7s
is the per-launch vision check), wall 330s. runs/measure_20260620_220238/summary.json. Spot-
audited final frames vs the JSON readback (5,194 = 98x53; 1,886 = 82x23) — the rate is real.
═══════════════════════════════════════════════════════════════

Current hardware topology (confirmed this session)



Desktop (Windows 11, i7-14700K, Intel Arc B580) — the orchestrator: runs the agent loop, physically holds the Acer USB3 HDMI capture card, commands the HID injector over WiFi. Also Windows box "aahla" in play for testing (PowerShell).

Laptop (RTX 4080) — runs EvoCUA-8B via Ollama (CUDA). Reachable at http://192.168.0.155:11434/v1 (bound to 0.0.0.0 via systemctl edit ollama → OLLAMA\_HOST=0.0.0.0:11434).

MacBook Pro — the target/sandbox (skipped VMs — real HDMI-out + USB + cross-OS test). CIRCUITPY drive mounts here since the Pico's plugged into it.

Raspberry Pi Pico 2 W (RP2350) — the HID injector (replaced the Arduino R4). CircuitPython 10.2.1. Bought 2 + micro-USB data cable from Microcenter.



Model decision: EvoCUA-8B (chosen over UI-TARS-1.5-7B, currently using Q\_4\_KS quant - only GGUF pre-compiled takes up <5GB, might want to compile our own Q\_5\_KM or Q\_8)

EvoCUA-8B scores 46.1% OSWorld (beats 72B models), Qwen3-VL backbone, safest CUA per Bengio/Song study, cross-OS. Running the Q4\_K\_S GGUF (community repo AhmedMostafa-notabot/EvoCUA-8B-20260105-Q4\_K\_S-GGUF) — includes the critical mmproj-Evocua-F16.gguf vision projector. Built in Ollama with a Modelfile using two FROM lines (language GGUF + mmproj). Vision smoke-test PASSED — accurately described the actual COSMIC desktop. Use model name evocua-8b (NOT bare EvoCUA:latest). Still need to add PARAMETER num\_ctx 8192 to the Modelfile and re-create (screenshots are token-heavy).

\[CLARIFIED] Running the model does NOT train it — GGUF weights are frozen, inference only. \[CLARIFIED] EvoCUA is currently the ONLY model (it plans+grounds+terminates solo); the two-model split (EvoCUA grounding + separate reasoning model on B580) is an UPGRADE PATH only if solo reasoning proves weak — decide empirically.

EvoCUA uses S2 mode (not S1)

From the uploaded repo source (prompts.py, utils.py, qwen\_vl\_utils.py): S2 mode emits an Action: line + <tool\_call>{"name":"computer\_use","arguments":{...}}</tool\_call> JSON. The adapter (evocua.py) ports smart\_resize/process\_image verbatim. \[CORRECTED 2026-06-18] Coordinate representation is RELATIVE, not absolute processed-pixels: COORD\_TYPE="relative" → the system prompt states a 1000x1000 screen, the model emits a 0..999 grid, and we scale to the real screen via x/999, y/999. This matches the upstream default (evocua\_agent.py adjust\_coordinates). The old "[600,690] processed → [900,1012] target" self-test note above reflected the ABANDONED absolute config; the live code is relative and grounds correctly (see 2026-06-18 session).

★ THE BIG WIN THIS SESSION: Pico W absolute HID — WORKING ★

Switched from R4 (relative, corner-home, fought macOS acceleration) to Pico 2 W absolute HID because macOS acceleration couldn't be linearized. Absolute = digitizer descriptor, 0–32767 logical range, cursor teleports to exact pixel, zero calibration ever.

The nasty bug that ate \~2 hours: the HID Report ID. Original boot.py declared Report ID 1; the Mac enumerated the device but silently dropped every report (no error, no movement). Fix: removed the report ID entirely — single mouse report doesn't need one. Report is now flat 5 bytes \[buttons, xL, xH, yL, yH], sent via plain send\_report(\_report) (no ID arg).

Also fixed: \_report\[0] \&= \~bit byte-overflow → \&= (\~bit) \& 0xFF; socket bind changed from device-IP to "0.0.0.0" (was crashing code.py after WiFi connect → "on WiFi but not listening").

Confirmed working: boot self-test moves the Mac cursor (center→corners→center) ✓. WiFi command path works ✓. The injector is DONE.

(Red herring resolved: WiFi "wasn't working" because the SSID/password placeholders were never filled in — YOUR\_SSID/YOUR\_PASS. HID self-test worked anyway because it runs before WiFi by design.)

Files (all in /mnt/user-data/outputs/)

Active local-EvoCUA stack:



agent\_loop\_evocua.py — the local EvoCUA loop over Ollama. MODEL\_URL/MODEL\_NAME set for Ollama; coords arrive already in target space (no SCALE knob). CONFIRM\_FIRST=5.

evocua.py — EvoCUA-8B S2 adapter: smart\_resize, <tool\_call> JSON parsing, exact coordinate projection. Self-tested.

pico\_w/boot.py — v2, custom absolute-mouse descriptor, NO report ID, 5-byte report. (boot.py changes need a HARD reset/power-cycle to take effect.)

pico\_w/code.py — v2, WiFi listener + full M/C/R/D/U/K/T/X/S/H protocol, byte-mask fix, 0.0.0.0 bind, WiFi-retry loop, boot self-test, verbose logging.



Supporting (from earlier sessions): r4\_client.py (TCP client; mouse protocol carries over to the Pico, keyboard rewritten this session to send key names not numeric codes), run\_logger.py (instrumentation, JSONL/annotated frames/reclick detection; PRICES dict has unverified placeholders), compare.py, plus superseded Claude-API loop files (agent\_loop\_logged.py, agent\_loop\_local.py with UI-TARS scaffold, uitars.py).

Uploaded EvoCUA source (/mnt/user-data/uploads/): evocua\_agent.py, prompts.py, utils.py, qwen\_vl\_utils.py — defined the S2 protocol evocua.py matches.

═══════════════════════════════════════════════════════════════
SESSION 2026-06-18 — END-TO-END WORKING + QUANT/HISTORY WORK
═══════════════════════════════════════════════════════════════

★ THE BIG WIN: full pipeline runs against the real Mac. EvoCUA (Ollama) → parse → Pico HID → click, with HDMI capture at 1920x1080. Single-target click ("click the TV icon in the Dock") landed dead-on, app opened, model terminated correctly. Both prior model-level unknowns RESOLVED: (a) S2 <tool_call> output parses cleanly; (b) grounding is pixel-accurate on large targets. Capture dims printed each run; Pico SCREEN_W/SCREEN_H MUST equal them (still the only value affecting click accuracy).

QUANTS NOW ON HAND (laptop /home/aaron/models/evocua-8b/):
- evocua-8b-20260105-q4_k_s.gguf — original community Q4 baseline.
- evocua-8b-q8.gguf — Ollama model `evocua-8b` (Q8_0, ~9GB / 5.4GB... actually ~9GB).
- evocua-8b-q5_k_m.gguf — imatrix-calibrated Q5_K_M, 5574 MiB (5.71 BPW). Ollama model `evocua-8b-q5`. imatrix built from agent_calibration.txt = qwen2.5vl text narration of ~150 frames (2 fpm) of Aaron's own screen recordings, ~70KB / ~17k tok (49 chunks @512). NOTE: calibration is TEXT-ONLY (no images run through it) → it weights the reasoning/tool-format/vocabulary side, NOT the vision→coordinate path directly. v2 idea: add real S2-format tool_call JSON + coordinate samples to exercise the digit-emission channels; grow corpus + more distinct tasks.
- mmproj-Evocua-F16.gguf — the F16 vision projector, shared by ALL of the above. Every Modelfile needs two FROM lines (LM gguf + this mmproj).
- Arch confirmed from quant log: qwen3vl, 36 blocks, 8.2B, GQA 32/8 heads, ctx_train 262144, rope freq_base 5e6, 3 deepstack layers.

VRAM / num_ctx: num_ctx 8192 fits Q8 at 100% GPU on the 4080. num_ctx 16384 OOMs (CUDA resource allocation failed at first inference — KV cache couldn't allocate on top of the Q8 weights). Editing the Modelfile does nothing until `ollama create` re-runs. Lever for more context without OOM: KV-cache quant → OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve (keeps cache on GPU). Don't CPU-offload — bandwidth ~10x lower, tanks the loop.

Q4 vs Q8 (empirical): Q8 fixed CATASTROPHIC behavior, not just grounding. Q4 looped forever re-clicking the same coord with no self-awareness (hit the iter cap). Q8 recognizes failure and terminates cleanly (status=failure). Marginal-decision improvement. Q4→Q8 is the meaningful jump; Q8→FP16 would buy ~nothing.

macOS APP-LAUNCH GOTCHAS (target machine):
- Spotlight does NOT surface stock Calculator.app. Top hit is an App Store listing ("Solves: Calculator for All") + web/Siri suggestions; clicking routes into Safari/Google. Pressing Return would NOT launch the stock app. Spotlight is unreliable for launching built-in apps here.
- F4 over HID does NOT open Launchpad. Launchpad is bound to the Apple keyboard's media/special F4; a generic USB-HID sends the STANDARD function-key F4 (0x3D), which macOS does not map to Launchpad. Symptom: explicit "press F4" goal = total no-op (frames confirm nothing happened) AND a false-positive terminate(success). Manual F4 on the built-in keyboard works (different key semantics). NOT an adapter bug — upstream sends identical pyautogui.press('f4').
- WORKING launch primitive: click the Launchpad DOCK ICON (2nd icon, the grid) → Launchpad opens → click/filter the app icon. The terse goal "Open Launchpad, launch Calculator app" succeeded this way (real Calculator.app). Prefer Dock-icon, not F4.

DOCUMENTATION AUDIT — evocua.py/agent_loop_evocua.py vs upstream evocua/ (evocua_agent.py, prompts.py, utils.py):
- CORRECT: action vocabulary, coordinate math (relative /999), smart_resize/process_image (factor 32), system prompt + tool schema (verbatim), tool_call JSON parse, key combo-vs-press split.
- WAS WRONG → FIXED: history protocol. Old ground() sent a SINGLE turn with a hand-rolled "Step N: click x,y" text summary — no past screenshots, no assistant turns. Upstream _build_s2_messages replays the last N turns as real image(user)+response(assistant) pairs and summarizes only OLDER steps as text (using the model's own Action: lines). We were blinding the model to its own visual history — plausibly the root cause of the Q4 loop and the Q8 false-positive terminate (it had no prior frame to see its last action did nothing).
  FIX (this session): ported build_messages() + extract_action_line() into evocua.py; rewrote ground(client, model, screenshots, responses, proc_w, proc_h, instruction, history_n) with context-overflow decrement-retry. Loop now maintains screenshots[] (processed b64) + responses[] (raw) and feeds them. HISTORY_TURNS=1, MAX_TOKENS=1536 (each processed 1080p frame ≈ 2040 vision tokens; at history_n=1 that's current+1 frame ≈ fits 8192 with margin). Logic unit-tested (turn order/pairing/image-count match upstream). NOTE: Linux mount lags behind file-tool writes during a session — verify via Read, not bash, or trust that Windows-side file is correct.
- Trust the <tool_call> JSON, NOT the "Action:" prose — they can diverge; adapter executes the JSON (observed run #073156: prose said 382,614, JSON+executed was 734,663, which was correct).
- Termination reliability gap remains: model can declare success with nothing on screen. Consider a verify-before-terminate guard (e.g. confirm target window visible). Partially mitigated by the history fix.

EXPERIMENT LADDER (next, one variable at a time):
1. Q8 (`evocua-8b`) + history fix, HISTORY_TURNS=1 — first real test of the message-protocol change vs the known-good Q8 baseline.
2. Q5 (`evocua-8b-q5`) + history fix, HISTORY_TURNS=1 — only the quant changes. Does imatrix-Q5 hold up vs Q8?
3. Q5, HISTORY_TURNS=2 — only then add history depth (Q5 frees ~3.5GB vs Q8, so the budget exists).
Same Launchpad-via-Dock goal all three. Watch: no re-click loops, no false-positive success, `ollama ps` = 100% GPU, no "[ctx] grounding failed … retrying" spam. Just point MODEL_NAME in the loop at the model under test.

LADDER RESULTS — RAN 2026-06-18 (all three PASS, indistinguishable on this task):
- Preload: `curl localhost:11434/api/generate -d '{"model":NAME,"keep_alive":"30m"}'` (empty prompt = load-only). num_ctx 8192 confirmed baked/in-effect (no overflow on any rung).
- Rung 1 — Q8 (evocua-8b), history_n=1, CONFIRM_FIRST=5: FINISHED, 4 iters, 0 re-clicks. wait → click Launchpad dock [174,1033] → click Calculator [739,667] → terminate(success). Crosshairs eyeballed dead-on. ★ The history-protocol port (build_messages replaying real frame+assistant turns) did NOT regress grounding vs the known-good Q8 baseline — the thing this rung existed to verify.
- Rung 2 — Q5 (evocua-8b-q5), history_n=1, CONFIRM_FIRST=5: FINISHED, 4 iters, 0 re-clicks. EXACT SAME coords as Q8 ([174,1033],[739,667]) — temp 0 deterministic, identical trajectory. Only diff was Action-prose flavor ("fourth row, third column" vs "fourth row"); executed JSON identical. imatrix-Q5 is viable — indistinguishable from Q8 here.
- Rung 3 — Q5, history_n=2, CONFIRM_FIRST=0: FINISHED, 4 iters, 0 re-clicks. NO "[ctx] grounding failed" line → history_n=2 FIT inside num_ctx 8192 and took effect (did NOT silently decrement to 1). Coords [174,1035]/[739,667] (2px noise). Per-step model latency 4.3–4.8s except the terminate step 10.5s (extra history frame → more tokens). So HISTORY_TURNS=2 does NOT require raising num_ctx — 3 frames (~6.1k vis) + ~0.7k sys ≈ 6.85k prompt < 8192; the Q5 VRAM headroom is a SEPARATE axis (lets you go deeper than 2 later via higher num_ctx).
- TIMING CAVEAT: rungs 1–2 wall-time (60.3s/52.0s) is POLLUTED by human Enter-pauses (CONFIRM_FIRST=5); rung 3 (37.3s) is the only clean no-human time. Per-step (X.Xs) latencies printed by the loop ARE clean (measured around the API call only, before the confirm gate). For a real Q8-vs-Q5 speed delta, re-run rungs 1–2 with CONFIRM_FIRST=0.
- BIG TAKEAWAY: the Launchpad-via-Dock task is SATURATED — too easy to separate Q8/Q5/history-depth behaviorally (all converge to the same 4-step trajectory). To find a discriminating signal, NEXT need a harder multi-step task (more steps, recoverable mistakes, a state where seeing the prior frame actually matters) — that's where history depth and quant precision should diverge. Also the false-positive-terminate risk was NOT exercised (every run genuinely succeeded); a verify-before-terminate guard is still untested.
- Loop left set to: see NEXT EXPERIMENT below (loop was re-pinned to the harder task after the ladder).

NEXT EXPERIMENT — HARDER DISCRIMINATING TASK (designed 2026-06-18, runs when Mac guest login is set up):
WHY: the dock task saturated because the target was a ~100px icon and the path was 4 trivial steps — Q8/Q5/history-depth all collapse to the same trajectory. To separate them we change the two things that make precision and memory matter:
  (1) SMALL, DENSELY-PACKED TARGETS → stresses grounding precision (the Q5-vs-Q8 axis). Calculator buttons are ~60px uniform; at 1920x1080 a few px of grounding drift flips a 7 into an 8. The dock icon was too forgiving to expose any quant gap.
  (2) SEQUENTIAL STATE WITH A VISIBLE, RECOVERABLE WRONG-STATE → stresses self-correction + history depth + verify-before-terminate. The Calculator display is an unambiguous correctness signal the model must READ before terminating; a misclick shows up immediately and is recoverable (Clear + redo).
SETUP (guest/sandbox login on the Mac):
  - Mac display MUST stay 1920x1080 single/mirrored so capture dims == Pico SCREEN_W/SCREEN_H (guest login can reset scaling — re-check the "capture dims:" line prints 1920x1080 before trusting any click).
  - Calculator reachable via the proven Launchpad-dock path (NOT Spotlight, NOT F4 — both broken here, see gotchas above). Pre-opening Calculator is fine if launch variability gets in the way; the discriminator is the arithmetic, not the launch.
HARD-LADDER (same one-variable-at-a-time discipline as the saturated ladder):
  - Hard-Rung 1: Q8 (evocua-8b), history_n=1, CONFIRM_FIRST=5 — REFERENCE run, gated so the human eyeballs whether each small-button crosshair lands. Establishes the known-best trajectory on the new task before changing anything.
  - Hard-Rung 2: Q5 (evocua-8b-q5), history_n=1, CONFIRM_FIRST=0 — only the quant changes. HYPOTHESIS: Q5's lower precision may misground a small button where Q8 nailed it → wrong digit on display → exercises self-correction. This is the first place Q8/Q5 could actually diverge.
  - Hard-Rung 3: Q5, history_n=2, CONFIRM_FIRST=0 — only history depth changes. Does seeing 2 prior frames help it catch/repair a misclick faster than 1?
GOAL string: "Open Calculator and compute 7 × 8 + 5" → expected display 61 (basic immediate-eval: 7×8=56, +5=61). Button sequence 7,×,8,+,5,= — all DISTINCT (no repeated digit/op) so run_logger re-click detection isn't confused by a legitimately-repeated button. Do NOT put the answer in the goal: terminating without 61 on the display = false-positive terminate, which is exactly what we want to catch.
METRICS TO COMPARE ACROSS RUNGS: per-button grounding (does the crosshair / the display advance correctly?), final display == 61?, self-correction on any misclick, re-click loops, per-step latency (clean at CONFIRM_FIRST=0), ctx retries (should be none at n≤2/8192), iters-to-done.
HISTORY-DEPTH ISOLATOR (only if Hard-Rung 3 is STILL indistinguishable — the arithmetic display is always visible, so history is partly redundant): inject partial observability — force the model to RECALL a value N steps after it was last on screen (e.g. compute 7×8=56, Clear to 0, then re-enter 56 and +100). Tune N so the 56-frame falls OUTSIDE history_n=1's window but INSIDE n=2's — that's the only construction where n=2 strictly beats n=1. NOTE the loop logs the PRE-action frame each step, so the value's last-visible frame is the step where the model DECIDED to clear; count carefully when tuning N.
HARD-RUNG 1 RESULT (Q8, ran 2026-06-18, launch INCLUDED — first harder-task run): SUCCESS, display = 61 (confirmed on screen, true-positive terminate). 13 iters, 0 re-clicks, ~6.5s/step clean (CONFIRM_FIRST=0). ★ Real self-correction surfaced: model MISIDENTIFIED a dock icon and opened Freeform (not Calculator), got hit with Freeform's welcome + an iCloud dialog, DISMISSED all of it, then went Launchpad→Calculator and did the math right. Small-button grounding dead-on (7,×,8,=,+,5,= all registered; running-expression line read "56+5"). So Q8 recovers from a wrong-app + multi-dialog detour — the recoverable-wrong-state behavior the harder task was designed to surface. BUT the launch mess is NON-REPRODUCIBLE (which icon it grabs / which dialogs appear varies), so it swamps the Q5-vs-Q8 grounding signal → PIVOT to pre-opened for the controlled ladder.
NOTE: the new macOS Calculator shows the running EXPRESSION ("56+5") above the result — extra persistent on-screen state → history depth is even MORE redundant for arithmetic. Hard-Rung 3 (history_n=2) will very likely TIE; expect to need the recall-after-clear isolator to move the history needle.
LOOP NOW PINNED TO HARD-RUNG 1b (clean, pre-opened): GOAL="Using the open Calculator, compute 7 × 8 + 5", MODEL_NAME=evocua-8b, HISTORY_TURNS=1. PRE-OPEN Calculator + press AC (display=0) before EACH rung so all share one start state. Run order: 1b Q8 ref → 2 Q5 (only quant) → 3 Q5 history_n=2 (only depth). Preload the model, confirm CONTEXT 8192, AC-clear, then run.

★★★ HARD-LADDER RESULTS — RAN 2026-06-18 — THE TASK FINALLY DISCRIMINATES ★★★
REFERENCE BUTTON COORDS (Q8, pre-opened, 1920x1080 capture): 7=536,678  ×=672,678  8=578,678  +=674,771  5=584,726  ==672,817. Note + lives in the RIGHT (orange operator) column at x≈674.
- HARD-RUNG 1b (Q8, evocua-8b, history_n=1, pre-opened): CLEAN SUCCESS. 7 iters, 0 re-clicks, sequence 7,×,8,+,5,= → display 61 (true positive). Every small (~60px) button landed dead-on. This is the known-best reference.
- HARD-RUNG 2 (Q5, evocua-8b-q5, history_n=1, pre-opened): ★ CATASTROPHIC FAILURE — the first real Q8/Q5 separation. 17 iters, 5 re-clicks, terminated success with 5985 on the display (expected 61) = FALSE-POSITIVE TERMINATE. Root cause = a REPRODUCIBLE Q5 GROUNDING DEFICIT ON THE "+" BUTTON: every time it wanted +, it emitted raw x≈301 (the MIDDLE digit column) and hit a digit instead — i=3 wanted + → hit 8 (display 7×88), i=11 wanted + → hit 5 (display 7×85). × grounded fine (right column, x≈350 raw); only + (lower in the orange column) collapsed onto the digit grid. Cascade: misground + → re-click loop trying to Clear (kept hitting 8 → 7×888→7×8888) → = gave 62216 → AC recovered to 0 (genuine self-correction!) → restarted 7×8 clean → misground + AGAIN onto 5 → +/- → = → 5985 → false terminate. So Q5 brings back ALL THREE pathologies Q8 had cured: small-target misground, re-click loops, false-positive terminate. CONCLUSION: the Q4→Q8 jump was NOT the whole story — Q5 ≠ Q8 once targets are small enough to demand precision. The dock task + easy ladder never stressed this. imatrix-Q5 is viable ONLY for coarse targets.
- VERIFY-BEFORE-TERMINATE: no longer hypothetical — Q5 declared success with 5985 ≠ 61. A guard (OCR display, compare to expected, or at minimum require the display to be stable/non-empty) would have caught it. PROMOTE this from "consider" to a real TODO.
- HARD-RUNG 3 (Q5, history_n=2) IS NOW A REAL TEST, not a likely tie: does seeing 2 prior frames let Q5 notice "I keep hitting 8 when I want +" and break the loop / avoid the false terminate? The failure happened at history_n=1; deeper history is exactly the lever that might rescue self-correction. LOOP NOW PINNED HERE: MODEL_NAME=evocua-8b-q5, HISTORY_TURNS=2, pre-opened, AC-clear first. Watch: does it still misground + (grounding is quant-bound, so PROBABLY yes), but does it RECOVER faster / terminate honestly (history-bound, maybe)?
- NEXT after Rung 3: if Q5's + misground persists regardless of history, that's a clean "need Q8 for precision tasks" verdict. To isolate history depth cleanly from the grounding deficit, re-run the recall-after-clear isolator on Q8 (whose grounding is reliable) so the ONLY variable is memory.

★★★ HARD-RUNG 3 RESULT (Q5, history_n=2, pre-opened) — RAN 2026-06-18 — HISTORY RESCUED Q5 ★★★
CLEAN SUCCESS: 7 iters, 0 re-clicks, sequence 7,×,8,+,5,= → display 61 (true positive, confirmed on screen). THE KEY DATUM: at i=3 the + click landed at 672,769 = the RIGHT operator column (Q8 ref 674,771, within 2px). At history_n=1 (Hard-Rung 2) Q5 put + at x≈578 = the DIGIT column, twice, and false-terminated. ONLY history_n changed (1→2). The 94px column flip is FAR beyond the ~3px run-to-run capture noise (e.g. i=0 was 538,675 vs Rung2's 538,678), so the attribution is clean: DEEPER HISTORY FIXED THE GROUNDING OF +.
- CONTRADICTS the "grounding is quant-bound, history won't help" hypothesis. Mechanism (plausible, not yet proven): at history_n=2 the model's context for the + decision includes the frame+assistant-response from its × click TWO turns back — a successful OPERATOR-COLUMN action that anchors + to the same right column. At history_n=1 it only saw the immediately-prior 8 click (digit column), biasing + onto the digit grid. So the extra frame is a SPATIAL ANCHOR, not just failure-reflection.
- VERDICT SHIFT: not "need Q8 for precision" but "Q5 + history_n=2 ≈ Q8" on this task — same trajectory/result, ~3.5GB lighter. BIG if it generalizes.
- COST: per-step latency ~doubles once history fills (i=3–5 = 8.4–9.0s vs 3.7–4.6s for the no-history early steps). The 2nd frame's ~2040 vision tokens. Still fits num_ctx 8192 (no [ctx] retry).
- CAVEAT: ONE deterministic run. Variable attribution is clean but temp-0 determinism ≠ generality.
SUMMARY TABLE (7×8+5, pre-opened, 1920x1080):
  Q8 n=1  → 61, 7 iters, 0 reclicks (reference)
  Q5 n=1  → 5985, 17 iters, 5 reclicks, FALSE terminate (+ misground ×2)
  Q5 n=2  → 61, 7 iters, 0 reclicks (+ grounded right column — history rescued)

NEXT — GENERALITY + MECHANISM (designed 2026-06-18, one variable at a time):
1. REPLICATE/GENERALIZE: run a DIFFERENT expression whose operator sits in the orange column at a different row, e.g. "6 × 9 + 4" or "3 + 8 − 2", at Q5 n=1 then Q5 n=2 (pre-opened, AC-clear each). Does n=2 RELIABLY rescue the operator misground, or was 7×8+5 a lucky anchor alignment? If n=2 wins across 2–3 distinct expressions → "Q5+n=2≈Q8" is real.
2. MECHANISM TEST (is it the × anchor?): construct a task where the operator the model must ground is NOT preceded by another operator inside the n=2 window (e.g. start with a multi-digit number: "12 + 3" → digits 1,2 then +; at the + step the n=2 history shows two DIGIT clicks, no operator anchor). If Q5 n=2 STILL misgrounds + there but succeeds on 7×8+5, that supports the "prior-operator-click anchors the next operator" mechanism.
3. STILL OUTSTANDING: verify-before-terminate guard (Q5 n=1 false-positive 5985 is the motivating case); recall-after-clear history isolator on Q8 (memory-only, grounding held constant). 
LOOP STATE AFTER RUNG 3: MODEL_NAME=evocua-8b-q5, HISTORY_TURNS=2, GOAL="Using the open Calculator, compute 7 × 8 + 5", CONFIRM_FIRST per Aaron (0). For generality test, change GOAL + run n=1 vs n=2.

★★★ IMATRIX A/B — RAN 2026-06-18 — IMATRIX EXONERATED ★★★
HYPOTHESIS: the + misground was caused by the TEXT-ONLY imatrix calibration starving the vision→coordinate path. TEST: built a CLEAN (no-imatrix) Q5_K_M straight from f16 (llama-quantize Q5_K_M, no --imatrix — same recipe as Q8), 5574 MiB / 5.71 BPW (same footprint as imatrix Q5). Pipeline this session: llama-quantize.exe on desktop (b9692 vulkan build) → detached Start-Process so the MCP-session timeout didn't kill it (the -NoNewWindow child got killed the first time at block 286/399; -WindowStyle Hidden detached survived) → scp to laptop ~/models/evocua-8b/ → Modelfile cloned from evocua-8b-q5 (FROM clean-gguf + FROM mmproj-Evocua-F16.gguf + TEMPLATE {{ .Prompt }} + num_ctx 8192, ONLY the LM source differs) → ollama create evocua-8b-q5-clean (new LM layer b507b9.., same mmproj blob e3e3fc.. as imatrix q5). NOTE on tooling: Windows OpenSSH output is NOT capturable through the Windows-MCP PowerShell wrapper (ssh/scp/ssh-keygen write to the console handle, every redirect came back empty, BatchMode looked like an auth fail). FIX = drive ssh/scp from WSL (native linux ssh pipes back fine): copy the win key into WSL ~/.ssh w/ chmod 600, `wsl bash -lc "ssh -i ~/.ssh/id_ed25519_win aaron@192.168.0.155 ..."`. Key had NO passphrase — the Windows failures were perms+console capture, not a passphrase. Use this WSL path for any future laptop automation.
RESULT (clean-Q5, evocua-8b-q5-clean, history_n=1, pre-opened 7×8+5): ★ SAME FAILURE AS IMATRIX-Q5. 7 iters, 0 re-clicks, terminated success with 6195 (7×885) on display = FALSE POSITIVE (expected 61). i=3 wanted + → clicked 578,677 = the DIGIT column (the 8 button), EXACTLY the imatrix-Q5 misground (Q8 ref + = 674,771, right operator column). So the + operator-column grounding deficit REPRODUCES without the imatrix → it is QUANT-BOUND (Q5_K_M precision), NOT caused by the text-only calibration. IMATRIX EXONERATED for this deficit.
- Downstream tail differed (same root cause, different deterministic path): imatrix-Q5 n=1 cascaded into clear-loops (17 iters → 5985, 5 reclicks); clean-Q5 n=1 plowed straight to a wrong answer and false-terminated (7 iters → 6195, 0 reclicks, LESS self-correction). So the imatrix didn't help OR hurt the grounding; it only shifted the post-failure trajectory.
UPDATED Q5 PICTURE (7×8+5, pre-opened, history_n=1): imatrix-Q5 → 5985 (loop+false term); clean-Q5 → 6195 (straight false term). BOTH misground + onto the digit column. Q8 → 61. The ONLY thing that fixed Q5 so far is history_n=2 (rescued imatrix-Q5).
NEXT (the now-obvious test): clean-Q5 at HISTORY_TURNS=2 — does the n=2 spatial-anchor rescue GENERALIZE to the clean quant, or was it specific to imatrix-Q5? If clean-Q5 n=2 also grounds + in the operator column → "Q5 + n=2 ≈ Q8" is robust across both quants (strong, deployable). If clean-Q5 n=2 STILL misgrounds → the imatrix was actually HELPING the n=2 rescue (surprising, worth understanding). LOOP being pinned: MODEL_NAME=evocua-8b-q5-clean, HISTORY_TURNS=2, pre-opened, AC-clear first.

★★★ CONFIG GAP DISCOVERED 2026-06-18 — WE'VE BEEN RUNNING AT 1/4 THE INTENDED HISTORY DEPTH ★★★
Aaron uploaded the official EvoCUA run harness (run_multienv_evocua.py, lib_run_single.py, README, tech_report). The REFERENCE OPERATING POINT (argparse defaults + the S2 example in README/run_multienv):
  - max_history_turns = 4 (S2 example) / 3 (argparse default). WE RAN history_n=1 (rescued at 2). So we operated the model at 1/4–1/3 of its designed history depth.
  - temperature = 0.01 (S2 example) / 0.0 (default). We use 0.0.
  - max_tokens = 32768 (default). We use 1536 (fine for actual short S2 gen, but far below spec budget).
  - precision = FULL model via vLLM (bf16). The reference NEVER quantizes to GGUF — Q5/Q8/imatrix is entirely our territory.
  - coordinate_type=relative, resize_factor=32, screen 1920x1080 — WE MATCH these.
IMPLICATION (this likely explains the whole + misground saga): EvoCUA is designed/benchmarked with 3–4 prior turns in context. Our copy-bias hypothesis predicts the n=1 failure exactly — with only the just-clicked DIGIT in the window, the coordinate decode has only a digit coordinate to copy, so + lands on the 8. The n=1→n=2 rescue (× operator coord entering the window) is just the FIRST STEP of the curve the model is meant to run at 3–4. So "Q5 misgrounds +" is plausibly an artifact of UNDER-FED HISTORY (a config issue), not Q5 quant quality. The entire imatrix/clean/Q8 ladder may have been chasing a symptom.
WHY WE COULDN'T ALREADY BE AT SPEC: num_ctx 8192 can't hold 4–5 frames (~9–11k tokens) — that's the ceiling that forced us to n≤2. Raising num_ctx needs VRAM. HARDWARE CORRECTION: the laptop is a 4080 MOBILE = 12GB VRAM (NOT the 16GB desktop part). Q8 at num_ctx 16384 (~8.7GB weights + ~2.4GB KV + buffers) blows past 12GB → that's why it OOM'd. Q5 at 16384 loads at 8.5GB TOTAL, 100% GPU, CONTEXT 16384 (CONFIRMED via ollama ps 2026-06-18) — fits 12GB with ~3.5GB to spare, NO KV-cache quant needed. So on this 12GB card the smaller quant is the difference between being able to run at spec history depth AT ALL vs not. That's the real, deployable reason Q5 matters — NOT "Q5 grounds worse." CAVEAT: the 8.5GB is the RESIDENT footprint after a TEXT-only load; a real 5-frame (history=4) forward pass adds transient vision-encoder activation on top — watch for an inference-time OOM mid-run; fallback = history=3 or add OLLAMA_KV_CACHE_TYPE=q8_0.
DECISIVE TEST (run at spec): re-create the Q5 model at num_ctx 16384 (+ OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 for VRAM safety on the 4080), set HISTORY_TURNS=4, temperature=0.01, re-run pre-opened 7×8+5. If + grounds in the operator column at the model's real operating point → "Q5 misgrounds +" collapses into "we starved it of history," and the quant is exonerated. Watch: i=3 + coord (operator col ~672 = fixed). Note ground() temp is hardcoded 0.0 in evocua.py — bump to 0.01 to match spec. Also reference sleep_after_execution=5.0 (we use SETTLE_SEC=1) and max_steps=50 (we use 25) — minor.

★★★ SPEC RUN RESULT (clean-Q5, history=4, num_ctx 16384, temp 0.01) — RAN 2026-06-18 — STILL FAILS, BUT PARTIALLY IMPROVED ★★★
SETUP CONFIRMED: re-created evocua-8b-q5-clean at num_ctx 16384; ollama ps = 8.5GB, 100% GPU, CONTEXT 16384 (fits the 12GB mobile 4080, NO KV-quant needed). history=4 + temp 0.01 in evocua.py. No inference OOM even with 5-frame vision activation. Per-step latency tripled (~13s) once history filled.
RESULT: STILL FALSE-POSITIVE TERMINATE — display 595 (7×85), expected 61. 8 iters, 0 re-clicks. BUT the failure MODE changed informatively:
- i=3 +: landed at 580,771. The ROW is now CORRECT (y=771 = the actual + row; n=1 had put it at y=677 = the just-clicked 8's row). The COLUMN is still WRONG (x=580 = digit column 2 → hit the "2", display 7×82). So MORE HISTORY FIXED THE VERTICAL GROUNDING, NOT THE HORIZONTAL. Q5 still pulls operator-column x toward the digit grid (~580 vs Q8 ref 674).
- Partial self-correction: i=4 it backspaced the erroneous 2 (clicked 534,630 = the ⌫/AC button mid-entry → "7×8"), recognizing the error — but then i=5 went straight to "5" WITHOUT re-attempting the +, computing 7×85 → 595. So it fixed the symptom (removed the 2) but dropped the + step entirely.
THE REAL LESSON (vindicates the earlier undersampling concern, NOT the under-fed-history hypothesis): cross-run the + COLUMN result is contradictory — imatrix-Q5 n=2 → x=672 (CORRECT, →61); clean-Q5 n=1 → x=578 (wrong); clean-Q5 n=4 → x=580 (wrong). More history did NOT monotonically help the column; a DIFFERENT quant at LESS history got it right. ⇒ the + column grounding is BOUNDARY-MARGINAL: outcome flips on capture noise + config, and SINGLE deterministic samples cannot attribute success/failure to quant vs imatrix vs history depth. We have been over-reading n=1 runs the entire session.
- "Under-fed history" is THEREFORE only PARTIALLY supported: history demonstrably helps the VERTICAL (row) grounding, but does NOT fix the horizontal (operator column), and the task still fails at full spec. So it is NOT purely a config issue.
- SURVIVING HYPOTHESES (both point away from "Q5 is just a worse model"): (1) BOUNDARY-MARGINAL grounding → must measure SUCCESS RATES over many runs per config, not single samples. (2) HORIZONTAL RESOLUTION of a tiny right-edge target → the operator column sits at the far-right of a ~200px window centered in 1920x1080; in the 0..999 grid the columns are ~22-28 units apart, so horizontal precision is the binding constraint. HIGH-LEVERAGE SINGLE TEST: crop/enlarge the capture to the calculator region (then offset/scale coords back for the Pico) — if + then grounds reliably, it was resolution, not the quant.
- verify-before-terminate STILL the motivating fix (595 declared success).
NEXT: stop single-sampling. Either (a) replicate each config K times for + success rates (needs auto-AC-reset + batch runner), or (b) the crop/enlarge harness test (higher leverage — directly tests the resolution hypothesis that would fix horizontal grounding regardless of quant). Loop currently: evocua-8b-q5-clean, history=4, temp 0.01, num_ctx 16384.

───────────────────────────────────────────────────────────────
NEXT STEPS (most pre-2026-06-18 items below are DONE — see session log above; remaining/superseded:)



Point r4\_client.py at the Pico — change R4\_IP to the Pico's current IP (check UniFi — it IS .183, DHCP-reserved on UniFi, already correct in the file) and PORT to 8000. MOUSE protocol carries over unchanged; the KEYBOARD protocol did NOT — the host sent numeric Arduino keycodes, but the Pico firmware expects key NAMES — FIXED this session in r4\_client.py (norm\_key + name-based K/X/T commands) and agent\_loop\_evocua.py (dropped the numeric KEYMAP). Coordinate back-projection in agent\_loop\_evocua.py now uses the live frame.shape, not a hardcoded 1920×1080.

Set SCREEN\_W/SCREEN\_H in pico\_w/code.py to the Mac's actual display resolution (never got confirmed — ask Aaron or read System Settings → Displays). This is the only value affecting click accuracy. Save (code.py changes don't need power-cycle).

Confirm 4 corners via r4\_client.py: (0,0), (SCREEN\_W,SCREEN\_H), center — watch cursor hit each. Dead-on = mapping correct, no calibration.

Add num\_ctx 8192 to the Ollama Modelfile + re-create.

Wire capture: Mac HDMI → Acer card → desktop; set Mac to single/mirrored display (one coordinate space — same discipline that bit earlier nested-desktop capture).

First EvoCUA run vs Mac with CONFIRM\_FIRST=5. Two unknowns to watch (both model-level, everything under them is proven): (a) does EvoCUA's S2 <tool\_call> output parse — watch the raw: lines; (b) is Q4 grounding pixel-accurate — watch the crosshair. If clicks systematically off, bump to higher-bit GGUF (Aaron has VRAM for Q8 \~9GB + 1.16GB mmproj).



Deferred / optional



Two-model architecture (EvoCUA grounding + reasoning model on B580) — only if solo EvoCUA reasoning proves weak.

Adaptive settle time, fine-tuning on logged trajectories — both opt-in future projects.

Verify run\_logger.py PRICES against current Anthropic pricing (only matters for Claude-API path, now superseded).

Re-add scroll to the Pico descriptor (dropped in v2 for robustness; S command currently a no-op placeholder).



Working style

Aaron is a highly technical tinkerer (electronics, soldering, Linux, homelab). Prefers direct, technically-honest answers; incremental isolated testing ("don't stack unknowns"); empirical decisions over premature optimization. Got understandably frustrated during the Pico debugging when responses kept asking him to switch devices — he wanted a concrete plan and a code audit, which is what resolved it (the report-ID bug). Lead with concrete steps, isolate variables, watch one signal at a time.

═══════════════════════════════════════════════════════════════
SESSION 2026-06-18 (late) — OPERATOR SURFACES + ANSWER CHANNEL + MCP
  (NEW, COMPILES + UNIT-TESTED, but NONE of it has touched the real rig yet — budget a debug day)
═══════════════════════════════════════════════════════════════

THREE ENTRYPOINTS NOW (one rig, strictly serial — only ONE may own the capture card + Pico at a time):
- run_probe.py — the BENCHMARK / re-baseline harness. The validated path. S2-only now (S1 config
  removed: S1 is upstream's trajectory-generation format, not a deployment mode — see prompts.py L53).
  Has the OCR verify-gate + calculator AC-reset; CONFIGS is a single S2 entry (q5-clean, h4, ctx 16384).
- operate.py — INTERACTIVE operator (REPL or one-shot/--once). FREE-RUN by default (sandbox). NO benchmark
  coupling: no expected, no OCR gate, no AC-reset — uses env.observe() to read the screen without clicking.
  2-way street via input() when the model emits an answer. --confirm gates each action.
- evocua_mcp_server.py — rig as MCP tools over Streamable HTTP for Open WebUI / LibreChat. JOB MODEL
  (start_computer_task→job_id, get_task_status poll, continue_task reply, cancel_task, get_task_screenshot),
  so long rollouts never hit host tool-timeouts. `--mock` runs the FULL lifecycle with NO hardware/Ollama.
  README_evocua_mcp.md has the Open WebUI (v0.6.31+, native MCP, Streamable-HTTP NOT OpenAPI) + LibreChat wiring.

ANSWER CHANNEL (evocua_agent.py; delivered as answer_channel.patch, already applied to the 4 files):
- agent.last_answer (reset per predict) + an "ANSWER" sentinel from the S2 parser. terminate now CAPTURES its
  `answer` field; a standalone `answer` action is NON-terminal and emits "ANSWER" (the 2-way seed).
- answer_in_schema flag (default FALSE): with it OFF the advertised S2 tool schema is BYTE-IDENTICAL to upstream
  — frozen-contract re-baseline is UNAFFECTED; we only CAPTURE an answer if the model emits one (free
  instrumentation). answer_in_schema=TRUE adds `answer` to the enum (advertises it) = a DELIBERATE second
  intervention that changes the prompt; measure against baseline.
- pico_env.step handles "ANSWER" (no settle, returns info={"answer":True}). run_probe now logs
  self_report / self_report_matches / answers (free self-report-vs-OCR-truth signal).

PROVEN this session (offline): all 5 .py compile; parser fix robust on 7 tool_call formats; answer-channel
unit-tested against the REAL agent (standalone→ANSWER, terminate→DONE+capture, plain click clears last_answer,
schema frozen when flag off); MCP MOCK lifecycle passes start→busy-guard→awaiting_reply→continue→succeeded→restart.

UNPROVEN — the debug surface for tomorrow (isolate one variable at a time, per working style):
  1. operate.py / MCP have NEVER run against the real rig end-to-end. The whole hardware+model path THROUGH
     these new wrappers is untested live. Start here.
  2. Does the 8B EVER emit an `answer`? With answer_in_schema=False it isn't advertised → probably never. To test
     the 2-way street at all you must set answer_in_schema=True (and accept it's a prompt change).
  3. MCP real backend: build_backend(mock=False) builds EvoCUAAgent+PicoEnv and monkeypatches env.safe_release.
     The server runs the rollout in a DAEMON THREAD — threading × the exclusive capture card × Ollama latency is
     the likely bug nest. Never exercised outside mock.
  4. Open WebUI/LibreChat transport wiring is version-sensitive (native MCP, Streamable-HTTP only).
  5. Non-calculator GROUNDING on a real desktop is itself unproven — operate.py's first novel goals ARE the experiment.

FIRST THINGS TO CHECK (ordered, don't stack unknowns):
  a. `python evocua_mcp_server.py --mock`, drive it from the host (or curl /mcp) → proves transport + host wiring
     before tying up the rig.
  b. operate.py one-shot on the KNOWN-GOOD task, Calculator pre-opened:
     `python operate.py --once "Using the open Calculator, compute 7 x 8 + 5"`. If this diverges from run_probe's
     proven 7-step trajectory, the bug is in operate/pico_env, NOT the model.
  c. Only then a novel desktop goal.
  d. 2-way street: answer_in_schema=True + a goal that forces a question; watch for "ANSWER" / awaiting_reply.

REMINDERS: capture line MUST read 1920x1080 (only thing affecting click accuracy). Ctrl+C is clean now
(show=False killed the cv2 window that ate it; KeyboardInterrupt handled in run_probe/operate; 180s Ollama
timeout so a hung call can't wedge it). evocua_agent.py now lives at REPO ROOT (pristine evocua/ pkg on sys.path);
operate.py + the MCP server assume that layout. Env gotchas: the PowerShell-MCP wrapper STRIPS `$` (use cmd / WSL
/ literal paths); the Linux sandbox mount LAGS file-tool writes (verify via Read or Windows-side, not bash wc -l).
Repo was also decluttered: superseded code + scratch probes archived under _archive/, reusable probes in probes/.

