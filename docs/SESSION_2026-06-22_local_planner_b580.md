# Session 2026-06-22 (cont.) — B580 local planner bring-up + first local runs

Continues `SESSION_2026-06-22_closed_loop_and_hard_constraints.md` (which shipped + LIVE-validated the
per-step closed loop, hard-fact constraints/gate, and launch hardening — firefox task completed end-to-end
on Claude, benchmark 10/10 intact). This doc is the **all-local planner** thread: standing the planner up
on the desktop's Intel Arc **B580** so the closed loop / run_goal don't depend on the HF router or Claude API.

## TL;DR for next time
The local stack is **up, fast, sees the screen, emits valid JSON** — but the **9B's multi-step planning is
the new bottleneck**. With reasoning forced off it's quick but makes logic errors and loses the plot on
replans; with reasoning on it grounds well but is far too slow (6477 tok on one step). The task did **not**
complete locally yet. The speed problem is solved; the open problem is **plan quality vs. latency on a 9B**.

## Decisions / setup (validated this session)
- **Platform: llama.cpp Vulkan `llama-server`** — NOT Ollama/IPEX-LLM (Intel **archived IPEX-LLM Jan 2026**,
  security issues; its replacement `llm-scaler` doesn't support consumer B580). Vulkan is mainline + as-fast
  or faster than SYCL on Battlemage. `llama-server` gives the OpenAI `/v1` endpoint on :8080 (LocalPlanner's
  default), and mmproj handles the vision model.
- **Model: `unsloth/Qwen3.5-9B-GGUF`** — a real VLM (image-text-to-text, base Qwen/Qwen3.5-9B), UD-Q4_K_XL
  (~6 GB) + `mmproj-F16.gguf` (~1.15 GB). Fits the 12 GB B580 with room for 16k ctx. (Q8 too tight; avoid.)
- **Working server command:**
  ```
  llama-server -m Qwen3.5-9B-UD-Q4_K_XL.gguf --mmproj mmproj-F16.gguf --device Vulkan0 -ngl 99 \
    -c 16384 --image-min-tokens 1024 -np 1 --reasoning-budget 0 --host 0.0.0.0 --port 8080
  ```
  `--device Vulkan0` pins to the B580 (it also enumerates the UHD 770 iGPU — don't let it split).
  `--reasoning-budget 0` DISABLES thinking and **works** on this build (log: `reasoning-budget: forcing
  immediately, done`; eval dropped to 11–138 tokens). `-np 1` single slot. `--image-min-tokens 1024` for
  grounding accuracy (per a llama.cpp Qwen-VL warning).
- **Client env (PowerShell — `$env:`, not `set`):** `$env:AGENT_PLANNER_MODEL="qwen3.5-9b"` (no "thinking"
  in the name → 4k budget, not 16k; also fixes the label). `setx AGENT_PLANNER_MODEL qwen3.5-9b` to persist.

## Code wired this session
- `LocalPlanner._complete` now sends `chat_template_kwargs.enable_thinking` **explicitly** (True/False) — it
  used to only ever send True, so thinking could never be turned off from the client. (Belt-and-suspenders
  with `--reasoning-budget 0`; on Qwen3.5 the server flag is the one that actually works — see llama.cpp #20182.)
- `CFG.planner_local_url` (`AGENT_PLANNER_BASE_URL`, default `http://127.0.0.1:8080/v1`) + `planner_kind`
  now accepts `local`. Wired `--kind local` into `run_goal_once.py`, `probe_planner.py`, and the server's
  `build_planner`.
- `run_goal_once.py`: `--plan` (force OLD run_goal regardless of `AGENT_CLOSED_LOOP`), `--closed-loop`,
  `--max-steps`. `probe_planner.py`: `--step` (probe the closed-loop `next_step` path), `--kind local`.
  `scratch/_vischeck.py`: direct multimodal "what's on screen?" sanity check. `closed_loop_max_steps` 12→16.
- All offline tests still green; `measure.py --k 10` = **10/10** (the run_plan refactor + launch misfire OCR
  did NOT regress the keyboard benchmark).

## What works locally (validated)
- **Vision: excellent.** Direct probe (`_vischeck.py`) — the 9B read the Default-apps page accurately,
  including "Web browser: Google Chrome". mmproj/image path is solid on the B580.
- **Speed (thinking off): good.** Decode ~48 tok/s. `--reasoning-budget 0` → ~11–138 output tokens/call.
- **Single-step grounding (probe).** With thinking off, `next_step` on the Default-apps frame returned
  `{"op":"click","target":"Google Chrome"}` — correct, grounded (click the current-default tile).

## Where it FAILED — the two live local runs (the work for next time)
Both `--kind local`, qwen3.5-9b, `--reasoning-budget 0`. The model is fast now; **quality is the problem.**

**A. `--plan` (run_goal / plan-then-replan): `failed@7:verify`, 20.2 s, 2 replans** (`runs/firefox_re2_151954`).
- 27-step initial plan with a **logic error**: it planned to `click "the full firefox.exe path in the cmd
  output"` then `type 'start "" "%1"'` — i.e. click text in the terminal and use it as `%1`. You can't click
  terminal text to launch it; the click (correctly) failed. The reg query DID find the path (HKCU App
  Paths\firefox.exe exists), but the 9B mishandled the result.
- **Replans got SHORTER and dropped the set-default flow** — attempts 1 & 2 just `start`-launched Firefox
  then `verify "Is Firefox the default?"`, skipping the whole `ms-settings:defaultapps` → click-tile →
  pick-Firefox sequence. So verify can't pass (Firefox opened but was never set default). The 9B lost the plot.

**B. `--closed-loop`: wandered, user Ctrl+C'd** (KeyboardInterrupt). It issued reg/where queries (some
**without an `enter` tap** between, so they'd concatenate), then **clicked a hallucinated "Firefox shortcut
icon on the desktop"** twice (there is none → clicked (36,343), no effect), then `taskkill firefox`, `echo
%LOCALAPPDATA%`, reg query again… not converging. Per-step the 9B doesn't track state well enough to drive
the loop, and each step also eats ~22 s of image re-encode.

## Key cost finding: per-step image re-encode ~22 s (structural)
Every closed-loop turn sends a NEW screenshot, and the model's **SWA defeats the prompt cache** (log:
`forcing full prompt re-processing`), so each step pays full `process_mtmd` image encoding (~22 s prefill).
The closed loop pays this **per step**; run_goal pays it ~2–3× total. → **For the local vision model, run_goal
is structurally far cheaper than the closed loop.** (Reinforces the A/B from the other doc.)

## The core tension
- Thinking ON  → grounded single steps (probe: `click Google Chrome`) but ~600–6477 tok/step → minutes/step.
- Thinking OFF → fast (~138 tok/step) but planning quality drops (logic errors, dropped steps, wandering).

## NEXT STEPS (ordered)
1. **Capped reasoning budget — the likely sweet spot.** Don't run 0 OR unbounded. Try `--reasoning-budget
   256` (then 512). Enough reasoning to ground (the probe showed thinking → a correct grounded step) without
   the 6477-token blowup. This is the single highest-leverage knob to try first.
2. **Tighten the find-path idiom so the 9B can't "click terminal text."** Make it explicit: after `reg query`,
   READ the path and **`type 'start "" "<path>"'`** — never `click` text in cmd, never use `%1`. Or sidestep
   the transcription entirely: on this box Firefox is at `C:\Program Files\Mozilla Firefox\firefox.exe`
   (confirmed), so the directive can name the path directly rather than make the 9B read it off-screen.
3. **For local, default to run_goal, not the closed loop** (per-step image cost + the 9B's weak per-step
   state-tracking). Keep the closed loop for a stronger planner / harder tasks.
4. **Decide if the 9B is enough.** It reads screens and emits valid JSON but its multi-step *reasoning* is
   below Claude/30B. Options: a capped-thinking 9B (step 1), a different/better-fit ≤12 GB VLM, or a HYBRID
   (local 9B for simple tasks, Claude/30B for hard) — measure against the Claude run_goal baseline (27.7 s,
   completes) on the SAME task.
5. **Minor:** graceful Ctrl+C in `run_goal_step` (the closed-loop run ended in a raw KeyboardInterrupt
   traceback — cosmetic). And try a lower `--image-min-tokens` to shave the ~22 s/step encode vs. accuracy.

## Quick repro / commands
```
# server (B580): the working command above.  client:
$env:AGENT_PLANNER_MODEL="qwen3.5-9b"
python tools\probe_planner.py --kind local --step --frame runs\firefox_124412\05_launch.png "Open Firefox and set it as the default browser"   # no rig
python tools\run_goal_once.py --kind local --plan "Open Firefox and set it as the default browser"        # rig, run_goal
python tools\run_goal_once.py --kind local --closed-loop "..."                                            # rig, closed loop
python scratch\_vischeck.py runs\firefox_124412\05_launch.png                                             # direct vision check
```
Baseline to beat: **Claude `run_goal` completes this task in ~27.7 s (1 replan), screen-verified.**
