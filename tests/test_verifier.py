"""
test_verifier.py — OFFLINE tests for the postcondition oracle (roadmap Phase 2,
docs/PLAN_2026-07-22_phase2_subgoal_verification.md slice D-a):
kvm_agent.models.base.{Verdict, Verifier} and kvm_agent.models.holo.{parse_verdict,
verify_message, HoloVerifier}.

The load-bearing property under test is FAIL-VISIBLE: every way the oracle can fail to
answer must produce satisfied=None, never True and never a coerced False. An oracle that
silently answers True when broken launders a missing check into a passing one — finding
#8's class (tools/battery.py), one level down.

No network: the endpoint is never touched; call_fn is injected or parse_verdict is
called directly.

    python -m pytest tests/test_verifier.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.models.base import Verdict, Verifier
from kvm_agent.models.holo import (
    VERIFY_SCHEMA, HoloVerifier, parse_verdict, verify_message,
)


def _msg(payload):
    """An assistant message the way model_dump() delivers it: content is a JSON STRING."""
    return {"content": json.dumps(payload)}


# --- Protocol conformance ------------------------------------------------------

def test_holo_verifier_satisfies_the_verifier_protocol():
    assert isinstance(HoloVerifier(), Verifier), \
        "HoloVerifier must structurally satisfy the Verifier Protocol"


def test_a_non_holo_verifier_also_satisfies_the_protocol():
    """The Phase-5 gate in miniature: relocating the oracle to another card/model means
    writing a class of this shape, not editing HoloVerifier."""
    class StubVerifier:
        def check(self, data_url, w, h, question, claim=""):
            return Verdict(satisfied=True, evidence="stub", raw={}, usage={},
                           wall_time_s=0.0)
    assert isinstance(StubVerifier(), Verifier)


# --- parse_verdict: the happy paths -------------------------------------------

def test_parse_verdict_reads_a_true_verdict():
    v = parse_verdict(_msg({"evidence": "title bar reads hello.txt", "satisfied": True}),
                      usage={"prompt_tokens": 11}, wall_time_s=1.5)
    assert v.satisfied is True
    assert v.evidence == "title bar reads hello.txt"
    assert v.usage == {"prompt_tokens": 11}
    assert v.wall_time_s == 1.5
    assert v.answered is True


def test_parse_verdict_reads_a_false_verdict_with_its_evidence():
    v = parse_verdict(_msg({"evidence": "editor is open but empty", "satisfied": False}))
    assert v.satisfied is False
    assert v.answered is True, "False is an ANSWER; only None is a non-answer"
    assert "empty" in v.evidence


# --- parse_verdict: every failure is satisfied=None, never True, never False ---

def test_unparseable_response_is_none_not_a_verdict():
    v = parse_verdict({"content": "not json at all"})
    assert v.satisfied is None
    assert v.answered is False
    assert "not JSON" in v.evidence


def test_empty_response_is_none():
    for message in ({"content": ""}, {"content": None}, {}, None):
        v = parse_verdict(message)
        assert v.satisfied is None, f"{message!r} must not produce a verdict"


def test_missing_satisfied_field_is_none():
    v = parse_verdict(_msg({"evidence": "I looked at the screen"}))
    assert v.satisfied is None
    assert "I looked at the screen" in v.evidence, \
        "the evidence is still worth recording when the verdict is missing"


def test_stringly_typed_satisfied_is_not_coerced():
    """The fail-open that would matter most: {"satisfied": "false"} is truthy in Python.
    A truthiness check here would turn a NEGATIVE verdict into a passing one."""
    for bogus in ("false", "true", 1, 0, "yes", None, [], {}):
        v = parse_verdict(_msg({"evidence": "e", "satisfied": bogus}))
        assert v.satisfied is None, f"satisfied={bogus!r} must not be coerced to a bool"


def test_verdict_never_silently_becomes_true():
    """Belt and braces over the whole failure surface: nothing that isn't a real boolean
    true may come back as satisfied=True."""
    bad = [{"content": "junk"}, {"content": ""}, {}, _msg({}),
           _msg({"satisfied": "true"}), _msg({"evidence": "x"})]
    assert all(parse_verdict(m).satisfied is not True for m in bad)


# --- the message the oracle actually sees --------------------------------------

def test_verify_message_is_stateless_and_carries_exactly_one_image():
    msgs = verify_message("data:image/jpeg;base64,AAA", "open the calculator")
    assert len(msgs) == 2, "system + one user turn: no history, ever"
    assert msgs[0]["role"] == "system"
    images = [c for c in msgs[1]["content"] if c.get("type") == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"] == "data:image/jpeg;base64,AAA"
    text = "".join(c.get("text", "") for c in msgs[1]["content"])
    assert "<task>" in text and "open the calculator" in text


def test_claim_is_included_but_marked_untrusted():
    msgs = verify_message("data:x", "read the clock", claim="It says 00:10")
    text = "".join(c.get("text", "") for c in msgs[1]["content"])
    assert "It says 00:10" in text, "the claim must reach the oracle to be checkable"
    assert "UNTRUSTED" in text, \
        "the claim is data to check, not an instruction (epistemic firewall)"
    assert "</claimed_answer>" in text, "the claim stays in its own delimited block"


def test_claim_block_is_absent_when_there_is_no_claim():
    text = "".join(c.get("text", "")
                   for c in verify_message("data:x", "q")[1]["content"])
    assert "claimed_answer" not in text


def test_schema_puts_evidence_before_satisfied():
    """Structured-output generation follows property order and thinking is off, so
    evidence-first is the oracle's only chance to observe before committing."""
    props = list(VERIFY_SCHEMA["json_schema"]["schema"]["properties"])
    assert props == ["evidence", "satisfied"]
    assert VERIFY_SCHEMA["json_schema"]["schema"]["additionalProperties"] is False


# --- HoloVerifier wiring -------------------------------------------------------

def test_check_passes_question_and_claim_through_to_the_call_fn():
    seen = {}
    def fake_call_fn(data_url, question, claim="", target=None):
        seen.update(data_url=data_url, question=question, claim=claim, target=target)
        return Verdict(satisfied=True, evidence="ok", raw={}, usage={}, wall_time_s=0.1)

    v = HoloVerifier(target="local", call_fn=fake_call_fn).check(
        "data:url", 1280, 720, "save the file as hello.txt", claim="saved it")
    assert v.satisfied is True
    assert seen == {"data_url": "data:url", "question": "save the file as hello.txt",
                    "claim": "saved it", "target": "local"}


def test_check_is_stateless_across_calls():
    """No call may see anything from the one before it — the property the whole tier
    split rests on (docs/ROADMAP.md §3)."""
    calls = []
    def fake_call_fn(data_url, question, claim="", target=None):
        calls.append((data_url, question, claim))
        return Verdict(satisfied=False, evidence="no", raw={}, usage={}, wall_time_s=0.0)

    verifier = HoloVerifier(call_fn=fake_call_fn)
    verifier.check("frame_a", 1280, 720, "task one", claim="first")
    verifier.check("frame_b", 1280, 720, "task two")
    assert calls == [("frame_a", "task one", "first"), ("frame_b", "task two", "")]
    assert not hasattr(verifier, "history")


def test_a_raising_call_fn_is_not_swallowed_into_a_pass():
    """call_holo_verify converts model-side failures into satisfied=None itself; a
    call_fn that raises anyway must not be caught and turned into a verdict here."""
    def exploding(*a, **k):
        raise RuntimeError("endpoint down")
    try:
        HoloVerifier(call_fn=exploding).check("data:x", 1280, 720, "q")
    except RuntimeError:
        pass
    else:
        raise AssertionError("a raising call_fn must propagate, not become a verdict")


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
