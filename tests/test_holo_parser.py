"""
test_holo_parser.py — OFFLINE test for the model adapter's parse layer, the
capture->prompt->parse territory AGENTS.md §2 names as the historical bug site.
Ports holo.py's _self_test into the suite proper (review 2026-07-21 P2: the parser's
only coverage lived outside tests/ and wasn't collectable) and pins what happens to
the RETIRED phase-2 tool-calling format: the current structured-output parser must
reject it LOUDLY, never silently.

Fixtures:
  holo_native_verbatim_raw.json    current structured-output shape, all 10 tools
  holo_phase2_native_tools_raw.json retired phase-2 message.tool_calls shape

    python tests/test_holo_parser.py   (or pytest tests/test_holo_parser.py)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.models.holo import (
    TOOL_CALL_SCHEMAS, dropped_actions, parse_response,
)

_FIXDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "kvm_agent", "models", "_fixtures")


def _fixture(name):
    with open(os.path.join(_FIXDIR, name)) as f:
        return json.load(f)


def test_native_verbatim_fixtures_parse_cleanly():
    examples = _fixture("holo_native_verbatim_raw.json")
    dropped_before = dropped_actions.count
    total_actions = 0
    for ex in examples:
        step = parse_response(ex["message"], ex["image_w"], ex["image_h"])
        assert not step.get("error"), f"failed to parse {ex['instruction']!r}: {step.get('error')}"
        assert step["actions"], f"no actions for {ex['instruction']!r}"
        total_actions += len(step["actions"])
    assert dropped_actions.count == dropped_before, "no captured example may be dropped"
    assert total_actions >= len(examples), "the multi-call batch example must yield >1 action"


def test_click_projection():
    # [0,1000] normalized -> real screen pixels, against the verified formula
    examples = _fixture("holo_native_verbatim_raw.json")
    ex = next(e for e in examples
              if json.loads(e["message"]["content"])["tool_calls"][0]["tool_name"] == "click_desktop")
    step = parse_response(ex["message"], ex["image_w"], ex["image_h"])
    raw = json.loads(ex["message"]["content"])["tool_calls"][0]
    expected = [raw["x"] / 1000 * ex["image_w"], raw["y"] / 1000 * ex["image_h"]]
    assert step["actions"][0]["coordinate"] == expected


def test_all_schema_tools_covered_by_fixtures():
    examples = _fixture("holo_native_verbatim_raw.json")
    schema_tools = {s["properties"]["tool_name"]["const"] for s in TOOL_CALL_SCHEMAS}
    fixture_tools = {tc["tool_name"] for e in examples
                     for tc in json.loads(e["message"]["content"])["tool_calls"]}
    assert not schema_tools - fixture_tools, \
        f"tools with no fixture coverage: {schema_tools - fixture_tools}"


def test_retired_phase2_format_fails_loud():
    # These are captures from the RETIRED phase-2 native tool-calling line
    # (message.tool_calls, empty content). The structured-output parser must turn
    # them into error steps -- loud, no exception, no silent empty-action step.
    examples = _fixture("holo_phase2_native_tools_raw.json")
    dropped_before = dropped_actions.count
    for ex in examples:
        step = parse_response(ex["message"], 1920, 1080)
        assert step.get("error"), "retired format must produce an error step"
        assert step["actions"] == [], "no actions may be invented from the old shape"
    assert dropped_actions.count > dropped_before, "drops must be counted (loud-failure guardrail)"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
