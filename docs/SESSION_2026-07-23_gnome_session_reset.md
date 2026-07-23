# SESSION 2026-07-23 — GNOME evaluation-session reset

## Outcome

Implemented the approved reset slice without a power-button actuator, warm reboot, or
resident target-side agent. The code is offline-validated; a short physical smoke test
on the dedicated evaluation account remains. Full suite evidence:
`runs/session_reset_offline_20260723_113316/pytest_auto_login.txt`.

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
- `cleanup` — allowlisted cleanup, retain the running session;
- `cleanup-logout` — cleanup and GNOME logout, then operator login;
- `none` — disclosed state carryover (`--no-reboot` remains a compatibility alias).

The GNOME task list removes `hello.txt`, `report.txt`, `time.txt`, and `notes.txt`
before their producing tasks and resets the default color scheme before the dark-theme
task. Battery results now record `verify_mode`, grader, spot-check percentage, and reset
strategy, closing the provenance gap found in the first D-c physical run.

`--auto-login` removes the repeated operator-login step for `cleanup-logout`. The
battery prompts once with `getpass`, keeps the credential in process memory only, and
types it into GDM after each logout. Results record only `auto_login: true`; the
credential is never serialized. GDM is expected to leave the just-logged-out eval
account selected with its password field focused. The following camera/HID gate is the
authoritative proof that this assumption held and the desktop became interactive.

## Operator setup and physical smoke

Create or select a dedicated standard GNOME user containing no personal files. Log into
that account and confirm Ctrl+Alt+T opens Terminal. No passwordless login and no target
service are required.

Run a one- or two-task smoke with `--reset-strategy cleanup-logout --auto-login`. Enter
the disposable password once at the hidden prompt. For each task, verify cleanup logs
out, GDM selects the same account, the password is entered, and the existing camera/HID
gate passes. Deliberately creating one declared file before the smoke proves cleanup
removes it. A typo/permission failure should leave `KVM_RESET_FAILED` visible instead
of reaching GDM.

## Limits

This resets only declared battery-owned files/settings and the GNOME session. It is not
a disk snapshot and must never be pointed at a personal account. Procedural/temporal
oracle flaws in `file_create_rename`, `clock_to_file`, and `copy_paste_notes` are
separate from reset and remain to be corrected before D-c rig confirmation.
