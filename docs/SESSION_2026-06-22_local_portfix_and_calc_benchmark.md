# Session 2026-06-22 (cont. 2) — Local planner: port-conflict fix, reasoning-budget reality, first all-local PASS on a NEW repeatable benchmark

Continues `SESSION_2026-06-22_local_planner_b580.md`. That doc ended with the all-local stack "up, fast,
sees the screen, emits valid JSON" but **not completing a task**, and proposed a capped `--reasoning-budget`
as the fix. This session: the all-local loop **completed a task end-to-end for the first time** — but on a
NEW clean benchmark (NOT firefox) — and the capped-budget idea turned out **not to exist in this build**.

## TL;DR
- ★ **First all-local end-to-end PASS.** B580 9B (llama.cpp Vulkan) planned + the executive ran
  "Compute 47×89 in Calculator, then type the result in Notepad" to `done` in **19.6 s, 0 replans,
  screen-verified** (Notepad genuinely shows `4183` — confirmed by OCR, not self-report).
  Run: `runs/calc_transcribe_160217/`.
- **Dropped firefox as the benchmark.** It needs network (winget), mutates persistent state (install +
  default browser), and the target's FF Start shortcut is broken → runs aren't comparable/repeatable.
- **New benchmark = "compute & transcribe"** (see below): built-in apps only, no network, resettable,
  clear vision-verifiable end state, keyboard-only (no grounding) → isolates PLANNING + keyboard executive.
- **Port conflict fixed (this was the real blocker for "is the model reachable").** Docker/SearXNG binds
  `127.0.0.1:8080` *specifically* and shadows llama-server's `0.0.0.0:8080` on loopback → the planner
  (base_url `127.0.0.1:8080`) was hitting **SearXNG**, not the model (got SearXNG 404 HTML through the
  OpenAI client). Moved llama-server to **`127.0.0.1:8090`** (specific loopback bind, so Docker can't
  shadow it) and updated `CFG.planner_local_url` default 8080 → 8090.
- **`--reasoning-budget 256` does NOT cap thinking on build b9692.** With enable_thinking=True the model
  emitted **3419 tokens / 78.8 s** for ONE decompose. This build treats reasoning-budget as **binary**
  (`0` = off, non-zero = on/effectively unbounded). The prior doc's NEXT-step #1 ("capped sweet spot
  256/512") is **not achievable here** — drop it. Real choice stays: budget 0 (fast ~10 s) vs on (~78 s/call).
- **For the EASY new task, budget 0 plans it perfectly and fast.** Offline probe AND the live run both
  produced a correct keyboard-first plan. The prior "budget 0 plans badly" was a *firefox/hard-task*
  finding, not universal — an easy, well-idiomed task is fine on the fast config.

## Setup changes
- **llama-server relaunched** on the B580: same model/flags (Qwen3.5-9B-UD-Q4_K_XL + mmproj-F16, `--device
  Vulkan0 -ngl 99 -c 16384 --image-min-tokens 1024 -np 1 --reasoning-budget 0`) but **`--host 127.0.0.1
  --port 8090`**. Model files live in `C:\Users\aahla\`; launched detached via PowerShell `Start-Process
  -WindowStyle Hidden`; logs → `C:\Users\aahla\llama_8090.{out,err}.log`. (`model loaded` in ~14 s,
  B580 = 12 GB.)
- **`kvm_agent/config.py`**: `planner_local_url` default `…:8080/v1` → **`…:8090/v1`** (comment documents
  the Docker shadowing). All tools (`probe_planner`, `run_goal_once`, server `build_planner`) pick it up
  with no env threading.
- **New scratch tooling** (gitignored): `scratch/_probe_new_task.py` (offline decompose probe; sets planner
  env IN-PROCESS before importing CFG — dodges the PowerShell `$`-stripping + cmd nested-quote traps),
  `scratch/run_calc.bat <local|claude>` (one-command live run; memory OFF, closed-loop OFF, model label
  `qwen3.5-9b`, goal hardcoded to avoid shell quoting), `scratch/_ocr.py` (tesseract token dump for
  ground-truth frame verification).

## The new benchmark — "compute & transcribe"
Goal string: `Compute 47 x 89 using the Calculator app, then open Notepad and type the result.`
Expected end state: **Notepad shows `4183`** (= 47×89).
- **Repeatable**: built-in apps only, no network, no persistent state mutation; reset = close apps
  (`run_goal` `reset_clean` handles it).
- **Keyboard-only plan** (launch/type/tap/verify) → **no UI-TARS grounding** → isolates the PLANNER and the
  keyboard executive from the (fragile) grounding path.
- **Verifiable**: calc display via `verify number==`, Notepad text via OCR/vision.
- Still exercises the **read-a-value → use-it-downstream** skill the 9B botched on firefox, but cleanly.

9B live plan (budget 0): `[launch calculator, type "47*89", tap enter, verify number==4183,
launch notepad, type "4183", done]` (lint appended the missing trailing `done`). The 9B computed 47×89
itself rather than reading the display — valid, but see the hardening note.

## Caveat / gap found (don't gloss over)
The **live decompose dropped the final Notepad verify** (the offline probe included it; the live plan had
ONLY the calc `verify number==4183`, then typed 4183 and went to `done`). So the agent did **not** self-verify
the Notepad contents — I confirmed `4183` externally via OCR. This is (a) run-to-run plan variance at
budget 0, and (b) exactly the "success decoupled from the screen" class the project fights. For a robust
benchmark the plan must reliably include the final verify.

## NEXT (revised)
1. **Claude baseline** on the SAME task — `scratch\run_calc.bat claude` — for the head-to-head (local 19.6 s
   vs Claude). NOT run this session (chose local-only). This is the apples-to-apples number the prior doc wanted.
2. **K-rep reliability** of the local run: does it pass N/N, and does the plan reliably include BOTH verifies?
   One pass is a single sample. (Reuse the `measure.py` K-rep pattern.)
3. **Abandon the "capped budget" line** — not available in b9692. If a middle ground is needed: (a) a newer
   llama.cpp build that honors a positive `--reasoning-budget` (the `--reasoning-budget-message` arg implies
   intent), (b) a prompt-level "think briefly" instruction, or (c) HYBRID: budget 0 for easy tasks, budget-on
   (accept ~78 s) only for hard ones.
4. **Ratchet difficulty** now that a clean task passes all-local: a product the planner can't do mentally (forces
   READING the display), or a step that needs a click (re-introduces grounding) — one new axis at a time.
5. Keep the firefox/default-app class only as a **HARD-tier** probe, never the repeatable baseline.

## Repro
```
# server (B580) — now on 8090, from C:\Users\aahla:
llama-server.exe -m Qwen3.5-9B-UD-Q4_K_XL.gguf --mmproj mmproj-F16.gguf --device Vulkan0 -ngl 99 \
  -c 16384 --image-min-tokens 1024 -np 1 --reasoning-budget 0 --host 127.0.0.1 --port 8090
# offline plan probe (no rig):
python scratch\_probe_new_task.py --thinking 0      # fast/correct;  --thinking 1 = ~78s, NOT capped
# live run (rig free; stop nothing — agent_server wasn't running):
scratch\run_calc.bat local                          # -> runs\calc_transcribe_<time>\, status done 19.6s
# ground-truth a frame:
python scratch\_ocr.py                              # tesseract token dump (edit the path inside)
```
Baseline to beat: Claude `run_goal` (firefox, prior session) = 27.7 s. Local new-task = **19.6 s** (easier task; run Claude on the SAME task for a fair number — NEXT #1).
