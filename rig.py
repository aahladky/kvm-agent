#!/usr/bin/env python
"""rig.py — single front door for operating the KVM computer-use rig.

The system spans three machines + a couple of helper services, and every session used to start
with the same scavenger hunt ("is llama-server up, on what port, is Ollama serving the right
models, is the capture card free?"). This CLI collapses that into one place. Everything it knows
comes from kvm_agent.config.CFG — the single map of IPs/ports/models — so there is ONE source of
truth, not a literal scattered across 20 scripts.

    python rig.py status        # health-check every component across all 3 machines (read-only)
    python rig.py run "<goal>"  # forward a goal to run_goal_once (planner kind from --kind / CFG)
    python rig.py up | down     # (next increment) start/stop the local llama-server

`status` is non-invasive: HTTP GETs + TCP port checks only, and the Pico is ICMP-pinged (never
TCP-probed — a stray connection wedges its CircuitPython firmware, per CLAUDE.md).
"""
import os, sys, json, socket, subprocess, argparse
import urllib.request
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from kvm_agent.config import CFG

# ── tiny helpers ────────────────────────────────────────────────────────────────────────────
def _short(e):
    return f"{type(e).__name__}: {str(e)[:80]}"


def _host_port(url, default_port=80):
    u = urlparse(url if "//" in url else "//" + url)
    return u.hostname, (u.port or default_port)


def _port_open(host, port, timeout=1.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _http_text(url, timeout=5):
    req = urllib.request.Request(url, headers={"User-Agent": "rig-status"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ── component checks → (rows, ok) ; row = (component, target, STATUS, detail) ─────────────────
def check_planner():
    kind = CFG.planner_kind
    if kind == "claude":
        ok = bool(CFG.anthropic_key)
        return ([("planner (Claude API)", "api.anthropic.com", "OK" if ok else "WARN",
                  f"model={CFG.planner_model}; key {'set' if ok else 'MISSING'}")], ok)
    if kind == "hf":
        return ([("planner (HF router)", "router.huggingface.co", "SKIP",
                  f"model={CFG.planner_model} (not health-checked)")], True)
    if kind != "local":
        return ([("planner", kind, "SKIP", f"kind={kind}")], True)
    url = CFG.planner_local_url.rstrip("/") + "/models"
    tgt = CFG.planner_local_url
    try:
        body = _http_text(url, timeout=5)
    except Exception as e:
        return ([("planner (llama-server)", tgt, "DOWN",
                  f"no service answering — is llama-server up? {_short(e)}")], False)
    try:
        j = json.loads(body)
    except Exception:
        svc = "SearXNG" if "searxng" in body.lower() else "non-JSON/HTML"
        return ([("planner (llama-server)", tgt, "WARN",
                  f"reachable but answered {svc} — WRONG service on this port "
                  f"(Docker shadowing the 0.0.0.0 bind?)")], False)
    data = (j.get("data") or j.get("models") or []) if isinstance(j, dict) else []
    mid = (data[0].get("id") or data[0].get("name")) if data else None
    if mid:
        return ([("planner (llama-server)", tgt, "OK", f"serving {mid}")], True)
    return ([("planner (llama-server)", tgt, "WARN", "endpoint up but lists no model")], False)


def check_ollama():
    try:
        tags = json.loads(_http_text(CFG.ollama_base.rstrip("/") + "/api/tags", timeout=6))
    except Exception as e:
        return ([("Ollama (laptop)", CFG.ollama_base, "DOWN", _short(e)),
                 ("  executor model", CFG.executor_model, "DOWN", "Ollama unreachable"),
                 ("  verifier model", CFG.verifier_model, "DOWN", "Ollama unreachable")], False)
    names = sorted((m.get("name") or "") for m in tags.get("models", []))
    rows = [("Ollama (laptop)", CFG.ollama_base, "OK", f"{len(names)} models loaded")]
    ok = True
    for role, want in (("executor", CFG.executor_model), ("verifier", CFG.verifier_model)):
        hit = any(want.split(":")[0] in n for n in names)
        ok = ok and hit
        rows.append((f"  {role} model", want, "OK" if hit else "DOWN",
                     "present" if hit else "MISSING on laptop"))
    return rows, ok


def check_agent_server():
    busy = _port_open("127.0.0.1", 8088)
    if busy:
        return ([("agent_server / rig", "127.0.0.1:8088", "BUSY",
                  "server is up and holds the capture card — stop it before a live run")], False)
    return ([("agent_server / rig", "127.0.0.1:8088", "OK", "free (capture card available)")], True)


def check_hindsight():
    host, port = _host_port(CFG.hindsight_url, 8888)
    up = _port_open(host, port)
    note = "" if CFG.hindsight_enabled else " [disabled in CFG]"
    if up:
        return ([("hindsight (memory)", f"{host}:{port}", "OK", f"listening{note}")], True)
    return ([("hindsight (memory)", f"{host}:{port}", "WARN",
              f"not listening{note} — recall fails soft, runs still work")], True)


def check_pico():
    ip, port = CFG.pico_ip, CFG.pico_port
    # ICMP ONLY. Never TCP-connect :8000 here — a stray probe wedges the CircuitPython HID firmware.
    try:
        out = subprocess.run(["ping", "-n", "1", "-w", "1200", ip],
                             capture_output=True, text=True, timeout=5)
        up = "ttl=" in out.stdout.lower()
    except Exception:
        up = False
    if up:
        return ([("pico (HID)", f"{ip}:{port}", "OK",
                  "host answers ping (TCP intentionally not probed)")], True)
    return ([("pico (HID)", f"{ip}:{port}", "WARN",
              "no ICMP reply — INCONCLUSIVE (CircuitPython may not answer ping; TCP not probed)")], True)


# ── rendering ─────────────────────────────────────────────────────────────────────────────────
def _render(rows):
    header = ("COMPONENT", "TARGET", "STATUS", "DETAIL")
    table = [header] + [(c, t, f"[{s}]", d) for (c, t, s, d) in rows]
    w = [max(len(r[i]) for r in table) for i in range(3)]   # last column not padded
    for i, r in enumerate(table):
        print(f"{r[0].ljust(w[0])}  {r[1].ljust(w[1])}  {r[2].ljust(w[2])}  {r[3]}")
        if i == 0:
            print(f"{'-'*w[0]}  {'-'*w[1]}  {'-'*w[2]}  {'-'*6}")


def cmd_status(_args):
    print(f"rig status  —  planner kind: {CFG.planner_kind}\n")
    rows, notready = [], []
    pr, p_ok = check_planner();        rows += pr
    if CFG.planner_kind == "local" and not p_ok:
        notready.append("planner endpoint not serving the model")
    orows, o_ok = check_ollama();      rows += orows
    if not o_ok:
        notready.append("Ollama / executor / verifier not ready")
    arows, free = check_agent_server(); rows += arows
    if not free:
        notready.append("agent_server is holding the capture card")
    rows += check_hindsight()[0]
    rows += check_pico()[0]
    _render(rows)
    print()
    if not notready:
        print("READY — planner, Ollama (executor+verifier) and a free capture card all good.")
        return 0
    print("NOT READY: " + "; ".join(notready))
    return 1


def cmd_run(args):
    kind = args.kind or CFG.planner_kind
    cmd = [sys.executable, os.path.join(HERE, "tools", "run_goal_once.py"),
           "--kind", kind, "--plan", args.goal]
    print(f"[rig] (tip: run `python rig.py status` first)\n[rig] -> {' '.join(cmd)}")
    return subprocess.call(cmd)


# Canonical local-server launch (single source of truth). Port comes from CFG; model files live in
# C:\Users\aahla. NOTE: actually starting it (detached) + bringing up Ollama over SSH is the next
# increment — for now `up`/`down` print the blessed command so it lives in ONE place.
def _llama_cmd():
    _, port = _host_port(CFG.planner_local_url, 8090)
    exe = r"C:\Users\aahla\llama-b9692-bin-win-vulkan-x64\llama-server.exe"
    return (f'"{exe}" -m Qwen3.5-9B-UD-Q4_K_XL.gguf --mmproj mmproj-F16.gguf --device Vulkan0 '
            f'-ngl 99 -c 16384 --image-min-tokens 1024 -np 1 --reasoning-budget 0 '
            f'--host 127.0.0.1 --port {port}   (run from C:\\Users\\aahla)')


def cmd_up(_args):
    print("[rig] up is not auto-wired yet. Canonical local-planner launch (run on the desktop):\n")
    print("  " + _llama_cmd())
    print("\n  (reasoning-budget 0 = fast/no-think; non-zero = on but UNBOUNDED on build b9692.)")
    return 0


def cmd_down(_args):
    print("[rig] down is not auto-wired yet. To stop the local planner server:\n")
    print("  taskkill /F /IM llama-server.exe")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="rig", description="Front door for the KVM computer-use rig.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="health-check every component (read-only)").set_defaults(fn=cmd_status)
    pr = sub.add_parser("run", help="forward a goal to run_goal_once")
    pr.add_argument("goal")
    pr.add_argument("--kind", default=None, help="local|claude|hf|rule (default: CFG.planner_kind)")
    pr.set_defaults(fn=cmd_run)
    sub.add_parser("up", help="(next increment) start the local llama-server").set_defaults(fn=cmd_up)
    sub.add_parser("down", help="(next increment) stop the local llama-server").set_defaults(fn=cmd_down)
    args = ap.parse_args()
    if not getattr(args, "fn", None):
        return cmd_status(args)   # bare `python rig.py` == status
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
