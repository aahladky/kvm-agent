# UI-TARS Q8 — stood up & tested (2026-06-20)

## Setup (done)
Pulled `ui-tars-1.5-7b-q8_0.gguf` (8.10 GB, qwen2vl) from Lucy-in-the-Sky to the laptop;
`ollama create uitars-q8` reusing the **existing mmproj blob** + same Modelfile as q4
(`num_ctx 16384`, `TEMPLATE {{ .Prompt }}`). Runs at **9.0 GB / ~6.4 s/step**, fits the 12 GB
card with the freed headroom — no OOM. Driven via `operate.py --model uitars-q8`.

## Result (run `runs/uitars-q8_20260620_105800/`, clean start, --expect 59, 24-step cap)
**Did not complete** — ABORTed by the recovery-loop guard. Notepad half done correctly
("milk, eggs, and bread" typed); then it wandered in the calculator launch/entry phase
(clicks scattered into wrong windows, e.g. (1614,225)) and never converged on the keypad.
Same failure *class* as Q4.

Caveat: ONE deterministic sample (temp 0). Per our own EvoCUA lesson, single samples mislead —
this is **not** proof Q8 ≈ Q4; it's "Q8 didn't obviously crack it here," and the messy start
state (leftover windows surviving Win+D minimize) contributed to the wander.

## What this tells us
- The durable win stands: the **input bug (history coordinate-mimicry) is fixed** — grounding
  is reliable at both quants; digits land on keys.
- The residual — **long-horizon planning / state-tracking on a multi-app task + fiddly calculator
  launch** — persists across Q4 and Q8. So the quant lever (the thing the +4GB enabled) is **not**
  the fix for this residual; it's a genuine UI-TARS-1.5-7B capability limit on long horizons.

## Un-tried levers (NOT yet "not possible")
1. **Measure success RATES (K≥10 runs) at Q4 vs Q8** with auto clean-reset, instead of single
   deterministic samples — the only honest way to compare, per the EvoCUA re-baseline lesson.
2. **Task decomposition / structured controller:** the model's *single-action grounding* is now
   reliable; its *long-horizon planning* is the weak point. Externalize the plan — feed one
   sub-goal at a time ("open Calculator", then "type 42", then "press +", …) so the model only
   does the thing it's good at. Highest-leverage architecture change.
3. **Truly clean start:** close apps (not just Win+D minimize) so leftover windows can't derail
   navigation; or run the calculator sub-task pre-opened (run_probe-style AC-reset).
4. Simpler/representative tasks: the Notepad half + the calibration targets were reliable; the
   brutal part is the calculator keypad arithmetic specifically.
