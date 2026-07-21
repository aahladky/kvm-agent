"""
test_holo_messages.py — OFFLINE tests for the holo model-adapter message layer:
parse_response against the captured native-verbatim fixture, observation_message /
tool_output_message shapes, and trim_to_last_n_images eviction. No network.

(The phase2 fixture holo_phase2_native_tools_raw.json is deliberately NOT parsed
here: it captures the retired phase-2 OpenAI-tool-calling format, which the current
verbatim-content parser is not meant to accept.)

    python tests/test_holo_messages.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.models.holo import (
    TOOL_CALL_SCHEMAS, observation_message, parse_response, tool_output_message,
    trim_to_last_n_images,
)

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "kvm_agent", "models", "_fixtures", "holo_native_verbatim_raw.json")


def _examples():
    with open(FIXTURE) as f:
        return json.load(f)


def test_parse_response_verbatim_fixture_all_examples():
    """Every captured raw response parses without error and yields actions."""
    for ex in _examples():
        step = parse_response(ex["message"], ex["image_w"], ex["image_h"])
        assert not step.get("error"), \
            f"failed to parse: {ex['instruction']!r} -> {step.get('error')}"
        assert step["actions"], f"no actions: {ex['instruction']!r}"


def test_parse_response_fixture_covers_every_schema_tool():
    """The fixture exercises every tool the schema offers (parser coverage)."""
    schema_tools = {s["properties"]["tool_name"]["const"] for s in TOOL_CALL_SCHEMAS}
    fixture_tools = {tc["tool_name"] for e in _examples()
                     for tc in json.loads(e["message"]["content"])["tool_calls"]}
    assert not (schema_tools - fixture_tools), \
        f"tools with no fixture coverage: {schema_tools - fixture_tools}"


def test_parse_response_coordinate_projection():
    """Raw [0,1000] coords project against the real image dims."""
    ex = next(e for e in _examples()
              if json.loads(e["message"]["content"])["tool_calls"][0]["tool_name"]
              == "click_desktop")
    step = parse_response(ex["message"], ex["image_w"], ex["image_h"])
    raw_tc = json.loads(ex["message"]["content"])["tool_calls"][0]
    expected = [raw_tc["x"] / 1000 * ex["image_w"], raw_tc["y"] / 1000 * ex["image_h"]]
    assert step["actions"][0]["coordinate"] == expected, \
        (step["actions"][0]["coordinate"], expected)


def test_observation_message_shape():
    with_text = observation_message("data:image/jpeg;base64,AA", "do the thing")
    assert with_text["role"] == "user"
    chunks = with_text["content"]
    assert chunks[0]["type"] == "text" and "do the thing" in chunks[0]["text"]
    assert chunks[0]["text"].startswith("<observation>")
    assert chunks[1]["type"] == "image_url"
    assert chunks[2]["text"] == "\n</observation>"
    bare = observation_message("data:image/jpeg;base64,AA")
    assert bare["content"][0]["text"] == "<observation>\n"


def test_tool_output_message_shape():
    msg = tool_output_message("click_desktop", "Executed. Screen changed.")
    assert msg["role"] == "user"
    assert msg["content"].startswith('<tool_output tool="click_desktop">')
    assert "Executed. Screen changed." in msg["content"]
    assert msg["content"].endswith("</tool_output>")


def _history(n_obs):
    h = []
    for i in range(n_obs):
        h.append(observation_message(f"data:image/jpeg;base64,{i:02d}"))
        h.append({"role": "assistant", "content": f'{{"step": {i}}}'})
    return h


def test_trim_to_last_n_images_evicts_oldest():
    h = _history(4)
    trim_to_last_n_images(h, n=2)
    obs = [m for m in h if m["role"] == "user"]
    for m in obs[:-2]:
        assert m["content"][1] == {"type": "text", "text": "[screenshot evicted]"}, m
    for m in obs[-2:]:
        assert m["content"][1]["type"] == "image_url", m
    # eviction keeps the <observation> text wrapper and never touches assistant turns
    for m in obs:
        assert m["content"][0]["text"].startswith("<observation>")
    for m in h:
        if m["role"] == "assistant":
            assert isinstance(m["content"], str) and "step" in m["content"]


def test_trim_to_last_n_images_zero_evicts_all():
    h = _history(2)
    trim_to_last_n_images(h, n=0)
    for m in h:
        if m["role"] == "user":
            assert m["content"][1] == {"type": "text", "text": "[screenshot evicted]"}


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
