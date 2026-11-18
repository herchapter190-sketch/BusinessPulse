"""Stage 8 checks: report snapshot, Excel export, PDF export and the Reports page."""

import io
import zipfile
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config import design
from config import session_keys
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import excel_report as xr
from tools import pdf_report as pr
from tools import report_data as rd
from tools.file_loader import load_file
from tools.sample_data import read_sample

PAGE = str(Path(__file__).resolve().parent.parent / "views" / "reports.py")
TODAY = QualityRules().today()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sample_frame(key: str) -> tuple[str, pd.DataFrame]:
    name, content = read_sample(key)
    return name, load_file(name, content).data


def prepare(name: str, frame: pd.DataFrame, version: str = "Original upload", errors: bool = False):
    return bm.prepare_dataset(frame, name, version, include_error_rows=errors)


def make_report(sales_frame=None, expense_frame=None, sales_name="sales_clean.csv",
                expense_name="expenses_clean.csv", filters: bm.Filters | None = None, pending=False,
                frequency="M") -> rd.ReportData:
    sales = prepare(sales_name, sales_frame) if sales_frame is not None else None
    expenses = prepare(expense_name, expense_frame) if expense_frame is not None else None
    full = bm.available_range(sales, expenses)
    default = bm.default_range(sales, expenses, TODAY)
    filters = filters or (bm.Filters(*default) if default else bm.Filters())
    window = (filters.start or default[0], filters.end or default[1]) if full else None
    main = sales or expenses
    return rd.build_report(file_name=main.name, version=main.version,
                           companion_name=expenses.name if sales and expenses else None, sales=sales,
                           expenses=expenses, sales_frame=sales_frame, expense_frame=expense_frame, filters=filters,
                           window=window, full_range=full, frequency=frequency, include_pending=pending,
                           include_errors=False, today=TODAY)


@pytest.fixture(scope="module")
def demo() -> rd.ReportData:
    return make_report(sample_frame("sales_clean")[1], sample_frame("expenses_clean")[1])


@pytest.fixture(scope="module")
def demo_workbook(demo) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(xr.excel_bytes(demo)))


def pdf_text_content(report: rd.ReportData) -> str:
    """The PDF's page content, uncompressed, so its text can be searched."""
    from reportlab import rl_config
    previous = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        data = pr.pdf_bytes(report)
    finally:
        rl_config.pageCompression = previous
    assert data.startswith(b"%PDF")
    return data.decode("latin-1")


def summary_kpis(workbook) -> dict[str, object]:
    ws = workbook["Summary"]
    rows = list(ws.iter_rows(values_only=True))
    start = next(i for i, row in enumerate(rows) if row[:2] == ("KPI", "Value"))
    return {row[0]: row[1] for row in rows[start + 1:start + 9] if row[0]}


# ---------------------------------------------------------------------------
# Snapshot: same figures as the app
# ---------------------------------------------------------------------------
def test_report_figures_match_the_dashboard(demo):
    sales, expenses = demo.sales, demo.expenses
    expected = bm.compute_dashboard(sales, expenses, demo.filters, False)
    for key, kpi in expected.kpis.items():
        assert demo.result.kpis[key].value == kpi.value
    assert demo.format_kpi(demo.kpi("revenue")) == "461,120.95"      # as on the Dashboard (tests of Stage 5)
    assert demo.format_kpi(demo.kpi("profit")) == "46,010.52"
    assert demo.format_kpi(demo.kpi("margin")) == "10.0%"
    assert demo.trend.equals(bm.time_table(expected, sales, expenses, demo.filters, "M"))


def test_findings_are_prioritised_and_summary_uses_only_figures(demo):
    priorities = [finding.priority for finding in demo.findings]
    assert priorities == sorted(priorities)
    assert demo.findings[0].status in ("error", "warning")
    assert all(finding.action for finding in demo.findings)
    assert any("461,120.95" in sentence for sentence in demo.summary)
    assert len(demo.findings) == len(demo.insights.insights) + len(demo.anomalies.anomalies)


def test_records_contain_only_filtered_rows_with_counting_status():
    _, sales_frame = sample_frame("sales_clean")
    product = sales_frame["product_name"].iloc[0]
    filters = bm.Filters(date(2025, 3, 1), date(2025, 3, 31), products=(product,))
    report = make_report(sales_frame, filters=filters)
    records = report.records[0].frame
    assert len(records) == len(report.result.sales_rows) > 0
    assert set(records["product_name"]) == {product}
    dates = pd.to_datetime(records["order_date"])
    assert dates.min() >= pd.Timestamp("2025-03-01") and dates.max() < pd.Timestamp("2025-04-01")
    assert (records[rd.RECORD_COUNTED] == "Yes").sum() == len(report.result.revenue_rows)
    assert list(records.columns[1:12]) == list(sales_frame.columns)          # original columns, unchanged
    not_counted = records[records[rd.RECORD_COUNTED] == "No"]
    assert (not_counted[rd.RECORD_NOTE] != "").all()


def test_pending_setting_changes_counted_rows_and_revenue():
    _, sales_frame = sample_frame("sales_clean")
    off, on = make_report(sales_frame), make_report(sales_frame, pending=True)
    pending_off = (off.records[0].frame[rd.RECORD_NOTE].str.startswith("Pending")).sum()
    assert pending_off > 0
    assert (on.records[0].frame[rd.RECORD_NOTE].str.startswith("Pending")).sum() == 0
    assert on.kpi("revenue").value > off.kpi("revenue").value
    assert on.counting_text == "Completed and pending orders"


def test_records_follow_the_analysed_version():
    """Cleaned data: the records come from the frame that was analysed, not the original upload."""
    _, sales_frame = sample_frame("sales_clean")
    cleaned = sales_frame.iloc[:500].copy()
    sales = prepare("sales_clean.csv", cleaned, "Cleaned dataset")
    report = rd.build_report(file_name="sales_clean.csv", version="Cleaned dataset", companion_name=None,
                             sales=sales, expenses=None, sales_frame=cleaned, expense_frame=None,
                             filters=bm.Filters(), window=None, full_range=None, frequency="M",
                             include_pending=False, include_errors=False, today=TODAY)
    assert len(report.records[0].frame) == len(report.result.sales_rows) <= 500
    assert report.records[0].version == "Cleaned dataset"


# ---------------------------------------------------------------------------
# Mixed currencies
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mixed() -> rd.ReportData:
    _, sales_frame = sample_frame("sales_clean")
    _, expense_frame = sample_frame("expenses_clean")
    return make_report(sales_frame.rename(columns={"total_sales": "total_sales_usd"}),
                       expense_frame.rename(columns={"amount": "amount_eur"}))


def test_mixed_currencies_are_never_combined(mixed):
    assert mixed.mixed_currency
    assert mixed.kpi_currency("revenue") == "USD" and mixed.kpi_currency("expenses") == "EUR"
    assert not mixed.kpi("profit").available and not mixed.kpi("margin").available
    assert "Profit" not in mixed.trend
    assert not [i for i in mixed.insights.insights if i.topic in ("Expenses", "Profit")]
    assert not [a for a in mixed.anomalies.anomalies if a.area in ("Expenses", "Profit")]
    assert "conversion rate" in mixed.insights.missing["Expenses"]
    expense_breakdown = [b for b in mixed.breakdowns if b.entity == "expenses"][0]
    assert expense_breakdown.currency == "EUR"


def test_mixed_currency_exports_label_each_currency(mixed):
    workbook = openpyxl.load_workbook(io.BytesIO(xr.excel_bytes(mixed)))
    ws = workbook["Summary"]
    formats = {row[0].value: row[1].number_format for row in ws.iter_rows(min_col=1, max_col=2)
               if row[0].value in ("Total revenue", "Total expenses")}
    assert "USD" in formats["Total revenue"] and "EUR" in formats["Total expenses"]
    headers = [cell.value for cell in workbook["Trend"][next(
        r for r in range(1, 20) if workbook["Trend"].cell(r, 1).value == "Period")]]
    assert "Revenue (USD)" in headers and "Expenses (EUR)" in headers and "Profit" not in headers
    text = pdf_text_content(mixed)
    assert "different currencies" in text and "USD" in text and "EUR" in text


def test_mixed_symbols_in_one_file_raise_a_warning():
    frame = pd.DataFrame({"date": ["2025-01-05", "2025-01-06", "2025-02-07"],
                          "product": ["A", "B", "A"], "sales": ["$10", "€20", "$5"]})
    report = make_report(frame, sales_name="mixed.csv")
    assert any(kind == "error" and "different currency symbols" in text for kind, text in report.notes)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def test_workbook_sheets_headers_and_formats(demo_workbook):
    assert demo_workbook.sheetnames == ["Summary", "Trend", "Breakdowns", "Performance", "Insights", "Anomalies",
                                       "Data notes", "Filtered sales records", "Filtered expense records"]
    trend = demo_workbook["Trend"]
    header_row = next(r for r in range(1, 20) if trend.cell(r, 1).value == "Period")
    header = trend.cell(header_row, 1)
    assert header.font.bold and header.fill.fgColor.rgb.endswith(design.REPORT_PALETTE["header_fill"].lstrip("#"))
    assert header.border.bottom.color.rgb.endswith(design.REPORT_PALETTE["rule"].lstrip("#"))
    assert isinstance(trend.cell(header_row + 1, 1).value, datetime)            # a real date, not text
    assert trend.cell(header_row + 1, 1).number_format == xr.MONTH_FORMAT      # monthly periods: "Jan 2025"
    assert trend.cell(header_row + 1, 2).number_format == xr.money_format(None)
    assert trend.freeze_panes == f"A{header_row + 1}"
    assert trend.auto_filter.ref.startswith(f"A{header_row}:")
    assert len(trend._charts) == 2                                   # revenue/expenses lines + profit bars
    assert any(rule.rules[0].operator == "lessThan" for rule in trend.conditional_formatting)  # losses marked


def test_workbook_layout_navigation_and_print_setup(demo_workbook):
    summary = demo_workbook["Summary"]
    links = {cell.value: cell.hyperlink.location for row in summary.iter_rows() for cell in row
             if cell.hyperlink is not None}
    for name in demo_workbook.sheetnames[1:]:                          # contents links to every other sheet
        assert links[name] == f"'{name}'!A1"
    for ws in demo_workbook.worksheets:
        assert ws.page_setup.fitToWidth == 1 and ws.sheet_properties.pageSetUpPr.fitToPage
        assert "&P" in ws.oddFooter.right.text
        if not ws.title.startswith("Filtered"):
            assert ws.sheet_view.showGridLines is False
            assert ws["A1"].font.name == design.EXCEL_HEADING_FONT
    assert demo_workbook["Insights"].print_title_rows and demo_workbook["Insights"].auto_filter.ref


def test_workbook_percentages_are_fractions(demo, demo_workbook):
    """Percentages are stored as Excel fractions (0.125) shown as 12.5%, never as 12.5 or as text."""
    breakdowns = demo_workbook["Breakdowns"]
    shares = [cell.value for row in breakdowns.iter_rows() for cell in row
              if cell.number_format == xr.PERCENT_FORMAT and cell.value is not None]
    assert shares and all(isinstance(v, float) and 0 <= v <= 1 for v in shares)
    first = demo.breakdowns[0].table["Share"].iloc[0]
    assert shares[0] == pytest.approx(first / 100)
    for ws in demo_workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.number_format == xr.PERCENT_FORMAT and cell.value is not None:
                    assert isinstance(cell.value, (int, float)) and abs(cell.value) < 10, (ws.title, cell.coordinate)


def test_records_sheet_writes_unambiguous_numbers_and_dates(demo_workbook):
    ws = demo_workbook["Filtered sales records"]
    headers = {ws.cell(8, c).value: c for c in range(1, ws.max_column + 1)}
    row = 9
    assert isinstance(ws.cell(row, headers["order_date"]).value, datetime)
    assert isinstance(ws.cell(row, headers["total_sales"]).value, float)
    assert ws.cell(row, headers["total_sales"]).number_format == "#,##0.00"
    assert isinstance(ws.cell(row, headers["quantity"]).value, int)
    assert isinstance(ws.cell(row, headers["order_id"]).value, str)            # IDs stay text


def test_typed_records_leave_ambiguous_text_unchanged():
    frame = pd.DataFrame({"id": ["00123", "00456"], "amount": ["1,000", "5"], "price": ["2.50", ""],
                          "when": ["31/01/2025", "2025-02-01"], "iso": ["2025-01-31", None]})
    typed, formats = xr.typed_records(frame)
    assert list(typed["id"]) == ["00123", "00456"] and list(typed["amount"]) == ["1,000", "5"]
    assert list(typed["when"]) == ["31/01/2025", "2025-02-01"]
    assert typed["price"].iloc[0] == 2.5 and pd.isna(typed["price"].iloc[1]) and formats["price"] == "#,##0.00"
    assert typed["iso"].iloc[0] == pd.Timestamp("2025-01-31") and pd.isna(typed["iso"].iloc[1])


def test_workbook_figures_match_the_app(demo, demo_workbook):
    values = summary_kpis(demo_workbook)
    assert values["Total revenue"] == pytest.approx(demo.kpi("revenue").value)
    assert values["Net profit"] == pytest.approx(demo.kpi("profit").value)
    assert values["Profit margin"] == pytest.approx(demo.kpi("margin").value / 100)     # 10.0% stored as 0.0998
    trend = demo_workbook["Trend"]
    header_row = next(r for r in range(1, 20) if trend.cell(r, 1).value == "Period")
    revenue = [trend.cell(r, 2).value for r in range(header_row + 1, header_row + 1 + len(demo.trend))]
    assert sum(revenue) == pytest.approx(demo.kpi("revenue").value)


def test_records_sheet_is_labelled_and_complete(demo, demo_workbook):
    ws = demo_workbook["Filtered sales records"]
    assert "filtered source records" in ws["A1"].value
    assert "not a calculated summary" in ws["A2"].value
    assert ws.cell(8, 1).value == rd.RECORD_ROW
    assert ws.max_row - 8 == len(demo.records[0].frame)
    assert ws.auto_filter.ref and ws.freeze_panes == "B9"


def test_formula_injection_is_neutralised_everywhere():
    dangerous = ["=HYPERLINK(\"http://x\",\"click\")", "+cmd|' /C calc'!A0", "-2+3", "@SUM(1,1)", "\t=1+1"]
    frame = pd.DataFrame({"date": ["2025-01-0%d" % (i + 1) for i in range(5)] + ["2025-02-01"],
                          "product": dangerous + ["Safe"], "category": dangerous[::-1] + ["Safe"],
                          "sales": [10, 20, 30, 40, 50, 60]})
    report = make_report(frame, sales_name="=evil.csv")
    data = xr.excel_bytes(report)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheets = [archive.read(n).decode("utf-8") for n in archive.namelist() if n.startswith("xl/worksheets/")]
    assert not any("<f>" in sheet or "<f " in sheet for sheet in sheets)          # no formula anywhere
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    found = 0
    for ws in workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith(xr.FORMULA_PREFIXES):
                    assert cell.data_type == "s" and cell.quotePrefix, (ws.title, cell.coordinate, cell.value)
                    found += 1
    assert found >= 10                      # product and category names, file name, ...


def test_clean_value_handles_missing_and_illegal_values():
    assert xr.clean_value(float("nan")) is None and xr.clean_value(pd.NaT) is None and xr.clean_value(None) is None
    assert xr.clean_value("a\x00b\x07c") == "abc"
    assert xr.clean_value(pd.Timestamp("2025-01-02", tz="UTC")).tzinfo is None
    assert xr.money_format("GBP") == '"GBP "#,##0.00;-"GBP "#,##0.00'


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def test_pdf_contains_branding_period_filters_and_figures(demo):
    text = pdf_text_content(demo)
    for expected in ("Business performance report", "Reporting period: 01 Jan 2025 ", "Executive summary".upper(),
                     "Product: All", "Completed orders only", "Generated", "461,120.95", "46,010.52",
                     "PRIORITY ACTIONS", "Next step:", "ANOMALIES", "INSIGHTS", "Page 1 of"):
        assert expected in text, expected
    assert text.count("/Type /Page\n") + text.count("/Type /Page ") >= 5 or "/Count" in text


def test_pdf_text_replaces_unsupported_characters():
    assert pr.pdf_text("−12 → 13 ≥ 5") == "-12 -> 13 >= 5"
    assert pr.pdf_text("Café £5 €6") == "Café £5 €6"
    assert pr.pdf_text("东京") == "??"


def test_nice_ticks_and_compact_labels():
    assert pr.nice_ticks(0, 38_000) == [0, 10_000, 20_000, 30_000, 40_000]
    assert pr.nice_ticks(0, 0) == [0, 0.25, 0.5, 0.75, 1.0]
    assert pr.compact(461_120.95) == "461.1k" and pr.compact(-5_000) == "-5k" and pr.compact(0) == "0"


# ---------------------------------------------------------------------------
# Missing data and empty results
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["no_match", "sales_only", "expenses_only", "no_dates", "no_dimensions"])
def test_exports_handle_missing_data(case):
    _, sales_frame = sample_frame("sales_clean")
    _, expense_frame = sample_frame("expenses_clean")
    if case == "no_match":
        report = make_report(sales_frame, expense_frame, filters=bm.Filters(products=("No such product",)))
        assert report.result.sales_rows.empty and not report.kpi("revenue").value
    elif case == "sales_only":
        report = make_report(sales_frame)
        assert not report.kpi("expenses").available and len(report.records) == 1
    elif case == "expenses_only":
        report = make_report(None, expense_frame)
        assert report.sales is None and not report.dimensions
    elif case == "no_dates":
        report = make_report(sales_frame.drop(columns=["order_date"]))
        assert report.window is None and report.trend.empty
    else:
        report = make_report(sales_frame.drop(columns=["product_name", "category", "region"]))
        assert not report.dimensions and not report.breakdowns
    workbook = openpyxl.load_workbook(io.BytesIO(xr.excel_bytes(report)))
    assert workbook.sheetnames[0] == "Summary"
    assert pr.pdf_bytes(report).startswith(b"%PDF")


def test_error_files_are_valid():
    assert pr.error_pdf("Something went wrong").startswith(b"%PDF")
    workbook = openpyxl.load_workbook(io.BytesIO(xr.error_workbook("=1+1 went wrong")))
    assert workbook.active["A5"].data_type == "s"


# ---------------------------------------------------------------------------
# Reports page
# ---------------------------------------------------------------------------
def open_reports(main: str | None = None, companion: str | None = None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=180)
    if main:
        name, content = read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
    if companion:
        name, content = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": content, "source": "sample",
                                                          "sheet": None}
    app.run()
    assert not app.exception, app.exception
    return app


def test_reports_page_empty_state_and_demo():
    app = open_reports()
    assert any("No data loaded yet" in block.value for block in app.markdown)
    app.button(key="load_demo").click().run()
    assert not app.exception, app.exception
    assert any("461,120.95" in block.value for block in app.markdown)


def test_reports_page_offers_both_downloads_with_filters():
    app = open_reports("sales_clean", "expenses_clean")
    buttons = app.get("download_button")
    assert len(buttons) == 2
    assert any("Reporting period" in caption.value for caption in app.caption)
    app.multiselect(key="rep_region").set_value(["North"]).run()
    assert not app.exception, app.exception
    summary = next(block.value for block in app.markdown if block.value.startswith("Revenue was"))
    assert "461,120.95" not in summary                       # the filter changes the report's figures
    assert "Profit is not shown" in summary                  # as on the Dashboard with a region filter


# ---------------------------------------------------------------------------
# Dashboard PDF (Stage 10): KPIs, trends and breakdowns of the Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_pdf_has_the_dashboard_sections_and_figures(demo):
    from reportlab import rl_config
    previous = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        data = pr.dashboard_pdf_bytes(demo)
    finally:
        rl_config.pageCompression = previous
    assert data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF")
    text = data.decode("latin-1")
    pages = text.count("/Type /Page\n") + text.count("/Type /Page ") + text.count("/Type /Page>")
    assert 1 <= pages <= 4                                     # an overview, not the full report
    assert "Business dashboard" in text and "Key figures" in text
    assert "Performance per" in text and "Where the money comes from" in text
    assert "Business insights" not in text and "Unusual months" not in text
    revenue = demo.format_kpi(demo.kpi("revenue"))
    assert revenue in text                                     # the same figure as on the Dashboard
    assert demo.title != "Business dashboard"                  # the full report keeps its own title


def test_dashboard_page_offers_the_pdf_download():
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "views" / "dashboard.py"),
                            default_timeout=120)
    for key, target in (("sales_clean", "main"), ("expenses_clean", "companion")):
        name, content = read_sample(key)
        if target == "main":
            app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
            app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
        else:
            app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": content, "source": "sample",
                                                              "sheet": None}
    app.run()
    assert not app.exception, app.exception
    buttons = [element for element in app.get("download_button")]
    assert len(buttons) == 1 and "Download PDF" in buttons[0].proto.label
