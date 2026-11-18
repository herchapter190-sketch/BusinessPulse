"""Stage 5 checks for tools/business_metrics.py and tools/charts.py.

Uses the sample datasets (read only) and small in-memory tables whose correct
answers can be worked out by hand.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from config import business_rules as br
from config import design
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import charts
from tools import data_cleaning as dc
from tools.file_loader import load_file
from tools.sample_data import read_sample

RULES = QualityRules(reference_date=date(2026, 11, 17))


def prepare(table: pd.DataFrame, name: str = "test.csv", version: str = "Original upload", **kwargs):
    return bm.prepare_dataset(table, name, version, rules=RULES, **kwargs)


def text_table(rows: dict) -> pd.DataFrame:
    return pd.DataFrame({column: [str(value) for value in values] for column, values in rows.items()})


def small_sales() -> pd.DataFrame:
    """Five orders worked out by hand. Completed revenue = 10 + 40 + 30 = 80."""
    return text_table({
        "Order ID": ["A1", "A2", "A3", "A4", "A5"],
        "Date": ["2025-01-05", "2025-01-20", "2025-02-03", "2025-02-10", "2025-02-25"],
        "Product": ["Pen", "Mug", "Pen", "Mug", "Lamp"],
        "Category": ["Office", "Kitchen", "Office", "Kitchen", "Home"],
        "Quantity": ["1", "2", "3", "1", "1"],
        "Unit Price": ["10", "20", "10", "20", "50"],
        "Total": ["10", "40", "30", "20", "50"],
        "Region": ["North", "South", "North", "South", "North"],
        "Status": ["Completed", "Completed", "Completed", "Returned", "Cancelled"],
    })


def small_expenses() -> pd.DataFrame:
    return text_table({
        "Expense ID": ["E1", "E2", "E3"],
        "Expense Date": ["2025-01-31", "2025-02-28", "2025-03-31"],
        "Category": ["Rent", "Rent", "Rent"],
        "Amount": ["25", "25", "25"],
    })


@pytest.fixture(scope="module")
def sample_sales():
    return prepare(load_file(*read_sample("sales_clean")).data, "sales_clean.csv")


@pytest.fixture(scope="module")
def sample_expenses():
    return prepare(load_file(*read_sample("expenses_clean")).data, "expenses_clean.csv")


# ---------------------------------------------------------------------------
# KPI calculations
# ---------------------------------------------------------------------------
def test_core_kpis_worked_out_by_hand():
    result = bm.compute_dashboard(prepare(small_sales()), None, bm.Filters())
    k = result.kpis
    assert k["revenue"].value == 80.0              # completed orders only
    assert k["orders"].value == 3
    assert k["units"].value == 6                   # 1 + 2 + 3
    assert k["aov"].value == pytest.approx(80 / 3)
    assert k["returns"].value == 20.0              # the returned order
    assert not k["expenses"].available and not k["profit"].available and not k["margin"].available
    assert "Not counted: 1 returned/refunded (20.00), 1 cancelled (50.00)" in " ".join(result.notes)


def test_kpis_match_an_independent_calculation(sample_sales, sample_expenses):
    raw_sales = pd.read_csv("data/sample/sales_clean.csv")
    raw_expenses = pd.read_csv("data/sample/expenses_clean.csv")
    completed = raw_sales[raw_sales["order_status"] == "Completed"]
    result = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters())
    assert result.kpis["revenue"].value == pytest.approx(completed["total_sales"].sum())
    assert result.kpis["orders"].value == completed["order_id"].nunique()
    assert result.kpis["units"].value == completed["quantity"].sum()
    assert result.kpis["expenses"].value == pytest.approx(raw_expenses["amount"].sum())


def test_every_kpi_explains_itself(sample_sales, sample_expenses):
    result = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters())
    for kpi in result.kpis.values():
        assert kpi.definition
        assert kpi.calculation if kpi.available else kpi.note
    table = bm.kpi_table(result)
    assert list(table.columns) == ["KPI", "Value", "What it means", "How it was calculated", "Notes"]


def test_pending_orders_are_optional():
    table = small_sales()
    table.loc[4, "Status"] = "Pending"
    dataset = prepare(table)
    assert bm.compute_dashboard(dataset, None, bm.Filters()).kpis["revenue"].value == 80.0
    assert bm.compute_dashboard(dataset, None, bm.Filters(), include_pending=True).kpis["revenue"].value == 130.0


def test_revenue_from_quantity_and_price_when_no_amount_column():
    table = small_sales().drop(columns=["Total"])
    dataset = prepare(table)
    assert "quantity × unit price" in dataset.amount_source
    assert bm.compute_dashboard(dataset, None, bm.Filters()).kpis["revenue"].value == 80.0


def test_without_order_ids_rows_are_counted_as_transactions():
    dataset = prepare(small_sales().drop(columns=["Order ID"]))
    orders = bm.compute_dashboard(dataset, None, bm.Filters()).kpis["orders"]
    assert orders.label == "Transactions" and orders.value == 3


# ---------------------------------------------------------------------------
# Profit and margin
# ---------------------------------------------------------------------------
def test_net_profit_and_margin_over_the_shared_period():
    sales, expenses = prepare(small_sales()), prepare(small_expenses())
    result = bm.compute_dashboard(sales, expenses, bm.Filters())
    # Sales cover 5 Jan – 25 Feb; expenses cover 31 Jan – 31 Mar. Shared: 31 Jan – 25 Feb.
    assert result.profit_period == (pd.Timestamp(2025, 1, 31), pd.Timestamp(2025, 2, 25))
    # Revenue in shared period: A3 (30). Expenses in shared period: E1 (25). Profit = 5.
    assert result.kpis["profit"].label == "Net profit"
    assert result.kpis["profit"].value == 5.0
    assert result.kpis["margin"].value == pytest.approx(5 / 30 * 100)
    assert "dates covered by both" in result.kpis["profit"].note
    assert result.kpis["expenses"].value == 75.0  # all expenses in the selected range (3 months)


def test_sample_net_profit_and_margin(sample_sales, sample_expenses):
    result = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters())
    revenue, spent = result.kpis["revenue"].value, result.kpis["expenses"].value
    assert result.kpis["profit"].value == pytest.approx(revenue - spent)
    assert result.kpis["margin"].value == pytest.approx((revenue - spent) / revenue * 100)


def test_gross_profit_when_cost_is_in_the_sales_file():
    table = small_sales()
    table["Cost"] = ["4", "15", "12", "8", "30"]
    sales = prepare(table)
    result = bm.compute_dashboard(sales, None, bm.Filters())
    assert result.profit_type == "gross"
    assert result.kpis["expenses"].label == "Cost of sales"
    assert result.kpis["profit"].value == 80.0 - (4 + 15 + 12)
    assert result.kpis["margin"].value == pytest.approx(49 / 80 * 100)


def test_profit_is_not_invented_when_files_do_not_overlap():
    expenses = small_expenses()
    expenses["Expense Date"] = ["2024-01-31", "2024-02-28", "2024-03-31"]
    result = bm.compute_dashboard(prepare(small_sales()), prepare(expenses), bm.Filters())
    assert not result.kpis["profit"].available
    assert "don't cover any of the same dates" in result.kpis["profit"].note
    assert not result.kpis["margin"].available


def test_profit_not_shown_when_filtering_by_product():
    result = bm.compute_dashboard(prepare(small_sales()), prepare(small_expenses()), bm.Filters(products=("Pen",)))
    assert not result.kpis["profit"].available
    assert "not recorded per product" in result.kpis["profit"].note
    assert "selected dates only" in result.kpis["expenses"].note


def test_margin_unavailable_when_revenue_is_zero():
    table = small_sales()
    table["Status"] = "Cancelled"
    result = bm.compute_dashboard(prepare(table), prepare(small_expenses()), bm.Filters())
    assert result.kpis["revenue"].value == 0.0
    assert not result.kpis["margin"].available


def test_different_currencies_are_not_combined():
    sales = small_sales().rename(columns={"Total": "Total (USD)"})
    expenses = small_expenses().rename(columns={"Amount": "Amount EUR"})
    result = bm.compute_dashboard(prepare(sales), prepare(expenses), bm.Filters())
    assert not result.kpis["profit"].available
    assert any("different currencies" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Missing columns and empty datasets
# ---------------------------------------------------------------------------
def test_dataset_without_amounts_is_unknown():
    dataset = prepare(text_table({"Name": ["a", "b"], "Colour": ["red", "blue"]}))
    assert dataset.kind == "unknown"
    assert dataset.amount_source is None


def test_sales_without_dates_has_kpis_but_no_trend():
    sales = prepare(small_sales().drop(columns=["Date"]))
    assert not sales.has_dates
    result = bm.compute_dashboard(sales, None, bm.Filters())
    assert result.kpis["revenue"].value == 80.0
    assert bm.time_table(result, sales, None, bm.Filters(), "M").empty
    assert any("No date column" in note for note in sales.notes)


def test_sales_without_quantity_has_no_units_kpi():
    sales = prepare(small_sales().drop(columns=["Quantity", "Unit Price"]))
    units = bm.compute_dashboard(sales, None, bm.Filters()).kpis["units"]
    assert not units.available and "No quantity column" in units.note


def test_expense_only_dataset():
    expenses = prepare(small_expenses())
    assert expenses.kind == "expenses"
    result = bm.compute_dashboard(None, expenses, bm.Filters())
    assert result.kpis["expenses"].value == 75.0
    assert not result.kpis["revenue"].available
    assert "No sales data" in result.kpis["revenue"].note


def test_empty_dataset_and_empty_filter_result():
    empty = small_sales().iloc[0:0]
    result = bm.compute_dashboard(prepare(empty), None, bm.Filters())
    assert result.kpis["revenue"].value == 0.0
    assert not result.kpis["aov"].available  # no orders: no average

    sales = prepare(small_sales())
    nothing = bm.compute_dashboard(sales, None, bm.Filters(regions=("Nowhere",)))
    assert nothing.sales_rows.empty and nothing.kpis["revenue"].value == 0.0
    assert bm.breakdown(nothing.revenue_rows, "product").empty
    assert charts.ranked_bar_chart(bm.breakdown(nothing.revenue_rows, "product"), "Revenue", None) is None


# ---------------------------------------------------------------------------
# Negative and zero values, errors, dates
# ---------------------------------------------------------------------------
def test_negative_amounts_reduce_revenue_and_are_explained():
    table = small_sales()
    table.loc[1, "Total"] = "-15"
    result = bm.compute_dashboard(prepare(table), None, bm.Filters())
    assert result.kpis["revenue"].value == 10 - 15 + 30
    assert "negative amount" in result.kpis["revenue"].note
    assert result.kpis["returns"].value == 20 + 15


def test_zero_amounts_are_counted_as_real_values():
    table = small_sales()
    table.loc[0, "Total"] = "0"
    result = bm.compute_dashboard(prepare(table), None, bm.Filters())
    assert result.kpis["revenue"].value == 70.0
    assert result.kpis["orders"].value == 3


def test_rows_with_confirmed_errors_are_left_out_and_reported():
    messy = load_file(*read_sample("sales_messy")).data
    dataset = prepare(messy, "sales_messy.csv")
    assert dataset.error_rows_excluded == 45
    assert any("45 row(s) with confirmed errors" in warning for warning in dataset.warnings)
    assert len(dataset.rows) < 8010 - 45 + 1
    including = prepare(messy, "sales_messy.csv", include_error_rows=True)
    assert including.error_rows_excluded == 0
    assert any("INCLUDED" in warning for warning in including.warnings)


def test_messy_data_warns_about_split_names_and_ambiguous_dates():
    dataset = prepare(load_file(*read_sample("sales_messy")).data, "sales_messy.csv")
    joined = " ".join(dataset.warnings)
    assert "written in different ways" in joined
    assert "ambiguous" in joined  # 03/05/2025-style dates are not guessed


def test_ambiguous_dates_are_never_guessed():
    dataset = prepare(text_table({"Date": ["03/05/2025", "25/12/2025"], "Sales": ["10", "20"]}))
    assert list(dataset.rows["date"]) == [pd.Timestamp(2025, 12, 25)]  # only the unambiguous date is used


def test_duplicates_with_ids_are_not_double_counted():
    table = pd.concat([small_sales(), small_sales().iloc[[0]]], ignore_index=True)
    result = bm.compute_dashboard(prepare(table), None, bm.Filters())
    assert result.kpis["revenue"].value == 80.0


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_date_filter_is_inclusive():
    sales = prepare(small_sales())
    result = bm.compute_dashboard(sales, None, bm.Filters(start=date(2025, 1, 20), end=date(2025, 2, 3)))
    assert result.kpis["revenue"].value == 40 + 30
    assert list(result.revenue_rows["row"]) == [2, 3]


def test_date_filter_on_sample_matches_pandas(sample_sales):
    raw = pd.read_csv("data/sample/sales_clean.csv", parse_dates=["order_date"])
    in_december = raw[(raw["order_date"].dt.month == 12) & (raw["order_date"].dt.year == 2025)
                      & (raw["order_status"] == "Completed")]
    result = bm.compute_dashboard(sample_sales, None, bm.Filters(start=date(2025, 12, 1), end=date(2025, 12, 31)))
    assert result.kpis["revenue"].value == pytest.approx(in_december["total_sales"].sum())


@pytest.mark.parametrize("filters, revenue", [
    (bm.Filters(products=("Pen",)), 40.0),
    (bm.Filters(categories=("Kitchen",)), 40.0),
    (bm.Filters(regions=("North",)), 40.0),
    (bm.Filters(regions=("North",), products=("Mug",)), 0.0),
    (bm.Filters(products=("Pen", "Mug")), 80.0),
])
def test_dimension_filters(filters, revenue):
    assert bm.compute_dashboard(prepare(small_sales()), None, filters).kpis["revenue"].value == revenue


def test_filters_apply_to_kpis_and_charts_consistently(sample_sales):
    filters = bm.Filters(categories=("Electronics",), regions=("North",))
    result = bm.compute_dashboard(sample_sales, None, filters)
    by_product = bm.breakdown(result.revenue_rows, "product")
    over_time = bm.time_table(result, sample_sales, None, filters, "M")
    assert by_product["Amount"].sum() == pytest.approx(result.kpis["revenue"].value)
    assert over_time["Revenue"].sum() == pytest.approx(result.kpis["revenue"].value)
    assert set(result.revenue_rows["category"]) == {"Electronics"}


def test_dimension_options_list_most_common_first(sample_sales):
    options = bm.dimension_options(sample_sales)
    assert set(options) == {"product", "category", "region"}
    assert options["category"][0] == "Electronics"


# ---------------------------------------------------------------------------
# Separate sales and expense files, chart tables
# ---------------------------------------------------------------------------
def test_separate_files_are_never_merged_row_by_row(sample_sales, sample_expenses):
    result = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters())
    assert "amount" in result.expense_rows and "product" not in result.expense_rows
    assert len(result.revenue_rows) + len(result.expense_rows) < len(sample_sales.rows) + len(sample_expenses.rows)
    table = bm.time_table(result, sample_sales, sample_expenses, bm.Filters(), "M")
    assert len(table) == 18
    assert table["Revenue"].sum() == pytest.approx(result.kpis["revenue"].value)
    assert table["Expenses"].sum() == pytest.approx(result.kpis["expenses"].value)
    assert table["Profit"].sum() == pytest.approx(result.kpis["profit"].value)


def test_time_table_marks_profit_only_where_both_files_have_data():
    sales, expenses = prepare(small_sales()), prepare(small_expenses())
    result = bm.compute_dashboard(sales, expenses, bm.Filters())
    table = bm.time_table(result, sales, expenses, bm.Filters(), "M").set_index("Period")
    assert np.isnan(table.loc[pd.Timestamp(2025, 3, 1), "Profit"])  # March has expenses but no sales


def test_breakdown_combines_the_tail_into_all_others(sample_sales):
    result = bm.compute_dashboard(sample_sales, None, bm.Filters())
    table = bm.breakdown(result.revenue_rows, "product", top=5)
    assert len(table) == 6 and table["Name"].iloc[-1].startswith("All others (16)")
    assert table["Amount"].sum() == pytest.approx(result.kpis["revenue"].value)
    assert table["Share"].sum() == pytest.approx(100)


def test_frequency_and_partial_period_notes():
    assert bm.choose_frequency(date(2025, 1, 1), date(2025, 1, 31)) == "D"
    assert bm.choose_frequency(date(2025, 1, 1), date(2025, 4, 30)) == "W"
    assert bm.choose_frequency(date(2025, 1, 1), date(2026, 6, 30)) == "M"
    table = pd.DataFrame({"Period": [pd.Timestamp(2025, 1, 1), pd.Timestamp(2025, 2, 1)]})
    assert "last month" in bm.partial_period_note(table, "M", date(2025, 1, 1), date(2025, 2, 15))
    assert bm.partial_period_note(table, "M", date(2025, 1, 1), date(2025, 2, 28)) is None


# ---------------------------------------------------------------------------
# Default date range and future dates (Stage 5.5 polish)
# ---------------------------------------------------------------------------
TODAY = date(2026, 11, 17)


def sales_with_a_future_date() -> pd.DataFrame:
    table = small_sales()
    table.loc[2, "Date"] = "2027-12-03"  # a mistyped year (the order really belongs to 2025)
    return table


def test_default_range_ends_at_the_last_date_that_is_not_in_the_future():
    sales = prepare(sales_with_a_future_date())
    assert bm.available_range(sales, None) == (date(2025, 1, 5), date(2027, 12, 3))
    assert bm.default_range(sales, None, TODAY) == (date(2025, 1, 5), date(2025, 2, 25))
    assert bm.future_dated(sales, None, TODAY) == (1, date(2027, 12, 3))


def test_future_rows_are_kept_and_can_be_included_by_widening_the_range():
    sales = prepare(sales_with_a_future_date())
    assert len(sales.rows) == 5  # nothing is deleted
    start, end = bm.default_range(sales, None, TODAY)
    default = bm.compute_dashboard(sales, None, bm.Filters(start, end))
    assert default.kpis["revenue"].value == 50.0  # 10 + 40; the future-dated 30 is outside the period
    widened = bm.compute_dashboard(sales, None, bm.Filters(*bm.available_range(sales, None)))
    assert widened.kpis["revenue"].value == 80.0


def test_future_rows_note_explains_both_cases():
    excluded = bm.future_rows_note(4, date(2027, 12, 3), date(2026, 6, 30), date(2026, 6, 30))
    assert "4 row(s) are dated after today" in excluded and "03 Dec 2027" in excluded
    assert "ends on 30 Jun 2026" in excluded and "not deleted" in excluded and "Data quality" in excluded
    included = bm.future_rows_note(4, date(2027, 12, 3), date(2026, 6, 30), date(2027, 12, 3))
    assert "included in the selected period" in included
    assert bm.future_rows_note(0, None, date(2026, 6, 30), date(2026, 6, 30)) is None


def test_default_range_without_future_dates_is_the_full_range(sample_sales, sample_expenses):
    assert bm.default_range(sample_sales, sample_expenses, TODAY) == bm.available_range(sample_sales, sample_expenses)
    assert bm.future_dated(sample_sales, sample_expenses, TODAY) == (0, None)


def test_default_range_when_every_date_is_in_the_future():
    plan = small_sales().assign(Date=["2030-01-05", "2030-01-20", "2030-02-03", "2030-02-10", "2030-02-25"])
    sales = prepare(plan)
    assert bm.default_range(sales, None, TODAY) == bm.available_range(sales, None)


def test_messy_sample_default_range_leaves_out_its_four_future_orders():
    messy = prepare(load_file(*read_sample("sales_messy")).data, "sales_messy.csv")
    assert bm.available_range(messy, None)[1] == date(2027, 12, 3)
    assert bm.default_range(messy, None, TODAY) == (date(2025, 1, 1), date(2026, 6, 30))
    assert bm.future_dated(messy, None, TODAY) == (4, date(2027, 12, 3))
    result = bm.compute_dashboard(messy, None, bm.Filters(*bm.default_range(messy, None, TODAY)))
    table = bm.time_table(result, messy, None, bm.Filters(*bm.default_range(messy, None, TODAY)), "M")
    assert table["Period"].max() == pd.Timestamp(2026, 6, 1)  # no empty months up to December 2027


# ---------------------------------------------------------------------------
# Currency and formatting
# ---------------------------------------------------------------------------
def test_currency_is_never_assumed(sample_sales):
    assert sample_sales.currency is None
    assert "does not state a currency" in sample_sales.currency_note
    assert bm.format_money(1234.5, None) == "1,234.50"


def test_currency_from_column_name_or_consistent_symbol():
    assert prepare(small_sales().rename(columns={"Total": "Revenue (GBP)"})).currency == "GBP"
    table = small_sales()
    table["Total"] = ["$10", "$40", "$30", "$20", "$50"]
    assert prepare(table).currency == "$"
    table.loc[0, "Total"] = "10"  # mostly symbols but not all (4 of 5): not stated clearly enough
    assert prepare(table).currency is None


def test_formatting():
    assert bm.format_money(-2500, "EUR") == "−EUR 2,500.00"
    assert bm.format_money(None, None) == "—"
    assert bm.format_percent(12.345) == "12.3%"
    assert bm.format_count(15470.0) == "15,470"
    assert bm.format_date(pd.Timestamp(2025, 3, 5)) == "05 Mar 2025"


# ---------------------------------------------------------------------------
# Cleaned vs original, and no mutation
# ---------------------------------------------------------------------------
def test_cleaned_dataset_gives_different_figures_from_original():
    loaded = load_file(*read_sample("sales_messy"))
    session = dc.CleaningSession(loaded.data, "sig", loaded.file_name)
    session.apply(dc.propose_trim(session, dc.text_columns(session.working)))
    session.apply(dc.propose_capitalization(session, "region"))
    cleaned = session.approve()
    original = prepare(loaded.data, "sales_messy.csv", "Original upload")
    tidy = prepare(cleaned.data, "sales_messy.csv", "Cleaned dataset")
    assert tidy.version == "Cleaned dataset"
    # In the original, "north", "NORTH" and " North" are separate regions; after cleaning they are one.
    # Cleaned: the 5 real regions plus "(not specified)" for the 15 blank region cells.
    cleaned_regions = bm.dimension_options(tidy)["region"]
    assert sorted(cleaned_regions) == sorted(["Central", "East", "North", "South", "West", br.NOT_SPECIFIED])
    assert len(bm.dimension_options(original)["region"]) > len(cleaned_regions)


def test_cleaning_exclusions_are_respected():
    loaded = load_file(*read_sample("sales_messy"))
    session = dc.CleaningSession(loaded.data, "sig", loaded.file_name)
    session.apply(dc.propose_exclude_duplicates(session), confirmed=True)
    cleaned = session.approve()
    dataset = prepare(cleaned.data, "sales_messy.csv", "Cleaned dataset")
    excluded_rows = {label + 1 for label in cleaned.excluded_rows}
    assert not excluded_rows & set(dataset.rows["row"])


def test_original_data_is_never_changed():
    loaded = load_file(*read_sample("sales_messy"))
    before = loaded.data.copy(deep=True)
    dataset = prepare(loaded.data, "sales_messy.csv")
    result = bm.compute_dashboard(dataset, None, bm.Filters(regions=("North",)))
    bm.time_table(result, dataset, None, bm.Filters(), "M")
    bm.breakdown(result.revenue_rows, "product")
    pd.testing.assert_frame_equal(loaded.data, before)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def test_charts_build_and_follow_the_colour_rules(sample_sales, sample_expenses):
    result = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters())
    table = bm.time_table(result, sample_sales, sample_expenses, bm.Filters(), "M")
    line = charts.time_chart(table, "M", None)
    assert [trace.name for trace in line.data] == ["Revenue", "Expenses"]
    assert [trace.line.color for trace in line.data] == [design.REVENUE_COLOR, design.EXPENSES_COLOR]
    assert line.layout.yaxis.title.text == "Amount (currency not specified)"
    assert "yaxis2" not in line.layout.to_plotly_json()  # one axis only

    profit = charts.profit_chart(table, "M", None)
    colors = set(profit.data[0].marker.color)
    assert colors == {design.PROFIT_COLOR, design.LOSS_COLOR}  # the sample has profitable and loss-making months

    bars = charts.ranked_bar_chart(bm.breakdown(result.revenue_rows, "product"), "Revenue", None)
    assert bars.data[0].marker.color[-1] == charts.OTHERS_COLOR  # "All others" is grey
    assert set(bars.data[0].marker.color[:-1]) == {charts.BAR_COLOR}


def test_no_misleading_charts_without_data():
    empty = pd.DataFrame(columns=["Period"])
    assert charts.time_chart(empty, "M", None) is None
    assert charts.profit_chart(empty, "M", None) is None
    only_revenue = pd.DataFrame({"Period": [pd.Timestamp(2025, 1, 1)], "Revenue": [5.0]})
    assert charts.profit_chart(only_revenue, "M", None) is None
    assert br.TOP_ITEMS == 10
