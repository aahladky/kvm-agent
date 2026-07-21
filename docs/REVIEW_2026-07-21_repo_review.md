# Repo Review — 2026-07-21

Full-scope review of the live tree at `8c7d5cd` (`main`): code correctness with a
silent-failure focus, tests/tools, appliance (Pi 5 bridge + `pico_fw`), hygiene,
docs, and packaging. Review only — no fixes in this diff. Every claim carries a
file:line ref; all P0/P1 claims were re-verified by hand against the tree, not
taken from a single read.

**Verdict up front:** the live stack is coherent and internally consistent. The
Pi5 bridge ⇄ host client ⇄ Pico firmware protocol cross-checks clean end to end,
nothing live imports `_archive/`, the tree is small and tidy. The real issues
cluster exactly where AGENTS.md §2 predicts they would: silent failures around
the capture→verify path, one unguarded model-call path that can kill a whole
battery, config sprawl that contradicts `config.py`'s own charter, an untested
core, and a CLAUDE.md that is ~80 KB of history contradicting its own header.

---

## P0 — silent failures and run-killers

These are the "looks like success / dies without a verdict" class this project
has been burned by repeatedly.

### 1. `set_screen` exists on both ends and is called by neither
`ApplianceClient.set_screen` (`kvm_agent/hardware/appliance.py:92-99`) and the
bridge route (`appliance/pi5/hid_bridge.py:126,166`) are both live, but **no live
code ever calls it** — not `boot()` (`agent_loop_holo.py:86-92`), not
`PicoEnv.__init__` (`kvm_agent/hardware/env.py:208-224`), not the battery. The
bridge's pixel→wire scale therefore stays on its hardcoded fallback
(`hid_bridge.py:260`, marked "FALLBACK ONLY — overwritten at runtime by
/hid/set_screen once a caller pushes it"). If the target's real resolution ever
differs from that fallback, **every click lands stretched, silently** — the exact
720p-A/B case `appliance.py:95-98`'s own docstring warns about. `CFG.screen_size`
is never pushed to the bridge.

### 2. An API error or timeout kills the run — and the rest of the battery — with no recorded verdict
`call_holo_full` re-raises on failure (`kvm_agent/models/holo.py:604-609`), and
`run()` calls it with no guard (`agent_loop_holo.py:356`). The OpenAI client
timeout is 180 s (`kvm_agent/llm/ollama.py:14`). Consequences of a single network
blip or model-server hiccup:
- The exception propagates out of `run()`, so the already-created `RunRecorder`
  never gets `finish()` — **no `summary.json`**, the run dir just stops.
- In the battery (`tools/battery.py:108`) the only guard is the outer
  `try/finally: shutdown()`, so the exception **aborts the current task and every
  remaining task**. The exec-error path learned this lesson on 2026-07-21 (the
  `winkey` crash, handled at `agent_loop_holo.py:396-404`); the model-call path
  never did.

### 3. A capture stall is a `print`, then business as usual
`_execute` catches the `wait_newer` `TimeoutError` and prints a warning
(`agent_loop_holo.py:250-254`), then proceeds to `wait_until_stable` and lets the
caller frame-diff a **possibly stale frame**. Finding #6 (post-action frame
predating the action) was closed by the frame-seq pairing; a swallowed stall
quietly reopens that class. Neither the recorder nor the model's `<tool_output>`
ever learns the freshness floor was violated.

### 4. The camera-verified HID gate only protects the battery
`target.verify_hid` (`kvm_agent/hardware/target.py:26-77`) exists precisely
because "the bridge probe's kbd/mouse online flags can LIE" (`target.py:30-33`,
the I2 half-dead-HID class). Its only caller is `tools/battery.py:97`. `boot()`
does `clear_hid()` and nothing else (`agent_loop_holo.py:86-92`), so every REPL /
non-battery session runs on an unverified HID channel — the exact failure the
gate was built to catch.

### 5. `wait_until_stable` cannot say "I never stabilized"
`kvm_agent/hardware/env.py:34-60` returns `None` whether the screen settled, the
timeout expired mid-churn, or `read_fn()` returned `None` for the entire window
(dead capture → returns having compared nothing). Callers (`_execute`,
`PicoEnv._settle`, `verify_hid`) cannot distinguish "settled" from "capture is
dead," and the comment at `env.py:44` asks future readers to re-validate a
threshold that has no return signal to validate against.

### 6. `jinja2` is not a declared dependency; `requests` is declared and unused
- `kvm_agent/models/holo.py:347` lazily imports `jinja2` and renders
  `docs/native/local-desktop-2026-06-12.j2` (`holo.py:104,349`) — the system
  prompt of the live loop. It is absent from `pyproject.toml:10-16`. Because the
  import is inside a function, a fresh environment fails **at runtime, on the
  native-prompt path**, not at install.
- `requests` (`pyproject.toml:15`) has zero live imports — the only greps are the
  `holo_requests.jsonl` log filename (`config.py:75`, `holo.py:115`). Live HTTP
  is `urllib` (`appliance.py`) + the `openai` SDK.

---

## P1 — bugs and suspect logic

### 7. Planning-only steps count as "screen frozen"
`update_plan` short-circuits before any frame diff (`agent_loop_holo.py:405-407`),
so a step whose batch is only `update_plan` leaves `step_changed=False`, which
increments `frozen` (`:453`). Four consecutive planning steps trip the
no-progress abort (`:465-467`) as "screen frozen" despite legitimate progress.
Today this is masked in the battery only because it passes
`no_progress_abort=False` (`tools/battery.py:109`); the REPL default is armed.

### 8. `drag_to` trusts the tracked cursor without re-asserting it
`agent_loop_holo.py:190-202` reads `CURSOR["pos"]` and goes straight to
`down(); move(x2,y2); up()`. The no-tracked-position case is handled (loud
no-op), but a *stale* tracked position is not: any physical drift or any action
that moves the pointer without updating `CURSOR` starts the drag from the wrong
place. `ApplianceClient.drag` (`appliance.py:76-80`) already re-moves to the
start first — and `_execute` doesn't use it. Absolute-pointing hardware makes the
fix free: one `move(x1,y1)` before `down()`.

### 9. `Camera.__init__` raises `SystemExit` on bring-up failure
`env.py:136`. `SystemExit` sails past `except Exception` in any embedding caller
(battery, future server), turning a rig fault into a process teardown instead of
a catchable error.

### 10. `_scalar` will average garbage into a coordinate
`holo.py:397-409`: any list is averaged with only a `logger.warning` — a
zero-length list is a `ZeroDivisionError`, and a nonsense multi-element list
becomes a plausible-looking midpoint click. The hosted-API range quirk is real,
but the handling deserves a length/shape check before it invents a coordinate.

### 11. `test_clear_hid.py` tests an error path that cannot occur in production
The fake server returns HTTP **200** with `{"ok": false}`
(`tests/test_clear_hid.py:23-30`); the real bridge returns **502/404/400**
(`hid_bridge.py:243,235,241`). Since `_req` wraps everything in
`except Exception` (`appliance.py:36-40`), real bridge errors arrive as
`HTTPError` → `"transport error"`, and the `data.get("ok")` false-branch the test
exercises is unreachable. It still fails loudly — but the bridge's carefully
constructed `ack`/`error` detail never reaches the caller, and the unhappy path
is validated against a server that doesn't behave like the real one.

### 12. `show_reasoning.py` — the designated first-responder tool — has a stale action vocabulary
`_same_action` branches on `"left_click"`, `"type"`, `"key"`
(`tools/show_reasoning.py:32-40`), but the live loop emits `hotkey` with a `keys`
list (`agent_loop_holo.py:207-210`); there is no `"key"` action kind. So the
`"key"` branch is dead, hotkey/double_click repeats are never flagged, and `:85`
reads `action.get("key")` (should be `keys`) so hotkey steps print empty detail.
Its usage text also still references retired `waa` run tags and `run.py`
(`:1,13-15`). PROJECT_STATE.md §2 names this the "first tool on any failed run" —
it should speak the current action set.

---

## P2 — test coverage

The tested surface is real but thin, and it misses exactly the layer AGENTS.md §2
calls the historical bug site (capture → prompt → parse → action):

- **Zero tests** for `run()` (`agent_loop_holo.py:307`) and `_execute()`
  (`:163`) — batch execution, history threading, `<tool_output>` construction,
  stuck/frozen/click-repeat guards. All of it unexercised.
- **Zero tests** for the model adapter: `parse_response` (`holo.py:470`),
  `observation_message` (`:412`), `tool_output_message` (`:429`), and the
  in-place image eviction `trim_to_last_n_images` (`:448`). Two fixtures that
  look purpose-built for exactly this —
  `kvm_agent/models/_fixtures/holo_native_verbatim_raw.json` and
  `holo_phase2_native_tools_raw.json` — are referenced by **no test**.
- `ApplianceClient`: only `clear_hid` + the generic raise are tested;
  `move/click/type/combo/drag/set_screen/...` and the newline-splitting `type()`
  (`appliance.py:58-69`) are not.
- `pikvm_proto`: only the static `KEYCODES` dict is touched. `crc16` (`:138`),
  `_frame` (`:151`), `_roundtrip` magic/CRC/error decode (`:173`), and the
  load-bearing `_px_to_proto` (`:287`) — all pure, all untested.
- The tests are script-style (module-level asserts + `sys.exit`), so `pytest`
  can't even collect them; there is no runner config, no CI, and no declared test
  dependency. They only "pass" via `python tests/x.py` on a provisioned machine.
- `pikvm_proto.py:34` does a top-level `import serial`, coupling the pure
  keycode/CRC/coordinate helpers to a hardware dep — `test_key_aliases.py` dies
  on import on any box without pyserial.
- Import side effect: importing `agent_loop_holo` runs `os.makedirs` for the
  debug dir (`agent_loop_holo.py:59-60`), so merely importing it (as
  `test_frame_diff.py` and `target.verify_hid` do) writes to disk.

---

## P3 — hygiene, docs, config sprawl

### CLAUDE.md is the single biggest doc problem
The header layout block (`CLAUDE.md:11-29`, "cleaned 2026-07-20") is accurate.
Nearly everything under it is not:
- `:36-41` still names `waa/runner.py` as the live eval harness; `:56-65` cites
  `waa/shakedown_results/` paths — archived on 2026-07-20 per PROJECT_STATE §5,
  i.e. the body contradicts the header two dozen lines above it.
- The layered "READ FIRST" blocks describe the retired EvoCUA/UI-TARS/B580
  planner-executive stack, the VM target, and the Firefox/calc benchmarks at
  length (~80 of the 82 KB).
- `:13` claims "~120M tracked"; actual tracked content is **2.5 MB** (312 files,
  745 KiB pack).
- `:22-23` misplaces the archived pico firmware (`_archive/old-stack/`; it
  actually lives at `_archive/firmware_old/appliance_pico/` —
  `appliance/README.md:14` has it right).

PROJECT_STATE.md is the accurate source of truth, with one nit: `:15` still says
"one tool-call per step," which `9a98d96` (native-verbatim rearchitecture,
2026-07-21) changed to batched tool calls (`agent_loop_holo.py:7-9`,
`holo.py:24-25` document the new contract).

### Config sprawl vs `config.py`'s charter
`config.py:3` claims every knob "lives HERE." Counterexamples, all hardcoded:
- `FRAME_CHANGE_THRESHOLD=3.0` in three places: `agent_loop_holo.py:70`,
  `wait_until_stable`'s default (`env.py:34`), and `_tile_max_diff`'s implicit
  calibration — with `env.py:44` asking future readers to "RE-VALIDATE and
  adjust" a value that has no single home.
- Loop tuning (`CONFIRM_FIRST/STUCK_LIMIT/NO_PROGRESS_LIMIT`,
  `agent_loop_holo.py:62-71`); `verify_hid`'s screen default, Start-corner
  coordinate, and `thresh=20.0` (`target.py:26,71`); the 1280×720 evidence
  downscale (`env.py:170`) while the model-input res correctly uses CFG
  (`env.py:185`); model params (`holo.py:598,600,645`); timeouts
  (`ollama.py:14`, `appliance.py:27`).

### Layering and duplication
- `kvm_agent/hardware/target.py:46` imports `_frame_diff_score` from the
  **root app script** — package→script inversion, plus the import-time
  `os.makedirs` side effect noted above. The tile-diff metric all three callers
  need has no shared home: it's implemented twice (`env.py:23-31` on arrays vs
  `agent_loop_holo.py:259-280` on PNG bytes) with identical hardcoded geometry
  that can drift apart silently.
- `tools/probe_resolution_ab.py:45-54` re-implements
  `Camera.model_input_jpeg` (`env.py:174-188`) by hand; they match today and
  will drift.
- `build_messages` + `trim_to_last_n_images` (`holo.py:440-445,448-463`) mutate
  the caller's shared `history` dicts as a side effect of building a request.
  It converges today because `run()` re-trims (`agent_loop_holo.py:437-440`),
  but the aliasing is a trap if the two `max_history_images` ever differ.

### AGENTS.md §1 output discipline
- `tools/probe_resolution_ab.py` prints its A/B benchmark means to stdout only
  (`:128-147`) — the one tool whose primary result never lands in `runs/`.
- `tools/battery.py:77` writes the battery summary as a loose file at `runs/`
  root (`runs/battery_<ts>_results.json`) rather than in a per-run folder
  (per-task `RunRecorder` dirs are correctly foldered).
- Device-side, not host-side, but noted: `hid_bridge.py:266` defaults its log to
  `/home/aaron/...`, and `hid-bridge.service:8,10` hardcodes `User=aaron` +
  `/home/aaron/hid_bridge.py`.

### Dead code and naming residue
- `_frame_png_full` (`agent_loop_holo.py:101-105`) — exact alias of
  `_frame_png`; every caller is in `_archive/`. `_frame_diff_score`'s
  `drop_bottom_row` param is accepted and ignored (`:283-288`).
  `probe_resolution_ab.py:31` imports CFG and never uses it.
- `kvm_agent/llm/ollama.py` — module still named for retired Ollama and imported
  live (`holo.py:99`); its own header admits the name survives only for import
  stability.
- `appliance/pi5/stage1_ping_test.py` still speaks the retired ASCII protocol
  (`:65-73`) — it cannot talk to the shipped `pico_fw` (binary CRC16 frames
  only). Same category as the un-ported `send.py` the README already flags.
- `hid_bridge.py:261` comments still point at the archived `waa/runner.py`.
- `docs/native/local-desktop-2026-06-12.j2` is a **runtime asset loaded by live
  code** (`holo.py:104`) stored under `docs/` — docs are partially load-bearing.
- `_archive/firmware_old/agent_calibration.txt` (108 KB) is Python source under a
  `.txt` name.

### Packaging
`pip install .` yields the library only: the live entry point is a root script
(self-acknowledged at `pyproject.toml:21-23`), there is no console entry point,
and cross-directory imports run on cwd/sys.path accidents
(`tests/test_battery.py` → `import battery`; `tests/test_key_aliases.py` →
`import pikvm_proto`; `tools/battery.py` → `import agent_loop_holo`).

---

## What's clean — verified, not assumed

- **Wire protocol, end to end:** every `ApplianceClient` path exists in the
  bridge `ROUTES` (`appliance.py:46-102` ⇄ `hid_bridge.py:161-167,225`); ports
  agree everywhere (`config.py:40`, `hid_bridge.py:259`, the systemd unit); and
  `pikvm_proto` constants match `pico_fw/src/ph_proto.h` exactly — magic bytes,
  command set, PONG flags, button bits, error codes, and the
  `_px_to_proto` ⇄ `ph_cmds.c:113` coordinate mapping. No mismatch found.
- **No live imports from `_archive/`** — checked by scanning every live import
  and by targeted grep for all retired module names. The rule holds.
- **Repo size/hygiene:** 2.5 MB tracked, no blobs, no stray binaries, clean
  `git status`, `.gitignore` correct, `_archive/` is 1.7 MB total.
- **Battery grading is fail-closed** (`tools/battery.py:52-56`), results are
  written incrementally so a crash keeps prior grades (`:116`), and the HID gate
  loop actually re-prompts until the camera confirms input (`:96-104`).
- **`appliance/README.md` is accurate** — including the archive locations
  CLAUDE.md gets wrong.
- The AGENTS.md blame-ledger discipline visibly shaped the code: the exec-error
  containment (`agent_loop_holo.py:396-404`), the freshness floor, and the
  fail-closed grade prompt all trace to ledger rows.

---

## Recommended next actions (ordered; not part of this diff)

1. Push `CFG.screen_size` to the bridge via `set_screen` in `boot()` — one line,
   closes the silent click-scaling hole (P0-1).
2. Guard the `call_holo_full` call in `run()`: catch, `recorder.finish(False,
   note=...)`, count against `STUCK_LIMIT` or abort the task — never the whole
   battery (P0-2).
3. Surface a capture stall into the step's `<tool_output>` and the recorder
   instead of a print (P0-3).
4. `pyproject`: add `jinja2`, drop `requests` (P0-6).
5. Call `verify_hid` from `boot()` (or make the REPL path opt-in-skippable) so
   the gate covers every session (P0-4).
6. Exempt pure-`update_plan` steps from the frozen counter (P1-7); add the
   pre-drag `move(x1,y1)` (P1-8).
7. Prune CLAUDE.md to the header + a pointer at PROJECT_STATE.md/docs; fix the
   one stale line in PROJECT_STATE §2 (P3).
8. Pytest-ify the test suite (declare pytest, convert the script asserts) and
   write the `parse_response` test the orphaned fixtures were made for (P2).
9. Then the P3 tail: single home for the tile-diff metric + threshold in CFG,
   move the `.j2` out of docs/ (or bless docs/native as a runtime asset dir),
   drop `_frame_png_full`/dead params, decouple `pikvm_proto`'s pure helpers
   from the `serial` import.

---

## Errata & amendments (2026-07-21, post-merge audit by Aaron)

The review above merged as written; Aaron's close reading found the following
corrections. Kept here rather than edited in place, per the house record-keeping
discipline. Items marked FIXED were addressed the same day on the follow-up branch.

### Where the review UNDERSTATED the problem
- **P0-6 (jinja2) is worse than stated.** `SYSTEM_PROMPT = _render_native_prompt()`
  runs at module top level (`holo.py:365`), so a fresh env without jinja2 fails at
  `import kvm_agent.models.holo` — before any run starts, taking every entry point
  and test with it, not just "the native-prompt path". The fix (add jinja2, drop
  requests) was exactly right. FIXED.
- **P2 (pyserial) is worse than stated.** pyserial was not declared in
  pyproject.toml at all, so there was NO install path that made
  `test_key_aliases.py` importable. FIXED: `serial` is now an optional import in
  `pikvm_proto` (pure helpers importable without it) and pyserial is declared as
  the `appliance` extra.

### Where the review OVERSTATED or needed nuance
- **P2-b (parser "entirely untested") overstated.** `parse_response` had offline
  coverage via `_self_test()` (`holo.py:664`) against
  `holo_native_verbatim_raw.json` — just not in `tests/` and not
  pytest-collectable. Only `holo_phase2_native_tools_raw.json` was truly orphaned —
  and it turns out to be a capture of the RETIRED phase-2 tool-calling format, so
  the correct test pins the current parser rejecting it loudly, not parsing it.
  FIXED: `tests/test_holo_parser.py` covers both.
- **P1-9 (Camera SystemExit): real hazard, wrong victim.** The battery was not
  exposed — `boot()` is called outside the battery's `try`. It is a
  future-embedding trap, not a live bug. (Unscheduled; noted for a future batch.)
- **P0-2 nuance:** the failure was persisted to `logs/holo_requests.jsonl` before
  the re-raise — what was lost is `summary.json` and the remaining battery tasks,
  not all forensic trace. FIXED (the guard).
- **P1-8 (drag_to) nuance:** in-loop desync was not actually possible (every
  pointer-moving branch updates CURSOR); the real exposure is target-side physical
  drift. The suggested fix (pre-drag re-home) was still right. FIXED.

### Cosmetic corrections
- Tracked content is ~1.85 MB, not "2.5 MB" (the review's figure came from du
  block-size rounding).
- `build_messages` ALIASES the caller's history; `trim_to_last_n_images` is what
  mutates through the alias.
- The header the review called "accurate" (CLAUDE.md:11-29) itself contained the
  :13 (~120M) and :22-23 (pico-fw location) errors the review lists — the review
  should not have endorsed it wholesale. (CLAUDE.md has since been pruned outright.)

### Implementation snags the review missed (found while fixing)
- **P0-4 (verify_hid in boot()) was not a drop-in**: the interactive replug loop
  and the multi-attempt cost live in the battery, and boot() needed a
  non-interactive failure policy that didn't exist. Resolution: single-attempt
  gate, raise-with-diagnosis, hardware released so boot() stays re-runnable,
  `verify=False` escape hatch; battery keeps its own gate.
- **P0-3 (capture stall)**: `wait_newer`'s timeout EQUALS the settle budget
  (1.5 s), so "just raise" would have changed severity semantics. Resolution: one
  extended grace (STALL_GRACE_S), then surface to model + recorder, abort after
  STALL_ABORT_LIMIT consecutive stalls (not gated by no_progress_abort).
- **P0-1**: the bridge docstring falsely named `PicoEnv.__init__` as the
  set_screen caller. FIXED in the same commit as the wiring.
- **show_reasoning.py was more broken than "stale vocabulary"**: since the
  batching change, `step_NN.json`'s `action` field holds the whole parsed STEP, so
  the tool's per-step readout matched nothing at all on new runs. FIXED: handles
  both record shapes, batch-aware repeat detection, hotkey vocabulary.
