# SESSION 2026-07-23 — Phase 2 slice D-c: flip the gates

## Outcome

D-c is code-complete and offline-validated; the physical extended battery remains
pending. The hard gate was already earned by D-b's 0/9 live false-refusal result.
This session changes control flow and grading, so it deliberately makes no rig-success
claim from offline tests.

## In-loop terminal gate

`agent_loop_holo.run(verify_mode="gate")` now treats only `satisfied=True` as an
accepted finish. False and None are both fail-closed refusals: the actor receives
`NOT accepted` plus the verifier's evidence in its ordinary `<tool_output>` history,
then gets a fresh observation. `VERIFY_REFUSE_LIMIT=3` bounds the loop and produces
the distinct failed note `answer refused by verifier x3`.

The last verdict and refusal count are returned on every gate-mode exit. Shadow and
off behavior remain unchanged; off still returns exactly its historical two-key dict.

## Automated grading and sampling

`tools/battery.py` defaults to gate mode and verifier-primary grading. A false,
unanswered, or missing verdict grades fail; only an accepted true verdict grades pass.
`--human` retains all-human grading. In normal runs, every model/verifier disagreement
and a random 10% of agreements receives a human grade; void remains human-only.

`--no-reboot` is the approved honest state-carryover mode: it runs one fail-closed HID
gate at boot, skips per-task reboot/replug prompts, disables random live spot-check
prompts, and records a deferred-review marker for any disagreement. It does not claim
the determinism of rebooted tasks.

## Metrics correction

The review found that `battery_metrics.py` could turn an interrupted ten-task battery
with one recorded pass into 1/1. The analyzer now retains each battery's
`total_tasks`, `graded`, and `complete`; completion stays 1/10 in that case. Post-D-c
verifier agreement, false-refusal, and false-confirmation are computed only over rows
with an actual human ground-truth grade, never over verifier-primary rows grading
themselves.

## Verification and remaining work

The complete offline suite passes: 165 tests in 14.30s
(`runs/d_c_offline_20260723_104436/pytest.txt`). Added coverage proves refusal and
continuation, immediate acceptance, x3 termination, verifier-error refusal,
fail-closed grading, CLI contracts, incomplete denominators, and human-sample-only
metrics.

Next is the physical extended GNOME battery in default gate mode, followed by
`tools/battery_metrics.py`. That run decides whether D-c is rig-confirmed. D-d remains
separately gated on observing confident-wrong progress.
