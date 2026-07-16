# User Guide: Pre-Approval Reviewer

## What this tool does

This tool assists pre-approval reviewers by automatically checking provider websites. Here's the workflow:

1. **You upload** a completed pre-approval application (a PDF form).
2. **The tool reads** the form and extracts key details: the provider, requested item, website link, and stated fee.
3. **The tool visits** the provider's public website to verify the stated information—fees, schedules, registration requirements, etc.
4. **The tool captures evidence**—screenshots and PDFs of key pages. Every image is stamped with the date and time it was captured, plus the website URL, so you have a clear audit trail.
5. **You review the report**, which shows what was found, what wasn't found, and what requires your manual check.
6. **You make the final decision** on approval or denial. The tool assists but never decides.

**No API key required to start.** The tool works out of the box using built-in automatic checking. On the dashboard, you'll see an engine dropdown — **Automatic** (fast, rule-based, no cost) is the default; **AI-assisted** (smarter reading, like a person, requires a paid API key) is available if your IT team sets it up.

## One-time setup

**On macOS, this is one step:** find the project folder and double-click
**`install.command`**. A Terminal window opens and does everything for
you — checks that Python is installed, sets everything up, and offers to
start the app when it's done. If something's missing (like Python itself),
it tells you exactly what to install and where to get it, then you just
run it again. You don't need an IT contact or any command-line knowledge
for this.

**On Windows**, double-click **`install.bat`** instead — same idea, no
extra software needed.

If you're on Linux, or prefer the command line, run this instead:

```bash
./scripts/install.sh
```

Either way, the tool is ready to use afterward — no API key needed. (Optional:
if your organization wants to use AI-assisted checking, open the new `.env`
file the installer created and set `ANTHROPIC_API_KEY=` to your key.)

## Starting the app

Open a terminal and run:

```bash
python -m preapproval serve
```

or (if that doesn't work):

```bash
.venv/bin/python -m preapproval serve
```

The tool will print a message like:

```
Starting the web app on http://127.0.0.1:8000
```

Open your web browser and go to **http://127.0.0.1:8000**. You'll see the dashboard.

## Reviewing an application

**Step 1: Choose a form**
- Click **Upload an application form** and select a PDF from your computer, *or*
- Click **Use a sample form** to try a built-in example.

**Step 2: Confirm anything unclear (sometimes)**
- If the form is missing a website link, or the provider name / requested item / category couldn't be read confidently, the tool pauses and shows you what it found so far, asking you to fill in or correct it before it checks the website. Type the correct value and click **Continue review** — or leave a field blank to have that item marked Needs Review instead.
- Most well-formed applications skip this step entirely.

**Step 3: Watch the progress**
- The tool will show a live log of what it's doing: extracting the form, visiting the website, capturing evidence.
- This typically takes 2–5 minutes.

**Step 4: Read the report**
- Once complete, click to view the report. You'll see:
  - **Request summary**: who is requesting what, at what price.
  - **Rate comparison**: what the form states vs. what the website shows.
  - **Findings table**:
    - **Found**: the website confirms this item (you'll see evidence).
    - **Not Found**: the website doesn't show this (you may need to check manually).
    - **Needs Review**: the website was blocked or unreachable (you must verify manually).
    - **Internal**: not verifiable from a website (e.g., budget approval).
  - **Evidence**: date-stamped screenshots showing what the tool found. Each image shows the capture date/time and the URL it came from.

## Adjusting a report

If you want to change a finding or add a note (e.g., "I called the provider and confirmed the price"), use the chat box at the bottom of the report.

Type plain-language requests like:

- "Change the published fee item to Not Found — I couldn't find it either."
- "Add a note: I called the provider and they confirmed the schedule is accurate."
- "Mark this as Needs Review — the website seems outdated."

The tool will update the report immediately. If you're using Automatic mode
(no API key), the chat understands a smaller, fixed set of phrasings — if it
doesn't understand your request, it will reply with examples and a list of
the items you can refer to, rather than just ignoring you.

## Troubleshooting

| Problem | What to do |
|---------|-----------|
| "Set ANTHROPIC_API_KEY" message (only when using AI-assisted engine) | Ask IT to set the API key and restart the app. The automatic engine works without it. |
| A website is blocked or slow | Items from that site will show "Needs Review". Check the provider manually. |
| Report seems stuck | Wait a few more minutes. If it still hangs, check the logs (ask IT to look at `logs/app.log`). |
| Evidence images are missing | This is rare. Ask IT to check the logs for errors during capture. |

## Questions?

Contact your administrator or the technical lead if the tool behaves unexpectedly.
