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
