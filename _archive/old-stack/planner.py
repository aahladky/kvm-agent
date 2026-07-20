"""Back-compat shim. Canonical code now lives in kvm_agent.orchestration.planner.
Remove once agent_server.py + tools/ import from kvm_agent.* directly.
"""
from kvm_agent.orchestration.planner import *  # noqa: F401,F403
from kvm_agent.orchestration.planner import (  # noqa: F401
    Planner, ClaudePlanner, LocalPlanner, HFPlanner, RulePlanner, run_goal, SYSTEM, _extract_json,
)
