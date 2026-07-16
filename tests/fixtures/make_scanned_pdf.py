"""Generate a scanned / image-only PDF from a digital one, for tests.

Renders page 1 to a raster image and degrades it (rotate, blur, reduce
contrast) so it looks like a phone photo / photocopy, then re-saves it as an
image-only PDF with no text layer. ``pdfplumber`` then extracts no text or
tables from the result — exercising the automation engine's scanned-PDF path.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
from PIL import ImageEnhance, ImageFilter


def make_scanned_pdf(source_pdf: Path, out_path: Path) -> None:
    """Write a rasterized, degraded, text-layer-free copy of ``source_pdf``."""
    source_pdf = Path(source_pdf)
    out_path = Path(out_path)
    with pdfplumber.open(str(source_pdf)) as pdf:
        img = pdf.pages[0].to_image(resolution=150).original
    img = img.convert("RGB")
    img = img.rotate(-3.5, expand=True, fillcolor=(255, 255, 255))
    img = img.filter(ImageFilter.GaussianBlur(radius=1.2))
    img = ImageEnhance.Contrast(img).enhance(0.6)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PDF", resolution=150.0)
