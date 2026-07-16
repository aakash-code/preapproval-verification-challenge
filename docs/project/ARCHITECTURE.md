# Architecture & Workflow

Designed against the requirements in `docs/Project-Brief.pdf` (§3 core
workflow, §5 checklist logic, §6 evidence/report rules, §10 constraints),
`docs/Short-Brief.pdf`, and `docs/Sample-Applications-Guide.pdf`.

## System overview

```
                       ┌──────────────────────────────────────────────┐
   Reviewer (browser)  │  Web app  (FastAPI, 127.0.0.1:8000)          │
   ────────────────────▶  dashboard · job progress · report · chat    │
                       └──────────────┬───────────────────────────────┘
   Technical user (terminal)          │ background job thread
   ──────────────▶ CLI (review/chat) ─┤  (same pipeline for both surfaces)
                                      ▼
        ┌───────────────────────────────────────────────────────────┐
        │                     PIPELINE                              │
        │ 1 extract.py   PDF → ApplicationRequest (Claude,          │
        │                structured outputs, Pydantic-validated)    │
        │ 2 checklists.py  category → config/checklists/*.yaml      │
        │                website-verifiable vs internal split (§5)  │
        │ 3 research.py  agent loop: Claude ⇄ browser tools         │
        │      browser.py  Playwright Chromium: navigate, read,     │
        │                date-stamped whole-page + targeted capture │
        │ 4 report.py    result.json + report.md + report.html      │
        │ 5 chat.py      plain-language revision (shared dispatch   │
        │                used by CLI chat and web chat)             │
        └───────────────────────────────────────────────────────────┘
                 │                                   │
                 ▼                                   ▼
        Claude API (claude-opus-4-8)        Provider public websites
```

Data at rest: `outputs/<application>/` (report package + `evidence/`),
`logs/app.log` (rotating). Domain knowledge lives only in
`config/checklists/*.yaml` — the code contains no category rules.

## Review workflow (maps to Project-Brief §3)

1. Reviewer submits a form PDF (web upload, sample picker, or CLI path).
2. **Extract** — Claude reads the PDF; output is validated against the
   `ApplicationRequest` schema. Missing/ambiguous fields are recorded in
   `extraction_notes`, never invented.
3. **Checklist selection** — the extracted category loads its YAML checklist;
   appeals `inherit` the community-class checks and add denial-reason framing.
4. **Research** — the agent visits the form's URL, follows fee/schedule/
   registration links, and for each *website-verifiable* criterion records
   Found / Not Found / Needs Review via tools. Guards: unknown criterion ids
   are rejected; `finish_review` refuses until every criterion has a finding;
   unresolved criteria are force-marked Needs Review. Internal criteria (§5)
   are reported verbatim as "Internal — not website-verifiable".
5. **Evidence (§6)** — whole-page screenshot+PDF per key page, plus one
   labeled targeted capture per confirmed requirement; every image has the
   UTC timestamp + URL burned in.
6. **Report** — request at a glance, rate comparison + cap check, findings
   table, pages reviewed, embedded evidence (HTML) — shareable as a folder.
7. **Human review (§10)** — the reviewer reads, optionally adjusts findings
   in plain language (chat), and makes the approve/deny decision themselves.

## Dual engine design

Engine selection is resolved via `engine.py:resolve_engine()`: if `--engine ai` is requested or `auto` is used with `ANTHROPIC_API_KEY` set, the AI engine runs; otherwise the automation engine runs. Both are accessed through the same CLI/web entry points.

**AI engine** (`research.py`): Claude drives the browser, decides where to look, and judges ambiguous cases. Requires an API key; more nuanced judgment on messy websites.

**Automation engine** (`automation/`): Deterministic keyword and price-pattern matching (keyword detection in HTML, regex patterns for fees and schedules, Playwright navigation). Zero API key required; always returns the same result for the same input. When uncertain, conservatively marks criteria "Needs Review" rather than guessing.

Extraction differs too: the AI engine reads the PDF with Claude; the automation engine parses it with `pdfplumber` + label-matching rules (`automation/extract_rules.py`). This means the automation engine requires a PDF with a real text layer (digital/typed forms); on a scanned or image-only PDF it raises an actionable `PipelineError` suggesting the AI engine, which reads the page visually. Both produce the same `ApplicationRequest` / `VerificationResult` Pydantic models, so `report.py`, `chat.py`, and the web dashboard do not know or care which engine produced the results. Both populate the audit database. The choice is transparent to users — reviewers see the same report format either way.

## Evidence database

Every review (regardless of engine) is recorded in `data/preapproval.db` (SQLite, included in stdlib — zero additional dependencies). Three tables track the audit trail:

- **reviews**: one row per completed review (category, provider, URL, fee comparison, verdict, timestamp, engine used).
- **findings**: one row per criterion (criterion id/text, status, note, evidence URL).
- **evidence**: one row per captured file (name, sha256 hash, capture time, kind: "fullpage" or "targeted"). Hashes allow integrity verification against disk.

Entry points: `db.list_reviews()` returns all reviews (newest first); `db.get_evidence_for_review(review_name)` returns evidence rows for a given review. The database complements (not replaces) the file-based report package. A write failure never crashes a review — it is best-effort, not a hard dependency.

## Error handling & logging

- Typed `PipelineError` wraps each stage; users see a plain-language message,
  `logs/app.log` gets the traceback.
- Browser tool failures (timeouts, bot walls, dead links) are returned to the
  agent as text so it can respond honestly (capture what it sees → Needs
  Review), not as crashes.
- Web jobs run in a background thread with a per-job log buffer; the progress
  page streams the same lines the CLI prints. A failed job shows its error
  state and points at the log.

## Security & privacy (§10)

- **Local-first:** the web app binds to `127.0.0.1` only — nothing is exposed
  to the network; no auth is needed because there is no remote access.
- **PHI minimization:** sample data is synthetic. The design assumption for
  production: participant identity fields are not needed for web research —
  only provider/item/URL/fee are; a redaction step before the API call, a
  BAA-covered arrangement, encrypted storage, and per-user access control
  would be required before real forms are used.
- **Secrets & configuration:** `preapproval/config.py:load_env()` loads a
  local `.env` file (git-ignored — see `.env.example` for the template) at
  the top of both entry points (`cli.py:main()`, `web/app.py:create_app()`).
  This is a **local-development convenience only**:
  - `load_dotenv(..., override=False)` — a real environment variable already
    set (by the shell, a systemd unit, a container, a secrets manager) always
    wins over `.env`, so `.env` can never silently shadow a production
    secret.
  - `.env` itself, and any `.env.*` variant, is git-ignored; only
    `.env.example` (values blank, template only) is committed. This repo is
    **public**, so nothing with a real value in it may ever be committed.
  - **Production should not use a `.env` file at all** — set
    `ANTHROPIC_API_KEY` (and any future third-party keys) as real environment
    variables through the deployment platform's own secrets mechanism.
    `load_env()` is a silent no-op when no `.env` file is present, so this is
    always safe to leave in place.
  - The key is never written to disk, logs, or reports regardless of how
    it's supplied. Nothing else in the app authenticates against anything.
- **Serving evidence safely:** report/evidence routes resolve paths and
  refuse anything outside `outputs/` (no path traversal).
- **Browser blast radius:** the research browser is headless, isolated per
  run, and only ever *reads* public pages and captures images; it submits no
  forms and stores no credentials.
- **Evidence integrity:** timestamps/URLs are burned into images at capture
  time; reports reference files by name so tampering is evident against the
  audit copy.

## What a deployed multi-user version would add

Auth (SSO), HTTPS, a real job queue (e.g. RQ/Celery), object storage for
report packages, per-reviewer audit trails, retention policies, and the PHI
controls above. The pipeline itself is stateless per review, so it ports
unchanged.

## Model responsibilities

In-product: `claude-opus-4-8` (adaptive thinking) powers the **AI engine**
(extraction, research, and chat) when `ANTHROPIC_API_KEY` is set — judgment
quality and evidence integrity dominate cost there. The **automation engine**
uses no model at all — deterministic rules only — so the tool is fully usable
with zero API key or spend; chat (plain-language report revision) still
requires the AI engine, since it inherently needs a model.
Development process: Fable 5 designs architecture/security/workflow and
reviews; Opus 4.8 / Sonnet 5 implement; Haiku 4.5 writes user documentation
(see `GROUND_RULES.md`).
