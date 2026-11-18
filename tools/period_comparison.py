"""Period comparisons: month-over-month, quarter-over-quarter and year-over-year.

Principles (no Streamlit code here):
- Every figure for a period comes from tools.business_metrics.compute_dashboard()
  run with that period's dates, so a month's revenue here is exactly what the
  Dashboard shows when its date filter is set to that month.
- A change is only calculated between two COMPLETE periods (see assess_coverage).
  Partial periods, periods with no records and periods with possible missing data
  still show their figures, clearly marked, but never a change.
- Nothing is invented: a value that can't be calculated is None, with the reason.
- Product/category/region filters apply to both periods in the same way. Whether a
  period is complete is judged on the whole file, before those filters, because a
  product not selling in a month is a real result, not missing data.

Formulas
- Change           = current − comparison
- % change         = (current − comparison) ÷ comparison × 100, only when the
                     comparison value is above 0
- Margin change    = current margin − comparison margin, in percentage points (pp)
- Revenue per day  = revenue ÷ days of the period covered by the data
- Return rate      = returned orders ÷ (completed + returned orders) × 100
- Cancellation rate = cancelled orders ÷ orders with a recognised status × 100
- Growth gap       = expense % change − revenue % change, in percentage points
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from config import business_rules as br
from config.quality_rules import QualityRules
from tools import business_metrics as bm

FREQUENCIES = {"M": "Month", "Q": "Quarter", "Y": "Year"}
PERIODS_PER_YEAR = {"M": 12, "Q": 4, "Y": 1}
MODE_PREVIOUS = "previous"
MODE_LAST_YEAR = "last_year"
MODE_LABELS = {MODE_PREVIOUS: "Previous period", MODE_LAST_YEAR: "Same period last year"}

# Coverage statuses, from worst to best.
OUTSIDE = "outside"            # the file has no data for any day of the period
NO_RECORDS = "no_records"      # inside the file's dates, but not a single record
NOT_FINISHED = "not_finished"  # the period ends after today
PARTIAL = "partial"            # the file starts or ends part-way through the period
POSSIBLE_GAP = "possible_gap"  # an unusually long stretch without records
COMPLETE = "complete"
STATUS_LABELS = {OUTSIDE: "No data", NO_RECORDS: "No records", NOT_FINISHED: "Not finished",
                 PARTIAL: "Partial period", POSSIBLE_GAP: "Possible missing data", COMPLETE: "Complete"}

# Which datasets each figure depends on (only the loaded ones are checked).
METRIC_SOURCES = {
    "revenue": ("sales",), "orders": ("sales",), "units": ("sales",), "aov": ("sales",), "returns": ("sales",),
    "revenue_per_day": ("sales",), "return_rate": ("sales",), "cancellation_rate": ("sales",),
    "expenses": ("expenses",), "profit": ("sales", "expenses"), "margin": ("sales", "expenses"),
}
METRIC_ORDER = ("revenue", "expenses", "profit", "margin", "orders", "units", "aov", "revenue_per_day",
                "returns", "return_rate", "cancellation_rate")


# ---------------------------------------------------------------------------
# Periods and spans
# ---------------------------------------------------------------------------
def _day(value) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def span_label(start, end) -> str:
    start, end = _day(start), _day(end)
    if start == end:
        return start.strftime("%d %b %Y").lstrip("0")
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {end.strftime('%b %Y')}"
    if start.year == end.year:
        return f"{start.day} {start.strftime('%b')} – {end.day} {end.strftime('%b %Y')}"
    return f"{start.day} {start.strftime('%b %Y')} – {end.day} {end.strftime('%b %Y')}"


@dataclass(frozen=True)
class Span:
    """A run of whole days, first and last day included."""
    start: pd.Timestamp
    end: pd.Timestamp
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Period:
    """A calendar month, quarter or year."""
    frequency: str      # "M", "Q" or "Y"
    start: pd.Timestamp
    end: pd.Timestamp   # last day, included

    @property
    def label(self) -> str:
        if self.frequency == "M":
            return self.start.strftime("%b %Y")
        if self.frequency == "Q":
            return f"Q{self.start.quarter} {self.start.year}"
        return str(self.start.year)

    def shift(self, count: int) -> Period:
        return period_of(pd.Period(self.start, freq=self.frequency) + count, self.frequency)

    def previous(self) -> Period:
        return self.shift(-1)

    def same_period_last_year(self) -> Period:
        return self.shift(-PERIODS_PER_YEAR[self.frequency])

    def comparison(self, mode: str) -> Period:
        return self.previous() if mode == MODE_PREVIOUS else self.same_period_last_year()

    @property
    def span(self) -> Span:
        return Span(self.start, self.end, self.label)


def period_of(value, frequency: str) -> Period:
    """The calendar period that contains a date (or a pandas Period)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown period size '{frequency}'. Use one of: {', '.join(FREQUENCIES)}.")
    period = value if isinstance(value, pd.Period) else pd.Period(_day(value), freq=frequency)
    return Period(frequency, period.start_time.normalize(), period.end_time.normalize())


def periods_between(start, end, frequency: str) -> list[Period]:
    """Every calendar period that touches the dates start … end, oldest first."""
    first, last = period_of(start, frequency), period_of(end, frequency)
    periods, current = [], first
    while current.start <= last.start:
        periods.append(current)
        current = current.shift(1)
    return periods


# ---------------------------------------------------------------------------
# Coverage: is a period complete?
# ---------------------------------------------------------------------------
@dataclass
class Coverage:
    """How well one dataset covers one span of days."""
    dataset: str                       # "sales" or "expenses"
    span: Span
    status: str
    used_start: pd.Timestamp | None    # the days of the span that fall inside the file's dates
    used_end: pd.Timestamp | None
    record_days: int                   # days with at least one record
    longest_gap: int                   # longest run of days without records (inside the used days)
    gap_threshold: int
    reasons: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status == COMPLETE

    @property
    def used_days(self) -> int:
        return 0 if self.used_start is None else (self.used_end - self.used_start).days + 1

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def reason(self) -> str:
        return " ".join(self.reasons)


def record_dates(dataset: bm.PreparedDataset) -> np.ndarray:
    """Distinct days with at least one usable record (any status, before filters)."""
    if not dataset.has_dates:
        return np.array([], dtype="datetime64[ns]")
    return np.sort(dataset.rows["date"].dropna().dt.normalize().unique())


def gap_threshold(dates: np.ndarray) -> int:
    """How many days in a row without records count as possible missing data for this
    file: at least GAP_MIN_DAYS, and more than GAP_MULTIPLIER × its usual quiet
    stretch (the GAP_PERCENTILE-th percentile of the empty days between records)."""
    if len(dates) < 2:
        return br.GAP_MIN_DAYS
    quiet = np.diff(dates).astype("timedelta64[D]").astype(int) - 1
    usual = int(np.percentile(quiet, br.GAP_PERCENTILE, method="higher"))
    return max(br.GAP_MIN_DAYS, br.GAP_MULTIPLIER * usual + 1)


def assess_coverage(dataset: bm.PreparedDataset, span: Span, name: str, today: date | None = None,
                    dates: np.ndarray | None = None) -> Coverage:
    """Classify a span for one dataset. A span is COMPLETE only when the file covers
    every day of it, the span has ended, and there is no unusually long stretch of
    days without records. A span with no records at all is never complete: the data
    can't tell 'no sales' apart from 'missing data'."""
    today = _day(today or QualityRules().today())
    dates = record_dates(dataset) if dates is None else dates
    threshold = gap_threshold(dates)
    if not len(dates):
        return Coverage(name, span, OUTSIDE, None, None, 0, 0, threshold,
                        [f"The {name} data has no usable dates."])
    first, last = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    if span.end < first or span.start > last:
        return Coverage(name, span, OUTSIDE, None, None, 0, 0, threshold,
                        [f"The {name} data covers {bm.format_date(first)} – {bm.format_date(last)}, so there is no "
                         f"data for {span.label}."])

    used_start, used_end = max(span.start, first), min(span.end, last)
    inside = dates[(dates >= np.datetime64(used_start)) & (dates <= np.datetime64(used_end))]
    # Longest run of empty days between used_start and used_end (edges included).
    edges = np.concatenate([[np.datetime64(used_start - pd.Timedelta(days=1))], inside,
                            [np.datetime64(used_end + pd.Timedelta(days=1))]]).astype("datetime64[D]")
    runs = np.diff(edges).astype(int) - 1
    longest = int(runs.max()) if len(runs) else 0
    reasons: list[str] = []

    if not len(inside):
        reasons.append(f"There are no {name} records from {bm.format_date(used_start)} to {bm.format_date(used_end)}, "
                       "although the file has data before and after. BusinessPulse can't tell whether nothing "
                       "happened or the data is missing, so this period is not treated as complete.")
        return Coverage(name, span, NO_RECORDS, used_start, used_end, 0, longest, threshold, reasons)

    status = COMPLETE
    if span.end > today:
        status = NOT_FINISHED
        reasons.append(f"{span.label} has not finished yet (today is {bm.format_date(today)}).")
    if used_start > span.start or used_end < span.end:
        status = status if status != COMPLETE else PARTIAL
        if used_start > span.start:
            reasons.append(f"The {name} data starts on {bm.format_date(first)}, part-way through {span.label}.")
        if used_end < span.end:
            reasons.append(f"The {name} data ends on {bm.format_date(last)}, part-way through {span.label}.")
    if longest >= threshold:
        status = status if status != COMPLETE else POSSIBLE_GAP
        position = int(np.argmax(runs))
        gap_from = pd.Timestamp(edges[position]) + pd.Timedelta(days=1)
        gap_to = pd.Timestamp(edges[position + 1]) - pd.Timedelta(days=1)
        reasons.append(f"No {name} records for {longest} days in a row ({bm.format_date(gap_from)} – "
                       f"{bm.format_date(gap_to)}). That is unusually long for this file ({threshold}+ days counts "
                       "as possible missing data), so this period is not treated as complete.")
    return Coverage(name, span, status, used_start, used_end, len(inside), longest, threshold, reasons)


def _datasets(sales, expenses) -> dict[str, bm.PreparedDataset]:
    return {name: d for name, d in (("sales", sales), ("expenses", expenses)) if d is not None and d.has_dates}


def comparison_unavailable_reason(sales: bm.PreparedDataset | None,
                                  expenses: bm.PreparedDataset | None) -> str | None:
    """Why no period comparison is possible at all, or None when it is."""
    if sales is None and expenses is None:
        return "No sales or expense amounts were found, so there is nothing to compare."
    if not _datasets(sales, expenses):
        return ("Comparisons need dates, and no usable date column was found. Check Column recognition on the "
                "Data quality page, or convert the dates on the Data cleaning page.")
    return None


@dataclass
class PeriodOption:
    period: Period
    coverage: dict[str, Coverage]

    @property
    def complete(self) -> bool:
        return all(c.complete for c in self.coverage.values())

    @property
    def status_text(self) -> str:
        labels = {c.label for c in self.coverage.values()}
        if len(labels) == 1:
            return labels.pop()
        return " · ".join(f"{name.capitalize()}: {c.label.lower()}" for name, c in self.coverage.items())


def available_periods(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, frequency: str,
                      window: tuple[date, date] | None = None, today: date | None = None) -> list[PeriodOption]:
    """Every period from the first to the last date in the data (only those touching
    `window` when given), with each dataset's coverage. Oldest first."""
    datasets = _datasets(sales, expenses)
    if not datasets:
        return []
    dates = {name: record_dates(d) for name, d in datasets.items()}
    start = min(pd.Timestamp(d[0]) for d in dates.values())
    end = max(pd.Timestamp(d[-1]) for d in dates.values())
    if window:
        start, end = max(start, _day(window[0])), min(end, _day(window[1]))
        if start > end:
            return []
    return [PeriodOption(period, {name: assess_coverage(datasets[name], period.span, name, today, dates[name])
                                  for name in datasets})
            for period in periods_between(start, end, frequency)]


def default_period(options: list[PeriodOption]) -> Period | None:
    """The latest period that is complete for every loaded dataset; failing that, the
    latest complete for the sales data; failing that, the latest period."""
    if not options:
        return None
    for test in (lambda o: o.complete, lambda o: "sales" in o.coverage and o.coverage["sales"].complete):
        found = [o for o in options if test(o)]
        if found:
            return found[-1].period
    return options[-1].period


# ---------------------------------------------------------------------------
# Order-status rates
# ---------------------------------------------------------------------------
@dataclass
class Rate:
    key: str
    label: str
    value: float | None
    definition: str
    calculation: str = ""
    note: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None


def _orders_by_group(rows: pd.DataFrame) -> pd.Series:
    """Orders per status group: different order IDs when there is an ID column, rows otherwise."""
    if rows.empty:
        return pd.Series(dtype=float)
    if "order_id" in rows.columns:
        return rows.dropna(subset=["order_id"]).groupby("status_group")["order_id"].nunique()
    return rows.groupby("status_group").size()


def _status_vocabulary(dataset: bm.PreparedDataset) -> tuple[set[str], list[str]]:
    """The status groups present in the whole file, and the status values found."""
    if "status" not in dataset.rows.columns:
        return set(), []
    return set(dataset.rows["status_group"].unique()), sorted(dataset.rows["status"].unique(), key=str)


def status_rate(sales: bm.PreparedDataset | None, sales_rows: pd.DataFrame, which: str) -> Rate:
    """Return rate or cancellation rate for the given (already filtered) sales rows.

    Shown only when statuses can be identified reliably:
    1. the file has a recognised order-status column;
    2. at least one status in the file means 'returned' (or 'cancelled'), so a 0% is
       a real 0% and not a file that simply never records them;
    3. at most MAX_UNRECOGNISED_STATUS_SHARE of the period's orders have a status
       BusinessPulse doesn't recognise.
    """
    group = br.STATUS_RETURNED if which == "return_rate" else br.STATUS_CANCELLED
    noun = "returned" if which == "return_rate" else "cancelled"
    if which == "return_rate":
        label, definition = "Return rate", ("Share of fulfilled orders that were returned or refunded. Returns "
                                            "are counted in the period the order was placed, because the data "
                                            "has no return date.")
    else:
        label, definition = "Cancellation rate", ("Share of the orders placed in the period that were cancelled "
                                                  "(pending orders included, because they were placed).")
    if sales is None:
        return Rate(which, label, None, definition, note="No sales data.")
    groups, values = _status_vocabulary(sales)
    if not groups or groups == {br.STATUS_NONE}:
        return Rate(which, label, None, definition,
                    note=f"No order-status column was recognised, so {noun} orders can't be identified.")
    if group not in groups:
        shown = ", ".join(values[:6]) + ("…" if len(values) > 6 else "")
        return Rate(which, label, None, definition,
                    note=f"None of the statuses in this file mean '{noun}' (found: {shown}), so BusinessPulse "
                         f"can't tell whether {noun} orders are recorded at all.")
    counts = _orders_by_group(sales_rows)
    count = lambda name: int(counts.get(name, 0))  # noqa: E731
    recognised = sum(count(name) for name in (br.STATUS_REVENUE, br.STATUS_PENDING, br.STATUS_RETURNED,
                                              br.STATUS_CANCELLED))
    unrecognised = count(br.STATUS_UNKNOWN)
    if recognised + unrecognised == 0:
        return Rate(which, label, None, definition, note="There are no orders in this period.")
    share = unrecognised / (recognised + unrecognised)
    if share > br.MAX_UNRECOGNISED_STATUS_SHARE:
        return Rate(which, label, None, definition,
                    note=f"{unrecognised:,} of {recognised + unrecognised:,} orders ({share * 100:.1f}%) have a "
                         f"status BusinessPulse doesn't recognise (more than the "
                         f"{br.MAX_UNRECOGNISED_STATUS_SHARE * 100:.0f}% allowed), so the rate could be wrong. "
                         "Correct the statuses on the Data cleaning page.")
    if which == "return_rate":
        denominator = count(br.STATUS_REVENUE) + count(br.STATUS_RETURNED)
        if denominator == 0:
            return Rate(which, label, None, definition, note="There are no completed or returned orders.")
        value = count(br.STATUS_RETURNED) / denominator * 100
        calculation = (f"{count(br.STATUS_RETURNED):,} returned ÷ ({count(br.STATUS_REVENUE):,} completed + "
                       f"{count(br.STATUS_RETURNED):,} returned) × 100 = {bm.format_percent(value)}.")
    else:
        value = count(br.STATUS_CANCELLED) / recognised * 100
        calculation = (f"{count(br.STATUS_CANCELLED):,} cancelled ÷ {recognised:,} orders with a recognised "
                       f"status × 100 = {bm.format_percent(value)}.")
    note = f"{unrecognised:,} order(s) with an unrecognised status are left out." if unrecognised else ""
    return Rate(which, label, value, definition, calculation, note)


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------
@dataclass
class Change:
    key: str
    label: str
    kind: str                      # money / count / percent
    current: float | None
    comparison: float | None
    change: float | None           # current − comparison (pp for percentages)
    change_pct: float | None       # % change (never for percentages)
    comparable: bool
    formula: str = ""
    note: str = ""                 # why the change (or the % change) is not shown

    @property
    def is_points(self) -> bool:
        return self.kind == bm.KIND_PERCENT


def compute_change(key: str, label: str, kind: str, current: float | None, comparison: float | None,
                   currency: str | None, blocked_reason: str = "") -> Change:
    """The change between two values. `blocked_reason` (for example a partial period)
    stops the change being calculated; the two values are still reported."""
    if current is None or comparison is None:
        return Change(key, label, kind, current, comparison, None, None, False,
                      note=blocked_reason or "Needs a value for both periods.")
    if blocked_reason:
        return Change(key, label, kind, current, comparison, None, None, False, note=blocked_reason)
    fmt = (lambda v: bm.format_money(v, currency)) if kind == bm.KIND_MONEY else \
        bm.format_percent if kind == bm.KIND_PERCENT else bm.format_count
    if kind == bm.KIND_PERCENT:
        points = current - comparison
        return Change(key, label, kind, current, comparison, points, None, True,
                      f"{fmt(current)} − {fmt(comparison)} = {format_points(points)}.")
    difference = current - comparison
    formula = f"{fmt(current)} − {fmt(comparison)} = {fmt(difference)}"
    if comparison > 0:
        percent = difference / comparison * 100
        return Change(key, label, kind, current, comparison, difference, percent, True,
                      f"{formula}; {fmt(difference)} ÷ {fmt(comparison)} × 100 = {format_pct_change(percent)}.")
    note = ("% change not shown: the earlier value is 0." if comparison == 0 else
            "% change not shown: the earlier value is negative (a loss), so a percentage would be misleading.")
    return Change(key, label, kind, current, comparison, difference, None, True, formula + ".", note)


REVENUE_PER_DAY_DEFINITION = ("Revenue divided by the number of days of the period that the data covers, so "
                              "periods of different lengths (28 to 31 days) can be compared fairly.")


def format_value(value: float | None, kind: str, currency: str | None) -> str:
    if kind == bm.KIND_MONEY:
        return bm.format_money(value, currency)
    if kind == bm.KIND_PERCENT:
        return bm.format_percent(value)
    return bm.format_count(value)


def format_signed(value: float | None, kind: str, currency: str | None) -> str:
    """A change with its sign: +1,234.50, −12, +2.5 pp."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    if kind == bm.KIND_PERCENT:
        return format_points(value)
    text = format_value(abs(value), kind, currency)
    return ("+" if value > 0 else "−" if value < 0 else "") + text


def change_text(change: Change, currency: str | None) -> str | None:
    """Short text for a KPI card: '+17.4% (+12,516.24)', '+1.2 pp' or '+300.00'
    (when a % change isn't meaningful). None when no change was calculated."""
    if change.change is None:
        return None
    if change.is_points:
        return format_points(change.change)
    amount = format_signed(change.change, change.kind, currency)
    return f"{format_pct_change(change.change_pct)} ({amount})" if change.change_pct is not None else amount


def change_direction(change: Change) -> str:
    """'up', 'down' or 'off' (no change), for the arrow next to a change."""
    if change.change is None or round(change.change, 6) == 0:
        return "off"
    return "up" if change.change > 0 else "down"


def metric_definition(figures: PeriodFigures, key: str) -> str:
    if key in figures.rates:
        return figures.rates[key].definition
    if key == "revenue_per_day":
        return REVENUE_PER_DAY_DEFINITION
    kpi = figures.result.kpis.get(key)
    return kpi.definition if kpi else ""


def metric_calculation(figures: PeriodFigures, key: str) -> str:
    """How the value for one period was calculated, with its numbers (or why it is missing)."""
    if key in figures.unavailable:
        return f"Not available: {figures.unavailable[key]}"
    if key in figures.rates:
        rate = figures.rates[key]
        return rate.calculation if rate.available else f"Not available: {rate.note}"
    if key == "revenue_per_day":
        value, cover = figures.values.get(key), figures.coverage.get("sales")
        if value is None or cover is None:
            return "Not available: needs revenue and dates."
        currency = figures.result.currency
        return (f"Revenue {bm.format_money(figures.values.get('revenue'), currency)} ÷ {cover.used_days} day(s) = "
                f"{bm.format_money(value, currency)}.")
    kpi = figures.result.kpis.get(key)
    if kpi is None:
        return ""
    return kpi.calculation if kpi.available else f"Not available: {kpi.note}"


def incomplete_sides(comparison: PeriodComparison, key: str) -> list[str]:
    """Short reasons a change was not calculated because of the periods, e.g. 'Jun 2026 is partial period'."""
    needed = [name for name in METRIC_SOURCES.get(key, ()) if name in comparison.current.coverage]
    reasons = []
    for side in (comparison.current, comparison.comparison):
        for name in needed:
            cover = side.coverage[name]
            if not cover.complete:
                text = f"{side.span.label}: {cover.label.lower()}" + (f" ({name})" if len(needed) > 1 else "")
                reasons.append(text)
    return list(dict.fromkeys(reasons))


def format_pct_change(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    rounded = round(value, 1)
    sign = "+" if rounded > 0 else "−" if rounded < 0 else ""
    return f"{sign}{abs(rounded):.1f}%"


def format_points(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    rounded = round(value, 1)
    sign = "+" if rounded > 0 else "−" if rounded < 0 else ""
    return f"{sign}{abs(rounded):.1f} pp"


@dataclass
class GrowthGap:
    """Expense growth compared with revenue growth, in percentage points."""
    expense_label: str
    expense_growth: float | None
    revenue_growth: float | None
    gap: float | None
    calculation: str = ""
    meaning: str = ""
    note: str = ""                 # why it is unavailable

    @property
    def available(self) -> bool:
        return self.gap is not None


def growth_gap(revenue: Change | None, expenses: Change | None, separate_expense_file: bool,
               dimension_filters: bool) -> GrowthGap:
    """gap = expense % change − revenue % change. Above 0: expenses grew faster than
    revenue (or fell more slowly). Unavailable whenever either growth rate is."""
    label = (expenses.label if expenses else "Expenses")
    unavailable = lambda reason: GrowthGap(label, None, None, None, note=reason)  # noqa: E731
    if revenue is None:
        return unavailable("Needs sales data.")
    if expenses is None:
        return unavailable("Needs expense data: add an expense file on the Dashboard.")
    for change, name in ((revenue, "revenue"), (expenses, label.lower())):
        if change.current is None and change.comparison is None:
            return unavailable(f"There are no {name} figures for either period. {change.note}".strip())
    if separate_expense_file and dimension_filters:
        return unavailable("Expenses are not recorded per product, category or region, so they can't be compared "
                           "with filtered revenue. Clear the product, category and region filters.")
    for change, name in ((revenue, "revenue"), (expenses, label.lower())):
        if not change.comparable:
            return unavailable(f"The {name} change can't be calculated: {change.note}")
        if change.change_pct is None:
            return unavailable(f"The {name} growth rate can't be calculated: {change.note}")
    gap = expenses.change_pct - revenue.change_pct
    calculation = (f"{label} growth {format_pct_change(expenses.change_pct)} − revenue growth "
                   f"{format_pct_change(revenue.change_pct)} = {format_points(gap)}.")
    if round(gap, 1) > 0:
        meaning = f"{label} grew faster than revenue (or fell more slowly), by {format_points(gap).lstrip('+')}."
    elif round(gap, 1) < 0:
        meaning = f"Revenue grew faster than {label.lower()} (or fell more slowly), by {format_points(-gap).lstrip('+')}."
    else:
        meaning = f"{label} and revenue changed at the same rate."
    return GrowthGap(label, expenses.change_pct, revenue.change_pct, gap, calculation, meaning)


# ---------------------------------------------------------------------------
# Comparing two periods
# ---------------------------------------------------------------------------
@dataclass
class PeriodFigures:
    """Everything calculated for one side of a comparison."""
    span: Span
    period: Period
    coverage: dict[str, Coverage]
    result: bm.DashboardResult
    values: dict[str, float | None]
    labels: dict[str, str]
    kinds: dict[str, str]
    rates: dict[str, Rate]
    pending_orders: int = 0
    pending_amount: float = 0.0
    unavailable: dict[str, str] = field(default_factory=dict)   # metric -> why it has no value

    @property
    def complete(self) -> bool:
        return all(c.complete for c in self.coverage.values())


@dataclass
class PeriodComparison:
    frequency: str
    mode: str
    to_date: bool
    current: PeriodFigures
    comparison: PeriodFigures
    changes: dict[str, Change]
    growth: GrowthGap
    currency: str | None
    notes: list[tuple[str, str]]   # (kind, text) for ui.notes_panel
    coverage_notes: list[tuple[str, str]] = field(default_factory=list)   # the period-status part of `notes`

    @property
    def other_notes(self) -> list[tuple[str, str]]:
        """Notes other than period statuses (which a page may already show next to each period)."""
        return [note for note in self.notes if note not in self.coverage_notes]

    @property
    def title(self) -> str:
        return f"{self.current.span.label} vs {self.comparison.span.label}"


def _span_filters(filters: bm.Filters, span: Span) -> bm.Filters:
    return bm.Filters(span.start.date(), span.end.date(), filters.products, filters.categories, filters.regions)


def period_figures(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, filters: bm.Filters,
                   span: Span, period: Period, include_pending: bool = False, today: date | None = None,
                   dates: dict[str, np.ndarray] | None = None) -> PeriodFigures:
    """All figures for one span, calculated by the Dashboard's own compute_dashboard()."""
    result = bm.compute_dashboard(sales, expenses, _span_filters(filters, span), include_pending)
    datasets = _datasets(sales, expenses)
    dates = dates or {name: record_dates(d) for name, d in datasets.items()}
    coverage = {name: assess_coverage(d, span, name, today, dates[name]) for name, d in datasets.items()}
    values = {key: kpi.value for key, kpi in result.kpis.items()}
    labels = {key: kpi.label for key, kpi in result.kpis.items()}
    kinds = {key: kpi.kind for key, kpi in result.kpis.items()}

    revenue = values.get("revenue")
    sales_cover = coverage.get("sales")
    if revenue is not None and sales_cover is not None and sales_cover.used_days:
        values["revenue_per_day"] = revenue / sales_cover.used_days
    else:
        values["revenue_per_day"] = None
    labels["revenue_per_day"], kinds["revenue_per_day"] = "Revenue per day", bm.KIND_MONEY

    rates = {which: status_rate(sales, result.sales_rows, which) for which in ("return_rate", "cancellation_rate")}
    for which, rate in rates.items():
        values[which], labels[which], kinds[which] = rate.value, rate.label, bm.KIND_PERCENT

    # A period the file doesn't reach, or one inside the file's dates without a single
    # record, has no figures: compute_dashboard() would return 0 for it, which would
    # wrongly read as "nothing was sold" when the data may simply be missing. (A product
    # that sold nothing in a period that does have records is a real 0 and is kept.)
    unavailable: dict[str, str] = {}
    for key in list(values):
        for name in METRIC_SOURCES.get(key, ()):
            cover = coverage.get(name)
            if cover is not None and cover.status in (OUTSIDE, NO_RECORDS):
                values[key] = None
                unavailable[key] = cover.reason

    pending_orders, pending_amount = 0, 0.0
    if sales is not None and not include_pending and not result.sales_rows.empty:
        pending = result.sales_rows[result.sales_rows["status_group"] == br.STATUS_PENDING]
        if not pending.empty:
            pending_orders = int(_orders_by_group(pending).sum())
            pending_amount = float(pending["amount"].sum()) if "amount" in pending else 0.0
    return PeriodFigures(span, period, coverage, result, values, labels, kinds, rates, pending_orders, pending_amount,
                         unavailable)


def _to_date_spans(current: Period, comparison: Period, datasets: dict[str, bm.PreparedDataset],
                   today: date | None) -> tuple[Span, Span]:
    """Like-for-like spans: the days of the current period the data covers so far, and
    the same days of the comparison period (e.g. 1–17 Nov vs 1–17 Oct)."""
    last = min([current.end, _day(today or QualityRules().today())] +
               [d.date_range[1] for d in datasets.values()])
    last = max(last, current.start)
    days = (last - current.start).days
    comparison_end = min(comparison.start + pd.Timedelta(days=days), comparison.end)
    return (Span(current.start, last, span_label(current.start, last)),
            Span(comparison.start, comparison_end, span_label(comparison.start, comparison_end)))


def compare_periods(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, filters: bm.Filters,
                    period: Period, mode: str = MODE_PREVIOUS, include_pending: bool = False,
                    to_date: bool = False, today: date | None = None) -> PeriodComparison:
    """Compare `period` with the previous period or the same period last year.

    `to_date=True` compares only the days of `period` that the data covers so far
    with the same days of the comparison period, so a partly-recorded month can be
    compared fairly. Product/category/region filters apply to both periods."""
    reason = comparison_unavailable_reason(sales, expenses)
    if reason:
        raise ValueError(reason)
    if mode not in MODE_LABELS:
        raise ValueError(f"Unknown comparison '{mode}'.")
    datasets = _datasets(sales, expenses)
    dates = {name: record_dates(d) for name, d in datasets.items()}
    other = period.comparison(mode)
    current_span, other_span = _to_date_spans(period, other, datasets, today) if to_date else \
        (period.span, other.span)
    now = period_figures(sales, expenses, filters, current_span, period, include_pending, today, dates)
    before = period_figures(sales, expenses, filters, other_span, other, include_pending, today, dates)
    currency = now.result.currency

    changes: dict[str, Change] = {}
    for key in METRIC_ORDER:
        if key not in now.values and key not in before.values:
            continue
        needed = [name for name in METRIC_SOURCES[key] if name in now.coverage]
        blocked = []
        for side in (now, before):
            for name in needed:
                if not side.coverage[name].complete:
                    blocked.append(f"{side.span.label} is not complete for the {name} data "
                                   f"({side.coverage[name].label.lower()}): {side.coverage[name].reason}")
        label = now.labels.get(key) or before.labels.get(key) or key
        kind = now.kinds.get(key) or before.kinds.get(key)
        change = compute_change(key, label, kind, now.values.get(key), before.values.get(key), currency,
                                " ".join(dict.fromkeys(blocked)))
        if change.current is None or change.comparison is None:
            missing = now if change.current is None else before
            why = missing.unavailable.get(key) or (
                missing.rates[key].note if key in missing.rates else
                missing.result.kpis[key].note if key in missing.result.kpis else "")
            change.note = why or change.note
        changes[key] = change

    # Expense data exists as a separate file, or as a cost column in the sales file.
    separate = expenses is not None
    has_expense_data = separate or (sales is not None and sales.has("cost"))
    growth = growth_gap(changes.get("revenue") if sales is not None else None,
                        changes.get("expenses") if has_expense_data else None,
                        separate, filters.has_dimension_filters)

    notes: list[tuple[str, str]] = []
    coverage_notes: list[tuple[str, str]] = []
    for side in (now, before):
        for cover in side.coverage.values():
            if not cover.complete:
                coverage_notes.append(("warning", f"{side.span.label} ({cover.dataset}): {cover.label}. "
                                                  f"{cover.reason}"))
    notes.extend(coverage_notes)
    for side in (now, before):
        if side.pending_orders:
            notes.append(("warning", f"{side.span.label} has {side.pending_orders:,} pending order(s) worth "
                                     f"{bm.format_money(side.pending_amount, currency)} that are not counted as "
                                     "revenue, so its revenue may look lower than it will be once they are "
                                     "completed. Choose 'Count pending orders as revenue' to include them."))
    if mode == MODE_PREVIOUS and current_span.days != other_span.days:
        notes.append(("info", f"{current_span.label} has {current_span.days} days and {other_span.label} has "
                              f"{other_span.days}. Revenue per day compares them fairly."))
    if to_date:
        notes.append(("info", f"Like-for-like comparison: {current_span.label} is compared with the same days of "
                              f"the earlier period, {other_span.label}."))
    if filters.has_dimension_filters:
        notes.append(("info", "Product, category and region filters apply to both periods."))
    return PeriodComparison(period.frequency, mode, to_date, now, before, changes, growth, currency, notes,
                            coverage_notes)


# ---------------------------------------------------------------------------
# All periods in a table
# ---------------------------------------------------------------------------
def period_table(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, filters: bm.Filters,
                 frequency: str, metric: str = "revenue", include_pending: bool = False,
                 window: tuple[date, date] | None = None, today: date | None = None) -> pd.DataFrame:
    """One row per period with the metric, its status, and its change against the
    previous period and the same period last year. Changes are only filled in when
    both periods are complete for the data the metric needs."""
    options = available_periods(sales, expenses, frequency, window, today)
    columns = ["Period", "Start", "Status", "Value", "Change vs previous", "% vs previous",
               "Same period last year", "% vs last year", "Note"]
    if not options:
        return pd.DataFrame(columns=columns)
    datasets = _datasets(sales, expenses)
    dates = {name: record_dates(d) for name, d in datasets.items()}
    cache: dict[pd.Timestamp, PeriodFigures] = {}

    def figures(period: Period) -> PeriodFigures:
        if period.start not in cache:
            cache[period.start] = period_figures(sales, expenses, filters, period.span, period, include_pending,
                                                 today, dates)
        return cache[period.start]

    rows = []
    for option in options:
        now = figures(option.period)
        kind = now.kinds.get(metric, bm.KIND_MONEY)
        currency = now.result.currency
        changes = {}
        for mode in (MODE_PREVIOUS, MODE_LAST_YEAR):
            before = figures(option.period.comparison(mode))
            needed = [n for n in METRIC_SOURCES.get(metric, ("sales",)) if n in now.coverage]
            blocked = [f"{side.span.label}: {side.coverage[n].label.lower()}" for side in (now, before)
                       for n in needed if not side.coverage[n].complete]
            changes[mode] = compute_change(metric, now.labels.get(metric, metric), kind, now.values.get(metric),
                                           before.values.get(metric), currency,
                                           "Not compared (" + "; ".join(dict.fromkeys(blocked)) + ")."
                                           if blocked else "")
        previous, last_year = changes[MODE_PREVIOUS], changes[MODE_LAST_YEAR]
        notes = [] if option.complete else [option.status_text]
        if now.pending_orders:
            notes.append(f"{now.pending_orders:,} pending order(s) not counted")
        rows.append({
            "Period": option.period.label,
            "Start": option.period.start,
            "Status": option.status_text,
            "Value": now.values.get(metric),
            "Change vs previous": previous.change,
            "% vs previous": previous.change if kind == bm.KIND_PERCENT else previous.change_pct,
            "Same period last year": last_year.comparison,
            "% vs last year": last_year.change if kind == bm.KIND_PERCENT else last_year.change_pct,
            "Note": "; ".join(notes),
        })
    return pd.DataFrame(rows, columns=columns)
