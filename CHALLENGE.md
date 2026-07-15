# Pre-Approval Website-Verification Tool — Build Challenge

**F5 Global Talent — AI Specialist evaluation project**

You've been invited to this challenge because you're in the F5 Global Talent AI Specialist Pool. This is a real business problem from one of our clients, a NY-based human-services agency. We want to see how you turn a messy, real-world workflow into a clean, working AI tool.

---

## The project in one paragraph

The client's staff review purchase-approval applications for a government-funded disability program. Before certain purchases (a community class, a gym membership, a household item, a coaching course, etc.) can be approved, a reviewer must open the provider's public website and confirm the item is real and open to the public at a published price — then save date-stamped screenshot/PDF evidence for audits. Today that's done by hand, one site at a time. **Your job: build an AI tool that reads a completed application form (PDF), does the website research, captures the evidence, and produces a review-ready report.**

A human always makes the final approve/deny decision. Honest "couldn't verify this" results are correct and expected.

## What's in this repo

```
docs/
  Short-Brief.pdf                 ← 1-page summary (start here)
  Project-Brief.pdf               ← the FULL spec: requirements, checklists,
                                    evidence rules, grading criteria (read all of it)
  Sample-Applications-Guide.pdf   ← index of the 10 test forms
samples/
  Sample-01 … Sample-10           ← 10 completed application forms (PDF).
                                    Fictional participants, REAL public provider
                                    websites — your tool has genuine evidence to find.
AI-CONVERSATION.md                ← placeholder — you MUST replace this (see below)
```

## How to do the challenge

1. Click **"Use this template"** (top right) to create **your own copy** of this repo.
2. **Your repo must be PUBLIC.** Private repos cannot be reviewed and count as no submission.
3. Read `docs/Short-Brief.pdf`, then `docs/Project-Brief.pdf` in full — Section 5 (what a website can and can't prove) is the crux of the evaluation.
4. Build the tool. CLI, web UI, or chat interface — your choice; justify it. Use any AI models/tools/frameworks you want.
5. Run it end-to-end on **at least 3** of the forms in `samples/` and commit the output report packages (report + evidence captures).
6. Replace `AI-CONVERSATION.md` with your **exported AI conversation** (Claude Code, Cursor, ChatGPT, etc.), including the **list of tools and models you used** at the top. See the file for the required format.
7. Push everything, then submit your repo link at:

**→ https://f5globaltalent.com/careers/preapproval-tool-test-001**

Use the **same email address you applied to the F5 AI Specialist Pool with** — that's how we match your submission to your profile. You'll also record a short 60-second intro video on the submission page.

## What you deliver (authoritative list)

1. **The working tool** in this repo — runs the full workflow on at least 3 samples.
2. **The repo itself**, organized the way you'd want a team to inherit it: README with run instructions a non-technical reviewer could follow, clear structure, config-driven checklists, at least one committed sample output report with its evidence captures, a note on how to add a new form/checklist, and a statement of limitations/assumptions.
3. **`AI-CONVERSATION.md`** — your real AI conversation + the tools/models list.

*(Where this README and the Project Brief PDF differ on deliverables — e.g. the brief mentions a walkthrough video — this README is authoritative: the AI conversation file plus the intro video on the submission page replace it.)*

## Rules

- Repo stays **public** from submission until you hear back from us.
- **No fabricated evidence.** Screenshots and citations must be real captures of what the site showed. "Not Found / Needs Review" is a correct answer; a hallucinated finding is an automatic fail.
- Do not commit API keys or secrets. All sample data is synthetic — commit it freely.
- Your own work, with AI assistance expected and encouraged — that's the point. The AI conversation shows us how you actually work.

Good luck. We're looking at how you think, not how polished the UI is.
