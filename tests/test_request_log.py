"""Offline tests for run-local model request evidence."""
import base64
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.models.holo as holo


def test_bound_requests_land_in_the_owning_run():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "one_run", "model_requests.jsonl")
        log = holo._RequestLog()
        with log.bind(path):
            log.write({"kind": "actor", "response_message": {"content": "{}"}})
        with open(path) as f:
            row = json.loads(f.readline())
    assert row["kind"] == "actor"
    assert "ts" in row


def test_exact_model_input_bytes_are_content_addressed_beside_request():
    raw = b"exact jpeg fixture bytes"
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "run", "model_requests.jsonl")
        log = holo._RequestLog()
        with log.bind(path):
            log.write({"messages": [{
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": data_url}}],
            }]})
        with open(path) as f:
            row = json.loads(f.readline())
        saved = row["messages"][0]["content"][0]["image_url"]["url"]
        name = saved.split("<saved ", 1)[1].split(";", 1)[0]
        with open(os.path.join(td, "run", name), "rb") as f:
            assert f.read() == raw


def test_unbound_request_gets_a_fresh_run_directory():
    with tempfile.TemporaryDirectory() as td:
        saved_cfg = holo.CFG
        holo.CFG = types.SimpleNamespace(runs_dir=td)
        try:
            log = holo._RequestLog()
            log.write({"kind": "one-shot"})
        finally:
            holo.CFG = saved_cfg
        dirs = os.listdir(td)
        assert len(dirs) == 1 and dirs[0].startswith("model_request_")
        path = os.path.join(td, dirs[0], "model_requests.jsonl")
        assert os.path.isfile(path)
