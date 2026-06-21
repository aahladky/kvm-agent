"""Back-compat shim. Canonical code now lives in kvm_agent.models.evocua.
Remove once tools/ (operate.py, run_probe.py, evocua_mcp_server.py) import from kvm_agent.* directly.
"""
from kvm_agent.models.evocua import *  # noqa: F401,F403
from kvm_agent.models.evocua import EvoCUAAgent  # noqa: F401
