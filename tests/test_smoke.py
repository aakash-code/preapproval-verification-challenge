"""Smoke tests: checklists load, reports render, web routes behave safely.

No test here needs ANTHROPIC_API_KEY or the network — the key is cleared (or set
to a dummy) so no API call can happen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from preapproval import checklists
from preapproval.checklists import load_checklist
from preapproval.models import (
    ApplicationRequest,
    Category,
    Finding,
    FindingStatus,
    RateComparison,
    VerificationResult,
)
from preapproval.report import write_report

CATEGORIES = [
    "community_class",
    "coaching",
    "membership",
    "hri",
    "otps",
    "transition_program",
    "appeal",
]


# ---- checklists ---------------------------------------------------------

def test_all_seven_checklists_load():
    for cid in CATEGORIES:
        cl = load_checklist(cid)
        assert cl["criteria"], f"{cid} has no criteria"


def test_appeal_inherits_community_class_criteria():
    community = load_checklist("community_class")
    appeal = load_checklist("appeal")
    community_ids = {c["id"] for c in community["criteria"]}
    appeal_ids = {c["id"] for c in appeal["criteria"]}
    # Every community_class criterion is present in the appeal checklist...
    assert community_ids <= appeal_ids
    # ...plus the appeal-specific one.
    assert "denial_reason_evidence" in appeal_ids


# ---- report -------------------------------------------------------------

def _canned_result() -> VerificationResult:
    request = ApplicationRequest(
        category=Category.COMMUNITY_CLASS,
        participant_name="Jordan Rivera",
        requested_item="Beginner pottery class",
        provider_name="Clay Studio NYC",
        url="https://claystudio.example",
        fee_stated="$40 per session",
    )
    findings = [
        Finding(
            criterion_id="published_fees",
            criterion_text="Class has published fees",
            status=FindingStatus.FOUND,
            note="Public price list shows $40 per session.",
            evidence_files=["evidence-published_fees-fee.png"],
        ),
        Finding(
            criterion_id="budget_approved",
            criterion_text="Community classes are currently approved in the participant's budget",
            status=FindingStatus.INTERNAL,
            note="Depends on internal records.",
        ),
    ]
    return VerificationResult(
        request=request,
        findings=findings,
        rate_comparison=RateComparison(
            fee_on_form="$40 per session", fee_on_website="$40 per session", verdict="matches application exactly"
        ),
        pages_visited=["https://claystudio.example"],
        summary="The pottery class is publicly offered with a matching published fee.",
        review_timestamp="2026-07-15 12:00 UTC",
    )


def test_write_report_produces_package(tmp_path: Path):
    result = _canned_result()
    write_report(result, tmp_path)

    for name in ("report.md", "report.html", "result.json"):
        assert (tmp_path / name).exists(), f"{name} missing"

    md_text = (tmp_path / "report.md").read_text()
    html_text = (tmp_path / "report.html").read_text()

    assert "Jordan Rivera" in md_text
    assert "Clay Studio NYC" in md_text
    assert "matches application exactly" in md_text
    assert "Internal" in md_text  # internal finding rendered
    assert "Jordan Rivera" in html_text
    assert "<table>" in html_text


# ---- web ----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from preapproval.web.app import create_app

    return TestClient(create_app())


def test_index_lists_samples(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # Sample picker should list a known sample by friendly name.
    assert "GallopNYC" in resp.text


def test_report_nonexistent_is_friendly_404(client):
    resp = client.get("/reports/definitely-not-a-real-report")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()
    assert "Traceback" not in resp.text


def test_evidence_route_rejects_traversal(client, tmp_path, monkeypatch):
    from preapproval.web import app as webapp

    # A secret outside outputs/ that must never be served.
    secret = webapp.REPO_ROOT / "SMOKE_SECRET.txt"
    secret.write_text("TOP-SECRET-DO-NOT-LEAK")
    try:
        # Encoded traversal attempt.
        resp = client.get("/reports/anything/evidence/..%2f..%2fSMOKE_SECRET.txt")
        assert resp.status_code in (400, 404)
        assert "TOP-SECRET-DO-NOT-LEAK" not in resp.text
    finally:
        secret.unlink(missing_ok=True)


def test_review_rejects_bogus_sample(client, monkeypatch):
    # Set a dummy key so the request passes the key check and reaches sample
    # validation; a bogus sample must be rejected before any job/API call.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    resp = client.post("/review", data={"sample": "../etc/passwd"})
    assert resp.status_code == 400
    resp2 = client.post("/review", data={"sample": "no-such-sample.pdf"})
    assert resp2.status_code == 400


def test_review_without_key_shows_setup(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/review", data={"sample": "no-such-sample.pdf"})
    assert resp.status_code == 200
    assert "ANTHROPIC_API_KEY" in resp.text
