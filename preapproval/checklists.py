"""Config-driven checklists.

The mapping "form -> checklist -> which items are website-verifiable -> how to
check each" is knowledge the system applies, kept in config/checklists/*.yaml
so a non-engineer can add a new category without touching code. An `inherits`
key lets a checklist (e.g. appeals) extend another one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

CHECKLIST_DIR = Path(__file__).resolve().parent.parent / "config" / "checklists"


def _load_raw(checklist_id: str) -> Dict[str, Any]:
    path = CHECKLIST_DIR / f"{checklist_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No checklist config for category '{checklist_id}' — expected {path}. "
            f"Add a YAML file there to support a new form type."
        )
    with open(path) as f:
        return yaml.safe_load(f)


def load_checklist(checklist_id: str) -> Dict[str, Any]:
    """Load a checklist, resolving single-level `inherits`."""
    raw = _load_raw(checklist_id)
    if "inherits" in raw:
        base = _load_raw(raw["inherits"])
        merged_criteria: List[Dict[str, Any]] = list(base.get("criteria", []))
        merged_criteria.extend(raw.get("criteria", []))
        raw = {**base, **raw, "criteria": merged_criteria}
    return raw


def website_verifiable(checklist: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in checklist["criteria"] if c.get("website_verifiable")]


def internal_only(checklist: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in checklist["criteria"] if not c.get("website_verifiable")]
