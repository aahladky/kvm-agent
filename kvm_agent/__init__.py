"""kvm_agent - consolidated KVM-over-IP computer-use agent.

Importing the package seeds the OpenAI/Ollama env defaults from config.CFG,
preserving the implicit-env contract the modules relied on when each one called
os.environ.setdefault(...) at import time. Values equal the prior hardcoded
literals, so behavior is unchanged.
"""
import os
from kvm_agent.config import CFG

os.environ.setdefault("OPENAI_BASE_URL", CFG.openai_base)
os.environ.setdefault("OPENAI_API_KEY", CFG.openai_key)
