# REPORT 2026-07-23 — Clear and honest project-state overview

## Executive assessment

The project is in a good **experimental-system** state and a poor
**finished-product** state. That distinction is the most useful summary.

The physical KVM primitive is real: the host captures the laptop over HDMI, asks a
local vision model what to do, projects structured actions into screen pixels, and
drives the target through an external Pi 5 + Pico USB-HID appliance. The controlled
model seam passed 4/4 fixed-frame contracts, and one no-retry physical calibration
completed capture→model→parser→HID→capture in five steps and 77.2 seconds. The
deterministic offline suite passes 184/184. These results are enough to begin using
the system on bounded work and to trust failures as useful evidence.

They are not evidence of a generally reliable autonomous desktop agent. The current
actor is a flat, sequential, medium-horizon loop. It has no harness-controlled
subgoals, no hierarchical memory, no resume, no plan-level recovery, no concurrency
contract, and no demonstrated cross-OS or long-duration reliability. Terminal
verification exists, but direct `run()` calls default it off, it uses the same Holo
model in a separate stateless call, and no one physical run composes the live actor,
HID path, and terminal gate. Those are boundaries, not wording details.

The recommendation is therefore to **stop adding architecture now**. Use the agent on
real, bounded tasks. When a failure escapes, preserve it and add the smallest
deterministic fixture that proves the broken seam. Do not restore the broad desktop
battery as a development gate, and do not build D-d, memory, a second model, or a
framework until repeated evidence names the missing mechanism.

The implementation baseline audited here is `274f1fc` (`fix: parse serving commands
with shell semantics`). Before this report's docs-only commit, local `main` was nine
commits ahead of `gitea/main` (`f7ad67a`). Nothing in this work is pushed. Audit
artifact: `runs/project_state_overview_20260723_203110/audit.txt`.

## 1. What the project is

The north star is a self-hosted computer-use agent that can operate a desktop with
**zero software installed on the target**:

```text
target HDMI ──> capture card ──> host camera buffer ──> local vision model
     ^                                                       │
     │                                                       v
target USB <── Pico 2 W HID <── Pi 5 HTTP/UART bridge <── parsed actions
```

The target sees an ordinary monitor, keyboard, and mouse. Host-side tools may inform
the agent, but target state is established only from captured pixels. Firmware ACKs,
HTTP responses, model prose, and target-network reachability are diagnostic signals,
not target-side truth.

Today the target is one spare Ubuntu/GNOME laptop, not “any desktop” in the empirical
sense. Windows was used previously and its stack is archived. The hardware boundary is
OS-neutral; current focus/reset/launch behavior is GNOME-specific.

## 2. Current live architecture

### Actor and control flow

`agent_loop_holo.py` is the production actor loop and REPL surface. It is 841 lines
and intentionally remains a plain Python module rather than a framework:

1. capture one fresh frame;
2. build a production `HoloSession` request;
3. receive strict structured tool calls;
4. execute a batch sequentially;
5. wait for a post-action frame newer than the HID fire;
6. report camera-derived change magnitude/region to the model;
7. append the turn to session history; and
8. repeat until `finished`, a circuit breaker, or `max_steps`.

The first coordinate-bearing action of a batch is protected by a pre-fire TOCTOU
guard. If the target region changed while the model was thinking, the click is refused
and the model gets a fresh observation. Dropped model steps, execution errors, repeated
clicks, frozen screens, unstable target regions, and rejected terminal claims all have
bounded failure paths.

The module uses process-global `ENV`, `LAST`, `CURSOR`, `PLAN`, and serving state. That
is acceptable for one sequential operator-driven rig; it is not a concurrency or
multi-session architecture. Direct `run()` calls also default to human confirmation
for their first five steps; unattended tools opt out explicitly.

### Model boundary

`kvm_agent.models.base` defines two separate contracts:

- `ModelSession.decide/commit/reset/tool_name` for the stateful actor; and
- `Verifier.check` for a stateless pixel postcondition call.

`kvm_agent.models.holo.HoloSession` is the only actor implementation. It owns the
native Holo prompt, JSON schema, history layout, image trimming, response parsing, and
normalized-coordinate projection. `HoloVerifier` uses its own messages, schema,
temperature 0, and no conversation history.

Both currently call the same Holo3.1-35B-A3B model. The verifier is independent of the
actor's story and context, but not independent in model family or learned biases.

### Capture and projection

`kvm_agent.hardware.env` owns:

- the threaded OpenCV/V4L2 camera;
- monotonic frame sequence numbers;
- dead/stale/stable settle classification;
- tile-based change and target-region metrics;
- the one-frame-to-evidence-PNG/model-JPEG transformation; and
- adoption of the capture card's actual negotiated dimensions.

The current capture and target are 1280×720. `HOLO_MODEL_INPUT_RES=1080` is a maximum
downscale height: `model_input_jpeg` does not upscale a smaller source, so current
physical runs send 1280×720 JPEGs. Coordinates stay sound because the actual frame
dimensions become both the projection basis and the Pi bridge's `set_screen` scale.

### HID appliance

The action channel has three owned layers:

- `kvm_agent.hardware.appliance.ApplianceClient` — host-side HTTP client;
- `appliance/pi5/hid_bridge.py` + `pikvm_proto.py` — Pi 5 HTTP service and CRC-framed
  UART protocol; and
- `appliance/pico_fw/` — RP2350/TinyUSB firmware presenting keyboard and absolute
  mouse collections to the target.

The deployed firmware has a one-second watchdog, all-keys-up clearing, per-command
PONG state, safe retry rules, UART resynchronization pauses, retained mouse-ABS reports
across USB suspend, and visibility for watchdog reboot / USB-suspended state.
Ambiguous wheel delivery is deliberately not retried because wheel movement is
relative and could double-fire.

The camera-verified `verify_hid` gate remains more authoritative than all of those
transport signals. It opens/closes a shell landmark and moves the pointer, then checks
captured pixel changes.

### Serving

The model server is deliberately outside this repository:

- llama-swap/modelctl own lifecycle and configuration;
- this repo owns only a fail-soft serving snapshot/preflight contract;
- Holo runs on the Arc Pro B70;
- `fast-7b` runs on the B580 and can remain co-resident;
- Holo is Q4_K_M with context 64,000, parallel 1, image-min-tokens 1,024,
  q8_0/q4_0 KV cache, split mode `none`, and an mmproj.

The matrix eviction hole is closed, but Holo can still be cold after its TTL. The
latest live probe observed an 11.0-second cold load and 0.1-second immediate warm call,
with `fast-7b` still resident:
`runs/serving_probe_20260723_201921/probe.json`.

### Evidence

`RunRecorder` writes per-run metadata, each pre-decision frame, raw assistant message
including reasoning, parsed action, usage, timing, verification result, and summary.
Model requests also go to a shared `runs/logs/holo_requests.jsonl`; the Pi bridge keeps
a separate appliance-local command log.

The controlled integration tools are stricter than ordinary runs:

- Slice A redirects the exact wire request/response into each case directory; and
- Slice B copies the physical actor's request log into the outer calibration run and
  records setup/final frames, page spec, HTTP access log, actor evidence, and oracle
  classification.

Ordinary `RunRecorder` directories are therefore useful but not yet wholly
self-contained for the exact prompt/tool-output transformation walk required before
blaming the model.

## 3. What is actually proven

### Deterministic offline gate

The complete suite collected and passed 184 tests in 14.51 seconds:
`runs/project_state_overview_20260723_203110/full_pytest.txt`.

Coverage includes:

- actor-loop exits, batching, errors, guards, verification, and fake sessions;
- native message construction, parsing, coordinate projection, and history trimming;
- golden transcript preservation across the model seam;
- capture freshness, settle behavior, and frame-diff metrics;
- appliance clear-HID and Pi protocol retry behavior;
- verifier parsing/fail-closed behavior and archive replay labeling;
- battery/reset/metrics logic, without making the battery a live gate;
- serving parsing/snapshots;
- both controlled-smoke classifiers; and
- documentation-layout law.

This is strong control-flow and transformation coverage. It is not measured line or
branch coverage, and test count alone should not be treated as a quality score.

### Slice A: real-model contract

One live invocation generated four fixed 1280×720 desktop-like frames and passed each
through a fresh production `HoloSession`, one request per case, no retry:

| Case | Required behavior | Observed | Wall time |
|---|---|---|---:|
| `click_target` | click inside a broad visible target | `[640.0, 374.4]`, inside | 33.811s |
| `type_nonce` | type exact visible nonce | `KVM-7319` | 6.187s |
| `complete` | emit `finished` on clear success | `finished` | 8.088s |
| `incomplete` | act and do not finish | valid click, no finish | 6.917s |

The first request paid a cold-load penalty; 6–8 seconds is the relevant warm fixed-frame
path. Saved image/request hashes, raw response, parsed action, and coordinate projection
were cross-checked:
`runs/model_contract_smoke_20260723_161257/summary.json` and
`runs/model_contract_smoke_20260723_161257/inspection.txt`.

This proves the serving/request/schema/parser boundary on four broad contracts. It does
not prove arbitrary grounding quality.

### Slice B: complete physical actor path

One repository-owned calibration page, seed 7319, completed without retries:

- five model steps;
- 77.2 seconds;
- click seed-positioned START;
- re-observe `KVM-0289`;
- focus and type the exact nonce;
- submit;
- re-observe green success; and
- only then call `finished`.

The page exposes no host-side success callback. Captured pixels are the completion
truth, and the `finished` decision's own pre-decision frame had to be green success.
The inspected stage sequence is `start, entry, entry, entry, success`:
`runs/physical_calibration_smoke_20260723_165441/summary.json` and
`runs/physical_calibration_smoke_20260723_165441/post_implementation_inspection.json`.

This proves the single path:

```text
capture → production session → local model → production parser
        → projection → HTTP/UART/USB HID → capture → finished
```

It does not exercise verifier gate mode, arbitrary applications, reset, long idle, or
more than one live seed.

### Verifier and D-c

The verifier has three useful evidence layers:

1. D-a offline replay scored 29/29 human-graded cases (14 positives, 15 negatives) and
   rejected 80/80 adversarial confident false-completion claims:
   `runs/verify_replay_20260723_000637/results.json` and
   `runs/verify_replay_20260723_002007/results.json`.
2. D-b shadow mode judged nine true terminal claims on fresh live frames with 0/9
   false refusals. The ten-task run had no true-fail terminal claim, so live
   false-confirmation remains **unmeasured**, not zero:
   `runs/battery_metrics_20260723_100508/report.json`.
3. D-c offline tests prove that only `satisfied=True` advances; False and None refuse,
   feed evidence back, and abort after three refusals. They also prove fail-closed
   grading and honest incomplete-run denominators:
   `runs/d_c_offline_20260723_104436/pytest.txt`.

D-c is reasonably accepted for the project's present risk on this decomposed evidence.
The honest limits are:

- it checks only `finished`, not intermediate subgoals;
- `run()` defaults to verification off;
- actor and verifier use the same model;
- the physical calibration deliberately used verification off; and
- no physical negative case has demonstrated the live gate refusing a wrong claim and
  then recovering.

### Reset isolation

Warm reboot was rejected as a dependable reset because it can leave the laptop network
adapter offline. A full shutdown/boot restores networking but is manual and still does
not revert files.

The implemented alternative is active-session cleanup for a dedicated evaluation
account:

- only code-owned app/process patterns;
- only allowlisted simple filenames under that account's home;
- named GNOME setting profiles, not task-provided shell;
- ordered union of all task manifests before each task;
- visible HID typing into a terminal;
- camera-verifier confirmation of a clean desktop; and
- fail-closed `KVM_RESET_FAILED` behavior.

One physical run recorded 10/10 reset checks satisfied, including the decisive
post-Pinta cleanup:
`runs/battery_20260723_135007/results.json`. A later union-manifest run passed its
first five reset events before the hour-long battery was intentionally stopped:
`runs/battery_20260723_142910/results.json`.

This is useful task isolation, not a general desktop rollback. It cannot undo arbitrary
file edits, restore application-internal state, or replace a disk image.

## 4. What is not proven

### General task capability

The project has no current statistical capability benchmark. The last broad shadow run
was 10/10 human-graded on ten GNOME tasks, but:

- it was one run on one machine and one desktop state;
- it took about 31 minutes before analysis;
- one task physically completed at the step limit without emitting `finished`;
- all nine judged terminal claims were positives, so no live false confirmation was
  measurable; and
- later reset attempts showed how easily environment state dominates the result.

That run remains useful historical evidence, not a release gate or a population-level
success rate.

### Long-horizon autonomy

The loop is still flat and usually capped around 10–15 steps. Image history is bounded,
but there is no hierarchical task state or external memory. `update_plan` appeared zero
times in D-b's 76 steps and zero times across the prior 19 recorded battery runs. The
harness does not sequence it even if emitted.

`copy_paste_notes` showed the model could finish the physical task and exhaust its
budget before re-observing and declaring completion. That is evidence for eventual
checkpointing, but it is **under-confident correct progress**, not the
confident-wrong intermediate progression D-d was designed to catch. Building D-d now
would still be architecture in search of its failure.

### Unattended hardware duration

The deployed watchdog/retry/suspend changes passed functional checks and the complete
physical calibration. The planned eight-hour idle-plus-periodic-action soak was
postponed. Therefore:

- functional HID confidence is good;
- multi-hour silent-wedge confidence is incomplete;
- long-idle mouse recurrence remains possible; and
- manual Pico replug/full target power-cycle remain recovery tools.

### OS and display breadth

Current evidence covers GNOME at 1280×720 on one display. Multi-monitor absolute
pointing is unverified. Horizontal wheel scrolling is unsupported. Dragging is
host-timed and can inherit HTTP/UART jitter. Windows-era findings are archived and
must be re-opened, not assumed solved, if the target returns to Windows.

### Speed

D-b measured:

- actor median 15.76 seconds per step;
- verifier median 4.88 seconds;
- verified terminal-step combined median 26.6 seconds; and
- actor median 9,663.5 prompt tokens per step.

`parallel=1` serializes actor and verifier. The system is usable for careful bounded
automation, not interactive-speed control.

## 5. Testing methodology going forward

The ordinary development gate is the deterministic offline suite. Live checks are
boundary-triggered:

| Change touches | Required evidence |
|---|---|
| ordinary control flow or utilities | affected offline tests + full offline suite |
| prompt, image preparation, model adapter, parser, history, termination | offline suite + Slice A |
| capture, projection, HID, focus, physical closed loop | offline suite + Slice A + Slice B |
| one application/task capability | one task designed for that exact claim |
| escaped model/harness defect | saved frame + exact prompt + raw output + transformation walk + offline replay, then the smallest regression fixture |

Neither live slice is an every-commit gate. Neither should gain retries, majority vote,
an interactive grader, a scenario registry, a database, resumable campaigns, or a
dashboard. The broad battery remains manual historical tooling. If it again starts
blocking ordinary work, archive it rather than repair it into a second test platform.

## 6. Maintainability assessment

The active tracked surface is not enormous, but it is no longer tiny. Simple line
counts at the audited commit are:

| Area | Tracked lines |
|---|---:|
| `agent_loop_holo.py` + `kvm_agent/` | 3,293 |
| `tools/` | 2,943 |
| `tests/` | 4,385 |
| `appliance/` | 3,546 |

These counts include fixtures/generated firmware headers and indicate scale, not code
quality. Two concentrations deserve attention:

- `agent_loop_holo.py` is 841 lines and `kvm_agent/models/holo.py` is 982 lines. They
  remain understandable because ownership is clear, but cross-cutting features should
  not continue accumulating in the root loop.
- The controlled integration additions total 1,519 lines:
  Slice A tool/tests 637; Slice B driver/page/tests 882. They avoid a second actor loop,
  but their maintenance cost is real.

The right response is not another abstraction layer. It is a strict change budget:

- keep the actor sequential;
- keep one model adapter and one verifier implementation;
- freeze the four-plus-one calibration set;
- keep the battery manual/legacy;
- prefer recorded fixtures over more harness code; and
- archive superseded machinery instead of supporting parallel generations.

The repository's `_archive/` is large (209 tracked files), but active code does not
import it. That is acceptable as historical reference under the write-only archive
rule; it should not become a source of revived components without a new, evidence-led
decision.

## 7. Open findings, in priority order

### A. Evidence locality and working-agreement compliance

This is the clearest bounded maintenance task:

- put the exact production request and tool-output transformation in each run
  directory, not only the shared Holo JSONL;
- decide how the appliance's persistent wire log is correlated/exported into `runs/`;
- prevent ordinary tests and firmware builds from creating hidden cache/build state;
  and
- remove existing ignored caches only in an explicit cleanup after resolving what must
  be preserved.

This improves diagnosis without changing actor behavior. It should be one small branch,
not bundled with D-d or a logging framework.

### B. Verification policy is caller-dependent

`run()` defaults to off for compatibility and requires a verifier injection for gate
mode. That is honest but easy for a caller to overlook. Do not silently change the
low-level function default. When a stable operator entrypoint exists, make its policy
explicit and record `verify_mode` in run metadata.

### C. Deployment drift remains partly external

The Pi bridge/firmware and llama-swap configuration are deployed outside this Git
working tree. Serving snapshots make the model side observable. The appliance side has
health bits and a wire log but no automatic code/firmware revision attestation in each
run. Add revision reporting only if deployment drift causes a real diagnosis problem.

### D. Small known tool limitation

`tools/serving_probe.py --model fast-7b` parses the OpenVINO command correctly but exits
failed because the tool assumes the requested model must have an mmproj. That behavior
is correct for its normal Holo vision preflight; it is not a general text-model health
checker. Leave it alone unless the project needs that broader interface:
`runs/serving_probe_20260723_201900/probe.json`.

### E. Trigger-based hardware work

- overnight soak: run when convenient or on mouse-death recurrence;
- bridge keep-alive: build only if retain/resend still fails;
- power control: build only if manual cold boot repeatedly costs useful work;
- timed drag/multi-monitor: build only for a selected task that needs them.

## 8. Roadmap status

| Phase | Honest state |
|---|---|
| 0 — primitive hardening | code deployed and functionally validated; long soak postponed |
| 1 — model seam | complete |
| 2 — independent verification/subgoals | D-a/D-b/D-c terminal portion complete on decomposed evidence; D-d/subgoal unit not built |
| 3 — hierarchical memory | not started, not justified |
| 4 — oversight dial/macros | not started; blocked on a real subgoal unit and reliability data |
| 5 — multi-model decomposition | serving co-residency ready; grounding/latency/model-fit gates unmeasured |
| 6 — external tools | not started |

Phase numbers should not create momentum by themselves. The next justified work is an
operational evidence period, not Phase 3.

## 9. Recommended operating posture

1. Merge this documentation locally and keep the remote untouched until the operator
   chooses to push the nine existing local commits plus this documentation commit.
2. Use the current system for bounded tasks with deliberate step/time limits.
3. Treat the camera and saved run as truth; inspect `tools/show_reasoning.py` first on a
   failure.
4. If a boundary changes, run only the gate in §5 that covers it.
5. If the laptop needs recovery, use a full shutdown/boot; do not trust warm reboot to
   restore networking.
6. If HID verification fails, stop before acting and replug/power-cycle the Pico rather
   than clicking blind.
7. Keep D-d and all later architecture deferred until repeated, well-preserved evidence
   makes the maintenance cost unavoidable.

No additional rig run is required to begin useful work. No broad battery rerun is
required to merge or trust the current starting point. The most valuable next result is
a real task—or a real escaped failure—not another layer of test infrastructure.
