# Session 2026-07-19 (continued) — note-uptake fix, contamination audit, WinUI3 flyout
# bug confirmed real, VM→physical-device pivot decided, full output-layout consolidation

Continues the same day covered by `docs/SESSION_2026-07-19_holo_focus_bug_and_native_prompt_port.md`.
That doc ends with uncommitted structured-output-probe work in progress; this session
finished it, found a second real bug, made a major architecture call, and fixed a
structural project-hygiene problem. Commits this session, oldest first:

```
8261471 holo: add note-uptake A/B probes (function-calling vs structured output)
544996a holo: make note a required schema field, not optional -- fixes 0% uptake
4910d0c hid: auto-sync HID appliance resolution to the actual capture-card negotiation
8b78cf7 hid: query the guest's TRUE resolution and sync the whole pipeline to it
2b9a590 hid: log every command end to end -- sent, appliance chain, and what the target reports
2ba377f holo: log every actual request/response to the model, not just the response
cea7f99 holo: rearchitect from OpenAI function-calling to native's actual structured-output mechanism
542efe6 agent_loop: remove injected retry from _execute() -- contamination fix
acca3ce consolidate all generated/runtime output under var/, add a mechanical layout guard
```

## 1. Structured output + note-required fix — DONE, verified live

Holo3.1 now talks to the backend via genuine JSON-schema-constrained structured output
(`response_format={"type":"json_schema",...}`), matching H Company's actual reference
implementation, instead of OpenAI function-calling. `note` is a required (non-nullable)
schema field — this, not the function-calling/structured-output mechanism itself, is
what fixed the 0% note-uptake problem from earlier in the day (proven via
`tools/probe_holo_note_uptake_at_depth.py`'s 5-arm replay: required-note hit 9/9,
seeded-history also worked but requires history tampering, worked-example failed 0/9).
Live-verified: a full notepad task run (`runs/waa__...WOS_20260719_114041`) passed at
score=1.0 with 15/15 steps carrying a persisted note, and the request log confirmed zero
function-calling fields were used on the wire. See `kvm_agent/models/holo.py`'s module
docstring for the full list of disclosed deviations from native (required-non-nullable
note, single-tool_call constraint, custom press_key/scroll/drag_and_drop, condensed
system prompt, goldfish memory at n=1) — all deliberate, all still true.

## 2. Contamination audit → `_execute()`'s injected retry removed

**Governing principle for the rest of the session** (user's own words): *"if anything is
sending inputs to the vm or editing the model's inputs to the vm outside of anything from
holo reference implementation it is contamination."*

`_execute()` in `agent_loop_holo.py` used to auto-retry `left_click`/`type` up to 2x on a
frame-diff "no visible change" heuristic, and for `type` specifically injected an extra
click at screen-center the model never asked for. Root-caused as the ACTUAL cause of a
real bug, not a fix for one: a `type` for "draft.txt" was genuinely delivered (frame diff
2.3, just under the 3.0 threshold — a legitimately small filename-field edit) and got
retyped by this code's own retry, producing `"This is a draft.draft.txtdraft.txt"`. Fixed
by removing the retry entirely — native's own loop has no host-side execution-retry
heuristic at all; the model judges success from the next screenshot via its own `thought`.

Live-validated twice on the identical task: run5 (retry still present) scored 1.0; run6
(retry removed) scored 0.0, stuck cycling File-menu attempts until `max_steps`. **This was
not evidence the retry should come back** — see §3, the wire log proved every one of
run6's clicks was genuinely delivered. Re-adding the retry would have reintroduced the
exact same false-remediation shape as the draft.txt bug.

## 3. THE BIG FINDING: a real, documented WinUI3 flyout-click bug, confirmed live

This is the most important technical result of the session and directly explains a lot of
prior "unreliable grounding" folklore in this project's history.

**What it is:** Windows 11's modern (WinUI3) Notepad's `File` menu — and, per the
pervasiveness audit below, other flyout/dropdown-style Windows UI — sometimes ignores a
pixel-accurate click on an open menu item entirely: the click is genuinely delivered
(verified via wire-level HID logs, `mouse_online: true`, clean ACK) and lands exactly on
the target (verified by drawing the actual click coordinate onto the actual frame the
model saw — dead-center on "Save as" text), but the menu just closes with no effect,
as if the click landed on empty space outside it.

**This is a real, independently-documented Microsoft bug**, not a project-specific
mystery:
[microsoft-ui-xaml#10481](https://github.com/microsoft/microsoft-ui-xaml/issues/10481) —
a `MenuFlyout` ignores hover *and* click on first open specifically when the input
arrives via a remote/synthetic path (their repro is RDP RemoteApp) rather than a local
physical mouse; works fine on subsequent opens of the same control. It also matches a
much older, independently-documented Win32/RPA-industry gotcha (UiPath, Power Automate
Desktop both ship default `DelayBefore`/`DelayAfter` params and a dedicated "Hover"
primitive specifically for this class of failure) — though the settle-delay mitigation
those tools use did NOT turn out to be the fix here (see below).

**How it was pinned down (in order, don't skip steps — each ruled something out):**
1. run6's File-menu clicks showed the "wire-clean, no visible effect" signature repeatedly.
2. Cross-checked against `logs/appliance_client_commands.jsonl`: 81/81 commands in that
   run showed `mouse_online: true`, clean ACKs, ~1.7ms wire time — zero transport failures.
3. Marked the actual click coordinates onto the actual frames the model saw: pixel-perfect
   on both "File" and "Save as," including one frame showing the File menu genuinely open
   with the crosshair dead-center on "Save as" — the very next frame shows the menu closed,
   no dialog, nothing typed.
4. Web research found the Microsoft issue + the RPA-industry settle-delay convention.
5. **First live A/B test (`tools/probe_flyout_click_ab.py`, 12 trials)**: teleport-click vs
   move-then-settle-250ms. Result: 11/12 succeeded regardless of condition — only the
   very first trial of the whole run failed. Read initially as "settle delay barely
   matters, some other factor dominates" — **this reading was wrong and got corrected by
   the user**, who pointed out only 1 of the 12 trials was a genuine first-open (the other
   11 all reused an already-opened-once File menu within one Notepad process).
6. **Second probe (`tools/probe_first_open_reliability.py`, 6 trials)**: kills and
   relaunches the notepad.exe *process* fresh before every trial. Result: 6/6 succeeded.
   This also turned out to test the wrong thing — the Windows *session* (DWM/compositor)
   was already warm from the first probe's trials earlier that day, even though each
   process was new.
7. **Third probe (`tools/probe_session_fresh_first_open.py`, 4 trials)**: a genuine
   `VMController.revert_clean()` snapshot-revert + cold reboot before every trial — the
   exact machinery every real WAA task already uses. Result: **4/4 FAILED.** File menu
   opened cleanly every time; the very first click on "Save as" inside it never produced
   the dialog, every single trial. This is the real, faithful test, and it's
   near-deterministic on this rig.
8. **Pervasiveness audit** (a forked subagent, full log correlation across every
   wire-log-covered run that day): 49-63% of every `left_click` in every run — pass or
   fail — hit this exact wire-clean-no-effect pattern. Confirmed run5's "pass" was itself
   propped up by the (now-removed) retry logic silently absorbing 2 fully-exhausted
   retries on this exact failure mode, not by the underlying issue being fixed.

**What's confirmed vs. still open:**
- Confirmed: the bug is real, reproduces live and near-deterministically on this rig on a
  genuine fresh-session first flyout interaction, matches a documented Microsoft issue,
  is not a delivery/transport problem, and is not fixed by a settle delay in the one
  controlled test that isolated it.
- NOT confirmed / still open: why run6's real task kept failing on File-menu clicks for
  20+ steps rather than just the first one (the clean probes only ever tested a *single*
  first-open per session, not a repeated-failure pattern) — plausibly compounded by the
  model's own coordinate-estimation drift stacking on top of the flyout bug, but this is
  an unverified hypothesis, explicitly flagged as such, not asserted as fact. Whether this
  reproduces on a genuinely physical (non-VM, non-SPICE) input path is **untested** — see
  §4, this is now a first-priority thing to check once the physical-device migration
  lands, since it would settle whether this is a VM/SPICE-synthetic-input artifact or a
  true physical-HID-injection limitation.

## 4. Two more real problems found investigating the above (both in the VM layer)

- **A separate virtual input device was letting the user's own real mouse/keyboard bleed
  into the guest.** The VM's qemu command line has `-device usb-tablet,...` — a
  QEMU-emulated virtual mouse, completely independent of the Pico's `hostdev` USB
  passthrough (which was re-verified clean: real hardware passthrough, confirmed live in
  the running qemu process's own `-device usb-host,hostdevice=/dev/bus/usb/001/018`
  argument, not just present-but-unattached config). Standard SPICE-console behavior:
  whenever `remote-viewer`'s window has focus/is fullscreened, real host mouse movement
  gets translated into this virtual device inside the guest — the same mechanism as any
  RDP/VNC session. Not a bug in the Pico path; a leftover default from a normal libvirt VM
  that was never stripped for a project whose whole premise is "the target only ever sees
  the Pico's physical HID." **Not yet removed** — became moot once the VM-to-physical
  pivot was decided (§5), but worth remembering if a VM target is ever used again.
- **The physical monitor output feeding the capture card was running at the wrong
  resolution.** `HDMI-7` (confirmed via EDID: the connected "display" identifies itself
  as `"HDMI TO USB"` — literally the Macrosilicon capture chip) declares its own native/
  preferred mode as 1920x1080 (first Detailed Timing Descriptor), but X11 was driving it
  at 2560x1440. That's a real extra scaling step between "what's rendered" and "what the
  capture card digitizes," on top of the capture chip's own internal scaler — a plausible
  contributor to imprecise clicks on small/dense targets, never isolated or fixed. Also
  moot pending the physical-device pivot, but the same class of check (compare the
  physical output's driven resolution against its EDID-declared native mode) is worth
  running again on whatever output eventually feeds the capture card post-migration.

## 5. Decision: abandon the QEMU VM target, move to a physical device

**Status: decided, NOT yet executed.** Target: the MacBook Pro from early in this
project's history, now dual-booted to Windows 10 (real HDMI-out + USB, no virtualization
layer — this is the original hardware topology from before the project moved to a VM for
task-reset convenience).

**Why:** in one move this eliminates the SPICE virtual-tablet input contamination (§4),
the HDMI-7 resolution mismatch (§4), and the entire snapshot-revert/cold-reboot machinery
(`kvm_agent/hardware/vm.py`, `waa/runner.py`'s VM-dependent task setup) — and it returns
to the project's original stated premise ("nothing installed on the target... undetectable,
OS-agnostic"), which the VM was always a convenience deviation from.

**What has to happen physically before anything else** (cannot be done remotely, needs
the user):
1. Move the Pico's USB cable from the Linux host (currently passed through to the VM) to
   plug directly into the MacBook Pro.
2. Re-wire the capture card's HDMI input to the MacBook Pro's actual video output, not
   whatever's currently feeding it.
3. Confirm the MacBook Pro is booted into Windows 10 at a usable desktop.

**Before running anything against it**, verify concretely, don't assume: grab a live
frame and confirm it's actually the Mac's screen (not stale/black/still the VM), and do a
real HID round-trip (toggle a key, read it back) to confirm the Pico's input is reaching
Windows on the Mac.

**Once verified**, `agent_loop_holo.py`'s REPL (`boot()`/`run()`) works against it
directly with zero code changes — it doesn't know or care whether the capture+appliance
are pointed at a VM or a physical box. The WAA-specific benchmark harness
(`waa/runner.py`, VM revert, task configs) is VM-coupled and won't apply unless the WAA
benchmark eval framework specifically is wanted against the new target too — that's a
separate, not-yet-scoped decision.

**First priority test once wired up**: re-run `tools/probe_session_fresh_first_open.py`
(or an adapted version — it currently calls `VMController.revert_clean()`, which won't
apply to a physical target; the File-menu-click test logic itself is target-agnostic)
against the physical Mac. This directly answers the open question from §3: is the WinUI3
flyout bug a VM/SPICE-synthetic-input artifact, or does it also hit genuine physical HID
injection? That result changes how seriously to weight the bug going forward.

## 6. Full output/log layout consolidation

Separate from the above — a structural fix prompted by how hard the investigation in §3
turned out to be to do cleanly, given output was scattered across four independently-
invented path schemes plus a remote Pi 5, an external git clone, and (worst offender) a
Claude-Code job-tmp directory that isn't part of the repo at all.

**Full detail:** `docs/PROJECT_LAYOUT.md` (the canonical, kept-current layout doc — read
this, not this recap, for the actual rules) and `docs/EXTERNAL_DEPS.md` (Pi 5 + the
WindowsAgentArena clone). Headline: everything generated/runtime now lives under one
gitignored `var/` root, reached exclusively through `kvm_agent.config.CFG`'s
`var_dir`-derived properties (`runs_dir`, `logs_dir`, `waa_results_dir`, `waa_cache_dir`,
`waa_shakedown_dir`, `dbg_dir`, `scratch_dir`) — never hardcode a new path, add a CFG
property instead. Enforced mechanically, not just documented:
`tools/check_layout.py`, wired via a committed `.claude/settings.json` `PreToolUse` hook
(fires automatically for any future Claude Code session on this repo, no install step)
plus `tools/install_hooks.sh` for plain-terminal commits. Today's live investigation
evidence (screenshots, the 3 probe scripts referenced in §3) was rescued out of the
job-tmp directory into `tools/` (the scripts, fixed and reusable) and
`var/scratch/2026-07-19_*/` (the raw evidence) before it could be lost to job cleanup.

## Quick-reference facts (came up enough this session to be worth pinning here)

- **Pi 5 HID bridge**: `192.168.0.29:8080` (not 8000), runs as systemd `hid-bridge.service`
  — `sudo systemctl restart hid-bridge.service` after any manual code deploy (no automated
  deploy script exists yet). Full command reference: `/health` GET, `/hid/{move,click,
  rclick,down,up,home,key,type,combo,scroll,probe,set_screen}` — see
  `appliance/pi5/hid_bridge.py`'s module docstring or ask a future session to re-derive it,
  it's stable.
- **UART baud rate**: 115200, consistent Pi5↔Pico, GP0(TX)/GP1(RX).
- **`appliance/pi5/send.py` and `stage1_ping_test.py` are STALE** — they speak the old
  plain-ASCII protocol against the retired CircuitPython firmware, not PiKVM's current
  binary CRC16-framed protocol. Don't use them; use the HTTP bridge or `pikvm_proto.
  PicoHidLink` directly instead.
- **Driving Holo directly without the WAA harness**: `agent_loop_holo.py` is REPL-driven,
  not a CLI script. `python3 -i -c "from agent_loop_holo import *; boot()"` then
  `ground()`/`mark()`/`do()` for single-action propose-then-confirm, or `run(goal,
  max_steps=N, confirm_first=0)` for a full closed loop — **`confirm_first` must be 0 for
  any non-interactive/backgrounded invocation**, its default gates the first 5 steps
  behind a real keypress and will crash with `EOFError` on a closed stdin otherwise.
