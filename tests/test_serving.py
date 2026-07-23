"""
test_serving.py — OFFLINE tests for the serving-layer seam (kvm_agent.llm.serving).

No endpoint is ever contacted: `parse_serving_cmd` is pure, and `serving_snapshot`'s
network calls are monkeypatched. The properties under test are the two that make this
module worth having at all:

  1. it captures the serving params that shape what the model SEES (a real
     `--image-min-tokens` floor lives in the live holo3.1 launch command, invisible to
     this project until now), and
  2. it NEVER raises -- a probe that can kill a run is worse than no probe.

    python -m pytest tests/test_serving.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.llm.serving as serving
from kvm_agent.llm.serving import describe, parse_serving_cmd, serving_snapshot

# The REAL holo3.1 launch command, as llama-swap reported it via /running on
# 2026-07-23. Kept verbatim (escaped newlines and all) so the parser is tested against
# the actual wire shape rather than a tidied-up invention.
LIVE_HOLO_CMD = (
    "/home/aaron/workspace/llama.cpp/build-sycl/bin/llama-server --port ${PORT} \\\n"
    "  --model \\\n"
    "  /home/aaron/models/mradermacher/Holo-3.1-35B-A3B-GGUF/Holo-3.1-35B-A3B.Q4_K_M.gguf \\\n"
    "  -ngl \\\n  999 \\\n  --flash-attn \\\n  auto \\\n  -c \\\n  64000 \\\n"
    "  --jinja \\\n  --parallel \\\n  1 \\\n  --split-mode \\\n  none \\\n"
    "  --tensor-split \\\n  4,1 \\\n  --mmproj \\\n"
    "  /home/aaron/models/mradermacher/Holo-3.1-35B-A3B-GGUF/Holo-3.1-35B-A3B.mmproj-f16.gguf \\\n"
    "  --cache-type-k \\\n  q8_0 \\\n  --cache-type-v \\\n  q4_0 \\\n"
    "  --image-min-tokens \\\n  1024\n")


def test_parses_the_live_holo_command():
    p = parse_serving_cmd(LIVE_HOLO_CMD)
    assert p["ctx"] == 64000 and isinstance(p["ctx"], int)
    assert p["parallel"] == 1
    assert p["quant"] == "Q4_K_M"
    assert p["model_file"] == "Holo-3.1-35B-A3B.Q4_K_M.gguf"
    assert p["has_mmproj"] is True
    assert p["cache_type_k"] == "q8_0" and p["cache_type_v"] == "q4_0"
    assert p["split_mode"] == "none"
    assert p["n_gpu_layers"] == 999


def test_captures_the_server_side_image_token_floor():
    """The whole reason this module exists: --image-min-tokens is a SERVER-side floor on
    image tokens, set in a config outside this repo, while config.py documents a
    client-side resolution A/B as though model input were ours alone to control."""
    assert parse_serving_cmd(LIVE_HOLO_CMD)["image_min_tokens"] == 1024


def test_missing_mmproj_is_detectable():
    """A vision model launched without an mmproj answers fluently from text alone --
    it reads as 'the model got bad at grounding', the most expensive misdiagnosis
    available here."""
    without = LIVE_HOLO_CMD.replace("--mmproj", "--not-mmproj")
    assert parse_serving_cmd(without)["has_mmproj"] is False


def test_parses_an_ovms_style_command_without_inventing_gguf_fields():
    p = parse_serving_cmd(
        "python3 ovms-proxy.py --model-id fast-7b --listen-port 5803 "
        "--source-model OpenVINO/Qwen2.5-7B-Instruct-int4-ov --target-device GPU.1")
    assert p["target_device"] == "GPU.1"
    assert p["source_model"] == "OpenVINO/Qwen2.5-7B-Instruct-int4-ov"
    assert p["has_mmproj"] is False
    assert "quant" not in p and "model_file" not in p, \
        "no --model flag: don't fabricate GGUF fields for a non-GGUF backend"


def test_parser_tolerates_junk_without_raising():
    for cmd in ("", None, "llama-server", "--model", "-c --parallel", "   "):
        out = parse_serving_cmd(cmd)
        assert isinstance(out, dict)
    assert "ctx" not in parse_serving_cmd("-c --parallel"), \
        "a flag with no value records nothing rather than something wrong"
    assert "model_path" not in parse_serving_cmd("--model"), "dangling flag at the end"


def _fake_endpoint(models=None, running=None, models_raises=None, running_raises=None):
    def _get_json(url, timeout_s):
        if url.endswith("/v1/models"):
            if models_raises:
                raise models_raises
            return {"data": [{"id": m} for m in (models or [])]}
        if url.endswith("/running"):
            if running_raises:
                raise running_raises
            return {"running": running or []}
        raise AssertionError(f"unexpected url {url}")
    return _get_json


def _with_endpoint(fn, **kw):
    saved = serving._get_json
    serving._get_json = _fake_endpoint(**kw)
    try:
        return fn()
    finally:
        serving._get_json = saved


def test_snapshot_reports_resident_model_with_its_params():
    snap = _with_endpoint(
        lambda: serving_snapshot(model="holo3.1"),
        models=["holo3.1", "fast-7b"],
        running=[{"model": "holo3.1", "state": "ready", "ttl": 3600, "cmd": LIVE_HOLO_CMD}])
    assert snap["reachable"] and snap["configured"] and snap["resident"]
    assert snap["state"] == "ready" and snap["ttl"] == 3600
    assert snap["params"]["image_min_tokens"] == 1024
    assert snap["co_resident"] == []
    assert snap["error"] is None


def test_snapshot_separates_configured_from_resident():
    """A configured-but-unloaded model costs a cold start; it is not a fault. Conflating
    the two would either cry wolf or hide a real absence."""
    snap = _with_endpoint(lambda: serving_snapshot(model="holo3.1"),
                          models=["holo3.1"], running=[])
    assert snap["configured"] is True and snap["resident"] is False
    assert snap["params"] == {}
    assert "NOT resident" in describe(snap)


def test_snapshot_names_co_resident_models():
    """Co-tenancy IS the eviction-risk signal: on 2026-07-23 one unrelated request to
    fast-7b evicted holo3.1, and the reverse also holds."""
    snap = _with_endpoint(
        lambda: serving_snapshot(model="holo3.1"),
        models=["holo3.1", "fast-7b"],
        running=[{"model": "fast-7b", "state": "ready", "cmd": "x"}])
    assert snap["resident"] is False
    assert snap["co_resident"] == ["fast-7b"]


def test_unconfigured_model_is_reported_not_raised():
    snap = _with_endpoint(lambda: serving_snapshot(model="holo3.1"),
                          models=["something-else"], running=[])
    assert snap["reachable"] is True and snap["configured"] is False
    assert "NOT CONFIGURED" in describe(snap)


def test_unreachable_endpoint_never_raises():
    snap = _with_endpoint(lambda: serving_snapshot(model="holo3.1"),
                          models_raises=OSError("connection refused"))
    assert snap["reachable"] is False
    assert snap["resident"] is None and snap["configured"] is None
    assert "connection refused" in snap["error"]
    assert "UNREACHABLE" in describe(snap)


def test_server_without_a_running_endpoint_degrades_gracefully():
    """A plain llama-server or vLLM has no /running. Residency is unknowable there --
    say so, don't invent it, and don't fail."""
    snap = _with_endpoint(lambda: serving_snapshot(model="holo3.1"),
                          models=["holo3.1"], running_raises=OSError("404"))
    assert snap["reachable"] is True and snap["configured"] is True
    assert snap["resident"] is None, "unknown residency is None, not False"
    assert "residency unknown" in describe(snap)


def test_root_strips_the_openai_v1_suffix():
    assert serving._root("http://127.0.0.1:9292/v1") == "http://127.0.0.1:9292"
    assert serving._root("http://127.0.0.1:9292/v1/") == "http://127.0.0.1:9292"
    assert serving._root("http://host:1234") == "http://host:1234"


def test_run_does_not_probe_when_boot_never_checked():
    """Hermeticity contract, tested behaviourally rather than by inspecting a mutable
    global: offline tests set agent_loop_holo.ENV directly and never boot, so run() must
    not touch the network. An exploding probe proves it isn't called."""
    import agent_loop_holo as al
    import tests.test_agent_loop as tal

    def exploding(*a, **k):
        raise AssertionError("run() probed the serving layer without boot()'s opt-in")

    saved_probe, saved_serving = al.serving_snapshot, dict(al.SERVING)
    al.serving_snapshot = exploding
    al.SERVING.clear()
    al.SERVING.update({"checked": False})
    saved = tal._patch_run(lambda *a, **k: (
        {"actions": [{"action": "finished", "text": "done"}], "note": None,
         "thought": None}, {"content": "{}"}, {}))
    try:
        out = al.run("t", max_steps=1, confirm_first=0, tag="t_noprobe")
        assert out["finished"] is True
        meta = tal.FakeRecorder.instances[-1].meta
        assert meta.get("serving") is None, \
            "an unchecked session records serving=None, not a fabricated snapshot"
    finally:
        tal._restore_run(saved)
        al.serving_snapshot = saved_probe
        al.SERVING.clear()
        al.SERVING.update(saved_serving)


def test_run_records_a_fresh_snapshot_per_run_when_checked():
    """A battery runs for an hour and the model can be evicted between tasks by any
    other consumer of the box, so the snapshot is taken per RUN, not once per session."""
    import agent_loop_holo as al
    import tests.test_agent_loop as tal

    calls = {"n": 0}
    def probe(*a, **k):
        calls["n"] += 1
        return {"model": "holo3.1", "reachable": True, "configured": True,
                "resident": False, "params": {}, "co_resident": ["fast-7b"],
                "error": None}

    saved_probe, saved_serving = al.serving_snapshot, dict(al.SERVING)
    al.serving_snapshot = probe
    al.SERVING.clear()
    al.SERVING.update({"checked": True})
    saved = tal._patch_run(lambda *a, **k: (
        {"actions": [{"action": "finished", "text": "done"}], "note": None,
         "thought": None}, {"content": "{}"}, {}))
    try:
        al.run("t", max_steps=1, confirm_first=0, tag="t_probe1")
        al.run("t", max_steps=1, confirm_first=0, tag="t_probe2")
        assert calls["n"] == 2, "one snapshot per run, so mid-battery eviction is visible"
        meta = tal.FakeRecorder.instances[-1].meta
        assert meta["serving"]["co_resident"] == ["fast-7b"], \
            "the snapshot travels with the run's evidence, like the system prompt does"
    finally:
        tal._restore_run(saved)
        al.serving_snapshot = saved_probe
        al.SERVING.clear()
        al.SERVING.update(saved_serving)


if __name__ == "__main__":
    import traceback
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
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
