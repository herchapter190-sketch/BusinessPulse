"""Business Dashboard screen.

Shows KPIs and charts for the main dataset (cleaned version when approved) and,
optionally, a second file (for example expenses next to sales). The two files
are never merged row by row; they are only compared as totals over the dates
both cover. Choosing the data (version, second file, filters) is shared with the
other Analysis pages in ui/analysis_context.py; all calculations live in
tools/business_metrics.py.
"""

import html
import logging

import pandas as pd
import streamlit as st

from config import design
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import charts, pdf_report
from tools import report_data as rd
from ui import analysis_context as ctx
from ui import components as ui
from ui.state import load_demo

LOGGER = logging.getLogger(__name__)
FREQUENCY_OPTIONS = {"auto": "Automatic", "D": "Day", "W": "Week", "M": "Month", "Q": "Quarter"}
# The Dashboard is the quick overview: one revenue mix (the first of these the data has)
# and the expense mix. Products, categories and regions in depth are on Performance.
OVERVIEW_DIMENSIONS = ("category", "product", "region")
FINANCIAL_KPIS = ("revenue", "expenses", "profit", "margin")
ACTIVITY_KPIS = ("orders", "units", "aov", "returns")
# The one headline figure: the bottom line when it can be calculated, otherwise the first available total.
LEAD_PREFERENCE = ("profit", "revenue", "expenses")


# ---------------------------------------------------------------------------
def render_empty_state() -> None:
    ui.page_header("Business dashboard", "Revenue, expenses, profit and trends at a glance.", eyebrow="Analysis")
    actions = ui.empty_state("dashboard", ":material/monitoring:", "No data loaded yet",
                             "Upload a sales or expense file first, or try the demo with fictional sample data.")
    left, right = actions.columns(2)
    if left.button("Go to Upload data", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")
    right.button("Load demo data", type="primary", icon=":material/play_arrow:", width="stretch",
                 key="load_demo", on_click=load_demo,
                 help="Loads the fictional sample sales and expense files.")


def file_name_html(name: str) -> str:
    """A file name that may wrap only after '_', '-' or '.', never in the middle of a word."""
    escaped = html.escape(name, quote=False)
    for mark in ("_", "-", "."):
        escaped = escaped.replace(mark, mark + "<wbr>")
    return f'<p class="bp-source-file">{escaped}</p>'


def render_sources(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, currency_text: str):
    with st.container(key="bp_dash_sources"):
        columns = st.columns(3)
        for column, key, title, dataset in ((columns[0], "sales", "Sales data", sales),
                                            (columns[1], "expenses", "Expense data", expenses)):
            with column, ui.card(f"source_{key}"):
                st.markdown(f'<p class="bp-card-title">{title}</p>', unsafe_allow_html=True)
                if dataset is None:
                    st.caption("Not available")
                else:
                    kind = "success" if dataset.version == "Cleaned dataset" else "neutral"
                    st.markdown(file_name_html(dataset.name), unsafe_allow_html=True)
                    st.markdown(ui.status_badge(kind, dataset.version, " "))
                    st.caption(f"{len(dataset.rows):,} rows used of {dataset.source_rows:,} · "
                               f"{bm.coverage_text(dataset)}")
        with columns[2], ui.card("source_currency"):
            st.markdown('<p class="bp-card-title">Currency</p>', unsafe_allow_html=True)
            st.markdown(currency_text)


def kpi_tile(result: bm.DashboardResult, key: str, lead: bool = False) -> None:
    kpi = result.kpis[key]
    help_text = kpi.definition + ("\n\n" + kpi.calculation if kpi.available else "")
    status = None
    if key == "profit" and kpi.available:
        status = ("success", "Profit") if kpi.value >= 0 else ("error", "Loss")
    footnote = kpi.note if kpi.available else f":material/info: {kpi.note}" if kpi.note else ""
    ui.kpi_card(key, kpi.label, bm.format_kpi(kpi, result.currency), help=help_text, footnote=footnote,
                status=status, lead=lead)


def render_kpis(result: bm.DashboardResult, currency_text: str, after_financial=None) -> None:
    """Financial results first (one headline figure, larger and first), then the
    sales-activity counts as a quieter strip. Same KPIs and values as before."""
    available = [key for key in FINANCIAL_KPIS if key in result.kpis]
    lead = next((key for key in LEAD_PREFERENCE if key in available and result.kpis[key].available), None)
    supporting = [key for key in available if key != lead]
    activity = [key for key in ACTIVITY_KPIS if key in result.kpis]

    if currency_text == "Not specified":
        currency_line = "Currency not stated in the data."
    elif currency_text.startswith("Mixed"):
        currency_line = f"Mixed currencies ({currency_text.split(': ', 1)[-1]}): check before comparing totals."
    else:
        currency_line = f"Amounts in {currency_text}."
    ui.section_header("Financial results", currency_line)
    with st.container(key="bp_dash_kpis"):
        widths = ([1.4] if lead else []) + [1] * len(supporting)
        if widths:
            tiles = st.columns(widths, gap="small")
            for tile, key in zip(tiles, ([lead] if lead else []) + supporting):
                with tile:
                    kpi_tile(result, key, lead=key == lead)
    if after_financial:
        after_financial()
    if activity:
        st.markdown('<p class="bp-dash-label">Sales activity</p>', unsafe_allow_html=True)
        with st.container(key="bp_dash_activity"):
            for tile, key in zip(st.columns(len(activity), gap="small"), activity):
                with tile:
                    kpi_tile(result, key)


def money_table(table: pd.DataFrame, currency: str | None, money_columns: list[str]) -> pd.DataFrame:
    shown = table.copy()
    for column in money_columns:
        if column in shown:
            shown[column] = shown[column].map(lambda value: bm.format_money(value, currency))
    if "Share" in shown:
        shown["Share"] = shown["Share"].map(bm.format_percent)
    if "Units" in shown:
        shown["Units"] = shown["Units"].map(bm.format_count)
    return shown


def chart_with_table(figure, table: pd.DataFrame, key: str, title: str, caption: str = "") -> None:
    """One chart in a card: title, optional caption, the chart and its table view."""
    with ui.chart_card(key):
        st.markdown(f'<p class="bp-card-title">{title}</p>', unsafe_allow_html=True)
        if caption:
            st.caption(caption)
        st.plotly_chart(figure, width="stretch", theme=None, key=key,
                        config={"displayModeBar": False, "responsive": True})
        with st.expander("Show data table", icon=":material/table:"):
            st.dataframe(table, hide_index=True, width="stretch")


def render_charts(result, sales, expenses, filters, frequency: str) -> None:
    currency = result.currency
    table = bm.time_table(result, sales, expenses, filters, frequency)
    unit = charts.period_label(frequency).lower()

    ui.section_header("Trends")
    figure = charts.time_chart(table, frequency, currency)
    if figure is None:
        st.info("**No trend chart.** A trend chart needs dates, and no usable date column was found in the data.",
                icon=":material/info:")
    else:
        series = [name for name in ("Revenue", "Expenses") if name in table]
        names = " and ".join(name.lower() for name in series)
        span = [d for d in (filters.start, filters.end)]
        window = bm.default_range(sales, expenses, QualityRules().today())
        note = bm.partial_period_note(table, frequency, span[0] or window[0], span[1] or window[1])
        caption = f":material/info: {note}" if note else ""
        display = table.copy()
        display["Period"] = display["Period"].dt.strftime("%Y-%m-%d")
        chart_with_table(figure, money_table(display, currency, ["Revenue", "Expenses", "Profit"]), "chart_time",
                         f"{names.capitalize()} per {unit}", caption)

    profit_figure = charts.profit_chart(table, frequency, currency)
    if profit_figure is not None:
        label = {"net": "Net profit", "gross": "Gross profit"}.get(result.profit_type, "Profit")
        caption = ""
        if result.profit_type == "net" and result.profit_period:
            caption = (f"Dates covered by both files: {bm.format_date(result.profit_period[0])} – "
                       f"{bm.format_date(result.profit_period[1])}.")
        profit_table = table.dropna(subset=["Profit"])[["Period", "Profit"]].copy()
        profit_table["Period"] = profit_table["Period"].dt.strftime("%Y-%m-%d")
        chart_with_table(profit_figure, money_table(profit_table, currency, ["Profit"]), "chart_profit",
                         f"{label} per {unit}", caption)
    elif result.kpis["profit"].note and (sales is not None):
        st.caption(f":material/info: Profit over time is not shown: {result.kpis['profit'].note}")

    ui.section_header("Revenue and expense mix")
    breakdowns = []
    revenue_color, expense_color = design.REVENUE_COLOR, design.EXPENSES_COLOR
    if sales is not None:
        by = next((by for by in OVERVIEW_DIMENSIONS if sales.has(by)), None)
        if by is not None:
            breakdowns.append((f"Revenue by {by}", bm.breakdown(result.revenue_rows, by), "Revenue", revenue_color))
    if expenses is not None and expenses.has("category"):
        breakdowns.append(("Expenses by category", bm.breakdown(result.expense_rows, "category"), "Spent",
                           expense_color))
    breakdowns = [item for item in breakdowns if not item[1].empty]
    if not breakdowns:
        st.info("**No breakdowns.** No product, category or region columns were recognised in the data.",
                icon=":material/info:")
        return
    with st.container(key="bp_dash_breakdowns"):
        for index in range(0, len(breakdowns), 2):
            columns = st.columns(2, gap="medium")
            for column, (title, data, value_label, color) in zip(columns, breakdowns[index:index + 2]):
                with column:
                    figure = charts.ranked_bar_chart(data, value_label, currency, color)
                    shown = money_table(data.rename(columns={"Amount": value_label}), currency, [value_label])
                    key = "chart_" + "".join(ch if ch.isalnum() else "_" for ch in title.lower())
                    chart_with_table(figure, shown, key, title)
    if sales is not None and any(sales.has(by) for by in OVERVIEW_DIMENSIONS):
        if st.button("Products, categories and regions in depth", icon=":material/leaderboard:", type="tertiary",
                     key="go_performance"):
            st.switch_page("views/performance.py")


def dashboard_pdf(**report_inputs):
    """Called only when the download button is clicked: builds the report snapshot
    for the current data, period and filters, and writes the Dashboard PDF. A
    failure gives a one-page PDF that says why."""
    def build() -> bytes:
        try:
            return pdf_report.dashboard_pdf_bytes(rd.build_report(**report_inputs))
        except Exception as error:  # noqa: BLE001 - shown to the user inside the file
            LOGGER.exception("Dashboard PDF failed")
            return pdf_report.error_pdf(f"{type(error).__name__}: {error}")
    return build


# ---------------------------------------------------------------------------
loaded, signature = ctx.loaded_file()
if loaded is None:
    render_empty_state()
    st.stop()

header_actions = ui.page_header("Business dashboard", "Revenue, expenses, profit and trends at a glance.",
                                eyebrow="Analysis", actions=1.6)
with header_actions:
    pdf_column, settings_column = st.columns([1.15, 1], gap="small")
pdf_slot = pdf_column.empty()   # filled once the figures are known (the PDF follows the filters)
with settings_column.popover("Settings", icon=":material/tune:", width="stretch"):
    frequency_choice = st.selectbox("Group dates by", list(FREQUENCY_OPTIONS), format_func=FREQUENCY_OPTIONS.get,
                                    key="dash_frequency")
    include_pending, include_errors = ctx.counting_settings()

data = ctx.select_data(loaded, signature, include_errors)
sales, expenses = data.sales, data.expenses

if not data.has_amounts:
    roles = ", ".join(sorted(data.main.mapping.matches)) or "none"
    st.warning(f"**BusinessPulse couldn't find sales or expense amounts in {loaded.file_name}.** A dashboard needs "
               "a column with sales/revenue amounts (or quantity and unit price), or a column with expense "
               f"amounts. Recognised fields: {roles}. Check **Column recognition** on the Data quality page if a "
               "column was missed.", icon=":material/warning:")
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)
    st.stop()

# Future dates (after today, as on the Data quality page) are outside the default
# period, so one mistyped year can't stretch the charts; the user can still pick them.
today = QualityRules().today()
full_range = bm.available_range(sales, expenses)
start_range = bm.default_range(sales, expenses, today)
with st.container(key="bp_dash_filters"):   # Dashboard-only wrapper: lets the filter row wrap on narrow screens
    filters = ctx.render_filters(sales, full_range, start_range)
future_count, latest_future = bm.future_dated(sales, expenses, today)

result = bm.compute_dashboard(sales, expenses, filters, include_pending)
if full_range:
    shown_start, shown_end = filters.start or start_range[0], filters.end or start_range[1]
    st.caption(f"**{bm.format_date(shown_start)} – {bm.format_date(shown_end)}** · data from "
               f"{bm.format_date(full_range[0])} to {bm.format_date(full_range[1])}"
               + (f" · filtered by {', '.join(bm.DIMENSION_LABELS[r].lower() for r, v in filters.dimension_values.items() if v)}"
                  if filters.has_dimension_filters else ""))

future_note = bm.future_rows_note(future_count, latest_future, start_range[1], filters.end) if full_range else None
window = (filters.start or start_range[0], filters.end or start_range[1]) if full_range else None
frequency = frequency_choice if frequency_choice != "auto" else (bm.choose_frequency(*window) if window else "M")

# The PDF uses exactly the data, period, filters and counting options shown here.
main_frame = ctx.main_versions(loaded, signature)[data.version]
companion_frame = ctx.load_companion()[0]
frame_of = {id(data.main): main_frame}
pdf_notes = [("warning", future_note)] if future_note else []
if data.companion_error:
    pdf_notes.append(("warning", data.companion_error))
pdf_inputs = dict(
    file_name=loaded.file_name, version=data.version, companion_name=data.companion_name, sales=sales,
    expenses=expenses, sales_frame=frame_of.get(id(sales), companion_frame) if sales is not None else None,
    expense_frame=frame_of.get(id(expenses), companion_frame) if expenses is not None else None,
    filters=filters, window=window, full_range=full_range, frequency=frequency, include_pending=include_pending,
    include_errors=include_errors, today=today, extra_notes=pdf_notes)
pdf_name = (f"BusinessPulse_dashboard_{window[0]:%Y-%m-%d}_to_{window[1]:%Y-%m-%d}.pdf" if window
            else "BusinessPulse_dashboard.pdf")
pdf_slot.download_button("Download PDF", dashboard_pdf(**pdf_inputs), file_name=pdf_name, mime="application/pdf",
                         icon=":material/picture_as_pdf:", key="download_dashboard_pdf", width="stretch",
                         on_click="ignore", help="The KPIs, trends and breakdowns shown here, for the selected "
                                                 "period and filters. Created on your computer when you click.")
ui.notes_panel([("warning", w) for dataset in (sales, expenses) if dataset for w in dataset.warnings]
               + ([("warning", future_note)] if future_note else [])
               + [("info", note) for note in result.notes])

if sales is not None and result.sales_rows.empty and (filters.has_dimension_filters or filters.start):
    st.info("**No sales match the selected filters.** Change the filters or click **Reset filters**.",
            icon=":material/filter_alt_off:")

# The second-file action appears once: right under the financial results while one
# side (usually expenses) is missing, so profit is one step away; otherwise at the bottom.
def show_companion_controls() -> None:
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)


second_file_missing = data.companion_name is None and (sales is None or expenses is None)
render_kpis(result, data.currency_text, show_companion_controls if second_file_missing else None)
render_charts(result, sales, expenses, filters, frequency)

# Where the figures come from, out of the way: files used, a second file, every calculation.
if not second_file_missing:
    show_companion_controls()
notes = [n for dataset in (sales, expenses) if dataset for n in dataset.notes] + \
    [dataset.currency_note for dataset in (sales, expenses) if dataset]
with st.expander("How are these figures calculated?", icon=":material/calculate:"):
    render_sources(sales, expenses, data.currency_text)
    st.dataframe(bm.kpi_table(result), hide_index=True, width="stretch",
                 column_config={"How it was calculated": st.column_config.TextColumn(width="large"),
                                "What it means": st.column_config.TextColumn(width="medium")})
    for note in dict.fromkeys(notes):
        st.markdown(f"- {note}")
