"""Data models shared across extraction, research, and reporting."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Category(str, Enum):
    COMMUNITY_CLASS = "community_class"
    COACHING = "coaching"
    MEMBERSHIP = "membership"
    HRI = "hri"
    OTPS = "otps"
    TRANSITION_PROGRAM = "transition_program"
    APPEAL = "appeal"


_ACRONYM_CATEGORIES = {"hri", "otps"}


def format_category(category: "Category | str") -> str:
    """Display-friendly category name, keeping known acronyms uppercase."""
    value = category.value if isinstance(category, Category) else str(category)
    if value in _ACRONYM_CATEGORIES:
        return value.upper()
    return value.replace("_", " ").title()


class ChecklistAnswer(BaseModel):
    """A YES/NO question as answered on the form itself."""

    question: str
    answer: Optional[bool] = Field(
        None, description="True for YES, False for NO, null if left blank"
    )


class ApplicationRequest(BaseModel):
    """Everything the tool needs, extracted from a completed application PDF."""

    category: Category = Field(description="Which of the seven form types this is")
    participant_name: str
    participant_age: Optional[int] = None
    fi_coordinator_name: Optional[str] = None
    broker_name: Optional[str] = None
    requested_item: str = Field(
        description="The specific class / membership / item / program requested"
    )
    provider_name: str
    url: Optional[str] = Field(
        None, description="Link to webpage / place of publication / product link on the form"
    )
    fee_stated: Optional[str] = Field(
        None, description="Fee / rate / price exactly as written on the form, e.g. '$80 per 30-minute session'"
    )
    valued_outcome: Optional[str] = None
    form_answers: List[ChecklistAnswer] = Field(
        default_factory=list, description="The YES/NO checklist answers on the form"
    )
    denial_reason: Optional[str] = Field(
        None, description="Appeals only: the stated reason the original request was denied"
    )
    justification: Optional[str] = Field(
        None, description="Appeals only: the applicant's justification for appealing"
    )
    extraction_notes: Optional[str] = Field(
        None, description="Anything ambiguous, missing, or hard to read on the form"
    )


class FindingStatus(str, Enum):
    FOUND = "Found"
    NOT_FOUND = "Not Found"
    NEEDS_REVIEW = "Needs Review"
    INTERNAL = "Internal — not website-verifiable"


class Finding(BaseModel):
    criterion_id: str
    criterion_text: str
    status: FindingStatus
    note: str = Field(description="Plain-language note on what the page shows, quoting the relevant line where possible")
    evidence_url: Optional[str] = None
    evidence_files: List[str] = Field(default_factory=list)


class RateComparison(BaseModel):
    fee_on_form: Optional[str] = None
    fee_on_website: Optional[str] = None
    verdict: str = Field(
        default="not checked",
        description="e.g. 'matches application exactly', 'differs', 'not published'",
    )
    cap_check: Optional[str] = Field(
        None, description="Whether the stated fee is within the program cap for this category"
    )


class VerificationResult(BaseModel):
    request: ApplicationRequest
    findings: List[Finding] = Field(default_factory=list)
    rate_comparison: RateComparison = Field(default_factory=RateComparison)
    pages_visited: List[str] = Field(default_factory=list)
    summary: str = ""
    review_timestamp: str = ""
