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
the ten sample forms in `samples/.`)*

**Project documentation**: See `docs/project/` for [ARCHITECTURE.md](docs/project/ARCHITECTURE.md), [GROUND_RULES.md](docs/project/GROUND_RULES.md), and [USER_GUIDE.md](docs/project/USER_GUIDE.md).

## Running it (step by step)

You need: **Python 3.10+**. An **Anthropic API key** ([console.anthropic.com](https://console.anthropic.com)) is optional — the tool works fully without one, including chat, using the deterministic automation engine; a key only unlocks the AI-assisted engine and free-form natural-language chat (the no-key chat still understands a set of plain commands — see "Talking to the tool" below).

### Setup (one-time)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# Optional — only needed for the AI-assisted engine and free-form chat.
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is loaded automatically (via `preapproval.config.load_env()`) and is
**git-ignored — never commit it**. It's a local-development convenience only;
a real environment variable you've already set always takes precedence over
whatever's in `.env`, and in production you should set secrets through your
deployment platform's own mechanism rather than shipping a `.env` file at
all. See `docs/project/ARCHITECTURE.md` → Secrets & configuration.

If you'd rather not use a file, `export ANTHROPIC_API_KEY=sk-ant-...` works
exactly as before.

### Web app (recommended)

Start the local web application:

```bash
.venv/bin/python -m preapproval serve
```

Open http://127.0.0.1:8000 in your browser. Upload a form or pick a sample, watch the live progress, read the report, and use the chat box to adjust findings in plain language. On the dashboard, use the **engine dropdown** (Automatic vs AI-assisted) to choose the review method. See [USER_GUIDE.md](docs/project/USER_GUIDE.md) for non-technical reviewers.

### Command line

Review an application (or several) from the terminal:

```bash
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

Useful flags: `--out DIR` changes the output root; `--headed` shows the browser window while the agent works (fun to watch); `--engine {auto,ai,automation}` (default `auto`) selects the review engine — `ai` uses Claude (requires `ANTHROPIC_API_KEY`), `automation` uses deterministic rules (no key needed), and `auto` picks based on whether the key is set.

### Talking to the tool

Reviewers can adjust a completed report in plain language — this works both
with and without an API key:

```bash
.venv/bin/python -m preapproval chat outputs/sample-01
reviewer> change the published schedule item to Needs Review — the calendar link was broken for me
reviewer> add a note that I called the provider to confirm the price
reviewer> regenerate the report
```

**With `ANTHROPIC_API_KEY` set**, Claude understands free-form phrasing like
the examples above. **Without a key**, chat still works via a deterministic
command parser — it understands a fixed set of patterns instead of open-ended
language: `mark <criterion> as Found|Not Found|Needs Review`, `add note to
<criterion>: <text>` (or a bare `add note: <text>` to append to the summary),
`update summary: <text>`, and `regenerate report`. `<criterion>` can be an
id (e.g. `published_fees`) or a snippet of the criterion's text; an
unrecognized message gets a help reply listing the available criterion ids
rather than silently failing.

To re-run the website checks (e.g. the site changed), just run `review` on the
same PDF again.

## How it's built

```
preapproval/
  extract.py     PDF → structured request (Claude + structured outputs, Pydantic-validated)
  engine.py      engine resolution: picks ai or automation based on ANTHROPIC_API_KEY
  db.py          SQLite audit database (reviews, findings, evidence tables with sha256 integrity)
  checklists.py  loads config/checklists/*.yaml (supports `inherits`, e.g. appeals extend community classes)
  research.py    the agent loop: Claude + browser tools (open_url, capture_*, record_finding, finish_review)
  browser.py     Playwright wrapper — navigation, date-stamp banner, highlight-and-capture
  report.py      renders report.md / report.html / result.json
  chat.py        plain-language report revision
  cli.py         entry point (review / chat / serve commands)
  logging_config.py  console + rotating file handler
  errors.py      PipelineError for user-friendly error messages
  automation/    deterministic rule-based engine (no API key required)
    extract_rules.py   PDF → ApplicationRequest via keyword/layout rules
    research_rules.py  website checking via pattern matching and Playwright
  web/           FastAPI local web UI (dashboard, upload, job progress, report viewer, chat)
    app.py       routes, template rendering, job queue
    jobs.py      background job management
    templates/   HTML templates (base, index, job, report, error, setup)
    static/      CSS and JavaScript
config/checklists/*.yaml   the seven category checklists (the domain knowledge)
data/            SQLite audit database preapproval.db (gitignored)
logs/            runtime logs (gitignored, rotating)
outputs/         report packages, one directory per reviewed application
                 (all 10 samples are committed as the required sample output)
tests/           unit and integration tests
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

### Two review engines

**Automatic** (rule-based): A deterministic keyword and price-pattern matcher that runs via Playwright, with zero API key required. It follows the same honesty guarantee as the AI engine — when uncertain, it leans "Needs Review" rather than guessing. If the form's URL doesn't already land on a page showing the requested item, it tries two fallbacks before giving up: (1) a targeted hop to whichever on-page link's text best matches the item/provider name (finds a specific program page from a homepage link, e.g. training/coaching/membership sites), and (2) for recognized e-commerce hosts (currently Amazon), an on-site search URL built from the item's name (`/s?k=<query>`, parenthetical qualifiers stripped). Both are logged, both are skipped once the page already looks confirmed, and both still fail honestly to "Needs Review" if the destination is blocked or unclear — they widen *where* the tool looks, not what it's willing to assert.

**AI-assisted** (Claude): The original engine using Claude's judgment to read websites like a human reviewer would. Requires `ANTHROPIC_API_KEY`. Better for nuanced or ambiguous cases.

Both engines produce identical report formats and both populate the audit database.

### Evidence database

Every review is recorded in `data/preapproval.db` (SQLite, zero additional dependencies), with three tables: `reviews` (the request and rate comparison), `findings` (one row per criterion), and `evidence` (captured files with sha256 integrity hashes). Use `preapproval.db.list_reviews()` and `get_evidence_for_review(review_name)` as the query entry points. A database write failure never crashes a review — it is a best-effort audit trail, not a hard dependency.

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
- **Scanned PDFs:** the automation engine's extraction needs a real text layer
  (it works on digital/typed forms, not scanned images without OCR) and raises a
  clear, actionable error on an image-only PDF. The AI engine can read
  image-only PDFs via vision when a key is available.
- **Not in scope** (per the brief): internal systems, budgets, Life Plans,
  approval decisions, payments.
