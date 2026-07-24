# Session 2026-07-18/19 (overnight) — real root causes found, native prompt ported, 3-depth shakedown run

Started from `docs/REPORT_2026-07-19_problems.md` (the skeptical review of the prior WAA
adoption report). That review's environment fixes were real, but this session found the
review's own "model problems" conclusions (M1/M4) were themselves partly wrong — several
of them were environment bugs in disguise. Ends with a working, verified click-to-focus
fix, a completed 3-depth shakedown, and a scoped port of native holo-desktop-cli's prompt
engineering into our own loop.

## 1. Small fixes shipped and verified

- **`press_key` tool** added to Holo's action space (`kvm_agent/models/holo.py` TOOLS +
  `parse_response`, `agent_loop_holo.py` `_execute`) — the loop had no keyboard path
  besides `write()`'s `press_enter`; a real notepad run burned 15 click/drag steps trying
  to clear a Save-dialog filename field with the mouse alone.
- **`waa/runner.py`'s Docker→sandbox path rewrite** only handled `C:\\Users\\Docker`;
  4 tasks (1 chrome, 3 file_explorer) use `C:/Users/Docker` and were silently missed.
  Fixed to rewrite both spellings.
- **WAA server terminal-window leak (real, pre-existing, present in EVERY WAA run since
  adoption).** `/execute` and `/setup/launch` in the vendored
  `WindowsAgentArena/.../vm/setup/server/main.py` spawned every command without
  `CREATE_NO_WINDOW`; Windows 11's default terminal handler popped a visible
  WindowsTerminal window on top of the desktop for every single guest command —
  confirmed present even in an *old, passing* `storage_sense` run's step_00, so it
  predates and is independent of tonight's other findings. Patched (host source +
  live in-guest copy), verified 0 windows across repeated `/execute` calls post-fix,
  re-baked into the `clean-desktop` snapshot.
- **Store auto-update pause** — user-applied via the Store UI (5-week cap, Windows 11
  ignores the classic `ForegroundLockTimeout`-style registry permanent-fix path for this).
  Expires **~2026-08-23** — memory `waa_store_autoupdate_pause.md` has the exact
  re-application steps and the click-sequence gotchas (a stray click near the toggle can
  hit the adjacent "Windows Update" hyperlink and pop a shell dialog).
- **`tools/show_reasoning.py`** — every step's `reasoning_content` was already being
  captured verbatim by `RunRecorder` and never read back systematically. This tool prints
  a run's action+reasoning trace readably, with a `--repeats-only` flag for the
  loop/stall signature. **This should be the first thing checked on any failed run** —
  see `holo_reasoning_capture.md` memory. Confirmed 33/33 steps present on a real run.

## 2. The real root cause: Windows doesn't reliably hand focus to a freshly-launched app

Root-caused, not guessed — reproduced twice by replaying a real failing run's exact
action prefix. After launching Notepad via Win+R and pressing Enter, `GetForegroundWindow()`
returned **Program Manager / "FolderView"** (the desktop icon list), not Notepad — despite
Notepad rendering on top with an apparently-active title bar and blinking cursor. Every
subsequent keystroke went to the desktop's incremental-search, not the document.

This is a documented, general Windows/RPA-industry gotcha (SetForegroundWindow
restrictions; Windows 11 is known to silently ignore the `ForegroundLockTimeout` registry
workaround), confirmed via Microsoft's own Power Automate docs and RPA community
consensus: **"Focus window" alone is not reliable — always click the window afterward.**

**Fix landed in `agent_loop_holo.py`'s `_execute()`** (verify-and-retry): after
`left_click`/`type`, diff the screen before/after via `_frame_diff_score`. A `type` that
produced no visible change gets a click at screen-center (forces real Win32 focus via
genuine input) before retrying — NOT a blind resend into a window that was never focused.
`left_click` failures retry the identical click (different failure class — a Pico ACK
only proves the Pico's USB stack accepted the report, not that Windows processed it; see
`pico_passthrough_mouse_dead` memory).

**Verified fix:** replaying the same failing prefix with the fix active produced the
correct document content ("This is a draft.") where two prior unguarded replays produced
zero characters. The subsequent notepad task in the 3-depth shakedown passed cleanly at
both history=1 and history=2 (first passes ever recorded on that task).

### What this was NOT (dead ends chased and disproven, kept for honesty)

- **Not a dead mouse.** 47+ isolated clicks across multiple test batches (raw primitive,
  model-grounded path, exact type-then-click transition) all succeeded. A camera-verified
  right-click (move → rclick, the gold-standard test per `pico_passthrough_mouse_dead`)
  landed exactly where commanded.
- **Not (solely) a grounding/coordinate-precision problem.** One early hypothesis (720p
  model-input downscale hurting precision on small calendar grid cells) was directly
  tested and **disproven** — full 1080p input still failed, with a *different* failure
  mode (locked into the wrong year-picker arrow direction for 20+ steps instead of
  coordinate misses). Resolution isn't nothing, but it isn't the calc-task story.
- **Not (solely) history depth.** History=1 vs 2 vs 3 all showed real effects in
  different places, but none alone flips the hard calc task — see §3/§4.

## 3. Three-depth shakedown (17 tasks × HOLO_HISTORY_IMAGES ∈ {1,2,3}), run overnight

`tools/shakedown_ab.py --depths 1 2 3` — notepad/windows_calc/microsoft_paint/clock/settings
(2+3+3+4+5=17 tasks), ~6.1 hours wall time. Results in `waa/shakedown_results/manifest.json`
and per-batch JSON; full log in `shakedown_full.log`.

| depth | passed / attempted |
|---|---|
| 1 | 5 / 17 |
| 2 | 7 / 16 (1 task lost to an appliance crash) |
| 3 | 7 / 15 (2 tasks lost to appliance crashes) |

By category across all 3 depths: `notepad` 3/6, `windows_calc` **0/9**, `microsoft_paint`
7/8 (best), `clock` 1/10 (weak, also where both appliance crashes hit), `settings` 8/15.

**Two appliance-level crashes** (`HTTP 502 Bad Gateway` on `/hid/key`, a real Pi5
hid-bridge transport failure, not a scoring failure) hit deep into the run (~4-5h of
continuous use). Not investigated further tonight — worth a look if it recurs; the
hid-bridge deliberately has zero logging (`log_message` is a no-op), so there's no
forensic trail from these two events.

**Read the pass-rate trend across depths with real caution**: n=1 per task per depth, and
the crashes make denominators unequal. Not enough here to declare a winner on history
depth alone.

## 4. windows_calc deep dive: 0/9, but the reason is more interesting than "hard task"

This is the session's longest thread. Full arc, in order:

1. Read the model's own reasoning on a failing calc run (via `show_reasoning.py`) —
   looked like a genuine, sympathetic struggle with a nested year/month/day calendar
   picker (misclicking day 21 for day 8, etc.).
2. **Pulled H Company's own `holo-desktop-cli`** (`hcompai/holo-desktop-cli` on GitHub,
   installed live inside the guest via `irm https://install.hcompany.ai/install.ps1 | iex`,
   pointed at our own local holo3.1 via `--base-url http://192.168.122.1:9292/v1` — the
   libvirt bridge IP, reachable since llama-swap binds `*:9292`) and ran the **identical**
   task natively (no Pico, no capture card — real Win32 input/capture inside the guest).
   **It passed** — exact byte-match on the saved file. Same model, same task, different
   execution path.
3. That flipped the diagnosis from "model can't do this" to "our pipeline is worse at
   this specific class of interaction than native's." Formed and **tested** the
   resolution hypothesis (native sends the model full 1920x1080; we downscale to 720p) —
   **disproven directly**: full-res through our own harness still failed, with a
   different failure signature (wrong arrow direction, not coordinate misses).
4. Tested full-res **+** history=3 together — also failed, this time with 15 consecutive
   zero-effect clicks on an already-open month grid ("Oct"). Directly disproved my own
   "already selected, false-positive stall" theory by diffing the pre/post frames
   (pixel-identical from the very first attempt, not just later retries).
5. **Live-reproduced the actual widget behavior by hand** (`agent_loop_holo` calls
   against the real Calculator), confirming its navigation is genuinely inconsistent:
   from a day-view, one header click correctly zooms out to a month-grid; but selecting a
   specific *year* from the decade-grid skips month-view entirely and drops straight into
   a day-view for an arbitrary default month — reproduced twice, independently. This is a
   real WinUI3 CalendarView quirk, not a model or environment failure.
6. User asked the sharp question: *if it was landing on the widget, why didn't clicking
   again refocus it, the way it fixed Notepad?* Checked directly: `GetForegroundWindow()`
   confirmed Calculator held genuine Win32 foreground focus the *entire* stuck stretch —
   this is a structurally different bug from the Notepad case. WinUI3 popups/flyouts are
   commonly built `WS_EX_NOACTIVATE` (deliberately non-focus-stealing), so the parent
   window legitimately stays "foreground" throughout; the stuck page's problem is most
   likely its rendered pixels desyncing from its actual hit-testable element tree (a
   real, if narrower, WinUI3 popup bug class), not absent focus. **Untested candidate
   fix**: Escape-and-reopen the popup rather than re-clicking within it, when a click
   inside a flyout/popup specifically shows zero effect across multiple nearby
   coordinates.

**Bottom line on windows_calc**: 0/9 is real, but it's not one root cause — across 4
different attempts on the identical task, each one stumbled on a *different* specific
aspect of this one genuinely confusing widget (grid misclicks, arrow-direction lock-in,
an unresolved stuck-flyout page, and native's own initial-but-recovered arrow confusion).
Consistent with temperature=0.8 sampling variance determining which trap you fall into on
a hard task, not a single fixable bug.

## 5. Native holo-desktop-cli comparison → scoped prompt port

**What holodesktop actually is** (this matters for what to do with it): H Company's own
open-source, actively-developed agent CLI/runtime. It **installs on the machine it
controls** and uses native OS APIs (Accessibility/Screen Recording permissions on macOS,
native Win32 input on Windows) — explicitly *not* a remote-desktop client. That's the
opposite operating model from this project's entire premise ("nothing installed on the
target machine" — see the top of this file's CLAUDE.md). **You cannot replace this
project with holodesktop without abandoning the reason it exists.** But the prompt
engineering and agent-loop wisdom are pure text/logic on *our* side, independent of the
installation model — that part is portable regardless.

**Captured ground truth**, not guessed: set up a logging proxy inside the guest between
`holo.exe` and our local llama-swap endpoint (`tools`-style throwaway script, not
committed — see §7), ran a real task through it, and inspected the raw outgoing request.

- Native's system prompt: **~26,000 characters**, templated per-run (budget/effort/persona
  filled in). Ours: ~700, static.
- Native uses **JSON-schema-constrained structured output** (`structured_outputs`), not
  OpenAI tool-calling — `tools`/`tool_choice` are unset in the actual request. We use
  native function-calling (`tools=[...]`, `tool_choice="required"`). Different mechanisms
  for the same job; not swapped tonight (too large/risky a change to make blind).
- Concrete, adoptable wins identified: an **explicit loop-detection instruction**
  ("Detect loops... if it failed previously, you MUST pivot"), a **persistent notes
  mechanism** (native: "only the last few screenshots are kept; notes persist" — this
  reframes the whole history-depth investigation: the fix for goldfish memory isn't more
  images, it's structured text persistence), and a **much stricter termination
  checklist** before calling `answer` (plausibly explains false-positive "task completed
  successfully" claims seen tonight that didn't match the saved file).

**Ported** (adapted, not copied verbatim — native batches multiple tool calls per step
and we force exactly one via `tool_choice="required"`, so native's standalone `note` tool
would burn a whole step every use, worsening the exact step-budget-burn problem it's
meant to help; instead added as an optional `note` param on every existing action tool):

- `kvm_agent/models/holo.py`: `NOTE_PARAM` added to click/write/press_key/scroll/
  drag_and_drop tool schemas; `parse_response` threads it into the normalized action dict;
  `SYSTEM_PROMPT` gained the loop-detection line, notes-persistence framing, and
  termination checklist (condensed from native's wording, not full copies); `observation_message`/
  `build_messages`/`call_holo_full` gained a `notes: list[str]` param, rendered in an
  `<notes>` block on **every** turn (unlike history, notes must survive image eviction).
- `agent_loop_holo.py`'s `run()`: maintains a growing `notes` list, passes it to
  `call_holo_full` every step, appends `action["note"]` when present, logs it.

**Verified safe**: `python -m kvm_agent.models.holo` self-test still 6/6 clean, manual
parse tests confirm `note` threads through correctly and is absent when not given.

**Tested live, honest result — mixed, not a clean win**: ran the same hardest calc task
once with the port active. Still failed (0/1, 40 steps) — expected, see §4, this is the
worst-case task in the set. Two signals worth carrying forward:
- **Notes: zero uptake.** The model never used the `note` param once in 40 steps, despite
  the instruction. Either the instruction needs to be more explicit about *when*
  (a single-window task like Calculator never visually "loses" its state the way
  switching apps does, so the model may not have judged anything worth persisting), or it
  needs a stronger nudge.
- **Loop-detection: a real, partial behavioral change.** 9 repeat-flagged steps this run,
  but scattered across genuinely different strategies (left-arrow, header-click, direct
  year-grid click) rather than one 15-20-step mega-streak on a single dead-end like the
  pre-port runs showed. Didn't solve the task, but visibly changed the failure shape.

**Not yet validated**: the port on an easier task class (e.g., the notepad File-menu
repeat-click pattern that originally motivated this) where notes/loop-detection are more
plausibly load-bearing and we have a clean before/after baseline (0/8 historically, 1/1
after the focus fix landed). This is the natural next test — see §7.

## 6. PiKVM MCP tooling — explored, not adopted, one idea worth stealing

`KultivatorConsulting/pikvm_mcp_server` (small: 3 stars, 2 forks, last push ~4mo stale) —
a thin MCP transport layer exposing a *real, full PiKVM device* as tools for an
MCP-capable agent (Claude Code etc.), not a competing agent runtime. Different layer of
the stack from holodesktop: no planning/notes/reasoning of its own, purely the
hardware-action layer, meant to be driven by an external agent's brain.

**Relevant overlap**: our Pico firmware is *already* a port of PiKVM's own firmware (see
`pikvm_hid_rp2350_port` memory) — we're one layer below full PiKVM already, running a
custom Pi5 `hid_bridge.py` instead of the real `kvmd` PiKVM software stack. The one
feature this MCP server has that we lack: **`pikvm_auto_calibrate`** — vision-based
cursor-position calibration via screenshot diffing, correcting drift between commanded
and actual cursor position. Directly relevant to tonight's coordinate-precision
questions (§4's grid-cell misses).

**Recommendation, not yet implemented**: don't adopt the MCP server or migrate to full
PiKVM software (a real infrastructure project — different OS image, ATX/video/mass-storage
subsystems we don't use) — instead steal the *idea*: a lightweight, camera-based
"click a known target, verify the cursor/effect landed where commanded, compute a
correction factor" routine, added to our own `pico_client.py`/`appliance.py`. Scoped but
not started.

## 7. Next steps, priority order

1. **Test the prompt port on an easier, previously-diagnosed task class** (notepad
   File-menu repeats) rather than only the hardest calc task — a fairer read of whether
   notes/loop-detection help on the failure mode they were actually designed for.
2. **Strengthen the notes instruction** if step 1 also shows zero uptake — the current
   wording may not create enough pressure to actually use the param.
3. **Escape-and-reopen recovery** for stuck-popup clicks (§4.6) — untested candidate fix
   for a real, narrower bug class distinct from the Notepad focus fix.
4. **hid-bridge logging** — currently zero (`log_message` is a no-op by design, "the
   caller sees ACKs in the JSON"). The two overnight 502 crashes left no forensic trail.
   Worth adding minimal request/response logging if appliance crashes recur.
5. **Calibration routine** (§6) — vision-verified click-precision correction, adapted
   from the pikvm_mcp_server idea, not its code.
6. **Store auto-update pause expires ~2026-08-23** — re-apply or get the permanent HKLM
   fix via an elevated session before then (see `waa_store_autoupdate_pause.md`).
7. Working tree has real, tested, uncommitted changes (`agent_loop_holo.py`,
   `kvm_agent/config.py`, `kvm_agent/hardware/env.py`, `kvm_agent/models/holo.py`,
   `waa/runner.py`, plus new `tools/shakedown_ab.py` and `tools/show_reasoning.py`) —
   not committed this session per the "only commit when explicitly asked" rule; ask to
   have this reviewed/committed when ready.

## 8. Uncommitted diagnostic scratch tooling (not in the repo)

Two throwaway scripts lived only in `/tmp` during this session, not committed:
`capture_proxy.py` (a minimal Python `http.server` reverse proxy, logs the full JSON
request body to a file before forwarding — used to capture native holo-desktop-cli's
real outgoing request) and the base64-transfer pattern for pulling large text back
through the WAA `/execute` channel without hitting Windows console Unicode-encoding
truncation. Worth formalizing into `tools/` if this kind of request-capture is needed
again.
