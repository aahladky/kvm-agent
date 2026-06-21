# FINDINGS 2026-06-21 — Pico `10054` "wedge" root-caused + FIXED

Resolves the open "Pico-offline / nothing but trouble" thread (SESSION_2026-06-21 §5, and the
recurring `WinError 10054` / "needs a power-cycle" pain). **The HID hardware was never the problem.**

## Symptom
- Multi-step rollouts (and the Open WebUI smoke) failed at the first executive command: the first
  `launch`/`type` was sent with **no error but had no effect** on the target, then every subsequent
  command got `ConnectionResetError(10054)`. R4's single auto-reconnect didn't help.
- A bare TCP connect to `:8000` always **succeeded**, so every "is the Pico up?" probe looked fine
  while nothing actually worked — the classic trap (lwIP answers even when the Python loop is stuck).
- Once in the bad state it was **persistent**: a 35s wait did not clear it; only a power-cycle did.

## What it was NOT (all proven this session)
- **HID / firmware logic: good.** Driving R4 directly (bypassing planner/executive) injected mouse +
  keyboard perfectly — Win+R opened Run, moves landed, **zero resets**, stable active and through 30s
  idle. Captured frames confirmed it (Run dialog opened on the target).
- **WiFi power-save: ruled out.** Boot serial shows `WiFi power-save DISABLED (cyw43.PM_DISABLED)` —
  the `cyw43` call DOES execute on CircuitPython 10.2.1 (it's a real shared-binding, not the no-op
  first suspected).
- **The `agent_server` "check Pico injector" pre-connect:** a contributing trigger (removed), but not
  the whole story — the run still failed after it was gone.

## Root cause (CONFIRMED, by reproducing one variable at a time)
`code.py`'s serve loop did `conn.settimeout(None)` — block on `recv_into` **forever**. When a
connection half-opens (the client process is killed, a Wi-Fi blip, or a stray connect+close), the loop
never sees the dead peer and never returns to `accept()`. New connections are accepted by lwIP (so TCP
"looks up") but are **never serviced** → the host's first command silently buffers into the dead socket
(no error, no effect), then RSTs (`10054`). Because the loop is wedged, R4's reconnect lands in the same
unserviced state. Only a power-cycle (or the OS finally tearing down the half-open) recovered it.

Aggravators that kept re-wedging it during debugging: every `Stop-Process` on `agent_server` abandons
its Pico connection (half-open), and the redundant pre-check opened+closed an extra connection. In
**normal** operation (one R4 reused across goals, cleanly closed) half-opens are rare — but a single one
was unrecoverable, which is the entire "nothing but trouble" story.

## The fix
- **`code.py` (FLASH THIS):** add `CONN_TIMEOUT = 45`; use `conn.settimeout(CONN_TIMEOUT)` instead of
  `None`; wrap `recv_into` so a timeout/stuck peer recycles the socket back to `accept()`. The loop now
  **always** returns within `CONN_TIMEOUT` → the wedge is structurally impossible; worst case is ~45s of
  self-recovery, never a power-cycle. 45s comfortably exceeds the longest real inter-command gap
  (planning + a cold verify pass ~ up to 30s).
- **`r4_client.py`:** send retries 2 → 4 with 0.3/0.6/0.9s backoff, so a transient RST is masked across
  the Pico's quick recycle instead of failing the rollout.
- **`agent_server.py`:** removed the wedging "check Pico injector" pre-connect; offline detection now
  rides on the Executive's own `R4()` (fast-fails in 5s).

## Confirmation
- Flashed by copying `code.py` to CIRCUITPY (drive `I:` when the Pico is on the orchestrator; serial
  console = `COM7`). CircuitPython auto-reloaded → un-wedged + fixed.
- Serial (COM7) live test: commands accepted, `move -> …`, clean `closed → waiting for connection…`,
  **no 10054**. Boot shows `WiFi OK` + `WiFi power-save DISABLED`.
- Full Open WebUI smoke (`:8088`, goal "Open Notepad and type: hello from open-webui"): **done in 8.6s,
  0 re-plans**, `verify: true` (qwen2.5vl read the text off the captured screen). `runs/goal_052846.json`.

## Still open / optional (none are blockers)
- **No per-command ACK.** A half-open that happens *mid*-rollout can still cost that one rollout
  (recovered on the next, no power-cycle). If it ever matters, add a 1-byte ack to the protocol and have
  `r4_client` wait for it — then it can distinguish "delivered" from "buffered into a dead socket."
- **Kill the stale `pico_serial_log.py` stuck on the target's COM3** (it hogs the port + spews
  `ClearCommError`).
- Nice-to-have: `agent_server` clean-shutdown (close `_EXEC`) so dev restarts don't leave half-opens.

## Handy: viewing the Pico serial
USB-CDC at 115200 on whatever machine the Pico is plugged into (was `COM7` on the orchestrator this
session). Mu Editor → **Serial** button; or `python -m serial.tools.miniterm COM7 115200` (`Ctrl+]`
quits); or PuTTY (Serial / COM7 / 115200). In the console: `Ctrl+C` → REPL, `Ctrl+D` → reload (reprints
the boot sequence). Only one program can hold the port at a time — close any stale monitor first.
