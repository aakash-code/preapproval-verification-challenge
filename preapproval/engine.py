"""Engine resolution: pick the AI (Claude) engine or the deterministic one.

The whole pipeline works with or without an Anthropic API key. When a key is
present the existing Claude-based behavior is the default and stays selectable;
without a key the deterministic rule-based ("automation") engine runs instead.
"""

from __future__ import annotations

import os


def resolve_engine(requested: str | None) -> str:
    """Resolve a requested engine to a concrete "ai" or "automation".

    - "ai" / "automation": honored as-is.
    - "auto" / None / anything else: "ai" if ANTHROPIC_API_KEY is set, else
      "automation".
    """
    if requested in ("ai", "automation"):
        return requested
    return "ai" if os.environ.get("ANTHROPIC_API_KEY") else "automation"
