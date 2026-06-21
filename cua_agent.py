"""Back-compat shim. Canonical code now lives in kvm_agent.models.factory.
Remove once agent_server.py + tools/ import from kvm_agent.* directly.
"""
from kvm_agent.models.factory import *  # noqa: F401,F403
from kvm_agent.models.factory import make_agent  # noqa: F401
