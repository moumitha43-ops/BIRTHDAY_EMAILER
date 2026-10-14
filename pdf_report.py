"""
pdf_report.py — Professional PDF birthday report using ReportLab

Layout
  • Header on every page: college logo (if uploaded) + institution block,
    gold/forest rule. Page 1 has the full header, later pages a compact one.
  • Title block + summary strip (report date · total · generated on)
  • Table whose header cells are aligned exactly like their data cells
  • Footer on every page: "system-generated" note and "Page X of Y"
"""
import io
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, NextPageTemplate,
    Table, TableStyle, Paragraph, Spacer,
)

from helpers import find_logo, now_local

# ── Palette ────────────────────────────────────────────────────────────────────
FOREST   = colors.HexColor("#1a2e1e")
GOLD     = colors.HexColor("#c9a84c")
GREEN_LT = colors.HexColor("#f2f7f3")
IVORY    = colors.HexColor("#faf6ec")
GREY     = colors.HexColor("#6b7a68")
LINE     = colors.HexColor("#d9d2bd")
WHITE    = colors.white

INSTITUTE = "Bannari Amman Institute of Technology"
DEPT      = "Office of Student Affairs"
ADDRESS   = "Sathyamangalam · Tamil Nadu"

PAGE_W, PAGE_H = A4
MARGIN    = 2 * cm
CONTENT_W = PAGE_W - 2 * MARGIN

HDR_TOP       = PAGE_H - 1.3 * cm      # y where the header starts
HDR_FULL_H    = 2.1 * cm               # full header height (page 1)
HDR_COMPACT_H = 1.2 * cm               # compact header height (page 2+)
FOOTER_Y      = 1.1 * cm


# ── Helpers ────────────────────────────────────────────────────────────────────
def _fit(path, max_w, max_h):
    """Scale an image to fit inside max_w × max_h, keeping its aspect ratio."""
    iw, ih = ImageReader(str(path)).getSize()
    s = min(max_w / iw, max_h / ih)
    return iw * s, ih * s


def _draw_header(c, report_title, full):
    logo = find_logo()
    x_text = MARGIN
    hdr_h = HDR_FULL_H if full else HDR_COMPACT_H

    if logo:
        try:
            lw, lh = _fit(logo, 3.2 * cm if full else 1.9 * cm, hdr_h)
            c.drawImage(str(logo), MARGIN, HDR_TOP - (hdr_h + lh) / 2,
                        width=lw, height=lh, mask="auto")
            x_text = MARGIN + lw + 0.5 * cm
        except Exception:
            x_text = MARGIN          # unreadable image → text-only header

    c.setFillColor(FOREST)
    if full:
        c.setFont("Helvetica-Bold", 15.5)
        c.drawString(x_text, HDR_TOP - 0.75 * cm, INSTITUTE)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 10)
        c.drawString(x_text, HDR_TOP - 1.3 * cm, DEPT)
        c.setFont("Helvetica", 8.5)
        c.drawString(x_text, HDR_TOP - 1.78 * cm, ADDRESS)
    else:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x_text, HDR_TOP - 0.5 * cm, INSTITUTE)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 8.5)
        c.drawString(x_text, HDR_TOP - 0.95 * cm, DEPT)
        c.drawRightString(PAGE_W - MARGIN, HDR_TOP - 0.5 * cm, report_title)

    # double rule: thick gold + hairline forest
    y = HDR_TOP - hdr_h - 0.25 * cm
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.6)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    c.setStrokeColor(FOREST)
    c.setLineWidth(0.4)
    c.line(MARGIN, y - 0.1 * cm, PAGE_W - MARGIN, y - 0.1 * cm)


class _NumberedCanvas(rl_canvas.Canvas):
    """Canvas that knows the total page count → 'Page X of Y' footer."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total):
        self.setStrokeColor(LINE)
        self.setLineWidth(0.5)
        self.line(MARGIN, FOOTER_Y + 0.45 * cm, PAGE_W - MARGIN, FOOTER_Y + 0.45 * cm)
        self.setFont("Helvetica", 8)
        self.setFillColor(GREY)
        self.drawString(MARGIN, FOOTER_Y,
                        f"{INSTITUTE} · This document is system-generated.")
        self.drawRightString(PAGE_W - MARGIN, FOOTER_Y,
                             f"Page {self._pageNumber} of {total}")


# ── Public API ─────────────────────────────────────────────────────────────────
def generate_birthday_report(
    students: list,
    query_date: date = None,
    title: str = None,
) -> bytes:
    """Return raw PDF bytes for a birthday report."""

    now = now_local()
    report_title = title or (
        f"Birthday Report — {query_date.strftime('%d %B %Y')}"
        if query_date else f"Birthday Report — {now.strftime('%d %B %Y')}"
    )

    # ── Page templates (full header on p1, compact afterwards) ──
    bottom    = FOOTER_Y + 1.1 * cm
    first_top = HDR_TOP - HDR_FULL_H - 0.9 * cm
    later_top = HDR_TOP - HDR_COMPACT_H - 0.9 * cm

    def frame(top, fid):
        return Frame(MARGIN, bottom, CONTENT_W, top - bottom, id=fid,
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=2 * cm, bottomMargin=2 * cm,
        title=report_title, author=INSTITUTE,
    )
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[frame(first_top, "f1")],
                     onPage=lambda c, d: _draw_header(c, report_title, True)),
        PageTemplate(id="Later", frames=[frame(later_top, "f2")],
                     onPage=lambda c, d: _draw_header(c, report_title, False)),
    ])

    # ── Styles ──
    H_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=15,
                             leading=19, textColor=FOREST, alignment=TA_LEFT)
    H_SUB   = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5,
                             leading=13, textColor=GREY, alignment=TA_LEFT)
    CAP     = ParagraphStyle("cap", fontName="Helvetica-Bold", fontSize=7.5,
                             leading=10, textColor=GREY, alignment=TA_LEFT)
    VAL     = ParagraphStyle("val", fontName="Helvetica-Bold", fontSize=11,
                             leading=14, textColor=FOREST, alignment=TA_LEFT)
    TH_L    = ParagraphStyle("thl", fontName="Helvetica-Bold", fontSize=9,
                             leading=11, textColor=WHITE, alignment=TA_LEFT)
    TH_C    = ParagraphStyle("thc", parent=TH_L, alignment=TA_CENTER)
    TD_L    = ParagraphStyle("tdl", fontName="Helvetica", fontSize=9,
                             leading=12, textColor=colors.HexColor("#22302a"),
                             alignment=TA_LEFT)
    TD_C    = ParagraphStyle("tdc", parent=TD_L, alignment=TA_CENTER)
    NOTE    = ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=10,
                             leading=14, textColor=GREY, alignment=TA_CENTER)

    story = [NextPageTemplate("Later")]

    # ── Title block ──
    story.append(Paragraph(report_title, H_TITLE))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Students celebrating their birthday on the selected date", H_SUB))
    story.append(Spacer(1, 0.45 * cm))

    # ── Summary strip: 3 equal cells, same left alignment as the table below ──
    report_date = (query_date or now.date()).strftime("%d %B %Y")
    cells = [
        [Paragraph("REPORT DATE", CAP), Paragraph(report_date, VAL)],
        [Paragraph("TOTAL STUDENTS", CAP), Paragraph(str(len(students)), VAL)],
        [Paragraph("GENERATED ON", CAP),
         Paragraph(now.strftime("%d %b %Y, %I:%M %p"), VAL)],
    ]
    third = CONTENT_W / 3
    strip = Table([cells], colWidths=[third] * 3)
    strip.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), IVORY),
        ("BOX",           (0, 0), (-1, -1), 0.6, LINE),
        ("LINEAFTER",     (0, 0), (1, 0), 0.6, LINE),
        ("LINEABOVE",     (0, 0), (-1, 0), 2, GOLD),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(strip)
    story.append(Spacer(1, 0.6 * cm))

    # ── Records ──
    if not students:
        box = Table([[Paragraph("No birthday records found for the selected date.", NOTE)]],
                    colWidths=[CONTENT_W])
        box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 26),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 26),
        ]))
        story.append(box)
    else:
        rows = [[Paragraph("#", TH_C), Paragraph("Name", TH_L),
                 Paragraph("Date of Birth", TH_C), Paragraph("Email", TH_L)]]

        def esc(t):
            return (str(t or "—").replace("&", "&amp;")
                    .replace("<", "&lt;").replace(">", "&gt;"))

        for i, s in enumerate(students, 1):
            rows.append([Paragraph(str(i), TD_C),
                         Paragraph(esc(s.get("name")), TD_L),
                         Paragraph(esc(s.get("dob")), TD_C),
                         Paragraph(esc(s.get("email")), TD_L)])

        col_w = [1.1 * cm, 5.6 * cm, 3.2 * cm, CONTENT_W - 9.9 * cm]
        tbl = Table(rows, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), FOREST),
            ("LINEBELOW",     (0, 0), (-1, 0), 1.5, GOLD),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("TOPPADDING",    (0, 1), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GREEN_LT]),
            ("LINEBELOW",     (0, 1), (-1, -1), 0.3, LINE),
            ("BOX",           (0, 0), (-1, -1), 0.6, LINE),
        ]))
        story.append(tbl)

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
