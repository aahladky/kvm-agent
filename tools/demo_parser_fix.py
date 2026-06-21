#!/usr/bin/env python3
"""
demo_parser_fix.py - demonstrate the root-cause parser fix + answer-channel on the LIVE
import path, with NO hardware and NO Ollama.

It uses the SAME corrected import preamble the entry points now use (append evocua/ for
mm_agents.* submodules, keep repo root ahead so `import evocua_agent` loads the PATCHED
copy), then feeds _parse_response_s2 the tool_call formats the GGUF/Ollama model emits in
the wild - including the same-line "<tool_call>{json}</tool_call>" that the stale
evocua/evocua_agent.py silently DROPPED (the bug behind the stalls / re-click loops /
false terminates in the multi-session quant saga).

It prints which agent file the import resolves to, so the demo is honest about what runs.

Run (from the repo, e.g. C:\\Dev\\vllm):  python demo_parser_fix.py
"""
import os, sys, re, inspect

# --- corrected live preamble (matches the patched run_probe/operate/mcp_server) ---
REPO = os.environ.get("EVOCUA_REPO") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)                       # repo root first -> patched evocua_agent wins
sys.path.append(os.path.join(REPO, "evocua"))  # evocua/ APPENDED -> mm_agents.* submodules
os.environ.setdefault("OPENAI_BASE_URL", "http://mock")   # never contacted here
os.environ.setdefault("OPENAI_API_KEY", "mock")
import evocua_agent
from evocua_agent import EvoCUAAgent


def banner(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


CLICK = {  # all encode left_click at relative [300,627]
    "same-line (the old bug)": '<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[300,627]}}</tool_call>',
    "newline canonical":       'Action: Click.\n<tool_call>\n{"name":"computer_use","arguments":{"action":"left_click","coordinate":[300,627]}}\n</tool_call>',
    "all-on-one-line":         'Click. <tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [300, 627]}}</tool_call>',
    "pretty-printed":          '<tool_call>\n{\n  "name": "computer_use",\n  "arguments": {"action": "left_click", "coordinate": [300, 627]}\n}\n</tool_call>',
    "bare json (no tags)":     '{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [300, 627]}}',
    "trailing prose":          '<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[300,627]}}</tool_call>\nThat clicks the key.',
}


def main():
    src = inspect.getsource(evocua_agent)
    print("LIVE import resolves to:", evocua_agent.__file__)
    print("  fix#1 history-normalize:", bool(re.search(r'sub\(r"<tool_call>', src)),
          "| fix#2 DOTALL parser:", "re.DOTALL" in src,
          "| answer-channel:", ("last_answer" in src))
    resolved = evocua_agent.__file__.replace("\\", "/")
    assert "/evocua/evocua_agent.py" not in resolved, \
        "FAIL: resolved to the stale evocua/evocua_agent.py - import shadowing not fixed!"

    ag = EvoCUAAgent(model="m", max_tokens=64, temperature=0.0, top_p=0.9, prompt_style="S2",
                     max_history_turns=1, screen_size=(1920, 1080),
                     coordinate_type="relative", resize_factor=32)

    banner("A) every tool_call format parses to the SAME click (no more dropped steps)")
    ok = 0
    for label, resp in CLICK.items():
        _, code = ag._parse_response_s2(resp, 1000, 1000, 1920, 1080)
        ok += bool(code)
        print(f"  [{'OK ' if code else 'DROP'}] {label:<24} -> {code}")
    print(f"\n  parsed {ok}/{len(CLICK)} formats  (the stale evocua/ copy parses only the newline form)")
    assert ok == len(CLICK), "a format was dropped - not on the patched agent"

    banner("B) terminate -> DONE/FAIL, and its reported answer is captured")
    for label, resp in {
        "terminate success +answer": '<tool_call>{"name":"computer_use","arguments":{"action":"terminate","status":"success","answer":"display shows 61"}}</tool_call>',
        "terminate failure":         '<tool_call>{"name":"computer_use","arguments":{"action":"terminate","status":"failure"}}</tool_call>',
    }.items():
        _, code = ag._parse_response_s2(resp, 1000, 1000, 1920, 1080)
        print(f"  {label:<26} -> code={code}  last_answer={getattr(ag,'last_answer',None)!r}")

    banner("C) answer-channel: a standalone `answer` is the MCP '2-way street' seed")
    resp = '<tool_call>{"name":"computer_use","arguments":{"action":"answer","text":"I see a.txt and b.txt - which one?"}}</tool_call>'
    _, code = ag._parse_response_s2(resp, 1000, 1000, 1920, 1080)
    print(f"  standalone answer -> code={code}  (non-terminal sentinel)")
    print(f"  agent.last_answer -> {getattr(ag,'last_answer',None)!r}")
    assert code == ["ANSWER"] and ag.last_answer, "answer-channel not active on the live agent"
    print("\n  -> On the patched path operate.py / the MCP REAL backend CAN now reach 'awaiting_reply'.")

    print("\n[PASS] parser fix + answer-channel verified on the live import path.")


if __name__ == "__main__":
    main()
