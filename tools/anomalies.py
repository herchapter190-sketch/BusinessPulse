"""Anomaly detection (Stage 7): unusual months for revenue, expenses, profit and for
individual products, categories and regions. No Streamlit code here.

The rule is the same everywhere and fully explainable:
1. Only *complete* months are used (period_comparison decides completeness), so a
   partly-recorded month is never called a drop.
2. Each complete month is compared with up to ANOMALY_MAX_HISTORY complete months
   before it (at least ANOMALY_MIN_HISTORY are needed) inside the selected dates.
3. Robust z-score = 0.6745 × (value − median) ÷ MAD. When MAD is 0 the mean
   absolute deviation is used instead (z = (value − median) ÷ (1.2533 × MeanAD)).
4. A month is flagged when |z| ≥ ANOMALY_Z_MEDIUM *and* it differs from the median by
   at least ANOMALY_MIN_DEVIATION_PCT %. |z| ≥ ANOMALY_Z_HIGH is high severity.

Figures come from period_comparison.period_figures() (which uses the Dashboard's
compute_dashboard()) and performance.dimension_table(), so they match the other pages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from config import business_rules as br
from tools import business_metrics as bm
from tools import performance as perf
from tools import period_comparison as pc

HIGH = "high"
MEDIUM = "medium"
SEVERITY_LABELS = {HIGH: "High", MEDIUM: "Medium"}
METRICS = {"revenue": "Revenue", "expenses": "Expenses", "profit": "Profit"}
DIMENSIONS = ("product", "category", "region")
AREAS = ("Revenue", "Expenses", "Profit", "Products", "Categories", "Regions")
DIMENSION_AREAS = {"product": "Products", "category": "Categories", "region": "Regions"}
# Which direction is the bad one: a revenue or profit drop, an expense rise.
BAD_WHEN_HIGHER = {"expenses"}


@dataclass
class Score:
    z: float | None             # None when the baseline is too short
    median: float
    mad: float
    deviation_pct: float | None  # |value − median| ÷ |median| × 100; None when median is 0
    history: int


@dataclass
class Anomaly:
    key: str
    area: str                  # one of AREAS
    subject: str               # "Revenue", or the product/category/region name
    period: str                # e.g. "Mar 2026"
    start: pd.Timestamp
    value: float
    typical: float             # median of the baseline months
    z: float
    severity: str              # HIGH / MEDIUM
    direction: str             # "higher" / "lower"
    unfavourable: bool
    title: str
    reason: str
    evidence: list[str]


@dataclass
class AnomalyReport:
    anomalies: list[Anomaly]
    months_checked: int
    complete_months: int
    skipped: list[str] = field(default_factory=list)     # incomplete months, with why
    missing: dict[str, str] = field(default_factory=dict)  # area -> why nothing could be checked
    checked: dict[str, int] = field(default_factory=dict)  # area -> number of series checked

    def for_area(self, area: str) -> list[Anomaly]:
        return [a for a in self.anomalies if a.area == area]

    def count(self, severity: str) -> int:
        return sum(a.severity == severity for a in self.anomalies)


# ---------------------------------------------------------------------------
# The statistical rule
# ---------------------------------------------------------------------------
def robust_score(value: float, history: list[float]) -> Score:
    """Robust z-score of `value` against `history` (see module docstring)."""
    baseline = np.asarray([h for h in history if h is not None and not math.isnan(h)], dtype=float)
    if len(baseline) < br.ANOMALY_MIN_HISTORY:
        median = float(np.median(baseline)) if len(baseline) else float("nan")
        return Score(None, median, float("nan"), None, len(baseline))
    median = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - median)))
    diff = value - median
    if mad > 0:
        z = 0.6745 * diff / mad
    else:
        mean_ad = float(np.mean(np.abs(baseline - median)))
        z = diff / (1.2533 * mean_ad) if mean_ad > 0 else (0.0 if abs(diff) < 0.005 else math.copysign(math.inf, diff))
    deviation = abs(diff) / abs(median) * 100 if abs(median) >= 0.005 else None
    return Score(z, median, mad, deviation, len(baseline))


def severity(score: Score) -> str | None:
    """HIGH, MEDIUM or None (not unusual)."""
    if score.z is None or abs(score.z) < br.ANOMALY_Z_MEDIUM:
        return None
    if score.deviation_pct is not None and score.deviation_pct < br.ANOMALY_MIN_DEVIATION_PCT:
        return None
    return HIGH if abs(score.z) >= br.ANOMALY_Z_HIGH else MEDIUM


def scan_series(series: list[tuple[str, pd.Timestamp, float | None]],
                min_difference: float = 0.0) -> list[tuple[str, pd.Timestamp, float, Score, str]]:
    """Check every month of a series (oldest first; None = month not complete or no
    value). Returns (label, start, value, score, severity) for each unusual month.
    The baseline for a month is the complete months before it, never after. A month
    closer to the median than `min_difference` is never flagged."""
    found = []
    for index, (label, start, value) in enumerate(series):
        if value is None:
            continue
        history = [v for _, _, v in series[:index] if v is not None][-br.ANOMALY_MAX_HISTORY:]
        score = robust_score(value, history)
        level = severity(score)
        if level and abs(value - score.median) >= min_difference:
            found.append((label, start, value, score, level))
    return found


def _z_text(z: float) -> str:
    return "not defined (every earlier month was identical)" if math.isinf(z) else f"{z:+.1f}"


def _rule_lines(score: Score, label: str, variation: str) -> list[str]:
    return [f"Usual variation (MAD): {variation}. Robust z-score: {_z_text(score.z)} "
            f"(unusual from ±{br.ANOMALY_Z_MEDIUM:g}, high from ±{br.ANOMALY_Z_HIGH:g}).",
            f"Baseline: the {score.history} complete month(s) before {label}."]


def _describe(key: str, area: str, label: str, start, value: float, score: Score, level: str,
              metric: str, currency) -> Anomaly:
    """Revenue, expenses or profit."""
    direction = "higher" if value > score.median else "lower"
    unfavourable = (direction == "higher") == (metric in BAD_WHEN_HIGHER)
    money = lambda v: bm.format_money(v, currency)                     # noqa: E731
    # Profit can sit near zero, where a percentage is misleading: give the amount instead.
    size = (f"{score.deviation_pct:.0f}% {direction}" if score.deviation_pct is not None and metric != "profit"
            else f"{money(abs(value - score.median))} {direction}")
    title = f"Unusual {area.lower()} {'spike' if direction == 'higher' else 'drop'} in {label}"
    reason = (f"{area} in {label} was {size} than in a typical month (the median of the {score.history} complete "
              "month(s) before it), far outside the usual month-to-month variation.")
    evidence = [f"{area} {label}: {money(value)}; typical month: {money(score.median)} "
                f"(difference {pc.format_signed(value - score.median, bm.KIND_MONEY, currency)})."]
    evidence += _rule_lines(score, label, money(score.mad))
    return Anomaly(key, area, area, label, start, value, score.median, score.z, level, direction, unfavourable,
                   title, reason, evidence)


def _describe_share(key: str, area: str, item: str, by: str, label: str, start, share: float, score: Score,
                    level: str, revenue: float, total: float, currency) -> Anomaly:
    """A product, category or region whose share of revenue was unusual."""
    direction = "higher" if share > score.median else "lower"
    title = f"{item}: unusually {'high' if direction == 'higher' else 'low'} share of revenue in {label}"
    reason = (f"{item} brought in {share:.1f}% of revenue in {label}, against a typical {score.median:.1f}% (the "
              f"median of the {score.history} complete month(s) before it). Shares are used so that seasons "
              f"affecting every {by} are not flagged.")
    evidence = [f"{item} {label}: revenue {bm.format_money(revenue, currency)} of "
                f"{bm.format_money(total, currency)} ({share:.1f}%); typical share {score.median:.1f}% "
                f"({pc.format_points(share - score.median)})."]
    evidence += _rule_lines(score, label, f"{score.mad:.1f} percentage points")
    return Anomaly(key, area, item, label, start, share, score.median, score.z, level, direction,
                   direction == "lower", title, reason, evidence)


# ---------------------------------------------------------------------------
# Everything together
# ---------------------------------------------------------------------------
def _needed_complete(option_coverage: dict, metric: str) -> bool:
    needed = [name for name in pc.METRIC_SOURCES.get(metric, ("sales",)) if name in option_coverage]
    return all(option_coverage[name].complete for name in needed)


def build_anomalies(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, filters: bm.Filters,
                    window: tuple[date, date] | None, include_pending: bool = False,
                    today: date | None = None) -> AnomalyReport:
    """Unusual months inside `window` for the filtered data."""
    reason = pc.comparison_unavailable_reason(sales, expenses)
    if reason:
        return AnomalyReport([], 0, 0, missing={area: reason for area in AREAS})
    options = pc.available_periods(sales, expenses, "M", window, today)
    datasets = {name: d for name, d in (("sales", sales), ("expenses", expenses)) if d is not None}
    dates = {name: pc.record_dates(d) for name, d in datasets.items()}
    figures = {}
    skipped = []
    for option in options:
        if option.complete or any(c.complete for c in option.coverage.values()):
            figures[option.period.start] = pc.period_figures(sales, expenses, filters, option.period.span,
                                                             option.period, include_pending, today, dates)
        if not option.complete:
            skipped.append(f"{option.period.label}: {option.status_text}")
    complete_months = sum(o.complete for o in options)
    anomalies: list[Anomaly] = []
    missing: dict[str, str] = {}
    checked: dict[str, int] = {}
    currency = next(iter(figures.values())).result.currency if figures else None
    months_checked = 0

    def series_for(metric: str, getter) -> list:
        rows = []
        for option in options:
            ok = option.period.start in figures and _needed_complete(option.coverage, metric)
            rows.append((option.period.label, option.period.start,
                         getter(figures[option.period.start]) if ok else None))
        return rows

    def shortage(series: list) -> str | None:
        usable = sum(v is not None for _, _, v in series)
        if usable <= br.ANOMALY_MIN_HISTORY:
            return (f"Needs at least {br.ANOMALY_MIN_HISTORY + 1} complete months (the first "
                    f"{br.ANOMALY_MIN_HISTORY} form the baseline); the selected dates have {usable}.")
        return None

    # Revenue, expenses and profit
    for metric, area in METRICS.items():
        series = series_for(metric, lambda f, m=metric: f.values.get(m))
        if all(v is None for _, _, v in series) and figures:
            first = next(iter(figures.values()))
            kpi = first.result.kpis.get(metric)
            missing[area] = (kpi.note if kpi is not None and kpi.note else f"No {area.lower()} figures were found.")
            continue
        why = shortage(series)
        if why:
            missing[area] = why
            continue
        checked[area] = 1
        months_checked = max(months_checked, sum(v is not None for _, _, v in series) - br.ANOMALY_MIN_HISTORY)
        for label, start, value, score, level in scan_series(series):
            anomalies.append(_describe(f"{metric}_{start:%Y%m}", area, label, start, value, score, level,
                                       metric, currency))

    # Products, categories and regions (their monthly revenue)
    has_sales = sales is not None and "amount" in sales.rows.columns
    for by in DIMENSIONS:
        area = DIMENSION_AREAS[by]
        if not has_sales:
            missing[area] = "Needs sales data."
            continue
        if by not in sales.rows.columns:
            missing[area] = f"No {by} column was recognised in the sales data."
            continue
        tables = {start: perf.dimension_table(f.result, by).set_index("Name")["Revenue"]
                  for start, f in figures.items()}
        whole = perf.dimension_table(bm.compute_dashboard(sales, expenses, filters, include_pending), by)
        items = whole[whole["Share"] >= br.ANOMALY_MIN_ITEM_SHARE]["Name"].tolist()
        revenue_series = series_for("revenue", lambda f: 0.0)
        why = shortage(revenue_series)
        if why:
            missing[area] = why
            continue
        if not items:
            missing[area] = f"No {by} has at least {br.ANOMALY_MIN_ITEM_SHARE:g}% of revenue in the selected dates."
            continue
        checked[area] = len(items)
        totals = {start: float(table.sum()) for start, table in tables.items()}
        for item in items:
            # Share of the month's revenue, so a season that lifts every item (e.g. December)
            # is not reported item by item. An item missing from a complete month sold nothing: 0.
            series = [(label, start, None if value is None or totals[start] <= 0 else
                       float(tables[start].get(item, 0.0)) / totals[start] * 100)
                      for label, start, value in revenue_series]
            for label, start, value, score, level in scan_series(series, br.ANOMALY_MIN_SHARE_POINTS):
                anomalies.append(_describe_share(f"{by}_{item}_{start:%Y%m}", area, str(item), by, label, start,
                                                 value, score, level, float(tables[start].get(item, 0.0)),
                                                 totals[start], currency))

    order = {HIGH: 0, MEDIUM: 1}
    anomalies.sort(key=lambda a: (order[a.severity], -a.start.value, -abs(a.z) if not math.isinf(a.z) else -1e18))
    return AnomalyReport(anomalies, months_checked, complete_months, skipped, missing, checked)
