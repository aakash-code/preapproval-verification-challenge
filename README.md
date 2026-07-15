# Pre-Approval Website-Verification Tool

An AI assistant for Pre-Approvals Reviewers. Given a completed pre-approval
application form (PDF), it:

1. **Reads the form** — extracts the category, requested item, provider,
   website link, and stated fee (Claude reads the PDF directly; no brittle OCR
   templates).
2. **Picks the right checklist** — seven categories, defined in
   `config/checklists/*.yaml`, each separating **website-verifiable** criteria
   from **internal** ones (budget / Life Plan items a website can never prove).
3. **Researches the provider's public website** — Claude drives a real Chromium
   browser (Playwright), following fee/schedule/registration links as needed.
4. **Captures date-stamped evidence** — a whole-page screenshot **and PDF** of
   each key page, plus a targeted, labeled screenshot per confirmed
   requirement. Every capture has the UTC timestamp and URL burned into the
   image for the audit file.
5. **Produces a review-ready report** — request at a glance, rate comparison,
   a Found / Not Found / Needs Review table with per-criterion notes and
   evidence references, in Markdown and self-contained HTML.

The tool **assists** the reviewer — it never approves or denies anything, and
honest "Not Found / Needs Review" results are expected and correct. Every
"Found" must be backed by a capture; the agent is instructed to never guess.

*(This repo was created from the challenge template — the original challenge
instructions are preserved in `CHALLENGE.md`, the full brief in `docs/`, and
the ten sample forms in `samples/`.)*

## Running it (step by step)

You need: **Python 3.10+** and an **Anthropic API key**
([console.anthropic.com](https://console.anthropic.com)).

```bash
# 1. One-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# 2. Your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Review an application (or several)
.venv/bin/python -m preapproval review "samples/Sample-01---Community-Class-GallopNYC.pdf"
```

Each run writes a report package to `outputs/sample-01/`:

```
outputs/sample-01/
  report.md      ← the reviewer-facing report
  report.html    ← same report with the evidence images embedded (open in a browser)
  result.json    ← machine-readable result (used by the chat mode)
  evidence/      ← date-stamped screenshots + page PDFs
```

Useful flags: `--out DIR` changes the output root; `--headed` shows the
browser window while the agent works (fun to watch).

### Talking to the tool

Reviewers can adjust a completed report in plain language:

```bash
.venv/bin/python -m preapproval chat outputs/sample-01
reviewer> change the published schedule item to Needs Review — the calendar link was broken for me
reviewer> add a note that I called the provider to confirm the price
reviewer> regenerate the report
```

To re-run the website checks (e.g. the site changed), just run `review` on the
same PDF again.

## How it's built

```
preapproval/
  extract.py     PDF → structured request (Claude + structured outputs, Pydantic-validated)
  checklists.py  loads config/checklists/*.yaml (supports `inherits`, e.g. appeals extend community classes)
  research.py    the agent loop: Claude + browser tools (open_url, capture_*, record_finding, finish_review)
  browser.py     Playwright wrapper — navigation, date-stamp banner, highlight-and-capture
  report.py      renders report.md / report.html / result.json
  chat.py        plain-language report revision
  cli.py         entry point
config/checklists/*.yaml   the seven category checklists (the domain knowledge)
```

**Model:** `claude-opus-4-8` with adaptive thinking, via the Anthropic Python
SDK. Chosen because the hard part of this task is judgment on messy websites
("is this price really open to everyone?"), and evidence integrity matters
more than per-run cost. Swapping to `claude-sonnet-5` (one constant per
module) cuts cost ~40% if volume demands it.

**Why an agent loop, not a scraper:** provider sites vary wildly — fees may be
on a linked page, in a PDF flyer, or absent. The model decides where to look,
when it has proof, and when to stop and say "a human must check". The tool
layer keeps it honest: `record_finding` rejects unknown criteria,
`finish_review` refuses to complete until every criterion has a finding, and
any criterion left unresolved is force-marked **Needs Review**, never guessed.

## Adding or changing a form/checklist

Add a YAML file to `config/checklists/` (no code changes needed):

```yaml
id: my_new_category          # must match the file name
name: My New Category
criteria:
  - id: published_fees
    text: The fee is published
    website_verifiable: true
    guidance: What the agent should look for
  - id: budget_approved
    text: Category is approved in the budget
    website_verifiable: false   # internal — reported, never assessed from the web
caps: { program_cap_per_year: 500 }   # optional; shown to the agent for fee sanity-checks
```

Then add the category value to `Category` in `preapproval/models.py` so
extraction can classify it, and mention distinguishing form features in the
extraction prompt if it's not obvious from the form title. A checklist can
`inherits: another_id` to extend it (this is how appeals reuse the community
class checks).

## Limitations, assumptions & what production would require

- **Bot walls:** large retail sites (e.g. Amazon) sometimes block headless
  browsers. The agent captures what it sees and marks the affected criteria
  Needs Review rather than pretending. Production would want a residential
  proxy / retailer product API for those categories.
- **Evidence ≠ truth:** a screenshot proves what the site showed at capture
  time, not that the site is accurate. The reviewer stays in the loop.
- **PHI:** sample forms are synthetic. Production would need a BAA-covered API
  arrangement, redaction of participant fields before any third-party call
  (only the provider/item/fee are needed for web research), encrypted storage
  of report packages, and access controls.
- **Cost/latency:** a review takes ~2–5 minutes and a few dollars of Opus
  tokens. Batchable overnight; Sonnet or per-category model routing would cut
  cost.
- **Determinism:** two runs may phrase notes differently or pick different
  evidence pages. The findings schema and forced per-criterion coverage keep
  results comparable.
- **Not in scope** (per the brief): internal systems, budgets, Life Plans,
  approval decisions, payments.
