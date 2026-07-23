# SESSION 2026-07-23 — Controlled live-model contract smoke

## Result

Slice A of the approved model/harness integration-testing plan is complete and
live-validated without using the camera, HID appliance, or laptop. The real local
Holo endpoint passed all four controlled production-session contracts on one request
per case, with no retries:

| Case | Parsed action | Result | Wall time |
|---|---|---:|---:|
| `click_target` | click `[640.0, 374.4]` inside the declared button | pass | 33.811s |
| `type_nonce` | type `KVM-7319`, `press_enter=false` | pass | 6.187s |
| `complete` | `finished` | pass | 8.088s |
| `incomplete` | click `[640.0, 446.4]`, no `finished` | pass | 6.917s |

The first call included a cold model load: the recorded serving snapshot showed
`holo3.1` non-resident with `fast-7b` co-resident. The subsequent 6–8 second calls are
the relevant warm-path timing. Evidence:
`runs/model_contract_smoke_20260723_161257/summary.json`.

## Implementation

- Added `tools/model_contract_smoke.py`, one noninteractive tool that generates four
  fixed desktop-like frames and sends each through a fresh production `HoloSession`.
  It is not an agent loop and contains no HID/capture path.
- Broad predicates check target-region clicking, exact nonce typing, correct completion,
  and refusal to finish an explicitly incomplete state. They do not compare reasoning,
  prose, or exact point coordinates.
- A response exception is classified as infrastructure failure and stops the run;
  semantic/schema disagreement is a contract failure. Neither path retries.
- Each case records its exact JPEG, complete request, actual wire log, raw response,
  parsed step, projection evidence, and result under one run directory.
- Added a bounded `timeout_s` argument to `call_holo_full`; its production default
  remains 180 seconds, while the smoke explicitly used 45 seconds.
- Added six offline tests covering deterministic frame generation, all predicates,
  normalized-coordinate projection, artifact completeness, infrastructure abort, and
  preflight failure.

## Verification

- Focused offline tests: 22/22 passed
  (`runs/model_contract_slice_a_20260723_161037/focused_pytest.txt`).
- Complete offline suite: 177/177 passed in 13.90 seconds
  (`runs/model_contract_slice_a_20260723_161037/full_pytest.txt`).
- Live model: 4/4 passed
  (`runs/model_contract_smoke_20260723_161257/summary.json`).
- All four frames were inspected by eye. For every case, the saved JPEG SHA-256 equals
  both the request's recorded hash and the decoded request data URL; the actual wire
  response equals `raw_response.json`; the wire-parsed step equals `result.json`; and
  the request prompt hash equals the saved production prompt hash. Both pointer cases'
  raw `[0,1000]` coordinates project exactly to the parsed pixels
  (`runs/model_contract_smoke_20260723_161257/inspection.txt`).

No model fault was claimed and the Blame Ledger is unchanged.

## Remaining

Slice B is still pending: one repository-owned physical calibration surface exercising
capture→production session→parser→coordinate mapping→HID→capture with a deterministic
page-owned visual oracle. It remains a separate branch and diff; no real-application
scenario or generalized testing framework is part of it.
