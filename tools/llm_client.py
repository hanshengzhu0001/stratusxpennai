"""
LLM client configuration for the baseline module.

Uses the Stratus API (OpenAI-compatible) by default.
Set env vars in .env or export them in your shell.

This file is the ONLY place where baseline LLM connection details live.
"""
import os

API_KEY = os.environ.get("BASELINE_LLM_API_KEY", "PLACEHOLDER_KEY")
BASE_URL = os.environ.get("BASELINE_LLM_BASE_URL", "https://api.stratus.run/v1")
MODEL = os.environ.get("BASELINE_LLM_MODEL", "stratus-x1ac-base-claude-sonnet-4-5")


def is_configured() -> bool:
    """Check if a real API key has been provided."""
    return API_KEY.strip() != "PLACEHOLDER_KEY" and len(API_KEY.strip()) > 0
