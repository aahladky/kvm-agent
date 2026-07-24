# Session 2026-06-21 — config.py wired + first LIVE run_goal (Firefox)

Goal of the session: (1) get the `config.py` consolidation actually finished, then (2) push the
agent to run a **complex** task end-to-end on the live rig — "Download and install Firefox then set
it as the default browser." This is the first real exercise of the LIVE `planner -> executive`
(`run_goal`) path; the prior default-browser win was an *isolated harness*, not this chain.

Target = the Windows 10 box driven over camera + Pico HID. The Windows-MCP PowerShell used for
tooling runs on the **desktop orchestrator**, a different machine — it can see the target only via
the capture card, and cannot query the target's registry.

---

## 1. The earlier Firefox "1 step, 0.0s, done" was RulePlanner, not a weak 8B

`runs/goal_115333/planner.json` (written by the new raw-logging) shows `planner="RulePlanner",
raw=null, parsed=[{"op":"done"}]`. The server had been started with `AGENT_PLANNER=rule` in its
shell (carried over from a `measure` run). RulePlanner only handles notepad/calculator; for anything
else it returns a bare `[{"op":"done"}]`, which `validate_plan` happily passed and `run_plan` ran as
an instant "success." `AGENT_PLANNER` is **not** globally set (default `hf`).

## 2. Silent-success guard + raw planner logging  (orchestration/planner.py)

- `plan_is_actionable(plan)` — true iff the plan contains a state-changing op
  (`launch/type/tap/key/click`). `run_goal` now **refuses** a non-actionable plan for a non-empty
  goal: it does not execute it, emits a loud `guard:` event, returns a failure status, and lets the
  replan loop run. This converts the silent success into an honest failure.
- `validate_plan` additionally **warns** when a plan has actions but no `verify` (goal state would go
  unchecked before `done`).
- `ClaudePlanner._complete` / `LocalPlanner._complete` now stash `self.last_raw`; `run_goal` writes a
  `planner.json` (planner class, **raw model reply**, parsed plan, validated plan, lint issues) next
  to `plan.json`. The raw reply was previously discarded — that's why the first no-op was un-debuggable.
- Guard + lint unit-tested offline. The 10/10 keyboard benchmark is unaffected: the guard lives in
  `run_goal`, and `measure.py` calls `run_plan` directly with actionable plans.

## 3. config.py migration finished  (CFG is now the single source)

It had been only half-wired: `hardware/pico_client.py`, `llm/ollama.py`, the tesseract path, and
`__init__.py` read `CFG`, but the **server** read `os.environ` directly and re-hardcoded the OpenAI
endpoint in ~6 files; and `AGENT_PLANNER` / `AGENT_SEND_IMAGE` weren't in `config.py` at all.

- `config.py`: added `planner_kind` (`AGENT_PLANNER`), `send_image` (`AGENT_SEND_IMAGE`),
  `anthropic_key`, `runs_dir`. (`executor_model`/`verifier_model`/`planner_model`/`cam_index`/
  `screen_*` already existed.)
- Repointed at `CFG`: `server/app.py` (`build_planner`, `get_executive`, `/health`), `executive.py`
  (module-level OpenAI/Ollama defaults + `Executive.open` cam/screen/executor), `models/uitars.py`,
  `models/evocua.py`, `live_ctl.py`.
- **Collapsed** root `agent_server.py` from a 208-line duplicate (it imported the root *shims* and
  read its own env) into a thin launcher that imports `kvm_agent.server.app`. One server now.
- Verified: defaults == prior literals (executable test); `python -m py_compile` + `import
  kvm_agent.server.app` clean on Windows (`IMPORT_OK hf True C:\Dev\vllm\runs False`).

## 4. Planner idioms — the 8B plans the task well once told the conventions  (planner.py SYSTEM)

Added general, keyboard-first idioms:
- **Install** via winget: `launch 'cmd'` -> type `winget install --silent
  --accept-package-agreements --accept-source-agreements <Id>` -> `tap enter` -> `sleep` -> verify.
- **Set a Windows default app**: `launch 'ms-settings:defaultapps'` (Win+R runs `ms-settings:` URIs).
- **Register** a freshly winget-installed browser by launching it once **before** setting default.
- `alt+f4` the terminal so it doesn't cover Settings.

`tools/probe_planner.py` (no-HID planner probe) shows the 8B (Qwen3-VL-8B via HF) adopting all of
these and emitting a clean ~7–10 step keyboard-first plan. **The 8B is not the weak link.**

## 5. Live results (tools/run_goal_once.py — 2 runs)

Frames + `planner.json` + `plan.json` under `runs/firefox*` and `runs/firefox_re1_*`/`_re2_*`.

**Works:** `cmd` -> `winget install Mozilla.Firefox` -> vision-verified **installed**. The replan
loop, no-op guard, lint, per-step capture, planner.json, and the `alt+f4`-close-terminal step all
functioned live.

**Blocked (default-browser half)** — root-caused from the captured frames:
- (a) `launch firefox` via **Win+R errors** "Windows cannot find 'Firefox'." A winget-installed app
  isn't bare-name runnable via Win+R (`runs/firefox_re1_125356/06_launch.png`).
- (b) `Executive.launch()` **false-confirmed** on that error dialog — the dialog's title bar reads
  "Firefox," so the `_app_open` vision check ("is a Firefox window open?") answered yes and reported
  `launch Firefox ok` when nothing launched. A silent false-positive.
- (c) Therefore Firefox never registered and is **absent from the Win10 default-browser chooser**
  (`runs/firefox_re1_125356/09_click.png` shows only Chrome/IE/Edge). The default cannot be set.
- Secondary (moot until a/b): the "Web browser" row is below the fold (needs scroll); the set is a
  tile -> flyout -> pick sequence the planner doesn't encode and grounding struggles with.

Grounding records (from `plan.json`) confirm UI-TARS *did* emit click coordinates (470,910 /
365,793 / 536,777) — these weren't no-coordinate misses; they hit the wrong spot / a screen with no
valid target, so the frame didn't change and `click_target` correctly reported failure.

---

## Next steps (ordered — (1)+(2) likely get the whole task to pass)

1. **Launch installed GUI apps via Start-menu search** (tap `Win` -> type name -> `Enter`), not
   Win+R. Keep Win+R for system commands (`cmd`, `ms-settings:` URIs). Implement in
   `Executive.launch()` — split system-command vs installed-app, or try Start search for non-system
   names.
2. **Harden `Executive.launch()` confirm**: detect a "Windows cannot find '<x>'" dialog (vision/OCR),
   `Esc` it, return `False`. Do not accept a same-named error dialog as "the app is open." This is the
   silent false-positive that broke this run.
3. **Add a `scroll` op** to the plan schema + executive (firmware wheel v5 works) so the planner can
   reach below-the-fold targets.
4. **Encode the Win10 default-browser idiom**, reusing `tools/isolate_default_browser.py`'s solved
   flow: scroll to "Web browser" -> click the current-default tile -> click Firefox in the flyout ->
   verify. Only works after (1)/(2) make Firefox actually launch + register.
5. **Re-run firefox live**; expect a pass.

## Tooling / env gotchas

- The Linux **bash mount** serves stale/truncated/null-byte snapshots of files just written by the
  file tools (minutes of lag this session; `py_compile` gave bogus errors). Proper fix: compile/run
  repo code via the **Windows-side MCP** (Windows-MCP PowerShell / Desktop Commander) against
  `C:\Dev\vllm`; verify contents with the Read tool, not bash. Use bash only for self-contained
  sandbox work.
- New diagnostics in `tools/`: `probe_planner.py` (planner output, no HID; `--kind`, `--frame`),
  `run_goal_once.py` (one goal end-to-end on the rig; needs the rig free — stop `agent_server` first,
  single capture card).
- Current target state: Firefox **is installed**; Chrome is still the default browser.
