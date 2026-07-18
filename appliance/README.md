# appliance/ — Pi 5 + Pico HID appliance

Device-side code for the rig appliance that replaces Pico-over-WiFi HID and host cv2/V4L2
capture. Design + rationale: `docs/PLAN_2026-07-18_pi5_pico_appliance.md`. Motivating flaws:
`docs/FINDINGS_2026-07-18_harness_review.md`.

- `pico/` — CircuitPython firmware for the Pico 2 W (deploy to CIRCUITPY as `code.py`).
- `pi5/`  — code that runs on the Pi 5 appliance (`send.py` one-shot; `hid_bridge.py` service).
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

## Stage 3 result (2026-07-18): PASS

Both HID collections confirmed acting **inside Windows** through the full path
(Pi → UART → Pico → USB passthrough → win11-agent), verified via the capture card:
- Mouse: clicked Start → menu opened.
- Keyboard: typed "notepad" + Enter into Start search → Notepad launched → typed
  `STAGE3 HID VIA UART OK` → exact text appeared.

Clears the old "keyboard alive / mouse dead" failure mode — both alive through passthrough.
No new code (reused `stage2_hid.py` + `send.py` + the host capture card). The Pico's control
path (Pi/UART) is independent of where its USB points, so passing the USB to the VM did not
affect control. Also fixed the recurring stale hostdev bus/device pin: the VM's Pico
passthrough now matches by VID:PID (`--config`), so it won't drift on the next replug.

## Stage 5 result (2026-07-18): PASS (HID-only)

`pi5/hid_bridge.py` — a stdlib-http.server + pyserial service holding ONE persistent serial
link to the Pico, serializing seq/ACK'd commands under a lock. Every endpoint returns the
real Pico ACK as JSON `{ok, ack, ms, cmd}`. Verified from the host via curl: `/health` +
`/hid/probe`/`move`/`scroll` all ok with real ACKs; 404/400 error handling correct; and an
end-to-end visible test (`/hid/key?name=enter` + `/hid/type?text=STAGE5 API OK`) landed the
exact text in the VM Notepad. Capture deliberately NOT in the bridge (deferred).

Run it: `ssh -f pi 'setsid python3 ~/hid_bridge.py >~/hid_bridge.log 2>&1 </dev/null'`
(→ http://<pi>:8080). NOTE: `/hid/type` can't carry a literal newline (the UART protocol is
newline-framed); the host-side client (Stage 6) splits text on newlines into `T` segments +
`K enter`, as the old R4.type did. TODO: make hid_bridge a systemd service so it survives
reboots (currently launched via ssh -f).

## Stage 6 result (2026-07-18): PASS

`kvm_agent/hardware/appliance.py: ApplianceClient` — drop-in for the WiFi `R4` (same
move/click/type/key/combo/scroll/drag surface) but backed by the Pi bridge HTTP API; a
failed/dropped command raises `ApplianceError` LOUDLY instead of silently succeeding.
`PicoEnv` now selects the HID client via `CFG.hid_kind` (default `appliance`; `wifi` = retired
R4), keeping the host `Camera` for capture. New config: `CFG.hid_kind`, `CFG.appliance_url`.
Verified end-to-end: `agent_loop_holo.boot()` comes up on the appliance + host capture (no
more dead-WiFi failure), `ENV.r4` typed a line into the VM Notepad through
host→bridge→UART→Pico→passthrough→VM, and `shutdown()` was clean.

`type()` newline handling lives here (splits on `\n` → `T` segments + `K enter`), since the
UART protocol can't frame a literal newline.

## Bring-up stages (isolate one unknown per stage — see the plan doc)

1. **UART link** ✅ DONE — `pico/stage1_uart_echo.py` + `pi5/stage1_ping_test.py`.
2. **HID over UART** ✅ DONE — `pico/stage2_hid.py` + `pi5/send.py` + `host/stage2_verify.py`.
3. **Through libvirt passthrough → win11-agent** ✅ DONE — reused Stage-2 firmware + send.py.
4. **Capture alone (ustreamer on the Pi 5)** — DEFERRED (capture stays host-side for now).
5. **Appliance HTTP API (HID-only)** ✅ DONE — `pi5/hid_bridge.py`.
6. **Integrate into the Holo loop** ✅ DONE — `kvm_agent/hardware/appliance.py` + `env.py` selector.

## Remaining follow-ups (not blocking)

- Stage 4: move capture to the Pi 5 (ustreamer) when desired.
- systemd-ify `hid_bridge.py` on the Pi so it survives reboots (currently `ssh -f`).
- Harness-logic flaws from `docs/FINDINGS_2026-07-18_harness_review.md` (#4 frame-diff signal,
  #7 no reset, #8 fail-open grading, #9 no-progress, #11 refusal-vs-exhaustion) — separate from
  the HID rebuild; still open. Also the `Camera.release()` thread-join race (#5).

## Stage 1 quickstart

1. Copy `pico/stage1_uart_echo.py` to CIRCUITPY as `code.py` (keep existing `boot.py`).
2. Wire per the table above.
3. On the Pi 5: `python3 pi5/stage1_ping_test.py` (defaults to `/dev/ttyAMA0`).
4. Expect `200/200 OK` and `STAGE 1: PASS`. A dropped command shows as a loud TIMEOUT, a
   desync as a MISMATCH — the observability the WiFi transport never had.
