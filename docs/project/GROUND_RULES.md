# Development Ground Rules

These rules govern all work on this project.

## 1. Two audiences, one tool
The tool must be usable by **non-technical reviewers** (web UI, plain
language, no terminal required) and by **technical users** (CLI, JSON output,
config files). Every feature ships on both surfaces or documents why not.

## 2. Web-based first
The primary interface is the local web application (`preapproval serve`).
The CLI remains for automation/batch use. Anything a reviewer needs —
upload a form, watch progress, read the report, adjust findings in plain
language — must work in the browser.

## 3. Source of truth for requirements
`docs/Project-Brief.pdf` (full spec), `docs/Short-Brief.pdf` (summary), and
`docs/Sample-Applications-Guide.pdf` (test data) are the requirements.
`CHALLENGE.md` lists the deliverables. When in doubt, the Project Brief wins —
especially §5 (what a website can and can't prove) and §10 (human-in-the-loop,
privacy, no fabricated evidence).

## 4. Project structure consistency
```
preapproval/          application code (pipeline + web + CLI)
config/checklists/    domain knowledge (YAML — no rules hard-coded in Python)
docs/                 challenge briefs (PDF) + docs/project/ (our documents)
logs/                 runtime logs (gitignored, rotating)
outputs/              report packages (one directory per reviewed application)
samples/              the ten provided test forms
```
- Code: typed, Pydantic models at module boundaries, one module per pipeline
  stage.
- Errors: every pipeline stage and web route wraps failures in try/except,
  logs the traceback to `logs/`, and surfaces a plain-language message to the
  user. Never a bare stack trace in the UI; never a swallowed exception.
- Logging: `preapproval.logging_config` — console + rotating file handler.
  Every review run logs its steps; the web UI streams the same log to the user.

## 5. Working process
**Plan → execute → test → find bugs/errors → fix → test — repeat until the
goal is met.** No stage is skipped: every change gets a written plan (or spec),
an implementation, and an actual run against real samples/pages before it is
considered done.

## 6. Model responsibilities
| Role | Model |
|---|---|
| Architecture, security, workflow design, review | Claude Fable 5 |
| Coding & execution planning | Claude Opus 4.8 / Claude Sonnet 5 |
| Project documentation | Claude Haiku 4.5 |
| In-product: form extraction & website research agent | `claude-opus-4-8` (adaptive thinking) |
| In-product: report chat/revision | `claude-opus-4-8` |

## 7. Evidence integrity (non-negotiable, from the brief)
- A human always makes the final approve/deny decision.
- "Not Found / Needs Review" is a correct answer; a fabricated finding is a
  failure. Every "Found" must be backed by a date-stamped capture.
- No real participant data is ever committed or sent anywhere; samples are
  synthetic.
