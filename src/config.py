"""Loads configuration and secrets from the project .env.

Secrets never get hard-coded or logged. The sandbox is given only what a single
run needs, and the Stripe key stays on the host unless a run explicitly needs it.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# LLM (TokenRouter, OpenAI-compatible)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.tokenrouter.com/v1")
LLM_API_KEY = os.environ.get("TOKENROUTER_API_KEY", "")
# A capable coding model; override with TOOLSMITH_MODEL.
MODEL = os.environ.get("TOOLSMITH_MODEL", "anthropic/claude-sonnet-5.5")

# Stripe (test mode only)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_ACCOUNT_ID = os.environ.get("STRIPE_ACCOUNT_ID", "")

# CreateOS sandbox
CREATEOS_API_KEY = os.environ.get("CREATEOS_API_KEY", "")
CREATEOS_BIN = os.environ.get(
    "CREATEOS_BIN",
    str(ROOT.parent / "toolchain" / "bin" / "createos.exe"),
)

# Guardrail: money actions at or above this (USD cents) need human approval.
APPROVAL_CAP_CENTS = int(os.environ.get("TOOLSMITH_CAP_CENTS", "20000"))  # $200

# Hosts the sandbox is allowed to reach while running a tool.
# Stripe for the API; the two PyPI hosts only while installing deps.
STRIPE_EGRESS = ["api.stripe.com"]
INSTALL_EGRESS = ["api.stripe.com", "pypi.org", "files.pythonhosted.org"]


def require(name: str, value: str) -> str:
    if not value:
        raise SystemExit(f"Missing {name} in .env — see README.")
    return value
