"""Insights (Stage 6.4): plain-language findings, each with its supporting numbers
and a practical next step. No Streamlit code here.

Nothing is calculated twice and nothing is invented. Every number comes from the
existing engines:
- month-on-month changes from period_comparison.compare_periods(), which only
  calculates a change when both months are complete;
- the run of rising or falling months from period_comparison.period_table();
- product, category and region figures from performance.dimension_table();
- totals from business_metrics.compute_dashboard().
A finding is made only when its inputs exist. Otherwise the topic is listed in
`missing` with the reason, so the page can say why there is no finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from config import business_rules as br
from tools import business_metrics as bm
from tools import performance as perf
from tools import period_comparison as pc

POSITIVE = "positive"
ATTENTION = "attention"
NEUTRAL = "neutral"
TOPICS = ("Revenue", "Profit", "Expenses", "Products", "Categories", "Regions")
DIMENSION_TOPICS = {"product": "Products", "category": "Categories", "region": "Regions"}

# Next steps for the weakest item and for a concentration risk, per dimension.
WEAK_ADVICE = {
    "product": "Review its price, promotion and placement, or whether it is worth keeping in the range.",
    "category": "Check whether the range, pricing or visibility of this category needs attention.",
    "region": "Check whether customers in this region are reached as well as in {top}.",
}


@dataclass
class Insight:
    key: str
    topic: str                 # one of TOPICS
    tone: str                  # POSITIVE / ATTENTION / NEUTRAL
    title: str
    evidence: list[str]        # the supporting numbers, with how they were calculated
    recommendation: str


@dataclass
class InsightReport:
    period: str | None             # the month the trends describe, e.g. "Jun 2026"
    compared_with: str | None      # the month before it
    insights: list[Insight]
    missing: dict[str, str] = field(default_factory=dict)   # topic -> why there is no finding

    def for_topic(self, topic: str) -> list[Insight]:
        return [insight for insight in self.insights if insight.topic == topic]

    def count(self, tone: str) -> int:
        return sum(insight.tone == tone for insight in self.insights)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _money(value, currency) -> str:
    return bm.format_money(value, currency)


def _why(change: pc.Change | None, fallback: str) -> str:
    return (change.note if change and change.note else fallback).strip()


def growth_streak(table: pd.DataFrame, upto: pd.Timestamp) -> int:
    """Month-on-month revenue changes in a row with the same sign, ending at `upto`:
    +3 = rose three months running, −2 = fell twice. A month whose change could not
    be calculated (not complete, no data) ends the run."""
    rows = table[table["Start"] <= upto].sort_values("Start")
    streak = 0
    for value in reversed(rows["% vs previous"].tolist()):
        if pd.isna(value) or round(value, 1) == 0 or (streak and (value > 0) != (streak > 0)):
            break
        streak += 1 if value > 0 else -1
    return streak


# ---------------------------------------------------------------------------
# Revenue, profit and expenses (latest complete month vs the month before)
# ---------------------------------------------------------------------------
def revenue_insights(comparison: pc.PeriodComparison, trend: pd.DataFrame) -> tuple[list[Insight], str]:
    change = comparison.changes.get("revenue")
    if change is None or not change.comparable or change.change_pct is None:
        return [], _why(change, "Needs revenue for two complete months.")
    currency = comparison.currency
    now, before = comparison.current.span.label, comparison.comparison.span.label
    pct = change.change_pct
    evidence = [f"Revenue {now}: {_money(change.current, currency)}; {before}: {_money(change.comparison, currency)}.",
                f"Calculation: {change.formula}"]
    for key in ("orders", "aov"):
        driver = comparison.changes.get(key)
        text = pc.change_text(driver, currency) if driver and driver.comparable else None
        if text:
            evidence.append(f"{driver.label}: {pc.format_value(driver.comparison, driver.kind, currency)} → "
                            f"{pc.format_value(driver.current, driver.kind, currency)} ({text}).")
    streak = growth_streak(trend, comparison.current.period.start) if not trend.empty else 0
    if abs(streak) >= br.INSIGHT_STREAK_MONTHS:
        evidence.append(f"Revenue has {'risen' if streak > 0 else 'fallen'} month on month {abs(streak)} times in "
                        "a row.")
    if abs(pct) < br.INSIGHT_STEADY_PCT:
        insight = Insight("revenue_trend", "Revenue", NEUTRAL,
                          f"Revenue was steady in {now} ({pc.format_pct_change(pct)} vs {before})", evidence,
                          "Sales are stable. Look for growth in the strongest products and regions below.")
    elif pct > 0:
        insight = Insight("revenue_trend", "Revenue", POSITIVE,
                          f"Revenue grew {pc.format_pct_change(pct)} in {now} vs {before}", evidence,
                          "Find out what drove the increase (products, regions, promotions) and keep doing it.")
    else:
        insight = Insight("revenue_trend", "Revenue", ATTENTION,
                          f"Revenue fell {pc.format_pct_change(pct).lstrip('−')} in {now} vs {before}", evidence,
                          "Check whether fewer orders or smaller orders caused the drop, and which products and "
                          "regions fell most (below).")
    return [insight], ""


def profit_insights(comparison: pc.PeriodComparison) -> tuple[list[Insight], str]:
    current = comparison.current
    kpi = current.result.kpis.get("profit")
    if kpi is None or not kpi.available or current.values.get("profit") is None:
        return [], (current.unavailable.get("profit") or (kpi.note if kpi else "") or "Needs revenue and expenses.")
    currency = comparison.currency
    now, before = current.span.label, comparison.comparison.span.label
    change, margin = comparison.changes.get("profit"), comparison.changes.get("margin")
    profit = current.values["profit"]
    margin_text = (f"Profit margin: {bm.format_percent(margin.comparison)} → {bm.format_percent(margin.current)} "
                   f"({pc.format_points(margin.change)})." if margin and margin.comparable else "")
    if profit < 0 and current.complete:
        evidence = [f"Calculation: {kpi.calculation}"] + ([margin_text] if margin_text else [])
        return [Insight("profit_loss", "Profit", ATTENTION, f"The business made a loss of {_money(-profit, currency)} "
                        f"in {now}", evidence, "Expenses were higher than revenue. Review the expense categories "
                        "that rose and the products that sell least.")], ""
    if change is None or not change.comparable:
        return [], _why(change, "Needs profit for two complete months.")
    evidence = [f"Profit {now}: {_money(change.current, currency)}; {before}: {_money(change.comparison, currency)}.",
                f"Calculation: {change.formula}"] + ([margin_text] if margin_text else [])
    signed = pc.format_signed(change.change, change.kind, currency)
    pct = f" ({pc.format_pct_change(change.change_pct)})" if change.change_pct is not None else ""
    margin_fell = bool(margin and margin.comparable and round(margin.change, 1) < 0)
    if round(change.change, 2) > 0:
        advice = ("Profit grew while each sale earned a little less: review discounts and the costs that rose."
                  if margin_fell else "Profitability improved: keep the pricing and cost control behind it.")
        return [Insight("profit_trend", "Profit", POSITIVE, f"Profit rose by {signed}{pct} in {now}", evidence,
                        advice)], ""
    if round(change.change, 2) < 0:
        return [Insight("profit_trend", "Profit", ATTENTION, f"Profit fell by {signed.lstrip('−')}{pct} in {now}",
                        evidence, "Compare the expense changes and the products that sold less (below) to see "
                        "what reduced profit.")], ""
    return [Insight("profit_trend", "Profit", NEUTRAL, f"Profit was unchanged in {now}", evidence,
                    "No action needed on profit; watch next month's figures.")], ""


def expense_insights(comparison: pc.PeriodComparison) -> tuple[list[Insight], str]:
    gap = comparison.growth
    insights: list[Insight] = []
    if gap.available:
        evidence = [f"Calculation: {gap.calculation}"]
        change = comparison.changes.get("expenses")
        if change and change.comparable:
            evidence.append(f"{change.label}: {change.formula}")
        if round(gap.gap, 1) >= br.INSIGHT_GROWTH_GAP_PP:
            advice = ("Check whether the extra spending is bringing in more sales." if gap.expense_growth > 0 else
                      "Sales fell faster than costs, so each sale now carries more cost. Look for costs that can "
                      "be adjusted to the lower sales.")
            insights.append(Insight("expense_growth", "Expenses", ATTENTION, gap.meaning, evidence, advice))
        elif round(gap.gap, 1) <= -br.INSIGHT_GROWTH_GAP_PP:
            insights.append(Insight("expense_growth", "Expenses", POSITIVE, gap.meaning, evidence,
                                    "Costs are under control relative to sales. Keep monitoring the largest "
                                    "expense categories."))
        else:
            insights.append(Insight("expense_growth", "Expenses", NEUTRAL,
                                    f"{gap.expense_label} and revenue moved at a similar rate", evidence,
                                    "No action needed; expenses are in line with sales."))
    mover = expense_category_mover(comparison)
    if mover:
        insights.append(mover)
    return insights, ("" if insights else gap.note)


def expense_category_mover(comparison: pc.PeriodComparison) -> Insight | None:
    """The expense category that rose most, when both months are complete and the
    expense rows have a category."""
    change = comparison.changes.get("expenses")
    now_rows, before_rows = comparison.current.result.expense_rows, comparison.comparison.result.expense_rows
    if change is None or not change.comparable or "category" not in now_rows or "category" not in before_rows:
        return None
    joined = pd.concat({"now": now_rows.groupby("category")["amount"].sum(),
                        "before": before_rows.groupby("category")["amount"].sum()}, axis=1).fillna(0.0)
    if joined.empty:
        return None
    joined["diff"] = joined["now"] - joined["before"]
    name = joined["diff"].idxmax()
    row = joined.loc[name]
    if round(row["diff"], 2) <= 0:
        return None
    currency = comparison.currency
    pct = (f" ({pc.format_pct_change(row['diff'] / row['before'] * 100)})" if row["before"] > 0 else "")
    now, before = comparison.current.span.label, comparison.comparison.span.label
    return Insight("expense_category", "Expenses", NEUTRAL,
                   f"{name} was the expense that rose most in {now}",
                   [f"{name}: {_money(row['before'], currency)} in {before} → {_money(row['now'], currency)} in {now} "
                    f"(+{_money(row['diff'], currency)}{pct})."],
                   "Confirm this increase was planned and is worth what it costs.")


# ---------------------------------------------------------------------------
# Products, categories and regions
# ---------------------------------------------------------------------------
def dimension_insights(by: str, result: bm.DashboardResult,
                       comparison: pc.PeriodComparison | None) -> tuple[list[Insight], str]:
    """Top and weakest item over the selected dates, a concentration risk, and the
    biggest rise and fall between the two compared months."""
    topic, plural = DIMENSION_TOPICS[by], perf.DIMENSION_PLURALS[by]
    if by not in result.revenue_rows.columns:
        return [], f"No {by} column was recognised in the sales data."
    table = perf.dimension_table(result, by)
    if len(table) < 2:
        return [], f"Needs at least two {plural} with counted sales in the selected dates and filters."
    currency = result.currency
    total = _money(table["Revenue"].sum(), currency)
    top, weak = table.iloc[0], table.iloc[-1]
    insights = []

    def facts(row) -> str:
        return (f"{row['Name']}: revenue {_money(row['Revenue'], currency)} of {total} "
                f"({bm.format_percent(row['Share'])}); {bm.format_count(row['Orders'])} order(s).")

    if len(table) >= 3 and top["Share"] >= br.INSIGHT_CONCENTRATION_SHARE:
        runners = ", ".join(str(name) for name in table["Name"].iloc[1:3])
        insights.append(Insight(f"{by}_concentration", topic, ATTENTION,
                                f"{top['Name']} brings in {bm.format_percent(top['Share'])} of revenue",
                                [facts(top)], f"A large share of revenue depends on one {by}. Consider growing the "
                                              f"next-best {plural} ({runners}) to spread the risk."))
    else:
        insights.append(Insight(f"{by}_top", topic, POSITIVE, f"{top['Name']} is the top {by} by revenue",
                                [facts(top), facts(table.iloc[1])],
                                "Protect what works: keep it well supplied, visible and well served."))
    insights.append(Insight(f"{by}_weak", topic, NEUTRAL, f"{weak['Name']} is the weakest {by} by revenue",
                            [facts(weak)], WEAK_ADVICE[by].format(top=top["Name"])))
    insights.extend(dimension_movers(by, comparison))
    return insights, ""


def dimension_movers(by: str, comparison: pc.PeriodComparison | None) -> list[Insight]:
    """The item whose revenue rose most and the one that fell most between the two
    compared months. Only when the revenue change between them is valid (both
    months complete), so an item missing from a month really sold nothing."""
    change = comparison.changes.get("revenue") if comparison else None
    if change is None or not change.comparable:
        return []
    now = perf.dimension_table(comparison.current.result, by).set_index("Name")["Revenue"]
    before = perf.dimension_table(comparison.comparison.result, by).set_index("Name")["Revenue"]
    joined = pd.concat({"now": now, "before": before}, axis=1).fillna(0.0)
    if len(joined) < 2:
        return []
    joined["diff"] = joined["now"] - joined["before"]
    currency, plural = comparison.currency, perf.DIMENSION_PLURALS[by]
    topic = DIMENSION_TOPICS[by]
    now_label, before_label = comparison.current.span.label, comparison.comparison.span.label
    found = []
    for key, name, tone in (("rise", joined["diff"].idxmax(), POSITIVE), ("fall", joined["diff"].idxmin(), ATTENTION)):
        row = joined.loc[name]
        if (key == "rise" and round(row["diff"], 2) <= 0) or (key == "fall" and round(row["diff"], 2) >= 0):
            continue
        pct = f" ({pc.format_pct_change(row['diff'] / row['before'] * 100)})" if row["before"] > 0 else ""
        signed = pc.format_signed(row["diff"], bm.KIND_MONEY, currency)
        evidence = [f"{name}: {_money(row['before'], currency)} in {before_label} → {_money(row['now'], currency)} "
                    f"in {now_label} ({signed}{pct})."]
        if key == "rise":
            found.append(Insight(f"{by}_rise", topic, tone, f"{name} grew the most of all {plural} in {now_label}",
                                 evidence, "Find out what worked (price, promotion, stock) and try it elsewhere."))
        else:
            found.append(Insight(f"{by}_fall", topic, tone, f"{name} fell the most of all {plural} in {now_label}",
                                 evidence, "Check for stock problems, price changes or lost customers."))
    return found


# ---------------------------------------------------------------------------
# Everything together
# ---------------------------------------------------------------------------
def build_insights(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None, filters: bm.Filters,
                   window: tuple[date, date] | None, include_pending: bool = False,
                   today: date | None = None) -> InsightReport:
    """All findings for the selected dates and filters. Trends compare the latest
    complete month inside `window` with the month before; products, categories and
    regions use the whole selected period."""
    has_sales = sales is not None and "amount" in sales.rows.columns
    result = bm.compute_dashboard(sales, expenses, filters, include_pending)
    insights: list[Insight] = []
    missing: dict[str, str] = {}

    comparison, reason = None, pc.comparison_unavailable_reason(sales, expenses)
    if reason is None:
        period = pc.default_period(pc.available_periods(sales, expenses, "M", window, today))
        if period is None:
            reason = "There are no months in the selected dates."
        else:
            comparison = pc.compare_periods(sales, expenses, filters, period, pc.MODE_PREVIOUS, include_pending,
                                            today=today)

    def add(topic: str, found: tuple[list[Insight], str]) -> None:
        insights.extend(found[0])
        if not found[0]:
            missing[topic] = found[1]

    if comparison is None:
        for topic in ("Revenue", "Profit", "Expenses"):
            missing[topic] = reason
    else:
        if has_sales:
            trend = pc.period_table(sales, expenses, filters, "M", "revenue", include_pending, window, today)
            add("Revenue", revenue_insights(comparison, trend))
        else:
            missing["Revenue"] = "Needs sales data."
        add("Profit", profit_insights(comparison))
        add("Expenses", expense_insights(comparison))

    for by, topic in DIMENSION_TOPICS.items():
        if has_sales:
            add(topic, dimension_insights(by, result, comparison))
        else:
            missing[topic] = "Needs sales data."

    return InsightReport(comparison.current.span.label if comparison else None,
                         comparison.comparison.span.label if comparison else None, insights, missing)
