#!/usr/bin/env python3
"""
kvm_rig_mcp — stdio MCP server that exposes the physical KVM rig (HDMI capture for
observation, UNO R4 / Pico HID for action) as flat, model-agnostic computer-control
tools. Any vision-capable MCP client (Claude Desktop, Cursor, etc.) can launch it over
stdio, see the target screen, and act on it.

Reuses the rig you already have:
  - observation: Camera from the OSWorld-compat env (MSMF backend + first-frame discard,
    i.e. the fix for the 2026-06-19 YUY2 ghosting). We do NOT reimplement capture.
  - action: R4 from pico_client (auto-reconnect, char/space/newline handling, combos).

This is a DIFFERENT server from evocua_mcp_server.py: no agent loop, no job model — one
tool per primitive, each action returns a fresh screenshot so the driving model runs a
per-step closed loop (look -> act -> look) instead of open-loop N-step planning.

COORDINATE CONTRACT (read this):
  The server serves screenshots at OUTPUT_W x OUTPUT_H and expects every tool coordinate
  in THAT space (top-left origin). It scales coords back to the rig's native resolution
  before injecting via the Pico. Default output is 1280x720 — deliberately at/below the
  ~1568px long-edge cap that Anthropic-family clients downscale images to, so frames pass
  through to the model untouched and the coordinates it emits round-trip correctly. If you
  raise OUTPUT to native (1920x1080), a client that re-downscales will hand the model a
  smaller image, its coords will be in THAT smaller space, and every click will land wrong.
  Lower OUTPUT also tends to ground better. Keep the model in OUTPUT space everywhere;
  _to_native() owns the OUTPUT -> 1920x1080 mapping.
"""

import sys
import time
import threading
from typing import Annotated, Literal, Optional

import anyio
import cv2
from pydantic import Field
from mcp.server.fastmcp import FastMCP, Image

# Allow launching this file directly (`python kvm_rig_mcp.py`) in addition to
# `python -m kvm_agent.hardware.kvm_rig_mcp`. Running a script directly puts the script's
# own folder on sys.path, not the project root, so bare `import kvm_agent` fails. Add the
# project root — two levels up from this file: hardware/ -> kvm_agent/ -> <root>.
from pathlib import Path
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Capture lives next to PicoEnv. The uploaded file is env.py but its docstring calls
# itself pico_env.py — accept either module name.
try:
    from kvm_agent.hardware.env import Camera
except ImportError:  # pragma: no cover
    from kvm_agent.hardware.pico_env import Camera
from kvm_agent.hardware.pico_client import R4

# --- geometry ---------------------------------------------------------------
NATIVE_W, NATIVE_H = 1920, 1080      # capture card native (must equal Pico SCREEN_W/H)
OUTPUT_W, OUTPUT_H = 1280, 720       # what the model sees and grounds against
SCALE_X = NATIVE_W / OUTPUT_W
SCALE_Y = NATIVE_H / OUTPUT_H

# --- timing -----------------------------------------------------------------
CAM_INDEX = 0
SETTLE_S = 1.2                        # pause after an action before re-capturing (UI redraw)

mcp = FastMCP("kvm_rig_mcp")


# ---------------------------------------------------------------------------
# Hardware singleton
# ---------------------------------------------------------------------------
class _Rig:
    """Owns the exclusive hardware (one capture card, one Pico socket) for the server's
    lifetime. Created lazily on first tool call so tool-listing and --help work with the
    rig powered off. A single lock serializes all hardware access (the model issues calls
    one at a time anyway; this is belt-and-suspenders against overlap)."""

    _inst: Optional["_Rig"] = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cam = Camera(CAM_INDEX, NATIVE_W, NATIVE_H)   # may raise SystemExit if card busy
        try:
            self.r4 = R4()
        except BaseException:
            try:
                self.cam.release()    # don't orphan the capture device if the Pico is offline
            except Exception:
                pass
            raise

    @classmethod
    def get(cls) -> "_Rig":
        with cls._init_lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def capture_output_png(self) -> bytes:
        """Latest frame, scaled to OUTPUT_W x OUTPUT_H, PNG-encoded. Caller holds self.lock."""
        frame = self.cam.read()
        if (OUTPUT_W, OUTPUT_H) != (NATIVE_W, NATIVE_H):
            frame = cv2.resize(frame, (OUTPUT_W, OUTPUT_H), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", frame)
        return buf.tobytes()


def _to_native(x: int, y: int) -> tuple[int, int]:
    """OUTPUT-space pixel -> native (1920x1080) pixel for the Pico."""
    return int(round(x * SCALE_X)), int(round(y * SCALE_Y))


def _hw_error(e: BaseException) -> str:
    """Turn a hardware init failure into an actionable message for the model/operator."""
    name = type(e).__name__
    msg = str(e)
    if isinstance(e, SystemExit) or "frame" in msg.lower():
        return ("Error: the capture card delivered no frames. Another process (OBS, a "
                "browser tab, another capture script) is probably holding the device — "
                "free it and retry.")
    if name in ("ConnectionRefusedError", "TimeoutError", "OSError",
                "ConnectionResetError", "socket.timeout"):
        return (f"Error: can't reach the Pico HID listener ({name}: {msg}). Check it is "
                "powered, on the network at the configured IP/port, and not already "
                "connected by another client.")
    return f"Error initializing rig: {name}: {msg}"


async def _action(note: str, body, settle: float = SETTLE_S) -> list:
    """Shared path for every tool: ensure the rig is up, run `body(rig)` (the HID ops)
    under the rig lock in a worker thread, settle, capture, and return note + screenshot.
    On hardware failure returns a single actionable text block (no image)."""
    try:
        rig = await anyio.to_thread.run_sync(_Rig.get)
    except (Exception, SystemExit) as e:
        return [_hw_error(e)]

    def _run() -> bytes:
        with rig.lock:
            body(rig)
            if settle:
                time.sleep(settle)
            return rig.capture_output_png()

    try:
        png = await anyio.to_thread.run_sync(_run)
    except Exception as e:
        return [f"Error during action: {type(e).__name__}: {e}"]
    return [note, Image(data=png, format="png")]


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------
@mcp.tool(
    name="rig_screen_capture",
    annotations={"title": "Capture target screen", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def rig_screen_capture() -> list:
    """Return the current screen of the controlled machine as a PNG, WITHOUT taking any
    action. Use this to look before you act and to confirm the result of an action.

    Returns: [text, image] where the text states the resolution. The image is
    {OUTPUT_W}x{OUTPUT_H}, top-left origin; give every coordinate to the other tools
    within that range.
    """
    note = (f"Screen is {OUTPUT_W}x{OUTPUT_H}, top-left origin. Use coordinates within "
            f"this range for all click/move/drag/scroll calls.")
    return await _action(note, lambda rig: None, settle=0.0)


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------
@mcp.tool(
    name="rig_click",
    annotations={"title": "Click", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_click(
    x: Annotated[int, Field(ge=0, le=OUTPUT_W, description="X in screen pixels (0..%d)" % OUTPUT_W)],
    y: Annotated[int, Field(ge=0, le=OUTPUT_H, description="Y in screen pixels (0..%d)" % OUTPUT_H)],
    button: Annotated[Literal["left", "right"], Field(description="Mouse button")] = "left",
    clicks: Annotated[int, Field(ge=1, le=3, description="1=single, 2=double, 3=triple")] = 1,
) -> list:
    """Move the pointer to (x, y) and click. Prefer the keyboard tools (rig_type_text,
    rig_press_key, rig_hotkey) for navigation and text entry when possible — clicking
    depends on pixel-accurate coordinates and is the most error-prone action. Returns
    [text, screenshot] showing the result.
    """
    nx, ny = _to_native(x, y)

    def body(rig: _Rig) -> None:
        rig.r4.move(nx, ny)
        for _ in range(clicks):
            rig.r4.rclick() if button == "right" else rig.r4.click()

    label = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}[clicks]
    return await _action(f"{label} ({x},{y}) with {button} button.", body)


@mcp.tool(
    name="rig_move_mouse",
    annotations={"title": "Move pointer", "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def rig_move_mouse(
    x: Annotated[int, Field(ge=0, le=OUTPUT_W, description="X in screen pixels")],
    y: Annotated[int, Field(ge=0, le=OUTPUT_H, description="Y in screen pixels")],
) -> list:
    """Move the pointer to (x, y) without clicking (e.g. to reveal a hover state). Returns
    [text, screenshot]."""
    nx, ny = _to_native(x, y)
    return await _action(f"Moved pointer to ({x},{y}).", lambda rig: rig.r4.move(nx, ny))


@mcp.tool(
    name="rig_scroll",
    annotations={"title": "Scroll wheel", "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_scroll(
    amount: Annotated[int, Field(ge=-50, le=50,
                                 description="Wheel ticks: positive scrolls UP, negative DOWN")],
    x: Annotated[Optional[int], Field(ge=0, le=OUTPUT_W,
                                      description="Optional: move here before scrolling")] = None,
    y: Annotated[Optional[int], Field(ge=0, le=OUTPUT_H, description="Optional Y for the move")] = None,
) -> list:
    """Scroll the wheel `amount` ticks (positive up, negative down). If x and y are given,
    move there first (to scroll a specific pane). Returns [text, screenshot]."""
    move = x is not None and y is not None
    nx, ny = _to_native(x, y) if move else (None, None)

    def body(rig: _Rig) -> None:
        if move:
            rig.r4.move(nx, ny)
        rig.r4.scroll(amount)

    where = f" at ({x},{y})" if move else ""
    return await _action(f"Scrolled {amount} ticks{where}.", body)


@mcp.tool(
    name="rig_drag",
    annotations={"title": "Drag", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_drag(
    from_x: Annotated[int, Field(ge=0, le=OUTPUT_W, description="Start X")],
    from_y: Annotated[int, Field(ge=0, le=OUTPUT_H, description="Start Y")],
    to_x: Annotated[int, Field(ge=0, le=OUTPUT_W, description="End X")],
    to_y: Annotated[int, Field(ge=0, le=OUTPUT_H, description="End Y")],
) -> list:
    """Press the left button at (from_x, from_y), move to (to_x, to_y), and release — a
    left-button drag (select text, move a slider, drag an item). Returns [text, screenshot].
    Note: chorded/held-modifier drags (e.g. shift-drag) are NOT supported by the Pico
    client, only a plain left-button drag."""
    fx, fy = _to_native(from_x, from_y)
    tx, ty = _to_native(to_x, to_y)
    return await _action(
        f"Dragged ({from_x},{from_y}) -> ({to_x},{to_y}).",
        lambda rig: rig.r4.drag(fx, fy, tx, ty),
    )


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------
@mcp.tool(
    name="rig_type_text",
    annotations={"title": "Type text", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_type_text(
    text: Annotated[str, Field(min_length=1, max_length=2000,
                               description="Text to type. A trailing newline presses Enter; "
                                           "embedded newlines press Enter between lines.")],
) -> list:
    """Type a string at the current focus. Handles digits, capitals, punctuation, and
    spaces (US layout); newlines are sent as Enter. This is the preferred way to enter
    text — much more reliable than clicking an on-screen keyboard. Make sure the right
    field is focused first (click it or Tab to it). Returns [text, screenshot]."""
    preview = text if len(text) <= 60 else text[:57] + "..."
    return await _action(f"Typed: {preview!r}", lambda rig: rig.r4.type(text))


@mcp.tool(
    name="rig_press_key",
    annotations={"title": "Press a key", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_press_key(
    key: Annotated[str, Field(min_length=1, max_length=20,
                              description="Key name: enter, esc, tab, space, backspace, delete, "
                                          "up, down, left, right, home, end, pageup, pagedown, "
                                          "f1..f12, etc.")],
) -> list:
    """Tap a single named key. Use for navigation and editing keys (Enter, Tab, Esc,
    arrows, Backspace, Delete, Page Up/Down, function keys). For ordinary characters use
    rig_type_text; for chords (Ctrl+S) use rig_hotkey. Returns [text, screenshot]."""
    return await _action(f"Pressed {key}.", lambda rig: rig.r4.key(key))


@mcp.tool(
    name="rig_hotkey",
    annotations={"title": "Key chord", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_hotkey(
    keys: Annotated[list[str], Field(min_length=2, max_length=4,
                                     description='Keys held together, e.g. ["ctrl","s"], '
                                                 '["ctrl","shift","t"], ["alt","tab"]. '
                                                 'Use "gui" for the Windows/Command key.')],
) -> list:
    """Press a key combination (held together, released together): Ctrl+S, Ctrl+Shift+T,
    Alt+Tab, etc. Order the modifiers first. Returns [text, screenshot]."""
    combo = "+".join(keys)
    return await _action(f"Pressed {combo}.", lambda rig: rig.r4.combo(combo))


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
@mcp.tool(
    name="rig_wait",
    annotations={"title": "Wait", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True},
)
async def rig_wait(
    seconds: Annotated[float, Field(ge=0.1, le=30.0, description="Seconds to wait")] = 2.0,
) -> list:
    """Wait without acting, then return a fresh screenshot — for pages or dialogs that are
    still loading. Returns [text, screenshot]."""
    return await _action(f"Waited {seconds:g}s.", lambda rig: None, settle=seconds)


# ---------------------------------------------------------------------------
# OPTIONAL: semantic clicking via your grounder (UI-TARS). Unwired by default.
# ---------------------------------------------------------------------------
def _ground(image_png: bytes, description: str) -> Optional[tuple[int, int]]:
    """CONTRACT: given the current OUTPUT-resolution screenshot (PNG, OUTPUT_W x OUTPUT_H)
    and a natural-language target description, return (x, y) IN OUTPUT-RESOLUTION PIXELS,
    or None if the target wasn't found.

    Wire this to your EXISTING UI-TARS path — do not reimplement the prompt/parse. The
    grounder client, coordinate parsing, and 7-class failure attribution already live in
    measure_grounding.py; import and call that here. Sketch:

        from kvm_agent.grounding.measure_grounding import ground_once   # your harness's entry
        res = ground_once(image_png, description)
        if res is None or res.failure_class is not None:
            return None
        gx, gy = res.x, res.y          # UI-TARS coord space (typically normalized 0..1000)
        return int(gx / 1000 * OUTPUT_W), int(gy / 1000 * OUTPUT_H)   # -> OUTPUT pixels

    Send UI-TARS the SAME image bytes the model saw, so its coordinates are in OUTPUT space;
    _to_native() then maps OUTPUT -> 1920x1080 for the Pico. Run this in the worker thread
    (it already is — rig_click_element offloads the whole closure), since UI-TARS inference
    is blocking.
    """
    raise NotImplementedError(
        "rig_click_element needs _ground() wired to your UI-TARS grounder. See the "
        "docstring; reuse measure_grounding.py instead of reimplementing the prompt/parse."
    )


@mcp.tool(
    name="rig_click_element",
    annotations={"title": "Click element by description", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def rig_click_element(
    description: Annotated[str, Field(min_length=1, max_length=200,
                                      description="What to click, in words: visible text on the "
                                                  "control, its role, and/or nearby labels — "
                                                  "e.g. 'the blue Sign in button', 'the equals "
                                                  "key', 'the search box at the top'.")],
    button: Annotated[Literal["left", "right"], Field(description="Mouse button")] = "left",
) -> list:
    """Click a target named in natural language. The SERVER grounds the description to
    pixels with the local UI-TARS grounder, so a client that can plan but can't ground
    accurately can still drive the rig. Returns [text, screenshot] on success, or an
    actionable error (re-describe, or fall back to rig_click with explicit coordinates from
    the latest screenshot) if the target can't be located.

    Requires _ground() to be wired to your grounder; until then this returns an error.
    """
    try:
        rig = await anyio.to_thread.run_sync(_Rig.get)
    except (Exception, SystemExit) as e:
        return [_hw_error(e)]

    def _run():
        with rig.lock:
            shot = rig.capture_output_png()
            try:
                pt = _ground(shot, description)
            except NotImplementedError as e:
                return ("UNWIRED", str(e))
            if pt is None:
                return ("NOTFOUND", None)
            ox, oy = pt
            nx, ny = _to_native(ox, oy)
            # --- pre-click verification gate: re-crop around (ox, oy) and confirm it
            #     matches `description` before committing; on mismatch return ("VERIFY", ...).
            rig.r4.move(nx, ny)
            rig.r4.rclick() if button == "right" else rig.r4.click()
            time.sleep(SETTLE_S)
            return ("OK", (ox, oy), rig.capture_output_png())

    res = await anyio.to_thread.run_sync(_run)
    tag = res[0]
    if tag == "UNWIRED":
        return [f"Error: {res[1]}"]
    if tag == "NOTFOUND":
        return [f"Could not locate '{description}' on screen. Re-describe it more specifically "
                f"(exact visible text, the control's role, a nearby label), or call rig_click "
                f"with explicit coordinates read from the latest screenshot."]
    _, (ox, oy), png = res
    return [f"Clicked '{description}' at ({ox},{oy}).", Image(data=png, format="png")]


if __name__ == "__main__":
    import argparse
    from mcp.server.transport_security import TransportSecuritySettings
    p = argparse.ArgumentParser(
        description=("KVM rig MCP server. Default transport is stdio (the MCP client spawns "
                     "this file as a subprocess on the same machine). Use --http to serve "
                     "Streamable HTTP so a client on another device can connect over the LAN. "
                     "The server must run on the machine physically wired to the capture card "
                     "and Pico."))
    p.add_argument("--http", action="store_true",
                   help="Serve Streamable HTTP instead of stdio.")
    p.add_argument("--host", default="127.0.0.1",
                   help=("Bind address for --http. Default 127.0.0.1 = localhost only (safest; "
                         "reach it from another device via an SSH tunnel). Pass 0.0.0.0 to "
                         "expose on the LAN — note this server drives a real keyboard/mouse, so "
                         "anyone who can reach the port controls the target machine."))
    p.add_argument("--port", type=int, default=8765, help="Port for --http (default 8765).")
    p.add_argument("--allow-host", action="append", default=None, metavar="HOST[:PORT]",
                   help=("The address(es) clients actually dial to reach this server, e.g. "
                         "192.168.0.184 — added to the allowed Host header (any port). "
                         "Repeatable. The Streamable HTTP transport rejects Host headers that "
                         "aren't allow-listed (DNS-rebinding protection), which is why a LAN "
                         "client otherwise gets 421 Misdirected Request. If omitted while "
                         "serving a non-localhost address, that Host check is disabled instead."))
    args = p.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port

        host_is_local = args.host in ("127.0.0.1", "localhost", "::1")
        if args.allow_host:
            # Keep the Host check ON, scoped to the address(es) clients dial. A bare host gets
            # a ":*" port wildcard so the port doesn't have to match exactly.
            hosts = ["127.0.0.1:*", "localhost:*"]
            origins = []
            for h in args.allow_host:
                hosts.append(h if ":" in h else f"{h}:*")
                origins.append(f"http://{h}")
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=hosts, allowed_origins=origins)
        elif not host_is_local:
            # The address a client dials varies (esp. with --host 0.0.0.0 or DHCP), so the
            # built-in localhost-only Host check would 421 every LAN request. Turn it off —
            # the real boundary here is the firewall / SSH tunnel, not the Host header. Use
            # --allow-host to keep the check on and pinned to a known address instead.
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False)
            print(f"[kvm_rig_mcp] WARNING: serving on {args.host}:{args.port} with no auth — "
                  f"anyone who can reach this port can control the target machine.",
                  file=sys.stderr)

        print(f"[kvm_rig_mcp] Streamable HTTP at http://{args.host}:{args.port}"
              f"{mcp.settings.streamable_http_path}", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()   # stdio transport (default)
