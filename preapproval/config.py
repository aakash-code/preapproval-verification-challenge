"""Local-development secrets loading.

Loads a ``.env`` file from the repo root into the process environment, if one
exists. This is a **local development convenience only**:

- ``.env`` is git-ignored (see ``.gitignore``) and must never be committed —
  this repo is public, so anything written there is a real credential leak.
- Real environment variables always win over ``.env`` values
  (``override=False``), so a deployment that sets ``ANTHROPIC_API_KEY`` (or
  any other key) via its platform's secrets mechanism is never silently
  shadowed by a stray local ``.env`` file.
- In production, don't ship a ``.env`` file at all — set real environment
  variables through the deployment platform (systemd unit, container env,
  cloud secrets manager, etc.). ``load_env()`` is a no-op if no ``.env`` is
  present, so this is safe to call unconditionally in every entry point.

Copy ``.env.example`` to ``.env`` and fill in real values to use this.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .logging_config import REPO_ROOT

logger = logging.getLogger("preapproval.config")

_loaded = False


def load_env() -> None:
    """Load ``<repo_root>/.env`` into the environment, if present. Idempotent."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv

        # override=False: real environment variables (e.g. set by the shell,
        # a deploy platform, or a secrets manager) always take precedence
        # over whatever is in .env.
        load_dotenv(dotenv_path=env_path, override=False)
        logger.info("Loaded local settings from %s", env_path)
    except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
        logger.warning(
            "%s exists but python-dotenv is not installed — run "
            "`pip install -r requirements.txt`. Falling back to real "
            "environment variables only.",
            env_path,
        )
