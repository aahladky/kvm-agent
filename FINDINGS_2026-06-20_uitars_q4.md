# UI-TARS-1.5-7B (Q4) first instrumented run — diagnosis

_2026-06-20. Run: `runs/uitars-q4_20260620_072444/` (operate.py, backend=uitars, history=4,
14-step cap, free-run). Driven + analyzed remotely; per-step frames+Thoughts captured by the
new operate.py logging. Goal: "Open Notepad and type: milk, eggs, and bread. Then open
Calculator and compute 42 + 17"._

## Outcome
Did **not** terminate (hit the 14-step cap). All 14 actions were clicks; **no `type`, no
`finished()`**. From step ~3 it sat in the Calculator clicking a digit-pad region and looping.
`dup_of_prev=False` every step — unlike EvoCUA's no-op loop, the clicks **did** change the
screen.

## What actually happened (from the model's own Thoughts + the display)
UI-TARS's reasoning and screen-reading are **correct**; its **digit-pad grounding is not**:

- It opened Calculator and entered the first number, then at step 7 *intended "7"* but its click
  landed on the **"2"** button → display became **`42 + 12`**. It then **correctly read** "12",
  **correctly concluded** "that's wrong", and **cleared to retry** (step 8 Thought: *"Oh no…
  showing 12 instead of 42 + 17… I'll clear"*).
- Retry: it meant to enter `42` but the clicks produced **`84`** → display `84 +`. Again it read
  it correctly (*"ended up with 84… let me clear"*) and cleared.
- → infinite **clear → re-enter → mis-click → wrong display → clear** loop. It never reaches 59,
  so it never emits `finished()`.

Evidence: `_dbg/u03.png` (step-3 crosshair dead-on "4" — buttons are **large**, so this is not a
tiny-target problem), `_dbg/disp08.png` (display = `42 +` / `12`), `_dbg/disp12.png` (`84 +` / `84`).

## Root cause (this run) — CORRECTED: history coordinate-mimicry, NOT quant
First pass blamed Q4 grounding. **That was wrong** (the same reflex that mis-led the EvoCUA
saga). Proven by an offline A/B probe against the laptop Ollama, replaying the captured frames
(same model, same Q4, same frame, same coordinate conversion — only the history differs):

| Probe | context | model's click | lands on |
|---|---|---|---|
| A | **no history**, "click the 7 button" | `(182,445)` | **"7" — dead on** |
| B | **real 4-step history** + goal | `(251,553)` | **"2"** (verbatim the coord from step 4's history) |

So the model grounds "7" **correctly** in isolation; the replayed history makes it **copy a
prior action's coordinate** instead of grounding the current target. Verified mechanism:
- The raw emitted coords are byte-identical across different intents — `(251,553)` served "2"
  (step 4), "7" (step 7), "2" (step 10); each repeat was already present in the 4-step window.
- Our coordinate conversion is correct (step 3 raw `(173,500)` → px `(172,495)`, dead on "4").
- Most targets that recur with a *correct* in-history coord stay correct ("4","+","CE") — i.e.
  the model is reusing in-context coordinates, good or bad.

This is the **coordinate-level analog of the EvoCUA history-mimicry bug**; the EvoCUA findings
even warned: *"never store raw model text that you also feed back as exemplars without
normalizing it — the mimicry channel — this applies to any future field."* We hit exactly that
with UI-TARS click coordinates. Its error-correction works (notices wrong display, clears), but
because the next re-entry copies a bad coord again, self-correction becomes an endless loop.

Honest caveat: Q4 *may* make the model lean on copying vs re-grounding more than Q5/Q8 would —
but history is the **proven, primary lever** (Q4 alone grounds "7" fine), so it's what to fix
first. Do not jump to Q5.

## Contrast with the EvoCUA flail
- **EvoCUA (Q5):** confabulated a non-existent "calculator icon", clicked the pinned Microsoft
  Store, looped open/close with **no self-awareness**; clicks were no-ops (`dup=true`).
- **UI-TARS (Q4):** real Calculator, **coherent reasoning, accurate display reading, genuine
  self-correction** — but shaky digit grounding → a *sighted* clear/retry loop (`dup=false`).

Behaviorally UI-TARS is the better agent here; it's **grounding-bound, not reasoning-bound**.

## Caveats on this run
- **Contaminated start state:** the screen began with a leftover Notepad "save changes?" dialog
  from earlier aborted runs, and the model believed the shopping list was already written — so
  this run did **not** exercise the Notepad half. Reset the target to a bare desktop before each
  bake-off run.
- Single run, Q4, history=4, temp 0.01. ~12.5 s/step (history fills → ~14 s/step; prompt cache
  can't help, images change every step).

## Next tests (one variable at a time) — fix the HISTORY, not the quant
1. **Audit our history replay vs UI-TARS reference + kill the coordinate-copy vector.**
   `uitars_agent._build_messages` replays each prior turn as `user(image)` + `assistant(full
   raw response incl. `click(start_box='(x,y)')`)`. Those raw coords are the copy exemplars.
   Options to test: (a) match bytedance/UI-TARS's actual history format (it may compact/abstract
   prior actions); (b) replay prior actions with coordinates **abstracted** (Thought + target
   description, no pixels) so state is preserved but there's nothing to copy; (c) history-depth
   sweep. The A/B probe rig (`runs/.../` frames hitting Ollama directly) tests each offline, no rig.
2. **Clean desktop start** so the Notepad half is actually tested (this run began mid-task with a
   stale save-dialog).
3. **Retry-cap guard** in the harness: after N clear/restart cycles without progress, abort (so a
   stuck loop fails fast instead of burning the step budget). The re-click guard didn't fire
   because the clicks vary across the pad.
4. (Secondary, only after 1) Q5/Q8 to see if higher precision *also* resists copying — but not
   before the history fix, which is the proven cause.

## Tooling note
operate.py now writes `runs/<model>_<ts>/` with `frames/stepNNN.png` + `manifest.json`
(step, sha, dup_of_prev, **thought**, actions, xy, pred_s, full response) + `goal.txt`. That
logging is what made this diagnosis possible from the captured run alone — every future rollout
is self-documenting and readable straight from the folder.
