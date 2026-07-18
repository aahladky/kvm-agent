# Plan: Pi 5 + Pico HID appliance (replaces Pico-over-WiFi + host cv2 capture)

Decided 2026-07-18 with Aaron. Supersedes the Pico-over-WiFi HID transport and the host-side
cv2/V4L2 capture for the rig. Motivated by the structural flaws in
`docs/FINDINGS_2026-07-18_harness_review.md` (esp. #1 no-ACK action channel, #2 reconnect
masks dead HID, #5 capture-thread SIGABRT, and the WiFi failure class).

## Why this shape

- **Pico can't be dropped, Pi 5 can't be the gadget.** Pi 5 has no reliable USB-device/gadget
  mode (Code-10 reports, unsupported by PiKVM). Pico is a rock-solid USB HID gadget. So each
  board does what it's good at.
- **The flaky part was never the Pico-as-gadget — it was the WiFi control transport and the
  fire-and-forget protocol.** Both get deleted.

## Architecture

```
  Holo loop (main host)
      │  HTTP/WS  (wired Ethernet)
      ▼
  Pi 5 appliance ───────────────┐
   • capture: HDMI → JPEG stills │  wired UART (3.3V, GPIO or USB-serial)
   • HID bridge: API → UART      │
      │                          ▼
      │                       Pico 2 W  (WiFi stack DELETED)
      │                        • UART command loop + per-command ACK
      │                        • USB HID gadget (absolute mouse + keyboard)
      │                             │ USB
      ▼                             ▼
  (fetches snapshots)          target: main-host USB → libvirt passthrough → win11-agent VM
                                     (future: Pico USB → a physical target; Pi5 captures its HDMI)
```

One mouse-move's path: `Holo → HTTP → Pi5 → UART → Pico → USB → host → passthrough → VM`.
Every hop is wired Ethernet / wired UART / wired USB. **No WiFi anywhere.**

## What this fixes vs what stays ours

Fixes (from the review doc): #1 (UART request/response = real ACK), #2 (a dead Pico/link is a
missing ACK, not a silent success; + keyboard LED-readback liveness probe), #3 (defined API,
not a hand-rolled combo parser), #5 (host stops managing a local capture device), and the
entire WiFi failure class (power-save disassoc, DHCP drift, replug-hang).

Does NOT fix (still harness-layer, tracked separately): #4 (bogus frame-diff tool-result),
#7 (no reset between tasks), #8 (fail-open grading), #9 (no no-progress detection), #11
(refusal-vs-exhaustion scoring). And the inherent limit: **mouse actions have no per-action
confirmation at any layer — the ACK proves "Pico sent the HID report," not "the OS moved the
cursor." The screen capture remains the only mouse ground truth.**

## Wire protocol (Pi 5 ⇄ Pico, over UART)

Line-framed ASCII, sequence-numbered, **every command gets an ACK** (this is the core fix):

```
  host→pico:  <seq> <CMD> <args>\n      e.g.  "42 M 960,540\n"   "43 T hello\n"
  pico→host:  <seq> OK\n   |   <seq> ERR <reason>\n
```

- Controller blocks for the matching `<seq>` ACK with a timeout. Missing/mismatched/timed-out
  ACK = **detected** failure (surfaced to the caller), never a silent pass.
- Keyboard liveness: `<seq> PROBE\n` → Pico toggles/reads a lock LED and replies
  `<seq> OK caps=<0|1> num=<0|1>\n`, confirming the keyboard collection is alive *at the target*
  (generalizes the existing caps-lock-readback trick). No mouse equivalent exists — documented.
- Commands map 1:1 from the current firmware (M/C/R/D/U/K/T/X/S/H) so the Pico-side action code
  barely changes; only the transport + ACK wrapper is new.

## Pi 5 appliance service (HTTP API the main host calls)

Mirrors exactly what the Holo loop needs (`agent_loop_holo._execute` + `_frame_png`):

- `GET  /snapshot`            → JPEG of the current frame
- `POST /hid/move?x&y`        → absolute move (returns Pico ACK status)
- `POST /hid/click` `/rclick` `/down` `/up`
- `POST /hid/type`  (body: text)   `/hid/key?name`   `/hid/combo?spec`   `/hid/scroll?ticks`
- `GET  /hid/probe`          → keyboard liveness (LED readback)
- `GET  /health`             → {ethernet, uart_link, pico_acking, capture_ok}

Every HID call returns the real ACK result, so the host can act on a genuine success/failure
instead of the current unconditional `""`.

## Code seam (main host)

New `kvm_agent/hardware/appliance.py: ApplianceClient` exposing the SAME surface the loop uses
today (`move/click/type/key/combo/scroll` + `png_bytes`/`snapshot`), so `agent_loop_holo`'s
`_execute` and `_frame_png` swap over with minimal change. Keep `R4`/`Camera` as a fallback
path. Add `CFG.appliance_url`.

## Open implementation choices (recommendations; Aaron's call)

1. **UART link:** direct GPIO-UART (Pi5 GPIO14/15 ↔ Pico GPIO, cross TX/RX + gnd) — one fewer
   part — vs a USB-serial dongle. *Rec: direct GPIO.* (Bring-up note: Pi 5 UART device naming
   changed under RP1; confirm the device path in stage 1.)
2. **Capture device on the Pi 5:** reuse the existing MS2109 USB card (known-good, USB-A on the
   Pi 5) vs a TC358743 CSI bridge (lower latency). *Rec: reuse the MS2109* — latency is noise at
   our cadence.
3. **Snapshot server:** run stock **ustreamer** (`--encoder=CPU`, proven, gives `/snapshot` for
   free) and only custom-build the tiny Pico UART bridge, vs an all-custom capture service.
   *Rec: ustreamer for capture + our own HID bridge.* (kvmd/full PiKVM won't run on Pi 5;
   ustreamer alone is separable and does run.)

## Incremental bring-up (isolate one unknown per stage — do NOT stack)

1. **UART link only.** Pi 5 ↔ Pico echo/ACK a `PING`. Proves framing + ACK + the wire. No HID,
   no capture, no VM.
2. **HID over UART.** Pico USB → main host directly (not the VM yet). Send move/type over UART,
   watch it act on the host. Proves gadget + command path. No capture, no VM.
3. **Through passthrough.** Pico USB → VM via libvirt hostdev. Same commands, confirm they reach
   win11-agent. Proves the passthrough hop with the new firmware.
4. **Capture alone.** ustreamer on the Pi 5 serving `/snapshot` of the target HDMI; fetch one,
   eyeball it. Proves capture independently.
5. **Appliance API.** Thin service wrapping snapshot + UART-HID with ACK surfaced; `curl` it.
6. **Integrate.** `ApplianceClient` replaces `R4`+`Camera`; run `calc_basic`. Full loop.

Each stage independently verifiable; a failure is localized to that stage.

## Hardware Aaron needs on hand

Pi 5 (have), Pico 2 W (have), an HDMI capture device for the Pi 5 (the MS2109 card, if freed
from the host), a UART link (jumper wires for direct GPIO, or a USB-serial dongle), wired
Ethernet for the Pi 5, and the usual power. Soldering only if going direct-GPIO and wanting it
permanent.
