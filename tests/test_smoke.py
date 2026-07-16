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

# A real sample filename, used where a valid PDF selection is needed.
SAMPLES_FOR_TEST = ["Sample-01---Community-Class-GallopNYC.pdf"]


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
    # Without a key and without explicitly requesting engine=ai, a bogus sample
    # must still be rejected (400) before any engine logic runs — the review
    # form now defaults to the automation engine, which needs no key.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/review", data={"sample": "no-such-sample.pdf"})
    assert resp.status_code == 400


def test_review_engine_ai_without_key_shows_setup(client, monkeypatch):
    # Explicitly requesting the AI engine with no key shows the setup page — but
    # only for a *valid* PDF selection (validation still comes first).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sample = next(iter(SAMPLES_FOR_TEST))
    resp = client.post("/review", data={"sample": sample, "engine": "ai"})
    assert resp.status_code == 200
    assert "ANTHROPIC_API_KEY" in resp.text


# ---- automation extraction ---------------------------------------------

def _sample_paths():
    from preapproval.web.app import SAMPLES_DIR

    return sorted(SAMPLES_DIR.glob("*.pdf"))


def test_extract_rules_all_samples():
    from preapproval.automation.extract_rules import extract_request_rules

    paths = _sample_paths()
    assert len(paths) == 10, "expected 10 sample PDFs"
    for p in paths:
        req = extract_request_rules(p)
        assert isinstance(req, ApplicationRequest)
        assert isinstance(req.category, Category)
        assert req.category in set(Category)
        assert req.participant_name.strip(), f"{p.name}: empty participant_name"


def test_extract_rules_category_detection():
    from preapproval.automation.extract_rules import extract_request_rules

    expected = {
        "Sample-01": "community_class",
        "Sample-02": "community_class",
        "Sample-03": "coaching",
        "Sample-04": "membership",
        "Sample-05": "membership",
        "Sample-06": "hri",
        "Sample-07": "hri",
        "Sample-08": "otps",
        "Sample-09": "transition_program",
        "Sample-10": "appeal",
    }
    for p in _sample_paths():
        key = next((k for k in expected if k in p.name), None)
        assert key, f"unexpected sample file {p.name}"
        req = extract_request_rules(p)
        assert req.category.value == expected[key], f"{p.name} -> {req.category.value}"


# ---- engine resolution --------------------------------------------------

def test_resolve_engine(monkeypatch):
    from preapproval.engine import resolve_engine

    # Explicit choices pass through regardless of the environment.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve_engine("ai") == "ai"
    assert resolve_engine("automation") == "automation"

    # auto/None resolve on the key's presence.
    assert resolve_engine("auto") == "automation"
    assert resolve_engine(None) == "automation"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    assert resolve_engine("auto") == "ai"
    assert resolve_engine(None) == "ai"
    # Explicit automation still wins even when a key is set.
    assert resolve_engine("automation") == "automation"


# ---- audit database -----------------------------------------------------

def test_db_roundtrip(tmp_path, monkeypatch):
    from preapproval import db

    db_path = tmp_path / "audit.db"
    # Point save_review's init_db at the temp DB.
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_path)

    result = _canned_result()
    out_dir = tmp_path / "out"
    write_report(result, out_dir)  # creates evidence/ dir if any

    rid = db.save_review(result, out_dir, "sample-xx", "automation")
    assert rid > 0

    conn = db.init_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == len(result.findings)

        # Re-saving the same review_name upserts (no duplicate rows).
        db.save_review(result, out_dir, "sample-xx", "automation")
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == len(result.findings)
    finally:
        conn.close()


def test_web_review_engine_field_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "automation" in resp.text
    assert 'name="engine"' in resp.text


# ---- interactive clarification (web two-phase job) ----------------------

def _poll_status(client, job_id, wanted, tries=100, delay=0.1):
    import time

    for _ in range(tries):
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in wanted:
            return data
        time.sleep(delay)
    raise AssertionError(f"job never reached {wanted}; last={data}")


def test_web_clarification_flow(client, monkeypatch, tmp_path):
    """Extraction with a missing URL pauses the job for reviewer confirmation."""
    from preapproval.automation import extract_rules, research_rules
    from preapproval.web import app as webapp

    # Keep all writes off the committed outputs/ and audit DB.
    monkeypatch.setattr(webapp, "OUTPUTS_DIR", tmp_path / "outputs")
    from preapproval import db

    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "audit.db")

    ambiguous_request = ApplicationRequest(
        category=Category.COMMUNITY_CLASS,
        participant_name="Test Participant",
        requested_item="Pottery class",
        provider_name="Clay Studio",
        url=None,  # <- triggers needs_clarification
    )

    def fake_extract(pdf_path):
        return ambiguous_request

    def fake_research(request, evidence_dir, headless=True, log=None):
        return VerificationResult(
            request=request,
            summary="stub research",
            review_timestamp="2026-07-16 00:00 UTC",
        )

    monkeypatch.setattr(extract_rules, "extract_request_rules", fake_extract)
    monkeypatch.setattr(research_rules, "run_research_rules", fake_research)

    sample = next(iter(SAMPLES_FOR_TEST))
    resp = client.post(
        "/review",
        data={"sample": sample, "engine": "automation"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]

    data = _poll_status(client, job_id, {"needs_clarification"})
    assert "url" in data["ambiguous_fields"]
    assert data["pending_request"]["url"] in (None, "")

    resp = client.post(
        f"/jobs/{job_id}/clarify",
        data={"url": "https://example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    data = _poll_status(client, job_id, {"done", "failed"})
    assert data["status"] == "done", data


def test_clarify_unknown_job_is_friendly(client):
    resp = client.post("/jobs/nope/clarify", data={"url": "https://example.com"})
    assert resp.status_code == 404
    assert "Traceback" not in resp.text


# ---- scanned / image-only PDF ------------------------------------------

def test_scanned_pdf_raises_friendly_error(tmp_path):
    import importlib.util

    from preapproval.automation.extract_rules import extract_request_rules
    from preapproval.errors import PipelineError

    fixture_path = Path(__file__).parent / "fixtures" / "make_scanned_pdf.py"
    spec = importlib.util.spec_from_file_location("make_scanned_pdf", fixture_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    source = _sample_paths()[0]  # Sample-01
    scanned = tmp_path / "scanned.pdf"
    mod.make_scanned_pdf(source, scanned)

    with pytest.raises(PipelineError) as excinfo:
        extract_request_rules(scanned)
    msg = str(excinfo.value).lower()
    assert "scanned" in msg or "image-only" in msg
