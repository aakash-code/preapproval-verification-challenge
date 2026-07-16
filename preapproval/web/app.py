"""FastAPI app factory for the local pre-approval verification web app.

Server-rendered (Jinja2) + a little vanilla JS for live polling and chat.
Binds to 127.0.0.1 only (see cli serve). Every route body is wrapped so a
failure logs a traceback and returns a friendly page/JSON — never a stack
trace.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from ..logging_config import REPO_ROOT, setup_logging
from ..models import VerificationResult, format_category
from . import jobs

logger = logging.getLogger("preapproval.web")

_HERE = Path(__file__).resolve().parent
SAMPLES_DIR = REPO_ROOT / "samples"
OUTPUTS_DIR = REPO_ROOT / "outputs"
UPLOADS_DIR = REPO_ROOT / "uploads"
KEY_ENV = "ANTHROPIC_API_KEY"

CHAT_MODEL = "claude-opus-4-8"

# Per-report chat state, keyed by report name: {"result", "messages"}.
_CHAT_STATE: Dict[str, Dict[str, Any]] = {}


def _friendly_sample_name(filename: str) -> str:
    """'Sample-01---Community-Class-GallopNYC.pdf' -> 'Sample 01 — Community Class GallopNYC'."""
    stem = Path(filename).stem
    stem = stem.replace("---", " — ").replace("-", " ")
    return re.sub(r"\s+", " ", stem).strip()


def _list_samples() -> List[Dict[str, str]]:
    if not SAMPLES_DIR.exists():
        return []
    return [
        {"filename": p.name, "friendly": _friendly_sample_name(p.name)}
        for p in sorted(SAMPLES_DIR.glob("*.pdf"))
    ]


def _list_completed_reviews() -> List[Dict[str, str]]:
    reviews: List[Dict[str, str]] = []
    if not OUTPUTS_DIR.exists():
        return reviews
    for result_json in sorted(OUTPUTS_DIR.glob("*/result.json")):
        try:
            result = VerificationResult.model_validate_json(result_json.read_text())
        except Exception:  # noqa: BLE001 - skip unreadable/partial outputs
            logger.warning("Skipping unreadable result.json at %s", result_json)
            continue
        r = result.request
        reviews.append(
            {
                "name": result_json.parent.name,
                "participant": r.participant_name,
                "category": format_category(r.category),
                "provider": r.provider_name or "— (not stated)",
                "review_date": result.review_timestamp,
            }
        )
    return reviews


def _safe_report_dir(name: str) -> Optional[Path]:
    """Resolve outputs/<name>, rejecting traversal. None if outside outputs/."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    resolved = (OUTPUTS_DIR / name).resolve()
    try:
        resolved.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        return None
    return resolved


def create_app() -> FastAPI:
    from ..config import load_env

    load_env()  # picks up .env for local dev; real env vars always win
    setup_logging()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Pre-Approval Verification")
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    def error_page(request: Request, message: str, status_code: int = 500) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": message},
            status_code=status_code,
        )

    def key_is_set() -> bool:
        return bool(os.environ.get(KEY_ENV))

    # ---- dashboard -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        try:
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "samples": _list_samples(),
                    "reviews": _list_completed_reviews(),
                    "key_set": key_is_set(),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to render dashboard")
            return error_page(request, "Could not load the dashboard.")

    # ---- start a review --------------------------------------------------

    @app.post("/review")
    async def review(
        request: Request,
        file: UploadFile | None = None,
        sample: str = Form(default=""),
        engine: str = Form(default="auto"),
    ):
        try:
            # Validate the PDF selection first, so a bogus sample is rejected
            # before any engine/key logic (same as before).
            pdf_path: Optional[Path] = None

            if sample:
                # Must be the basename of an existing samples/*.pdf — nothing else.
                if "/" in sample or "\\" in sample or ".." in sample:
                    return error_page(request, "Invalid sample selection.", status_code=400)
                candidate = SAMPLES_DIR / sample
                if candidate.suffix.lower() != ".pdf" or not candidate.exists():
                    return error_page(request, "That sample form does not exist.", status_code=400)
                pdf_path = candidate
            elif file is not None and file.filename:
                if not file.filename.lower().endswith(".pdf"):
                    return error_page(request, "Please upload a PDF file.", status_code=400)
                UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(file.filename).stem).strip("-") or "form"
                dest = UPLOADS_DIR / f"{safe_stem}-{uuid.uuid4().hex[:8]}.pdf"
                with dest.open("wb") as out:
                    shutil.copyfileobj(file.file, out)
                pdf_path = dest
            else:
                return error_page(request, "Choose a sample or upload a PDF first.", status_code=400)

            # Only require a key when the review will actually use the AI engine.
            from ..engine import resolve_engine

            resolved = resolve_engine(engine)
            if resolved == "ai" and not key_is_set():
                return templates.TemplateResponse(
                    request, "setup.html", {"key_env": KEY_ENV}, status_code=200
                )

            job = jobs.start_review_job(pdf_path, OUTPUTS_DIR, engine=engine)
            return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to start review")
            return error_page(request, "Could not start the review.")

    # ---- job progress ----------------------------------------------------

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str):
        try:
            job = jobs.get_job(job_id)
            if job is None:
                return error_page(request, "That review job was not found.", status_code=404)
            from ..models import Category

            pending = (
                job.pending_request.model_dump(mode="json")
                if job.pending_request is not None else None
            )
            return templates.TemplateResponse(
                request,
                "job.html",
                {
                    "job": job,
                    "pending_request": pending,
                    "categories": [c.value for c in Category],
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to render job page")
            return error_page(request, "Could not load the review progress.")

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        try:
            job = jobs.get_job(job_id)
            if job is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            payload: Dict[str, Any] = {
                "status": job.status,
                "log_lines": list(job.log_lines),
                "error": job.error,
                "report_name": job.report_name,
            }
            if job.status == "needs_clarification":
                payload["ambiguous_fields"] = list(job.ambiguous_fields)
                payload["pending_request"] = (
                    job.pending_request.model_dump(mode="json")
                    if job.pending_request is not None else None
                )
            return payload
        except Exception:  # noqa: BLE001
            logger.exception("Failed to read job status")
            return JSONResponse({"error": "Could not read job status."}, status_code=500)

    @app.post("/jobs/{job_id}/clarify")
    async def clarify(request: Request, job_id: str):
        try:
            # Read the submitted form generically so ANY field name the browser
            # sends reaches resume_after_clarification — no hardcoded allow-list
            # that would silently drop fields FastAPI doesn't recognize.
            form = await request.form()
            corrections = {
                k: str(v) for k, v in form.items() if str(v).strip()
            }
            if not jobs.resume_after_clarification(job_id, corrections):
                return error_page(
                    request,
                    "That review job was not found or is not awaiting clarification.",
                    status_code=404,
                )
            return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to apply clarification for job %s", job_id)
            return error_page(request, "Could not continue the review.")

    # ---- report ----------------------------------------------------------

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def report_page(request: Request, name: str):
        try:
            report_dir = _safe_report_dir(name)
            if report_dir is None or not (report_dir / "result.json").exists():
                return error_page(request, "That report was not found.", status_code=404)

            from ..report import render_html_body

            result = VerificationResult.model_validate_json(
                (report_dir / "result.json").read_text()
            )
            body = render_html_body(result, report_dir)
            # Rewrite evidence/<file> refs to the guarded serving route.
            body = body.replace('src="evidence/', f'src="/reports/{name}/evidence/')
            body = body.replace("src='evidence/", f"src='/reports/{name}/evidence/")
            return templates.TemplateResponse(
                request,
                "report.html",
                {"name": name, "report_body": body,
                 "participant": result.request.participant_name},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to render report %s", name)
            return error_page(request, "Could not load that report.")

    @app.get("/reports/{name}/evidence/{filename}")
    def evidence(request: Request, name: str, filename: str):
        try:
            report_dir = _safe_report_dir(name)
            if report_dir is None:
                return error_page(request, "Evidence not found.", status_code=404)
            if not filename or "/" in filename or "\\" in filename or ".." in filename:
                return error_page(request, "Invalid evidence file.", status_code=400)

            evidence_dir = (report_dir / "evidence").resolve()
            target = (evidence_dir / filename).resolve()
            # Final resolved path must live inside outputs/.
            try:
                target.relative_to(OUTPUTS_DIR.resolve())
            except ValueError:
                return error_page(request, "Evidence not found.", status_code=404)
            if not target.is_file():
                return error_page(request, "Evidence not found.", status_code=404)

            from fastapi.responses import FileResponse

            return FileResponse(str(target))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to serve evidence %s/%s", name, filename)
            return error_page(request, "Could not load that evidence file.")

    # ---- report chat -----------------------------------------------------

    @app.post("/api/reports/{name}/chat")
    async def report_chat(request: Request, name: str):
        try:
            report_dir = _safe_report_dir(name)
            if report_dir is None or not (report_dir / "result.json").exists():
                return JSONResponse({"error": "Report not found."}, status_code=404)

            payload = await request.json()
            message = (payload or {}).get("message", "").strip()
            if not message:
                return JSONResponse({"error": "Empty message."}, status_code=400)

            # No key: deterministic rule-based chat (stateless per message).
            if not key_is_set():
                from .. import chat

                result = VerificationResult.model_validate_json(
                    (report_dir / "result.json").read_text()
                )
                reply, updated = await run_in_threadpool(
                    chat.rule_based_reply, message, result, report_dir
                )
                return JSONResponse({"reply": reply, "updated": updated})

            import anthropic

            from ..chat import TOOLS, build_system_prompt, make_dispatch

            state = _CHAT_STATE.get(name)
            if state is None:
                result = VerificationResult.model_validate_json(
                    (report_dir / "result.json").read_text()
                )
                state = {"result": result, "messages": []}
                _CHAT_STATE[name] = state
            result = state["result"]
            messages: List[Dict[str, Any]] = state["messages"]

            handle = make_dispatch(result, report_dir)
            system = build_system_prompt(result)
            client = anthropic.Anthropic()

            messages.append({"role": "user", "content": message})
            reply_parts: List[str] = []
            updated = False

            while True:
                response = client.messages.create(
                    model=CHAT_MODEL, max_tokens=4000, system=system,
                    tools=TOOLS, messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        reply_parts.append(block.text.strip())
                    if block.type == "tool_use":
                        out = handle(block.name, block.input)
                        updated = True
                        results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": out}
                        )
                if not results:
                    break
                messages.append({"role": "user", "content": results})

            if updated:
                # Always regenerate the report files after any tool use.
                from ..report import write_report

                await run_in_threadpool(write_report, result, report_dir)

            return JSONResponse({"reply": "\n\n".join(reply_parts) or "(done)", "updated": updated})
        except Exception:  # noqa: BLE001
            logger.exception("Chat failed for report %s", name)
            return JSONResponse(
                {"error": "Sorry, the assistant could not respond. See logs/app.log."},
                status_code=500,
            )

    return app
