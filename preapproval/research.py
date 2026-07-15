"""Step 2–3: agentic website research and evidence gathering.

Claude drives a real Chromium browser through a small tool set: open pages,
read text, capture date-stamped whole-page records and per-criterion evidence,
and record a Found / Not Found / Needs Review finding for each
website-verifiable checklist item. A manual tool loop is used so browser state
and evidence side-effects stay in one place.

Honest "Not Found / Needs Review" is a correct result; findings must never be
fabricated — every Found needs a capture behind it.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import anthropic

from .browser import EvidenceBrowser
from .checklists import internal_only, load_checklist, website_verifiable
from .models import (
    ApplicationRequest,
    Finding,
    FindingStatus,
    RateComparison,
    VerificationResult,
)

MODEL = "claude-opus-4-8"
MAX_TURNS = 40

logger = logging.getLogger("preapproval.research")

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "open_url",
        "description": (
            "Navigate the browser to a URL and return the page's visible text plus a "
            "list of links on the page. Use this to visit the provider site and to "
            "follow links to fee/schedule/registration pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "get_page_text",
        "description": "Re-read the visible text and links of the current page (e.g. after a timeout).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "capture_full_page",
        "description": (
            "Save a date-stamped whole-page screenshot + PDF of the CURRENT page for the "
            "audit file ('here is the website we reviewed'). Capture the main provider page "
            "and any page carrying key proof (fees, schedule). Do this BEFORE recording "
            "findings that rely on the page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"label": {"type": "string", "description": "Short description, e.g. 'GallopNYC recreational riding page'"}},
            "required": ["label"],
        },
    },
    {
        "name": "capture_evidence",
        "description": (
            "Save a targeted, labeled, date-stamped screenshot proving ONE requirement on the "
            "CURRENT page. Pass a short exact `quote` of the on-page text that proves it, so the "
            "shot scrolls to and highlights the proof. One capture per confirmed requirement."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "string"},
                "label": {"type": "string", "description": "e.g. 'Evidence: published fees'"},
                "quote": {"type": "string", "description": "Exact short text from the page proving the requirement"},
            },
            "required": ["criterion_id", "label"],
        },
    },
    {
        "name": "record_finding",
        "description": (
            "Record the final status of one website-verifiable checklist criterion. Statuses: "
            "'Found' (evidence captured proves it), 'Not Found' (the site shows it is NOT satisfied, "
            "or the info is absent after genuine search), 'Needs Review' (ambiguous/gated/conflicting — "
            "a human must look). NEVER mark Found without a capture. Quote the relevant page text in the note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "string"},
                "status": {"type": "string", "enum": ["Found", "Not Found", "Needs Review"]},
                "note": {"type": "string"},
                "evidence_url": {"type": "string"},
                "evidence_files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["criterion_id", "status", "note"],
        },
    },
    {
        "name": "finish_review",
        "description": (
            "Call once, after every website-verifiable criterion has a recorded finding. "
            "Provides the rate comparison and a short overall summary for the reviewer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fee_on_website": {"type": "string", "description": "The fee as published on the site, or 'not published'"},
                "rate_verdict": {"type": "string", "description": "e.g. 'matches application exactly', 'differs', 'not published'"},
                "cap_check": {"type": "string", "description": "Whether the stated fee is within the program cap, if the category has one"},
                "summary": {"type": "string", "description": "3-6 sentence plain-language summary for the reviewer"},
            },
            "required": ["rate_verdict", "summary"],
        },
    },
]


def _system_prompt(request: ApplicationRequest, checklist: Dict[str, Any]) -> str:
    wv = website_verifiable(checklist)
    internal = internal_only(checklist)
    lines = [
        "You are a website-verification agent assisting a Pre-Approvals Reviewer for a",
        "government-funded disability program. Purchases must be genuinely public and",
        "legitimate; your job is to check the provider's PUBLIC website and capture",
        "date-stamped evidence for an audit file. A human makes the final decision.",
        "",
        "HARD RULES:",
        "- Never fabricate. Every 'Found' must be backed by a capture taken on the page that proves it.",
        "- 'Not Found' and 'Needs Review' are correct, expected answers when evidence is absent,",
        "  gated (login/bot-wall), ambiguous, or contradicts the form.",
        "- Only assess the website-verifiable criteria below. Internal criteria are handled by staff.",
        "- If the page is blocked (CAPTCHA, robot check) or the URL is dead, capture what you see,",
        "  mark affected criteria 'Needs Review', and say exactly what happened.",
        "- Workflow per page: read it, capture_full_page, then capture_evidence + record_finding",
        "  per criterion it proves. Follow fee/schedule/registration links when needed (stay on the",
        "  provider's own site). Record a finding for EVERY criterion before finish_review.",
        "",
        f"CATEGORY: {checklist['name']}",
        "",
        "WEBSITE-VERIFIABLE CRITERIA (record a finding for each):",
    ]
    for c in wv:
        lines.append(f"- [{c['id']}] {c['text']}")
        if c.get("guidance"):
            lines.append(f"    guidance: {c['guidance']}")
    if checklist.get("caps"):
        lines.append("")
        lines.append(f"PROGRAM CAPS / RULES: {json.dumps(checklist['caps'])}")
    if checklist.get("exclusions"):
        lines.append(f"EXCLUSION LIST: {', '.join(checklist['exclusions'])}")
    lines += [
        "",
        "INTERNAL CRITERIA (do NOT assess — they are reported as internal automatically):",
        *[f"- {c['text']}" for c in internal],
        "",
        "THE APPLICATION:",
        request.model_dump_json(indent=2, exclude={"form_answers"}),
    ]
    if request.category.value == "appeal" and request.denial_reason:
        lines += [
            "",
            "THIS IS AN APPEAL. Frame every check against the stated denial reason:",
            f"  DENIAL REASON: {request.denial_reason}",
            f"  APPLICANT'S JUSTIFICATION: {request.justification or '(none stated)'}",
            "Surface evidence that specifically supports or refutes the denial reason.",
        ]
    return "\n".join(lines)


def run_research(
    request: ApplicationRequest,
    evidence_dir: Path,
    client: anthropic.Anthropic | None = None,
    headless: bool = True,
    log=None,
) -> VerificationResult:
    if log is None:
        log = logger.info
    client = client or anthropic.Anthropic()
    checklist = load_checklist(request.category.value)
    browser = EvidenceBrowser(evidence_dir, headless=headless)

    findings: Dict[str, Finding] = {}
    rate = RateComparison(fee_on_form=request.fee_stated)
    summary = ""
    wv_ids = {c["id"]: c["text"] for c in website_verifiable(checklist)}

    _browser_tools = {"open_url", "get_page_text", "capture_full_page", "capture_evidence"}

    def handle_tool(name: str, args: Dict[str, Any]) -> str:
        if name in _browser_tools:
            # Browser failures (timeouts, bot walls, dead links, crashes) are
            # returned to the model as text so it can respond honestly (capture
            # what it sees -> Needs Review) rather than crashing the loop.
            try:
                if name == "open_url":
                    return browser.goto(args["url"]) + "\n\n" + browser.get_page_text()
                if name == "get_page_text":
                    return browser.get_page_text()
                if name == "capture_full_page":
                    return browser.capture_full_page(args["label"])
                if name == "capture_evidence":
                    return browser.capture_evidence(args["criterion_id"], args["label"], args.get("quote"))
            except Exception as exc:  # noqa: BLE001 - surface to model, keep loop alive
                logger.warning("Browser tool %s failed: %s", name, exc, exc_info=True)
                return (
                    f"TOOL ERROR: the browser could not complete {name} ({exc}). "
                    "Capture what you can see and mark affected criteria 'Needs Review'."
                )
        if name == "record_finding":
            cid = args["criterion_id"]
            if cid not in wv_ids:
                return f"ERROR: unknown criterion_id {cid!r}. Valid ids: {sorted(wv_ids)}"
            findings[cid] = Finding(
                criterion_id=cid,
                criterion_text=wv_ids[cid],
                status=FindingStatus(args["status"]),
                note=args["note"],
                evidence_url=args.get("evidence_url"),
                evidence_files=args.get("evidence_files", []),
            )
            missing = sorted(set(wv_ids) - set(findings))
            return f"Recorded {cid} = {args['status']}. Still missing findings for: {missing or 'none'}"
        if name == "finish_review":
            nonlocal summary
            rate.fee_on_website = args.get("fee_on_website")
            rate.verdict = args["rate_verdict"]
            rate.cap_check = args.get("cap_check")
            summary = args["summary"]
            missing = sorted(set(wv_ids) - set(findings))
            if missing:
                return f"NOT DONE: record findings for {missing} first, then call finish_review again."
            return "DONE"
        return f"ERROR: unknown tool {name}"

    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": "Begin the website verification. Start with the URL on the form."}
    ]
    system = _system_prompt(request, checklist)
    done = False

    try:
        for _ in range(MAX_TURNS):
            response = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=system,
                tools=TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    log(f"  [agent] {block.text.strip()[:300]}")
                if block.type == "tool_use":
                    log(f"  [tool] {block.name} {json.dumps(block.input)[:200]}")
                    result = handle_tool(block.name, block.input)
                    if block.name == "finish_review" and result == "DONE":
                        done = True
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": result}
                    )
            if not tool_results:
                if response.stop_reason == "pause_turn":
                    continue
                break
            messages.append({"role": "user", "content": tool_results})
            if done:
                break
    finally:
        browser.close()

    # Anything the agent never resolved is surfaced honestly, not guessed.
    for cid, text in wv_ids.items():
        if cid not in findings:
            findings[cid] = Finding(
                criterion_id=cid,
                criterion_text=text,
                status=FindingStatus.NEEDS_REVIEW,
                note="The automated review ended before this criterion was resolved — a human must check.",
            )
    # Internal criteria are appended verbatim, marked not-website-verifiable.
    ordered = [findings[c["id"]] for c in website_verifiable(checklist)]
    for c in internal_only(checklist):
        ordered.append(
            Finding(
                criterion_id=c["id"],
                criterion_text=c["text"],
                status=FindingStatus.INTERNAL,
                note=c.get("note", "Depends on internal records (budget / Life Plan / documents) — not checkable from the public web."),
            )
        )

    return VerificationResult(
        request=request,
        findings=ordered,
        rate_comparison=rate,
        pages_visited=list(dict.fromkeys(browser.visited)),
        summary=summary or "Review ended without a summary — see per-criterion notes.",
        review_timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
