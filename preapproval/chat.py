"""Plain-language reviewer interaction (§7 of the brief).

The reviewer opens a completed review and talks to it: "change this item to
Needs Review", "add a note to the report", "regenerate the report". Claude
edits the stored result via tools; the report files are re-rendered on save.
Re-running website checks is done via the main `review` command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


def _help_text(result: VerificationResult) -> str:
    """Help / fallback message listing example phrasings and criterion ids."""
    ids = ", ".join(f.criterion_id for f in result.findings) or "(none)"
    return (
        "I didn't understand that. Try one of:\n"
        "  - mark <criterion> as Needs Review\n"
        "  - add note to <criterion>: called the provider to confirm\n"
        "  - update summary: looks good overall\n"
        "  - regenerate report\n"
        "<criterion> can be an id or part of the criterion text. Available ids: "
        + ids
    )


def rule_based_reply(
    message: str, result: VerificationResult, report_dir: Path
) -> Tuple[str, bool]:
    """Apply one plain-language command to ``result`` without any API call.

    Parses ``message`` deterministically (``automation.chat_rules``), dispatches
    the matched tool via the shared ``make_dispatch`` handler, always regenerates
    the report files afterward on success, and returns ``(reply, updated)``.

    ``updated`` is False for help / ambiguous / unrecognized messages (nothing
    was changed), True when a command was applied.
    """
    from .automation.chat_rules import _find_criterion, _MARK_RE, _NOTE_RE, parse_command

    report_dir = Path(report_dir)
    handle = make_dispatch(result, report_dir)

    parsed = parse_command(message, result.findings)
    if parsed is None:
        # Distinguish an ambiguous criterion reference from a bare non-match so
        # we can list candidates when the reviewer clearly meant a criterion.
        for rx in (_MARK_RE, _NOTE_RE):
            m = rx.match(message)
            if m and m.group("crit"):
                candidates = _find_criterion(m.group("crit"), result.findings)
                if isinstance(candidates, list) and len(candidates) > 1:
                    listed = "\n".join(f"  - {c}" for c in candidates)
                    return (
                        "That matched more than one criterion — please be more "
                        "specific. Did you mean:\n" + listed,
                        False,
                    )
        return (_help_text(result), False)

    name, args = parsed

    if name == "update_summary" and "note" in args:
        # Bare "add note: ..." with no criterion -> append to the summary.
        new_summary = result.summary.rstrip() + " " + args["note"]
        out = handle("update_summary", {"summary": new_summary})
        confirmation = "Added your note to the summary."
    elif name == "update_finding":
        out = handle("update_finding", args)
        if out.startswith("ERROR"):
            return (out, False)
        if "status" in args:
            crit = args["criterion_id"]
            confirmation = f"Updated {crit} to {args['status']}."
        else:
            confirmation = f"Added a note to {args['criterion_id']}."
    elif name == "update_summary":
        handle("update_summary", args)
        confirmation = "Summary updated."
    elif name == "regenerate_report":
        handle("regenerate_report", {})
        return ("Report regenerated.", True)
    else:  # pragma: no cover - defensive
        return (_help_text(result), False)

    # Keep report.md / report.html in sync after any successful edit.
    handle("regenerate_report", {})
    return (confirmation + " Report regenerated.", True)


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
