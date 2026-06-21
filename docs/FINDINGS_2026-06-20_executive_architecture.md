# Reliable multi-step desktop use — the executive/executor architecture (2026-06-20)

**Result up front: the exact multi-app task the prior notes called a "genuine
UI-TARS-1.5-7B capability limit on long horizons" now runs at 10/10 = 100% verified
success over randomized reps.** It was an *architecture* problem, not a model limit.

Measured live on the real rig (Windows target, 1920×1080 HDMI capture, Pico HID):
`runs/measure_v3/summary.json` — K=10, operands & operator (+ / ×) randomized per rep,
each rep VERIFIED from the screen (calculator display read back, Notepad text checked),
auto-reset between reps. Mean 18.7 s/task, ~30 s/rep incl. reset, wall 302 s.

| rep | expr | expected | read off screen | notepad | result |
|----|------|---------|----------------|---------|--------|
|00|60+64|124|124|✓|PASS|
|01|44×76|3344|3344|✓|PASS|
|02|62×49|3038|3038|✓|PASS|
|03|56+85|141|141|✓|PASS|
|04|75×28|2100|2100|✓|PASS|
|05|28×23|644|644|✓|PASS|
|06|79+88|167|167|✓|PASS|
|07|50+23|73|73|✓|PASS|
|08|98×53|5194|5194|✓|PASS|
|09|82×23|1886|1886|✓|PASS|

---

## Prior work was treated as suspect — and it was wrong on the headline claim

Every prior conclusion in CLAUDE.md / the FINDINGS chain is a **single temp-0 sample**.
The "capability limit on long horizons" verdict rests on *one* messy-start Q8 run. That
is an anecdote, not evidence. Re-tested with rates, the claim collapses.

Three things the prior notes blamed on the model that were actually fixable engineering:

1. **The dense-keypad arithmetic** (the multi-day "+ misgrounds onto the digit column"
   saga) — was self-inflicted. The calculator accepts the **keyboard**. You don't click
   tiny keys; you type `42+17` + Enter. Zero grounding, zero misground. The whole Q4/Q5/
   Q8/imatrix/history-depth investigation was optimizing a channel we shouldn't use.

2. **The taskbar-search flail** ("clicked the empty center of the taskbar") — also gone.
   Launch apps with **Win+R → name → Enter** (keyboard, global, no taskbar grounding).
   Confirmed live: Win+R→`notepad`/`calc`→Enter opens reliably.

3. **The "planning slip / dropped a digit"** attributed to 7B cognition — the model's only
   state-record was a **lossy regex paraphrase** of its own thoughts (`uitars_agent.
   _summarize_action` → "clicked an element"). Give the model an exact plan + verify each
   step and the "cognition limit" doesn't appear.

The one durable prior finding that IS real: the **history coordinate-mimicry** input bug
(replaying raw `(x,y)` in history makes UI-TARS copy a coordinate instead of grounding).
Our executor sidesteps it structurally by grounding **statelessly** (one instruction, no
history) — verified dead-on live (the "7" key at (167,447); the calc/Notepad close-X).

---

## The architecture (what actually delivers reliability)

Stop making one 7B model plan + track state + ground + verify + terminate at once.
Split by strength:

```
  PLANNER  (pluggable: Claude now, a local model on the B580 later)   planner.py
     decompose a natural-language goal -> structured PLAN of atomic steps;
     re-plan from the live screen when a step fails.
        |
        v
  EXECUTIVE  (executive.py)   runs each step with the RIGHT primitive:
     - KEYBOARD-FIRST  launch (Win+R) / type / hotkey   -> deterministic, no grounding
     - UI-TARS STATELESS grounding  -> only for visual targets w/ no keyboard path
        |
        v
  VERIFIER  (qwen2.5vl via Ollama, or pytesseract if installed)
     confirm each step + the goal FROM THE SCREEN — never self-report.
```

Why each piece matters, with the live evidence that forced it:

- **Keyboard-first** removes the two historical failure classes entirely (launch + keypad).
- **Stateless grounding** keeps UI-TARS on the one thing it's reliably good at and dodges
  the mimicry bug. q4 grounding is pixel-accurate this way (no need for Q8 for grounding).
- **Vision verification** caught the real metric: success is "the screen shows 1886",
  not "the model said done". (Prior false-positive-terminates are designed out.)
- **Vision-gated reset** (`Executive.reset_clean`) was the hard-won fix. First attempts used
  a frame-diff "did the window close?" check — but **frame-diff cannot tell identical
  stacked windows apart**: closing 1 of 11 empty Notepads barely changes the frame, so reset
  gave up early and a window leaked every rep (this is what piled up 11 Notepads and made
  every post-first rep fail at launch — observed live, then fixed). The verifier *can* see
  "is a window open?", so it now drives the close loop and is self-correcting against the
  Alt+Space/​c close-race too. This single fix took the loop from 1/10 to 10/10.

---

## Real bugs found and fixed this session (all verified live unless noted)

- **Firmware silently drops F-keys.** `code.py:_keycode_for` only resolved named keys +
  single chars, so `alt+f4` sent *only Alt* → Alt+F4 was a no-op (and any F-key task would
  fail). Fixed to resolve F1–F12. **Needs a reflash** (the Pico's USB is on the target, so
  this was edited but not flash-tested here). Until flashed, close windows via Alt+Space→C
  or the grounding-X fallback (both used by `reset_clean`).
- **Frame-diff on live capture must be perceptual, not exact-hash.** Live frames are never
  byte-identical (sensor noise, the taskbar clock); `_changed` now uses mean abs pixel diff
  on a 160×90 grayscale (idle ≈ 0.0; a window open/close ≈ tens).
- **Keyboard close race** (Alt+Space menu not up before `c`) under loop load — fixed with
  adequate settle + the self-correcting vision-gated retry loop.
- **REPL echo of PNG bytes** (13 MB dumps) — methods no longer return raw frames.

---

## Deployable artifacts (all in C:\Dev\vllm)

- `executive.py` — `Executive` (launch/type/tap/key/click_target/verify/reset_clean/
  run_plan) + `Verifier` (pytesseract → qwen2.5vl fallback). Inject a live env+agent, or
  `Executive.open()` standalone.
- `planner.py` — `Planner` ABC + `ClaudePlanner` (Anthropic API — the baseline; set
  `ANTHROPIC_API_KEY`), `LocalPlanner` (OpenAI-compatible endpoint = the **B580 target**),
  `RulePlanner` (deterministic, dependency-free). `run_goal()` = closed-loop decompose →
  execute → re-plan-on-failure.
- `measure.py` — honest K-rep reliability harness (randomized, auto-reset, vision-verified,
  reports a RATE). `python measure.py --k 10`.
- `live_ctl.py` — interactive REPL controller (holds one camera+Pico open; cap/hot/typ/
  tap/click/tars/mark/do). How this session was driven.

PLAN step schema: `launch{app}`, `type{text}`, `tap{key}`, `key{combo}`,
`click{target}` (UI-TARS), `verify{expect|number==}`, `sleep`, `done`.

---

## Planner: Claude-now → B580-local (per Aaron's call)

- Validated this session with `RulePlanner` (automated: NL goal "…type: hello from the
  executive … compute 123 + 456" → plan → run → **done, 0 replans**, calc read 579 ✓) and
  with Claude authoring plans directly (the measurement plan).
- `ClaudePlanner` is the requested baseline — ready, just needs `ANTHROPIC_API_KEY` on the
  host (not set here). It sees the screenshot, so it can plan from real state + recover.
- **B580 path**: serve a reasoning model on the desktop Arc B580 with an OpenAI-compatible
  `/v1` (llama.cpp `llama-server` Vulkan/SYCL, or IPEX/vLLM), point `LocalPlanner(base_url=…)`
  at it. Same prompt/contract as ClaudePlanner → drop-in for the all-local end state. Vision
  optional (the executive's verify ops ground truth on-screen regardless).

## Next (one variable at a time, the house rule)

1. Swap `ClaudePlanner` in (add the key) and re-run `measure.py` end-to-end (planner+executive)
   for a baseline rate with a real planner brain, then stand up `LocalPlanner` on the B580
   and compare.
2. Broaden beyond Notepad+Calc: tasks that NEED visual grounding mid-flow (Settings toggles,
   browser, file dialogs) to stress `click_target` + re-plan recovery. Measure rates.
3. [DONE 2026-06-20] Reflashed `code.py`; **alt+f4 close VERIFIED live over HID**. `reset_clean`
   still uses Alt+Space->c — wiring alt+f4 in as the preferred close is DEFERRED (noted working).
   ALSO fixed this session: `launch()` now vision-confirms (frame-diff fast-path + Verifier
   "is <app> open?" fallback), fixing a 0/10 regression where Calculator-over-maximized-Notepad
   scored 4.64 < 6.0 and aborted the plan before typing. Re-measured 10/10
   (runs/measure_20260620_220238/summary.json). NOTE post-reflash: a Pico power-cycle/replug is
   needed if Windows rejects the re-enumerated HID interface (TCP up but no HID movement).
4. Optional: install Tesseract (UAC blocked it here) for deterministic OCR verify; qwen2.5vl
   works today as the verifier with no install.
```
