"""Load the Gemini API key from the environment or the project's local env file."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_ENV_FILENAMES = ("Secret.env", ".env")
_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_API_KEY_FILE_VARS = _API_KEY_ENV_VARS + ("KEY",)


def resolve_api_key() -> str | None:
    """Return the API key from the process env, else from the local env file."""
    for name in _API_KEY_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value.strip()

    for filename in _ENV_FILENAMES:
        env_file = PROJECT_ROOT / filename
        if not env_file.is_file():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in _API_KEY_FILE_VARS and value.strip():
                return value.strip().strip("'\"")
    return None
