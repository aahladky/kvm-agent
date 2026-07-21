"""Path setup for the suite: agent_loop_holo (repo root), battery (tools/) and
pikvm_proto (appliance/pi5) are import homes but not installed packages -- the known
packaging gap (pyproject NOTE). Each test file also inserts what it needs at the top
so `python tests/test_x.py` keeps working without pytest's conftest machinery."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "tools"), os.path.join(_ROOT, "appliance", "pi5")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
