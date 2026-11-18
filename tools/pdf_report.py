"""PDF export of a report snapshot. No Streamlit code.

An A4 business report in the "Mineral & Ink" style, built with ReportLab from
tools/report_data.ReportData: a title page header (logo, serif title, reporting
period, ink rule), report details, an executive summary with the key figures and
the most actionable findings, then trends, revenue mix, performance, insights,
anomalies and the methodology and data notes. Nothing is calculated here; every
number comes from the snapshot.

Style: white paper, ink text, 1 pt ink rules under the title and table headers,
hairlines between rows (no boxes, fills or shadows), copper section eyebrows,
Source Serif headings, IBM Plex Sans text and IBM Plex Mono for table figures,
axis numbers and timestamps. Status is always shown as an icon plus a label.

Charts are drawn with ReportLab's own vector graphics (no browser or image
renderer needed), with the design system's entity colours. The fonts are the
bundled open-licence files in static/fonts (embedded as subsets; nothing is
downloaded). A few symbols are still replaced with plain text, see pdf_text().
"""

from __future__ import annotations

import io
import math
from dataclasses import replace
from datetime import datetime
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, KeepTogether, NextPageTemplate,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

from config import business_rules as br
from config import design
from tools import anomalies as an
from tools import business_metrics as bm
from tools import insights as ins
from tools import performance as perf
from tools import report_data as rd

P = design.REPORT_PALETTE


def register_fonts() -> None:
    """Register the bundled TrueType fonts with ReportLab (once per process)."""
    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, file_name in design.REPORT_FONT_FILES.items():
        if name not in registered:
            pdfmetrics.registerFont(TTFont(name, str(design.FONT_DIR / file_name)))
    pdfmetrics.registerFontFamily(design.REPORT_FONT, normal=design.REPORT_FONT, bold=design.REPORT_FONT_BOLD,
                                  italic=design.REPORT_FONT, boldItalic=design.REPORT_FONT_BOLD)


register_fonts()
FONT, BOLD = design.REPORT_FONT, design.REPORT_FONT_BOLD
SERIF, DISPLAY, MONO = design.REPORT_FONT_SERIF, design.REPORT_FONT_DISPLAY, design.REPORT_FONT_MONO
SIZES = design.REPORT_FONT_SIZES
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = design.REPORT_MARGIN_MM * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
COVER_HEIGHT = 36 * mm            # page 1: logo, title, period and the ink rule above the content
HEADER_HEIGHT = 10 * mm           # later pages: running header above the content
FOOTER_GAP = 6 * mm               # space between the content and the footer rule
GUTTER = 8 * mm                   # between side-by-side tables and charts
CHART_HEIGHT = 64 * mm
MAX_X_LABELS = 12
CHART_NAME_CHARS = 26
KPI_FOOTNOTE_CHARS = 110
FREQUENCY_NAMES = {"D": "day", "W": "week", "M": "month", "Q": "quarter"}

# Characters that are printed as plain text instead (kept for consistent wording
# with the Excel export and older readers' copy-paste).
REPLACEMENTS = {"−": "-", "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≈": "~", "₹": "INR ",
                " ": " ", " ": " ", " ": " ", "✓": "", "⚠": ""}


def colour(value: str):
    return colors.HexColor(value)


INK, INK2, MUTED = colour(P["ink"]), colour(P["ink_secondary"]), colour(P["ink_muted"])
RULE, BORDER, DIVIDER = colour(P["rule"]), colour(P["border"]), colour(P["divider"])
ACCENT, PANEL = colour(P["accent_text"]), colour(P["panel"])
STATUS_FG = {kind: colour(fg) for kind, (fg, _, _) in design.REPORT_STATUS.items()}
STATUS_BG = {kind: colour(bg) for kind, (_, bg, _) in design.REPORT_STATUS.items()}
ENTITY = {name: colour(value) for name, value in design.REPORT_ENTITY_COLORS.items()}
HAIRLINE, RULE_WIDTH = 0.5, 0.9


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def pdf_text(value) -> str:
    """Text the report prints: known symbols replaced, anything else outside
    Windows-1252 shown as '?'."""
    text = str(value)
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.encode("cp1252", "replace").decode("cp1252")


def para_text(value) -> str:
    return escape(pdf_text(value))


def shorten(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def style(name: str, size: float | None = None, font: str = FONT, color=INK2, leading: float | None = None,
          **extra) -> ParagraphStyle:
    size = size or SIZES["body"]
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading or size * 1.42, textColor=color,
                          **extra)


BODY = style("body")
BODY_INK = style("body_ink", color=INK)
LEAD = style("lead", SIZES["body"] + 1, color=INK, leading=(SIZES["body"] + 1) * 1.5)
SMALL = style("small", SIZES["small"], color=MUTED)
SMALL_INK = style("small_ink", SIZES["small"] + 0.5, color=INK2)
LABEL = style("label", SIZES["label"], BOLD, MUTED, leading=SIZES["label"] * 1.3)
TITLE = style("title", SIZES["subsection"], BOLD, INK, spaceAfter=2)
SECTION = style("section", SIZES["section"], SERIF, INK, leading=SIZES["section"] * 1.25, spaceAfter=3)
TOPIC = style("topic", SIZES["topic"], SERIF, INK, leading=SIZES["topic"] * 1.3, spaceBefore=8, spaceAfter=2)
EYEBROW = style("eyebrow", SIZES["label"], BOLD, ACCENT, leading=SIZES["label"] * 1.5)
INTRO = style("intro", SIZES["small"] + 0.5, color=MUTED)
KPI_VALUE = style("kpi_value", SIZES["kpi"], BOLD, INK, leading=SIZES["kpi"] * 1.25)
BULLET = style("bullet", SIZES["body"] - 0.5, color=INK2, leftIndent=10, bulletIndent=1, spaceAfter=1)
CELL = style("cell", SIZES["table"], color=INK2)
CELL_NAME = style("cell_name", SIZES["table"], color=INK)
CELL_NUM = style("cell_num", SIZES["table"], MONO, INK2, alignment=2)
HEAD_CELL = style("head_cell", SIZES["label"], BOLD, MUTED)
HEAD_NUM = style("head_num", SIZES["label"], BOLD, MUTED, alignment=2)


def p(text, st: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(para_text(text), st)


def bold_lead(lead: str, text: str, st: ParagraphStyle = BODY) -> Paragraph:
    """'Lead: text' with the lead in semibold ink (e.g. 'Next step:')."""
    return Paragraph(f'<font name="{BOLD}" color="{P["ink"]}">{para_text(lead)}</font> {para_text(text)}', st)


# ---------------------------------------------------------------------------
# Status: always an icon and a label, never colour alone
# ---------------------------------------------------------------------------
def status_icon(c, kind: str, x: float, y: float, size: float) -> None:
    """A small filled status icon (x, y = bottom left): circle with a tick (success),
    triangle with '!' (warning), circle with '!' (error), circle with 'i' (info)."""
    c.saveState()
    c.setFillColor(STATUS_FG[kind])
    c.setStrokeColor(colors.white)
    c.setLineCap(1)
    c.setLineJoin(1)
    r, cx, cy = size / 2, x + size / 2, y + size / 2
    if kind == "warning":
        path = c.beginPath()
        path.moveTo(x, y)
        path.lineTo(x + size, y)
        path.lineTo(cx, y + size * 0.95)
        path.close()
        c.drawPath(path, stroke=0, fill=1)
        c.setLineWidth(size * 0.13)
        c.line(cx, y + size * 0.36, cx, y + size * 0.62)
        c.circle(cx, y + size * 0.2, size * 0.04, stroke=1, fill=0)
    else:
        c.circle(cx, cy, r, stroke=0, fill=1)
        c.setLineWidth(size * 0.14)
        if kind == "success":
            path = c.beginPath()
            path.moveTo(x + size * 0.27, cy)
            path.lineTo(x + size * 0.44, y + size * 0.32)
            path.lineTo(x + size * 0.74, y + size * 0.68)
            c.drawPath(path, stroke=1, fill=0)
        elif kind == "error":
            c.line(cx, y + size * 0.42, cx, y + size * 0.74)
            c.circle(cx, y + size * 0.25, size * 0.03, stroke=1, fill=0)
        else:
            c.line(cx, y + size * 0.26, cx, y + size * 0.52)
            c.circle(cx, y + size * 0.73, size * 0.03, stroke=1, fill=0)
    c.restoreState()


class StatusTag(Flowable):
    """One line of status labels (icon + uppercase label each), optionally followed by muted text."""

    def __init__(self, labels: list[tuple[str, str]], trailing: str = "", size: float = SIZES["label"]):
        super().__init__()
        self.labels, self.trailing, self.size = labels, pdf_text(trailing), size
        self.height = size * 1.7

    def wrap(self, available_width, available_height):
        self.width = available_width
        return available_width, self.height

    def draw(self):
        c, x, icon = self.canv, 0.0, self.size * 1.15
        baseline = (self.height - self.size) / 2 + 0.6
        for kind, text in self.labels:
            status_icon(c, kind, x, baseline - 0.9, icon)
            x += icon + 3
            label = pdf_text(text.upper())
            c.setFont(BOLD, self.size)
            c.setFillColor(STATUS_FG[kind])
            c.drawString(x, baseline, label)
            x += c.stringWidth(label, BOLD, self.size) + 8
        if self.trailing:
            c.setFont(FONT, self.size + 0.5)
            c.setFillColor(MUTED)
            room = max(self.width - x, 0)
            text = self.trailing
            while text and c.stringWidth(text, FONT, self.size + 0.5) > room:
                text = text[:-2].rstrip() + "…" if len(text) > 2 else ""
            c.drawString(x, baseline, text)


# ---------------------------------------------------------------------------
# Page decoration
# ---------------------------------------------------------------------------
PULSE = ((4.5, 15), (9, 15), (11.5, 8), (15.5, 21.5), (18.5, 12), (20.5, 15), (23.5, 15))   # assets/icon.svg


def logo_mark(c, x, y, size) -> None:
    """The BusinessPulse icon (assets/icon.svg): an ink tile with the pulse line cut out."""
    c.saveState()
    c.setFillColor(INK)
    c.roundRect(x, y, size, size, size * 5 / 28, stroke=0, fill=1)
    scale = size / 28
    c.setStrokeColor(colors.white)
    c.setLineWidth(max(0.6, 2.2 * scale))
    c.setLineCap(1)
    c.setLineJoin(1)
    path = c.beginPath()
    for index, (px, py) in enumerate(PULSE):
        point = (x + px * scale, y + size - py * scale)
        path.moveTo(*point) if index == 0 else path.lineTo(*point)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def wordmark(c, x, y, size) -> None:
    """'BusinessPulse' in one ink colour, as in the app's logo."""
    c.setFont(BOLD, size)
    c.setFillColor(INK)
    c.drawString(x, y, "BusinessPulse")


def brand(c, x, baseline, size) -> None:
    """Logo icon and wordmark, with the icon centred on the wordmark's capitals."""
    icon = size * 1.45
    logo_mark(c, x, baseline - size * 0.32, icon)
    wordmark(c, x + icon + size * 0.5, baseline, size)


def make_canvas(report: rd.ReportData):
    """A canvas class that draws the title header, running header and 'Page x of y' footer."""
    generated = report.generated_at.strftime("%d %b %Y, %H:%M")

    class ReportCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total: int) -> None:
            page = self._pageNumber
            top = PAGE_HEIGHT - MARGIN
            if page == 1:
                brand(self, MARGIN, top - 3 * mm, 10)
                self.setFillColor(MUTED)
                self.setFont(MONO, SIZES["label"])
                self.drawRightString(PAGE_WIDTH - MARGIN, top - 3 * mm, pdf_text(f"Generated {generated}"))
                self.setFillColor(INK)
                self.setFont(DISPLAY, SIZES["title"])
                self.drawString(MARGIN, top - 17 * mm, pdf_text(report.title))
                self.setFillColor(INK2)
                self.setFont(FONT, SIZES["body"] + 1.5)
                self.drawString(MARGIN, top - 24.5 * mm, pdf_text(f"Reporting period: {report.period_text}"))
                self.setStrokeColor(RULE)
                self.setLineWidth(RULE_WIDTH)
                self.line(MARGIN, top - 29 * mm, PAGE_WIDTH - MARGIN, top - 29 * mm)
            else:
                brand(self, MARGIN, top + 1 * mm, 8)
                self.setFillColor(MUTED)
                self.setFont(FONT, SIZES["label"])
                self.drawRightString(PAGE_WIDTH - MARGIN, top + 1 * mm,
                                     pdf_text(f"{report.title} · {report.period_text}"))
                self.setStrokeColor(BORDER)
                self.setLineWidth(HAIRLINE)
                self.line(MARGIN, top - 2.5 * mm, PAGE_WIDTH - MARGIN, top - 2.5 * mm)
            footer = MARGIN - FOOTER_GAP
            self.setStrokeColor(BORDER)
            self.setLineWidth(HAIRLINE)
            self.line(MARGIN, footer, PAGE_WIDTH - MARGIN, footer)
            self.setFillColor(MUTED)
            self.setFont(FONT, SIZES["label"])
            self.drawString(MARGIN, footer - 4 * mm, "BusinessPulse · Calculated locally from your data with "
                                                     "transparent rules; no estimates.")
            self.setFont(MONO, SIZES["label"])
            self.drawRightString(PAGE_WIDTH - MARGIN, footer - 4 * mm, f"Page {page} of {total}")

    return ReportCanvas


# ---------------------------------------------------------------------------
# Charts (ReportLab vector graphics)
# ---------------------------------------------------------------------------
def nice_ticks(low: float, high: float, count: int = 4) -> list[float]:
    if low == high:
        low, high = (low - 1, high + 1) if low else (0.0, 1.0)
    raw = (high - low) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start, end = math.floor(low / step) * step, math.ceil(high / step) * step
    return [start + i * step for i in range(int(round((end - start) / step)) + 1)]


def compact(value: float) -> str:
    """Short axis/bar label (the exact figures are in the tables and the Excel file)."""
    size = abs(value)
    sign = "-" if value < 0 else ""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if size >= limit:
            return f"{sign}{size / limit:,.1f}{suffix}".replace(".0" + suffix, suffix)
    return f"{sign}{size:,.0f}" if size >= 10 or size == 0 else f"{sign}{size:,.1f}"


def period_label(value, frequency: str) -> str:
    stamp = pd.Timestamp(value)
    if frequency == "Q":
        return f"Q{stamp.quarter} {stamp:%y}"
    if frequency == "M":
        return stamp.strftime("%b %y")
    return stamp.strftime("%d %b")


TICK_SIZE = SIZES["label"] - 0.5
CHART_PAD = {"left": 36, "bottom": 18, "right": 6, "top": 20}


def legend(drawing: Drawing, x: float, y: float, items: list[tuple[str, object, str]]) -> None:
    """Legend entries (name, colour, 'line' or 'bar'): a short line or a square swatch, then the name."""
    for name, fill, kind in items:
        if kind == "line":
            drawing.add(Line(x, y + 2.5, x + 12, y + 2.5, strokeColor=fill, strokeWidth=1.8))
            swatch = 15
        else:
            drawing.add(Rect(x, y - 0.5, 7, 7, fillColor=fill, strokeColor=None))
            swatch = 10
        drawing.add(String(x + swatch, y, pdf_text(name), fontName=FONT, fontSize=SIZES["label"], fillColor=INK2))
        x += swatch + pdfmetrics.stringWidth(pdf_text(name), FONT, SIZES["label"]) + 14


def _axes(drawing, left, bottom, width, height, ticks, currency) -> callable:
    low, high = ticks[0], ticks[-1]

    def y_of(value):
        return bottom + (value - low) / (high - low) * height

    for tick in ticks:
        y = y_of(tick)
        drawing.add(Line(left, y, left + width, y, strokeColor=colour(P["grid"]), strokeWidth=HAIRLINE))
        drawing.add(String(left - 5, y - 2.5, compact(tick), fontName=MONO, fontSize=TICK_SIZE,
                           fillColor=MUTED, textAnchor="end"))
    zero = y_of(0) if low <= 0 <= high else bottom
    drawing.add(Line(left, zero, left + width, zero, strokeColor=colour(P["axis"]), strokeWidth=0.8))
    if currency:
        drawing.add(String(left - 5, bottom + height + 7, pdf_text(currency), fontName=MONO, fontSize=TICK_SIZE,
                           fillColor=MUTED, textAnchor="end"))
    return y_of


def _x_labels(drawing, positions, periods, frequency, bottom) -> None:
    step = max(1, math.ceil(len(periods) / MAX_X_LABELS))
    for index in range(0, len(periods), step):
        drawing.add(String(positions[index], bottom - 11, period_label(periods[index], frequency), fontName=FONT,
                           fontSize=TICK_SIZE, fillColor=MUTED, textAnchor="middle"))


def line_chart(table: pd.DataFrame, series: list[tuple[str, str]], frequency: str, currency: str | None,
               width: float = CONTENT_WIDTH, height: float = CHART_HEIGHT) -> Drawing:
    """Lines per period (straight segments, one y-axis, no fills). series: (column, entity)."""
    drawing = Drawing(width, height)
    left, bottom = CHART_PAD["left"], CHART_PAD["bottom"]
    plot_w = width - left - CHART_PAD["right"]
    plot_h = height - bottom - CHART_PAD["top"]
    values = [v for column, _ in series for v in table[column].dropna()]
    ticks = nice_ticks(min(0.0, min(values, default=0)), max(values, default=1))
    y_of = _axes(drawing, left, bottom, plot_w, plot_h, ticks, currency)
    count = len(table)
    positions = [left + (plot_w * (i + 0.5) / count) for i in range(count)]
    for column, entity in series:
        segment = []
        for x, value in zip(positions + [None], list(table[column]) + [None]):
            if x is not None and pd.notna(value):
                segment.append((x, y_of(value)))
                continue
            if len(segment) > 1:
                drawing.add(PolyLine([c for point in segment for c in point], strokeColor=ENTITY[entity],
                                     strokeWidth=1.6, strokeLineJoin=1, strokeLineCap=1))
            if count <= design.CHART_MARKER_MAX_POINTS:
                for point in segment:
                    drawing.add(Circle(point[0], point[1], 1.7, fillColor=ENTITY[entity],
                                       strokeColor=colors.white, strokeWidth=0.6))
            segment = []
    _x_labels(drawing, positions, list(table["Period"]), frequency, bottom)
    legend(drawing, left, height - 9, [(column, ENTITY[entity], "line") for column, entity in series])
    return drawing


def profit_chart(table: pd.DataFrame, frequency: str, currency: str | None, label: str,
                 width: float = CONTENT_WIDTH, height: float = CHART_HEIGHT * 0.85) -> Drawing:
    """Profit bars per period: verdigris for a profit, cinnabar for a loss (below zero). Square ends."""
    drawing = Drawing(width, height)
    left, bottom = CHART_PAD["left"], CHART_PAD["bottom"]
    plot_w = width - left - CHART_PAD["right"]
    plot_h = height - bottom - CHART_PAD["top"]
    values = list(table["Profit"])
    known = [v for v in values if pd.notna(v)]
    ticks = nice_ticks(min(0.0, min(known)), max(0.0, max(known)))
    y_of = _axes(drawing, left, bottom, plot_w, plot_h, ticks, currency)
    count = len(table)
    slot = plot_w / count
    positions = [left + slot * (i + 0.5) for i in range(count)]
    for x, value in zip(positions, values):
        if pd.isna(value):
            continue
        y0, y1 = y_of(0), y_of(value)
        drawing.add(Rect(x - slot * 0.3, min(y0, y1), slot * 0.6, abs(y1 - y0) or 0.4,
                         fillColor=ENTITY["profit" if value >= 0 else "loss"], strokeColor=None))
    _x_labels(drawing, positions, list(table["Period"]), frequency, bottom)
    legend(drawing, left, height - 9, [(label, ENTITY["profit"], "bar"),
                                       ("Loss (below zero)", ENTITY["loss"], "bar")])
    return drawing


def bar_list(item: rd.Breakdown, width: float) -> Drawing:
    """Horizontal bars, largest first; 'All others' in the neutral colour. Names in sans, figures in mono."""
    rows = item.table
    bar_h, gap, top = 8.5, 5, 4
    height = top + len(rows) * (bar_h + gap) + 2
    drawing = Drawing(width, height)
    name_w, value_w = width * 0.40, 66
    plot_w = width - name_w - value_w
    high = max(float(rows["Amount"].max()), 0.0) or 1.0
    y = height - top - bar_h
    for name, amount, share in zip(rows["Name"], rows["Amount"], rows["Share"]):
        others = str(name).startswith("All others")
        drawing.add(String(name_w - 6, y + 2, pdf_text(shorten(name, CHART_NAME_CHARS)), fontName=FONT,
                           fontSize=SIZES["label"], fillColor=MUTED if others else INK2, textAnchor="end"))
        length = max(0.0, float(amount)) / high * plot_w
        drawing.add(Rect(name_w, y, max(length, 0.4), bar_h,
                         fillColor=ENTITY["others" if others else item.entity], strokeColor=None))
        share_text = f" · {share:.1f}%" if pd.notna(share) else ""
        drawing.add(String(name_w + length + 4, y + 2, compact(float(amount)) + share_text, fontName=MONO,
                           fontSize=TICK_SIZE, fillColor=MUTED))
        y -= bar_h + gap
    return drawing


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
class Rule(Flowable):
    """A full-width horizontal line (hairline by default)."""

    def __init__(self, colour_=BORDER, width: float = HAIRLINE, space_before: float = 0, space_after: float = 0):
        super().__init__()
        self.colour, self.line_width = colour_, width
        self.spaceBefore, self.spaceAfter = space_before, space_after

    def wrap(self, available_width, available_height):
        self.width = available_width
        return available_width, self.line_width

    def draw(self):
        self.canv.setStrokeColor(self.colour)
        self.canv.setLineWidth(self.line_width)
        self.canv.line(0, 0, self.width, 0)


def section(number: int, eyebrow: str, title: str, intro: str = "") -> list:
    """Section opening: hairline, copper eyebrow, serif title and a short muted introduction."""
    parts = [CondPageBreak(55 * mm), Spacer(1, 7 * mm), Rule(space_after=5),
             p(f"{number:02d} · {eyebrow.upper()}", EYEBROW), p(title, SECTION)]
    if intro:
        parts.append(p(intro, INTRO))
    parts.append(Spacer(1, 3.5 * mm))
    return parts


def chart_title(text: str) -> Paragraph:
    return p(text, TITLE)


def data_table(header: list[str], rows: list[list], widths: list[float], numeric: tuple[int, ...] = ()) -> Table:
    """Header in small semibold labels over a 1 pt ink rule; hairlines between rows.
    Columns in `numeric` hold figures: right-aligned, IBM Plex Mono. The first column holds names."""
    cells = [[Paragraph(para_text(h), HEAD_NUM if i in numeric else HEAD_CELL) for i, h in enumerate(header)]]
    for row in rows:
        cells.append([Paragraph(para_text(value), CELL_NUM if i in numeric else CELL_NAME if i == 0 else CELL)
                      for i, value in enumerate(row)])
    grid = Table(cells, colWidths=widths, repeatRows=1)
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
                              ("LINEBELOW", (0, 0), (-1, 0), RULE_WIDTH, RULE),
                              ("LINEBELOW", (0, 1), (-1, -1), HAIRLINE, DIVIDER),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                              ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                              ("LEFTPADDING", (0, 0), (0, -1), 0)]))
    return grid


def side_by_side(left: list, right: list | str) -> Table:
    half = (CONTENT_WIDTH - GUTTER) / 2
    grid = Table([[left, right]], colWidths=[half + GUTTER, half])
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (0, -1), GUTTER), ("RIGHTPADDING", (1, 0), (1, -1), 0),
                              ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return grid


def entry(content: list, heading: list | None = None) -> list:
    """One finding/insight/anomaly: kept together (with its heading, if given, so a
    heading is never left alone at the bottom of a page) and followed by a hairline.
    KeepTogether must never be nested: ReportLab then treats the group as too tall
    for any page and starts a new page for it."""
    return [KeepTogether((heading or []) + content + [Spacer(1, 5), Rule(DIVIDER)]), Spacer(1, 7)]


def money(value, currency) -> str:
    return bm.format_money(value, currency)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def details_block(report: rd.ReportData) -> list:
    """Report details as a two-column list of label / value pairs on a quiet ivory panel."""
    filters = "; ".join(f"{label}: {text}" for label, text in report.filter_lines) or "No product, category or " \
                                                                                     "region filters available"
    pairs = [("Data analysed", report.data_text, SMALL_INK), ("Reporting period", report.period_text, SMALL_INK),
             ("Active filters", filters, SMALL_INK), ("Revenue counts", report.counting_text, SMALL_INK),
             ("Rows with confirmed errors", report.error_rows_text, SMALL_INK),
             ("Currency", "; ".join(report.currency_lines[:2]) or "Not specified", SMALL_INK),
             ("Generated", report.generated_at.strftime("%d %b %Y, %H:%M"),
              style("mono_small", SIZES["small"], MONO, INK2)),
             ("Group dates by", FREQUENCY_NAMES.get(report.frequency, "-").capitalize() if report.window else "-",
              SMALL_INK)]
    cells, row = [], []
    for label, value, value_style in pairs:
        row.append([p(label.upper(), LABEL), Spacer(1, 1.5), p(value, value_style)])
        if len(row) == 2:
            cells.append(row)
            row = []
    grid = Table(cells, colWidths=[CONTENT_WIDTH / 2] * 2)
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                              ("LINEBELOW", (0, 0), (-1, -2), HAIRLINE, BORDER),
                              ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                              ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9)]))
    return [grid]


def warnings_block(report: rd.ReportData) -> list:
    critical = [text for kind, text in report.notes if kind == "error"]
    if report.mixed_currency:
        critical.append(report.currency_lines[-1])
    if not critical:
        return []
    rows = [[StatusTag([("error", "Warning")]), p(text, BODY_INK)] for text in critical]
    box = Table(rows, colWidths=[26 * mm, CONTENT_WIDTH - 26 * mm])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), STATUS_BG["error"]),
                             ("LINEBEFORE", (0, 0), (0, -1), 2, STATUS_FG["error"]),
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    return [Spacer(1, 4 * mm), box]


def kpi_grid(report: rd.ReportData) -> Table:
    """Key figures: 4 per row, separated by hairlines, with a 1 pt ink rule on top."""
    cells, row = [], []
    width = CONTENT_WIDTH / 4
    room = width - 16                      # cell width minus padding
    for kpi in report.kpis():
        value = pdf_text(report.format_kpi(kpi))
        size = SIZES["kpi"]
        while size > 12 and pdfmetrics.stringWidth(value, BOLD, size) > room:
            size -= 0.5                    # long values (e.g. with a currency code) stay on one line
        value_style = KPI_VALUE if size == SIZES["kpi"] else style(f"kpi_{size}", size, BOLD, INK,
                                                                   leading=SIZES["kpi"] * 1.25)
        content = [p(kpi.label.upper(), LABEL), Spacer(1, 2), p(value, value_style)]
        if kpi.key == "profit" and kpi.available:
            content.append(StatusTag([("success", "Profit")] if kpi.value >= 0 else [("error", "Loss")]))
        footnote = kpi.note if kpi.available else (f"Not available: {kpi.note}" if kpi.note else "Not available")
        if footnote:
            content.append(p(shorten(footnote, KPI_FOOTNOTE_CHARS), SMALL))
        row.append(content)
        if len(row) == 4:
            cells.append(row)
            row = []
    if row:
        cells.append(row + [""] * (4 - len(row)))
    grid = Table(cells, colWidths=[width] * 4)
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LINEABOVE", (0, 0), (-1, 0), RULE_WIDTH, RULE),
                              ("LINEBELOW", (0, 0), (-1, -1), HAIRLINE, BORDER),
                              ("LINEBEFORE", (1, 0), (-1, -1), HAIRLINE, BORDER),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                              ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                              ("LEFTPADDING", (0, 0), (0, -1), 0)]))
    return grid


def findings_block(findings: list[rd.Finding]) -> list:
    if not findings:
        return [p("No findings for the selected data. The reasons are listed under Insights and Anomalies.", SMALL)]
    parts = []
    number_style = style("finding_number", SIZES["section"], SERIF, MUTED, leading=SIZES["section"] * 1.2)
    for index, finding in enumerate(findings, start=1):
        row = Table([[p(f"{index}", number_style),
                      [StatusTag([(finding.status, finding.label)], finding.topic), p(finding.title, TITLE),
                       p(finding.evidence, SMALL_INK), Spacer(1, 1.5),
                       bold_lead("Next step:", finding.action, SMALL_INK)]]],
                    colWidths=[10 * mm, CONTENT_WIDTH - 10 * mm])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        parts += entry([row])
    return parts


def trends_section(report: rd.ReportData, number: int) -> list:
    unit = FREQUENCY_NAMES.get(report.frequency, "period")
    parts = section(number, "Trends", f"Performance per {unit}", f"Counted figures per {unit} for "
                                                                 f"{report.period_text}.")
    table = report.trend
    lines = [(column, entity) for column, entity in (("Revenue", "revenue"), ("Expenses", "expenses"))
             if column in table and table[column].notna().any()]
    if table.empty or not lines:
        return parts + [p("No trend: the data has no usable dates, or nothing matches the selected period.", SMALL)]
    currency = report.sales_currency if "Revenue" in table else report.expense_currency
    if report.mixed_currency and len(lines) == 2:
        # Different currencies never share an axis: one chart each.
        for column, entity in lines:
            chart_currency = report.sales_currency if column == "Revenue" else report.expense_currency
            parts += [KeepTogether([chart_title(f"{column} per {unit} ({chart_currency})"),
                                    line_chart(table, [(column, entity)], report.frequency, chart_currency,
                                               height=CHART_HEIGHT * 0.8)]), Spacer(1, 5 * mm)]
    else:
        title = " and ".join(column for column, _ in lines).replace("and Expenses", "and expenses")
        parts.append(KeepTogether([chart_title(f"{title} per {unit}"),
                                   line_chart(table, lines, report.frequency, currency)]))
    if report.trend_note:
        parts.append(p(report.trend_note, SMALL))
    if "Profit" in table and table["Profit"].notna().any():
        label = report.kpi("profit").label
        caption = "Teal bars show a profit, red bars a loss."
        period = report.result.profit_period
        if report.result.profit_type == "net" and period and (
                not report.window or (period[0].date(), period[1].date()) != tuple(report.window)):
            start, end = period
            caption += f" Only for {bm.format_date(start)} – {bm.format_date(end)}, the dates covered by both files."
        parts += [Spacer(1, 5 * mm), KeepTogether([chart_title(f"{label} per {unit}"), p(caption, SMALL),
                                                   Spacer(1, 2),
                                                   profit_chart(table, report.frequency,
                                                                report.kpi_currency("profit"), label)])]
    return parts


def mix_section(report: rd.ReportData, number: int) -> list:
    parts = section(number, "Revenue mix", "Where the money comes from and goes",
                    f"Largest first; beyond the top {br.TOP_ITEMS}, items are combined as 'All others'. Bar labels "
                    "are rounded; exact figures are in the Excel export.")
    if not report.breakdowns:
        return parts + [p("No product, category or region columns were recognised, so there are no breakdowns.",
                          SMALL)]
    half = (CONTENT_WIDTH - GUTTER) / 2
    blocks = []
    for item in report.breakdowns:
        title = item.title + (f" ({item.currency})" if item.currency else "")
        blocks.append([chart_title(title), Spacer(1, 2), bar_list(item, half)])
    for i in range(0, len(blocks), 2):
        pair = blocks[i:i + 2]
        parts += [side_by_side(pair[0], pair[1] if len(pair) == 2 else ""), Spacer(1, 6 * mm)]
    return parts


def performance_section(report: rd.ReportData, number: int) -> list:
    parts = section(number, "Performance", "Top and bottom performers", "Ranked by counted revenue over the whole "
                                                                         "reporting period. " + report.attribution[1])
    if not report.dimensions:
        return parts + [p("No product, category or region figures: the sales data has no such columns, or no sales "
                          "match the selected filters.", SMALL)]
    currency = report.sales_currency
    half = (CONTENT_WIDTH - GUTTER) / 2
    widths = [half * 0.52, half * 0.30, half * 0.18]
    for part in report.dimensions:
        plural = perf.DIMENSION_PLURALS[part.by]
        if part.top.empty:
            parts.append(p(f"{plural.capitalize()}: only one {part.by} has counted sales, so there is no ranking.",
                           SMALL))
            continue
        tables = []
        for title, chosen in ((f"Top {len(part.top)} {plural}", part.top),
                              (f"Bottom {len(part.bottom)} {plural}", part.bottom)):
            rows = [[name, money(revenue, currency), bm.format_percent(share)]
                    for name, revenue, share in zip(chosen["Name"], chosen["Revenue"], chosen["Share"])]
            tables.append([chart_title(title), data_table(["Name", "Revenue", "Share"], rows, widths,
                                                          numeric=(1, 2))])
        parts += [KeepTogether([side_by_side(*tables)]), Spacer(1, 5 * mm)]
        if part.note:
            parts.append(p(part.note, SMALL))
    return parts


def insights_section(report: rd.ReportData, number: int) -> list:
    insight_report = report.insights
    basis = (f"Trends compare {insight_report.period} with {insight_report.compared_with}. "
             if insight_report.period else "")
    parts = section(number, "Insights", "Business insights",
                    f"{basis}Products, categories and regions use the whole reporting period. Found with fixed, "
                    "transparent rules; suggested next steps are prompts to investigate, not guarantees.")
    labels = rd.TONE_LABELS
    for topic in ins.TOPICS:
        found = insight_report.for_topic(topic)
        if not found:
            parts += [bold_lead(f"{topic}:", "No finding. " + insight_report.missing.get(topic, "Not enough data."),
                                SMALL_INK), Spacer(1, 2 * mm)]
            continue
        heading = [p(topic, TOPIC), Rule(RULE, RULE_WIDTH, space_after=6)]
        for index, insight in enumerate(found):
            kind, label = labels.get(insight.tone, ("info", "Note"))
            content = [StatusTag([(kind, label)]), p(insight.title, TITLE)]
            content += [Paragraph(para_text(line), BULLET, bulletText="•") for line in insight.evidence]
            content += [Spacer(1, 2), bold_lead("Suggested next step:", insight.recommendation)]
            parts += entry(content, heading if index == 0 else None)
    return parts


def anomalies_section(report: rd.ReportData, number: int) -> list:
    anomaly_report = report.anomalies
    intro = (f"{anomaly_report.complete_months:,} complete month(s); each is compared with up to "
             f"{br.ANOMALY_MAX_HISTORY} complete months before it using a robust z-score (median and MAD). Medium "
             f"severity from ±{br.ANOMALY_Z_MEDIUM:g}, high from ±{br.ANOMALY_Z_HIGH:g}. An anomaly is a prompt to "
             "check, not proof of a problem.")
    parts = section(number, "Anomalies", "Unusual months", intro)
    if not anomaly_report.anomalies:
        parts.append(p("No unusual months found: every checked month stayed within its usual range."
                       if anomaly_report.checked else "Not enough data to check for anomalies. The reasons are "
                                                      "listed below.", BODY_INK))
    for anomaly in anomaly_report.anomalies:
        kind, label = rd.SEVERITY_LABELS[anomaly.severity]
        effect = "Unfavourable" if anomaly.unfavourable else "Favourable"
        content = [StatusTag([(kind, label), ("info", effect)], anomaly.area), p(anomaly.title, TITLE),
                   bold_lead("Why it was flagged:", anomaly.reason)]
        content += [Paragraph(para_text(line), BULLET, bulletText="•") for line in anomaly.evidence]
        parts += entry(content)
    lines = []
    for area in an.AREAS:
        if area in anomaly_report.missing:
            lines.append(f"{area}: not checked. {anomaly_report.missing[area]}")
        elif not anomaly_report.for_area(area):
            extra = "" if area in an.METRICS.values() else f" ({anomaly_report.checked.get(area, 0):,} checked)"
            lines.append(f"{area}: no unusual months{extra}.")
    lines += [f"Not checked, month not complete: {item}" for item in anomaly_report.skipped]
    if lines:
        parts.append(Spacer(1, 2 * mm))
        parts += [Paragraph(para_text(line), style("bullet_small", SIZES["small"], color=MUTED, leftIndent=10,
                                                   bulletIndent=1), bulletText="•") for line in lines]
    return parts


def methodology_section(report: rd.ReportData, number: int) -> list:
    parts = section(number, "Methodology", "How the figures were calculated and data notes",
                    "Every figure is calculated from the analysed data exactly as in the BusinessPulse app. "
                    "Row-level source records are in the Excel export.")
    rows = []
    for kpi in report.kpis():
        rows.append([kpi.label, report.format_kpi(kpi),
                     kpi.calculation if kpi.available else f"Not available: {kpi.note}"])
    parts.append(data_table(["Figure", "Value", "How it was calculated"], rows,
                            [36 * mm, 30 * mm, CONTENT_WIDTH - 66 * mm], numeric=(1,)))
    if report.notes:
        parts += [Spacer(1, 6 * mm), p("Data notes", TITLE), Spacer(1, 2)]
        labels = {"error": ("error", "Warning"), "warning": ("warning", "Check"), "info": ("info", "Note")}
        note_rows = []
        for kind, text in report.notes:
            status, label = labels.get(kind, ("info", "Note"))
            note_rows.append([StatusTag([(status, label)]), p(text, SMALL_INK)])
        grid = Table(note_rows, colWidths=[24 * mm, CONTENT_WIDTH - 24 * mm])
        grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LINEABOVE", (0, 0), (-1, 0), RULE_WIDTH, RULE),
                                  ("LINEBELOW", (0, 0), (-1, -1), HAIRLINE, DIVIDER),
                                  ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                  ("LEFTPADDING", (0, 0), (0, -1), 0)]))
        parts.append(grid)
    return parts


# ---------------------------------------------------------------------------
def build_story(report: rd.ReportData) -> list:
    story = [NextPageTemplate("later")]
    story += details_block(report)
    story += warnings_block(report)
    story += section(1, "Executive summary", "At a glance")
    story.append(p(" ".join(report.summary) or "No figures could be calculated for the selected data.", LEAD))
    story += [Spacer(1, 5 * mm), kpi_grid(report)]
    story += section(2, "Priority actions", "What needs attention first",
                     "The most actionable findings, from the insights and anomaly checks: high-severity "
                     "unfavourable changes first, then findings that need attention, then positive ones.")
    story += findings_block(report.findings[:rd.EXECUTIVE_FINDINGS])
    number = 3
    for builder in (trends_section, mix_section, performance_section, insights_section, anomalies_section,
                    methodology_section):
        story += builder(report, number)
        number += 1
    return story


DASHBOARD_TITLE = "Business dashboard"


def build_dashboard_story(report: rd.ReportData) -> list:
    """The Dashboard as a PDF: report details, key figures, trends and breakdowns
    (the same sections and figures as the full report, without insights and anomalies)."""
    story = [NextPageTemplate("later")]
    story += details_block(report)
    story += warnings_block(report)
    story += section(1, "Overview", "Key figures")
    story += [Spacer(1, 2 * mm), kpi_grid(report)]
    story += trends_section(report, 2)
    story += mix_section(report, 3)
    return story


def dashboard_pdf_bytes(report: rd.ReportData) -> bytes:
    """A PDF of the Dashboard for the current data, period and filters."""
    dashboard = replace(report, title=DASHBOARD_TITLE)
    return pdf_bytes(dashboard, build_dashboard_story(dashboard), subject="Business dashboard")


def pdf_bytes(report: rd.ReportData, story: list | None = None,
              subject: str = "Business performance report") -> bytes:
    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                               bottomMargin=MARGIN, title=pdf_text(f"{report.title} · {report.period_text}"),
                               author="BusinessPulse", subject=subject, creator="BusinessPulse")
    first = Frame(MARGIN, MARGIN, CONTENT_WIDTH, PAGE_HEIGHT - 2 * MARGIN - COVER_HEIGHT, id="first",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(MARGIN, MARGIN, CONTENT_WIDTH, PAGE_HEIGHT - 2 * MARGIN - HEADER_HEIGHT, id="later",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    document.addPageTemplates([PageTemplate("first", [first]), PageTemplate("later", [later])])
    document.build(story if story is not None else build_story(report), canvasmaker=make_canvas(report))
    return buffer.getvalue()


def error_pdf(message: str, generated_at: datetime | None = None) -> bytes:
    """A one-page PDF that explains why the report could not be created."""
    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=A4)
    top = PAGE_HEIGHT - MARGIN
    brand(c, MARGIN, top - 3 * mm, 10)
    c.setFillColor(INK)
    c.setFont(DISPLAY, SIZES["section"] + 5)
    c.drawString(MARGIN, top - 17 * mm, "The report could not be created")
    c.setStrokeColor(RULE)
    c.setLineWidth(RULE_WIDTH)
    c.line(MARGIN, top - 22 * mm, PAGE_WIDTH - MARGIN, top - 22 * mm)
    c.setFont(FONT, SIZES["body"])
    c.setFillColor(INK2)
    y = top - 32 * mm
    for line in simpleSplit(pdf_text(message)[:950], FONT, SIZES["body"], CONTENT_WIDTH):
        c.drawString(MARGIN, y, line)
        y -= SIZES["body"] * 1.45
    c.drawString(MARGIN, y - 8, "Please go back to BusinessPulse, check the data and settings, and try again.")
    c.setFillColor(MUTED)
    c.setFont(MONO, SIZES["label"])
    c.drawString(MARGIN, y - 24, (generated_at or datetime.now()).strftime("Generated %d %b %Y, %H:%M"))
    c.save()
    return buffer.getvalue()
