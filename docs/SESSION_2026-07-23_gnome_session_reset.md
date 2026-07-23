# SESSION 2026-07-23 — GNOME evaluation-session reset

## Outcome

Implemented the approved reset slice without a power-button actuator, warm reboot, or
resident target-side agent. The code is offline-validated; a short physical smoke test
on the dedicated evaluation account remains. Full suite evidence:
`runs/active_session_reset_20260723_125102/pytest_final.txt` (169 passed).

## What landed

`kvm_agent.hardware.target` now validates task reset manifests and constructs a narrow
visible shell command. Cleanup targets must be simple filenames directly under `$HOME`;
absolute paths, slashes, `..`, globs, expansion, whitespace, and unknown settings
profiles are rejected before HID fires. The only initial settings profile resets
GNOME's color scheme to the account default.

The command is typed through the appliance using Ctrl+Alt+T. Its chain exits or logs
out only after every operation succeeds. A failure leaves the terminal open with
`KVM_RESET_FAILED`; the battery requires operator confirmation before proceeding
because the HID channel does not return a shell exit status.

`tools/battery.py` adds four strategies:

- `manual-power-cycle` — existing default;
- `cleanup` — allowlisted cleanup, terminate known battery apps, retain GNOME itself;
- `none` — disclosed state carryover (`--no-reboot` remains a compatibility alias).

The GNOME task list removes `hello.txt`, `report.txt`, `time.txt`, and `notes.txt`
before their producing tasks and resets the default color scheme before the dark-theme
task. Battery results now record `verify_mode`, grader, spot-check percentage, and reset
strategy, closing the provenance gap found in the first D-c physical run.

The first physical attempt disproved the logout/login design before a task ran: two
batteries created only empty summary directories
(`runs/battery_20260723_123637/`, `runs/battery_20260723_124319/`). The GDM transition
is removed rather than extended with more UI assumptions.

Active-session cleanup now terminates a fixed code-owned application profile (Text
Editor, Calculator, Settings, Files, and Pinta) after restoring declared files/settings.
Missing processes are normal. Success exits the reset terminal; failure leaves
`KVM_RESET_FAILED`. The battery captures the resulting desktop and asks the independent
stateless verifier a narrow reset question: no failure terminal, task window, login, or
lock screen may remain. Every verdict is persisted under `reset_events` before the task,
and False/None aborts the battery loudly. The ordinary camera/HID gate follows it.

The first active-session physical smoke (`runs/battery_20260723_125911/results.json`)
correctly failed closed before task 1, but exposed another harness assumption: the reset
command required a final `gnome-terminal-server` process match, and this target's
terminal uses a different process name. Since every earlier `pkill` is explicitly
failure-tolerant, that final match was the only possible source of the visible
`KVM_RESET_FAILED`. Fixed by including the common GNOME terminal implementations in
the code-owned application profile and making no-match fall back to the shell's own
`exit`. The camera verifier—not a process name—remains the proof that no terminal
window survived.

The next physical run reached task 5 cleanly, then the reset before task 6 failed
closed because Pinta remained visible
(`runs/battery_20260723_130246/results.json`, reset event `top_bar_clock`; task evidence
`runs/battery_paint_line_20260723_132752/`). Pinta was not absent from the command:
both `pinta` and `Pinta.exe` were allowlisted. The bug was reset semantics—SIGTERM
allows an application with an unsaved document to handle/refuse graceful termination.
Allowlisted disposable task applications now receive SIGKILL. This deliberately
discards drafts and bypasses save dialogs; that is the intended reset behavior.

## Operator setup and physical smoke

Create or select a dedicated standard GNOME user containing no personal files. Log into
that account and confirm Ctrl+Alt+T opens Terminal. No passwordless login and no target
service are required.

Run a one- or two-task smoke with `--reset-strategy cleanup`. No password or logout is
involved. Deliberately create one declared file and leave one battery application open
before the smoke. The reset event must be recorded satisfied=True, the file must be
absent, the application window must be gone, and the HID gate must pass. A deliberate
bad manifest is rejected offline before HID; a target-side command failure must leave
`KVM_RESET_FAILED`, produce a failed reset verdict, and stop before the task.

## Limits

This resets only declared battery-owned files/settings and the GNOME session. It is not
a disk snapshot and must never be pointed at a personal account. Procedural/temporal
oracle flaws in `file_create_rename`, `clock_to_file`, and `copy_paste_notes` are
separate from reset and remain to be corrected before D-c rig confirmation.

## Physical correction: snap-hosted Pinta

The SIGKILL change was necessary but not sufficient. The next reset attempt still
failed closed before task 1 with the old Pinta window visible
(`runs/battery_20260723_134309/results.json`). A target-side process listing captured
through the physical HID/camera path showed why: Pinta is hosted by `dotnet`, with
`/snap/pinta/98/lib/pinta/Pinta.dll` in its arguments
(`runs/pinta_reset_diagnosis_20260723_134524/process_probe.png`). The earlier anchored
name regex required whitespace or end-of-command immediately after `pinta`, so the
slash in `/snap/pinta/98` prevented a match.

The application profile now contains fixed command-line regexes, including snap path
segments, instead of guessed executable names. Each pattern brackets its first
character so it cannot match the `pkill` command carrying that pattern. Task JSON can
still select only the named profile and cannot inject regex or shell. Offline coverage
replays the observed snap Pinta command line, covers snap Firefox, and asserts that no
profile pattern matches the generated reset shell command itself.

## Full battery and battery-wide isolation correction

The next complete physical battery proved the snap-aware application reset:
all ten reset events were `satisfied=True`, including the reset immediately after
`paint_line` that had failed twice before
(`runs/battery_20260723_135007/results.json`). The task score was 9/10.
`copy_paste_notes` failed at its 15-step limit while the save dialog was ready for the
filename replacement (`runs/battery_copy_paste_notes_20260723_142126/`); this was not
a reset-verifier or Pinta-process failure.

Reviewing that task's exact trace exposed a separate isolation defect. Its first editor
window opened `time.txt`, and its save dialog showed `report.txt` and `time.txt` from
earlier tasks. Dark mode also persisted beyond `dark_mode_confirm`. Each task declared
the state it could create, but the runner applied only the incoming task's declarations
before that task. Thus reset removed `notes.txt` before `copy_paste_notes` but failed to
remove outputs owned by preceding tasks.

The runner now treats task reset declarations as ownership declarations and builds one
ordered battery-wide union. Every cleanup reset removes all declared battery files,
resets all declared GNOME settings, and terminates the shared named application
profile. The effective manifest is persisted in `run_config` for review. A regression
test covers deduplication and stable ordering; the observed snap-process replay and
shell self-match guard remain. The full offline suite passes 171 tests
(`runs/pinta_reset_diagnosis_20260723_134524/isolation_full_pytest.txt`).

## Validation-boundary correction

Starting another ten-task battery to validate the battery-wide union was the wrong
gate. It repeated already-covered actor tasks, could pause unpredictably for random
human grading, and consumed roughly an hour to answer a reset question that should take
under a minute. The operator stopped it after five completed tasks; its five reset
events were all satisfied and its incomplete denominator was preserved as 5/10
(`runs/battery_20260723_142910/results.json`).

The reset change is accepted on component evidence: the exact snap Pinta process
capture, replay coverage for that command line, 171 offline tests, the earlier complete
battery's 10/10 clean reset events (including post-Pinta), and the follow-up run's
physical use of the battery-wide manifest. Another full battery is not required.

Going forward, a full battery is a benchmark run, not a development or merge gate.
Reset changes require a sub-minute state-seed/reset/verify smoke. Task changes require
only the affected task(s). Before another routine full battery, the runner needs
task selection/resume and must defer random human samples until after execution rather
than blocking the live run.
