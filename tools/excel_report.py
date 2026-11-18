"""Excel export of a report snapshot. No Streamlit code.

Builds an .xlsx workbook in memory from tools/report_data.ReportData. Nothing is
calculated here: every figure is copied from the snapshot as a real number (so it
can still be summed, filtered or charted in Excel) and only its display format is
set. Percentages are stored as fractions (0.125) and displayed as 12.5%, so they
work correctly in further Excel calculations; dates are real Excel dates.

Style ("Mineral & Ink", with fonts every Excel reader has): serif sheet titles over
a thin ink rule, ivory table headers with an ink rule below, hairlines between
rows, no gridlines on the report sheets, status always as a word (with its
colour), losses in the loss colour with their minus sign. Each sheet prints on
A4, fitted to the page width, with repeated table headers and page numbers.

Safety: every text value is written as plain text. Text that Excel could read as
a formula (starting with =, +, -, @, a tab or a line break) is stored as a string
with Excel's "quote prefix", so it is never evaluated, even after the cell is
edited. No formulas are written anywhere in the workbook.
"""

from __future__ import annotations

import io
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.marker import DataPoint
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink

from config import business_rules as br
from config import design
from tools import anomalies as an
from tools import business_metrics as bm
from tools import insights as ins
from tools import performance as perf
from tools import report_data as rd

EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_TEXT = 32_767
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
COUNT_FORMAT = "#,##0"
DECIMAL_FORMAT = "#,##0.00"
PERCENT_FORMAT = "0.0%"            # values are stored as fractions: 0.125 is shown as 12.5%
DATE_FORMAT = "DD MMM YYYY"        # 01 Jan 2025, as in the app (unambiguous in every country)
DATETIME_FORMAT = "DD MMM YYYY HH:MM"
MONTH_FORMAT = "MMM YYYY"          # monthly periods in the Trend table
SHEET_NAMES = {"summary": "Summary", "trend": "Trend", "breakdowns": "Breakdowns", "performance": "Performance",
               "insights": "Insights", "anomalies": "Anomalies", "notes": "Data notes"}
SHEET_DESCRIPTIONS = {
    "summary": "Report details, executive summary, priority findings and key figures",
    "trend": "Totals per period, with charts",
    "breakdowns": "Revenue and expenses by product, category and region",
    "performance": "Top, bottom and all products, categories and regions",
    "insights": "Every business insight with its evidence and suggested next step",
    "anomalies": "Unusual months and what was checked",
    "notes": "Warnings and notes that affect the figures",
}
FREQUENCY_NAMES = {"D": "Day", "W": "Week", "M": "Month", "Q": "Quarter"}
BANNER_ROWS = 4                    # title, subtitle, rule, gap: content starts on row 5


def _hex(colour: str) -> str:
    return colour.lstrip("#").upper()


P = design.REPORT_PALETTE
SANS, SERIF = design.EXCEL_FONT, design.EXCEL_HEADING_FONT
HEADER_FILL = PatternFill("solid", fgColor=_hex(P["header_fill"]))
LABEL_FILL = PatternFill("solid", fgColor=_hex(P["panel"]))
STATUS_FILLS = {kind: PatternFill("solid", fgColor=_hex(bg)) for kind, (_, bg, _) in design.REPORT_STATUS.items()}
STATUS_FONTS = {kind: Font(name=SANS, size=10, bold=True, color=_hex(fg))
                for kind, (fg, _, _) in design.REPORT_STATUS.items()}
FONT = Font(name=SANS, size=10, color=_hex(P["ink_secondary"]))
INK_FONT = Font(name=SANS, size=10, color=_hex(P["ink"]))
MUTED_FONT = Font(name=SANS, size=9, color=_hex(P["ink_muted"]))
BOLD_FONT = Font(name=SANS, size=10, bold=True, color=_hex(P["ink"]))
HEADER_FONT = Font(name=SANS, size=10, bold=True, color=_hex(P["ink"]))
TITLE_FONT = Font(name=SERIF, size=18, bold=True, color=_hex(P["ink"]))
SUBTITLE_FONT = Font(name=SANS, size=10, color=_hex(P["ink_muted"]))
SECTION_FONT = Font(name=SERIF, size=13, bold=True, color=_hex(P["ink"]))
KPI_FONT = Font(name=SANS, size=11, bold=True, color=_hex(P["ink"]))
LINK_FONT = Font(name=SANS, size=10, color=_hex(P["primary"]), underline="single")
LOSS_FONT = Font(color=_hex(design.REPORT_ERROR))          # conditional format: negative profit / margin
INK_SIDE = Side(style="thin", color=_hex(P["rule"]))
ROW_SIDE = Side(style="thin", color=_hex(P["divider"]))
CELL_BORDER = Border(bottom=ROW_SIDE)
HEADER_BORDER = Border(bottom=INK_SIDE)
RULE_BORDER = Border(bottom=INK_SIDE)
WRAP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")
HEADER_ALIGN = Alignment(wrap_text=True, vertical="bottom")
HEADER_ALIGN_RIGHT = Alignment(wrap_text=True, vertical="bottom", horizontal="right")
REPORT_TAB, RECORDS_TAB = _hex(P["ink"]), _hex(design.REPORT_ENTITY_COLORS["others"])


# ---------------------------------------------------------------------------
# Safe cell writing
# ---------------------------------------------------------------------------
def clean_value(value):
    """A value openpyxl can store: numbers stay numbers, missing values become
    empty cells, dates become Excel dates and everything else becomes text."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) or math.isinf(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return (value.tz_localize(None) if value.tzinfo else value).to_pydatetime()
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return value
    text = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    return text[:EXCEL_MAX_TEXT]


def store(cell, value) -> None:
    """Put a cleaned value in a cell. Text is always stored as text, never as a
    formula: text Excel could treat as one gets the quote prefix."""
    value = clean_value(value)
    cell.value = value
    if isinstance(value, str):
        if cell.data_type == "f" or value.startswith(FORMULA_PREFIXES):
            cell.data_type = "s"
            cell.quotePrefix = True
    elif isinstance(value, datetime):
        cell.number_format = DATETIME_FORMAT if (value.hour or value.minute) else DATE_FORMAT
    elif isinstance(value, date):
        cell.number_format = DATE_FORMAT


def put(ws, row: int, column: int, value, number_format: str | None = None, font: Font = FONT,
        fill: PatternFill | None = None, alignment: Alignment | None = None, border: Border | None = None):
    """Write one styled cell (see store())."""
    cell = ws.cell(row=row, column=column)
    store(cell, value)
    if number_format:
        cell.number_format = number_format
    cell.font = font
    if fill is not None:
        cell.fill = fill
    if alignment is not None:
        cell.alignment = alignment
    if border is not None:
        cell.border = border
    return cell


def fraction(value):
    """A percentage as the app shows it (12.5 = 12.5%) stored the Excel way (0.125)."""
    value = clean_value(value)
    return value / 100 if isinstance(value, float | int) and not isinstance(value, bool) else value


def money_format(currency: str | None) -> str:
    if not currency:
        return f"{DECIMAL_FORMAT};-{DECIMAL_FORMAT}"
    label = str(currency).replace('"', "")
    return f'"{label} "{DECIMAL_FORMAT};-"{label} "{DECIMAL_FORMAT}'


def count_format(values) -> str:
    numbers = [float(v) for v in values if isinstance(v, (int, float, np.number)) and not pd.isna(v)]
    return COUNT_FORMAT if all(v.is_integer() for v in numbers) else DECIMAL_FORMAT


def period_format(frequency: str) -> str:
    return MONTH_FORMAT if frequency == "M" else DATE_FORMAT


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def banner(ws, title: str, subtitle: str, columns: int) -> int:
    """Serif sheet title, muted subtitle and a thin ink rule across the used columns.
    Returns the first free row (5)."""
    put(ws, 1, 1, title, font=TITLE_FONT, alignment=Alignment(vertical="bottom"))
    put(ws, 2, 1, subtitle, font=SUBTITLE_FONT, alignment=Alignment(vertical="top"))
    for column in range(1, columns + 1):
        ws.cell(row=3, column=column).border = RULE_BORDER
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 4
    return BANNER_ROWS + 1


def section(ws, row: int, title: str, caption: str = "") -> int:
    put(ws, row, 1, title, font=SECTION_FONT, alignment=Alignment(vertical="bottom"))
    ws.row_dimensions[row].height = 22
    if caption:
        put(ws, row + 1, 1, caption, font=MUTED_FONT)
        return row + 2
    return row + 1


def merged_text(ws, row: int, first: int, last: int, value, font: Font = FONT, fill: PatternFill | None = None,
                width: float = 100, border: Border | None = None) -> None:
    """Long text in one merged, wrapped cell spanning columns first..last (prints without being cut off)."""
    put(ws, row, first, value, font=font, fill=fill, alignment=WRAP, border=border)
    if last > first:
        ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        if border is not None:
            for column in range(first + 1, last + 1):
                ws.cell(row=row, column=column).border = border
    ws.row_dimensions[row].height = wrapped_height([str(value)], width)


def header_row(ws, row: int, names, numeric=()) -> None:
    for column, name in enumerate(names, start=1):
        put(ws, row, column, name, font=HEADER_FONT, fill=HEADER_FILL, border=HEADER_BORDER,
            alignment=HEADER_ALIGN_RIGHT if name in numeric else HEADER_ALIGN)
    ws.row_dimensions[row].height = 20


def table(ws, row: int, frame: pd.DataFrame, formats: dict[str, str] | None = None,
          wrap: tuple[str, ...] = (), wrap_width: float = 50, percent: tuple[str, ...] = ()) -> int:
    """A formatted table: ivory header over an ink rule, hairlines between rows.
    Columns in `percent` hold app percentages (12.5) and are stored as fractions.
    Returns the row after it (plus a gap)."""
    formats = formats or {}
    numeric = [name for name in frame.columns if name in formats and name != "Period"]
    header_row(ws, row, frame.columns, numeric)
    for offset, values in enumerate(frame.itertuples(index=False, name=None), start=1):
        for column, (name, value) in enumerate(zip(frame.columns, values), start=1):
            put(ws, row + offset, column, fraction(value) if name in percent else value, formats.get(name),
                font=INK_FONT if column == 1 else FONT, alignment=WRAP if name in wrap else TOP, border=CELL_BORDER)
        if wrap:
            texts = [str(v) for n, v in zip(frame.columns, values) if n in wrap and isinstance(v, str)]
            ws.row_dimensions[row + offset].height = wrapped_height(texts, wrap_width)
    return row + len(frame) + 2


def wrapped_height(texts: list[str], width: float) -> float:
    """Approximate row height for wrapped text (Excel does not resize rows written by openpyxl)."""
    lines = max((sum(max(1, math.ceil(len(part) / max(width * 1.1, 1))) for part in str(t).split("\n"))
                 for t in texts if t), default=1)
    return max(16, min(14 * lines + 3, 300))


def set_widths(ws, widths: dict[int, float]) -> None:
    for column, width in widths.items():
        ws.column_dimensions[get_column_letter(column)].width = min(width, design.EXCEL_COLUMN_MAX_WIDTH)


def auto_widths(ws, frame: pd.DataFrame, first_column: int = 1, minimum: float = 10) -> None:
    for offset, name in enumerate(frame.columns):
        sample = frame[name].head(500).map(lambda v: len(str(clean_value(v) or "")))
        width = max([len(str(name)) + 3, minimum] + ([int(sample.max()) + 3] if len(sample) else []))
        ws.column_dimensions[get_column_letter(first_column + offset)].width = min(width,
                                                                                  design.EXCEL_COLUMN_MAX_WIDTH)


def table_formats(frame: pd.DataFrame, money: dict[str, str | None], percent=(), counts=(),
                  frequency: str = "") -> dict[str, str]:
    formats = {name: money_format(currency) for name, currency in money.items() if name in frame}
    formats.update({name: PERCENT_FORMAT for name in percent if name in frame})
    formats.update({name: count_format(frame[name]) for name in counts if name in frame})
    if "Period" in frame:
        formats["Period"] = period_format(frequency)
    return formats


def mark_losses(ws, column: int, first_row: int, last_row: int) -> None:
    """Negative values (losses) in the loss colour; the minus sign stays, so colour is never the only cue."""
    if last_row >= first_row:
        letter = get_column_letter(column)
        ws.conditional_formatting.add(f"{letter}{first_row}:{letter}{last_row}",
                                      CellIsRule(operator="lessThan", formula=["0"], font=LOSS_FONT))


def print_setup(ws, landscape: bool = False, title_row: int | None = None) -> None:
    """A4, fitted to the page width, with repeated table headers and page numbers."""
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.print_options.horizontalCentered = False
    if title_row:
        ws.print_title_rows = f"{title_row}:{title_row}"
    ws.oddFooter.left.text = "BusinessPulse · &A"
    ws.oddFooter.left.size = 8
    ws.oddFooter.right.text = "Page &P of &N"
    ws.oddFooter.right.size = 8


def finish(ws, landscape: bool = False, title_row: int | None = None) -> None:
    """Common finishing for a report sheet: no gridlines, ink tab, print layout."""
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = REPORT_TAB
    print_setup(ws, landscape, title_row)


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
def details(report: rd.ReportData) -> list[tuple[str, str]]:
    lines = [("Report", report.title), ("Data", report.data_text), ("Reporting period", report.period_text)]
    if report.full_range:
        lines.append(("Data available", f"{bm.format_date(report.full_range[0])} – "
                                        f"{bm.format_date(report.full_range[1])}"))
    if report.window:
        lines.append(("Group dates by", FREQUENCY_NAMES.get(report.frequency, report.frequency)))
    lines += [("Revenue counts", report.counting_text), ("Rows with confirmed errors", report.error_rows_text)]
    lines += [(f"Filter: {label.lower()}", text) for label, text in report.filter_lines]
    lines += [("Currency", line) for line in report.currency_lines]
    lines.append(("Generated", report.generated_at.strftime("%d %b %Y %H:%M")))
    return lines


def contents(ws, row: int, report: rd.ReportData) -> int:
    """Links to every sheet (internal links, not formulas)."""
    row = section(ws, row, "Contents")
    entries = [(SHEET_NAMES[key], SHEET_DESCRIPTIONS[key]) for key in SHEET_NAMES if key != "summary"]
    entries += [(records.title[:31], f"{records.title}: the source rows behind the figures")
                for records in report.records]
    for name, description in entries:
        cell = put(ws, row, 1, name, font=LINK_FONT, border=CELL_BORDER)
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{name}'!A1", display=name)
        merged_text(ws, row, 2, 6, description, font=MUTED_FONT, border=CELL_BORDER)
        ws.row_dimensions[row].height = 16
        row += 1
    return row + 1


def summary_sheet(ws, report: rd.ReportData) -> None:
    set_widths(ws, {1: 28, 2: 20, 3: 13, 4: 44, 5: 56, 6: 44})
    row = banner(ws, report.title, f"BusinessPulse · Reporting period: {report.period_text}", 6)
    row = section(ws, row, "Report details")
    for label, text in details(report):
        put(ws, row, 1, label, font=BOLD_FONT, fill=LABEL_FILL, alignment=TOP, border=CELL_BORDER)
        merged_text(ws, row, 2, 6, text, border=CELL_BORDER, width=170)
        row += 1
    row += 1

    critical = [text for kind, text in report.notes if kind == "error"]
    for text in critical:
        put(ws, row, 1, "Warning", font=STATUS_FONTS["error"], fill=STATUS_FILLS["error"], alignment=TOP)
        merged_text(ws, row, 2, 6, text, font=STATUS_FONTS["error"], fill=STATUS_FILLS["error"], width=170)
        row += 1
    row += 1 if critical else 0

    row = contents(ws, row, report)

    row = section(ws, row, "Executive summary", "Plain-language summary made only from the figures below.")
    for sentence in report.summary:
        merged_text(ws, row, 1, 6, sentence, font=INK_FONT, width=200)
        row += 1
    row += 1

    findings = report.findings[:rd.EXECUTIVE_FINDINGS]
    row = section(ws, row, "Priority findings",
                  "Most actionable first. Every finding is listed on the Insights and Anomalies sheets.")
    if findings:
        header_row(ws, row, ("Status", "Topic", "#", "Finding", "Evidence", "Suggested next step"), ("#",))
        for index, finding in enumerate(findings, start=1):
            r = row + index
            put(ws, r, 1, finding.label, font=STATUS_FONTS[finding.status], fill=STATUS_FILLS[finding.status],
                alignment=WRAP, border=CELL_BORDER)
            put(ws, r, 2, finding.topic, alignment=TOP, border=CELL_BORDER)
            put(ws, r, 3, index, COUNT_FORMAT, alignment=TOP, border=CELL_BORDER)
            put(ws, r, 4, finding.title, font=BOLD_FONT, alignment=WRAP, border=CELL_BORDER)
            put(ws, r, 5, finding.evidence, alignment=WRAP, border=CELL_BORDER)
            put(ws, r, 6, finding.action, alignment=WRAP, border=CELL_BORDER)
            ws.row_dimensions[r].height = wrapped_height([finding.title, finding.evidence, finding.action], 44)
        row += len(findings) + 2
    else:
        put(ws, row, 1, "No findings for the selected data. The reasons are listed on the Insights and "
                        "Anomalies sheets.", font=MUTED_FONT)
        row += 2

    row = section(ws, row, "Key figures", "The same figures as on the Dashboard, with how each was calculated.")
    header_row(ws, row, ("KPI", "Value", "Currency", "What it means", "How it was calculated", "Notes"), ("Value",))
    for kpi in report.kpis():
        row += 1
        currency = report.kpi_currency(kpi.key)
        if not kpi.available:
            value, number_format = "Not available", None
        elif kpi.kind == bm.KIND_MONEY:
            value, number_format = kpi.value, money_format(currency)
        elif kpi.kind == bm.KIND_PERCENT:
            value, number_format = fraction(kpi.value), PERCENT_FORMAT
        else:
            value, number_format = kpi.value, count_format([kpi.value])
        put(ws, row, 1, kpi.label, font=BOLD_FONT, alignment=TOP, border=CELL_BORDER)
        put(ws, row, 2, value, number_format, font=KPI_FONT if kpi.available else MUTED_FONT,
            alignment=Alignment(vertical="top", horizontal="right"), border=CELL_BORDER)
        put(ws, row, 3, (currency or "Not specified") if kpi.kind == bm.KIND_MONEY else "", font=MUTED_FONT,
            alignment=TOP, border=CELL_BORDER)
        put(ws, row, 4, kpi.definition, alignment=WRAP, border=CELL_BORDER)
        calculation = kpi.calculation if kpi.available else f"Not available: {kpi.note}"
        note = kpi.note if kpi.available else ""
        put(ws, row, 5, calculation, alignment=WRAP, border=CELL_BORDER)
        put(ws, row, 6, note, alignment=WRAP, border=CELL_BORDER)
        ws.row_dimensions[row].height = wrapped_height([kpi.definition, calculation, note], 44)
        if kpi.key in ("profit", "margin") and kpi.available:
            mark_losses(ws, 2, row, row)
    row += 2
    put(ws, row, 1, f"Data notes: {len(report.notes):,} (see the '{SHEET_NAMES['notes']}' sheet). Source rows: see "
                    "the 'Filtered ... records' sheets.", font=MUTED_FONT)
    finish(ws, landscape=True)


def trend_sheet(ws, report: rd.ReportData) -> None:
    row = banner(ws, "Over time", f"Per {FREQUENCY_NAMES.get(report.frequency, 'period').lower()} · "
                                  f"{report.period_text}", 8)
    finish(ws, landscape=True)
    if report.trend.empty:
        put(ws, row, 1, "No trend: the data has no usable dates, or nothing matches the selected period.",
            font=MUTED_FONT)
        set_widths(ws, {1: 80})
        return
    frame = report.trend.copy()
    if not report.sales_trend.empty:
        extra = report.sales_trend.drop(columns=["Revenue"]).rename(columns={"Orders": report.orders_label})
        frame = frame.merge(extra, on="Period", how="left")
    money = {"Revenue": report.sales_currency, "Expenses": report.expense_currency,
             "Profit": report.kpi_currency("profit"), "Average order value": report.sales_currency}
    if report.mixed_currency:
        rename = {name: f"{name} ({currency})" for name, currency in money.items() if currency and name in frame}
        frame = frame.rename(columns=rename)
        money = {rename.get(name, name): currency for name, currency in money.items()}
    formats = table_formats(frame, money, counts=(report.orders_label, "Units"), frequency=report.frequency)
    row = section(ws, row, "Totals per period", report.trend_note or "")
    start = row
    table(ws, row, frame, formats)
    auto_widths(ws, frame, minimum=14)
    rows = len(frame)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    ws.auto_filter.ref = f"A{start}:{get_column_letter(len(frame.columns))}{start + rows}"
    ws.print_title_rows = f"{start}:{start}"
    if "Profit" in frame:
        mark_losses(ws, list(frame.columns).index("Profit") + 1, start + 1, start + rows)

    # Native Excel charts that read the table above (entity colours from the design system).
    categories = Reference(ws, min_col=1, min_row=start + 1, max_row=start + rows)
    line_columns = [i for i, name in enumerate(frame.columns, 1) if str(name).split(" (")[0] in ("Revenue", "Expenses")]
    anchor_column = get_column_letter(len(frame.columns) + 2)
    if line_columns and rows > 1:
        chart = LineChart()
        chart.title = " and ".join(str(frame.columns[i - 1]) for i in line_columns) + " per period"
        chart.height, chart.width = 8, 18
        for column in line_columns:
            chart.add_data(Reference(ws, min_col=column, min_row=start, max_row=start + rows), titles_from_data=True)
        for series, column in zip(chart.series, line_columns):
            name = str(frame.columns[column - 1])
            colour = design.REPORT_ENTITY_COLORS["expenses" if name.startswith("Expenses") else "revenue"]
            series.graphicalProperties.line.solidFill = _hex(colour)
            series.graphicalProperties.line.width = 22000
            series.marker.symbol = "circle" if rows <= design.CHART_MARKER_MAX_POINTS else "none"
            series.marker.size = 4
            series.marker.graphicalProperties = GraphicalProperties(solidFill=_hex(colour),
                                                                    ln=LineProperties(solidFill=_hex(colour)))
            series.smooth = False
        chart.set_categories(categories)
        style_chart(chart, period_format(report.frequency))
        chart.legend.position = "b"
        ws.add_chart(chart, f"{anchor_column}{start}")
    if "Profit" in frame and frame["Profit"].notna().any():
        column = list(frame.columns).index("Profit") + 1
        chart = BarChart()
        chart.title = "Profit per period (red = loss)"
        chart.height, chart.width = 8, 18
        chart.gapWidth = 60
        chart.add_data(Reference(ws, min_col=column, min_row=start, max_row=start + rows), titles_from_data=True)
        chart.set_categories(categories)
        series = chart.series[0]
        series.graphicalProperties.solidFill = _hex(design.REPORT_ENTITY_COLORS["profit"])
        series.graphicalProperties.line.noFill = True
        series.invertIfNegative = False
        for index, value in enumerate(frame["Profit"]):
            if pd.notna(value) and value < 0:
                point = DataPoint(idx=index)
                point.graphicalProperties.solidFill = _hex(design.REPORT_ENTITY_COLORS["loss"])
                point.graphicalProperties.line.noFill = True
                series.dPt.append(point)
        style_chart(chart, period_format(report.frequency))
        chart.legend = None
        ws.add_chart(chart, f"{anchor_column}{start + 18}")


def style_chart(chart, category_format: str) -> None:
    """Quiet chart chrome: visible axes, horizontal hairline grid only, plain number formats."""
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.y_axis.numFmt = "#,##0"
    chart.x_axis.number_format = category_format
    chart.x_axis.majorGridlines = None
    chart.y_axis.majorGridlines = ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill=_hex(P["grid"]),
                                                                                      w=6350)))
    chart.y_axis.graphicalProperties = GraphicalProperties(ln=LineProperties(noFill=True))
    chart.x_axis.graphicalProperties = GraphicalProperties(ln=LineProperties(solidFill=_hex(P["axis"]), w=9525))


def breakdowns_sheet(ws, report: rd.ReportData) -> None:
    row = banner(ws, "Breakdowns", f"Largest first · items beyond the top {br.TOP_ITEMS} are combined · "
                                   f"{report.period_text}", 5)
    set_widths(ws, {1: 36, 2: 20, 3: 12, 4: 12, 5: 12})
    finish(ws)
    if not report.breakdowns:
        put(ws, row, 1, "No product, category or region columns were recognised, so there are no breakdowns.",
            font=MUTED_FONT)
        return
    for item in report.breakdowns:
        frame = item.table.rename(columns={"Amount": item.value_label})
        row = section(ws, row, item.title + (f" ({item.currency})" if item.currency else ""))
        row = table(ws, row, frame, table_formats(frame, {item.value_label: item.currency}, percent=("Share",),
                                                  counts=("Rows", "Units")), percent=("Share",))


def performance_sheet(ws, report: rd.ReportData) -> None:
    row = banner(ws, "Performance", f"Counted sales per product, category and region · {report.period_text}", 8)
    set_widths(ws, {1: 36, 2: 18, 3: 12, 4: 12, 5: 12, 6: 20, 7: 18, 8: 14})
    finish(ws, landscape=True)
    if not report.dimensions:
        put(ws, row, 1, "No product, category or region figures: the sales data has no such columns, there are no "
                        "sales data, or no sales match the selected filters.", font=MUTED_FONT)
        return
    merged_text(ws, row, 1, 8, report.attribution[1], font=MUTED_FONT, width=140)
    row += 2
    currency = report.sales_currency
    percent = ("Share", "Gross margin")
    for part in report.dimensions:
        plural = perf.DIMENSION_PLURALS[part.by]
        frame = part.table.rename(columns={"Orders": report.orders_label})
        formats = table_formats(frame, {"Revenue": currency, "Average order value": currency,
                                        "Gross profit": currency}, percent=percent,
                                counts=(report.orders_label, "Units"))
        tables = [(f"Top {len(part.top)} {plural} by revenue", part.top, ""),
                  (f"Bottom {len(part.bottom)} {plural} by revenue", part.bottom, ""),
                  (f"All {plural} ({len(frame):,})", part.table, part.note)]
        for title, chosen, caption in tables:
            if chosen.empty and not title.startswith("All"):
                continue
            chosen = chosen.rename(columns={"Orders": report.orders_label})
            row = section(ws, row, title, caption)
            start = row
            row = table(ws, row, chosen, formats, percent=percent)
            for name in ("Gross profit", "Gross margin"):
                if name in chosen:
                    mark_losses(ws, list(chosen.columns).index(name) + 1, start + 1, start + len(chosen))


INSIGHT_STATUS = {"Positive": "success", "Needs attention": "warning", "Note": "info"}


def insights_sheet(ws, report: rd.ReportData) -> None:
    insight_report = report.insights
    basis = (f"Trends: {insight_report.period} compared with {insight_report.compared_with}. "
             if insight_report.period else "")
    row = banner(ws, "Insights", f"{basis}Products, categories and regions use the whole selected period.", 5)
    set_widths(ws, {1: 14, 2: 18, 3: 46, 4: 70, 5: 50})
    labels = {ins.POSITIVE: "Positive", ins.ATTENTION: "Needs attention", ins.NEUTRAL: "Note"}
    rows = []
    for topic in ins.TOPICS:
        found = insight_report.for_topic(topic)
        if not found:
            rows.append({"Topic": topic, "Status": "No finding", "Finding": insight_report.missing.get(
                topic, "Not enough data."), "Evidence": "", "Suggested next step": ""})
        for insight in found:
            rows.append({"Topic": topic, "Status": labels.get(insight.tone, "Note"), "Finding": insight.title,
                         "Evidence": "\n".join(insight.evidence), "Suggested next step": insight.recommendation})
    frame = pd.DataFrame(rows, columns=["Topic", "Status", "Finding", "Evidence", "Suggested next step"])
    table(ws, row, frame, wrap=("Finding", "Evidence", "Suggested next step"))
    for offset, status in enumerate(frame["Status"], start=1):
        cell = ws.cell(row=row + offset, column=2)
        kind = INSIGHT_STATUS.get(status)
        cell.font = STATUS_FONTS[kind] if kind else MUTED_FONT
        if kind:
            cell.fill = STATUS_FILLS[kind]
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    if len(frame):
        ws.auto_filter.ref = f"A{row}:E{row + len(frame)}"
    finish(ws, landscape=True, title_row=row)


def anomalies_sheet(ws, report: rd.ReportData) -> None:
    anomaly_report = report.anomalies
    row = banner(ws, "Anomalies", f"{anomaly_report.complete_months:,} complete month(s) · "
                                  f"{anomaly_report.months_checked:,} checked · {report.period_text}", 10)
    set_widths(ws, {1: 13, 2: 14, 3: 12, 4: 26, 5: 11, 6: 16, 7: 16, 8: 10, 9: 60, 10: 60})
    finish(ws, landscape=True)
    if anomaly_report.anomalies:
        columns = ("Severity", "Effect", "Area", "Subject", "Month", "Value", "Typical", "Robust z", "Why it was "
                   "flagged", "Evidence")
        header_row(ws, row, columns, ("Value", "Typical", "Robust z"))
        start = row
        for anomaly in anomaly_report.anomalies:
            row += 1
            status = "error" if anomaly.severity == an.HIGH else "warning"
            if anomaly.area in an.METRICS.values():
                currency = report.expense_currency if anomaly.area == "Expenses" else report.sales_currency
                value_format, value, typical = money_format(currency), anomaly.value, anomaly.typical
            else:       # products/categories/regions: share of the month's revenue
                value_format, value, typical = PERCENT_FORMAT, fraction(anomaly.value), fraction(anomaly.typical)
            put(ws, row, 1, an.SEVERITY_LABELS[anomaly.severity], font=STATUS_FONTS[status],
                fill=STATUS_FILLS[status], alignment=TOP, border=CELL_BORDER)
            put(ws, row, 2, "Unfavourable" if anomaly.unfavourable else "Favourable", alignment=TOP,
                border=CELL_BORDER)
            for column, text in ((3, anomaly.area), (4, anomaly.subject), (5, anomaly.period)):
                put(ws, row, column, text, alignment=WRAP, border=CELL_BORDER)
            put(ws, row, 6, value, value_format, alignment=TOP, border=CELL_BORDER)
            put(ws, row, 7, typical, value_format, alignment=TOP, border=CELL_BORDER)
            put(ws, row, 8, anomaly.z, "0.0", alignment=TOP, border=CELL_BORDER)
            put(ws, row, 9, anomaly.reason, alignment=WRAP, border=CELL_BORDER)
            put(ws, row, 10, "\n".join(anomaly.evidence), alignment=WRAP, border=CELL_BORDER)
            ws.row_dimensions[row].height = wrapped_height([anomaly.reason, "\n".join(anomaly.evidence)], 56)
        ws.freeze_panes = ws.cell(row=start + 1, column=1)
        ws.auto_filter.ref = f"A{start}:J{row}"
        ws.print_title_rows = f"{start}:{start}"
        row += 2
        put(ws, row, 1, "Value and Typical are amounts for revenue, expenses and profit, and a share of the month's "
                        "revenue (%) for products, categories and regions.", font=MUTED_FONT)
        row += 2
    else:
        put(ws, row, 1, "No unusual months found." if anomaly_report.checked else
            "Not enough data to check for anomalies. The reasons are listed below.", font=BOLD_FONT)
        row += 2
    row = section(ws, row, "What was checked")
    for area in an.AREAS:
        if area in anomaly_report.missing:
            text = f"Not checked: {anomaly_report.missing[area]}"
        else:
            count = len(anomaly_report.for_area(area))
            text = f"{count:,} unusual month(s) found" + (
                "" if area in an.METRICS.values() else f" ({anomaly_report.checked.get(area, 0):,} checked)")
        put(ws, row, 1, area, font=BOLD_FONT, border=CELL_BORDER)
        merged_text(ws, row, 2, 5, text, border=CELL_BORDER, width=90)
        row += 1
    for item in anomaly_report.skipped:
        put(ws, row, 1, "Skipped", font=BOLD_FONT, border=CELL_BORDER)
        merged_text(ws, row, 2, 5, f"Month not complete: {item}", border=CELL_BORDER, width=90)
        row += 1


def notes_sheet(ws, report: rd.ReportData) -> None:
    row = banner(ws, "Data notes", "Warnings and notes that affect the figures, and how they were calculated.", 2)
    set_widths(ws, {1: 14, 2: 110})
    finish(ws, title_row=row if report.notes else None)
    labels = {"error": "Warning", "warning": "Check", "info": "Note"}
    if not report.notes:
        put(ws, row, 1, "No data notes.", font=MUTED_FONT)
        return
    header_row(ws, row, ("Type", "Note"))
    start = row
    for kind, text in report.notes:
        row += 1
        status = "error" if kind == "error" else "warning" if kind == "warning" else "info"
        put(ws, row, 1, labels.get(kind, "Note"), font=STATUS_FONTS[status], fill=STATUS_FILLS[status],
            alignment=TOP, border=CELL_BORDER)
        put(ws, row, 2, text, alignment=WRAP, border=CELL_BORDER)
        ws.row_dimensions[row].height = wrapped_height([text], 110)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    ws.auto_filter.ref = f"A{start}:B{row}"


PLAIN_NUMBER = r"-?(?:0|[1-9]\d*)(?:\.\d+)?"          # no leading zeros, separators or symbols
ISO_DATE = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"


def typed_records(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Source columns kept as text by the loader, written as real Excel numbers or dates
    when (and only when) every filled value is unambiguous: a plain number such as 38.50,
    or an ISO date such as 2025-01-31. Anything else (IDs with leading zeros, '1,000',
    '$10', '31/01/2025', mixed columns) stays exactly as uploaded. Returns the frame to
    write and the number format per converted column."""
    frame = frame.copy()
    formats: dict[str, str] = {}
    for name in frame.columns:
        column = frame[name]
        if not (pd.api.types.is_object_dtype(column) or pd.api.types.is_string_dtype(column)):
            continue
        text = column.astype("string").str.strip()
        filled = text[text.notna() & (text != "")]
        if filled.empty:
            continue
        if filled.str.fullmatch(PLAIN_NUMBER).all():
            decimals = filled.str.extract(r"\.(\d+)$")[0].str.len().max()
            frame[name] = pd.to_numeric(text.where(text != ""), errors="coerce")
            formats[name] = "0" if pd.isna(decimals) else "#,##0." + "0" * int(min(decimals, 6))
        elif filled.str.fullmatch(ISO_DATE).all():
            parsed = pd.to_datetime(text.where(text != ""), errors="coerce")
            if parsed[filled.index].notna().all():
                frame[name] = parsed
    return frame, formats


def records_sheet(ws, report: rd.ReportData, records: rd.Records) -> None:
    """Filtered source records: the rows behind the figures, clearly separate from the summaries."""
    frame = records.frame
    width = max(len(frame.columns), 4)
    banner(ws, f"{records.title} (filtered source records)",
           "Source rows, not a calculated summary. Totals are on the Summary sheet.", width)
    put(ws, 5, 1, records.description, font=MUTED_FONT)
    filters = "; ".join(f"{label}: {text}" for label, text in report.filter_lines) or "none"
    put(ws, 6, 1, f"File: {records.file_name} · Version: {records.version} · Period: {report.period_text} · "
                  f"Filters: {filters} · Rows: {len(frame):,}", font=MUTED_FONT)
    header = 8
    limit = EXCEL_MAX_ROWS - header
    if len(frame) > limit:
        put(ws, 7, 1, f"Only the first {limit:,} rows fit in one Excel sheet; {len(frame) - limit:,} more are not "
                      "listed. The totals on the Summary sheet include every row.", font=STATUS_FONTS["warning"])
        frame = frame.head(limit)
    frame, formats = typed_records(frame)
    numeric = [name for name in frame.columns if pd.api.types.is_numeric_dtype(frame[name])]
    header_row(ws, header, frame.columns, numeric)
    column_formats = [formats.get(name) for name in frame.columns]
    # Source rows are written without fonts or borders (much faster for large files),
    # with the same text-safety rule as every other cell.
    for offset, values in enumerate(frame.itertuples(index=False, name=None), start=1):
        for column, value in enumerate(values, start=1):
            if clean_value(value) is not None:
                cell = ws.cell(row=header + offset, column=column)
                store(cell, value)
                if column_formats[column - 1]:
                    cell.number_format = column_formats[column - 1]
    auto_widths(ws, frame)
    ws.freeze_panes = ws.cell(row=header + 1, column=2)
    if len(frame):
        ws.auto_filter.ref = f"A{header}:{get_column_letter(len(frame.columns))}{header + len(frame)}"
    ws.sheet_properties.tabColor = RECORDS_TAB     # source data: visually set apart
    print_setup(ws, landscape=True, title_row=header)


# ---------------------------------------------------------------------------
def build_workbook(report: rd.ReportData) -> Workbook:
    workbook = Workbook()
    workbook.properties.creator = "BusinessPulse"
    workbook.properties.title = f"{report.title} · {report.period_text}"
    summary = workbook.active
    summary.title = SHEET_NAMES["summary"]
    summary_sheet(summary, report)
    for key, builder in (("trend", trend_sheet), ("breakdowns", breakdowns_sheet),
                         ("performance", performance_sheet), ("insights", insights_sheet),
                         ("anomalies", anomalies_sheet), ("notes", notes_sheet)):
        builder(workbook.create_sheet(SHEET_NAMES[key]), report)
    for records in report.records:
        records_sheet(workbook.create_sheet(records.title[:31]), report, records)
    return workbook


def excel_bytes(report: rd.ReportData) -> bytes:
    buffer = io.BytesIO()
    build_workbook(report).save(buffer)
    return buffer.getvalue()


def error_workbook(message: str) -> bytes:
    """A one-sheet workbook that explains why the report could not be created."""
    workbook = Workbook()
    ws = workbook.active
    ws.title = "Report not created"
    row = banner(ws, "BusinessPulse", "The report could not be created", 4)
    put(ws, row, 1, message, font=STATUS_FONTS["error"])
    put(ws, row + 2, 1, "Please go back to BusinessPulse, check the data and settings, and try again.")
    set_widths(ws, {1: 100})
    ws.sheet_view.showGridLines = False
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
