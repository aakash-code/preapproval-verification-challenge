"""Playwright wrapper: navigation, text extraction, and date-stamped evidence capture.

Every capture (whole-page and targeted) gets a visible banner burned into the
image with the capture timestamp (UTC), the URL, and the label — an audit needs
to prove what the site showed on that date.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

MAX_TEXT_CHARS = 18000

_BANNER_JS = """
(label) => {
  const old = document.getElementById('__evidence_banner__');
  if (old) old.remove();
  const div = document.createElement('div');
  div.id = '__evidence_banner__';
  div.textContent = label;
  Object.assign(div.style, {
    position: 'fixed', top: '0', left: '0', right: '0', zIndex: '2147483647',
    background: '#1a1a2e', color: '#ffd966', font: 'bold 14px monospace',
    padding: '8px 12px', borderBottom: '3px solid #ffd966', whiteSpace: 'pre-wrap',
  });
  document.body.prepend(div);
}
"""

_REMOVE_BANNER_JS = """
() => { const b = document.getElementById('__evidence_banner__'); if (b) b.remove(); }
"""

_HIGHLIGHT_JS = """
(needle) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const target = needle.toLowerCase();
  let node;
  while ((node = walker.nextNode())) {
    if (node.textContent.toLowerCase().includes(target)) {
      const el = node.parentElement;
      if (!el || el.offsetParent === null) continue;  // skip invisible nodes
      el.scrollIntoView({block: 'center'});
      el.style.outline = '4px solid #e63946';
      el.style.backgroundColor = '#fff3b0';
      return true;
    }
  }
  return false;
}
"""


def _slug(text: str, limit: int = 60) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:limit] or "capture"


class EvidenceBrowser:
    """A single Chromium page plus evidence-capture helpers."""

    def __init__(self, evidence_dir: Path, headless: bool = True):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        )
        self.page = self._context.new_page()
        self.captures: List[str] = []
        self.visited: List[str] = []

    # ---- navigation ------------------------------------------------------

    def goto(self, url: str) -> str:
        if not re.match(r"^https?://", url):
            url = "https://" + url
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_timeout(2500)  # let dynamic content settle
        except PWTimeout:
            return f"TIMEOUT loading {url}. Partial content may be available via get_page_text."
        self.visited.append(self.page.url)
        return f"Loaded {self.page.url} — title: {self.page.title()!r}"

    def get_page_text(self) -> str:
        """Visible text plus link inventory, truncated."""
        try:
            text = self.page.evaluate("() => document.body.innerText") or ""
        except Exception as e:  # page navigated away / crashed
            return f"ERROR reading page: {e}"
        links = self.page.eval_on_selector_all(
            "a[href]",
            """els => els.slice(0, 120).map(a => {
                 const t = a.innerText.trim().slice(0, 80);
                 return t ? t + ' -> ' + a.href : null;
               }).filter(Boolean)""",
        )
        out = text[:MAX_TEXT_CHARS]
        if len(text) > MAX_TEXT_CHARS:
            out += "\n[... text truncated ...]"
        out += "\n\n== LINKS ON PAGE ==\n" + "\n".join(dict.fromkeys(links))
        return out

    # ---- evidence capture ------------------------------------------------

    def _stamp(self, label: str) -> str:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        banner = f"EVIDENCE CAPTURE | {ts}\nURL: {self.page.url}\n{label}"
        self.page.evaluate(_BANNER_JS, banner)
        return ts

    def capture_full_page(self, label: str) -> str:
        """Whole-page screenshot + PDF of the current page, date-stamped."""
        ts = self._stamp(label)
        stem = f"fullpage-{_slug(label)}"
        png = self.evidence_dir / f"{stem}.png"
        self.page.screenshot(path=str(png), full_page=True)
        try:
            pdf = self.evidence_dir / f"{stem}.pdf"
            self.page.pdf(path=str(pdf))
            files = [png.name, pdf.name]
        except Exception:
            files = [png.name]  # page.pdf only works in headless chromium
        self.page.evaluate(_REMOVE_BANNER_JS)
        self.captures.extend(files)
        return f"Saved whole-page capture at {ts}: {', '.join(files)}"

    def capture_evidence(self, criterion_id: str, label: str, quote: Optional[str] = None) -> str:
        """Targeted, labeled screenshot for one confirmed requirement.

        If `quote` is given, scrolls to and highlights the first visible
        occurrence so the proof is centered in the shot.
        """
        found = False
        if quote:
            try:
                found = self.page.evaluate(_HIGHLIGHT_JS, quote.strip()[:120])
            except Exception:
                found = False
        ts = self._stamp(f"Evidence: {label} [criterion: {criterion_id}]")
        fname = f"evidence-{_slug(criterion_id)}-{_slug(label, 40)}.png"
        self.page.screenshot(path=str(self.evidence_dir / fname), full_page=not found)
        self.page.evaluate(_REMOVE_BANNER_JS)
        self.captures.append(fname)
        note = "" if (found or not quote) else " (quote text not located on page — captured full page instead)"
        return f"Saved evidence capture at {ts}: {fname}{note}"

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._pw.stop()
