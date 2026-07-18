# appliance/ — Pi 5 + Pico HID appliance

Device-side code for the rig appliance that replaces Pico-over-WiFi HID and host cv2/V4L2
capture. Design + rationale: `docs/PLAN_2026-07-18_pi5_pico_appliance.md`. Motivating flaws:
`docs/FINDINGS_2026-07-18_harness_review.md`.

- `pico/` — CircuitPython firmware for the Pico 2 W (deploy to CIRCUITPY as `code.py`).
- `pi5/`  — code that runs on the Pi 5 appliance.
- `host/` — main-host bring-up/verification tooling (not the production client).

Host-side integration (the client the Holo loop talks to) will live at
`kvm_agent/hardware/appliance.py`, not here.

## Wiring (Pi 5 ⇄ Pico, 3 wires, 3.3V both sides — NO level shifter, NO power rails)

| Signal | Pi 5 header | → | Pico 2 W |
|---|---|---|---|
| Pi5 TX → Pico RX | GPIO14 TXD, pin 8 | → | GP1, pin 2 |
| Pi5 RX ← Pico TX | GPIO15 RXD, pin 10 | → | GP0, pin 1 |
| GND | GND, pin 6 | → | GND, pin 3 |

Cross TX↔RX. Pico is powered/flashed over its own USB; the GND wire is only the shared signal
reference. Enable the Pi 5 header UART (`enable_uart=1` in `/boot/firmware/config.txt`;
`raspi-config` → Serial: login shell NO, hardware YES; reboot). Verify: `ls -l /dev/serial0`.

## Stage 1 result (2026-07-18): PASS

200/200 pings, 0 drops, 0 desyncs, ~2.6ms round trip. Two findings baked into the code:

- **Header UART is `/dev/ttyAMA0`, NOT `/dev/serial0`.** On this Pi 5 (Trixie) `/dev/serial0`
  symlinks to `ttyAMA10` (the SoC PL011) — a different UART not wired to pins 8/10. Writing to
  it goes silently nowhere. `dtparam=uart0=on` already muxes GPIO14/15 to `ttyAMA0`
  (verify: `pinctrl get 14,15` → `a4 ... TXD0/RXD0`). The ping test now defaults to `ttyAMA0`.
- **Read the UART with `in_waiting`, not `read(N)`+timeout.** The first firmware used
  `uart.read(64, timeout=0.05)`, which blocks the full timeout waiting for 64 bytes that never
  come → round trip pinned at ~101ms. Reading `uart.in_waiting` bytes and acting immediately
  dropped it to ~2.6ms (40×). Fixed in `pico/stage1_uart_echo.py`.

## Stage 2 result (2026-07-18): PASS

8/8 checks. Full HID command set (`M/C/R/D/U/H/K/T/X/S` + `PROBE`) runs through the appliance
path (Pi → UART → Pico → USB HID → host) and was verified against real kernel input events
captured with an **exclusive device grab** (EVIOCGRAB) — so nothing touched the desktop.
Absolute moves land exactly (0,0 / 32767,32767 / 16383,16383 for 0,0 / max / center); click,
type, combo, scroll all emit the correct events; every command ACKs (moves ~4ms, typing/scroll
~30ms = real execution time). Firmware: `pico/stage2_hid.py`. Sender: `pi5/send.py`. Verifier:
`host/stage2_verify.py`.

## Bring-up stages (isolate one unknown per stage — see the plan doc)

1. **UART link** ✅ DONE — `pico/stage1_uart_echo.py` + `pi5/stage1_ping_test.py`.
2. **HID over UART** ✅ DONE — `pico/stage2_hid.py` + `pi5/send.py` + `host/stage2_verify.py`.
3. **Through libvirt passthrough → win11-agent** ← *next.* (same commands, now the Pico's USB
   goes to the VM; confirm they act inside Windows.)
4. Capture alone (ustreamer on the Pi 5).
5. Appliance HTTP API.
6. Integrate into the Holo loop (`ApplianceClient` replaces `R4` + `Camera`).

## Stage 1 quickstart

1. Copy `pico/stage1_uart_echo.py` to CIRCUITPY as `code.py` (keep existing `boot.py`).
2. Wire per the table above.
3. On the Pi 5: `python3 pi5/stage1_ping_test.py` (defaults to `/dev/ttyAMA0`).
4. Expect `200/200 OK` and `STAGE 1: PASS`. A dropped command shows as a loud TIMEOUT, a
   desync as a MISMATCH — the observability the WiFi transport never had.
