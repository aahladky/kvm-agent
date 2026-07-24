# SESSION 2026-07-23 — Controlled physical model/harness calibration

## Outcome

Slice B of the approved model/harness integration plan is implemented and
live-validated. One bounded, no-retry run traversed the real production path:

```text
camera capture → HoloSession request → local Holo3.1 → production parser
               → pixel projection → physical HID → camera capture → finished
```

Seed 7319 completed in five model steps and 77.2 seconds. The model clicked the
seed-positioned START control, re-observed the newly revealed nonce `KVM-0289`,
focused the field, typed it exactly, submitted it, re-observed the green success
state, and only then emitted `finished`.

Evidence: `runs/physical_calibration_smoke_20260723_165441/`.

## What changed

- `tools/physical_calibration_target.html` is one static, repository-owned target.
  Its start-button location and nonce come from a recorded seed. START reveals the
  nonce and entry field; only an exact value transitions to success.
- `tools/physical_calibration_smoke.py` is setup and evidence plumbing, not another
  actor loop. It starts a small static HTTP server, launches Firefox visibly via
  GNOME Alt+F2 over the existing HID appliance, then calls production `boot()`,
  `run()`, and `shutdown()`. The production run is capped at six steps and each
  model call at 60 seconds. There is no retry.
- The page does not send a success result to the host. Its JavaScript only changes
  local DOM state. The access log contains two target requests: the page and its
  favicon. The driver decides page presence/stage from broad magenta, amber, green,
  and red regions in captured BGR frames.
- Completion requires both a green final camera frame and a `finished` action whose
  own recorded pre-decision frame is already green success. A batch that submits and
  claims completion before re-observing success therefore fails the termination
  protocol.
- `tests/test_physical_calibration_smoke.py` covers seeded rendering, absence of a
  target result channel, synthetic stage/oracle frames, terminal ordering, and
  evidence-based failure ownership without contacting the model or rig.

## Evidence inspection

The five production decision frames were inspected by eye:

1. `physical_calibration_actor_20260723_165452/step_00.png` shows the start page and
   the lower-right START control selected by seed 7319.
2. `step_01.png` shows the revealed `KVM-0289` nonce and an empty, unfocused field.
3. `step_02.png` shows the field focused with a visible caret.
4. `step_03.png` shows exactly `KVM-0289` in the field before SUBMIT.
5. `step_04.png` shows the green `CALIBRATION SUCCESS` state that the terminal
   decision actually saw.

The matching five wire records preserve the request messages, raw responses, parsed
actions, usage, and timing in
`runs/physical_calibration_smoke_20260723_165441/logs/holo_requests.jsonl`.
RunRecorder preserves the frames, raw assistant messages, parsed pixel coordinates,
execution state, and summary beside them.

The page oracle measured a 0.631 green-frame fraction and 0.051 magenta-marker
fraction on the final frame, comfortably above the 0.12 and 0.004 thresholds.
`post_implementation_inspection.json` replays the final classifier over the saved
frames and confirms the stage sequence `start, entry, entry, entry, success`.

## Inspection-driven correction

The live pass predicate was sound, but initial diagnostic output formed one bounding
box around every blue pixel. Firefox's blue “restore tabs” control widened that box,
making failure localization less trustworthy. The target locator now selects the
largest connected blue component. Replaying it over the immutable live frames puts
the START click inside the START component, the field-focus click outside the SUBMIT
component, and the SUBMIT click inside the SUBMIT component. This correction changes
only failure diagnosis; final success and terminal ordering never depended on the
blue target box, so another physical run would add no evidence.

## Gates and scope

The new focused tests pass 5/5 and the complete deterministic suite passes 182/182:
`runs/slice_b_final_20260723_170734/full_pytest.txt`.

This remains an explicit integration check for capture, coordinate, HID, focus, or
closed-loop changes. It is not an ordinary merge gate, a capability benchmark, or a
reason to rerun broad desktop tasks. It adds no scenario registry, retry layer,
database, grader model, or second execution loop. General application behavior and
Phase 2 D-d remain separate, evidence-gated questions.
