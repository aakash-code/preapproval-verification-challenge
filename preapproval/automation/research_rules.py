"""Rule-based website research (no LLM).

Drives the same ``EvidenceBrowser`` the AI path uses, but decides each
website-verifiable criterion with deterministic keyword/price rules instead of a
model. Honest ``Needs Review`` is a correct, expected result — anything a rule
cannot settle is handed to a human. Every ``Found`` that can be backed by an
on-page quote gets a targeted evidence capture.
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..browser import EvidenceBrowser
from ..checklists import internal_only, load_checklist, website_verifiable
from ..models import (
    ApplicationRequest,
    Finding,
    FindingStatus,
    RateComparison,
    VerificationResult,
)

logger = logging.getLogger("preapproval.automation.research")

_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d{2})?")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_BLOCKED_PHRASES = ("captcha", "verify you are a human", "access denied", "robot check")

_LINK_KEYWORDS = ("fee", "price", "membership", "join", "schedule", "class", "register", "pricing")


def _first_price(text: str) -> Optional[str]:
    m = _MONEY_RE.search(text)
    return m.group().strip() if m else None


def _num(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


class _Ctx:
    """Everything an evaluator needs to decide one criterion."""

    def __init__(self, request: ApplicationRequest, checklist: Dict[str, Any],
                 page_text: str) -> None:
        self.request = request
        self.checklist = checklist
        self.text = page_text.lower()
        self.text_orig = page_text
        self.page_price = _first_price(page_text)
        self.cap_check: Optional[str] = None  # set by cap evaluators


Result = Tuple[FindingStatus, str, Optional[str]]  # status, note, quote


# ---- individual evaluators -------------------------------------------------

def _eval_open_to_public(ctx: _Ctx) -> Result:
    pos = ("open to the public", "all welcome", "everyone welcome", "sign up",
           "register now", "join now", "become a member", "enroll")
    neg = ("members only", "invite only", "private club", "staff only", "by invitation")
    for p in neg:
        if p in ctx.text:
            return FindingStatus.NOT_FOUND, f"The page indicates restricted access ('{p}').", None
    for p in pos:
        if p in ctx.text:
            return FindingStatus.FOUND, f"The page shows public access ('{p}').", p
    return (FindingStatus.NEEDS_REVIEW,
            "Could not find clear language about public access — please check manually.", None)


def _eval_published_fees(ctx: _Ctx) -> Result:
    if ctx.page_price:
        return (FindingStatus.FOUND,
                f"A published price was found on the page: {ctx.page_price}.", ctx.page_price)
    return (FindingStatus.NEEDS_REVIEW,
            "No price was visible on the page (sites often block automated readers) — "
            "please confirm the published fee manually.", None)


def _eval_identical_fees(ctx: _Ctx) -> Result:
    seg = ("opwdd", "disability discount", "special needs pricing",
           "sliding scale for disability")
    for p in seg:
        if p in ctx.text:
            return (FindingStatus.NOT_FOUND,
                    f"The page mentions segregated/special pricing ('{p}') — flag for review.", None)
    if ctx.page_price:
        return (FindingStatus.FOUND,
                "A single public price is shown with no OPWDD/disability-specific pricing tier.",
                ctx.page_price)
    return (FindingStatus.NEEDS_REVIEW,
            "No price was found, so identical-pricing could not be confirmed — please review.", None)


def _eval_subject_based(ctx: _Ctx) -> Result:
    subjects = ("art", "dance", "martial arts", "cooking", "fitness", "yoga",
                "music", "riding", "class", "course", "workshop", "lesson")
    for s in subjects:
        if s in ctx.text:
            return (FindingStatus.FOUND,
                    f"The page describes a subject-based activity ('{s}').", s)
    return (FindingStatus.NEEDS_REVIEW,
            "Could not identify a clear subject/skill on the page — please review.", None)


def _eval_no_college_credits(ctx: _Ctx) -> Result:
    bad = ("college credit", "academic credit", "degree program", "accredited degree")
    for p in bad:
        if p in ctx.text:
            return (FindingStatus.NOT_FOUND,
                    f"The page mentions credit/degree language ('{p}').", None)
    return (FindingStatus.FOUND,
            "No mention of college credit found on the page (inferred from absence).", None)


def _eval_not_clinical(ctx: _Ctx) -> Result:
    bad = ("clinical treatment", "therapy session", "medical therapy",
           "physical therapy clinic")
    for p in bad:
        if p in ctx.text:
            return (FindingStatus.NOT_FOUND,
                    f"The page frames the activity clinically ('{p}').", None)
    return (FindingStatus.FOUND,
            "No clinical/therapy framing found on the page (inferred from absence).", None)


def _eval_published_schedule(ctx: _Ctx) -> Result:
    keys = ("schedule", "class times", "calendar", "hours") + _WEEKDAYS
    for k in keys:
        if k in ctx.text:
            return (FindingStatus.FOUND,
                    f"The page shows scheduling information ('{k}').", k)
    return (FindingStatus.NEEDS_REVIEW,
            "No published schedule was visible — please check the site manually.", None)


def _eval_fee_match(ctx: _Ctx) -> Result:
    form_num = _num(ctx.request.fee_stated)
    page_num = _num(ctx.page_price)
    if form_num is None or page_num is None:
        return (FindingStatus.NEEDS_REVIEW,
                "Could not parse a fee from both the form and the website — please compare manually.",
                None)
    if abs(form_num - page_num) <= 1.0:
        return (FindingStatus.FOUND,
                f"The website price ({ctx.page_price}) matches the fee on the form "
                f"(${form_num:g}).", ctx.page_price)
    return (FindingStatus.NOT_FOUND,
            f"The website price ({ctx.page_price}) differs from the fee on the form "
            f"(${form_num:g}).", None)


def _eval_item_exists(ctx: _Ctx) -> Result:
    words = [w for w in re.findall(r"[a-zA-Z]+", ctx.request.requested_item or "") if len(w) > 3]
    hits = [w for w in words if w.lower() in ctx.text]
    if len(hits) >= 2:
        return (FindingStatus.FOUND,
                f"The page mentions the requested item ({', '.join(hits[:4])}).", hits[0])
    if ctx.page_price:
        return (FindingStatus.FOUND,
                f"A product price ({ctx.page_price}) is present on the page.", ctx.page_price)
    return (FindingStatus.NEEDS_REVIEW,
            "Could not confirm the item and price on the page — please review the listing.", None)


def _eval_not_excluded(ctx: _Ctx) -> Result:
    item = (ctx.request.requested_item or "").lower()
    exclusions = ctx.checklist.get("exclusions", []) or []
    for phrase in exclusions:
        pl = phrase.lower()
        if pl in item:
            return (FindingStatus.NOT_FOUND,
                    f"The requested item appears on the exclusion list ('{phrase}') — flag it.", None)
        # Also match individual significant tokens of the exclusion entry
        # (e.g. 'computer / laptop / tablet' -> 'laptop').
        for tok in re.findall(r"[a-zA-Z]+", pl):
            if len(tok) > 3 and re.search(rf"\b{re.escape(tok)}\b", item):
                return (FindingStatus.NOT_FOUND,
                        f"The requested item matches an exclusion-list keyword "
                        f"('{tok}', from '{phrase}') — flag it.", None)
    return (FindingStatus.FOUND,
            "The requested item does not match anything on the exclusion list.", None)


def _eval_safety_features(ctx: _Ctx) -> Result:
    return (FindingStatus.NEEDS_REVIEW,
            "Safety-feature claims require manual comparison against the product page.", None)


def _eval_noncredit_skill_building(ctx: _Ctx) -> Result:
    keys = ("continuing education", "noncredit", "non-credit", "professional development",
            "workforce", "vocational", "job skills", "certificate program")
    for k in keys:
        if k in ctx.text and "degree" not in ctx.text:
            return (FindingStatus.FOUND,
                    f"The page describes noncredit skill-building ('{k}').", k)
    return (FindingStatus.NEEDS_REVIEW,
            "Could not confirm noncredit/skill-building framing — please review.", None)


def _eval_not_opwdd_location(ctx: _Ctx) -> Result:
    bad = ("opwdd certified", "opwdd-certified", "group home", "day habilitation site")
    for p in bad:
        if p in ctx.text:
            return (FindingStatus.NOT_FOUND,
                    f"The page suggests an OPWDD facility ('{p}').", None)
    return (FindingStatus.FOUND,
            "No OPWDD-facility language found on the page (inferred from absence).", None)


def _eval_staff_screening(ctx: _Ctx) -> Result:
    return (FindingStatus.NEEDS_REVIEW,
            "Staff background-screening is proven by a letter, not the website — needs document.",
            None)


def _eval_educational_content(ctx: _Ctx) -> Result:
    keys = ("class", "course", "program", "workshop", "coaching", "training", "curriculum")
    for k in keys:
        if k in ctx.text:
            return (FindingStatus.FOUND,
                    f"The page describes an educational/coaching opportunity ('{k}').", k)
    return (FindingStatus.NEEDS_REVIEW,
            "Could not confirm educational/coaching content — please review.", None)


def _eval_fee_cap(ctx: _Ctx) -> Result:
    caps = ctx.checklist.get("caps", {}) or {}
    cap_values = [v for v in caps.values() if isinstance(v, (int, float))]
    fees = [float(m.group().replace(",", ""))
            for m in _NUM_RE.finditer(ctx.request.fee_stated or "")] if ctx.request.fee_stated else []
    if not cap_values or not fees:
        ctx.cap_check = "Could not evaluate the program cap — no parseable fee on the form."
        return (FindingStatus.NEEDS_REVIEW,
                "Could not parse the stated fee against the program caps — please review.", None)
    max_cap = max(cap_values)
    over = [f for f in fees if f > max_cap]
    fee_disp = ", ".join(f"${f:g}" for f in fees)
    if over:
        ctx.cap_check = f"Stated fee(s) {fee_disp} exceed the program cap (max ${max_cap:g})."
        return (FindingStatus.NOT_FOUND,
                f"Stated fee(s) {fee_disp} exceed the program cap (max ${max_cap:g}) — flag it.",
                None)
    ctx.cap_check = f"Stated fee(s) {fee_disp} are within the program caps (max ${max_cap:g})."
    return (FindingStatus.FOUND,
            f"Stated fee(s) {fee_disp} are within the program caps (max ${max_cap:g}).", None)


def _eval_denial_reason_evidence(ctx: _Ctx) -> Result:
    reason = (ctx.request.denial_reason or "").lower()
    if any(k in reason for k in ("fee", "price", "published")):
        if ctx.page_price:
            return (FindingStatus.FOUND,
                    f"The stated denial reason concerns fees/pricing; the site now shows a "
                    f"published price ({ctx.page_price}), which supports the appeal (refutes the denial).",
                    ctx.page_price)
        return (FindingStatus.NEEDS_REVIEW,
                "The denial reason concerns fees, but no price could be read from the site — "
                "this neither supports nor refutes the denial automatically; please review.", None)
    return (FindingStatus.NEEDS_REVIEW,
            "Please compare the stated denial reason against the page manually.", None)


_EVALUATORS: Dict[str, Callable[[_Ctx], Result]] = {
    "open_to_public": _eval_open_to_public,
    "published_fees": _eval_published_fees,
    "published_fee": _eval_published_fees,
    "identical_fees": _eval_identical_fees,
    "subject_based": _eval_subject_based,
    "no_college_credits": _eval_no_college_credits,
    "not_clinical": _eval_not_clinical,
    "published_schedule": _eval_published_schedule,
    "fee_match": _eval_fee_match,
    "item_exists": _eval_item_exists,
    "not_excluded": _eval_not_excluded,
    "safety_features": _eval_safety_features,
    "noncredit_skill_building": _eval_noncredit_skill_building,
    "not_opwdd_location": _eval_not_opwdd_location,
    "staff_screening": _eval_staff_screening,
    "educational_content": _eval_educational_content,
    "fee_cap": _eval_fee_cap,
    "denial_reason_evidence": _eval_denial_reason_evidence,
}


def _default_evaluator(ctx: _Ctx) -> Result:
    return (FindingStatus.NEEDS_REVIEW,
            "Automated rule-based check is not defined for this criterion — please review "
            "the page manually.", None)


def _looks_blocked(page_text: str) -> bool:
    low = page_text.lower()
    if any(p in low for p in _BLOCKED_PHRASES):
        return True
    # Very short text usually means the page didn't render / is gated.
    return len(page_text.strip()) < 40


def _candidate_link(page_text: str) -> Optional[str]:
    """Find one relevant fee/schedule/pricing link from the page's link list."""
    if "== LINKS ON PAGE ==" not in page_text:
        return None
    _, _, links_block = page_text.partition("== LINKS ON PAGE ==")
    for line in links_block.splitlines():
        low = line.lower()
        if any(k in low for k in _LINK_KEYWORDS):
            m = re.search(r"https?://\S+", line)
            if m:
                return m.group().rstrip(").,")
    return None


def run_research_rules(
    request: ApplicationRequest,
    evidence_dir: Path,
    headless: bool = True,
    log=print,
) -> VerificationResult:
    checklist = load_checklist(request.category.value)
    wv = website_verifiable(checklist)
    wv_ids = {c["id"]: c["text"] for c in wv}
    findings: Dict[str, Finding] = {}
    rate = RateComparison(fee_on_form=request.fee_stated)
    review_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    log("  [rules] Rule-based website verification (no AI model in use).")

    # No URL on the form -> nothing to check on the web; hand every
    # website-verifiable criterion to a human.
    if not request.url:
        log("  [rules] No website link on the form — marking website checks for manual review.")
        for cid, text in wv_ids.items():
            findings[cid] = Finding(
                criterion_id=cid, criterion_text=text, status=FindingStatus.NEEDS_REVIEW,
                note="No website link was provided on the form — a human must verify this manually.",
            )
        return _assemble(request, checklist, findings, rate, [], "", review_ts, wv, all_review=True)

    browser: Optional[EvidenceBrowser] = None
    page_text = ""
    ctx: Optional[_Ctx] = None
    try:
        browser = EvidenceBrowser(evidence_dir, headless=headless)
        log(f"  [rules] Opening {request.url}")
        status_line = browser.goto(request.url)
        log(f"  [rules] {status_line}")

        page_text = browser.get_page_text()
        blocked = status_line.startswith("TIMEOUT") or _looks_blocked(page_text)

        if blocked:
            log("  [rules] Page could not be read automatically (timeout / bot-wall / empty).")
            try:
                browser.capture_full_page(f"{request.provider_name or 'provider'} — page (could not read)")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Full-page capture failed on blocked page: %s", exc)
            note = ("The site could not be read automatically (it timed out, was blocked by a "
                    "bot-check, or returned no readable text). A human must verify this on the site.")
            for cid, text in wv_ids.items():
                findings[cid] = Finding(
                    criterion_id=cid, criterion_text=text,
                    status=FindingStatus.NEEDS_REVIEW, note=note,
                )
            return _assemble(request, checklist, findings, rate,
                             list(dict.fromkeys(browser.visited)), "", review_ts, wv,
                             all_review=True, blocked=True)

        browser.capture_full_page(f"{request.provider_name or 'provider'} — main page")

        # At most one extra hop: if no price on the main page, follow a
        # fee/schedule/pricing link and use that page's text instead.
        if "$" not in page_text:
            link = _candidate_link(page_text)
            if link:
                log(f"  [rules] No price on the main page; following a relevant link: {link}")
                hop_status = browser.goto(link)
                log(f"  [rules] {hop_status}")
                if not hop_status.startswith("TIMEOUT"):
                    hop_text = browser.get_page_text()
                    if not _looks_blocked(hop_text):
                        page_text = hop_text
                        # The hopped page is recorded in browser.visited (via goto).
                        browser.capture_full_page(
                            f"{request.provider_name or 'provider'} — fee/schedule page")

        ctx = _Ctx(request, checklist, page_text)

        for c in wv:
            cid = c["id"]
            evaluator = _EVALUATORS.get(cid, _default_evaluator)
            try:
                fstatus, note, quote = evaluator(ctx)
            except Exception as exc:  # noqa: BLE001 - one bad rule must not sink the run
                logger.warning("Evaluator for %s failed: %s", cid, exc)
                fstatus, note, quote = (
                    FindingStatus.NEEDS_REVIEW,
                    f"An automated check errored ({exc}); please review manually.", None)

            evidence_files: List[str] = []
            evidence_url: Optional[str] = None
            if fstatus == FindingStatus.FOUND and quote:
                try:
                    browser.capture_evidence(cid, f"Evidence: {c['text']}", quote=quote)
                    if browser.captures:
                        evidence_files.append(browser.captures[-1])
                    evidence_url = browser.page.url
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Evidence capture failed for %s: %s", cid, exc)

            findings[cid] = Finding(
                criterion_id=cid, criterion_text=c["text"], status=fstatus,
                note=note, evidence_url=evidence_url, evidence_files=evidence_files,
            )
            log(f"  [rules] {cid}: {fstatus.value}")

    except Exception as exc:  # noqa: BLE001 - keep whatever we already collected
        logger.warning("Rule-based research hit an unexpected error: %s", exc, exc_info=True)
        log(f"  [rules] Unexpected error: {exc}")
        for cid, text in wv_ids.items():
            if cid not in findings:
                findings[cid] = Finding(
                    criterion_id=cid, criterion_text=text, status=FindingStatus.NEEDS_REVIEW,
                    note=f"The automated review could not finish this check ({exc}) — please review.",
                )
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    # Rate comparison from what we found.
    page_price = ctx.page_price if ctx else None
    if page_price:
        form_num = _num(request.fee_stated)
        page_num = _num(page_price)
        if form_num is not None and page_num is not None:
            verdict = "matches application exactly" if abs(form_num - page_num) <= 1.0 else "differs"
        else:
            verdict = "not published" if form_num is None else "differs"
        rate.fee_on_website = page_price
        rate.verdict = verdict
    else:
        rate.fee_on_website = "not published"
        rate.verdict = "not published"
    rate.cap_check = ctx.cap_check if ctx else None

    visited = list(dict.fromkeys(browser.visited)) if browser else []
    return _assemble(request, checklist, findings, rate, visited, "", review_ts, wv)


def _assemble(request, checklist, findings, rate, visited, summary, review_ts, wv,
              all_review: bool = False, blocked: bool = False) -> VerificationResult:
    # Defensive: force-mark anything unresolved as Needs Review, like the AI path.
    for c in wv:
        cid = c["id"]
        if cid not in findings:
            findings[cid] = Finding(
                criterion_id=cid, criterion_text=c["text"], status=FindingStatus.NEEDS_REVIEW,
                note="This criterion was not resolved by the automated review — a human must check.",
            )

    ordered = [findings[c["id"]] for c in wv]
    for c in internal_only(checklist):
        ordered.append(Finding(
            criterion_id=c["id"], criterion_text=c["text"], status=FindingStatus.INTERNAL,
            note=c.get("note", "Depends on internal records (budget / Life Plan / documents) — "
                              "not checkable from the public web."),
        ))

    n_checked = len(wv)
    n_found = sum(1 for f in ordered if f.status == FindingStatus.FOUND)
    n_review = sum(1 for f in ordered[:n_checked] if f.status == FindingStatus.NEEDS_REVIEW)
    provider = request.provider_name or "the provider"
    summary = (
        f"Automated rule-based check of {provider}'s website found {n_found} of {n_checked} "
        f"website-verifiable items, with {n_review} needing manual review. No AI model was used "
        f"for this review — a human should double-check any 'Needs Review' items before deciding."
    )

    return VerificationResult(
        request=request,
        findings=ordered,
        rate_comparison=rate,
        pages_visited=visited,
        summary=summary,
        review_timestamp=review_ts,
    )
