"""Insights screen: plain-language findings about revenue, profit, expenses,
products, categories and regions, each with its numbers and a suggested next step.

Layout only. The data (version, second file, filters, counting options) comes from
ui/analysis_context.py, shared with the other analysis pages; every finding comes
from tools/insights.py, which reuses the Dashboard, Comparisons and Performance
calculations.
"""

import html
import re

import streamlit as st

from config import business_rules as br
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import insights as ins
from ui import analysis_context as ctx
from ui import components as ui
from ui.state import load_demo

PREFIX = "ins"
# Tone is said in words and with an icon, never by colour alone.
TONE_BADGES = {ins.POSITIVE: ("success", "Positive", ":material/check_circle:"),
               ins.ATTENTION: ("warning", "Needs attention", ":material/flag:"),
               ins.NEUTRAL: ("info", "For information", ":material/info:")}
WHERE_TO_START = ("Begin with **Needs attention**. Next steps are prompts to look closer, not guarantees.")
TOPIC_CAPTIONS = {
    "Revenue": "Latest complete month against the month before.",
    "Profit": "Latest complete month against the month before.",
    "Expenses": "Expenses against revenue, and the expense that rose most.",
    "Products": "Top, weakest and biggest movers.",
    "Categories": "Top, weakest and biggest movers.",
    "Regions": "Top, weakest and biggest movers.",
}
HOW_IT_WORKS = f"""
Insights are found with fixed, transparent rules. No AI and no estimates: every number is calculated from your
data exactly as on the Dashboard, Comparisons and Performance pages.

- **Trends** compare the latest *complete* month in your selected dates with the month before. A month that is
  partly recorded is never used for a change.
- A change smaller than **{br.INSIGHT_STEADY_PCT:g}%** is described as steady. **{br.INSIGHT_STREAK_MONTHS}** or
  more rises (or falls) in a row are reported as a run.
- Expenses are flagged when they grow at least **{br.INSIGHT_GROWTH_GAP_PP:g} percentage points** faster (or
  slower) than revenue.
- **Top and weakest** products, categories and regions use the whole selected period. One item with
  **{br.INSIGHT_CONCENTRATION_SHARE:g}%** or more of revenue (out of three or more) is flagged as a concentration
  risk.
- When the data needed for a finding is missing, no finding is made and the reason is shown instead.
- Suggested next steps are general prompts to investigate, not guarantees.
"""


def plain(text: str) -> str:
    """Escape Markdown characters so names from the data are shown as typed."""
    return re.sub(r"([\\`*_\[\]$<>#~|])", r"\\\1", str(text))


def render_empty_state() -> None:
    ui.page_header("Insights", "Key findings about your business, with the numbers behind them.", eyebrow="Analysis")
    actions = ui.empty_state("insights", ":material/lightbulb:", "No data loaded yet",
                             "Upload a sales file first, or try the demo with fictional sample data.")
    left, right = actions.columns(2)
    if left.button("Go to Upload data", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")
    right.button("Load demo data", type="primary", icon=":material/play_arrow:", width="stretch",
                 key="load_demo", on_click=load_demo, help="Loads the fictional sample sales and expense files.")


def render_summary(report: ins.InsightReport) -> None:
    """The number of findings leads; the two tone counts support it. Same three figures as before."""
    ui.section_header("Summary", "For the selected period and filters.")
    figures = (("findings", "Findings", len(report.insights), "Across all topics below."),
               ("attention", "Need attention", report.count(ins.ATTENTION), "Worth checking."),
               ("positive", "Positive", report.count(ins.POSITIVE), "Going well."))
    with st.container(key="bp_ins_kpis"):
        for tile, (key, label, value, footnote) in zip(st.columns([1.4, 1, 1], gap="small"), figures):
            with tile:
                ui.kpi_card(f"ins_{key}", label, f"{value:,}", footnote=footnote, lead=key == "findings")
    with st.container(key="bp_ins_start"):
        st.markdown(f":material/lightbulb: **Where to start.** {WHERE_TO_START}")


def render_insight(insight: ins.Insight) -> None:
    """One finding in its own glass card: tone, headline and figures on the left, the
    suggested next step on the right (below it on narrow screens)."""
    safe_key = re.sub(r"[^A-Za-z0-9_]", "_", insight.key)
    with ui.card(f"insight_{safe_key}"):
        finding, action = st.columns([3, 2], gap="large")
        with finding:
            st.markdown(ui.status_badge(*TONE_BADGES[insight.tone]))
            st.markdown(f'<p class="bp-card-title">{html.escape(insight.title, quote=False)}</p>',
                        unsafe_allow_html=True)
            st.markdown('<p class="bp-ins-label">What the numbers show</p>', unsafe_allow_html=True)
            st.markdown("\n".join(f"- {plain(line)}" for line in insight.evidence))
        with action, st.container(key=f"bp_ins_step_{safe_key}"):
            st.markdown(f":material/arrow_forward: **Suggested next step**\n\n{plain(insight.recommendation)}")


def render_topic(topic: str, report: ins.InsightReport) -> None:
    ui.section_header(topic, TOPIC_CAPTIONS[topic])
    found = report.for_topic(topic)
    if not found:
        st.caption(f":material/info: No {topic.lower()} finding: {plain(report.missing.get(topic, 'not enough data.'))}")
        return
    for insight in found:
        render_insight(insight)


# ---------------------------------------------------------------------------
loaded, signature = ctx.loaded_file()
if loaded is None:
    render_empty_state()
    st.stop()

header_actions = ui.page_header("Insights", "Key findings, the numbers behind them and a next step.",
                                eyebrow="Analysis", actions=1)
with header_actions.popover("Settings", icon=":material/tune:", width="stretch"):
    include_pending, include_errors = ctx.counting_settings(PREFIX)

data = ctx.select_data(loaded, signature, include_errors, PREFIX)
sales, expenses = data.sales, data.expenses
if not data.has_amounts:
    roles = ", ".join(sorted(data.main.mapping.matches)) or "none"
    st.warning(f"**BusinessPulse couldn't find sales or expense amounts in {loaded.file_name}.** Insights need a "
               "column with sales/revenue amounts (or quantity and unit price), or a column with expense amounts. "
               f"Recognised fields: {roles}. Check **Column recognition** on the Data quality page if a column was "
               "missed.", icon=":material/warning:")
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)
    st.stop()

ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)

today = QualityRules().today()
full_range = bm.available_range(sales, expenses)
start_range = bm.default_range(sales, expenses, today)
with st.container(key="bp_ins_filters"):   # Insights-only wrapper: lets the filter row wrap on narrow screens
    filters = ctx.render_filters(sales, full_range, start_range, PREFIX)
window = (filters.start or start_range[0], filters.end or start_range[1]) if full_range else None

report = ins.build_insights(sales, expenses, filters, window, include_pending, today)

if window:
    basis = (f"Trends compare **{report.period}** with **{report.compared_with}**. " if report.period else "")
    st.caption(f"**Selected period:** {bm.format_date(window[0])} – {bm.format_date(window[1])} · {basis}"
               "Products, categories and regions use the whole selected period.")
ui.notes_panel([("warning", w) for dataset in (sales, expenses) if dataset for w in dataset.warnings])

if not report.insights:
    ui.empty_state("insights_none", ":material/search_off:", "Not enough data for insights",
                   "The reasons are listed under each topic below. Widen the date range, clear filters, or add the "
                   "missing data.")
else:
    render_summary(report)

for topic in ins.TOPICS:
    render_topic(topic, report)

with st.expander("How are insights found?", icon=":material/calculate:"):
    st.markdown(HOW_IT_WORKS)
