"""Deterministic command parser for the no-API-key chat fallback.

When no ``ANTHROPIC_API_KEY`` is set the reviewer can still adjust a completed
report in plain language. This module turns a single free-text line into a
tool call (the same tools ``chat.make_dispatch`` dispatches), using regexes and
a fuzzy criterion match. It returns ``None`` when nothing matches (so the
caller can show a help message) or when a criterion reference is ambiguous.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple, Union

from ..models import Finding

# Free-text status word(s) -> canonical FindingStatus value.
_STATUS_ALIASES = {
    "found": "Found",
    "not found": "Not Found",
    "needs review": "Needs Review",
    "review": "Needs Review",
    "internal": "Internal — not website-verifiable",
}

# Longest aliases first so "not found" wins over "found".
_STATUS_PATTERN = "|".join(
    re.escape(k) for k in sorted(_STATUS_ALIASES, key=len, reverse=True)
)

_MARK_RE = re.compile(
    r"^\s*(?:mark|change|set)\s+(?P<crit>.+?)\s+(?:to|as)\s+"
    r"(?P<status>" + _STATUS_PATTERN + r")\s*$",
    re.IGNORECASE,
)

# "add [a] note [to <criterion>] : <text>"  or  "... - <text>"
_NOTE_RE = re.compile(
    r"^\s*add\s+(?:a\s+)?note\s*(?:to\s+(?P<crit>.+?))?\s*[:\-]\s*(?P<text>.+?)\s*$",
    re.IGNORECASE,
)

_SUMMARY_RE = re.compile(
    r"^\s*(?:set\s+|update\s+)?summary\s*(?:to|:)?\s+(?P<text>.+?)\s*$",
    re.IGNORECASE,
)

_REGEN_RE = re.compile(r"^\s*regenerate(?:\s+the)?\s+report\s*$", re.IGNORECASE)


def _find_criterion(query: str, findings: List[Finding]) -> Union[str, List[str]]:
    """Resolve a criterion reference to a single ``criterion_id``.

    Match is: exact ``criterion_id`` first, else case-insensitive substring
    against each finding's ``criterion_text`` (or ``criterion_id``). Returns the
    matched ``criterion_id`` on a unique match, or a list of candidate
    "``criterion_id``: text" strings when the match is ambiguous (0 or >1).
    """
    query = query.strip()
    q = query.lower()

    # Exact criterion_id wins outright.
    for f in findings:
        if f.criterion_id.lower() == q:
            return f.criterion_id

    matches = [
        f
        for f in findings
        if q in f.criterion_id.lower() or q in f.criterion_text.lower()
    ]
    if len(matches) == 1:
        return matches[0].criterion_id
    # 0 or >1 -> ambiguous; hand back candidate descriptions for the caller.
    return [f"{f.criterion_id}: {f.criterion_text}" for f in matches]


def parse_command(
    text: str, findings: List[Finding]
) -> Optional[Tuple[str, dict]]:
    """Parse one reviewer line into ``(tool_name, args)`` or ``None``.

    ``None`` means "nothing matched" OR "criterion reference was ambiguous" —
    in both cases the caller shows a help / disambiguation message. When a
    criterion is ambiguous the args' would-be criterion is unresolved, so we
    surface that by returning ``None`` too (the caller re-runs ``_find_criterion``
    to list candidates).
    """
    if not text or not text.strip():
        return None

    # Try the summary-update pattern before the mark/change/set pattern: a loose
    # criterion capture in _MARK_RE would otherwise swallow "set summary to X"
    # (crit="summary"), find no matching criterion, and silently return None.
    m = _SUMMARY_RE.match(text)
    if m:
        return ("update_summary", {"summary": m.group("text").strip()})

    m = _MARK_RE.match(text)
    if m:
        crit = _find_criterion(m.group("crit"), findings)
        if isinstance(crit, list):
            return None
        status = _STATUS_ALIASES[m.group("status").lower()]
        return ("update_finding", {"criterion_id": crit, "status": status})

    m = _NOTE_RE.match(text)
    if m:
        note_text = m.group("text").strip()
        crit_ref = m.group("crit")
        if crit_ref:
            crit = _find_criterion(crit_ref, findings)
            if isinstance(crit, list):
                return None
            return ("update_finding", {"criterion_id": crit, "note": note_text})
        # No criterion reference -> append to the summary. The caller supplies
        # the current summary and builds the final text.
        return ("update_summary", {"note": note_text})

    if _REGEN_RE.match(text):
        return ("regenerate_report", {})

    return None
