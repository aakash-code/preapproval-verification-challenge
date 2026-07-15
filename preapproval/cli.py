"""Command-line entry point.

  python -m preapproval review samples/Sample-01.pdf          # full workflow
  python -m preapproval review samples/*.pdf --out outputs    # batch
  python -m preapproval chat outputs/sample-01                # revise a report
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _out_name(pdf: Path) -> str:
    m = re.match(r"(sample-\d+)", pdf.stem.lower())
    return m.group(1) if m else re.sub(r"[^a-z0-9]+", "-", pdf.stem.lower()).strip("-")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="preapproval", description="Pre-approval website-verification tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_review = sub.add_parser("review", help="Run the full workflow on one or more application PDFs")
    p_review.add_argument("pdfs", nargs="+", type=Path)
    p_review.add_argument("--out", type=Path, default=Path("outputs"), help="Output root (default: outputs/)")
    p_review.add_argument("--headed", action="store_true", help="Show the browser window while researching")

    p_chat = sub.add_parser("chat", help="Adjust a completed report in plain language")
    p_chat.add_argument("report_dir", type=Path, help="An output directory containing result.json")

    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY first (see README).", file=sys.stderr)
        return 2

    # imports deferred so `--help` works without deps installed
    import anthropic

    from .extract import extract_request
    from .report import write_report
    from .research import run_research

    client = anthropic.Anthropic()

    if args.cmd == "chat":
        from .chat import chat_loop

        chat_loop(args.report_dir, client)
        return 0

    failures = 0
    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"SKIP {pdf}: not found", file=sys.stderr)
            failures += 1
            continue
        out_dir = args.out / _out_name(pdf)
        print(f"\n=== {pdf.name} → {out_dir} ===")
        try:
            print("[1/3] Reading the application form...")
            request = extract_request(pdf, client)
            print(f"      {request.category.value}: {request.requested_item!r} @ {request.provider_name} — {request.url}")
            if request.extraction_notes:
                print(f"      note: {request.extraction_notes}")
            if not request.url:
                print("      WARNING: no URL on the form — the agent will report this; expect Needs Review results.")

            print("[2/3] Researching the website and capturing evidence...")
            result = run_research(request, out_dir / "evidence", client, headless=not args.headed)

            print("[3/3] Writing the report package...")
            write_report(result, out_dir)
            print(f"DONE  → {out_dir}/report.md  (+ report.html, result.json, evidence/)")
        except Exception as e:
            failures += 1
            print(f"FAILED on {pdf.name}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
