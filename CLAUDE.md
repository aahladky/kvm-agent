Hardware Computer-Use Agent — Session Handoff

What this project is

A KVM-over-IP-style computer-use agent where nothing is installed on the target machine. A vision model sees the target's screen via HDMI capture, decides actions, and a physical USB-HID device injects mouse/keyboard. Target sees only a monitor + a USB mouse/keyboard — undetectable, OS-agnostic. Pure curiosity project, no practical application.

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

