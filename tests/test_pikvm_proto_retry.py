"""
test_pikvm_proto_retry.py — OFFLINE tests for pikvm_proto.PicoHidLink's host-side
retry logic (roadmap Phase 0 firmware hardening, docs/ROADMAP.md; the plan's Slice B,
_archive/docs_history/PLAN_2026-07-22_roadmap_alignment_slices.md Part 3): a NACK (well-framed error
code) is safe to retry for ANY command; an AMBIGUOUS failure (no/garbled response)
only retries IDEMPOTENT commands, never CMD_MOUSE_WHEEL (relative delta -- a spurious
retry could double-scroll). Fake serial, no hardware.

    python -m pytest tests/test_pikvm_proto_retry.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "appliance", "pi5"))

import pikvm_proto as pp
from pikvm_proto import PicoHidLink, ProtoError, crc16, MAGIC_RESP, PONG_OK


def _resp(code, out1=0, out2=0, out3=0):
    """Build a well-framed 8-byte response with the given resp[1] code byte."""
    body = bytes([MAGIC_RESP, code, out1, out2, out3, 0])
    c = crc16(body)
    return body + bytes([(c >> 8) & 0xFF, c & 0xFF])


NACK = _resp(0x45)          # PH_PROTO_RESP_INVALID_ERROR: well-framed, no PONG_OK bit
OK = _resp(PONG_OK)         # well-framed, PONG_OK set, no other flags
SHORT = b"\x34\x80"         # ambiguous: too few bytes
BAD_MAGIC = bytes([0x00]) + OK[1:]   # ambiguous: right length/CRC, wrong magic byte


class FakeSerial:
    """Feeds pre-scripted 8-byte responses to read(8), one per write()."""

    def __init__(self, script):
        self.script = list(script)
        self.writes = []

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)

    def read(self, n):
        if not self.script:
            return b""
        return self.script.pop(0)


def _make_link(script):
    link = PicoHidLink.__new__(PicoHidLink)   # bypass __init__ -- no real serial port
    link.ser = FakeSerial(script)
    link.lock = threading.Lock()
    link.port = "fake"
    return link


def setup_module(module):
    # Don't let 2 retries * 150ms actually sleep during an offline test suite.
    module._real_sleep = pp.time.sleep
    pp.time.sleep = lambda s: None


def teardown_module(module):
    pp.time.sleep = module._real_sleep


def test_nack_retried_for_any_command_including_non_idempotent():
    """CMD_MOUSE_WHEEL is NOT idempotent, but a NACK is a well-framed rejection --
    the pico definitely saw it, so retrying is safe regardless of command type."""
    link = _make_link([NACK, OK])
    result = link.mouse_wheel(3)
    assert result["retries"] == 1
    assert len(link.ser.writes) == 2, "the request was actually re-sent, not just re-read"


def test_ambiguous_failure_retried_for_idempotent_command():
    link = _make_link([SHORT, OK])
    result = link.ping()
    assert result["retries"] == 1
    assert len(link.ser.writes) == 2


def test_bad_magic_is_ambiguous_and_retried_for_idempotent_command():
    link = _make_link([BAD_MAGIC, OK])
    result = link.ping()
    assert result["retries"] == 1


def test_ambiguous_failure_not_retried_for_mouse_wheel():
    """A garbled response to a relative-delta command must surface immediately --
    guessing (retrying) risks double-scrolling if the first attempt actually landed."""
    link = _make_link([SHORT, OK])   # OK is scripted but must NEVER be consumed
    raised = None
    try:
        link.mouse_wheel(3)
    except ProtoError as e:
        raised = e
    assert raised is not None, "an ambiguous failure on a non-idempotent command raises"
    assert len(link.ser.writes) == 1, "no retry was attempted"
    assert len(link.ser.script) == 1, "the scripted OK response was never consumed"


def test_exhausts_retries_and_raises():
    link = _make_link([NACK, NACK, NACK])   # MAX_RETRIES=2 -> 3 total attempts
    raised = None
    try:
        link.ping()
    except ProtoError as e:
        raised = e
    assert raised is not None
    assert "exhausted" in str(raised)
    assert len(link.ser.writes) == pp.MAX_RETRIES + 1


def test_first_try_success_reports_zero_retries():
    link = _make_link([OK])
    result = link.ping()
    assert result["retries"] == 0
    assert len(link.ser.writes) == 1


def test_decode_code_surfaces_watchdog_and_suspended_flags():
    raw = _resp(PONG_OK | pp.PONG_WATCHDOG_REBOOTED, out3=pp.PONG2_USB_SUSPENDED)
    info = pp.decode_code(raw[1], raw)
    assert info["watchdog_rebooted"] is True
    assert info["usb_suspended"] is True

    raw_clean = _resp(PONG_OK)
    info_clean = pp.decode_code(raw_clean[1], raw_clean)
    assert info_clean["watchdog_rebooted"] is False
    assert info_clean["usb_suspended"] is False


def test_decode_code_without_raw_omits_usb_suspended():
    info = pp.decode_code(PONG_OK)
    assert "usb_suspended" not in info
    assert info["watchdog_rebooted"] is False


if __name__ == "__main__":
    import traceback
    setup_module(sys.modules[__name__])
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            fails += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    teardown_module(sys.modules[__name__])
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
