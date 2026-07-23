# REPORT 2026-07-23 — Codebase, recent commits, and outstanding plans

## Result

The live code is coherent and the offline gate is green: 157 tests passed in 11.86s
(`runs/review_20260723_103253/pytest.txt`). `main` was clean and synchronized with
`gitea/main` at review time. The D-a oracle, D-b shadow wiring, serving seam, and
llama-swap matrix enrollment form a consistent sequence; the latest correction commit
`2a22be8` is authoritative over `5d3d76a`: D-c's gate clears, D-d's does not.

## Remaining findings

1. **Incomplete-battery metrics can fail open.** `tools/battery_metrics.py` computes
   completion over result rows already graded pass/fail. An interrupted ten-task
   battery with one passing row can therefore read 1/1 (100%), recreating the exact
   ambiguity `tools/battery.py.make_payload` fixed. Preserve and aggregate
   `total_tasks`, `graded`, and `complete`; missing/ungraded tasks must remain visible.
   This belongs in D-c because automated grading makes the denominator load-bearing.
2. **Serving command parsing is lossy.** `kvm_agent/llm/serving.py` removes all
   backslashes and calls `split()`. A future quoted path or escaped space can be
   corrupted in the recorded serving contract. Normalize line continuations, then use
   shell-aware tokenization.
3. **Workspace hygiene needs an operator-safe cleanup.** Untracked
   `.claude/settings.local.json`, `.pytest_cache/`, and `__pycache__/` existed during
   review despite AGENTS.md §1. They were not deleted during a read-only review because
   existing untracked state belongs to the operator. Prevent regeneration outside
   `runs/` and remove or relocate them in a dedicated cleanup.

## Outstanding plan order

1. **D-c:** unblocked; implement terminal verification gating and fail-closed automated
   grading with the defined human spot-check sample.
2. **D-d:** mechanism settled as an explicit planner call, but implementation remains
   gated on observing confident-wrong progress rather than another clean sweep.
3. **Firmware soak:** deployed fixes are functional; the eight-hour soak/fault-injection
   gate remains postponed, not abandoned.
4. **Deferred hardware:** power control and bridge-side suspend keep-alive remain
   evidence-gated. Windows-only problems stay moot on the GNOME target.
