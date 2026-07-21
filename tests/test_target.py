"""
test_target.py — OFFLINE test for the manual power/reset seam.

    python tests/test_target.py
"""
import sys, os, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.hardware import target

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

calls = []
real_input = builtins.input
builtins.input = lambda prompt="": calls.append(prompt) or ""
try:
    target.reboot()
finally:
    builtins.input = real_input
check("reboot() blocks on operator confirmation exactly once", len(calls) == 1)
check("reboot() prompt tells the operator what to do", "power-cycle" in calls[0].lower())
check("is_up() is True after operator confirmation (v1 contract)", target.is_up() is True)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
