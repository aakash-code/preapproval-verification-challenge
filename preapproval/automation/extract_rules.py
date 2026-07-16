"""Rule-based PDF extraction (no LLM).

Reads a completed pre-approval application PDF with ``pdfplumber`` and builds an
``ApplicationRequest`` using deterministic layout/keyword rules. Verified
against all ten sample forms. Only raises ``PipelineError`` when the PDF has no
extractable text/tables at all (i.e. is not a valid form).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pdfplumber

from ..errors import PipelineError
from ..models import ApplicationRequest, Category, ChecklistAnswer

logger = logging.getLogger("preapproval.automation.extract")

_CHECK_GLYPHS = ("☑", "✓", "✔", "x", "X")
_ANY_CHECKBOX = ("☑", "☐", "✓", "✔", "✗", "✘")

_URL_RE = re.compile(r"https?://\S+")
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")

# Category detection: (keyword substrings, category). First match wins.
# "community class" has its own explicit entry (not just the fallback) so a
# genuine Community Class form title is recognized as a confident match, not
# flagged as an uncertain default — see _title_keyword_matched below.
_CATEGORY_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("pre-approval appeals", "appeals form"), "appeal"),
    (("coaching",), "coaching"),
    (("household related items",), "hri"),
    (("other than personal services", "otps"), "otps"),
    (("transition program",), "transition_program"),
    (("membership", "health club"), "membership"),
    (("community class",), "community_class"),
]


def _norm_label(text: str) -> str:
    return text.replace("’", "'").strip().lower()


def _is_label_row(row: List[Any]) -> bool:
    cells = [c for c in row if c not in (None, "")]
    if not cells:
        return False
    for c in cells:
        s = str(c)
        if "$" in s or any(g in s for g in _ANY_CHECKBOX):
            return False
    return True


def _is_yesno_table(table: List[List[Any]]) -> bool:
    if not table:
        return False
    header = [str(c).strip() if c is not None else "" for c in table[0]]
    if header and "please answer" in header[0].lower():
        return True
    non_empty = [h for h in header if h]
    if len(non_empty) >= 2 and non_empty[-1].upper() == "NO" and non_empty[-2].upper() == "YES":
        return True
    return False


_DEFAULT_CATEGORY = "community_class"


def _title_keyword_matched(text: str) -> bool:
    """Whether any category keyword matched the form title (first 6 lines)."""
    head = _norm_label("\n".join(text.splitlines()[:6]))
    return any(
        any(k in head for k in keywords) for keywords, _cat in _CATEGORY_RULES
    )


def _detect_category(text: str) -> str:
    head = "\n".join(text.splitlines()[:6])
    head = _norm_label(head)
    for keywords, cat in _CATEGORY_RULES:
        if any(k in head for k in keywords):
            return cat
    return _DEFAULT_CATEGORY


def _extract_fields(tables: List[List[List[Any]]]) -> Dict[str, str]:
    """Concatenate label->value pairs from every non-YES/NO table."""
    fields: Dict[str, str] = {}
    for table in tables:
        if _is_yesno_table(table):
            continue
        i = 0
        n = len(table)
        while i < n:
            row = table[i]
            if _is_label_row(row) and i + 1 < n:
                value_row = table[i + 1]
                for col, cell in enumerate(row):
                    if cell in (None, ""):
                        continue
                    val = value_row[col] if col < len(value_row) else None
                    if val in (None, ""):
                        continue
                    label = _norm_label(str(cell))
                    if label and label not in fields:
                        fields[label] = str(val).strip()
                i += 2
            else:
                i += 1
    return fields


def _parse_yesno(tables: List[List[List[Any]]]) -> List[ChecklistAnswer]:
    answers: List[ChecklistAnswer] = []
    for table in tables:
        if not _is_yesno_table(table):
            continue
        for row in table[1:]:
            if not row or row[0] in (None, ""):
                continue
            question = str(row[0]).strip()
            yes_cell = str(row[1]) if len(row) > 1 and row[1] else ""
            no_cell = str(row[2]) if len(row) > 2 and row[2] else ""
            if any(g in yes_cell for g in _CHECK_GLYPHS):
                answer: Optional[bool] = True
            elif any(g in no_cell for g in _CHECK_GLYPHS):
                answer = False
            else:
                answer = None
            answers.append(ChecklistAnswer(question=question, answer=answer))
    return answers


# Free-text sections (appeals) are captured from raw text, not tables. Each
# heading's value is the following line(s) up to the next known heading.
_STOP_HEADINGS = (
    "reason for the denial",
    "justification for appeal",
    "justification for why",
    "valued outcome",
    "date of the lp",
    "individual",
    "signature of",
    "for office use",
    "please submit",
    "please answer",
    "date of denial",
    "care manager",
)


def _grab_block(text: str, heading_sub: str) -> Optional[str]:
    """Return the text following the first line containing `heading_sub`,
    up to the next known heading / blank line."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        low = _norm_label(line)
        if heading_sub in low:
            # Value may be inline after a colon on the same line.
            collected: List[str] = []
            after = line.split(":", 1)[1].strip() if ":" in line else ""
            if after:
                collected.append(after)
            for nxt in lines[idx + 1 :]:
                low_n = _norm_label(nxt)
                if not low_n:
                    if collected:
                        break
                    continue
                if any(h in low_n for h in _STOP_HEADINGS):
                    break
                stripped = nxt.strip()
                # Skip wrapped-heading remnants (e.g. a heading that spilled
                # onto the next line and ends with ':' or '):') before content.
                if not collected and stripped.endswith(":"):
                    continue
                collected.append(stripped)
            joined = " ".join(collected).strip()
            return joined or None
    return None


def _classify(fields: Dict[str, str], req: Dict[str, Any], notes: List[str]) -> None:
    """Populate `req` (the kwargs for ApplicationRequest) from labelled fields."""
    fee_parts: List[Tuple[str, str]] = []
    for label, value in fields.items():
        value = value.strip()
        if not value:
            continue
        if "participant" in label and "age" in label:
            m = re.search(r"\d+", value)
            if m:
                req["participant_age"] = int(m.group())
            continue
        if "participant" in label and "name" in label:
            req["participant_name"] = value
            continue
        if "fi coordinator" in label:
            req["fi_coordinator_name"] = value
            continue
        if "broker" in label:
            req["broker_name"] = value
            continue
        if "provider" in label or "vendor" in label:
            if not req.get("provider_name"):
                req["provider_name"] = value
            continue
        if "link" in label or "webpage" in label or "publication" in label:
            m = _URL_RE.search(value)
            if m and not req.get("url"):
                req["url"] = m.group().rstrip(").,")
            continue
        if ("fee" in label or "price" in label or "rate" in label) and "duration" not in label:
            fee_parts.append((str(label).strip(), value))
            continue
        if (
            "class name" in label
            or "item requested" in label
            or "membership name" in label
            or "name of coaching provider" in label
            or "name of transition program provider" in label
            or ("subject area" in label and not req.get("requested_item"))
        ):
            if not req.get("requested_item"):
                req["requested_item"] = value
            continue
        if "safety features" in label:
            notes.append(f"Safety features: {value}")
            continue
        if "valued outcome" in label:
            req["valued_outcome"] = value
            continue
        if "date of denial" in label:
            req["denial_reason"] = f"Denial date: {value}. " + (req.get("denial_reason") or "")
            continue
        if "reason for the denial" in label:
            req["denial_reason"] = (req.get("denial_reason") or "") + value
            continue
        if "justification for appeal" in label or "justification for why" in label:
            req["justification"] = value
            continue

    if fee_parts:
        req["fee_stated"] = "; ".join(f"{lbl}: {val}" for lbl, val in fee_parts)


def extract_request_rules(pdf_path: Path) -> ApplicationRequest:
    pdf_path = Path(pdf_path)
    logger.info("Rule-based extraction of %s", pdf_path.name)

    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            raise PipelineError("The PDF has no pages — this is not a valid application form.")
        page0 = pdf.pages[0]
        text = page0.extract_text() or ""
        tables = page0.extract_tables() or []
        if not tables:
            # Fall back to scanning every page for tables.
            for p in pdf.pages:
                tables.extend(p.extract_tables() or [])
        # Also gather all text for free-text appeal blocks.
        full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    if not text.strip() and not tables:
        raise PipelineError(
            f"Could not extract any readable text or tables from {pdf_path.name}. This usually means it's a "
            f"scanned or image-only PDF with no text layer — the automatic (rule-based) reader needs one. "
            f"Try the AI-assisted engine instead (it reads the page visually), or provide a PDF with selectable text."
        )

    category = _detect_category(text)
    fields = _extract_fields(tables)

    req: Dict[str, Any] = {"category": Category(category)}
    notes: List[str] = []
    if category == _DEFAULT_CATEGORY and not _title_keyword_matched(text):
        notes.append(
            "Category could not be confidently determined from the form title; "
            "defaulted to Community Class — please confirm."
        )
    _classify(fields, req, notes)

    # Appeal free-text blocks (denial reason, justification, valued outcome)
    # live in the running text, not the tables.
    if category == "appeal":
        denial_date = _grab_block(full_text, "date of denial")
        reason = _grab_block(full_text, "reason for the denial")
        pieces: List[str] = []
        if denial_date:
            pieces.append(f"Denial date: {denial_date}.")
        if reason:
            pieces.append(reason)
        if pieces and not req.get("denial_reason"):
            req["denial_reason"] = " ".join(pieces)
        if not req.get("justification"):
            just = _grab_block(full_text, "justification for appeal")
            if just:
                req["justification"] = just
        if not req.get("valued_outcome"):
            vo = _grab_block(full_text, "valued outcome")
            if vo:
                req["valued_outcome"] = vo

    # HRI/OTPS: also try to capture a category-specific justification block.
    if category in ("hri", "otps") and not req.get("justification"):
        just = _grab_block(full_text, "justification for why")
        if just:
            req["justification"] = just

    # Best-effort fallback for participant_name so a well-formed form never
    # fails validation. Provider/item are intentionally left empty when not
    # found (see below) so compute_ambiguous_fields flags them for a reviewer;
    # a fake placeholder would silently defeat that clarification path.
    if not req.get("participant_name"):
        req["participant_name"] = "(not found on form)"
        notes.append("Participant name could not be read from the form.")
    if not req.get("provider_name"):
        # Leave provider_name empty (do NOT backfill a host/placeholder) so it is
        # flagged as ambiguous; explain in the notes only.
        if req.get("url"):
            host = urlparse(req["url"]).netloc or req["url"]
            host = host.split("/")[0]
            if host.lower().startswith("www."):
                host = host[4:]
            notes.append(
                f"No 'Name of Provider/Vendor' label on this form; the provider/vendor "
                f"name could not be read (the item link points to '{host}')."
            )
        else:
            notes.append("No provider/vendor name was stated on the form.")
    if not req.get("requested_item"):
        # Leave requested_item empty (do NOT backfill the provider name) so it is
        # flagged as ambiguous for reviewer confirmation.
        notes.append("No explicit item/class/service label was found on the form.")
    if not req.get("url"):
        notes.append("No website / product link was found on the form.")

    req["form_answers"] = _parse_yesno(tables)
    if notes:
        req["extraction_notes"] = " ".join(notes)

    logger.info(
        "Extracted: category=%s provider=%r url=%r item=%r fee=%r",
        category, req.get("provider_name"), req.get("url"),
        req.get("requested_item"), req.get("fee_stated"),
    )
    return ApplicationRequest(**req)
