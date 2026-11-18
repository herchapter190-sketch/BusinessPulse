"""Comparisons screen: month-over-month, quarter-over-quarter and year-over-year.

Layout only. The data (version, second file, filters, counting options) comes from
ui/analysis_context.py, shared with the Dashboard; every figure and every decision
about whether a change can be calculated comes from tools/period_comparison.py.
"""

import pandas as pd
import streamlit as st

from config import design
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import charts
from tools import period_comparison as pc
from ui import analysis_context as ctx
from ui import components as ui
from ui.state import load_demo

PREFIX = "cmp"
FREQUENCY_LABELS = {"M": "Month", "Q": "Quarter", "Y": "Year"}
KPI_ROWS = (("revenue", "expenses", "profit", "margin"),
            ("orders", "units", "aov", "revenue_per_day"),
            ("returns", "return_rate", "cancellation_rate"))
# Secondary rows: a quiet strip with a small label instead of cards (as on the Dashboard).
ROW_LABELS = (None, "Sales activity", "Returns and cancellations")
ROW_KEYS = ("bp_cmp_kpis", "bp_cmp_activity", "bp_cmp_rates")
LEAD_PREFERENCE = ("profit", "revenue", "expenses")   # same headline order as the Dashboard
SALES_ONLY = {"orders", "units", "aov", "revenue_per_day", "returns", "return_rate", "cancellation_rate"}
STATUS_BADGES = {   # coverage status -> (badge kind, icon); never colour alone
    pc.COMPLETE: ("success", None),
    pc.PARTIAL: ("info", None),
    pc.NOT_FINISHED: ("info", ":material/schedule:"),
    pc.POSSIBLE_GAP: ("warning", None),
    pc.NO_RECORDS: ("warning", None),
    pc.OUTSIDE: ("neutral", ":material/block:"),
}
MONEY_BARS = (("revenue", design.REVENUE_COLOR), ("expenses", design.EXPENSES_COLOR),
              ("profit", design.PROFIT_COLOR))
CHART_METRICS = {   # key -> colour (None = profit/loss colours)
    "revenue": design.REVENUE_COLOR, "expenses": design.EXPENSES_COLOR, "profit": None,
    "margin": design.PROFIT_COLOR, "orders": design.REVENUE_COLOR, "units": design.REVENUE_COLOR,
    "aov": design.REVENUE_COLOR, "revenue_per_day": design.REVENUE_COLOR,
}
HOW_IT_WORKS = """
**Which periods are compared.** Choose a month, quarter or year, and compare it with the previous one
(month-over-month, quarter-over-quarter, year-over-year) or with the same period a year earlier. Whole calendar
periods are always compared; the date range at the top decides which periods you can choose.

**Every figure is calculated exactly as on the Dashboard**, for the dates of each period, with the same data
version, filters and counting options.

| Figure | Formula |
|---|---|
| Change | this period − comparison period |
| % change | change ÷ comparison period × 100 (only when the comparison value is above 0) |
| Margin and rates | change in percentage points (pp): 12.5% → 15.0% is +2.5 pp |
| Revenue per day | revenue ÷ days of the period covered by the data |
| Return rate | returned orders ÷ (completed + returned orders) × 100 |
| Cancellation rate | cancelled orders ÷ orders with a recognised status × 100 |
| Growth gap | expense % change − revenue % change, in percentage points |

**A change is only calculated between two complete periods.**

| Period status | Meaning |
|---|---|
| Complete | The data covers every day of the period, the period has ended, and there is no unusually long run of days without records. |
| Partial period | The data starts or ends part-way through the period. |
| Not finished | The period ends after today. |
| Possible missing data | An unusually long run of days without any records (at least 14 days, and much longer than usual for this file). |
| No records | No records at all, although the file has data before and after. BusinessPulse can't tell "no sales" from "missing data". |
| No data | The file does not reach this period. Shown as "—", never as 0. |

Figures for incomplete periods are still shown, but without a change. **Compare to date** compares the days
covered so far with the same days of the comparison period (for example 1–17 Nov with 1–17 Oct).

Whether a period is complete is judged on the whole file, before product, category and region filters: a
product that sold nothing in a complete month really sold nothing.
"""


# ---------------------------------------------------------------------------
def render_empty_state() -> None:
    ui.page_header("Comparisons", "Compare a month, quarter or year with the one before or with the same "
                   "period last year.", eyebrow="Analysis")
    actions = ui.empty_state("comparisons", ":material/compare_arrows:", "No data loaded yet",
                             "Upload a sales or expense file first, or try the demo with fictional sample data.")
    left, right = actions.columns(2)
    if left.button("Go to Upload data", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")
    right.button("Load demo data", type="primary", icon=":material/play_arrow:", width="stretch",
                 key="load_demo", on_click=load_demo,
                 help="Loads the fictional sample sales and expense files.")


def status_badge(cover: pc.Coverage, show_dataset: bool) -> str:
    kind, icon = STATUS_BADGES[cover.status]
    text = f"{cover.dataset.capitalize()}: {cover.label.lower()}" if show_dataset else cover.label
    return ui.status_badge(kind, text, icon)


def render_period_card(key: str, heading: str, figures: pc.PeriodFigures) -> None:
    with ui.card(key):
        st.markdown(f'<p class="bp-card-title">{heading}</p>', unsafe_allow_html=True)
        st.markdown(f"### {figures.span.label}")
        days = f"{bm.format_date(figures.span.start)} – {bm.format_date(figures.span.end)} · {figures.span.days} days"
        st.caption(days)
        many = len(figures.coverage) > 1
        st.markdown(" ".join(status_badge(c, many) for c in figures.coverage.values()))
        for cover in figures.coverage.values():
            if not cover.complete:
                st.caption(cover.reason)


def control_bar(sales, expenses, window, today):
    """Period size, period, comparison and to-date choice. Returns (option, mode, to_date) or None."""
    with ui.card("cmp_controls"):
        st.markdown('<p class="bp-card-title">What to compare</p>', unsafe_allow_html=True)
        # Two rows of two; on narrow screens each control takes a full row (CSS).
        first, second = st.columns(2, vertical_alignment="bottom"), st.columns(2, vertical_alignment="bottom")
        slots = [first[0], first[1], second[0], second[1]]
        frequency = slots[0].segmented_control("Compare by", list(FREQUENCY_LABELS), format_func=FREQUENCY_LABELS.get,
                                               default="M", key="cmp_frequency") or "M"
        options = pc.available_periods(sales, expenses, frequency, window, today)
        if not options:
            return frequency, None, None, False
        by_label = {o.period.label: o for o in options}
        labels = list(reversed(by_label))           # latest first
        default = pc.default_period(options)
        period_label = slots[1].selectbox(
            FREQUENCY_LABELS[frequency], labels, index=labels.index(default.label), key=f"cmp_period_{frequency}",
            format_func=lambda label: label if by_label[label].complete else f"{label} · "
                                                                             f"{by_label[label].status_text.lower()}",
            help="Periods marked as partial, not finished or with possible missing data can't be compared as a "
                 "whole. Use 'Compare to date' for those.")
        option = by_label[period_label]
        mode = slots[2].segmented_control("Compare with", list(pc.MODE_LABELS), format_func=pc.MODE_LABELS.get,
                                          default=pc.MODE_PREVIOUS, key="cmp_mode") or pc.MODE_PREVIOUS
        to_date = slots[3].toggle("Compare to date", key="cmp_to_date", disabled=option.complete,
                                  help="Compares only the days covered so far with the same days of the "
                                       "comparison period. Only needed when the chosen period is not complete.")
    return frequency, option, mode, bool(to_date and not option.complete)


def figure_card(key: str, comparison: pc.PeriodComparison, lead: bool = False) -> None:
    change = comparison.changes[key]
    current, before = comparison.current, comparison.comparison
    currency = comparison.currency
    value = pc.format_value(change.current, change.kind, currency)
    delta = pc.change_text(change, currency)
    help_text = "\n\n".join(part for part in (
        pc.metric_definition(current, key),
        f"**{current.span.label}:** {pc.metric_calculation(current, key)}",
        f"**{before.span.label}:** {pc.metric_calculation(before, key)}",
        f"**Change:** {change.formula}" if change.formula else "") if part)
    if change.change is not None:
        footnote = f"{before.span.label}: {pc.format_value(change.comparison, change.kind, currency)}"
        if change.change_pct is None and not change.is_points and change.note:
            footnote += f" · {change.note}"
        # The figure's own caveat, as on the Dashboard (e.g. expenses are the total for the
        # dates only while filtering by product, category or region).
        kpi = current.result.kpis.get(key)
        if kpi is not None and kpi.available and kpi.note:
            footnote += f" · {kpi.note}"
    else:
        blocked = pc.incomplete_sides(comparison, key) if change.current is not None and \
            change.comparison is not None else []
        missing_side = current if change.current is None else before
        if blocked:
            footnote = ":material/info: Not compared: " + "; ".join(blocked)
        elif key in missing_side.unavailable:
            # The period has no data (or no records): the full reason is on its period card and in the tooltip.
            statuses = sorted({c.label.lower() for name, c in missing_side.coverage.items()
                               if name in pc.METRIC_SOURCES.get(key, ()) and c.status in (pc.OUTSIDE,
                                                                                         pc.NO_RECORDS)})
            footnote = f":material/info: {missing_side.span.label}: {', '.join(statuses)}"
        elif change.current is None:
            footnote = f":material/info: {change.note or 'Not available.'}"
        else:
            footnote = f":material/info: {before.span.label}: {change.note or 'no figure'}"
    ui.kpi_card(f"cmp_{key}", change.label, value, help=help_text, footnote=footnote, change=delta,
                direction=pc.change_direction(change), lead=lead)


def lead_key(comparison: pc.PeriodComparison) -> str | None:
    """The headline figure: the first of profit, revenue, expenses that has a change
    (else a value). Only chooses what to show first; no figure is changed."""
    changes = comparison.changes
    for test in (lambda c: c.change is not None, lambda c: c.current is not None):
        key = next((k for k in LEAD_PREFERENCE if k in changes and test(changes[k])), None)
        if key:
            return key
    return None


def headline(comparison: pc.PeriodComparison, key: str | None) -> str | None:
    """One sentence that reads the headline change out loud, from the existing figures only.
    Direction is said in words and with an arrow, never by colour alone."""
    if key is None or comparison.changes[key].change is None:
        return None
    change, currency = comparison.changes[key], comparison.currency
    now, before = comparison.current.span.label, comparison.comparison.span.label
    values = (f"{pc.format_value(change.current, change.kind, currency)} in {now}, "
              f"{pc.format_value(change.comparison, change.kind, currency)} in {before}")
    direction = pc.change_direction(change)
    if direction == "off":
        return f":material/arrow_forward: **{change.label} was unchanged** from {before} to {now} ({values})."
    verb, arrow = ("rose", "arrow_upward") if direction == "up" else ("fell", "arrow_downward")
    # Sizes without a sign: the verb already says which way the figure moved.
    if change.is_points:
        size = pc.format_points(abs(change.change)).lstrip("+")
    else:
        amount = pc.format_value(abs(change.change), change.kind, currency)
        size = f"{pc.format_pct_change(abs(change.change_pct)).lstrip('+')} ({amount})" \
            if change.change_pct is not None else amount
    return f":material/{arrow}: **{change.label} {verb} by {size}** from {before} to {now} ({values})."


def render_money_bars(comparison: pc.PeriodComparison) -> None:
    """Revenue, expenses and profit of both periods as bars: the comparison at a glance."""
    items = [(comparison.changes[key].label, comparison.changes[key].current, comparison.changes[key].comparison,
              color) for key, color in MONEY_BARS if key in comparison.changes]
    now, before = comparison.current.span.label, comparison.comparison.span.label
    figure = charts.comparison_bars(items, now, before, comparison.currency)
    if figure is None:
        return
    with ui.chart_card("cmp_money"):
        st.markdown(f'<p class="bp-card-title">{now} vs {before}</p>', unsafe_allow_html=True)
        st.plotly_chart(figure, width="stretch", theme=None, key="chart_cmp_money",
                        config={"displayModeBar": False, "responsive": True})


def render_figures(comparison: pc.PeriodComparison, has_sales: bool) -> None:
    ui.section_header("Key figures", "% change (amount) · margins and rates in percentage points (pp) · arrows show "
                                     "direction, not good or bad")
    lead = lead_key(comparison)
    for index, (row, label, container_key) in enumerate(zip(KPI_ROWS, ROW_LABELS, ROW_KEYS)):
        keys = [key for key in row if key in comparison.changes and (has_sales or key not in SALES_ONLY)]
        if not keys:
            continue
        if label:
            st.markdown(f'<p class="bp-dash-label">{label}</p>', unsafe_allow_html=True)
        if lead in keys:   # the headline figure goes first and a little wider
            keys = [lead] + [key for key in keys if key != lead]
        with st.container(key=container_key):
            widths = [1.4 if key == lead else 1 for key in keys]
            for tile, key in zip(st.columns(widths, gap="small"), keys):
                with tile:
                    figure_card(key, comparison, lead=key == lead)
        if index == 0:     # the money figures, drawn, right under their cards
            render_money_bars(comparison)


def comparison_table(comparison: pc.PeriodComparison, has_sales: bool) -> pd.DataFrame:
    currency = comparison.currency
    rows = []
    for key, change in comparison.changes.items():
        if not has_sales and key in SALES_ONLY:
            continue
        if change.is_points:
            pct = "—"
        else:
            pct = pc.format_pct_change(change.change_pct) if change.change_pct is not None else "—"
        rows.append({
            "Figure": change.label,
            comparison.current.span.label: pc.format_value(change.current, change.kind, currency),
            comparison.comparison.span.label: pc.format_value(change.comparison, change.kind, currency),
            "Change (amount or pp)": pc.format_signed(change.change, change.kind, currency),
            "% change": pct,
            "How the change was calculated": change.formula or f"Not calculated: {change.note}",
        })
    return pd.DataFrame(rows)


def render_growth(comparison: pc.PeriodComparison) -> None:
    gap = comparison.growth
    if not gap.available:   # one quiet line instead of an empty card
        st.markdown(f"{ui.status_badge('neutral', 'Growth gap not available', ':material/block:')} "
                    f"<span class='bp-card-text'>{gap.note}</span>", unsafe_allow_html=True)
        return
    ui.section_header("Expenses compared with revenue", "Growth gap = expense % change − revenue % change (pp).")
    with ui.card("cmp_growth"):
        left, right = st.columns([1, 2.4], vertical_alignment="center")
        left.metric("Growth gap", pc.format_points(gap.gap),
                    help="Above 0: expenses grew faster than revenue (or fell more slowly). Below 0: revenue "
                         "grew faster.")
        right.markdown(f"**{gap.meaning}**")
        right.caption(gap.calculation)


def render_period_chart(sales, expenses, filters, frequency, include_pending, window, today,
                        comparison: pc.PeriodComparison) -> None:
    name = FREQUENCY_LABELS[frequency].lower()
    ui.section_header(f"Every {name}", "Hatched bars: incomplete periods (not compared).")
    available = [key for key in CHART_METRICS if key in comparison.changes
                 and (comparison.changes[key].current is not None or comparison.changes[key].comparison is not None)]
    if not available:
        return
    labels = {key: comparison.changes[key].label for key in available}
    metric = st.selectbox("Show", available, format_func=labels.get, key="cmp_chart_metric")
    table = pc.period_table(sales, expenses, filters, frequency, metric, include_pending, window, today)
    if table.empty:
        st.info("There are no periods to show for the selected dates.", icon=":material/info:")
        return
    kind = comparison.changes[metric].kind
    chart_data = table.assign(Complete=table["Status"].eq(pc.STATUS_LABELS[pc.COMPLETE]))
    marked = {comparison.current.period.label: "This period", comparison.comparison.period.label: "Compared with"}
    figure = charts.period_chart(chart_data, labels[metric], kind, comparison.currency, CHART_METRICS[metric], marked)
    currency = comparison.currency
    shown = pd.DataFrame({
        "Period": table["Period"],
        "Status": table["Status"],
        labels[metric]: [pc.format_value(v if pd.notna(v) else None, kind, currency) for v in table["Value"]],
        "Change vs previous": [pc.format_signed(v if pd.notna(v) else None, kind, currency)
                               for v in table["Change vs previous"]],
        "% vs previous": ["—" if pd.isna(v) else (pc.format_points(v) if kind == bm.KIND_PERCENT
                                                  else pc.format_pct_change(v)) for v in table["% vs previous"]],
        "Same period last year": [pc.format_value(v if pd.notna(v) else None, kind, currency)
                                  for v in table["Same period last year"]],
        "% vs last year": ["—" if pd.isna(v) else (pc.format_points(v) if kind == bm.KIND_PERCENT
                                                   else pc.format_pct_change(v)) for v in table["% vs last year"]],
        "Note": table["Note"],
    })
    with ui.chart_card("cmp_periods"):
        st.markdown(f'<p class="bp-card-title">{labels[metric]} per {name}</p>', unsafe_allow_html=True)
        if figure is None:
            st.info(f"No {labels[metric].lower()} figures for these periods.", icon=":material/info:")
        else:
            st.plotly_chart(figure, width="stretch", theme=None, key="chart_cmp_periods",
                            config={"displayModeBar": False, "responsive": True})
        with st.expander("Show data table", icon=":material/table:"):
            st.dataframe(shown, hide_index=True, width="stretch")


def no_comparison_data(comparison: pc.PeriodComparison) -> bool:
    covers = comparison.comparison.coverage.values()
    return bool(covers) and all(c.status == pc.OUTSIDE for c in covers)


def range_notes(comparison: pc.PeriodComparison, window) -> list[tuple[str, str]]:
    """Explain when a compared period reaches outside the selected date range."""
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    notes = []
    current = comparison.current.span
    if current.start < start or current.end > end:
        notes.append(("info", f"Comparisons use whole calendar periods: {current.label} "
                              f"({bm.format_date(current.start)} – {bm.format_date(current.end)}) goes beyond your "
                              f"selected dates ({bm.format_date(start)} – {bm.format_date(end)}), and all of its "
                              "days are used."))
    other = comparison.comparison.span
    if (other.start < start or other.end > end) and not no_comparison_data(comparison):
        notes.append(("info", f"The comparison period, {other.label}, is outside your selected dates. It is taken "
                              "from the full data, so the two periods can be compared."))
    return notes


# ---------------------------------------------------------------------------
loaded, signature = ctx.loaded_file()
if loaded is None:
    render_empty_state()
    st.stop()

header_actions = ui.page_header("Comparisons", "A month, quarter or year against the one before or last year.",
                                eyebrow="Analysis", actions=1)
with header_actions.popover("Settings", icon=":material/tune:", width="stretch"):
    include_pending, include_errors = ctx.counting_settings(PREFIX)

data = ctx.select_data(loaded, signature, include_errors, PREFIX)
sales, expenses = data.sales, data.expenses
if not data.has_amounts:
    roles = ", ".join(sorted(data.main.mapping.matches)) or "none"
    st.warning(f"**BusinessPulse couldn't find sales or expense amounts in {loaded.file_name}.** Comparisons need a "
               "column with sales/revenue amounts (or quantity and unit price), or a column with expense amounts. "
               f"Recognised fields: {roles}. Check **Column recognition** on the Data quality page if a column was "
               "missed.", icon=":material/warning:")
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)
    st.stop()

ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)

unavailable = pc.comparison_unavailable_reason(sales, expenses)
if unavailable:
    ui.empty_state("comparisons_no_dates", ":material/event_busy:", "Comparisons need dates", unavailable)
    st.stop()

today = QualityRules().today()
full_range = bm.available_range(sales, expenses)
start_range = bm.default_range(sales, expenses, today)
with st.container(key="bp_cmp_filters"):   # Comparisons-only wrapper: lets the filter row wrap on narrow screens
    filters = ctx.render_filters(sales, full_range, start_range, PREFIX)
window = (filters.start or start_range[0], filters.end or start_range[1])

frequency, option, mode, to_date = control_bar(sales, expenses, window, today)
if option is None:
    ui.empty_state("comparisons_no_periods", ":material/date_range:", "No periods in the selected dates",
                   "Widen the date range or click Reset filters.")
    st.stop()

comparison = pc.compare_periods(sales, expenses, filters, option.period, mode, include_pending, to_date, today)

if not option.complete and not to_date:
    st.info(f"**{option.period.label} is not complete** ({option.status_text.lower()}), so changes are not "
            "calculated. Turn on **Compare to date** to compare the same days of both periods, or choose a "
            "complete period.", icon=":material/info:")
if no_comparison_data(comparison):
    if frequency == "Y":
        advice = "Choose a later year (a year-on-year comparison needs two years of data)."
    elif mode == pc.MODE_LAST_YEAR:
        advice = "Choose a later period, or compare with the previous period."
    else:
        advice = "Choose a later period."
    st.info(f"**There is no data for {comparison.comparison.span.label}.** "
            f"{next(iter(comparison.comparison.coverage.values())).reason} {advice}", icon=":material/info:")

# The headline change comes first, right under the controls, so it is read before the details.
sentence = headline(comparison, lead_key(comparison))
if sentence:
    with st.container(key="bp_cmp_headline"):
        st.markdown(sentence)

with st.container(key="bp_cmp_periods"):
    left, right = st.columns(2, gap="medium")
    with left:
        render_period_card("cmp_current", "This period", comparison.current)
    with right:
        render_period_card("cmp_comparison", "Compared with", comparison.comparison)

notes = ([("warning", w) for dataset in (sales, expenses) if dataset for w in dataset.warnings]
         + comparison.other_notes          # period statuses are shown on the period cards
         + [("info", f"{side.span.label}: {n}") for side in (comparison.current, comparison.comparison)
            for n in side.result.notes]
         + range_notes(comparison, window))
ui.notes_panel(notes)

if sales is not None and filters.has_dimension_filters and comparison.current.result.sales_rows.empty \
        and comparison.comparison.result.sales_rows.empty:
    st.info("**No sales match the selected filters in either period.** Change the filters or click **Reset "
            "filters**.", icon=":material/filter_alt_off:")

render_figures(comparison, sales is not None)
render_growth(comparison)
render_period_chart(sales, expenses, filters, frequency, include_pending, window, today, comparison)

with st.expander("All figures and how they are calculated", icon=":material/calculate:"):
    st.dataframe(comparison_table(comparison, sales is not None), hide_index=True, width="stretch",
                 column_config={"How the change was calculated": st.column_config.TextColumn(width="large")})
    st.markdown(HOW_IT_WORKS)
