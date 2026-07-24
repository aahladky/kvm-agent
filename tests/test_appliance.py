"""Offline tests for host-side HID response evidence."""
import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError


class _Response:
    def __init__(self, payload):
        self._body = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._body

    def __exit__(self, *args):
        return False


def test_success_response_is_available_for_the_run_recorder():
    payload = {
        "ok": True, "ack": "C", "cmd": "_cmd_click",
        "wire": {"kbd_online": True, "mouse_online": True, "wire_ms": 1.2},
    }
    client = ApplianceClient("http://fixture")
    with mock.patch("urllib.request.urlopen", return_value=_Response(payload)):
        assert client.click()["ack"] == "C"
    assert client.drain_events() == [{
        "path": "/hid/click", "method": "POST", "params": {},
        "ok": True, "response": payload,
    }]
    assert client.drain_events() == []


def test_transport_failure_is_preserved_before_it_raises():
    client = ApplianceClient("http://fixture")
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("wire timeout")):
        try:
            client.click()
        except ApplianceError:
            pass
        else:
            raise AssertionError("transport failure must raise")
    event = client.drain_events()[0]
    assert event["ok"] is False
    assert "wire timeout" in event["error"]
