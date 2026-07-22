# Agent Working Agreement — kvm-agent

This file binds EVERY AI agent that touches this repo — Claude Code, Kimi, Gemini,
Copilot, Hermes, whatever comes next. Read it fully before doing anything. It is
short. It is enforced by review: the user reviews diffs and artifacts, not excuses.

## 1. Output discipline — the law

- **Every log, test-run artifact, benchmark result, captured frame, trace, and
  script output goes in `runs/` — nowhere else.** `runs/` is permanent: it never
  moves, never gets renamed, never hides. (It is a symlink to
  `~/data/kvm-agent/runs`; that backing store may move, the `runs/` path may not.)
- One folder per run: `runs/<what>_<YYYYMMDD_HHMMSS>/`. If you write a script that
  emits output anywhere else by default, your script has a bug — fix the script.
- **Absolutely nothing project-related in hidden directories.** No `.claude/`, no
  `.cache/`, no dotdirs, no "temporary" state that only exists in a session folder.
  If it matters and it isn't in the repo or in `runs/`, it does not exist.
- Worktrees only at `~/workspace/worktrees/<task-name>` — visible, named for the
  task, deleted when merged or abandoned. Never inside `.claude/`.
- `scratch/` self-destructs in 14 days. Anything worth keeping gets promoted into
  `runs/` or the repo BEFORE the session ends.

## 2. The model is the last suspect

The local model is the one component we cannot change. Everything around it —
capture, prompting, parsing, focus handling, HID injection, verification — is ours,
and historically it is where the bugs are. You may NOT conclude "model limitation"
until you have produced, in writing, in `runs/`:

1. The exact frame + prompt the model saw (inspected by eye, not assumed).
2. The model's exact raw output (unparsed, unfiltered).
3. A walk of every transformation between capture → prompt → parse → action, each
   verified, or the bug named.
4. An offline replay: the saved frame+prompt fed directly to the model server with
   no pipeline. Model correct in isolation → pipeline bug, keep digging. Model
   wrong in isolation → now you may blame it, and you attach the replay as a
   minimal eval case.

Before claiming model fault, read the Blame Ledger below and state why your case
is different from every entry in it.

## 3. No ghost generations

Never create a new agent loop / planner / harness generation without, IN THE SAME
COMMIT, moving its predecessor to `_archive/` or deleting it. Four generations of
the same idea living side by side is how this repo got its reputation. `_archive/`
is write-only: add, never extend.

## 4. Session shape

- One task = one branch or worktree = one diff the user can review in ten minutes.
  Bigger task? Split it before you start.
- Cheap gates before rig time: unit tests, self-tests, dry-run harnesses pass
  BEFORE the user is asked to verify anything on the physical rig.
- Session ends commit-or-revert. `git status` clean. No uncommitted limbo, no
  untracked leftovers, no "I was about to".
- Before the session closes, record what changed and what was learned in
  `PROJECT_STATE.md` (and `docs/SESSION_*` for anything non-trivial).

## 5. Blame Ledger

Every time the model was blamed for a failure, and what the root cause actually
was. Append a row whenever a "model failure" is resolved — in either direction.

| Date | Symptom | Blamed on model | Actual root cause |
|---|---|---|---|
| 2026-06-21 | "Set default browser" task failed | Planner too small | THREE code bugs: UI-TARS grounding emitted finished()/scroll on visible targets; verify substring-matched text never on screen; click-confirm used frame-diff. Fixed: GROUNDING_DOUBAO mode, vision `ask` verify, per-step logging |
| 2026-07-19 | Typed text never landed in Notepad | "Dead mouse" / input flakiness | Win32 focus never transferred on app launch (desktop kept foreground). Fixed: click-to-focus retry in agent_loop_holo._execute() |
| 2026-07-19 | windows_calc 0/9 across history depths | Model input: resolution, history-depth | Both tested and DISPROVEN. Real causes: inconsistent WinUI3 date-picker widget + a stuck-popup click bug in our pipeline |
| 2026-07-21 | calc_multiply flailed 0/20 (first physical battery) | "Model's known M-class limitations" | MIXED, split three ways (evidence: `runs/battery_calc_multiply_20260721_074305/`): ~70s OS dead window swallowed delivered input (steps 0-9); "screen changed" tool result technically true / semantically FALSE at steps 4,5,11 (taskbar focus visuals, not search results — model typed blind); goldfish memory (model's own reasoning: "screenshots are being evicted, I can't see the results of my actions") + click-repeat guard disabled by design. Genuinely model: `winleft` invention, double-× |
| 2026-07-22 | paint_line hit max_steps with a "misclick cascade" (clicked pinta result → junk Firefox Google search); operator note: "model seemed to be unable to select or delete text" | Model clicking/text-editing | NEITHER (evidence: `runs/battery_paint_line_20260721_235845/`, docs/SESSION_2026-07-22): task infeasible (no paint app on the GNOME target) + decide-act TOCTOU race — during the model's ~19s think, GNOME's async search re-flowed (slow App Center snap provider row dropped out), so a click CORRECT against the decision frame (step_09.png, verified by eye + projection check) landed on the "Search online" row that slid up under it. Model never attempted select/delete; its X-button clears worked (steps 3, 13) |

Current score: model 0, our code 4 (+1 shared row, score held pending the signal redesign). Update the score with every row.
