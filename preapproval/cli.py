"""Command-line entry point.

  python -m preapproval review samples/Sample-01.pdf          # full workflow
  python -m preapproval review samples/*.pdf --out outputs    # batch
  python -m preapproval chat outputs/sample-01                # revise a report
  python -m preapproval serve --port 8000                     # local web app
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger("preapproval.cli")

_KEY_ENV = "ANTHROPIC_API_KEY"


def _out_name(pdf: Path) -> str:
    m = re.match(r"(sample-\d+)", pdf.stem.lower())
    return m.group(1) if m else re.sub(r"[^a-z0-9]+", "-", pdf.stem.lower()).strip("-")


def _run_review(pdf: Path, out_dir: Path, client, headed: bool) -> None:
    """Run the three pipeline stages, wrapping each in a friendly PipelineError."""
    from .errors import PipelineError
    from .extract import extract_request
    from .report import write_report
    from .research import run_research

    logger.info("[1/3] Reading the application form...")
    try:
        request = extract_request(pdf, client)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Extraction failed for %s", pdf.name)
        raise PipelineError(
            "Could not read the application form — is this a valid PDF?", original=exc
        ) from exc

    logger.info(
        "      %s: %r @ %s — %s",
        request.category.value, request.requested_item, request.provider_name, request.url,
    )
    if request.extraction_notes:
        logger.info("      note: %s", request.extraction_notes)
    if not request.url:
        logger.info("      WARNING: no URL on the form — the agent will report this; expect Needs Review results.")

    logger.info("[2/3] Researching the website and capturing evidence...")
    try:
        result = run_research(request, out_dir / "evidence", client, headless=not headed, log=logger.info)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Website research failed for %s", pdf.name)
        raise PipelineError(
            "Could not finish checking the provider's website — please try again.", original=exc
        ) from exc

    logger.info("[3/3] Writing the report package...")
    try:
        write_report(result, out_dir)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Writing the report failed for %s", pdf.name)
        raise PipelineError(
            "The review finished but the report could not be saved.", original=exc
        ) from exc

    logger.info("DONE  → %s/report.md  (+ report.html, result.json, evidence/)", out_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="preapproval", description="Pre-approval website-verification tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_review = sub.add_parser("review", help="Run the full workflow on one or more application PDFs")
    p_review.add_argument("pdfs", nargs="+", type=Path)
    p_review.add_argument("--out", type=Path, default=Path("outputs"), help="Output root (default: outputs/)")
    p_review.add_argument("--headed", action="store_true", help="Show the browser window while researching")

    p_chat = sub.add_parser("chat", help="Adjust a completed report in plain language")
    p_chat.add_argument("report_dir", type=Path, help="An output directory containing result.json")

    p_serve = sub.add_parser("serve", help="Run the local web app (127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")

    args = parser.parse_args(argv)

    from .logging_config import setup_logging

    setup_logging()

    # `serve` boots the web app itself; it does its own key check per-request so
    # the dashboard is reachable and can explain how to set the key.
    if args.cmd == "serve":
        import uvicorn

        from .web.app import create_app

        logger.info("Starting the web app on http://127.0.0.1:%s", args.port)
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")
        return 0

    if not os.environ.get(_KEY_ENV):
        logger.error("ERROR: set %s first (see README).", _KEY_ENV)
        return 2

    import anthropic

    client = anthropic.Anthropic()

    if args.cmd == "chat":
        from .chat import chat_loop

        chat_loop(args.report_dir, client)
        return 0

    from .errors import PipelineError

    failures = 0
    for pdf in args.pdfs:
        if not pdf.exists():
            logger.error("SKIP %s: not found", pdf)
            failures += 1
            continue
        out_dir = args.out / _out_name(pdf)
        logger.info("\n=== %s → %s ===", pdf.name, out_dir)
        try:
            _run_review(pdf, out_dir, client, args.headed)
        except PipelineError as e:
            failures += 1
            logger.error("FAILED on %s: %s", pdf.name, e.user_message)
        except Exception as e:  # noqa: BLE001 - defensive, already logged upstream
            failures += 1
            logger.exception("FAILED on %s", pdf.name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
