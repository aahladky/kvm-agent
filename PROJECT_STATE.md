# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-07-23 — Phase 2 slices D-a (the postcondition oracle, RIG-CONFIRMED) and
D-b (shadow wiring + harder tasks + metrics, RIG-CONFIRMED) done; D-c (flip the gates)
is CODE-COMPLETE/OFFLINE-VALIDATED and awaits its rig battery; D-d's gate does not clear.
The serving-layer contract and matrix enrollment are complete.
Supersedes the 2026-07-20 physical-target-move snapshot (git history). Design:
`docs/PLAN_2026-07-20_physical_target_move.md` and, for the phase now in flight,
`docs/PLAN_2026-07-22_phase2_subgoal_verification.md`; latest session:
`docs/SESSION_2026-07-23_phase2_slice_d_b_rig_results.md`._

## 1. What it is

A computer-use agent where **nothing is installed on the target**. A local vision
model sees the target's screen over an HDMI capture card and drives it through a
physical USB-HID injector. The target sees only a monitor + USB mouse/keyboard —
OS-agnostic, undetectable. Pure curiosity project.

## 2. The live system (current iteration)

- **LOOP** — `agent_loop_holo.py`: batched tool calls per step (native semantics:
  calls in a batch see each other's effects; only the batch's final screenshot goes
  back), observe→act with the frame-seq freshness floor (paired via seq numbers). Model: **Holo3.1-35B**
  served locally via **llama-swap** (`http://127.0.0.1:9292/v1`, SYCL llama-server on
  the Arc Pro B70, modelctl-managed). History depth: `HOLO_HISTORY_IMAGES=3`
  (native's max_images) is the standing default — operator decision 2026-07-22
  after the 4/4 battery; the queued history-depth A/B is dropped. The loop talks to
  the model only through `kvm_agent.models.base.ModelSession` (`decide`/`commit`,
  roadmap Phase 1 — `HoloSession` in `kvm_agent/models/holo.py` is the one
  implementation; `run(session=...)` is how a second one would plug in) — see
  Solved §3.
- **HID** — Pi 5 + Pico 2 W **appliance** (`appliance/`): Pico runs `pico_fw/`
  (C/TinyUSB, PiKVM port, CRC16 binary protocol over 3-wire UART); Pi 5 runs
  `hid_bridge.py` (HTTP API, `http://192.168.0.29:8080`). Host client:
  `kvm_agent/hardware/appliance.py`. `clear_hid` (all-keys-up) runs on connect + close.
  Phase 0 hardening (roadmap, Slice B — code landed AND DEPLOYED 2026-07-22/23,
  overnight soak-gate POSTPONED by operator, not run): 1s HW watchdog in
  `main.c`; host-side `_roundtrip` retry (`pikvm_proto.py`); mouse ABS report
  retain+resend on USB suspend; `PONG_WATCHDOG_REBOOTED`/`PONG2_USB_SUSPENDED`
  visibility bits surfaced through `/health` and the wire log. Deployed to the
  live Pico + Pi 5 and functionally verified (`/health` decodes the new fields
  correctly; `agent_loop_holo.boot()`'s camera-verified HID gate passed: "hid ok
  (gnome: kbd diff 49.1, mouse diff 49.1)") — the multi-hour unattended soak
  itself is what's postponed, not the deploy. See Solved §3 and `tools/soak.py`.
- **CAPTURE** — HDMI capture card via cv2 (V4L2 on the Linux host), `Camera` +
  `FrameBuffer` (monotonic frame seq) in `kvm_agent/hardware/env.py`.
- **TARGET** — physical spare laptop (Ubuntu/GNOME as of 2026-07-21; formerly
  Windows 10), lid closed, HDMI out → capture
  card → passthrough to the user's monitor. **The laptop renders at 1280x720; the
  chain (GPU or capture card) upscales to the 1920x1080 the camera delivers** —
  verified live 2026-07-21: the desktop fills the frame, so pixel FRACTIONS are
  consistent end-to-end (projection basis == bridge scale == USB wire fraction)
  and clicks land correctly. Costs are image-quality only (model input and
  evidence frames are upscaled 720p content). Set `SCREEN_W/H=1280x720` in
  `.env.local` for native capture, or set the laptop to 1080p — the measure-then-
  `set_screen` chain keeps the bases locked either way. Power/reset seam:
  `kvm_agent/hardware/target.py` (v1 MANUAL reboot; WoL/smart-plug backend deferred —
  decide with hardware in front of us). Reset strategy: reboot between tasks; disk
  image (Clonezilla) as the determinism backstop. `verify_hid`'s round-trips are
  shell-anchored via `CFG.target_shell` ("gnome" default since 2026-07-21: Super
  tap → Activities, Esc closes; Activities corner click, top-left — "windows"
  keeps win+r/Start for a Windows target); verified live on GNOME 2026-07-21
  (kbd diff 131.0, mouse diff 134.9).
- **EVAL** — human-graded battery: `tools/battery.py` + task JSON
  (`battery_tasks_gnome.json` for the GNOME target; `_shakedown.json` is the Windows
  list). The user grades pass/fail/void per task from the recorded evidence — void
  (infeasible task, note required) leaves the score's denominator but stays visible
  ("4/4 (1 void)"); no automated grade exists and no uncertain grade can masquerade
  as a pass (finding #8). Steps Recorder (psr.exe) was the Windows-only independent
  ground-truth channel — moot on GNOME; the camera is the only evidence channel.
- **EVIDENCE** — every run records per-step frames + raw model output +
  `reasoning_content` to `runs/<tag>_<time>/` (`RunRecorder`). First tool on any
  failed run: `tools/show_reasoning.py`.

### Repo layout (moved here 2026-07-22 from CLAUDE.md, now a pointer per AGENTS.md §6)

Code (~2 MB, tracked):
- `kvm_agent/` — canonical package (config, hardware, instrumentation, llm, models).
- `agent_loop_holo.py` — CURRENT agent loop (see LOOP above). Where new work happens.
- `appliance/`, `tools/`, `tests/`, `docs/` — appliance code (Pi 5 bridge + Pico),
  harnesses (battery, probes), offline unit tests, dated docs.
- `appliance/pico_fw/` — CURRENT Pico firmware (C/TinyUSB, ported from PiKVM
  2026-07-18). The old CircuitPython firmware is RETIRED
  (`_archive/firmware_old/appliance_pico/`) — never deploy.
- `docs/native/` — native Holo format reference (+ `docs/FORMAT_NOTES_holo.md`).
  The prompt template `local-desktop-2026-06-12.j2` in there is a LOAD-BEARING
  runtime asset loaded by `kvm_agent/models/holo.py`, not documentation.
- `_archive/old-stack/` — retired generations, reference only; nothing live
  imports from it.

Data (untracked, gitignored, physically outside the repo since 2026-07-20):
- `runs` → `~/data/kvm-agent/runs` (symlink; evidence — permanent, never moves)
- `scratch` → `~/tmp/kvm-agent-scratch` (symlink; auto-deleted after 14 days —
  promote anything worth keeping into `runs/` or the repo before session end)

## 3. Solved (verified)

- Win32 focus-transfer bug (2026-07-19, click-to-focus retry in `_execute()`).
- WAA server terminal-window leak (patched + re-baked; moot post-WAA).
- Pico HID reliability (PiKVM firmware port; WiFi-Pico path retired).
- Harness trust (2026-07-20): tile-max settle metric, frame-seq before/after pairing
  (finding #6 closed), `clear_hid` wiring.
- Blame ledger: **model 0, our code 3** (`AGENTS.md` §5).
- Review batch-1 fixes (2026-07-21, from the full-scope repo review): bridge
  screen-size sync at env bring-up (`set_screen` — existed on both ends, called by
  neither; closes the silent click-stretch hole), model-call exceptions contained as
  dropped steps (`run()` always finishes the recorder; one API error no longer kills
  a battery), planning-only steps exempt from the frozen-screen abort, `drag_to`
  re-asserts the tracked start before button-down, `jinja2` declared / `requests`
  dropped in `pyproject.toml`. Coverage: `tests/test_agent_loop.py` (offline, 12 checks).
- Review batch-2 fixes (2026-07-21): `wait_until_stable` returns a status
  ("stable"/"timeout"/"dead"); capture stalls/dead-capture windows are surfaced
  into the step's `<tool_output>` and the recorded step's `warnings` instead of a
  swallowed print; `boot()` runs the camera-verified HID gate by default
  (`verify=False` to bypass — the battery keeps its interactive per-task gate), so
  REPL sessions no longer click into a half-dead HID silently. (The "dead" status
  initially covered only the never-delivered case; the wedged-capture case needed
  the seq-aware fix in the second-review round below.)
- Review batch-3 hygiene (2026-07-21): CLAUDE.md pruned to a corrected header +
  trust-ordered pointers (the ~80 KB retired-stack body survives in git history);
  test suite is pytest-collectable while staying script-runnable, with a
  declared `[test]` extra and new holo message-layer coverage
  (`tests/test_holo_messages.py`); the tile-max metric and its threshold have a
  single home (`kvm_agent.hardware.env` + `CFG.frame_change_threshold`);
  `verify_hid` no longer imports the root app script; dead code dropped
  (`_frame_png_full`, `drop_bottom_row`, ASCII-only `stage1_ping_test.py`).
- Review batch-4 fixes (2026-07-21): `Camera` bring-up failure raises catchable
  `RuntimeError` instead of `SystemExit`; `_scalar` shape-guards coordinates (a
  nonsense list raises instead of inventing a midpoint click); `_req` surfaces the
  bridge's HTTP error detail (502/404 bodies) instead of a bare "transport error";
  `show_reasoning.py` speaks the live action vocabulary (hotkey/double_click/
  hold_and_tap, `keys` field) and the batched step-record shape; battery summary is
  foldered (`runs/battery_<ts>/results.json`) and the resolution A/B probe writes
  its results to `runs/`.
- Second-review fixes (2026-07-21, all 12 WRONG items; suite now 53 tests).
  Evidence: `summary.json` action lists read the batched step shape; the evidence
  PNG and model-input JPEG derive from ONE buffer read and the system prompt
  travels in each run's `meta.json`; battery scoring is fail-closed over ALL tasks
  (`total_tasks`/`graded`/`complete`). Feedback/robustness: exec-error steps count
  against `STUCK_LIMIT` (the abort was dead code) and their error `<tool_output>`
  reaches the model; `wait_until_stable` is seq-aware (a wedged capture reports
  "dead", not "stable"); `combo()` fails closed on any unknown key; unsupported /
  no-op actions report `NOT executed`; `type()`'s HTTP timeout scales with text
  length; screen size is measured from the actual frame after bring-up (projection
  AND the `set_screen` push); the freshness floor starts AFTER the HID fire;
  `CURSOR`/`PLAN` reset per run; `repeat_count` clamped; `HOLO_HISTORY_IMAGES=0`
  refused; `finish_reason='length'` logged. Firmware: `ph_usb_send_clear` no
  longer injects a phantom wheel scroll (upstream PiKVM bug) — Pico reflash and
  the Pi 5 `pikvm_proto.py` deploy CONFIRMED done before the 2026-07-21 23:51
  battery (operator, 2026-07-22).
- **First honest baseline: COMPLETE** (2026-07-21 23:51 battery, graded to the end).
  Honest score **4/4 (1 void)** — recorded 5/5 pre-void-grade; paint_line was
  infeasible (no paint app on the GNOME target) and force-graded "pass" under the
  p/f-only vocabulary, now fixed with the void grade (`tools/battery.py`).
  calc_multiply: clean 6-step pass vs 0/20 the previous morning (OS switch + fix
  rounds landed together — uncontrolled). Run config: GNOME target, native 720p,
  `HOLO_HISTORY_IMAGES=3`. Evidence: `runs/battery_20260721_235153/`; full review
  in `docs/SESSION_2026-07-22_first_complete_battery.md`.
- **Roadmap Phase 0, firmware hardening (Slice B, 2026-07-22/23, code landed AND
  DEPLOYED, overnight soak gate POSTPONED by operator):** HW watchdog
  (`watchdog_enable(1000, true)` in `main.c`,
  gated pet, `watchdog_enable_caused_reboot()` read before re-arming — verified
  it and the mode-change reboot use scratch[4], `ph_outputs.c`'s mode persistence
  uses scratch[0], no collision) surfaced as new PONG bit
  `PH_PROTO_PONG_WATCHDOG_REBOOTED` (0x20, the only free bit in resp[1]). Host
  `pikvm_proto.PicoHidLink._roundtrip` retries: NACK (well-framed rejection) retries
  ANY command; an ambiguous (no/garbled) response retries only idempotent commands
  {PING, CLEAR_HID, KBD_KEY, MOUSE_ABS, MOUSE_BUTTON}, never MOUSE_WHEEL (relative
  delta); 150ms pre-retry pause doubles as the firmware's 100ms UART resync
  trigger; `retries` count in every response + the wire log. Mouse ABS
  retain+resend (see Open Problems' long-idle mouse-death entry) mirrors kbd's
  existing pattern; `PH_PROTO_PONG2_USB_SUSPENDED` (resp[4], previously always-zero
  padding) exposes `tud_suspended()` for visibility. `tools/soak.py` (new): the
  Phase-0 gate harness — probe every 10s, corner-move + camera-liveness check every
  5min, JSONL to `runs/soak_<ts>/`, operator-driven fault injection. Firmware
  compiles clean (`-Wall -Wextra`, both `cmake` and the real `make` deploy path).
  Tests 71 → 79 green (`tests/test_pikvm_proto_retry.py`, fake serial).
  **DEPLOYED 2026-07-23**: Pico BOOTSEL-flashed (operator), Pi 5
  `pikvm_proto.py`/`hid_bridge.py` updated (backed up first) + `hid-bridge.service`
  restarted; `/health` decodes the new fields correctly
  (`watchdog_rebooted=0 usb_suspended=0` on the fresh flash, as expected —
  a power-on reset, not a watchdog reset); `agent_loop_holo.boot()`'s
  camera-verified HID gate PASSED ("hid ok (gnome: kbd diff 49.1, mouse diff
  49.1)"). The overnight soak itself (`tools/soak.py --hours 8`) is POSTPONED,
  operator decision — the target needs to sit occupied/semi-attended that long
  for fault injection, and the bug it guards (long-idle mouse death) is a minor
  inconvenience, not urgent. Not abandoned, just not run yet. Evidence:
  `docs/SESSION_2026-07-22_slice_b_firmware_hardening.md` ("Deploy" +
  "Soak: POSTPONED" sections; no `runs/` evidence — the deploy checks were
  one-shot health/gate calls, not a recorded run).
- **Roadmap Phase 1, the model seam (Slice C, 2026-07-22, pure refactor, no rig
  time):** `kvm_agent/models/base.py` (`StepDecision`, `ModelSession` Protocol —
  `decide`/`commit`/`tool_name`/`reset`, deliberately not three propose/ground/
  verify methods yet); `HoloSession` in `kvm_agent/models/holo.py` owns history,
  `<observation>`/`<tool_output>` construction, image trim, and the
  action-kind→native-tool-name map (`ACTION_TO_TOOL_NAME`, out of the loop, where
  it was an inline dict). `agent_loop_holo.run()` gained `session=` (default a
  fresh `HoloSession`) so a second `ModelSession` can drive it untouched — proven
  by a test that hands `run()` a non-Holo stub and asserts `call_holo_full` is
  never called. Proved a pure refactor via a golden-transcript fixture (a scripted
  6-step/7-tool-call scenario run against the pre-refactor code, history
  byte-identical post-refactor). Tests 71 → 78 green. Evidence:
  `docs/SESSION_2026-07-22_model_seam_slice_c.md` (no `runs/` evidence — offline
  only, no hardware touched).
- **Roadmap Phase 2, slice D-a — the postcondition oracle (2026-07-23, offline only,
  no rig time):** the first automated verification anywhere in this project.
  `kvm_agent/models/base.py` gains `Verdict` + a `Verifier` Protocol
  (`check(data_url, w, h, question, claim="")`) — deliberately a SEPARATE Protocol from
  `ModelSession`, not the `verify()` method that file's docstring used to promise, because
  statelessness is the whole property (a verify() on the object that owns conversation
  history would end up judging its actor's story instead of the pixels) and Phase 5
  relocates a separately-injected object by swapping one constructor argument.
  `kvm_agent/models/holo.py` gains `HoloVerifier` + `call_holo_verify`: same model id on
  the same llama-swap endpoint, but temperature 0.0, thinking off, and **its own message
  list** — NOT routed through `build_messages`/`call_holo_full`, whose hardcoded
  `SYSTEM_PROMPT`/`RESPONSE_SCHEMA` (`tool_calls: minItems 1`) would force the oracle to
  emit a desktop action. The actor path is byte-untouched, so the golden-transcript test
  still passes unchanged. `satisfied` is `bool | None`: None (model error, timeout,
  unparseable) is a third outcome, never a False and never a True — finding #8's
  fail-closed rule applied to the oracle itself.
  **Measured offline against the graded archive** (`tools/verify_replay.py`, new):
  **29/29 on human-graded cases — 14/14 positives (false-refusal rate 0.0, the number
  gating slice D-c) and 15/15 negatives (0 false confirmations)**, plus 64/65 on ungraded
  failed runs; 0 unanswered; median 4.2s and ~1.4k prompt tokens per check (~1/3 of an
  actor step). **Claim-resistance 80/80, 0 false confirmations**
  (`runs/verify_replay_20260723_002007/`, `--cases adversarial`): every unfinished-screen
  frame re-run with a confident FALSE "task complete" claim attached, because otherwise
  the eval is confounded (every positive carried a claim, every negative carried none) and
  the case that gates D-c — unfinished screen + confident claim — goes untested. The
  oracle does not fold to a confident lie.
  Two findings: three apparent misses were the LABEL being wrong (an observation task's
  postcondition already holds at `step_00` — now excluded with the reason recorded), and
  the one real miss (`small_target_tray`) confirmed *the target exists* rather than *the
  action's effect* on an action-phrased task ("click the WiFi icon") **with no claim
  attached** — the same case answered correctly once a claim was present ("no icon that
  has been clicked or activated"). So the failure mode is specifically action-phrased
  postcondition + claimless check, which is exactly how a SUBGOAL check runs (no `answer`
  text at a subgoal boundary). D-b's new tasks must be phrased as END STATES, and D-d must
  *reject or rewrite* action-phrased subgoal postconditions at harvest — native's prompt
  asks for verb-first goal titles, so action phrasing is the default output, not an edge
  case. A test also caught a real contract violation: `call_holo_verify` built its client
  outside its own try/except, so a client-construction failure propagated instead of
  becoming `satisfied=None` (fixed; `_target_config` stays outside — a bad target is a
  caller bug, not a model-side failure).
  **Honest limit**: no archived run has a false `finished` claim, so the negatives measure
  unfinished-screen recognition, NOT a true false-confirmation rate — that needs D-b.
  Tests 86 → 116 green. Evidence: `runs/verify_replay_20260723_000637/results.json`
  (and the pre-fix run `runs/verify_replay_20260722_235815/`),
  `docs/SESSION_2026-07-23_phase2_slice_d_a_verifier.md`.
- **Roadmap Phase 2, slice D-b — shadow wiring + harder tasks + metrics (2026-07-23,
  RIG-CONFIRMED):** `agent_loop_holo.run()` gains
  `verifier=`/`verify_mode=` (`"off"`|`"shadow"`|`"gate"`). `"off"` (the default) is
  provably byte-identical to pre-D-b `run()` — all six exit points now share one
  `_result()` closure, and in `"off"` mode the return dict has exactly the original two
  keys, never a third; the four pre-existing exact-dict-equality tests needed zero
  changes. `"shadow"` verifies the model's own `finished` claim against the same `after`
  frame the batch loop already captured (no extra capture), encoded via the new
  `kvm_agent.hardware.env.png_to_model_input_jpeg` (single home, also now used by
  `tools/verify_replay.py`) — same client-side encoding the actor's own input takes, so a
  live verdict is comparable to D-a's offline numbers. Records the verdict
  (`RunRecorder`'s new `verifications`/`verified_finish`, `run()`'s own
  `verified_finish` return key) but changes NOTHING about control flow. `"gate"`
  (slice D-c) is rejected loudly (`NotImplementedError`), never silently treated as
  shadow. A raising verifier is absorbed into `satisfied=None`, same P0-2 reasoning as
  the `session.decide()` guard. `tools/battery.py` gains `[verify_mode]` on its CLI and
  `auto_grade`/`auto_evidence` columns alongside (never replacing) the human grade,
  fail-closed the same way `grade_task` is. Four new end-state-phrased battery tasks
  (`file_create_rename`, `dark_mode_confirm`, `clock_to_file`, `copy_paste_notes`,
  ~8-15 steps each) give the battery headroom above its 5/5 ceiling — every one phrased
  as an end state per D-a's `small_target_tray` lesson. New `tools/battery_metrics.py`
  computes every roadmap §5 metric except grounding rate: completion rate,
  steps-to-completion, false-"finished" rate, verifier agreement/false-refusal/
  false-confirmation, guard-refusal rate, actor-vs-verify latency (holo3.1's
  `--parallel 1` means these serialize, not overlap), honest-refusal-vs-budget-
  exhaustion. Cross-validated: its guard-refusal count matches the TOCTOU
  rig-confirmation session's hand count exactly (4/64 steps once the one pre-guard
  battery is excluded); a real archived frame pushed through the live encode path
  returned the same verdict D-a's replay already scored for it. Found and fixed a real
  gap in `--all` mode: several pre-2026-07-21 `battery_<ts>/` dirs predate `results.json`
  and were silently contributing zero rows under a misleadingly "analyzed" label — now
  explicitly skipped and reported. Tests 131 → 157 green.
  **Rig session run 2026-07-23** (`runs/battery_20260723_093442/`,
  `runs/battery_metrics_20260723_100508/report.json`): extended 10-task battery,
  `verify_mode="shadow"` — 10/10 human-graded pass, false-"finished" 0/9.
  **False-refusal 0/9 on live frames: D-c's hard gate clears, D-c is a legitimate go.**
  Verifier-vs-human agreement is 100% (9/9) only over the 9 runs the verifier actually
  judged; the 10th (`copy_paste_notes`) never claimed `finished`, so produced no verdict,
  yet was human-graded pass — a real auto/human divergence (9/10 counted honestly) that
  D-c's planned fail-closed auto-grading would surface. False-confirmation stays
  unmeasured (zero true-fail cases to test against), which is why D-c's own design keeps
  every auto/human disagreement plus a sampled fraction of agreements human-checked.
  Guard-refusal rate 8/76 steps (10.5%). `update_plan`: 0/76 occurrences (0/19 in the
  pre-existing archive) — **settles D-d's mechanism (explicit planner call, not
  native-schema harvest) regardless of its gate.** **D-d's own gate — "headroom, not
  another clean sweep," at least one caught confident-wrong-progress case — is NOT met**:
  graded-level this battery is another 10/10 sweep. `copy_paste_notes` (`max_steps
  reached` at 15, human-graded pass) shows the model completed the actual terminal action
  with no budget left to screenshot-and-declare — real evidence for subgoal-level
  checkpointing, but *under-confident correct* progress, the mirror of the
  *confident-wrong* case D-d's gate requires. **D-c is unblocked and next; D-d needs a
  battery that actually produces its target failure mode.** Evidence:
  `docs/SESSION_2026-07-23_phase2_slice_d_b_rig_results.md`.
- **Roadmap Phase 2, slice D-c — terminal gate + automated grading (2026-07-23,
  CODE-COMPLETE/OFFLINE-VALIDATED, RIG PENDING):** `verify_mode="gate"` accepts only
  `satisfied=True`; False and None refuse the model's `finished` claim, thread the
  oracle evidence back through `<tool_output>`, and continue. Three refusals terminate
  failed with `answer refused by verifier x3`. `tools/battery.py` now defaults to gate
  mode and verifier-primary grading; missing/unanswered verdicts fail, human grading is
  retained behind `--human`, disagreements plus 10% of agreements are spot-checked,
  and `--no-reboot` is a genuinely no-prompt state-carryover mode. Metrics use only the
  human ground-truth sample for false-confirmation/refusal and preserve incomplete
  batteries' full denominator. 165 tests pass. Evidence:
  `runs/d_c_offline_20260723_104436/pytest.txt`,
  `docs/SESSION_2026-07-23_phase2_slice_d_c_gates.md`.
- **GNOME evaluation-session reset (2026-07-23, CODE-COMPLETE/OFFLINE-VALIDATED,
  PHYSICAL SMOKE PENDING):** the warm-reboot path is unsuitable on this laptop because
  it can leave the network adapter offline, while a full shutdown/boot is manual and
  still preserves files. Battery tasks now carry an allowlisted reset manifest: simple
  filenames directly under a dedicated eval account's `$HOME`, plus named GNOME-setting
  profiles implemented in code (task JSON cannot inject shell). `--reset-strategy`
  selects `manual-power-cycle` (still default), `cleanup`, `cleanup-logout`, or `none`.
  Cleanup is typed visibly through physical HID; success closes/logs out, failure leaves
  `KVM_RESET_FAILED` visible, and the operator confirms before the camera-verified HID
  gate. Battery `results.json` now records verify/grader/sample/reset configuration.
  169 tests pass. Evidence: `runs/session_reset_offline_20260723_113316/pytest.txt`,
  `docs/SESSION_2026-07-23_gnome_session_reset.md`.
- **Decide-act TOCTOU staleness — RIG-CONFIRMED 2026-07-22** (two apples-to-apples
  GNOME battery reruns, `runs/battery_20260722_173742/` 5/5 and
  `runs/battery_20260722_222137/` 5/5 (1 void)): the pre-fire target-tile guard
  (landed as part of the roadmap-alignment session, `docs/SESSION_2026-07-22_
  roadmap_alignment.md`) fired 4 times across ~64 steps in the two runs
  (`runs/battery_editor_save_file_20260722_222710/step_04.json`: region tile
  diff 70.5 at top-right, refused a stale hamburger-menu click, re-observed,
  task still passed; 2 similar refusals in `runs/battery_paint_line_20260722_
  223124/`; 1 in `runs/battery_text_editor_type_20260722_173847/`) — every
  refusal isolated (never 3-in-a-row, `GUARD_REFUSE_LIMIT` never hit), no task
  failure attributable to the guard. paint_line voided again
  (`runs/battery_paint_line_20260722_223124/`, operator: "confusing app ui
  relying on nonstandard icons without labels") — a genuine Pinta-UI-legibility
  issue, NOT a guard misfire (its two guard refusals were both legitimate
  mid-animation catches, confirmed by eye). §7 item 0's roadmap gate closes.
  Evidence: `docs/SESSION_2026-07-22_toctou_guard_rig_confirmation.md`.

- **The serving layer has a contract (2026-07-23, offline + live probe, no rig):** the
  model server lives OUTSIDE this repo (llama-swap + `modelctl`,
  `~/services/llama-swap/config.yaml`) and nothing here had ever inspected it. It is
  deliberately NOT adopted — it serves 16 models for unrelated purposes — but it now has
  a seam. `kvm_agent/llm/serving.py`: `parse_serving_cmd` (pure) + `serving_snapshot`
  (reachable / configured / resident / params / co_resident), fail-soft by contract (a
  probe that raises is a new way to kill a run). `boot(serving_check=True)` records and
  **warns** (it does not raise — unlike the HID gate, every serving fault announces
  itself at the first model call); `run()` re-snapshots PER RUN and writes it to
  `meta.json` under `serving`, so a mid-battery eviction is visible after the fact.
  `tools/serving_probe.py` is the fail-closed preflight — `verify_hid`'s analogue one
  layer up (the config says what the server WOULD launch; `/running` says what it IS
  running) — hard-failing only on unreachable / not-configured / **resident vision model
  with no mmproj** (which answers fluently from text alone, so it reads as "the model got
  bad at grounding": the most expensive misdiagnosis available here, AGENTS.md §2).
  Measured live: cold load 12.7s vs warm 0.1s; ctx 64000, `--image-min-tokens 1024`,
  cache k/v q8_0/q4_0, quant Q4_K_M, `--parallel 1` (no request concurrency, so a
  Phase-2 verify serializes behind the actor call). Tests 116 → 131, and the suite is
  proven endpoint-independent (`HOLO_LOCAL_URL=http://127.0.0.1:1/v1` → 131 passed, same
  runtime). Evidence: `runs/serving_probe_20260723_075311/probe.json`,
  `docs/SESSION_2026-07-23_serving_contract.md`.
  **The eviction hole this found is CLOSED (2026-07-23, operator-applied):** holo3.1 was
  absent from llama-swap's `matrix:` — the one step `modelctl` deliberately leaves to a
  human, never done after `modelctl pull` — so any other consumer of the box evicted it
  mid-run at a ~13-17s reload that lands as LATENCY, never an error (never actually
  observed in a real run: every >median+12s step in the archive is step 0, a cold load).
  Three lines added (`vars: holo`, `evict_costs: holo: 1`, `sets: holo_stack: holo & f7`),
  per `docs/PLAN_2026-07-23_serving_matrix_enrollment.md`. **The edit alone did nothing** —
  llama-swap runs without `--watch-config`, so the two-day-old process still held the old
  config and a behavioural re-test showed the eviction unchanged; reading the file said
  "fixed", only the test said otherwise. After a unit reload, both models stay resident in
  both directions (`['fast-7b', 'holo3.1']`). This also satisfies Phase 5's co-residency
  prerequisite, since holo3.1 runs `--split-mode none` (B70 alone) and so fits the
  existing `X & f7` pattern. Evidence: `runs/serving_probe_20260723_084700/probe.json`.

## 4. Open problems

- ~~holo3.1 absent from llama-swap's `matrix:`, evictable mid-run~~ — **CLOSED
  2026-07-23** (see Solved §3's serving entry).

- **Review follow-ups (2026-07-23):** ~~incomplete batteries could report 1/1~~ —
  **FIXED WITH D-c**: metrics retain `total_tasks`/`graded`/`complete`. Remaining:
  `parse_serving_cmd` removes every backslash and whitespace-splits a shell command;
  replace that lossy parser with shell-aware tokenization before a quoted/escaped model
  path makes the serving snapshot lie. Full review:
  `docs/REPORT_2026-07-23_codebase_review.md`.

- **Tool-result signal is semantically misleading**: changed/unchanged binary
  confirmed real-but-irrelevant pixels (taskbar focus visuals) as action success
  at decision-critical steps (2026-07-21). **Partial fix landed 2026-07-22** with
  the guard: tool_output now reports localized-vs-widespread + changed-tile count
  ("41/144 tiles, strongest top-left") — magnitude and spread, still not a
  correctness oracle (that stays Phase 2 of the roadmap).
- **Post-reboot half-dead HID recurs** (I2 class, physical): gate exists
  (`target.verify_hid` + replug loop in battery); automate with the power backend.
- **Long-idle mouse death needs a manual Pico replug** (operator, 2026-07-22
  post-rerun). Firmware diagnosis (same day): the suspend paths are asymmetric —
  kbd (`ph_usb.c:222-230`) requests remote wakeup and KEEPS the report pending
  for re-send after resume, but the mouse macro (`ph_usb.c:235`) does
  `tud_remote_wakeup(); _MOUSE_CLEAR; return;` — the event is DROPPED while the
  UART still PONGs OK (delivered-to-wire ≠ delivered-to-host, the exact lie the
  camera principle exists for). Remote wakeup IS advertised in the config
  descriptor (`ph_usb.c:360`), but if the target OS never enabled it on the
  device, `tud_remote_wakeup()` is a silent no-op and only a replug (re-enumerate)
  revives the mouse. Inherited upstream PiKVM behavior. **Fix (a) LANDED AND
  DEPLOYED 2026-07-22/23** (Slice B, `docs/SESSION_2026-07-22_slice_b_firmware_
  hardening.md`): the mouse ABS report path now retains+retries like the kbd
  path (`_mouse_abs_try_send`, `ph_usb.c`) instead of dropping on suspend; REL
  mode is untouched (not in this project's live deployment). **Fix (b) LANDED
  AND DEPLOYED as visibility-only**: `tud_suspended()` exposed as a new PONG2
  byte (`PH_PROTO_PONG2_USB_SUSPENDED`, resp[4] — previously always-zero
  padding), decoded host-side and confirmed live in `/health`; active
  refuse-into-a-suspended-bus behavior was NOT added (bigger behavior change,
  unproven need). **Fix (c) (bridge-side keep-alive) DEFERRED** — speculative,
  build only if the bug recurs after (a)+(b). Both deployed fixes passed the
  camera-verified HID gate post-deploy (2026-07-23), but the actual long-idle
  window they target is UNTESTED — the `tools/soak.py` overnight gate that would
  exercise it is POSTPONED (operator decision: the inconvenience it guards
  against, a manual replug, doesn't currently justify tying up the rig for
  8+ hours). Watch for recurrence on ordinary runs in the meantime; if it
  recurs, that's evidence fix (c) is needed even without a soak.
- Windows-era items, moot while the target is GNOME (re-open on a Windows target):
  ~70s OS dead window post-reboot (psr.exe zip outstanding), windows_calc class
  (WinUI3 date-picker + stuck-popup), Store auto-update pause expiry.
- Deferred: power-control backend; bridge-side suspend keep-alive (mouse-death
  fix candidate (c) above, pending soak evidence it's actually needed).
- Cleared from Deferred (the list had gone stale; corrected 2026-07-23):
  - ~~superseded adoption (structured-output rearchitecture + resolution sync)~~ —
    **both landed 2026-07-21, hours after this line was written that morning**
    (08:04). Structured output is the live contract: `RESPONSE_SCHEMA` +
    `response_format=json_schema` (strict) since `9a98d96` 09:30, the
    native-verbatim rearchitecture; `parse_response` normalizes that shape and
    nothing else. Resolution sync is the `set_screen` push at env bring-up,
    `9d01363` 12:59, hardened in `ded07a0` 18:00 (screen size measured from the
    actual frame, projection + bridge push). Both are recorded as done in §3
    (native-verbatim LOOP entry; review batch-1 and second-review fixes) — only
    this line disagreed.
  - ~~automated fail-closed vision grading~~ — the oracle exists and is
    offline-validated (§3, slice D-a). It does not yet GRADE anything: the battery
    is still human-graded, and the flip to `grader: "verifier"` is slice D-c, gated
    on D-b's live false-refusal rate
    (`docs/PLAN_2026-07-22_phase2_subgoal_verification.md`).

## 5. Retired

2026-07-20 sweep: EvoCUA/UI-TARS/B580-planner stack, orchestration, battery-v1,
hindsight, Ollama verifier, WiFi Pico, CircuitPython firmware, rig/preflight.
2026-07-20 physical move: **libvirt VM stack (`vm.py`, win11-agent),
WindowsAgentArena (`waa/`), the EvoCUA pyautogui exec-shim, `wol.py`,
`shakedown_ab.py`, `appliance/pico/` + `send.py` + `stage2_verify.py`** — all in
`_archive/`. Nothing live imports from `_archive/`.

## 6. House rules

`AGENTS.md` is law for every agent: all artifacts in `runs/`, nothing in hidden
dirs, the model is the last suspect, no ghost generations, sessions end
commit-or-revert with this file updated.
