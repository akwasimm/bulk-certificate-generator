"""Renders the single fixed certificate template to PDF bytes."""
import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)

BACKGROUND = colors.HexColor("#FDFBF4")
INK = colors.HexColor("#1E3A5F")
ACCENT = colors.HexColor("#C9A227")
MUTED = colors.HexColor("#6B7280")

TITLE_FONT = "Times-Bold"
NAME_FONT = "Times-BoldItalic"
BODY_FONT = "Times-Italic"
SMALL_FONT = "Helvetica"

MAX_TEXT_WIDTH = PAGE_W - 240


def _fit_size(text: str, font: str, preferred: int, max_width: float = MAX_TEXT_WIDTH,
              minimum: int = 8) -> int:
    size = preferred
    while size > minimum and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _centered(c: canvas.Canvas, text: str, y: float, font: str, size: int, color) -> float:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(PAGE_W / 2, y, text)
    return stringWidth(text, font, size)


def render_certificate_pdf(
    certificate_id: str,
    recipient_name: str,
    course_name: str,
    issued_by: str | None,
    issued_on: date,
) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor(issued_by or "Bulk Certificate Generator")

    c.setFillColor(BACKGROUND)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(5)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setLineWidth(1)
    c.rect(34, 34, PAGE_W - 68, PAGE_H - 68)

    _centered(c, "CERTIFICATE", PAGE_H - 118, TITLE_FONT, 42, INK)
    _centered(c, "OF  COMPLETION", PAGE_H - 148, TITLE_FONT, 15, ACCENT)

    _centered(c, "This certificate is proudly presented to", PAGE_H - 198, BODY_FONT, 14, MUTED)
    name_size = _fit_size(recipient_name, NAME_FONT, 44)
    name_width = _centered(c, recipient_name, PAGE_H - 254, NAME_FONT, name_size, INK)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1.2)
    c.line(PAGE_W / 2 - name_width / 2 - 24, PAGE_H - 268,
           PAGE_W / 2 + name_width / 2 + 24, PAGE_H - 268)

    _centered(c, "for successfully completing the course", PAGE_H - 302, BODY_FONT, 14, MUTED)
    _centered(c, course_name, PAGE_H - 342, TITLE_FONT,
              _fit_size(course_name, TITLE_FONT, 26), INK)

    seal_cx, seal_cy = PAGE_W / 2, 118
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.circle(seal_cx, seal_cy, 44)
    c.setLineWidth(1)
    c.circle(seal_cx, seal_cy, 36)
    _centered(c, "SEAL", seal_cy - 3, SMALL_FONT, 9, ACCENT)

    c.setFont(BODY_FONT, 13)
    c.setFillColor(MUTED)
    c.drawString(84, 96, f"Date:  {issued_on.strftime('%d %B %Y')}")

    line_y = 112
    c.setStrokeColor(INK)
    c.setLineWidth(1)
    c.line(PAGE_W - 300, line_y, PAGE_W - 84, line_y)
    if issued_by:
        issuer_size = _fit_size(issued_by, NAME_FONT, 16, max_width=200)
        c.setFont(NAME_FONT, issuer_size)
        c.setFillColor(INK)
        c.drawCentredString(PAGE_W - 192, line_y + 10, issued_by)
    c.setFont(SMALL_FONT, 8)
    c.setFillColor(MUTED)
    c.drawCentredString(PAGE_W - 192, line_y - 14, "Authorised Signatory")

    _centered(c, f"Certificate ID: {certificate_id}   |   Issued {issued_on.isoformat()}",
              44, SMALL_FONT, 8, MUTED)

    c.showPage()
    c.save()
    return buffer.getvalue()
