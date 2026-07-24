# Session 2026-07-23 — evidence locality, documentation cleanup, and D-d readiness

## Outcome

The evidence-layout debt is closed in code and on the deployed Pi appliance. Active
documentation now contains current operating truth rather than a chronological notebook.
D-d has a three-run maximum discovery probe and is no longer indefinitely blocked on
waiting for a model failure.

## Evidence changes

- `RunRecorder` now owns `model_requests.jsonl` and records exact tool-output payloads
  plus host-observed HTTP/Pico responses in each step.
- `HoloSession` and `HoloVerifier` route model logs into the owning run. Every image data
  URL is decoded into a content-addressed file there, preserving the exact JPEG bytes
  the actor/verifier saw. Unbound one-shot calls create a fresh
  `runs/model_request_<timestamp>/` folder instead of appending to a shared global log.
- `ApplianceClient` retains successful and failed bridge responses until the loop drains
  them into the step record.
- Battery boot, reset, reset-verifier, and HID-gate evidence stays in the battery run;
  replay and live-model smoke requests stay in their own run folders.
- Manual `cap()`, `ground()`, and `mark()` artifacts now live in recorded run folders,
  not `scratch/_dbg`.
- The Pi bridge creates `/home/aaron/runs/hid_bridge_<timestamp>/commands.jsonl`.
  The deployed files were backed up first under the appliance’s
  `/home/aaron/runs/hid_bridge_deploy_20260723_215430/`.

Deployment evidence, health response, active service state, log path, backup contents,
and source checksums:
`runs/hid_bridge_deploy_20260723_215502/`.

## Hidden-state and build cleanup

- Existing Pico SDK/PS2 dependencies moved from hidden directories to
  `appliance/pico_fw/deps/`.
- The existing firmware build and UF2 were preserved under
  `runs/pico_fw_build_migrated_20260723_214922/`.
- Future firmware builds create `runs/pico_fw_build_<timestamp>/`.
- Python bytecode, pytest cache, and obsolete tool-session directories were removed.
- `tools/run_tests.py` runs Python with bytecode disabled, disables pytest’s cache
  provider, and retains console output in one run folder.
- The docs-layout test rejects regenerated `.claude`, `.pytest_cache`, `__pycache__`,
  and retired hidden Pico build/dependency directories.

## Documentation

`PROJECT_STATE.md` was rewritten as the concise current architecture, operating
contract, code map, proven claims, limits, and D-d handoff. `docs/ROADMAP.md` now holds
only current sequencing, gates, and triggered maintenance. `appliance/README.md` now
documents the live appliance rather than its bring-up diary.

Completed plans, findings, reports, legacy READMEs, and old session narratives moved
intact to `_archive/docs_history/`. The active `docs/` root contains the roadmap, this
current session record, and native Holo assets.

## Bounded D-d trigger probe

`tools/battery_tasks_d_d_trigger.json` contains three 12-step tasks:

1. save → rename → reopen, targeting false assumptions at commit boundaries;
2. exact selection/copy/paste/save, targeting invisible clipboard/selection errors; and
3. calculate → transcribe → save → reopen, targeting transient-state transfer errors.

`tools/battery.py --task-id ID` runs exactly one. Run each at most once and stop after
the first frame where the actor treats a visibly unsatisfied intermediate postcondition
as complete and advances. If all three are clean, the probe is finished and D-d may
proceed as a non-regression A/B; no broader battery is required.

No physical D-d trigger task was run in this session.

## Verification

- Initial focused run exposed one test-double compatibility issue and retained it at
  `runs/offline_tests_20260723_215150/pytest.txt`.
- Corrected focused evidence/loop/docs checks: 65/65 pass at
  `runs/offline_tests_20260723_215208/pytest.txt`.
- Final focused battery/smoke/replay/docs checks pass at
  `runs/offline_tests_20260723_215350/pytest.txt`.
- Final deterministic suite after exact model-input archiving: **192/192 pass** at
  `runs/offline_tests_20260723_215947/pytest.txt`.
