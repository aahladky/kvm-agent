# SESSION 2026-07-22 — Roadmap Phase 0: firmware hardening (Slice B)

## What this session was

Slice B from `docs/PLAN_2026-07-22_roadmap_alignment_slices.md` Part 3: the
roadmap's Phase 0 ("harden the primitive for unattended runs"), scope grown by
the long-idle mouse-death diagnosis (`PROJECT_STATE.md` §4, found during the
post-guard-rerun operator notes). Code + offline tests + a firmware build
verification landed first; the operator then flashed the Pico and the deploy
was completed and functionally verified same session (see "Deploy" below). The
overnight soak itself (the actual Phase-0 gate) is POSTPONED, operator
decision — see "Deploy" for why.

## Changes

### 1. HW watchdog (`appliance/pico_fw/src/main.c`)
`watchdog_enable(1000, true)` after init; `watchdog_update()` every loop
iteration, gated on `!_reset_required` — the existing deliberate mode-change
reboot (`watchdog_reboot(0,0,100)`, reachable only via SET_KBD/SET_MOUSE, which
this host never sends) is already re-armed for a 100ms reboot when that flag is
set, so petting during that window would fight it instead of letting it fire on
schedule. `watchdog_enable_caused_reboot()` is read into `_watchdog_rebooted`
**before** `watchdog_enable()` — both touch the SAME scratch register
(pico-sdk's `scratch[4]`), and the mode-change reboot also clears it (to 0), so
this correctly distinguishes "genuine unpetted hang" from "the deliberate
reboot we already have." Verified `ph_outputs.c`'s mode persistence uses
`scratch[0]`, a different register — no collision (checked the pico-sdk source,
`.pico-sdk/src/rp2_common/hardware_watchdog/watchdog.c`).

### 2. Watchdog + suspend visibility (`ph_proto.h`, `main.c`)
- `PH_PROTO_PONG_WATCHDOG_REBOOTED` (`0x20`) — the only unused bit in resp[1]
  (RESET_REQUIRED already claims `0x40`); OR'd in whenever `_watchdog_rebooted`.
- `PH_PROTO_PONG2_USB_SUSPENDED` (`0x01` of resp[4]) — resp[4] was always
  zero-padding before this (checked: `main.c`'s `_send_response` only ever set
  resp[0-3] and resp[6-7]), so this is free wire real estate, not a bit stolen
  from resp[2]/resp[3]'s existing capability/output-mode semantics. Backed by a
  new `ph_g_usb_suspended` global in `ph_usb.c`, refreshed every `ph_usb_task()`
  tick from `tud_suspended()` — main.c reads the global instead of pulling
  `tusb.h` in just for this.

### 3. Host retry (`appliance/pi5/pikvm_proto.py`)
`_roundtrip` split into `_attempt` (one wire round trip, raises `_NackError` for
a well-framed rejection or `_AmbiguousError` for a no/garbled response) and the
retry wrapper: a NACK is safe to retry for **any** command (the pico definitely
saw and rejected it); an ambiguous failure only retries `IDEMPOTENT_CMDS =
{PING, CLEAR_HID, KBD_KEY, MOUSE_ABS, MOUSE_BUTTON}` — never `MOUSE_WHEEL`,
whose payload is a relative delta and would risk a double-scroll if the first
attempt actually landed. `MAX_RETRIES=2`, `RETRY_PAUSE_S=0.150` — deliberately
longer than the firmware's 100ms UART idle-gap resync window (`ph_com_uart.c`'s
`_TIMEOUT_US`), so the pause doubles as the resync trigger. Every successful
response carries a `retries` count; `decode_code` widened to `decode_code(code,
raw=None)` to also surface `watchdog_rebooted`/`usb_suspended` when the full
8-byte response is available (every real caller now passes it — `probe()` and
`hid_bridge.py`'s `_wire_info`).

### 4. Mouse suspend fix, part (a) — retain+resend (`ph_usb.c`)
The diagnosed bug: `_CHECK_MOUSE`'s old ABS/REL-shared macro, on
`tud_suspended()`, called `tud_remote_wakeup()` then unconditionally
`_MOUSE_CLEAR; return` — the report was **dropped**, never retried, while the
kbd path (`_kbd_sync_report`) already retains state and retries every 1ms tick
until delivery succeeds. New `_mouse_abs_try_send()` mirrors that pattern for
ABS mode: on suspend it does NOT clear state, just returns (retried on the next
`ph_usb_task()` tick, wired in alongside kbd's existing per-tick retry). A new
`_mouse_pending_v` holds the wheel delta (the one non-idempotent piece of an
ABS report) so it replays exactly once when the deferred send finally succeeds,
never twice. **ABS only** — REL mode is untouched (not in this project's live
deployment; GPIO defaults select ABS at boot, `ph_outputs.c`), and replaying a
stale relative delta after an arbitrary suspend gap has different, unverified
semantics best left for if/when REL is actually used.

Deliberately **not** implemented this session (PROJECT_STATE.md fix candidates
(b)/(c)): (b)'s "refuse commands into a suspended bus" — only the visibility
half landed; actively refusing is a bigger behavior change with no evidence yet
it's needed. (c) a bridge-side keep-alive to hold off autosuspend — speculative,
build only if the soak shows (a)+(b) aren't enough (roadmap §0: measurement
gates every step).

### 5. `tools/soak.py` (new)
The Phase-0 gate harness: probe every 10s (`/hid/probe` — now carrying
watchdog/suspend/retry visibility end-to-end), a benign corner mouse-move +
camera-liveness check (`Camera.wait_newer`) every 5min, JSONL to
`runs/soak_<ts>/soak.jsonl` (flushed per line). Fault injection (UART unplug,
bridge restart) is explicitly **operator-driven, not scripted** — the gate is
"every failure line maps to something the operator actually did," correlated by
wall-clock timestamp after the run. `python tools/soak.py --hours 8` for the
actual gate; unbounded (Ctrl-C to stop) by default.

## Verification this session

- `python -m pytest tests/` — 79 passed (was 71 pre-session). New:
  `tests/test_pikvm_proto_retry.py` (8 tests, fake serial: NACK-retried-for-any-
  command, ambiguous-retried-for-idempotent-only, ambiguous-NOT-retried-for-
  MOUSE_WHEEL, retry exhaustion raises, zero-retries on first-try success, bad
  magic counts as ambiguous, `decode_code`'s new fields with/without `raw`).
- Firmware: full clean rebuild via **both** `cmake --build` directly and the
  real `make` deploy path (`appliance/pico_fw/Makefile` → `hid.uf2`), with
  `-Wall -Wextra` — zero warnings, zero errors. This is a real compile against
  the pinned pico-sdk 2.2.0 + TinyUSB, not a syntax guess.
- NOT verified (needs the rig, next): BOOTSEL flash, Pi 5 `pikvm_proto.py`/
  `hid_bridge.py` deploy + hid-bridge restart, `/health` check, camera-verified
  HID gate, then the actual overnight soak.

## Known caveat to watch during the soak

Worst-case retry latency for one `_roundtrip` call is now ~3.3s (up from ~1.0s):
up to 3 attempts × the serial link's 1.0s read timeout, plus 2 × 150ms pauses.
`type_text()` calls `kbd_key()` twice per character, so a pathologically failing
link could in principle push a single `/hid/type` HTTP call close to its scaled
timeout budget. This only matters if MOST keystrokes are hitting worst-case
retry exhaustion, which would already mean the link is essentially dead either
way — but it's exactly the kind of thing the soak should surface if it's a real
problem in practice.

## Deploy (same session, after the operator flashed the Pico)

Completed and functionally verified:
- Backed up the Pi 5's running `pikvm_proto.py`/`hid_bridge.py` (timestamped
  `.bak.20260723_023236`), scp'd the new versions, syntax-checked remotely.
- `sudo systemctl restart hid-bridge` — clean restart, `hid-bridge.service`
  active against the freshly-flashed Pico.
- `/health`: `{"ok": true, "pico_acking": true, "probe": "PROBE caps=0 num=0
  scroll=0 kbd=1 mouse=1 watchdog_rebooted=0 usb_suspended=0", ...}` — the two
  new PONG fields decode correctly end-to-end (host `decode_code` → bridge
  `_cmd_probe`'s ack string), and read as expected for a just-BOOTSEL-flashed
  board (a USB/power-on reset, not a watchdog reset, so `watchdog_rebooted=0`
  is correct, not a false negative).
- `agent_loop_holo.boot()` (the camera-verified HID gate, `target.verify_hid`):
  **passed** — `hid ok (gnome: kbd diff 49.1, mouse diff 49.1)`. Both HID
  collections camera-confirmed delivering to the target OS, not just
  self-reporting online.

This is real evidence the Slice B changes work correctly against actual
hardware (new firmware bits decode right, the retry/watchdog code didn't break
anything, the appliance is fully functional) — it just isn't the multi-hour
soak.

## Soak: POSTPONED (operator decision, 2026-07-23)

The overnight (`--hours 8`) soak needs the target laptop occupied and
semi-attended (for fault injection) the whole time; the operator judged that
cost not worth paying right now, since the bug it's guarding against (long-idle
mouse death) is a minor inconvenience (a manual Pico replug) rather than
anything urgent. Deploy already stands on its own merits (camera-verified HID
gate passed) — the fixes are live and are already a strict improvement over
what was running before, soak or no soak. Run `python tools/soak.py --hours 8`
(with operator-driven fault injection during the window) whenever the rig is
free for an unattended stretch; nothing about postponing it puts the current
deploy at risk.

## Follow-ups

- **The soak** — postponed, not abandoned; run `python tools/soak.py --hours 8`
  next time the rig can sit unattended (or semi-attended for fault injection)
  that long. Gate = zero unexplained failure lines, every one mapping to an
  operator-injected fault.
- If the mouse-death symptom recurs even after (a)+(b) (now deployed and live),
  build fix candidate (c) (bridge-side keep-alive) — still deferred, not
  preemptively built.
- Roadmap Phase 1 (model seam) landed the same day on a separate branch,
  independent of this one (disjoint files) — see
  `docs/SESSION_2026-07-22_model_seam_slice_c.md`.
