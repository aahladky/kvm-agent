"""Back-compat shim. Canonical code now lives in kvm_agent.models.uitars.
Remove once agent_server.py + tools/ import from kvm_agent.* directly.
"""
from kvm_agent.models.uitars import *  # noqa: F401,F403
from kvm_agent.models.uitars import UITARSAgent  # noqa: F401
