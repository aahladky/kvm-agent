# PLAN 2026-07-23 — GNOME evaluation-session reset (APPROVED)

_Approved by the operator after the first D-c physical battery exposed state carryover.
The laptop's warm reboot is unusable because it can leave the network adapter offline;
a full shutdown/boot restores it but is manual and still does not revert saved files._

## Goal

Provide a useful between-task reset without installing an agent on the target and
without pretending that logout is a filesystem snapshot. Reset only state the battery
explicitly owns:

- remove task-declared files from a dedicated evaluation account;
- restore task-declared GNOME settings to known values;
- optionally log out to clear applications, windows, clipboard, dialogs, and focus;
- retain manual full shutdown/boot as the strongest existing fallback.

## Safety contract

1. Cleanup paths are simple filenames relative to the evaluation user's home directory:
   no absolute paths, `..`, slashes, globs, environment expansion, or broad directories.
2. Settings restoration is an allowlisted named profile implemented in code, not
   arbitrary shell supplied by task JSON.
3. Commands are typed visibly through the existing physical HID channel. Nothing is
   installed or left running on the target.
4. The battery records the selected reset strategy and task reset manifest in its
   results so the experiment is auditable.
5. Cleanup failure is loud. The harness never silently continues from an unknown state.

## Reset strategies

- `manual-power-cycle` — existing operator full shutdown/boot prompt, then HID gate.
- `cleanup` — task-declared file/settings cleanup through a visible terminal, close the
  terminal, then HID gate. No process/session reset.
- `cleanup-logout` — cleanup, GNOME logout, operator logs into the dedicated evaluation
  account, then HID gate. This clears session state without a warm reboot.
- `none` — disclosed state carryover; replaces the ambiguous `--no-reboot` behavior.

The default remains `manual-power-cycle` until the dedicated account is ready.

## Initial task manifests

- remove `hello.txt` before `editor_save_file`;
- remove `report.txt` before `file_create_rename`;
- restore the default color scheme before `dark_mode_confirm`;
- remove `time.txt` before `clock_to_file`;
- remove `notes.txt` before `copy_paste_notes`.

These manifests prevent stale outputs from satisfying a task. They do not fix the
separate oracle-design problems in temporal/procedural tasks; those remain recorded in
`docs/REPORT_2026-07-23_codebase_review.md` and the D-c rig review.

## Verification

Offline tests cover manifest validation, shell-command construction, reset-strategy
dispatch, result provenance, and the absence of broad/destructive cleanup. Physical
confirmation requires the operator to create or select a dedicated evaluation account,
then run a short cleanup/logout smoke test before another full battery.
