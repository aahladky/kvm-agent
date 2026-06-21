# Session Summary — EvoCUA Hardware Computer-Use Agent

## 2026-06-18 — ROOT CAUSE FOUND & FIXED (the "clicks don't register" blocker)

**The blocker below ("mouse clicks not registering") was NOT a descriptor *content* or
macOS-button problem. The real root cause, found on the Windows testbed:**

`boot.py` enabled a **no-report-ID** custom absolute mouse on the *same HID interface*
as the keyboard (`usb_hid.enable((abs_mouse, KEYBOARD))`). Mixing a no-report-ID
top-level collection with another collection is **invalid HID** — Windows rejects the
entire interface with **Code 10 ("device cannot start")**, so *neither mouse nor
keyboard worked*. CircuitPython and macOS were lenient enough to *enumerate* it, which
masked the fault and sent earlier sessions chasing wiggle/digitizer red herrings.

**Proof (Windows, `Get-PnpDevice` + `GetCursorPos` + low-level mouse hook):**
- abs_mouse **alone** → HID interface `OK`, absolute positioning pixel-exact.
- abs_mouse **+ keyboard** → MI_03 `Error` Code 10, nothing works.
- stock CircuitPython mouse → `OK` + moves (control).

**FIX (v4):** give the absolute mouse **Report ID 2** (`0x85, 0x02` in the descriptor,
`report_ids=(2,)`) so it coexists with the stock keyboard (Report ID 1). The report-ID
byte is prepended by CircuitPython; `send_report(buf)` and the 5-byte payload are
unchanged. `code.py` needed no logic change for HID.

**Verified end-to-end on the Windows board (.183 / COM7 / drive I:):**
positioning **5/5 pixel-exact**, left-click registers, right-click works, keyboard
coexists — both via direct `send_report` AND the full `r4_client → WiFi → code.py` path.
WiFi-resilience patch (radio toggle + hard-reset-after-N) folded into `code.py` v4 and
confirmed to survive a soft reload.

Files now in the repo (source of truth) and on drive `I:`:
- `boot.py` = v4 (Report ID 2 absolute mouse + keyboard). repo == I: (identical).
- `code.py` = v4 (mouse usage 0x01/0x02, SELFTEST try/except, WiFi resilience). Repo has
  placeholder creds; `I:` has real creds. `SCREEN_W/H=1920x1080` = the capture/pipeline
  coordinate space (NOT the display's native res — absolute HID maps fraction→display).

**macOS VERIFIED (the `.183` board was physically moved from Windows to the Mac):**
Rather than reflash `.224`, the already-fixed `.183` board (v4) was moved to the Mac. It
rejoined WiFi as `.183` (the agent's default IP). Driven over WiFi and watched via the
HDMI capture card:
- right-click empty desktop → the macOS Finder context menu (New Folder / Get Info /
  Change Wallpaper / Sort By / …) rendered **exactly at the click point**. ✅
- left-click → registered (dismissed the menu). ✅
- positioning pixel-accurate, capture↔command coords 1:1 (capture and `SCREEN_W/H` both
  1920x1080). ✅

**The long-standing "clicks don't register on macOS" blocker is RESOLVED on the real
target.** `.224` (old Mac board, v3) is now abandoned/unplugged.

Remaining to actually drive EvoCUA: just run `agent_loop_evocua.py` (R4 defaults to
`.183`, Ollama at `.155`, capture index 0 @ 1920x1080 — all already wired). The brain
(grounding/parse/coords) was verified earlier this session; HID is now verified on macOS.

Diagnostic tooling added this session (Windows HID verification, reusable): `_mon.py`
(WH_MOUSE_LL hook), `_hidtest.py` / `_drv.py` (serial-REPL + GetCursorPos harness),
`_serial.py` (Pico REPL helper).

---

_Original 2026-06-17 notes below — the "open problem" section is now RESOLVED per above._

Date: 2026-06-17. Picks up from `CLAUDE.md`. Single remaining blocker: **mouse click events not registering on macOS** (a HID descriptor/report problem). Everything underneath that is now proven.

---

## TL;DR

| Layer | Status | Where |
|---|---|---|
| Power (Pico→Mac) | ✅ Direct USB (NOT through Dell WD19S dock) | hardware |
| WiFi resilience (CYW43 soft-reboot bug) | ✅ Fixed on Pico #2; needs to land on #1 + repo | `code.py` |
| `boot.py` USB-busy crash | ✅ Fixed (try/except), needs to land on #1 + repo | `boot.py`/`code.py` |
| Absolute mouse positioning | ✅ Pixel-exact (`960,540 → abs 16383,16383`) | hardware verified |
| Keyboard (combo + type) | ✅ Ctrl+A select-all + "hello world" → Notes both worked | hardware verified |
| EvoCUA grounding (relative-coord mode) | ✅ Major fix — model now grounds full screen accurately | `evocua.py` |
| EvoCUA prompt / S2 schema | ✅ Now verbatim from official `prompts.py` | `evocua.py` |
| Coord back-projection from `frame.shape` | ✅ No more hardcoded 1920×1080 | `agent_loop_evocua.py` |
| `r4_client` keyboard protocol (host→Pico names) | ✅ Rewritten + isolated wire-format test PASSED | `r4_client.py` |
| **Physical mouse CLICK on macOS** | ❌ **BLOCKER — buttons do not register** | needs descriptor/report fix |
| Q4 vs Q8 quant | ⏸ NOT the problem (was a misdiagnosis when grounding was bad) | — |

---

## The one open problem: clicks on macOS

**Symptom (Mac, multiple independent tests):**
- Move + left-click on TV dock icon → cursor visibly on icon (tooltip "TV" appeared), TV did **not** launch. Repeated 4× by the loop.
- Long-hold left-click (180 ms via D/U) → still no launch.
- Right-click center desktop → no context menu.
- Left-drag on desktop → no rubber-band selection rectangle.
- **In every case the cursor positioned correctly** — only the button events are being ignored.

**What this rules out:**
- Position / coordinate mapping (cursor is provably on target).
- Click duration (180 ms held = same result as 30 ms).
- macOS pointer acceleration (absolute HID bypasses it; positioning is exact).
- WiFi / power / Pico crashing (board stays up, commands arrive — `CMD: 'C'` prints in serial).

**Leading hypothesis:** the custom absolute-mouse HID descriptor in `boot.py` enumerates and accepts reports, but **macOS isn't treating its button bits as click events**. Likely culprits, in order:
1. **Click-without-motion ignored by macOS.** A button press at a *freshly teleported* absolute position may be filtered. Fix: on click, send down → tiny 1px wiggle → up. Pure `code.py` change, cheap to try first.
2. **Descriptor needs to declare the device as a digitizer/touch instead of mouse.** Some absolute-mouse projects (Pi-KVM, hid-relay) use a digitizer descriptor specifically because macOS handles absolute mice oddly.
3. **Report-rate / report-protocol detail** (boot vs. report protocol, missing Tip Switch usage for digitizers, etc.).

**Windows isolation was inconclusive** — repeated serial pokes (Ctrl-C/Ctrl-D) and the soft-reboot WiFi bug muddied the tests; never got a clean Windows click result we could trust. Don't rely on the Windows screenshots.

---

## Files changed this session

### `evocua.py`
- **`COORD_TYPE = "relative"`** (was implicitly absolute). The model is trained on a 0..999 normalized grid; absolute-pixel mode caused catastrophic grounding errors on large-coord targets (Trash icon at `x≈1860` got mapped to `x=943` = dead center of dock). Relative mode fixed all 5 spread-out probe targets including the previously-broken right-edge and corner cases.
- Swapped the condensed prompt for the **verbatim official S2 prompt + tool schema** from `evocua/prompts.py` (`S2_SYSTEM_PROMPT`, `S2_DESCRIPTION_PROMPT_TEMPLATE`, `S2_ACTION_DESCRIPTION`, `build_s2_tools_def`).
- `max_tokens` 1024 → 4096 (this is a Thinking model; 1024 truncates).
- `_scale()` branches on `COORD_TYPE`: relative → `× target/999`; absolute → processed-pixel scaling.

### `agent_loop_evocua.py`
- Coord back-projection now uses **live `frame.shape`** (`fw, fh`) instead of hardcoded `CAP_W, CAP_H`. Capture dims and the Pico's `SCREEN_W/H` are now the single source of truth.
- Startup prints `capture dims: WxH` so any mismatch is loud.
- Dropped the dead Arduino-numeric `KEYMAP`; the `key` branch passes names through to the rebuilt `r4_client`.
- `GOAL` left at "Click the TV icon in the Dock." for click testing.

### `r4_client.py`
- Old client sent **Arduino numeric keycodes** (`K176` for Enter, `X128,97` for Ctrl+A). Pico firmware expects **names** (`Kenter`, `Xctrl+a`). Total keyboard failure with no error — moves worked, no key did. Fixed.
- New `norm_key()` aliases EvoCUA/xdotool names → Pico vocabulary (`Return→enter`, `Page_Up→pageup`, `super/meta→gui`, strips `_L/_R`, `del→delete`).
- Isolated wire-format test (now deleted) verified every command: `Kenter`, `Ka`, `Kpageup`, `Kgui`, `Xctrl+s`, `Xalt+tab`, `Thello`, `Kenter`, `Tworld`. **PASS.**

### `I:\code.py` (Pico #1 LIVE only — NOT in repo yet)
- **`SELFTEST = False`** — quick hack to stop the boot self-test from crashing `code.py` before WiFi came up on a slow-to-enumerate host.
- **Proper fix needed in repo:** wrap the SELFTEST block in `try/except` so `OSError: USB busy` is non-fatal but the self-test still runs when USB *is* ready.

### `K:\code.py` (Pico #2 LIVE only — NOT in repo yet)
- All of the above, plus **WiFi resilience patch** in `connect_wifi()`:
  - Toggle `wifi.radio.enabled = False/True` (with 0.5 s) before connecting — works around the CYW43 "Unknown failure 1" soft-reboot bug that hits every time `code.py` auto-reloads after an edit.
  - After 5 consecutive failures: `microcontroller.reset()` for a clean hard reboot.
  - Confirmed self-recovery: #2 came back at `.224` in ~3 s after the reload that triggered the bug.

### Bugs uncovered & root-caused (not all fixed in repo)
- **WiFi RST early in session** = transient CircuitPython socket-pool exhaustion (leaked sockets from crashed client runs + rapid reconnect probing). Power-cycle clears; `r4_client` could `try/finally` close on crash to avoid being the cause.
- **WiFi bounce loop later** = brownout reset, caused by **Dell WD19S dock** sharing power budget. Direct USB to Mac → stable.
- **Pico unreachable on Windows** = `OSError: USB busy` in the boot self-test (host hadn't configured HID interface yet) → unhandled → `code.py` crashed before WiFi.
- **"WiFi FAILED: Unknown failure 1"** = CircuitPython CYW43 soft-reboot bug — radio state doesn't reset on reload, so reconnect fails. The radio-toggle workaround fixes it.

---

## What's deployed where (state of CIRCUITPY drives + repo)

| File | Repo `C:\Dev\vllm\` | `I:\` (Pico #1, currently on Mac) | `K:\` (Pico #2, on Windows .224) |
|---|---|---|---|
| `boot.py` | v2 (5-byte, no Report ID) | identical | identical |
| `code.py` | v2 (handler) | v2 + `SELFTEST=False` hack | v2 + `SELFTEST=False` + WiFi-resilience patch |
| `agent_loop_evocua.py` | ✅ updated | n/a | n/a |
| `evocua.py` | ✅ updated (relative coord, verbatim prompts, 4096 tokens) | n/a | n/a |
| `r4_client.py` | ✅ updated (name-based key protocol) | n/a | n/a |

**Repo drift to fix:**
1. `code.py`: replace the `SELFTEST=False` hack with `try/except` around the self-test block (so it self-skips when USB not ready but still runs when it is).
2. `code.py`: port the **WiFi-resilience patch from K:** (radio toggle + 5-fail hard reset) into the repo so #1 gets it on the next deploy.
3. Optional: `r4_client.py` add `try/finally` to close socket on crash, so aborted runs don't leak Pico connections.

---

## Two-Pico A/B rig (built this session, ready to use)

- **Pico #1** — currently on **Mac** (the target), IP `192.168.0.183`, DHCP-reserved on UniFi.
- **Pico #2** — currently on **Windows** (this desktop), IP `192.168.0.224`. Drive `K:`, COM port `COM8`. `adafruit_hid` already in `K:\lib`.
- Both run byte-identical firmware (cloned from #1).
- WiFi-resilience fix means `code.py` edits no longer brick the boards — they auto-recover within ~3–30 s after auto-reload.

Use #2 as the testbed for descriptor/report experiments (edit `K:\boot.py` or `K:\code.py`, it auto-reloads, ping `.224`, test). Use #1 as the control on the Mac.

---

## Recommended next steps (in order)

1. **Click "wiggle" experiment.** Edit `K:\code.py` `click()` to send: down → `move_to(_cur_x+1, _cur_y)` → tiny sleep → `move_to(_cur_x-1, _cur_y)` → up. Move #2 to the Mac (or #1 stays on Mac and just patch `I:` too). If a Mac dock click *launches* the app, descriptor is fine — macOS just needs motion-during-button.
2. **If wiggle fails:** swap `boot.py` to a **digitizer descriptor** (HID usage page 0x0D, Stylus, with Tip Switch). Pi-KVM has a known-working version we can port.
3. **Sync the firmware fixes back to the repo** (`SELFTEST` try/except + WiFi resilience), so the next session doesn't relearn this.
4. **`CLAUDE.md` update** — the "NEXT STEPS" list there is now stale (corner test done, capture wired, grounding solved). Replace with: clicks-on-Mac descriptor fix + repo sync.

---

## Coordinate sanity check (from `_ground_probe.py`, relative mode, on a real Mac screenshot)

| Target | Model coord (relative→scaled) | Actual | Verdict |
|---|---|---|---|
| Apple menu (TL corner) | `[99, 14]` | ~`(15, 12)` | ✓ |
| Finder (far-left dock) | `[103, 1035]` | ~`(70, 985)` | ✓ on icon |
| Safari (mid dock) | `[453, 1031]` | mid-dock | ✓ region |
| Trash (far-right dock) | `[1812, 1033]` | ~`(1860, 1010)` | ✓✓ (was `[943, 944]` in absolute mode — broken) |
| Clock (TR menu bar) | `[1852, 12]` | ~`(1850, 12)` | ✓✓ |

Q4 grounding is **good enough**. Don't compile Q8 unless real-world tasks miss small targets.
