# appliance/ — Pi 5 + Pico HID appliance

Device-side code for the rig appliance that replaces Pico-over-WiFi HID and host cv2/V4L2
capture. Design + rationale: `docs/PLAN_2026-07-18_pi5_pico_appliance.md`. Motivating flaws:
`docs/FINDINGS_2026-07-18_harness_review.md`.

- `pico/` — CircuitPython firmware for the Pico 2 W (deploy to CIRCUITPY as `code.py`).
- `pi5/`  — code that runs on the Pi 5 appliance.

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

## Bring-up stages (isolate one unknown per stage — see the plan doc)

1. **UART link** ← *you are here.* `pico/stage1_uart_echo.py` + `pi5/stage1_ping_test.py`.
2. HID over UART (Pico USB → main host, not the VM yet).
3. Through libvirt passthrough → win11-agent.
4. Capture alone (ustreamer on the Pi 5).
5. Appliance HTTP API.
6. Integrate into the Holo loop (`ApplianceClient` replaces `R4` + `Camera`).

## Stage 1 quickstart

1. Copy `pico/stage1_uart_echo.py` to CIRCUITPY as `code.py` (keep existing `boot.py`).
2. Wire per the table above.
3. On the Pi 5: `python3 pi5/stage1_ping_test.py` (add `--port` if not `/dev/serial0`).
4. Expect `200/200 OK` and `STAGE 1: PASS`. A dropped command shows as a loud TIMEOUT, a
   desync as a MISMATCH — the observability the WiFi transport never had.
