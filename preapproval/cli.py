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
from typing import List

logger = logging.getLogger("preapproval.cli")

_KEY_ENV = "ANTHROPIC_API_KEY"


def _out_name(pdf: Path) -> str:
    m = re.match(r"(sample-\d+)", pdf.stem.lower())
    return m.group(1) if m else re.sub(r"[^a-z0-9]+", "-", pdf.stem.lower()).strip("-")


def _prompt_ambiguous_fields(request, ambiguous_fields: List[str]) -> None:
    """Interactively confirm/correct ambiguous fields on ``request`` in place."""
    from .models import (
        AMBIGUOUS_FIELD_PROMPTS,
        Category,
        apply_ambiguous_field_correction,
    )

    for field_name in ambiguous_fields:
        if field_name == "category":
            valid = ", ".join(c.value for c in Category)
            prompt = (
                f"The category could not be confidently determined (defaulted to "
                f"{request.category.value}). Enter the correct category id ({valid}), "
                f"or press Enter to keep the default: "
            )
            for attempt in range(2):
                value = input(prompt).strip()
                if not value:
                    break
                if apply_ambiguous_field_correction(request, "category", value):
                    logger.info("      reviewer-confirmed category: %s", value)
                    break
                if attempt == 0:
                    print(f"  '{value}' is not a valid category id. Try again.")
                else:
                    logger.warning(
                        "      invalid category %r; keeping default %s",
                        value, request.category.value,
                    )
            continue
        hint = AMBIGUOUS_FIELD_PROMPTS.get(field_name, f"Enter {field_name}")
        value = input(f"{hint} Enter it now (or press Enter to skip): ").strip()
        if apply_ambiguous_field_correction(request, field_name, value):
            logger.info("      reviewer-confirmed %s: %s", field_name, value)


def _run_review(pdf: Path, out_dir: Path, client, headed: bool, engine: str,
                interactive: bool = False) -> None:
    """Run the three pipeline stages, wrapping each in a friendly PipelineError.

    ``engine`` is the resolved engine ("ai" or "automation"). The "automation"
    path uses no Anthropic client at all.
    """
    from .errors import PipelineError
    from .models import compute_ambiguous_fields
    from .report import write_report

    logger.info("      Engine: %s (%s)", engine,
                "Claude / AI" if engine == "ai" else "deterministic rules, no API key")

    logger.info("[1/3] Reading the application form...")
    try:
        if engine == "ai":
            from .extract import extract_request
            request = extract_request(pdf, client)
        else:
            from .automation.extract_rules import extract_request_rules
            request = extract_request_rules(pdf)
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Extraction failed for %s", pdf.name)
        raise PipelineError(
            "Could not read the application form — is this a valid PDF?", original=exc
        ) from exc

    logger.info(
        "      %s: %r @ %s — %s",
        request.category.value,
        request.requested_item or "(not stated)",
        request.provider_name or "(not stated)",
        request.url,
    )
    if request.extraction_notes:
        logger.info("      note: %s", request.extraction_notes)

    ambiguous_fields = compute_ambiguous_fields(request)
    if ambiguous_fields and interactive and sys.stdin.isatty():
        logger.info("      Some fields need your confirmation before the website check:")
        _prompt_ambiguous_fields(request, ambiguous_fields)
    elif ambiguous_fields:
        logger.warning("      Ambiguous fields (not confirmed): %s", ", ".join(ambiguous_fields))

    if not request.url:
        logger.info("      WARNING: no URL on the form — the agent will report this; expect Needs Review results.")

    logger.info("[2/3] Researching the website and capturing evidence...")
    try:
        if engine == "ai":
            from .research import run_research
            result = run_research(request, out_dir / "evidence", client, headless=not headed, log=logger.info)
        else:
            from .automation.research_rules import run_research_rules
            result = run_research_rules(request, out_dir / "evidence", headless=not headed, log=logger.info)
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

    # Record the review in the audit DB. A DB failure must never fail the run.
    try:
        from . import db
        review_name = _out_name(pdf)
        db.save_review(result, out_dir, review_name, engine)
    except Exception:  # noqa: BLE001
        logger.warning("Could not write the audit-DB record for %s", pdf.name)

    logger.info("DONE  → %s/report.md  (+ report.html, result.json, evidence/)", out_dir)


def _rule_based_chat_loop(report_dir: Path) -> int:
    """No-key chat: apply plain-language commands deterministically, per line."""
    from .chat import rule_based_reply
    from .models import VerificationResult

    report_dir = Path(report_dir)
    result_json = report_dir / "result.json"
    if not result_json.exists():
        logger.error("ERROR: no result.json in %s", report_dir)
        return 2
    result = VerificationResult.model_validate_json(result_json.read_text())

    print(
        f"Chatting about {report_dir} (rule-based, no API key). "
        "Type your request ('quit' to exit)."
    )
    while True:
        try:
            user = input("\nreviewer> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user or user.lower() in {"quit", "exit"}:
            break
        reply, _updated = rule_based_reply(user, result, report_dir)
        print(reply)
    print("Bye.")
    return 0


def main(argv=None) -> int:
    from .config import load_env

    load_env()  # picks up .env for local dev; real env vars always win

    parser = argparse.ArgumentParser(prog="preapproval", description="Pre-approval website-verification tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_review = sub.add_parser("review", help="Run the full workflow on one or more application PDFs")
    p_review.add_argument("pdfs", nargs="+", type=Path)
    p_review.add_argument("--out", type=Path, default=Path("outputs"), help="Output root (default: outputs/)")
    p_review.add_argument("--headed", action="store_true", help="Show the browser window while researching")
    p_review.add_argument(
        "--no-interactive", action="store_true",
        help="Never prompt for missing/ambiguous fields, even in a terminal",
    )
    p_review.add_argument(
        "--engine", choices=["auto", "ai", "automation"], default="auto",
        help="Review engine: 'ai' (Claude, needs ANTHROPIC_API_KEY), 'automation' "
             "(deterministic rules, no key), or 'auto' (default: ai if a key is set, else automation)",
    )

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

    # `chat` uses Claude when a key is set; otherwise it falls back to a
    # deterministic rule-based command loop that needs no API key.
    if args.cmd == "chat":
        if os.environ.get(_KEY_ENV):
            import anthropic

            from .chat import chat_loop

            chat_loop(args.report_dir, anthropic.Anthropic())
            return 0
        return _rule_based_chat_loop(args.report_dir)

    # `review`: only require a key when the resolved engine is "ai".
    from .engine import resolve_engine

    engine = resolve_engine(args.engine)
    if engine == "ai" and not os.environ.get(_KEY_ENV):
        logger.error("ERROR: --engine ai needs %s. Use --engine automation to run without a key.", _KEY_ENV)
        return 2

    client = None
    if engine == "ai":
        import anthropic

        client = anthropic.Anthropic()

    logger.info("Using the %s engine.", engine)

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
            _run_review(pdf, out_dir, client, args.headed, engine,
                        interactive=not args.no_interactive)
        except PipelineError as e:
            failures += 1
            logger.error("FAILED on %s: %s", pdf.name, e.user_message)
        except Exception as e:  # noqa: BLE001 - defensive, already logged upstream
            failures += 1
            logger.exception("FAILED on %s", pdf.name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
