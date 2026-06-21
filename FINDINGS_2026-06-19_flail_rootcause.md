# Flail run root cause — 2026-06-19 (Windows target)

_Evidence: `flail_frames/` (20 frames + manifest.json), `runs/ollama-request-logs-1193963349/`
(21 request bodies, images + prompts), `journalctl_ollama.txt`. Annotated proof:
`_dbg/ROOTCAUSE_taskbar.png`._

## TL;DR
The model never opened Calculator because it **clicked the wrong place for the search box
and never noticed**. For "search bar" it emitted `[500,980]` (→ **961px, the dead-center of
the taskbar — empty**) on a **left-aligned Windows-10-style taskbar** whose "Type here to
search" box is at the **far left (~10–340px)**. The click was a no-op, so the subsequent
`type "calculator"` typed into nothing. The model then confabulated a "calculator icon in
the taskbar" and clicked **the pinned Microsoft Store icon** (~563–588px; **no Calculator is
pinned**), which opened the Store. From there it entered a **2-action limit cycle** —
"close the Microsoft Store" → "click the calculator icon" (Store) → repeat — until the step
budget ran out and the run was scored FAIL (~260s).

This is **not** quant, parser, HID, or coordinate-scaling. It is an **agent-grounding +
no-recovery** failure, and it is fully reproducible from the logs.

## Run under test
- Goal: **"Open Notepad and type: milk, eggs, and bread. Then open Calculator and compute 42 + 17"**
- Model `evocua-8b-q5-clean`, `num_ctx 16384`, `temp 0.01`, history depth 4, harness = `operate.py` (free-run).
- Target: **Windows** (taskbar/Start/Notepad/Microsoft Store), capture **1920×1088**, 1000×1000 grid (relative coords).
- Parser: all 13 responses used canonical `<tool_call>\n{…}\n</tool_call>` — **the 2026-06-18 same-line drop bug did NOT recur**; every action parsed and fired.

## Trajectory (reconstructed from request bodies; grid → real px via /999×1920,1088)
| A# | action | px | result |
|----|--------|----|--------|
| 1 | wait | — | — |
| 2 | click "search bar" `[178,980]` | (342,1067) | **HIT** — right edge of real box; search/Start opened |
| 3 | click "Notepad" `[81,534]` | (156,582) | **HIT** — Notepad opened |
| 4 | type "milk, eggs, and bread" | — | typed into Notepad ✓ |
| 5 | key ctrl+w | — | close Notepad |
| 6 | click "Don't Save" `[520,491]` | (999,535) | **HIT** — small center dialog button; Notepad closed |
| 7 | click "search bar" `[500,980]` | **(961,1067)** | **MISS** — empty taskbar center; no-op |
| 8 | type "calculator" | — | **into nothing** (frame frozen 8≡9≡10, `dup_of_prev_step=true`) |
| 9 | wait | — | — |
| 10 | click "search bar" `[500,980]` | (961,1067) | MISS |
| 11 | click "calculator icon in taskbar" `[293,980]` | (563,1067) | **Microsoft Store icon** → Store opens |
| 12 | click "close … Microsoft Store" `[698,19]` | (1342,21) | closes Store |
| 13 | click "search bar" `[500,980]` | (961,1067) | MISS |
| 14 | click "calculator icon" `[306,980]` | (588,1067) | Store |
| 15 | click "close … Microsoft Store" `[702,19]` | (1349,21) | closes Store |
| 16–19 | (search bar 961 → calc icon 588 → close 1349) ×… | | **limit cycle** → step cap → TERMINATE FAIL |

Notepad subtask (A2–A6) **succeeded** — proof the HID, capture, coordinate mapping, typing,
and small-target grounding all work. The failure is localized to the Calculator subtask.

## Root cause (specific + verifiable)
1. **Grounding to a centered-taskbar prior.** For "search bar" the model emits `[500,980]` =
   bottom-**center** (50%, 98%). That matches the *default centered Windows-11 taskbar*, but
   this machine is the *left-aligned Windows-10 style* with the search box at the far left.
   Its own action text even says "bottom **left**" while the coordinate is center — the
   grounding head contradicts both the language head and the pixels. Verifiable: `_dbg/ROOTCAUSE_taskbar.png`;
   the box is at ~10–340px, 961px is empty.
2. **History replay locks the error in.** `[500,980]` is emitted **identically 5×** (A7,10,13,16,19)
   at temp 0.01. Once the first wrong click is stored verbatim and replayed as history, the
   model copies its own prior coordinate for the same described element — a coordinate-level
   version of the mimicry channel flagged on 2026-06-18. It never re-grounds.
3. **No no-op / stall / verify guard.** `operate.py` is free-run: `pico_env.step` never
   compares consecutive frames, there is no "frame unchanged after action" check, no
   repeat-action breaker, and no verify-before-terminate. The instrumentation *detects* the
   stall (`dup_of_prev_step=true` on steps 9–10) but **nothing consumes it**, so a single
   missed click cascades to the full step budget.
4. **Environment rewards the hallucination.** The hallucinated "calculator icon in the
   taskbar" lands on the **pinned Microsoft Store** (~563–588px) and **no Calculator is
   pinned**, so the bad click opens a real window — turning a dead end into a self-sustaining
   open/close-Store loop.

## Variables interrogated — verdicts
| Variable | Verdict | Evidence |
|---|---|---|
| `<tool_call>` parser drop (2026-06-18 bug) | **RULED OUT** | all 13 responses newline-format; every action executed |
| Quant (Q5-clean) grounding precision | **RULED OUT as cause** | A2/A3/A6 (incl. a small center dialog button) all land dead-on; the search-bar error is a fixed center coordinate, not jitter |
| Coordinate scaling / aspect | **RULED OUT** | identical /999 transform; A7 vs A2 differ because the *model* emitted 500 vs 178, not the pipeline |
| HID / capture path | **RULED OUT** | Notepad opened, text typed, dialog dismissed |
| Grounding to wrong prior (center vs left taskbar) | **ROOT (trigger)** | `[500,980]`→961px empty, 5× identical; text says "left" |
| Verbatim-history reinforcement | **ROOT (lock-in)** | same coord repeated deterministically |
| Missing stall/verify guard | **ROOT (no recovery)** | `operate.py`/`pico_env` have none; `dup_of_prev_step` unused |
| Store pinned / Calculator not pinned | **CONTRIBUTING** | taskbar crop: Store at 563px, no Calculator |
| Capture **1920×1088** vs Pico `SCREEN_H=1080` | **LATENT BUG (not causal here)** | `code.py:40`, `pico_env` defaults `(1920,1080)`; real frames 1088 → ~0.7% Y scale error, clamped at bottom. Violates the "capture must equal Pico SCREEN_W/H" invariant `pico_env` itself prints |
| History frames sent at **1312×736** (aspect 1.783) vs current **1920×1088** (1.765) | **LATENT (minor)** | request body 000009: 4 history imgs 1312×736, current 1920×1088 — inconsistent preprocessing + slightly different aspect |

## Fixes (ordered by leverage)
1. **Stall/no-op detector** (highest): if the post-action frame hash == pre-action hash for a
   non-wait action, don't let the model proceed as if it worked — log loudly and, after k
   identical/empty-result steps, abort or inject a corrective turn. `dup_of_prev_step` is
   already computed; just consume it. This alone converts the flail from 20 wasted steps to ~2.
2. **Repeat-action breaker**: k consecutive clicks at the same coordinate (or the same
   described target with no state change) → force a re-observe / escalate. Kills the limit cycle.
3. **Don't reinforce bad coordinates via history**: when an action produced no frame change,
   either drop it from replayed history or annotate it as "no effect" so the model stops
   copying the wrong coordinate.
4. **Fix the resolution invariant**: make `PicoEnv`/`Camera` read the *actual* delivered frame
   size and set Pico `SCREEN_W/H` to match (capture is 1088, not 1080). Cheap, removes a latent
   Y bias and makes the printed invariant true.
5. **App-launch primitive for Windows** (mirror the macOS "use the Dock, not Spotlight"
   lesson): prefer a known-good launch path; if relying on search, verify the search panel
   actually opened before typing. Pin Calculator, or don't pin Store where the model guesses.

## One-line answer
The agent clicked an **empty patch of taskbar** for the search box (grounded to a centered-
taskbar prior on a left-aligned taskbar), **never detected the no-op**, repeated the exact
same wrong click via history replay, and its fallback "calculator icon" guesses hit the
**pinned Microsoft Store** — so it open/close-looped the Store to the step cap. Trigger =
grounding prior; lock-in = verbatim history; no recovery = missing stall/verify guard;
amplifier = Store pinned, Calculator not.
