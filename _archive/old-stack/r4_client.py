"""Back-compat shim. Canonical code now lives in kvm_agent.hardware.pico_client.
Kept so existing imports (`from r4_client import R4`) keep working post-consolidation.
Remove once agent_server.py + tools/ import from kvm_agent.* directly.
"""
from kvm_agent.hardware.pico_client import *  # noqa: F401,F403
from kvm_agent.hardware.pico_client import (  # noqa: F401
    R4, PicoClient, norm_key, NAME_ALIASES, R4_IP, R4_PORT,
)
