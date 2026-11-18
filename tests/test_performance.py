"""Stage 6.3 checks for the Performance screen (tools/performance.py and views/performance.py)."""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config import session_keys
from tools import business_metrics as bm
from tools import performance as perf
from tools.file_loader import load_file
from tools.sample_data import read_sample

ROOT = Path(__file__).resolve().parent.parent
PAGE = str(ROOT / "views" / "performance.py")
TIMEOUT = 180


def prepare(csv: str, name: str = "sales.csv", include_errors: bool = False) -> bm.PreparedDataset:
    return bm.prepare_dataset(load_file(name, csv.encode()).data, name, "Original upload",
                              include_error_rows=include_errors)


@pytest.fixture(scope="module")
def sample():
    sales = bm.prepare_dataset(load_file(*read_sample("sales_clean")).data, "sales_clean.csv", "Original upload")
    expenses = bm.prepare_dataset(load_file(*read_sample("expenses_clean")).data, "expenses_clean.csv",
                                  "Original upload")
    return sales, expenses


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------
def test_dimension_totals_match_the_dashboard(sample):
    sales, expenses = sample
    result = bm.compute_dashboard(sales, expenses, bm.Filters())
    for by in ("product", "category", "region"):
        table = perf.dimension_table(result, by)
        assert table["Revenue"].sum() == pytest.approx(result.kpis["revenue"].value)
        assert table["Units"].sum() == pytest.approx(result.kpis["units"].value)
        assert table["Share"].sum() == pytest.approx(100)
        assert list(table["Revenue"]) == sorted(table["Revenue"], reverse=True)
        assert "Gross profit" not in table        # expenses are not per product/category/region


def test_profit_is_not_attributed_with_a_separate_expense_file(sample):
    sales, expenses = sample
    result = bm.compute_dashboard(sales, expenses, bm.Filters())
    available, note = perf.profit_attribution(result, expenses)
    assert not available and "can't be attributed" in note
    available, note = perf.profit_attribution(bm.compute_dashboard(sales, None, bm.Filters()), None)
    assert not available and "no cost or expense data" in note


def test_filters_change_the_figures(sample):
    sales, expenses = sample
    region = bm.dimension_options(sales)["region"][0]
    result = bm.compute_dashboard(sales, expenses, bm.Filters(regions=(region,)))
    table = perf.dimension_table(result, "region")
    assert list(table["Name"]) == [region]
    assert table["Revenue"].iloc[0] == pytest.approx(result.kpis["revenue"].value)


def test_gross_profit_per_item_and_missing_values_are_not_zero():
    sales = prepare("order_id,order_date,product,quantity,amount,cost\n"
                    "1,2025-01-05,A,2,100,60\n"
                    "2,2025-01-06,A,1,50,\n"
                    "3,2025-01-07,B,,80,30\n", include_errors=True)   # blanks only reach figures this way
    result = bm.compute_dashboard(sales, None, bm.Filters())
    assert len(result.revenue_rows) == 3, sales.warnings
    assert result.profit_type == "gross"
    assert perf.profit_attribution(result, None)[0]
    table = perf.dimension_table(result, "product").set_index("Name")
    assert table.loc["A", "Revenue"] == 150
    assert table.loc["A", "Gross profit"] == 40            # the row without a cost is left out
    assert table.loc["A", "Gross margin"] == pytest.approx(40)
    assert table.loc["A", "Units"] == 3
    assert pd.isna(table.loc["B", "Units"])                 # no quantity -> —, never 0
    assert table.loc["B", "Average order value"] == 80


def test_transactions_without_order_ids():
    sales = prepare("order_date,region,amount\n2025-01-05,North,10\n2025-01-05,North,30\n2025-01-06,South,5\n")
    result = bm.compute_dashboard(sales, None, bm.Filters())
    table = perf.dimension_table(result, "region").set_index("Name")
    assert table.loc["North", "Orders"] == 2
    assert table.loc["North", "Average order value"] == 20
    assert table["Units"].isna().all()
    assert "Units" not in perf.ranking_metrics(table.reset_index())


def test_top_and_bottom_never_overlap():
    table = pd.DataFrame({"Name": list("ABCDEFG"), "Revenue": [70, 60, 50, 40, 30, 20, None]})
    top, bottom, note = perf.top_and_bottom(table, "Revenue", count=5)
    assert list(top["Name"]) == ["A", "B", "C"]
    assert list(bottom["Name"]) == ["F", "E", "D"]
    assert "1 item(s)" in note
    top, bottom, _ = perf.top_and_bottom(table.head(1), "Revenue")
    assert top.empty and bottom.empty


def test_trend_matches_dashboard_revenue(sample):
    sales, expenses = sample
    result = bm.compute_dashboard(sales, expenses, bm.Filters())
    trend = perf.trend_table(result, sales, bm.Filters(), "M")
    dashboard = bm.time_table(result, sales, expenses, bm.Filters(), "M")
    assert list(trend["Revenue"]) == pytest.approx(list(dashboard["Revenue"]))
    assert trend["Units"].sum() == pytest.approx(result.kpis["units"].value)
    assert (trend["Average order value"].dropna() > 0).all()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def open_page(main=None, companion=None, shared=None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=TIMEOUT)
    if main:
        name, data = read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": data, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, data)
    if companion:
        name, data = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": data, "source": "sample",
                                                          "sheet": None}
    if shared:
        app.session_state[session_keys.ANALYSIS_SHARED] = shared
    app.run()
    assert not app.exception, app.exception
    return app


def page_text(app: AppTest) -> str:
    return "\n".join(block.value for group in (app.markdown, app.caption, app.info, app.warning) for block in group)


def test_empty_state_and_demo():
    app = open_page()
    assert any("No data loaded yet" in block.value for block in app.markdown)
    app.button(key="load_demo").click()
    app.run()
    assert not app.exception
    labels = {metric.label for metric in app.metric}
    assert {"Top product", "Top category", "Top region"} <= labels    # leaders, not the Dashboard totals again
    assert "Total revenue" not in labels


def test_page_with_sample_data(sample):
    app = open_page("sales_clean", "expenses_clean")
    sales, expenses = sample
    text = page_text(app)
    for title in ("Sales over time", "Products", "Categories", "Regions"):
        assert any(h.value == title for h in app.subheader), title
    assert "can't be attributed" in text
    assert any(k.startswith("perf_rank_") for k in (s.key for s in app.selectbox))


def test_shared_filters_are_used(sample):
    sales, _ = sample
    region = bm.dimension_options(sales)["region"][0]
    app = open_page("sales_clean", shared={"region": (region,)})
    assert app.multiselect(key="perf_region").value == [region]
    shown = {m.label: m.value for m in app.metric}
    assert shown["Top region"] == region                              # the filter reaches the leaders


def test_expense_only_file_explains_sales_are_needed():
    app = open_page("expenses_clean")
    assert any("Performance needs sales data" in block.value for block in app.markdown)
