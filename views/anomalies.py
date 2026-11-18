"""Anomalies screen: months where revenue, expenses, profit or a product, category or
region behaved unusually, with the figures, the reason and a severity.

Layout only. The data (version, second file, filters, counting options) comes from
ui/analysis_context.py, shared with the other analysis pages; every finding comes
from tools/anomalies.py.
"""

import html
import re

import streamlit as st

from config import business_rules as br
from config.quality_rules import QualityRules
from tools import anomalies as an
from tools import business_metrics as bm
from ui import analysis_context as ctx
from ui import components as ui
from ui.state import load_demo

PREFIX = "ano"
# Severity is said in words and with an icon, never by colour alone.
SEVERITY_BADGES = {an.HIGH: ("error", "High severity", ":material/priority_high:"),
                   an.MEDIUM: ("warning", "Medium severity", ":material/error_outline:")}
NEXT_STEP = ("A prompt to check, not proof of a problem. Look at that month's records and confirm a known reason "
             "(a promotion, a one-off order, a missing invoice).")
AREA_CAPTIONS = {
    "Revenue": "Each complete month against the months before it.",
    "Expenses": "Each complete month against the months before it.",
    "Profit": "Each complete month against the months before it.",
    "Products": "Each product's share of monthly revenue against its usual share.",
    "Categories": "Each category's share of monthly revenue against its usual share.",
    "Regions": "Each region's share of monthly revenue against its usual share.",
}
HOW_IT_WORKS = f"""
Anomalies are found with one fixed, transparent statistical rule. No AI and no estimates: every figure is
calculated from your data exactly as on the Dashboard and Comparisons pages.

- Only **complete months** are used. A month that is partly recorded, or has a long stretch without records, is
  never checked and never used as a baseline.
- Each month is compared with up to **{br.ANOMALY_MAX_HISTORY}** complete months before it. At least
  **{br.ANOMALY_MIN_HISTORY}** are needed, so the first months of your data are only used as a baseline.
- The **typical month** is the median of those months, and the **usual variation** is the median absolute
  deviation (MAD). Medians are used so that one odd month doesn't hide another.
- **Robust z-score** = 0.6745 × (month − median) ÷ MAD. A month is unusual when the score is at least
  **±{br.ANOMALY_Z_MEDIUM:g}** (medium severity) or **±{br.ANOMALY_Z_HIGH:g}** (high severity) *and* it differs
  from the typical month by at least **{br.ANOMALY_MIN_DEVIATION_PCT:g}%**.
- Products, categories and regions are checked on their **share of the month's revenue**, so a season that lifts
  every item (such as December) is not flagged item by item. Only items with at least
  **{br.ANOMALY_MIN_ITEM_SHARE:g}%** of revenue in the selected dates are checked, and a share must move by at
  least **{br.ANOMALY_MIN_SHARE_POINTS:g} percentage point(s)**.
- Regular seasonal peaks can still be flagged for revenue, expenses and profit: the rule compares with recent
  months, not with the same month last year. An anomaly is a prompt to check, not proof of a problem.
"""


def plain(text: str) -> str:
    """Escape Markdown characters so names from the data are shown as typed."""
    return re.sub(r"([\\`*_\[\]$<>#~|])", r"\\\1", str(text))


def render_empty_state() -> None:
    ui.page_header("Anomalies", "Unusual months, explained with the numbers behind them.", eyebrow="Analysis")
    actions = ui.empty_state("anomalies", ":material/troubleshoot:", "No data loaded yet",
                             "Upload a sales file first, or try the demo with fictional sample data.")
    left, right = actions.columns(2)
    if left.button("Go to Upload data", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")
    right.button("Load demo data", type="primary", icon=":material/play_arrow:", width="stretch",
                 key="load_demo", on_click=load_demo, help="Loads the fictional sample sales and expense files.")


def render_summary(report: an.AnomalyReport) -> None:
    """The count of anomalies leads; severity and coverage support it. Same three figures as before."""
    ui.section_header("Summary", "For the selected period and filters.")
    medium = report.count(an.MEDIUM)
    figures = (("found", "Anomalies", f"{len(report.anomalies):,}",
                f"{report.count(an.HIGH):,} high · {medium:,} medium severity, across the areas below."),
               ("high", "High severity", f"{report.count(an.HIGH):,}", f"Robust z-score ±{br.ANOMALY_Z_HIGH:g} or more."),
               ("months", "Complete months", f"{report.complete_months:,}",
                f"{report.months_checked:,} checked; the rest form the baseline."))
    with st.container(key="bp_ano_kpis"):
        for tile, (key, label, value, footnote) in zip(st.columns([1.4, 1, 1], gap="small"), figures):
            with tile:
                ui.kpi_card(f"ano_{key}", label, value, footnote=footnote, lead=key == "found")
    with st.container(key="bp_ano_next"):
        st.markdown(f":material/lightbulb: **What to do next.** {NEXT_STEP}")


def render_anomaly(anomaly: an.Anomaly) -> None:
    """One finding in its own glass card: badges, title, reason, figures."""
    with ui.card(f"anomaly_{re.sub(r'[^A-Za-z0-9_]', '_', anomaly.key)}"):
        arrow = ":material/arrow_upward:" if anomaly.direction == "higher" else ":material/arrow_downward:"
        effect = "unfavourable" if anomaly.unfavourable else "favourable"
        st.markdown(f"{ui.status_badge(*SEVERITY_BADGES[anomaly.severity])} "
                    f"{ui.status_badge('neutral', f'{anomaly.direction.capitalize()} than usual · {effect}', arrow)}")
        st.markdown(f'<p class="bp-card-title">{html.escape(anomaly.title, quote=False)}</p>', unsafe_allow_html=True)
        st.markdown(f"**Why it was flagged:** {plain(anomaly.reason)}")
        if anomaly.evidence:
            st.markdown(f"**The figures:** {plain(anomaly.evidence[0])}")
        for line in anomaly.evidence[1:]:   # the statistical detail, quieter
            st.caption(plain(line))


def render_area(area: str, report: an.AnomalyReport) -> None:
    ui.section_header(area, AREA_CAPTIONS[area])
    if area in report.missing:
        st.caption(f":material/info: Not checked: {plain(report.missing[area])}")
        return
    found = report.for_area(area)
    if not found:
        checked = report.checked.get(area, 0)
        what = "" if area in an.METRICS.values() else f" ({checked:,} checked)"
        st.caption(f":material/check_circle: No unusual months found{what}.")
        return
    # The area's result in words and an icon (the "none" and "not checked" captions above carry their own icon).
    st.markdown(ui.status_badge("warning", f"{len(found):,} unusual month{'s' if len(found) != 1 else ''}",
                                ":material/troubleshoot:"))
    with st.container(key=f"bp_ano_list_{area.lower()}"):
        for anomaly in found:
            render_anomaly(anomaly)


# ---------------------------------------------------------------------------
loaded, signature = ctx.loaded_file()
if loaded is None:
    render_empty_state()
    st.stop()

header_actions = ui.page_header("Anomalies", "Months that moved far outside their usual range, and why.",
                                eyebrow="Analysis", actions=1)
with header_actions.popover("Settings", icon=":material/tune:", width="stretch"):
    include_pending, include_errors = ctx.counting_settings(PREFIX)

data = ctx.select_data(loaded, signature, include_errors, PREFIX)
sales, expenses = data.sales, data.expenses
if not data.has_amounts:
    roles = ", ".join(sorted(data.main.mapping.matches)) or "none"
    st.warning(f"**BusinessPulse couldn't find sales or expense amounts in {loaded.file_name}.** Anomaly checks "
               "need a column with sales/revenue amounts (or quantity and unit price), or a column with expense "
               f"amounts. Recognised fields: {roles}. Check **Column recognition** on the Data quality page if a "
               "column was missed.", icon=":material/warning:")
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)
    st.stop()

ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)

today = QualityRules().today()
full_range = bm.available_range(sales, expenses)
start_range = bm.default_range(sales, expenses, today)
with st.container(key="bp_ano_filters"):   # Anomalies-only wrapper: lets the filter row wrap on narrow screens
    filters = ctx.render_filters(sales, full_range, start_range, PREFIX)
window = (filters.start or start_range[0], filters.end or start_range[1]) if full_range else None

report = an.build_anomalies(sales, expenses, filters, window, include_pending, today)

if window:
    st.caption(f"**Selected period:** {bm.format_date(window[0])} – {bm.format_date(window[1])} · "
               f"{report.complete_months:,} complete month(s).")
notes = [("warning", w) for dataset in (sales, expenses) if dataset for w in dataset.warnings]
notes += [("info", f"Not checked, month not complete: {plain(item)}") for item in report.skipped]
ui.notes_panel(notes)

if not report.anomalies:
    checked_any = bool(report.checked)
    ui.empty_state("anomalies_none", ":material/check_circle:" if checked_any else ":material/search_off:",
                   "No unusual months found" if checked_any else "Not enough data to check for anomalies",
                   "Every checked month stayed within its usual range." if checked_any else
                   "The reasons are listed under each area below. Widen the date range or add more months of data.")
else:
    render_summary(report)

for area in an.AREAS:
    render_area(area, report)

with st.expander("How are anomalies found?", icon=":material/calculate:"):
    st.markdown(HOW_IT_WORKS)
