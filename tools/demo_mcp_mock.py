#!/usr/bin/env python3
"""
demo_mcp_mock.py - demonstrate the EvoCUA MCP server's job model end-to-end with NO
hardware and NO Ollama. Drives the ACTUAL @mcp.tool functions from evocua_mcp_server.py
against the canned MockAgent/MockEnv backend, exercising the real state machine:

    start_computer_task  -> running
    get_task_status      -> running ... awaiting_reply   (model asks a question)
    continue_task        -> running                       (orchestrator answers)
    get_task_status      -> succeeded                      (model's answer returned)

This is the "offline probe" layer from FINDINGS: validate host<->server<->state-machine
plumbing (job lifecycle, the 2-way street, busy-guard) without tying up the capture card.

Run (from the repo, e.g. C:\\Dev\\vllm):  python demo_mcp_mock.py
"""
import os, sys, json, time

os.environ.setdefault("OPENAI_BASE_URL", "http://mock")   # never contacted in mock mode
os.environ.setdefault("OPENAI_API_KEY", "mock")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import evocua_mcp_server as S


def banner(t):
    print("\n" + "=" * 68 + "\n" + t + "\n" + "=" * 68)


def poll_until(job_id, stop_states, timeout=10.0, label=""):
    """Poll get_task_status until status hits one of stop_states (or timeout)."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        snap = json.loads(S.get_task_status(job_id))
        if snap["status"] != last:
            print("   poll %s: status=%-14s step=%s last_action=%r"
                  % (label, snap["status"], snap["step"], snap["last_action"]))
            last = snap["status"]
        if snap["status"] in stop_states:
            return snap
        time.sleep(0.1)
    raise TimeoutError("never reached %s; last=%s" % (stop_states, last))


def main():
    agent, env = S.build_backend(mock=True, cfg=dict(S.DEFAULTS))
    S.MANAGER = S.JobManager(agent, env, settle=0.0, default_max_steps=25)
    print("[setup] MockAgent + MockEnv wired into JobManager (no hardware, no Ollama)")

    banner("1) start_computer_task - returns immediately with a job_id")
    r = json.loads(S.start_computer_task("open the files menu and open the right file"))
    print("  ->", r)
    job_id = r["job_id"]

    banner("2) busy-guard - a second start is rejected while one task is active")
    r2 = json.loads(S.start_computer_task("some other task"))
    print("  ->", r2)
    assert "error" in r2 and r2["active_job_id"] == job_id, "busy-guard failed"
    print("  [ok] rig correctly reported busy")

    banner("3) poll until the agent asks a question (the 2-way street)")
    snap = poll_until(job_id, {"awaiting_reply"}, label="A")
    print("  agent question -> %r" % snap["question"])

    banner("4) continue_task - orchestrator supplies the answer")
    rc = json.loads(S.continue_task(job_id, "open b.txt"))
    print("  ->", rc)
    assert rc.get("status") == "running", "continue_task did not resume"

    banner("5) poll until terminal - model's self-reported answer is returned")
    snap = poll_until(job_id, {"succeeded", "failed", "aborted", "cancelled"}, label="B")
    print("  final status   -> %s" % snap["status"])
    print("  model's answer -> %r" % snap["answer"])
    print("  detail         -> %r" % snap["detail"])
    print("  is_terminal    -> %s" % snap["is_terminal"])
    assert snap["status"] == "succeeded" and "b.txt" in (snap["answer"] or ""), "lifecycle mismatch"

    banner("6) get_task_screenshot - returns a PNG (placeholder frame in mock mode)")
    img = S.get_task_screenshot(job_id)
    fmt = getattr(img, "_format", None) or getattr(img, "format", "png")
    print("  -> PNG image, %d bytes, format=%s" % (len(img.data), fmt))

    banner("7) post-terminal start - rig is free again, new task accepted")
    r3 = json.loads(S.start_computer_task("a fresh task"))
    print("  ->", {k: r3[k] for k in ("job_id", "status") if k in r3})
    assert "job_id" in r3, "rig should be free after terminal"

    print("\n[PASS] full MCP job lifecycle exercised end-to-end with zero hardware.")


if __name__ == "__main__":
    main()
