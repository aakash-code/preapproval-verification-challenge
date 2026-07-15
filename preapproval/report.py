"""Step 4: render the review-ready report package.

Each run produces, in its output directory:
  result.json   — the full machine-readable result (used by `chat` to revise)
  report.md     — the reviewer-facing report
  report.html   — same report, styled, evidence images embedded inline
  evidence/     — date-stamped captures
"""

from __future__ import annotations

import json
from pathlib import Path

import markdown as md

from .models import FindingStatus, VerificationResult

_STATUS_ICON = {
    FindingStatus.FOUND: "✅ Found",
    FindingStatus.NOT_FOUND: "❌ Not Found",
    FindingStatus.NEEDS_REVIEW: "⚠️ Needs Review",
    FindingStatus.INTERNAL: "🏢 Internal — not website-verifiable",
}

_CSS = """
body{font-family:-apple-system,Segoe UI,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.5}
h1{border-bottom:3px solid #1a1a2e}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top}
th{background:#1a1a2e;color:#ffd966}
img{max-width:100%;border:1px solid #999;margin:.5rem 0}
code{background:#f2f2f2;padding:2px 4px}
blockquote{border-left:4px solid #ffd966;margin-left:0;padding-left:1rem;color:#444}
.banner{background:#fff8e1;border:1px solid #e0c25a;padding:.7rem 1rem;border-radius:6px}
"""


def render_markdown(result: VerificationResult, embed_images: bool = False) -> str:
    r = result.request
    lines = [
        "# Pre-Approval Website-Verification Report",
        "",
        f"> This report was produced by an automated review assistant on **{result.review_timestamp}**. "
        "It gathers public-website evidence only. **A human reviewer makes the final approve/deny decision.**",
        "",
        "## The request at a glance",
        "",
        "| | |",
        "|---|---|",
        f"| Participant | {r.participant_name} (age {r.participant_age or '—'}) |",
        f"| Category | {r.category.value.replace('_', ' ').title()} |",
        f"| Requested | {r.requested_item} |",
        f"| Provider / Vendor | {r.provider_name} |",
        f"| Website / link | {r.url or '— (missing on form)'} |",
        f"| Fee stated on form | {r.fee_stated or '—'} |",
        f"| Review date | {result.review_timestamp} |",
    ]
    if r.category.value == "appeal" and r.denial_reason:
        lines += ["", f"**Appeal — original denial reason:** {r.denial_reason}"]
        if r.justification:
            lines += ["", f"**Applicant's justification:** {r.justification}"]

    lines += [
        "",
        "## Rate comparison",
        "",
        "| Fee on application | Fee found on website | Verdict |",
        "|---|---|---|",
        f"| {result.rate_comparison.fee_on_form or '—'} | {result.rate_comparison.fee_on_website or '—'} | **{result.rate_comparison.verdict}** |",
    ]
    if result.rate_comparison.cap_check:
        lines += ["", f"**Program-cap check:** {result.rate_comparison.cap_check}"]

    lines += ["", "## Summary", "", result.summary, "", "## Checklist findings", ""]
    lines += ["| Status | Criterion | What the website shows | Evidence |", "|---|---|---|---|"]
    for f in result.findings:
        ev = ""
        if f.evidence_files:
            ev = "<br>".join(f"`evidence/{name}`" for name in f.evidence_files)
        if f.evidence_url:
            ev = f"[{f.evidence_url}]({f.evidence_url})<br>" + ev
        note = f.note.replace("\n", " ")
        lines.append(f"| {_STATUS_ICON[f.status]} | {f.criterion_text} | {note} | {ev or '—'} |")

    lines += [
        "",
        "## Pages reviewed",
        "",
        *[f"- {u}" for u in result.pages_visited] or ["- (none)"],
        "",
        "## Evidence captures",
        "",
        "All captures are date-stamped with the capture time (UTC) and URL burned into the image.",
        "",
    ]
    evidence_files = sorted({name for f in result.findings for name in f.evidence_files})
    # whole-page records aren't tied to one finding — list everything on disk in HTML step
    if embed_images:
        for name in evidence_files:
            lines += [f"**{name}**", "", f"![{name}](evidence/{name})", ""]
    else:
        lines += [f"- `evidence/{name}`" for name in evidence_files] or ["- see evidence/ directory"]

    return "\n".join(lines)


def write_report(result: VerificationResult, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(result.model_dump_json(indent=2))
    (out_dir / "report.md").write_text(render_markdown(result))

    # HTML version embeds every capture in the evidence dir (including whole-page records)
    body_md = render_markdown(result, embed_images=True)
    extra = []
    evidence_dir = out_dir / "evidence"
    listed = {name for f in result.findings for name in f.evidence_files}
    if evidence_dir.exists():
        for p in sorted(evidence_dir.glob("*.png")):
            if p.name not in listed:
                extra += [f"**{p.name}** (whole-page record)", "", f"![{p.name}](evidence/{p.name})", ""]
    html_body = md.markdown(body_md + "\n" + "\n".join(extra), extensions=["tables"])
    (out_dir / "report.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>Pre-Approval Review — "
        f"{result.request.participant_name}</title><style>{_CSS}</style><body>{html_body}"
    )
