"""Stage 6.4 checks for the insights engine (tools/insights.py) and the Insights screen."""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config import session_keys
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import insights as ins
from tools import performance as perf
from tools import period_comparison as pc
from tools.file_loader import load_file
from tools.sample_data import read_sample

ROOT = Path(__file__).resolve().parent.parent
PAGE = str(ROOT / "views" / "insights.py")


def test_every_business_rule_referenced_in_code_is_defined():
    """Regression (Stage 6.4): the Insights page crashed with AttributeError on br.INSIGHT_STEADY_PCT.
    Every `br.NAME` used in views/ and tools/ must exist in config/business_rules.py."""
    import re

    from config import business_rules as br

    insight_names = {"INSIGHT_STEADY_PCT", "INSIGHT_GROWTH_GAP_PP", "INSIGHT_CONCENTRATION_SHARE",
                     "INSIGHT_STREAK_MONTHS"}
    referenced = set()
    for folder in ("views", "tools"):
        for path in (ROOT / folder).glob("*.py"):
            referenced |= set(re.findall(r"\bbr\.([A-Z][A-Z0-9_]*)\b", path.read_text(encoding="utf-8")))
    assert insight_names <= referenced
    missing = sorted(name for name in referenced if not hasattr(br, name))
    assert not missing, f"Referenced but not defined in config/business_rules.py: {missing}"


@pytest.fixture(scope="module")
def sample():
    sales = bm.prepare_dataset(load_file(*read_sample("sales_clean")).data, "sales_clean.csv", "Original upload")
    expenses = bm.prepare_dataset(load_file(*read_sample("expenses_clean")).data, "expenses_clean.csv",
                                  "Original upload")
    today = QualityRules().today()
    window = bm.default_range(sales, expenses, today)
    return sales, expenses, window, today


def build(sample, filters=None, sales=True, expenses=True):
    s, e, window, today = sample
    filters = filters or bm.Filters(*window)
    return ins.build_insights(s if sales else None, e if expenses else None, filters, window, today=today)


def by_key(report):
    return {insight.key: insight for insight in report.insights}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def test_every_topic_has_a_finding_with_sample_data(sample):
    report = build(sample)
    assert (report.period, report.compared_with) == ("Jun 2026", "May 2026")
    assert report.missing == {}
    assert {i.topic for i in report.insights} == set(ins.TOPICS)
    assert all(i.evidence and i.recommendation for i in report.insights)


def test_revenue_and_profit_reuse_the_comparison_figures(sample):
    s, e, window, today = sample
    report = by_key(build(sample))
    comparison = pc.compare_periods(s, e, bm.Filters(*window), pc.period_of("2026-06-01", "M"), today=today)
    revenue = comparison.changes["revenue"]
    assert report["revenue_trend"].tone == ins.ATTENTION                    # 22,561.65 vs 31,820.99
    assert pc.format_pct_change(revenue.change_pct).lstrip("−") in report["revenue_trend"].title
    assert any(revenue.formula in line for line in report["revenue_trend"].evidence)
    assert any(comparison.changes["profit"].formula in line for line in report["profit_trend"].evidence)


def test_top_and_weakest_items_match_the_performance_tables(sample):
    s, e, window, _ = sample
    report = by_key(build(sample))
    table = perf.dimension_table(bm.compute_dashboard(s, e, bm.Filters(*window)), "product")
    assert report["product_top"].title.startswith(table.iloc[0]["Name"])
    assert report["product_weak"].title.startswith(table.iloc[-1]["Name"])


def test_expenses_only_file_makes_no_sales_findings(sample):
    report = build(sample, sales=False)
    assert all(report.missing[topic] == "Needs sales data." for topic in ("Revenue", "Products", "Regions"))
    assert all(i.topic == "Expenses" for i in report.insights)


def test_no_profit_or_expense_finding_without_expense_data(sample):
    report = build(sample, expenses=False)
    assert not report.for_topic("Profit") and not report.for_topic("Expenses")
    assert report.missing["Profit"] and report.missing["Expenses"]


def test_partial_months_are_never_compared(sample):
    _, e, _, today = sample
    frame = load_file(*read_sample("sales_clean")).data
    cut = bm.prepare_dataset(frame[frame["order_date"] <= "2026-06-15"], "sales_cut.csv", "Original upload")
    window = bm.default_range(cut, e, today)
    report = ins.build_insights(cut, e, bm.Filters(*window), window, today=today)
    assert (report.period, report.compared_with) == ("May 2026", "Apr 2026")   # June is only half recorded


def test_a_single_product_gives_no_ranking(sample):
    s, _, window, _ = sample
    product = sorted(s.rows["product"].dropna().unique())[0]
    report = build(sample, filters=bm.Filters(*window, products=(product,)))
    assert "at least two products" in report.missing["Products"]


def test_growth_streak_counts_runs_and_stops_at_gaps():
    table = pd.DataFrame({"Start": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]),
                          "% vs previous": [float("nan"), 5.0, 3.0, 1.0]})
    assert ins.growth_streak(table, pd.Timestamp("2026-04-01")) == 3
    table.loc[2, "% vs previous"] = -2.0
    assert ins.growth_streak(table, pd.Timestamp("2026-04-01")) == 1


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def open_page(main=None, companion=None, content=None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=180)
    if main or content:
        name, data = content or read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": data, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, data)
    if companion:
        name, data = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": data, "source": "sample",
                                                          "sheet": None}
    app.run()
    assert not app.exception, app.exception
    return app


def test_page_empty_state_and_demo():
    app = open_page()
    assert any("No data loaded yet" in block.value for block in app.markdown)
    app.button(key="load_demo").click()
    app.run()
    assert not app.exception, app.exception
    assert any("Revenue fell" in block.value for block in app.markdown)
    assert {m.label for m in app.metric} == {"Findings", "Need attention", "Positive"}


def test_page_explains_missing_findings_for_sales_only():
    app = open_page("sales_clean")
    captions = "\n".join(block.value for block in app.caption)
    assert "No profit finding" in captions and "No expenses finding" in captions
    assert any("Suggested next step" in block.value for block in app.markdown)


def test_page_rejects_file_without_amounts():
    app = open_page(content=("things.csv", b"colour,size\nred,10\nblue,12\n"))
    assert any("couldn't find sales or expense amounts" in w.value for w in app.warning)
