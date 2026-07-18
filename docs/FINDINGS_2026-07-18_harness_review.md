# Harness / Pico / capture code review — 2026-07-18

Critical read of the code paths exercised by the first live Holo battery run, prompted by
that run's results being untrustworthy. Each flaw is marked by evidence class:

- **[measured]** — proven against real data (saved run frames) during this review.
- **[confirmed]** — unambiguous from reading the code; failure mode is not in doubt.
- **[suspected]** — plausible from the code, needs a live test to confirm impact.

**Through-line:** in findings 1, 2, 4, and 8 the code manufactures a success signal that
is not tied to reality — and 3 of those 4 are in the harness/plumbing we wrote, not in the
model. Until the harness stops lying to both the model (tool-result text) and the scorer
(grading), treat every behavioral conclusion about "what Holo struggled with" as unreliable.

---

## STATUS — fixes as of 2026-07-18

- **Pico/action channel (#1, #2, #3): RESOLVED** by the Pi 5 + Pico UART appliance rebuild
  (wired transport, per-command ACK, defined API). See `docs/PLAN_2026-07-18_pi5_pico_appliance.md`
  and `appliance/`. Stages 1-3,5,6 done; the WiFi Pico is retired.
- **#5 (Camera SIGABRT race): FIXED** — `Camera.release()` now joins the capture thread before
  releasing the device (`kvm_agent/hardware/env.py`).
- **#4 (bogus frame-diff signal): FIXED** — replaced whole-frame mean with a tile-max metric,
  live-calibrated; offline test in `tests/test_frame_diff.py` (`agent_loop_holo.py`).
- **#9 (no no-progress detection): FIXED** — `run()` aborts on a frozen screen or clustered
  repeated clicks (`agent_loop_holo.py`).
- **#8 (fail-open grading): FIXED** — `Verifier.available()` + per-result verified/unverified/na
  status + loud warning; a None grade no longer masquerades as a verified pass
  (`kvm_agent/orchestration/executive.py`, `kvm_agent/battery/runner.py`, `tests/test_battery.py`).
- **#7 (no reset between tasks): FIXED** — libvirt snapshot revert + a FORCED COLD REBOOT
  (`kvm_agent/hardware/vm.py: VMController`), wired into `runner.py` before every task.
  **Important correction found live 2026-07-18 during the first real battery run**: a warm
  revert alone (no reboot) reliably left the passed-through USB HID device dead — every
  click/keypress ACKed at the Pico, QEMU showed the hostdev attached, but nothing reached
  the guest OS, while the desktop rendered PERFECTLY (the pixel check alone could not
  catch it). Root cause: a memory snapshot can only rewind the guest's belief about the
  USB device's state, not the physical device's actual state — a known general limitation
  of USB passthrough + snapshots. Fix: `revert_clean()` now ALWAYS forces a full
  shutdown+start after the revert (100% reliable across every test today, vs 0/2 for warm
  revert alone once a real round-trip check was added). Also added `_verify_hid()` — a
  NumLock-toggle + LED-readback round trip, independent of the camera — specifically to
  catch this class of failure, since the existing pixel-diff verification cannot see it.
  Also self-heals the virt-viewer SPICE bridge, which does NOT survive a revert/reboot on
  its own. Live-verified end-to-end (dirtied desktop with Notepad then Paint across
  several cycles; both pixel AND HID checks pass after the fix). Costs ~45-60s per task
  now instead of ~16s — a deliberate speed-for-reliability tradeoff.
- **#11 (refusal-vs-exhaustion scoring): FIXED** — researched how OSWorld/WebArena handle
  this (OSWorld: a distinct `FAIL` action, never inferred from silence; WebArena: exact
  answer must be `"N/A"`, gated on an exploration-effort threshold after the original
  version proved gameable). Adopted OSWorld's principle within Holo's real (vendor-
  documented) action space: `agent_loop_holo.run()` now returns `{finished, answer_text}`
  instead of a bare bool; `Verifier.judge_refusal()` (text-only LLM call, same fail-open
  contract as the vision graders) classifies the answer text as a genuine refusal vs a
  false success claim. `runner.py`: exhaustion (never answered) is now a deterministic
  failure — no ambiguity, no backend needed; answering is graded by the judge. Offline-
  tested (`tests/test_battery.py`), not yet run live.

---

## Pico / action channel — RESOLVED via the appliance rebuild (structural note at bottom)

### 1. No command acknowledgement — every action "succeeds" unconditionally. [confirmed]
`code.py`'s `handle()` never replies. So `pico_client.py._send()`'s `recv(64)` always times
out and returns `""`. `sendall()` succeeding only means bytes left the host TCP stack — not
that the Pico received the line, parsed it, or that Windows accepted the HID report. A
move/click/type that vanishes returns an identical clean `""`. This is exactly what bit us
2026-07-17: mouse HID dead, keyboard fine, *every send returned cleanly*. The harness
structurally cannot distinguish "acted" from "action silently dropped."

### 2. Reconnect-on-error masks a dead-HID Pico as healthy. [confirmed]
`_send()`'s `except OSError:` reconnects and resends. A Pico in the enumerated-but-HID-
rejected state (the Code 10 case documented repeatedly in CLAUDE.md) still accepts TCP and
consumes bytes, so the reconnect "recovers" a connection whose HID output is dead. TCP
health ≠ HID health; the code conflates them.

### 3. `combo()` silently drops unresolvable keys. [confirmed]
`code.py combo()` filters to resolvable keycodes and fires whatever remains.
`combo("ctrl+someunknown+a")` silently sends Ctrl+A — no error, wrong chord.

---

## Screen capture (kvm_agent/hardware/env.py)

### 4. `_frame_changed` tool-result signal is meaningless — measured wrong in BOTH directions. [measured]
`agent_loop_holo._frame_changed` (threshold 3.0 on a 160×90 mean-abs pixel diff) feeds Holo
a "screen changed / did not visibly change" tool-result each step. Measured on the saved
battery frames:
- **False-negative (calc_basic):** the real digit clicks 7/×/8/= produced diffs of
  **0.71, 0.01, 0.03, 0.12** — all << 3.0. While Holo correctly computed 7×8=56 it was being
  told "your action did not visibly change the screen" every step.
- **False-positive (small_target_tray):** every click produced diff **~9.5** (a notification
  flyout toggling), so Holo was told "screen changed" on all 6 clicks that never advanced the
  goal.

A whole-frame mean drowns out small-but-meaningful changes (a digit) and amplifies
large-but-irrelevant ones (a flyout). The signal added in the vendor-alignment pass to
*prevent* loops is, when measured, either inverted or noise. calc_basic passed only because
Holo reads the actual screenshot and ignored the bogus text.

### 5. `Camera.release()` never joins the capture thread → native crash race. [confirmed]
`release()` sets `self.run=False`, sleeps 0.1s, then `cap.release()`. The daemon `_loop`
thread can be blocked *inside* `cap.read()` (V4L2 blocks waiting for a frame) when
`cap.release()` frees the device underneath it — the mechanism behind the "exception not
rethrown / Aborted (core dumped)" SIGABRT. The 2026-07-17 "shutdown fix" only ensured
`release()` gets *called*; it did NOT add a `thread.join()`, so the race remains.

### 6. No frame-freshness guarantee. [suspected]
The background thread overwrites `self.frame` continuously; no timestamp/sequence number, and
`CAP_PROP_BUFFERSIZE=1` is a request V4L2 commonly ignores. `png_bytes()` returns whatever
the thread last stored, which can predate the action if the settle is shorter than the
capture-pipeline latency. The 1.5s settle likely masks it; no *guarantee* the graded frame is
post-action. Needs a timestamped-frame test.

### (minor) `png_bytes()` ignores `imencode`'s ok flag → `.tobytes()` on None if it ever fails.

---

## Harness (agent_loop_holo.py + kvm_agent/battery/runner.py)

### 7. No reset between tasks — desktop state accumulates without bound. [measured]
Each task started atop the leftover windows of every prior task (Calculator + Notepad +
Settings + File Explorer stacked by the 4th task, seen in the step_00 frames). `runner.py`
has zero cleanup. Invalidates every multi-task run's later tasks. (Our bug.)

### 8. Grading fail-open silently degrades to self-report. [confirmed]
`correct = bool(finished) and (graded is not False)` — `None` (can't-verify) counts as
correct. Both grading backends were down all session, so every `graded` was `None`, so
`correct` was pure self-report — printed as confident "correct: true" with no warning the
independent check never ran. A verifier that no-ops silently and reports success is the exact
anti-pattern the project exists to kill. (Our bug.)

### 9. Stuck-detection only counts parse errors, never no-progress. [confirmed]
`run()` increments `stuck` only on `action == "error"`. A model clicking the same wrong
coordinate 6+ times (small_target_tray) never trips it and burns the full budget.
`_frame_changed` is computed but never wired into loop-breaking. (Our bug.)

### 10. Scroll has no target; drag is a teleport. [confirmed scroll / suspected drag]
The Holo `scroll` tool schema has no x/y, and `_execute` fires `r4.scroll(±3)` at wherever
the cursor last landed — so scroll_to_about scrolled the sidebar the cursor was over, not the
content pane (model narrated "the screen isn't changing"). `R4.drag()` does
move→down→move→up with no hold and no intermediate waypoints — many apps read that as two
clicks, not a drag (untested; the model never attempted one).

### 11. `expect_answer=False` can't distinguish refusal from exhaustion. [confirmed]
`correct = not finished` — a task that flails to the step cap scores identically to an honest
"that app isn't installed." impossible_app "passed" for the wrong reason. (Our bug.)

---

## Structural note: why the Pico/USB-HID layer is the right first target

The action channel is not just buggy in spots — it's architecturally open-loop end to end,
and the failures above (1, 2) are symptoms of that, not independent defects:

**Software:** the transport is fire-and-forget. `host -> TCP -> firmware -> HID -> target OS`
has no return path at any hop. The firmware can't report "HID report accepted," the host
can't report "command parsed," and nothing reports "the target OS actually moved the cursor."
Every layer assumes the next one worked. There is no sequence numbering, so a dropped or
reordered command is invisible; no per-command status, so a partial combo or a rejected report
looks identical to success.

**Hardware/OS:** the composite HID device (keyboard collection + mouse collection sharing one
interface via Report IDs) has already demonstrated that the two collections can come up
independently — 2026-07-17 the keyboard worked while the mouse silently did nothing after a
live USB re-enumeration. WiFi transport adds a second unreliable hop (power-save
disassociations, DHCP drift, the "needs a physical replug" hang) on top of the USB one. So the
one channel the agent relies on to affect the world is the least observable and least reliable
part of the stack.

Directions to weigh when we tackle it (not decisions — starting points):
- An **ACK protocol**: firmware replies per command (received / parsed / HID-report-sent, with
  a sequence id echoed). Turns finding 1 from "impossible to detect" into "detectable."
- A **HID liveness probe** the host can issue that proves the mouse *and* keyboard collections
  are both alive at the target (e.g. a known no-op report whose acceptance the firmware can
  confirm), so finding 2 stops masking a half-dead device.
- Reconsider **WiFi vs wired** for the command channel, or at least a heartbeat that
  distinguishes "TCP up" from "HID delivering."
- Whether a **single combined HID interface with Report IDs** is the right descriptor at all,
  vs separate interfaces, given the independent-collection-death behavior.

These are notes, not a plan — the plan comes when we design the replacement.
