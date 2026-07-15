"""Step 1: read the application PDF and pull out the request.

The PDF goes to Claude as a document block; the response is validated against
the ApplicationRequest schema via structured outputs, so downstream code never
sees free-form text.
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic

from .models import ApplicationRequest

MODEL = "claude-opus-4-8"

_SYSTEM = """\
You extract data from completed pre-approval application forms for a NY
disability program (OPWDD Self-Direction). Forms may be scans — read carefully.

Rules:
- Copy values exactly as written; do not normalize fees ("$80 per 30-minute
  session" stays as-is).
- The category comes from which form template this is (title of the form).
  An "Appeal" form re-examines a Community Class denial — category is appeal.
- If the URL, provider, requested item, or category is missing or ambiguous,
  still extract what you can and describe the problem in extraction_notes —
  never invent values.
- form_answers: capture every YES/NO question with its ticked answer
  (null if blank)."""


def extract_request(pdf_path: Path, client: anthropic.Anthropic | None = None) -> ApplicationRequest:
    client = client or anthropic.Anthropic()
    pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode()

    response = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract the pre-approval request from this completed application form.",
                    },
                ],
            }
        ],
        output_format=ApplicationRequest,
    )
    request = response.parsed_output
    if request is None:
        raise RuntimeError(f"Extraction failed for {pdf_path.name}: could not parse structured output")
    return request
