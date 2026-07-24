# Project State — kvm-agent

_Current snapshot: 2026-07-23._

## Purpose

`kvm-agent` is a local computer-use agent that controls an unmodified target through
the same two interfaces a person uses:

```text
HDMI capture card → host vision model → normalized action
                                      → Pi 5 HTTP bridge → Pico 2 W USB HID → target
```

Nothing is installed on the target. The target sees a monitor, keyboard, and mouse.
The current target is an Ubuntu/GNOME laptop at 1280×720. The actor and visual
postcondition verifier currently use Holo 3.1 through the local OpenAI-compatible
endpoint.

## Current operating path

1. `agent_loop_holo.boot()` opens capture and the Pi/Pico action channel, synchronizes
   the real captured resolution to the bridge, checks serving configuration, and
   camera-verifies keyboard and mouse delivery.
2. `run()` captures one frame, asks a `ModelSession` for one batched decision, parses
   the structured response, applies the pre-fire screen-change guard, sends normalized
   actions over HID, captures a newer settled frame, and returns exact tool results to
   the model.
3. `verify_mode="gate"` independently checks a terminal `finished` claim against the
   post-action frame. False or unanswered claims are refused and returned to the actor;
   three refusals fail loudly. Direct `run()` calls default to verification off for
   compatibility. `tools/battery.py` defaults to gate mode.
4. Every recorded run is self-contained under `runs/<tag>_<timestamp>/`: decision
   evidence frames, the exact content-addressed JPEGs sent to actor/verifier, raw
   assistant turns, parsed actions, tool-output text, host-observed HTTP/Pico responses,
   model request/response records, verifier verdicts, timings, tokens, configuration,
   and summary.

The loop remains flat: there is no independently verified subgoal state, hierarchical
memory, recovery manager, or macro execution.

## Code map

| Path | Current responsibility |
|---|---|
| `agent_loop_holo.py` | Production capture → decide → guard → HID → settle → terminal-verify loop and REPL helpers. |
| `kvm_agent/models/base.py` | Model-neutral `ModelSession`, `StepDecision`, `Verifier`, and `Verdict` contracts. |
| `kvm_agent/models/holo.py` | Holo prompt/schema, request construction, response parsing, coordinate projection, conversation history, request logging, and stateless visual verifier. |
| `kvm_agent/hardware/env.py` | Fresh frame buffering, camera lifecycle, image encoding, settle/freshness checks, and environment bring-up. |
| `kvm_agent/hardware/appliance.py` | Host HTTP client for the HID appliance; fails loudly and retains every bridge response for the owning run. |
| `kvm_agent/hardware/target.py` | Camera-verified HID gate and allowlisted GNOME evaluation-session cleanup. |
| `kvm_agent/instrumentation/run_log.py` | Self-contained per-run evidence writer. |
| `kvm_agent/llm/serving.py` | Read-only serving/matrix inspection and shell-aware launch-command parsing. |
| `appliance/pi5/` | Pi 5 HTTP-to-UART bridge, binary Pico protocol, and systemd unit. |
| `appliance/pico_fw/` | Current RP2350 USB-HID firmware and runs-local build. |
| `tools/model_contract_smoke.py` | Four fixed-frame live model/request/parser contracts. |
| `tools/physical_calibration_smoke.py` | One deterministic physical capture→model→HID→capture calibration. |
| `tools/battery.py` | Explicit real-task runner with reset isolation and terminal verification; diagnostic, not a release benchmark. |
| `tools/run_tests.py` | Canonical cache-free deterministic test runner; retains output under `runs/`. |

Retired implementations live in `_archive/` and are never imported by live code.

## What is proven

- The deterministic offline suite covers the loop, model seam, parser/history,
  verifier failure behavior, reset isolation, metrics, capture freshness, HID protocol,
  serving inspection, evidence layout, and documentation layout: **192/192 pass**
  (`runs/offline_tests_20260723_215947/pytest.txt`).
- The live-model contract smoke passed all four fixed-frame cases.
- The physical calibration completed one bounded five-step capture→model→HID→capture
  flow with a deterministic visual oracle.
- D-a/D-b/D-c are implemented: a stateless postcondition verifier, live shadow
  measurement, and terminal claim gating.
- In the ten-task D-b shadow run, human grading was 10/10 and live verifier
  false-refusal was 0/9. The tenth task physically completed at its step limit without
  claiming completion.
- The camera-verified HID gate passed after the current watchdog, retry, and suspend
  firmware changes were deployed.
- Run-local model/tool/HID evidence and cache-free test/build paths are implemented.
  The updated bridge/service is deployed; its health and runs-local daemon log are
  recorded in `runs/hid_bridge_deploy_20260723_215502/`.

These are integration and boundary claims, not a statistical general-capability claim.

## Important limits

- Actor and verifier use the same model.
- Terminal verification is opt-in on direct `run()` calls and checks only `finished`.
- No live negative run has measured verifier false-confirmation or demonstrated a
  wrong terminal claim being refused and then recovered physically.
- The long-idle HID soak is postponed. Functional delivery is validated; multi-hour
  silent-wedge confidence is not.
- Warm reboot is unsuitable on this laptop because networking can remain offline.
  The battery uses allowlisted active-session cleanup or an explicit manual power cycle.
- Timed drag and multi-monitor absolute pointing are not implemented.
- Real application reliability is intentionally unscored until a selected task needs a
  concrete capability claim.

## D-d: bounded evidence search, then build

D-d adds an explicit planner call, one active subgoal, verifier-gated subgoal
transitions, per-subgoal budgets/counters, and active-subgoal instruction scoping inside
the existing loop. Native `update_plan` harvesting is rejected: it appeared 0/76 times
in the D-b run and 0 times across the earlier recorded battery runs.

Development is no longer blocked on waiting indefinitely for the actor to fail. The
baseline search is capped at three single-task runs, one pass per task, stopping after
the first qualifying case:

```bash
python tools/battery.py tools/battery_tasks_d_d_trigger.json \
  --task-id rename_commit_chain --reset-strategy cleanup
python tools/battery.py tools/battery_tasks_d_d_trigger.json \
  --task-id clipboard_exact_chain --reset-strategy cleanup
python tools/battery.py tools/battery_tasks_d_d_trigger.json \
  --task-id calculator_transfer_chain --reset-strategy cleanup
```

Each task has a 12-step ceiling and contains several commit boundaries:

| Task | Why it is high-yield |
|---|---|
| Rename/save/reopen chain | Save-dialog completion, rename commit, old-name removal, and reopen state can each look locally complete before the filesystem postcondition is true. |
| Exact clipboard chain | Selection, copy, document switch, paste, and save can visibly advance while an earlier clipboard/selection assumption is wrong. |
| Calculator transfer chain | A correct transient calculation must survive app switching, exact transcription, save, and reopen; later progress can conceal an earlier read/save error. |

A qualifying confident-wrong transition requires all four:

1. the actor explicitly says or behaves as though an intermediate checkpoint is done;
2. that decision frame visibly contradicts the checkpoint postcondition;
3. the actor advances to a later part of the task instead of correcting it; and
4. terminal gating has not already handled the error.

If none of the three tasks produces that pattern, D-d still proceeds as an experimental
A/B. The existing under-confident completion-at-budget case and the architectural lack
of a checkpoint/recovery unit are sufficient operator-approved motivation; a model that
does not fail on demand is not a reason to spend hours blocking development. Acceptance
then emphasizes non-regression, transparent subgoal evidence, and a removable fallback
to gate-only scoping rather than claiming a measured completion-rate uplift.

## Current next actions

1. Optionally run the three D-d trigger tasks one at a time under the cap above.
2. Implement D-d inside `agent_loop_holo.py`; do not create another loop generation.

## Documentation and evidence

- `AGENTS.md` — binding working agreement and Blame Ledger.
- `PROJECT_STATE.md` — current system truth and code map.
- `docs/ROADMAP.md` — direction, gates, and sequencing.
- `docs/ROADMAP.md` — current D-d design and integration-test contract.
- `docs/native/` — recovered native Holo prompt/runtime inputs used by current code.
- `_archive/docs_history/` — approved/completed plans, findings, reports, and session
  narratives; historical only.
- `runs/` — permanent executable evidence. Test output belongs here.
