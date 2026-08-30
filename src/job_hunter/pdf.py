from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename_part(value: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", value.strip()).strip("_")
    return cleaned or "Unknown"


def render_cover_letter_pdf(text: str, company: str, role: str, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{_sanitize_filename_part(company)}_{_sanitize_filename_part(role)}_Cover_Letter.pdf"
    path = out_dir / filename

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    style = getSampleStyleSheet()["Normal"]
    style.fontName = "Helvetica"
    style.fontSize = 11
    style.leading = 15

    story = []
    for line in text.split("\n"):
        if line.strip() == "":
            story.append(Spacer(1, 10))
        else:
            story.append(Paragraph(escape(line), style))

    doc.build(story)
    return path
