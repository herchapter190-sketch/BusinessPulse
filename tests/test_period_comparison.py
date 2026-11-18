"""Stage 6.1: period comparisons (tools/period_comparison.py).

Small hand-made datasets have answers worked out by hand; the sample data checks
real-world figures and that every period figure matches the Dashboard."""

from datetime import date

import pandas as pd
import pytest

from config import business_rules as br
from tools import business_metrics as bm
from tools import period_comparison as pc
from tools.file_loader import load_file
from tools.sample_data import read_sample

TODAY = date(2026, 11, 17)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sales_rows(days, amount=100.0, status="Completed", product="Mug", region="North", category="Home"):
    """One order per day for each day in `days` (strings or Timestamps)."""
    return [{"order_date": pd.Timestamp(day).strftime("%Y-%m-%d"), "product_name": product, "category": category,
             "region": region, "quantity": 1, "unit_price": amount, "total_sales": amount, "order_status": status}
            for day in days]


def make_sales(rows, with_status=True) -> bm.PreparedDataset:
    frame = pd.DataFrame(rows)
    frame.insert(0, "order_id", [f"ORD-{i:05d}" for i in range(1, len(frame) + 1)])
    if not with_status:
        frame = frame.drop(columns=["order_status"])
    return bm.prepare_dataset(frame.astype(str), "sales.csv", "Original upload")


def make_expenses(rows) -> bm.PreparedDataset:
    frame = pd.DataFrame(rows)
    frame.insert(0, "expense_id", [f"EXP-{i:05d}" for i in range(1, len(frame) + 1)])
    return bm.prepare_dataset(frame.astype(str), "expenses.csv", "Original upload")


def days(start, end):
    return list(pd.date_range(start, end, freq="D"))


def month(text, frequency="M"):
    return pc.period_of(pd.Timestamp(text), frequency)


@pytest.fixture(scope="module")
def sample_sales():
    return bm.prepare_dataset(load_file(*read_sample("sales_clean")).data, "sales_clean.csv", "Original upload")


@pytest.fixture(scope="module")
def sample_expenses():
    return bm.prepare_dataset(load_file(*read_sample("expenses_clean")).data, "expenses_clean.csv",
                              "Original upload")


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------
def test_period_labels_and_navigation():
    june = month("2026-06-15")
    assert (june.label, june.start, june.end) == ("Jun 2026", pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-30"))
    assert june.previous().label == "May 2026"
    assert june.same_period_last_year().label == "Jun 2025"
    assert month("2026-01-10").previous().label == "Dec 2025"
    quarter = month("2026-05-01", "Q")
    assert (quarter.label, quarter.previous().label, quarter.same_period_last_year().label) == \
        ("Q2 2026", "Q1 2026", "Q2 2025")
    assert quarter.end == pd.Timestamp("2026-06-30")
    year = month("2026-03-01", "Y")
    assert (year.label, year.previous().label, year.same_period_last_year().label) == ("2026", "2025", "2025")
    assert month("2024-02-10").end == pd.Timestamp("2024-02-29")  # leap year


def test_unknown_period_size_is_rejected():
    with pytest.raises(ValueError, match="Unknown period size"):
        pc.period_of(pd.Timestamp("2026-01-01"), "W")


def test_periods_between_includes_touched_periods():
    labels = [p.label for p in pc.periods_between("2025-01-20", "2025-04-02", "M")]
    assert labels == ["Jan 2025", "Feb 2025", "Mar 2025", "Apr 2025"]


# ---------------------------------------------------------------------------
# Complete, partial and missing-data periods
# ---------------------------------------------------------------------------
def coverage(dataset, text, today=TODAY):
    return pc.assess_coverage(dataset, month(text).span, "sales", today)


def test_fully_covered_month_is_complete():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-03-31")))
    february = coverage(sales, "2025-02-01")
    assert february.status == pc.COMPLETE and february.complete
    assert february.record_days == 28 and february.longest_gap == 0


def test_first_and_last_months_cut_by_the_data_are_partial():
    sales = make_sales(sales_rows(days("2025-01-15", "2025-03-10")))
    january, february, march = (coverage(sales, d) for d in ("2025-01-01", "2025-02-01", "2025-03-01"))
    assert january.status == march.status == pc.PARTIAL
    assert "starts on 15 Jan 2025" in january.reason
    assert "ends on 10 Mar 2025" in march.reason
    assert february.complete


def test_month_with_no_recorded_sales_is_not_assumed_complete():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-01-31") + days("2025-03-01", "2025-03-31")))
    february = coverage(sales, "2025-02-01")
    assert february.status == pc.NO_RECORDS and not february.complete
    assert "can't tell whether nothing happened or the data is missing" in february.reason

    comparison = pc.compare_periods(sales, None, bm.Filters(), month("2025-03-01"), today=TODAY)
    revenue = comparison.changes["revenue"]
    assert revenue.current == 3100
    assert revenue.comparison is None                            # never shown as 0: the data may be missing
    assert revenue.change is None and not revenue.comparable
    assert "can't tell whether nothing happened or the data is missing" in revenue.note
    for key in ("orders", "units", "revenue_per_day", "returns"):
        assert comparison.comparison.values[key] is None, key
    table = pc.period_table(sales, None, bm.Filters(), "M", today=TODAY)
    assert pd.isna(table.loc[table["Period"] == "Feb 2025", "Value"]).all()


@pytest.mark.parametrize("silent_days, status", [(br.GAP_MIN_DAYS - 1, pc.COMPLETE),
                                                 (br.GAP_MIN_DAYS, pc.POSSIBLE_GAP)])
def test_long_silence_inside_a_month_is_possible_missing_data(silent_days, status):
    gap_start = pd.Timestamp("2025-02-05")
    gap = set(pd.date_range(gap_start, periods=silent_days, freq="D"))
    sales = make_sales(sales_rows([d for d in days("2025-01-01", "2025-03-31") if d not in gap]))
    february = coverage(sales, "2025-02-01")
    assert february.status == status
    assert february.longest_gap == silent_days
    if status == pc.POSSIBLE_GAP:
        assert f"No sales records for {silent_days} days in a row (05 Feb 2025" in february.reason


def test_files_with_naturally_sparse_records_are_not_flagged():
    """Weekly records: 6 quiet days is normal, so only much longer silences count."""
    sales = make_sales(sales_rows(pd.date_range("2025-01-01", "2025-06-30", freq="7D")))
    assert pc.gap_threshold(pc.record_dates(sales)) == max(br.GAP_MIN_DAYS, br.GAP_MULTIPLIER * 6 + 1)
    assert coverage(sales, "2025-03-01").complete


def test_period_that_has_not_finished_is_not_complete():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-03-15")))
    march = coverage(sales, "2025-03-01", today=date(2025, 3, 15))
    assert march.status == pc.NOT_FINISHED
    assert "has not finished yet" in march.reason


def test_period_outside_the_data_has_no_figures_not_zero():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-06-30")))
    comparison = pc.compare_periods(sales, None, bm.Filters(), month("2025-06-01"), pc.MODE_LAST_YEAR, today=TODAY)
    assert comparison.comparison.coverage["sales"].status == pc.OUTSIDE
    revenue = comparison.changes["revenue"]
    assert revenue.comparison is None and revenue.change is None
    assert "covers 01 Jan 2025 – 30 Jun 2025, so there is no data for Jun 2024" in revenue.note
    assert [text for _, text in comparison.coverage_notes] == [
        "Jun 2024 (sales): No data. The sales data covers 01 Jan 2025 – 30 Jun 2025, so there is no data for "
        "Jun 2024."]
    assert all(note in comparison.notes for note in comparison.coverage_notes)
    assert not any(note in comparison.other_notes for note in comparison.coverage_notes)
    table = pc.period_table(sales, None, bm.Filters(), "M", today=TODAY)
    assert table["Same period last year"].isna().all()     # never shown as 0


def test_default_period_is_the_latest_complete_one():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-04-12")))
    options = pc.available_periods(sales, None, "M", today=TODAY)
    assert [o.period.label for o in options] == ["Jan 2025", "Feb 2025", "Mar 2025", "Apr 2025"]
    assert not options[-1].complete
    assert pc.default_period(options).label == "Mar 2025"


def test_window_limits_the_periods_offered():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-06-30")))
    options = pc.available_periods(sales, None, "M", (date(2025, 3, 10), date(2025, 4, 20)), TODAY)
    assert [o.period.label for o in options] == ["Mar 2025", "Apr 2025"]
    assert all(o.complete for o in options)   # whole calendar months are compared


# ---------------------------------------------------------------------------
# Changes worked out by hand
# ---------------------------------------------------------------------------
def test_changes_worked_out_by_hand():
    # January: 31 orders × 100 = 3,100. February: 28 orders × 150 = 4,200.
    sales = make_sales(sales_rows(days("2025-01-01", "2025-01-31"), 100.0)
                       + sales_rows(days("2025-02-01", "2025-02-28"), 150.0)
                       + sales_rows(days("2025-03-01", "2025-03-05"), 100.0))
    result = pc.compare_periods(sales, None, bm.Filters(), month("2025-02-01"), today=TODAY)
    revenue = result.changes["revenue"]
    assert (revenue.current, revenue.comparison, revenue.change) == (4200, 3100, 1100)
    assert revenue.change_pct == pytest.approx(1100 / 3100 * 100)
    assert pc.format_pct_change(revenue.change_pct) == "+35.5%"
    assert "4,200.00 − 3,100.00 = 1,100.00" in revenue.formula and "+35.5%" in revenue.formula
    orders = result.changes["orders"]
    assert (orders.current, orders.comparison, orders.change) == (28, 31, -3)
    per_day = result.changes["revenue_per_day"]
    assert (per_day.current, per_day.comparison) == (150, 100) and per_day.change_pct == pytest.approx(50)
    assert any("Feb 2025 has 28 days and Jan 2025 has 31" in text for _, text in result.notes)


def test_percent_change_needs_a_positive_earlier_value():
    zero = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 500.0, 0.0, None)
    assert zero.change == 500 and zero.change_pct is None and "earlier value is 0" in zero.note
    loss = pc.compute_change("profit", "Net profit", bm.KIND_MONEY, 200.0, -100.0, None)
    assert loss.change == 300 and loss.change_pct is None and "negative (a loss)" in loss.note
    fall = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 75.0, 100.0, "EUR")
    assert fall.change_pct == pytest.approx(-25) and pc.format_pct_change(fall.change_pct) == "−25.0%"


def test_margin_change_is_in_percentage_points():
    margin = pc.compute_change("margin", "Profit margin", bm.KIND_PERCENT, 12.5, 10.0, None)
    assert margin.change == pytest.approx(2.5) and margin.change_pct is None
    assert pc.format_points(margin.change) == "+2.5 pp"
    assert "12.5% − 10.0% = +2.5 pp" in margin.formula


def test_blocked_change_keeps_both_values():
    change = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 10.0, 20.0, None, "Partial period.")
    assert (change.current, change.comparison, change.change, change.comparable) == (10, 20, None, False)
    assert change.note == "Partial period."


# ---------------------------------------------------------------------------
# Return and cancellation rates
# ---------------------------------------------------------------------------
def status_month(statuses):
    """One order per day in March 2025 with the given statuses, plus full Feb/Apr so March is complete."""
    rows = sales_rows(days("2025-02-01", "2025-02-28"))
    march = days("2025-03-01", "2025-03-31")
    for index, status in enumerate(statuses):
        rows += sales_rows([march[index % len(march)]], status=status)
    covered = {pd.Timestamp(r["order_date"]) for r in rows}
    rows += sales_rows([d for d in march if d not in covered]) if len(statuses) < 31 else []
    return rows + sales_rows(days("2025-04-01", "2025-04-30"))


def rates_for_march(sales):
    result = pc.compare_periods(sales, None, bm.Filters(), month("2025-03-01"), today=TODAY)
    return result.current.rates


def test_cancellation_and_return_rates_worked_out_by_hand():
    # 8 completed + 1 returned + 1 cancelled + 1 pending in March (plus fillers on other days).
    statuses = ["Completed"] * 8 + ["Returned", "Cancelled", "Pending"]
    sales = make_sales(status_month(statuses))
    march = sales.rows[(sales.rows["date"] >= "2025-03-01") & (sales.rows["date"] <= "2025-03-31")]
    completed = int((march["status_group"] == br.STATUS_REVENUE).sum())   # 8 + 20 filler days
    rates = rates_for_march(sales)
    cancel = rates["cancellation_rate"]
    assert cancel.value == pytest.approx(1 / (completed + 3) * 100)
    assert f"1 cancelled ÷ {completed + 3} orders with a recognised status" in cancel.calculation
    returns = rates["return_rate"]
    assert returns.value == pytest.approx(1 / (completed + 1) * 100)
    assert "counted in the period the order was placed" in returns.definition


def test_rates_unavailable_without_a_status_column():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-03-31")), with_status=False)
    rates = rates_for_march(sales)
    for rate in rates.values():
        assert rate.value is None and "No order-status column" in rate.note


def test_cancellation_rate_unavailable_when_the_file_never_records_cancellations():
    sales = make_sales(status_month(["Completed"] * 10 + ["Returned"]))
    rates = rates_for_march(sales)
    assert rates["cancellation_rate"].value is None
    assert "None of the statuses in this file mean 'cancelled'" in rates["cancellation_rate"].note
    assert "Completed" in rates["cancellation_rate"].note
    assert rates["return_rate"].available


def test_rates_unavailable_when_too_many_statuses_are_unrecognised():
    statuses = ["Completed"] * 24 + ["Cancelled", "Returned"] + ["TBD"] * 5   # 5 of 31 = 16%
    rates = rates_for_march(make_sales(status_month(statuses)))
    assert rates["cancellation_rate"].value is None
    assert "5 of 31 orders (16.1%)" in rates["cancellation_rate"].note


def test_a_few_unrecognised_statuses_are_left_out_and_reported():
    statuses = ["Completed"] * 28 + ["Cancelled", "Returned", "TBD"]           # 1 of 31 = 3.2%
    rate = rates_for_march(make_sales(status_month(statuses)))["cancellation_rate"]
    assert rate.value == pytest.approx(1 / 30 * 100)
    assert "1 order(s) with an unrecognised status are left out" in rate.note


# ---------------------------------------------------------------------------
# Expense growth vs revenue growth
# ---------------------------------------------------------------------------
def expense_rows(day_amounts):
    return [{"expense_date": pd.Timestamp(d).strftime("%Y-%m-%d"), "category": "Rent", "amount": a}
            for d, a in day_amounts]


def growth_case():
    # Revenue: Jan 31 × 100 = 3,100; Feb 28 × 100 = 2,800... use per-day amounts to get round numbers.
    sales = make_sales(sales_rows(days("2025-01-01", "2025-01-31"), 1000 / 31)
                       + sales_rows(days("2025-02-01", "2025-02-28"), 1100 / 28)
                       + sales_rows(days("2025-03-01", "2025-03-03"), 10))
    expenses = make_expenses(expense_rows([(d, 500 / 31) for d in days("2025-01-01", "2025-01-31")]
                                          + [(d, 600 / 28) for d in days("2025-02-01", "2025-02-28")]
                                          + [(d, 1) for d in days("2025-03-01", "2025-03-03")]))
    return sales, expenses


def test_growth_gap_worked_out_by_hand():
    sales, expenses = growth_case()
    result = pc.compare_periods(sales, expenses, bm.Filters(), month("2025-02-01"), today=TODAY)
    gap = result.growth
    assert gap.revenue_growth == pytest.approx(10, abs=0.01)    # 1,000 -> 1,100
    assert gap.expense_growth == pytest.approx(20, abs=0.01)    # 500 -> 600
    assert gap.gap == pytest.approx(10, abs=0.02)
    assert "growth +20.0% − revenue growth +10.0% = +10.0 pp" in gap.calculation
    assert "grew faster than revenue" in gap.meaning


def test_growth_gap_unavailable_cases():
    sales, expenses = growth_case()
    february = month("2025-02-01")
    filtered = pc.compare_periods(sales, expenses, bm.Filters(regions=("North",)), february, today=TODAY).growth
    assert not filtered.available and "not recorded per product, category or region" in filtered.note

    no_expenses = pc.compare_periods(sales, None, bm.Filters(), february, today=TODAY).growth
    assert not no_expenses.available and "Needs expense data" in no_expenses.note

    partial = pc.compare_periods(sales, expenses, bm.Filters(), month("2025-03-01"), today=TODAY).growth
    assert not partial.available and "can't be calculated" in partial.note

    # An expense file that doesn't reach the periods: the reason is given, not "add an expense file".
    short_expenses = make_expenses(expense_rows([(d, 10) for d in days("2025-01-01", "2025-01-31")]))
    outside = pc.compare_periods(sales, short_expenses, bm.Filters(), month("2025-03-01"), today=TODAY).growth
    assert not outside.available
    assert "no total expenses figures for either period" in outside.note
    assert "covers 01 Jan 2025 – 31 Jan 2025" in outside.note and "add an expense file" not in outside.note

    zero = pc.growth_gap(pc.compute_change("revenue", "Total revenue", bm.KIND_MONEY, 10.0, 5.0, None),
                         pc.compute_change("expenses", "Total expenses", bm.KIND_MONEY, 10.0, 0.0, None),
                         True, False)
    assert not zero.available and "earlier value is 0" in zero.note


# ---------------------------------------------------------------------------
# Filters, like-for-like and missing dates
# ---------------------------------------------------------------------------
def test_filters_apply_to_both_periods_but_not_to_completeness(sample_sales, sample_expenses):
    filters = bm.Filters(regions=("North",))
    quarter = month("2026-04-01", "Q")
    result = pc.compare_periods(sample_sales, sample_expenses, filters, quarter, today=TODAY)
    for side, period in ((result.current, quarter), (result.comparison, quarter.previous())):
        expected = bm.compute_dashboard(sample_sales, sample_expenses,
                                        bm.Filters(period.start.date(), period.end.date(), regions=("North",)))
        assert side.values["revenue"] == pytest.approx(expected.kpis["revenue"].value)
        assert side.complete
    assert result.changes["profit"].current is None
    assert "Not shown while filtering" in result.changes["profit"].note


def test_product_that_did_not_sell_is_a_real_zero_in_a_complete_period():
    rows = sales_rows(days("2025-01-01", "2025-03-31"), product="Mug") + \
        sales_rows(days("2025-01-01", "2025-01-31"), product="Lamp")
    sales = make_sales(rows)
    result = pc.compare_periods(sales, None, bm.Filters(products=("Lamp",)), month("2025-02-01"), today=TODAY)
    revenue = result.changes["revenue"]
    assert result.current.complete and revenue.current == 0 and revenue.comparison == 3100
    assert pc.format_pct_change(revenue.change_pct) == "−100.0%"


def test_to_date_compares_the_same_days():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-03-17")))
    march = month("2025-03-01")
    whole = pc.compare_periods(sales, None, bm.Filters(), march, today=TODAY)
    assert whole.changes["revenue"].change is None                 # March is partial
    to_date = pc.compare_periods(sales, None, bm.Filters(), march, to_date=True, today=TODAY)
    assert (to_date.current.span.label, to_date.comparison.span.label) == ("1–17 Mar 2025", "1–17 Feb 2025")
    revenue = to_date.changes["revenue"]
    assert revenue.current == revenue.comparison == 1700 and revenue.change == 0
    assert any("Like-for-like" in text for _, text in to_date.notes)


def test_to_date_comparison_is_clipped_to_the_shorter_month():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-03-30")))
    result = pc.compare_periods(sales, None, bm.Filters(), month("2025-03-01"), to_date=True, today=TODAY)
    assert (result.current.span.label, result.comparison.span.label) == ("1–30 Mar 2025", "1–28 Feb 2025")


def test_no_dates_means_no_comparison():
    frame = pd.DataFrame({"product_name": ["Mug", "Lamp"], "total_sales": ["10", "20"]})
    sales = bm.prepare_dataset(frame, "s.csv", "Original upload")
    assert "Comparisons need dates" in pc.comparison_unavailable_reason(sales, None)
    assert pc.available_periods(sales, None, "M") == []
    assert pc.period_table(sales, None, bm.Filters(), "M").empty
    with pytest.raises(ValueError, match="need dates"):
        pc.compare_periods(sales, None, bm.Filters(), month("2025-01-01"))
    assert "nothing to compare" in pc.comparison_unavailable_reason(None, None)


# ---------------------------------------------------------------------------
# Display helpers and the period chart (Stage 6.2)
# ---------------------------------------------------------------------------
def test_change_texts_for_cards():
    money = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 84343.89, 71827.65, None)
    assert pc.change_text(money, None) == "+17.4% (+12,516.24)" and pc.change_direction(money) == "up"
    fall = pc.compute_change("orders", "Orders", bm.KIND_COUNT, 356.0, 478.0, None)
    assert pc.change_text(fall, None) == "−25.5% (−122)" and pc.change_direction(fall) == "down"
    points = pc.compute_change("margin", "Margin", bm.KIND_PERCENT, 2.55, 28.59, None)
    assert pc.change_text(points, None) == "−26.0 pp"
    from_zero = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 50.0, 0.0, "EUR")
    assert pc.change_text(from_zero, "EUR") == "+EUR 50.00"       # no % when the earlier value is 0
    same = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 10.0, 10.0, None)
    assert pc.change_direction(same) == "off"
    blocked = pc.compute_change("revenue", "Revenue", bm.KIND_MONEY, 10.0, 5.0, None, "Partial.")
    assert pc.change_text(blocked, None) is None and pc.change_direction(blocked) == "off"
    assert pc.format_signed(None, bm.KIND_MONEY, None) == "—"
    assert pc.format_value(12.345, bm.KIND_PERCENT, None) == "12.3%"


def test_every_figure_explains_its_calculation(sample_sales, sample_expenses):
    result = pc.compare_periods(sample_sales, sample_expenses, bm.Filters(), month("2026-04-01", "Q"), today=TODAY)
    for key in result.changes:
        assert pc.metric_definition(result.current, key), key
        assert pc.metric_calculation(result.current, key), key
    assert "÷ 91 day(s)" in pc.metric_calculation(result.current, "revenue_per_day")
    outside = pc.compare_periods(sample_sales, None, bm.Filters(), month("2025-03-01"), pc.MODE_LAST_YEAR,
                                 today=TODAY)
    assert pc.metric_calculation(outside.comparison, "revenue").startswith("Not available: The sales data covers")


def test_incomplete_sides_names_the_periods():
    sales = make_sales(sales_rows(days("2025-01-01", "2025-03-17")))
    result = pc.compare_periods(sales, None, bm.Filters(), month("2025-03-01"), today=TODAY)
    assert pc.incomplete_sides(result, "revenue") == ["Mar 2025: partial period"]


def test_period_chart_marks_incomplete_periods_without_relying_on_colour():
    from config import design
    from tools import charts
    table = pd.DataFrame({"Period": ["Jan 2025", "Feb 2025", "Mar 2025", "Apr 2025"],
                          "Value": [100.0, None, 120.0, 40.0],
                          "Status": ["Complete", "No data", "Complete", "Partial period"],
                          "Complete": [True, False, True, False]})
    fig = charts.period_chart(table, "Revenue", bm.KIND_MONEY, None, design.REVENUE_COLOR,
                              {"Mar 2025": "This period", "Jan 2025": "Compared with"})
    bar = fig.data[0]
    assert list(bar.marker.pattern.shape) == ["", "/", "", "/"]          # hatched = not complete
    assert set(bar.marker.color) == {design.REVENUE_COLOR}               # colour follows the entity
    assert pd.isna(bar.y[1])                                             # no data: no bar, never 0
    assert "(no figure)" in bar.customdata[1]
    assert {(a.text, a.x) for a in fig.layout.annotations} == {("This period", 2), ("Compared with", 0)}
    # Year labels look like numbers: annotations must still point at bar positions, not at "2025".
    years = pd.DataFrame({"Period": ["2025", "2026"], "Value": [300.0, 150.0], "Status": ["Complete", "Not finished"],
                          "Complete": [True, False]})
    year_fig = charts.period_chart(years, "Revenue", bm.KIND_MONEY, None, design.REVENUE_COLOR,
                                   {"2026": "This period", "2025": "Compared with"})
    assert year_fig.layout.xaxis.type == "category"
    assert sorted(a.x for a in year_fig.layout.annotations) == [0, 1]
    profit = charts.period_chart(table.assign(Value=[100.0, None, -20.0, 5.0]), "Net profit", bm.KIND_MONEY, None,
                                 None)
    assert list(profit.data[0].marker.color) == [design.PROFIT_COLOR, design.PROFIT_COLOR, design.LOSS_COLOR,
                                                 design.PROFIT_COLOR]
    assert charts.period_chart(table.assign(Value=[None] * 4), "Revenue", bm.KIND_MONEY, None, "#000") is None


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------
def test_every_sample_month_is_complete(sample_sales, sample_expenses):
    options = pc.available_periods(sample_sales, sample_expenses, "M", today=TODAY)
    assert len(options) == 18 and all(o.complete for o in options)
    assert pc.default_period(options).label == "Jun 2026"


@pytest.mark.parametrize("mode, comparison_label, expected", [
    (pc.MODE_PREVIOUS, "Q1 2026", "+17.4%"),
    (pc.MODE_LAST_YEAR, "Q2 2025", "+14.0%"),
])
def test_sample_quarter_comparisons(sample_sales, sample_expenses, mode, comparison_label, expected):
    result = pc.compare_periods(sample_sales, sample_expenses, bm.Filters(), month("2026-04-01", "Q"), mode,
                                today=TODAY)
    assert result.comparison.span.label == comparison_label
    revenue = result.changes["revenue"]
    assert revenue.current == pytest.approx(84343.89)
    assert pc.format_pct_change(revenue.change_pct) == expected
    assert result.growth.available


def test_sample_figures_match_the_dashboard(sample_sales, sample_expenses):
    """Every figure comes from compute_dashboard(): the same dates give the same numbers."""
    result = pc.compare_periods(sample_sales, sample_expenses, bm.Filters(), month("2025-11-01"), today=TODAY)
    expected = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters(date(2025, 11, 1), date(2025, 11, 30)))
    for key, kpi in expected.kpis.items():
        assert result.current.values[key] == kpi.value, key
    table = pc.period_table(sample_sales, sample_expenses, bm.Filters(), "M", today=TODAY)
    total = bm.compute_dashboard(sample_sales, sample_expenses, bm.Filters()).kpis["revenue"].value
    assert table["Value"].sum() == pytest.approx(total)


def test_sample_june_pending_orders_are_flagged(sample_sales, sample_expenses):
    result = pc.compare_periods(sample_sales, sample_expenses, bm.Filters(), month("2026-06-01"), today=TODAY)
    assert result.current.pending_orders == 54
    assert any("54 pending order(s)" in text and "not counted as revenue" in text for _, text in result.notes)
    counted = pc.compare_periods(sample_sales, sample_expenses, bm.Filters(), month("2026-06-01"),
                                 include_pending=True, today=TODAY)
    assert counted.current.pending_orders == 0
    assert counted.changes["revenue"].current > result.changes["revenue"].current


def test_sample_rates_and_period_table(sample_sales, sample_expenses):
    result = pc.compare_periods(sample_sales, sample_expenses, bm.Filters(), month("2026-04-01", "Q"), today=TODAY)
    assert result.current.rates["cancellation_rate"].available
    assert result.changes["return_rate"].is_points
    table = pc.period_table(sample_sales, sample_expenses, bm.Filters(), "Q", today=TODAY)
    assert list(table["Period"]) == ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026"]
    assert pd.isna(table["% vs previous"].iloc[0])           # no Q4 2024 in the data
    assert table["% vs last year"].iloc[:4].isna().all()     # no 2024 in the data
    assert table["% vs last year"].iloc[5] == pytest.approx(84343.89 / 74005.65 * 100 - 100)


def test_messy_sample_future_typos_do_not_create_complete_periods():
    messy = bm.prepare_dataset(load_file(*read_sample("sales_messy")).data, "sales_messy.csv", "Original upload")
    options = pc.available_periods(messy, None, "M", today=TODAY)
    after = [o for o in options if o.period.start > pd.Timestamp("2026-06-30")]
    assert after and not any(o.complete for o in after)
    assert {o.coverage["sales"].status for o in after} <= {pc.NO_RECORDS, pc.POSSIBLE_GAP, pc.NOT_FINISHED}
    assert pc.default_period(options).label == "Jun 2026"
