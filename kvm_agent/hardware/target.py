"""
target.py — physical-target power/reset seam
(docs/PLAN_2026-07-20_physical_target_move.md §2).

Replaces the libvirt VMController (archived 2026-07-20 with the VM stack). v1 is
MANUAL: the operator power-cycles the laptop and confirms the desktop is up. The
power-control decision (WoL vs smart plug vs hybrid) is deliberately deferred until
the hardware is in front of us; wol/smartplug backends slot in behind these same two
functions without touching callers (tools/battery.py).
"""


def reboot():
    """Full restart of the physical target between battery tasks. v1: the operator
    does it by hand; their Enter IS the readiness signal (desktop up and settled)."""
    input("[target] Power-cycle the laptop (full shutdown + boot). "
          "Press Enter when the desktop is up and settled... ")


def is_up():
    """v1 contract: True once reboot() returned (the operator confirmed). When a real
    backend lands this becomes an actual readiness probe."""
    return True
