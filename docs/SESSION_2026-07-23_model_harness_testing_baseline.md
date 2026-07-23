# SESSION 2026-07-23 — Model/harness testing baseline

## Result

`main` is a clean starting point for the approved model/harness integration work. It is
four intentional, unpushed reset-isolation commits ahead of `gitea/main`:

- `1e7a2b0` — active cleanup replaces the brittle logout path;
- `da71ba6` — reset tolerates the target's terminal implementation;
- `957dd6b` — disposable applications are force-closed;
- `6d97a03` — cleanup uses the battery-wide owned-state manifest.

The commits are already linear on `main`, form one coherent change stack, pass
`git diff --check`, and require no repair before branching. No secondary worktree or
uncommitted/untracked source change exists. Nothing was pushed.

## Verification

The complete offline suite passed 171/171 in 14.69 seconds:
`runs/main_baseline_20260723_155706/pytest.txt`.
The reconciled plan/current-state documentation passed the layout gate 5/5:
`runs/model_harness_plan_20260723_160424/docs_layout.txt`.

The review found substantial existing deterministic integration coverage in
`tests/test_agent_loop.py`, `tests/test_model_seam.py`, and
`tests/test_holo_messages.py`. Duplicating that coverage would add maintenance without
new evidence. The actual gaps are:

1. no fixed-frame smoke through the real endpoint and production `HoloSession`;
2. no controlled, deterministically scored target for the complete
   capture→model→HID→capture path.

The approved implementation is recorded in
`docs/PLAN_2026-07-23_model_harness_integration_testing.md` and split into two branches
so each diff stays independently reviewable.

## Workspace note

Ignored Python caches and pre-existing tool-managed `.claude` state are not pending Git
changes. They were not folded into this task or treated as source; deleting operator
tool configuration would be a separate, explicitly scoped cleanup.
