"""In-memory review-job registry for the web app.

Each review runs in a daemon thread. Progress is appended to the job's
``log_lines`` (the same lines the CLI logs) via a per-job ``JobLogHandler``, so
the progress page can stream them. State is kept in-process only — this is a
single-user local tool; a deployed version would use a real job queue.
"""

from __future__ import annotations

import datetime
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from ..models import ApplicationRequest

logger = logging.getLogger("preapproval.web.jobs")


@dataclass
class Job:
    id: str
    pdf_path: Path
    out_dir: Path
    # "running" | "needs_clarification" | "done" | "failed"
    status: str = "running"
    log_lines: List[str] = field(default_factory=list)
    error: Optional[str] = None
    report_name: Optional[str] = None
    created_at: str = ""
    engine: str = "auto"
    pending_request: Optional["ApplicationRequest"] = None
    ambiguous_fields: List[str] = field(default_factory=list)


# id -> Job
_JOBS: Dict[str, Job] = {}
_LOCK = threading.Lock()


def get_job(job_id: str) -> Optional[Job]:
    with _LOCK:
        return _JOBS.get(job_id)


def _out_name(pdf: Path) -> str:
    import re

    m = re.match(r"(sample-\d+)", pdf.stem.lower())
    return m.group(1) if m else re.sub(r"[^a-z0-9]+", "-", pdf.stem.lower()).strip("-")


def start_review_job(pdf_path: Path, out_root: Path, engine: str = "auto") -> Job:
    """Register a job and spawn a daemon thread that runs the full pipeline.

    ``engine`` is "auto" | "ai" | "automation"; it is resolved to a concrete
    engine inside the thread.
    """
    pdf_path = Path(pdf_path)
    out_root = Path(out_root)
    report_name = _out_name(pdf_path)
    out_dir = out_root / report_name

    job = Job(
        id=uuid.uuid4().hex[:12],
        pdf_path=pdf_path,
        out_dir=out_dir,
        created_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        engine=engine,
        report_name=report_name,
    )
    with _LOCK:
        _JOBS[job.id] = job

    thread = threading.Thread(target=_run, args=(job, report_name, engine), daemon=True)
    thread.start()
    return job


def _attach_job_handler(job: Job):
    """Attach a per-job log handler that streams INFO lines into the job buffer.

    Records carrying a traceback (exc_info) are filtered out so the progress
    console never shows a stack trace — those still go to logs/app.log via the
    file handler. Returns ``(root_logger, handler)`` for later detachment.
    """
    from ..logging_config import JobLogHandler

    handler = JobLogHandler(job.log_lines)
    handler.addFilter(lambda record: not record.exc_info)
    root = logging.getLogger("preapproval")
    root.addHandler(handler)
    return root, handler


def _run(job: Job, report_name: str, engine: str = "auto") -> None:
    # Deferred imports so importing this module never pulls in anthropic/playwright.
    from ..engine import resolve_engine
    from ..errors import PipelineError
    from ..models import compute_ambiguous_fields

    root, handler = _attach_job_handler(job)
    log = logger.info  # streamed to the job buffer, console, and app.log

    try:
        resolved = resolve_engine(engine)
        client = None
        if resolved == "ai":
            import anthropic

            client = anthropic.Anthropic()

        log("=== %s → %s ===", job.pdf_path.name, job.out_dir)
        log("Engine: %s (%s)", resolved,
            "Claude / AI" if resolved == "ai" else "deterministic rules, no API key")
        log("[1/3] Reading the application form...")
        try:
            if resolved == "ai":
                from ..extract import extract_request
                request = extract_request(job.pdf_path, client)
            else:
                from ..automation.extract_rules import extract_request_rules
                request = extract_request_rules(job.pdf_path)
        except PipelineError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Extraction failed for %s", job.pdf_path.name)
            raise PipelineError(
                "Could not read the application form — is this a valid PDF?", original=exc
            ) from exc

        log("      %s: %r @ %s — %s", request.category.value, request.requested_item,
            request.provider_name, request.url)
        if request.extraction_notes:
            log("      note: %s", request.extraction_notes)

        ambiguous = compute_ambiguous_fields(request)
        if ambiguous:
            job.pending_request = request
            job.ambiguous_fields = ambiguous
            job.status = "needs_clarification"
            log("      A few things on the form need your confirmation before the "
                "website check can start: %s", ", ".join(ambiguous))
            return

        if not request.url:
            log("      WARNING: no URL on the form — expect Needs Review results.")
    except PipelineError as e:
        job.error = e.user_message
        job.status = "failed"
        log("FAILED: %s", e.user_message)
        return
    except Exception:  # noqa: BLE001 - never leak a stack trace to the UI
        logger.exception("Unexpected job failure for %s", job.pdf_path.name)
        job.error = "Something went wrong during the review. See logs/app.log for details."
        job.status = "failed"
        log("FAILED: %s", job.error)
        return
    finally:
        root.removeHandler(handler)

    # Not ambiguous: run the research/report tail (manages its own handler).
    _run_research_and_report(job, request, report_name)


def _run_research_and_report(job: Job, request, report_name: str) -> None:
    """Research the website, write the report, record the audit row.

    Shared by ``_run``'s normal path and the post-clarification resume path.
    Owns the research/report try/except/PipelineError logic and sets the job's
    terminal status. Attaches its own per-job log handler so it works whether or
    not a caller has one attached.
    """
    from ..engine import resolve_engine
    from ..errors import PipelineError
    from ..report import write_report

    root, handler = _attach_job_handler(job)
    log = logger.info
    try:
        resolved = resolve_engine(job.engine)
        client = None
        if resolved == "ai":
            import anthropic

            client = anthropic.Anthropic()

        if not request.url:
            log("      WARNING: no URL on the form — expect Needs Review results.")

        log("[2/3] Researching the website and capturing evidence...")
        try:
            if resolved == "ai":
                from ..research import run_research
                result = run_research(request, job.out_dir / "evidence", client, headless=True, log=log)
            else:
                from ..automation.research_rules import run_research_rules
                result = run_research_rules(request, job.out_dir / "evidence", headless=True, log=log)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Website research failed for %s", job.pdf_path.name)
            raise PipelineError(
                "Could not finish checking the provider's website — please try again.", original=exc
            ) from exc

        log("[3/3] Writing the report package...")
        try:
            write_report(result, job.out_dir)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Writing the report failed for %s", job.pdf_path.name)
            raise PipelineError(
                "The review finished but the report could not be saved.", original=exc
            ) from exc

        try:
            from .. import db
            db.save_review(result, job.out_dir, report_name, resolved)
        except Exception:  # noqa: BLE001
            logger.warning("Could not write the audit-DB record for %s", job.pdf_path.name)

        log("DONE → %s/report.html", job.out_dir)
        job.report_name = report_name
        job.status = "done"
    except PipelineError as e:
        job.error = e.user_message
        job.status = "failed"
        log("FAILED: %s", e.user_message)
    except Exception:  # noqa: BLE001 - never leak a stack trace to the UI
        logger.exception("Unexpected job failure for %s", job.pdf_path.name)
        job.error = "Something went wrong during the review. See logs/app.log for details."
        job.status = "failed"
        log("FAILED: %s", job.error)
    finally:
        root.removeHandler(handler)


def resume_after_clarification(job_id: str, corrections: Dict[str, str]) -> bool:
    """Apply reviewer corrections and start the research phase.

    Returns False if the job is unknown or not awaiting clarification. Only
    non-empty corrections overwrite the pending request; ``category`` is
    validated against the ``Category`` enum and ignored if invalid.
    """
    from ..models import Category

    job = get_job(job_id)
    if job is None or job.status != "needs_clarification" or job.pending_request is None:
        return False

    request = job.pending_request
    applied: List[str] = []
    for field_name, value in corrections.items():
        value = (value or "").strip()
        if not value:
            continue
        if field_name == "category":
            try:
                request.category = Category(value)
            except ValueError:
                continue
        elif field_name in ("url", "provider_name", "requested_item"):
            setattr(request, field_name, value)
        else:
            continue
        applied.append(f"{field_name}={value}")

    job.status = "running"
    job.ambiguous_fields = []
    confirm_line = "Reviewer confirmed: " + (", ".join(applied) or "(no changes)")
    job.log_lines.append(confirm_line)
    logger.info("Resuming %s after clarification — %s", job.pdf_path.name, confirm_line)
    report_name = job.report_name or _out_name(job.pdf_path)
    thread = threading.Thread(
        target=_run_research_and_report, args=(job, request, report_name), daemon=True
    )
    thread.start()
    return True
