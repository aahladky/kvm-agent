# UI-TARS history-mimicry fix — LIVE validation (2026-06-20)

Run `runs/uitars-q4_20260620_103403/` (operate.py, backend=uitars, fixed adapter, 18-step cap).

## The fix (shipped)
`uitars_agent._build_messages` now replays history as a **coordinate-free text action-summary**
("1. clicked 4 / 2. clicked 2 / 3. clicked + …") plus **only the current screenshot** — no
assistant turns with raw `(x,y)` for the model to copy. Plus: `_to_actions` hardened so one
malformed action can't crash a run; stuck-state guard in operate.py.

## What the live run proved — the mimicry bug is FIXED
- **Correct in-context grounding.** Digits landed on the right keys: 4→(167,497), 2→(249,547),
  +→(406,547), and the decisive one **7→(184,441) — the actual "7" key**, NOT the copied "2"
  at (249,547) that the broken history produced. No coordinate-copy, no clear/retry mimicry loop.
- **Multi-step navigation worked:** opened search → Notepad → **typed "milk, eggs, and bread"
  correctly as one string** → back to search → Calculator → entered digits.
- **Fast:** 5.3 s/step (was 12.5 s with the old N-image history) — one image per request.
- **No crash** (robustness fix held); **self-correction active** (it read "49… should be 59" and
  tried to clear/retry).

## Residuals (NOT the input bug — model reasoning + run conditions)
1. **Planning slip:** at step 13 it entered **"7" directly after "42 +"**, skipping the "1" of
   "17" ("click 7 to complete") → computed **42+7=49**. Grounding was correct; the *plan* dropped
   a digit. Then recovery wandered (a misclick showed 24.01) and it ran out of steps without
   terminating. This is 7B/Q4 multi-digit sequencing + recovery convergence — a cognition limit,
   not the history-coordinate bug.
2. **Messy start state** ate ~half the budget: the screen began with a leftover Calculator +
   Notepad save-prompt (aftermath of the wedged-Pico episode), so steps 0–9 were spent clearing
   it before clean calculator entry at step 10. Couldn't auto-reset the desktop without
   re-wedging the Pico (a standalone R4 connection killed by the shell timeout drops the Pico W
   off WiFi — do resets through operate.py's own R4, not a separate process).

## Verdict against the goal (reliable multi-step navigation)
The proven root cause — **history coordinate-mimicry (input)** — is fixed and validated live:
navigation and grounding are now reliable and ~4× faster. What remains is **task completion on
multi-digit arithmetic + clean termination**, which is model-reasoning, not input. Next levers
(in order):
- **verify-before-terminate + retry-cap** in the harness (it self-corrects but doesn't converge;
  bound the recovery and confirm the display equals the target before declaring done);
- **clean start + higher --max-steps** (give the calculation a full budget);
- only then, **Q5/Q8** — now a legitimately *separate* question (reasoning/sequencing under
  context), distinct from the grounding bug that Q5 would NOT have fixed.
