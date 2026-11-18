"""Performance screen: sales over time, products, categories and regions, with
top and bottom performers.

Layout only. The data (version, second file, filters, counting options) comes from
ui/analysis_context.py, shared with the Dashboard and Comparisons; every figure
comes from tools/business_metrics.py (compute_dashboard) and tools/performance.py.
"""

import pandas as pd
import streamlit as st

from config import design
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import charts
from tools import performance as perf
from ui import analysis_context as ctx
from ui import components as ui
from ui.state import load_demo

PREFIX = "perf"
FREQUENCY_OPTIONS = {"auto": "Automatic", "D": "Day", "W": "Week", "M": "Month", "Q": "Quarter"}
SECTION_TITLES = {"product": "Products", "category": "Categories", "region": "Regions"}
# The Dashboard has the overall totals; this page goes deeper: what leads, how it changes, what lags.
DESCRIPTION = "What sells best, how sales change over time, and what lags behind."


# ---------------------------------------------------------------------------
def render_empty_state() -> None:
    ui.page_header("Performance", "Sales over time and by product, category and region.", eyebrow="Analysis")
    actions = ui.empty_state("performance", ":material/leaderboard:", "No data loaded yet",
                             "Upload a sales file first, or try the demo with fictional sample data.")
    left, right = actions.columns(2)
    if left.button("Go to Upload data", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")
    right.button("Load demo data", type="primary", icon=":material/play_arrow:", width="stretch",
                 key="load_demo", on_click=load_demo, help="Loads the fictional sample sales and expense files.")


def render_leaders(result: bm.DashboardResult, dimensions: list[str]) -> None:
    """The best-selling product, category and region of the selection, with their
    share of revenue (from the same tables as the sections below)."""
    leaders = []
    for by in dimensions:
        table = perf.dimension_table(result, by)
        if not table.empty:
            leaders.append((by, table.iloc[0], len(table)))
    if not leaders:
        return
    ui.section_header("Leaders", "Highest revenue in the selected period.")
    with st.container(key="bp_perf_kpis"):
        for tile, (by, top, count) in zip(st.columns(len(leaders), gap="small"), leaders):
            with tile:
                share = bm.format_percent(top["Share"]) if pd.notna(top["Share"]) else "—"
                ui.kpi_card(f"perf_top_{by}", f"Top {by}", str(top["Name"]),
                            footnote=f"{bm.format_money(top['Revenue'], result.currency)} · {share} of revenue",
                            help=f"The {by} with the most counted revenue, out of {count:,} "
                                 f"{perf.DIMENSION_PLURALS[by]}.")


def render_trend(result, sales, filters, frequency, window, orders_label) -> None:
    unit = charts.period_label(frequency).lower()
    ui.section_header("Sales over time")
    table = perf.trend_table(result, sales, filters, frequency)
    if table.empty:
        st.info("A trend needs dates. No usable date column was found in the sales data.", icon=":material/info:")
        return
    options = perf.ranking_metrics(table)
    labels = {column: orders_label if column == "Orders" else column for column in options}
    note = bm.partial_period_note(table, frequency, *window)
    display = table.copy()
    display["Period"] = display["Period"].dt.strftime("%Y-%m-%d")
    with ui.chart_card("perf_trend"):
        with st.container(key="bp_perf_trend_head"):
            title, choice = st.columns([2, 1], vertical_alignment="bottom")
            metric = choice.selectbox("Show", options, format_func=labels.get, key="perf_trend_metric",
                                      help="\n\n".join(f"**{labels[m]}**: {perf.METRICS[m][1]}" for m in options))
            title.markdown(f'<p class="bp-card-title">{labels[metric]} per {unit}</p>', unsafe_allow_html=True)
        kind = perf.METRICS[metric][0]
        figure = charts.trend_chart(table, metric, kind, result.currency, frequency, design.REVENUE_COLOR)
        if note:
            st.caption(f":material/info: {note}")
        st.plotly_chart(figure, width="stretch", theme=None, key="chart_perf_trend",
                        config={"displayModeBar": False, "responsive": True})
        with st.expander("Show data table", icon=":material/table:"):
            st.dataframe(perf.format_table(display, result.currency, orders_label), hide_index=True,
                         width="stretch")


def render_ranking(by: str, table, currency, orders_label) -> None:
    """Top and bottom performers in one card. Which list is which is said with an
    arrow and the words 'highest'/'lowest', never by colour alone."""
    plural = perf.DIMENSION_PLURALS[by]
    metrics = perf.ranking_metrics(table)
    labels = {column: orders_label if column == "Orders" else column for column in metrics}
    with ui.card(f"perf_{by}_ranking"):
        with st.container(key=f"bp_perf_rankhead_{by}"):
            title, choice = st.columns([2, 1], vertical_alignment="bottom")
            metric = choice.selectbox("Rank by", metrics, format_func=labels.get, key=f"perf_rank_{by}")
            title.markdown(f'<p class="bp-card-title">Top and bottom {plural}</p>', unsafe_allow_html=True)
        top, bottom, note = perf.top_and_bottom(table, metric)
        if top.empty:
            st.caption(f":material/info: Top and bottom {plural} need at least two {plural} with a "
                       f"{labels[metric].lower()} figure.")
            return
        shown = ["Name", metric] + (["Revenue"] if metric != "Revenue" else [])
        lists = ((f":material/arrow_upward: **Top {len(top)}** · highest {labels[metric].lower()}", top),
                 (f":material/arrow_downward: **Bottom {len(bottom)}** · lowest {labels[metric].lower()}", bottom))
        with st.container(key=f"bp_perf_rank_{by}"):
            for column, (heading, part) in zip(st.columns(2, gap="medium"), lists):
                with column:
                    st.markdown(heading)
                    st.dataframe(perf.format_table(part[shown], currency, orders_label), hide_index=True,
                                 width="stretch")
        if note:
            st.caption(f":material/info: {note}")


def render_dimension(by: str, result: bm.DashboardResult, attribution: tuple[bool, str], orders_label) -> None:
    title, plural = SECTION_TITLES[by], perf.DIMENSION_PLURALS[by]
    table = perf.dimension_table(result, by)
    ui.section_header(title)
    if table.empty:
        st.info(f"No counted sales for any {by} in the selected period and filters.", icon=":material/info:")
        return
    if not attribution[0]:
        st.caption(f":material/info: {attribution[1]}")
    currency = result.currency
    chart_data = bm.breakdown(result.revenue_rows, by)
    figure = charts.ranked_bar_chart(chart_data, "Revenue", currency, design.REVENUE_COLOR)
    with ui.chart_card(f"perf_{by}"):
        st.markdown(f'<p class="bp-card-title">Revenue by {by}</p>', unsafe_allow_html=True)
        st.plotly_chart(figure, width="stretch", theme=None, key=f"chart_perf_{by}",
                        config={"displayModeBar": False, "responsive": True})
        with st.expander(f"Table of all {plural} ({len(table):,})", icon=":material/table:"):
            st.dataframe(perf.format_table(table, currency, orders_label), hide_index=True, width="stretch")
    if len(table) > 1:
        render_ranking(by, table, currency, orders_label)


def render_definitions(orders_label: str, attribution: tuple[bool, str]) -> None:
    with st.expander("What do these figures mean?", icon=":material/calculate:"):
        rows = [f"| {orders_label if name == 'Orders' else name} | {definition} |"
                for name, (_, definition) in perf.METRICS.items()
                if attribution[0] or not name.startswith("Gross")]
        st.markdown("| Figure | Meaning |\n|---|---|\n" + "\n".join(rows))
        st.markdown(f"**Top and bottom performers.** Items are ranked by the chosen figure; up to "
                    f"{perf.PERFORMERS} are listed in each group and an item never appears in both. Items without "
                    "that figure are not ranked. Items with no counted sales in the period don't appear at all.")
        st.markdown(f"**Profit by item.** {attribution[1]}")


# ---------------------------------------------------------------------------
loaded, signature = ctx.loaded_file()
if loaded is None:
    render_empty_state()
    st.stop()

header_actions = ui.page_header("Performance", DESCRIPTION, eyebrow="Analysis", actions=1)
with header_actions.popover("Settings", icon=":material/tune:", width="stretch"):
    frequency_choice = st.selectbox("Group dates by", list(FREQUENCY_OPTIONS), format_func=FREQUENCY_OPTIONS.get,
                                    key="perf_frequency")
    include_pending, include_errors = ctx.counting_settings(PREFIX)

data = ctx.select_data(loaded, signature, include_errors, PREFIX)
sales, expenses = data.sales, data.expenses
if not data.has_amounts:
    roles = ", ".join(sorted(data.main.mapping.matches)) or "none"
    st.warning(f"**BusinessPulse couldn't find sales or expense amounts in {loaded.file_name}.** Performance needs "
               "a column with sales/revenue amounts (or quantity and unit price). "
               f"Recognised fields: {roles}. Check **Column recognition** on the Data quality page if a column was "
               "missed.", icon=":material/warning:")
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)
    st.stop()

ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)

if sales is None or "amount" not in sales.rows.columns:
    ui.empty_state("performance_no_sales", ":material/point_of_sale:", "Performance needs sales data",
                   "This file only has expenses. Add a sales file as the second file above (or load a sales file "
                   "on the Upload page) to see sales, products, categories and regions.")
    st.stop()

today = QualityRules().today()
full_range = bm.available_range(sales, expenses)
start_range = bm.default_range(sales, expenses, today)
with st.container(key="bp_perf_filters"):   # Performance-only wrapper: lets the filter row wrap on narrow screens
    filters = ctx.render_filters(sales, full_range, start_range, PREFIX)
result = bm.compute_dashboard(sales, expenses, filters, include_pending)
attribution = perf.profit_attribution(result, expenses)
orders_label = result.kpis["orders"].label

if full_range:
    shown_start, shown_end = filters.start or start_range[0], filters.end or start_range[1]
    st.caption(f"**{bm.format_date(shown_start)} – {bm.format_date(shown_end)}** · data from "
               f"{bm.format_date(full_range[0])} to {bm.format_date(full_range[1])}")

ui.notes_panel([("warning", w) for dataset in (sales, expenses) if dataset for w in dataset.warnings]
               + [("info", note) for note in result.notes])

if result.revenue_rows.empty:
    ui.empty_state("performance_no_match", ":material/filter_alt_off:", "No counted sales for these filters",
                   "Change the date range or filters, or click Reset filters.")
    st.stop()

dimensions = [by for by in bm.DIMENSIONS if sales.has(by)]
render_leaders(result, dimensions)

window = (filters.start or start_range[0], filters.end or start_range[1]) if full_range else None
if window:
    frequency = frequency_choice if frequency_choice != "auto" else bm.choose_frequency(*window)
    render_trend(result, sales, filters, frequency, window, orders_label)
else:
    st.info("Sales over time needs dates. No usable date column was found in the sales data.", icon=":material/info:")

if not dimensions:
    ui.empty_state("performance_no_dimensions", ":material/category:", "No products, categories or regions",
                   "No product, category or region column was recognised in the sales data. Check Column "
                   "recognition on the Data quality page if a column was missed.")
for by in dimensions:
    render_dimension(by, result, attribution, orders_label)

render_definitions(orders_label, attribution)
