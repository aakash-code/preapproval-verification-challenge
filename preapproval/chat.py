"""Plain-language reviewer interaction (§7 of the brief).

The reviewer opens a completed review and talks to it: "change this item to
Needs Review", "add a note to the report", "regenerate the report". Claude
edits the stored result via tools; the report files are re-rendered on save.
Re-running website checks is done via the main `review` command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import anthropic

from .models import FindingStatus, VerificationResult
from .report import write_report

MODEL = "claude-opus-4-8"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "update_finding",
        "description": "Change the status and/or note of one checklist finding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["Found", "Not Found", "Needs Review", "Internal — not website-verifiable"],
                },
                "note": {"type": "string"},
            },
            "required": ["criterion_id"],
        },
    },
    {
        "name": "update_summary",
        "description": "Replace the report's summary text.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
    {
        "name": "regenerate_report",
        "description": "Re-render report.md / report.html from the current state. Call after making edits.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def make_dispatch(result: VerificationResult, report_dir: Path):
    """Build the tool-dispatch function for one report.

    Returns ``handle(name, args) -> str`` closing over ``result`` and
    ``report_dir``. Shared by the CLI chat loop and the web chat route so both
    surfaces apply edits identically. Mutates ``result`` in place and rewrites
    the report files on ``regenerate_report``.
    """
    report_dir = Path(report_dir)

    def handle(name: str, args: Dict[str, Any]) -> str:
        if name == "update_finding":
            for f in result.findings:
                if f.criterion_id == args["criterion_id"]:
                    if args.get("status"):
                        f.status = FindingStatus(args["status"])
                    if args.get("note"):
                        f.note = args["note"]
                    return f"Updated {f.criterion_id}: status={f.status.value}"
            return f"ERROR: no finding with id {args['criterion_id']!r}"
        if name == "update_summary":
            result.summary = args["summary"]
            return "Summary updated."
        if name == "regenerate_report":
            write_report(result, report_dir)
            return f"Report re-rendered at {report_dir}/report.md and report.html"
        return f"ERROR: unknown tool {name}"

    return handle


def build_system_prompt(result: VerificationResult) -> str:
    return (
        "You help a Pre-Approvals Reviewer adjust a completed website-verification report. "
        "Use the tools to apply their requested changes, then regenerate the report. "
        "Answer questions about the findings from the report state below. Keep replies short. "
        "You cannot re-run website checks here — tell them to run `review` again for that.\n\n"
        "CURRENT REPORT STATE:\n" + result.model_dump_json(indent=2)
    )


def chat_loop(report_dir: Path, client: anthropic.Anthropic | None = None) -> None:
    client = client or anthropic.Anthropic()
    report_dir = Path(report_dir)
    result = VerificationResult.model_validate_json((report_dir / "result.json").read_text())

    handle = make_dispatch(result, report_dir)
    system = build_system_prompt(result)

    messages: List[Dict[str, Any]] = []
    print(f"Chatting about {report_dir}. Type your request ('quit' to exit).")
    while True:
        try:
            user = input("\nreviewer> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user or user.lower() in {"quit", "exit"}:
            break
        messages.append({"role": "user", "content": user})
        while True:
            response = client.messages.create(
                model=MODEL, max_tokens=4000, system=system, tools=TOOLS, messages=messages
            )
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(block.text.strip())
                if block.type == "tool_use":
                    out = handle(block.name, block.input)
                    print(f"  [{block.name}] {out}")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
            if not results:
                break
            messages.append({"role": "user", "content": results})
    print("Bye.")
