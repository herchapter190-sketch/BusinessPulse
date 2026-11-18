"""One snapshot of everything an exported report contains (Stage 8). No Streamlit code.

The Excel and PDF exports never calculate anything themselves: they read this
snapshot, and the snapshot only calls the functions the Analysis pages call, with
the same inputs (data version, second file, date range, filters, pending-orders
and error-row choices). So every exported figure is the figure shown in the app:

- KPIs, filtered rows        bm.compute_dashboard   (Dashboard, Performance)
- over time                  bm.time_table / perf.trend_table
- breakdowns                 bm.breakdown           (Dashboard)
- per product/category/region perf.dimension_table, perf.top_and_bottom (Performance)
- insights                   ins.build_insights     (Insights)
- anomalies                  an.build_anomalies     (Anomalies)

Currencies. Amounts are never converted or added across currencies. When the
sales and expense files state different currencies, each money figure keeps the
currency of the file it comes from, profit is not calculated (as in the app), and
written findings that would put amounts of both files side by side are left out
of the report, with the reason. When one file mixes currency symbols, the report
warns that its totals may combine currencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from config import business_rules as br
from tools import anomalies as an
from tools import business_metrics as bm
from tools import insights as ins
from tools import performance as perf

REPORT_TITLE = "Business performance report"
MONEY_KPIS = ("revenue", "expenses", "profit", "aov", "returns")
KPI_ORDER = ("revenue", "expenses", "profit", "margin", "orders", "units", "aov", "returns")
EXECUTIVE_FINDINGS = 6          # findings listed in the executive summary
FILTER_VALUES_SHOWN = 8         # filter values listed before "and N more"
MIXED_SYMBOLS_TEXT = "several different currency symbols"   # from bm.detect_currency

# Columns added to the filtered source records (prefixed so they never clash with the data's own columns).
RECORD_ROW = "Report: source row"
RECORD_COUNTED = "Report: counted in revenue"
RECORD_NOTE = "Report: note"

STATUS_TEXT = {
    br.STATUS_REVENUE: "Completed", br.STATUS_PENDING: "Pending", br.STATUS_RETURNED: "Returned/refunded",
    br.STATUS_CANCELLED: "Cancelled", br.STATUS_UNKNOWN: "Unrecognised status", br.STATUS_NONE: "No status column",
}
NOT_COUNTED_NOTE = {
    br.STATUS_PENDING: "Pending order: not counted, because 'Count pending orders as revenue' is off.",
    br.STATUS_RETURNED: "Returned/refunded: not counted in revenue (included in Returns and refunds).",
    br.STATUS_CANCELLED: "Cancelled: not counted in revenue.",
    br.STATUS_UNKNOWN: "Unrecognised status: not counted in revenue.",
}

# Finding priority for the executive summary: lower first.
TONE_LABELS = {ins.ATTENTION: ("warning", "Needs attention"), ins.POSITIVE: ("success", "Positive"),
               ins.NEUTRAL: ("info", "Note")}
SEVERITY_LABELS = {an.HIGH: ("error", "High-severity anomaly"), an.MEDIUM: ("warning", "Medium-severity anomaly")}


@dataclass
class Finding:
    """One prioritised finding for the executive summary."""
    priority: int
    status: str          # success / warning / error / info (always shown with its label)
    label: str           # e.g. "Needs attention"
    topic: str
    title: str
    evidence: str        # the main supporting figure
    action: str          # suggested next step


@dataclass
class Breakdown:
    title: str
    table: pd.DataFrame  # bm.breakdown: Name, Amount, Share, Rows[, Units]
    value_label: str     # "Revenue" or "Spent"
    entity: str          # "revenue" or "expenses" (chart colour)
    currency: str | None


@dataclass
class DimensionSection:
    by: str
    table: pd.DataFrame          # perf.dimension_table
    top: pd.DataFrame
    bottom: pd.DataFrame
    note: str


@dataclass
class Records:
    """Filtered source records: rows of the analysed file that match the report's settings."""
    title: str
    file_name: str
    version: str
    description: str
    frame: pd.DataFrame


@dataclass
class ReportData:
    title: str
    generated_at: datetime
    file_name: str
    version: str
    companion_name: str | None
    sales: bm.PreparedDataset | None
    expenses: bm.PreparedDataset | None
    filters: bm.Filters
    window: tuple[date, date] | None
    full_range: tuple[date, date] | None
    frequency: str
    include_pending: bool
    include_errors: bool
    result: bm.DashboardResult
    mixed_currency: bool
    currency_lines: list[str]
    trend: pd.DataFrame
    trend_note: str | None
    sales_trend: pd.DataFrame
    breakdowns: list[Breakdown]
    dimensions: list[DimensionSection]
    attribution: tuple[bool, str]
    insights: ins.InsightReport
    anomalies: an.AnomalyReport
    findings: list[Finding]
    summary: list[str]
    notes: list[tuple[str, str]]          # (kind, text): error / warning / info
    records: list[Records]
    left_out: list[str] = field(default_factory=list)   # findings left out of the report, with why

    # -- labels ------------------------------------------------------------
    @property
    def period_text(self) -> str:
        if not self.window:
            return "All rows (the data has no usable dates)"
        return f"{bm.format_date(self.window[0])} – {bm.format_date(self.window[1])}"

    @property
    def counting_text(self) -> str:
        return "Completed and pending orders" if self.include_pending else "Completed orders only"

    @property
    def error_rows_text(self) -> str:
        return ("Included (not recommended)" if self.include_errors
                else "Left out (rows with confirmed errors)")

    @property
    def filter_lines(self) -> list[tuple[str, str]]:
        """(label, text) for every filter, 'All' when it is not used."""
        lines = []
        options = bm.dimension_options(self.sales)
        for role, values in self.filters.dimension_values.items():
            if role not in options and not values:
                continue
            lines.append((bm.DIMENSION_LABELS[role], _list_text(values) if values else "All"))
        return lines

    @property
    def data_text(self) -> str:
        text = f"{self.file_name} ({self.version.lower()})"
        second = [d.name for d in (self.sales, self.expenses) if d is not None and d.name != self.file_name]
        return text + (f" + {second[0]} (second file, as uploaded)" if second else "")

    @property
    def orders_label(self) -> str:
        return self.result.kpis["orders"].label

    def kpi(self, key: str) -> bm.Kpi | None:
        return self.result.kpis.get(key)

    def kpis(self) -> list[bm.Kpi]:
        return [self.result.kpis[key] for key in KPI_ORDER if key in self.result.kpis]

    def kpi_currency(self, key: str) -> str | None:
        """The currency a money KPI is stated in. Without mixed currencies this is the
        currency the app shows; with mixed currencies each figure keeps its own file's."""
        if key not in MONEY_KPIS:
            return None
        if not self.mixed_currency:
            return self.result.currency
        if key == "expenses":
            return self.expenses.currency if self.expenses is not None else self.sales.currency
        return self.sales.currency if self.sales is not None else None

    @property
    def sales_currency(self) -> str | None:
        return self.kpi_currency("revenue")

    @property
    def expense_currency(self) -> str | None:
        return self.kpi_currency("expenses")

    def format_kpi(self, kpi: bm.Kpi) -> str:
        return bm.format_kpi(kpi, self.kpi_currency(kpi.key))

    @property
    def file_stem(self) -> str:
        if self.window:
            return f"BusinessPulse_report_{self.window[0]:%Y-%m-%d}_to_{self.window[1]:%Y-%m-%d}"
        return "BusinessPulse_report"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _list_text(values) -> str:
    values = [str(value) for value in values]
    shown = ", ".join(values[:FILTER_VALUES_SHOWN])
    more = len(values) - FILTER_VALUES_SHOWN
    return shown + (f" and {more:,} more" if more > 0 else "")


def is_mixed_currency(sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None) -> bool:
    """Same rule as bm.compute_dashboard: both files state a currency and they differ."""
    return bool(sales and expenses and sales.currency and expenses.currency and sales.currency != expenses.currency)


def currency_lines(sales, expenses, mixed: bool) -> list[str]:
    lines = []
    for label, dataset in (("Sales", sales), ("Expenses", expenses)):
        if dataset is not None:
            lines.append(f"{label}: {dataset.currency or 'not specified'} ({dataset.name})")
    if mixed:
        lines.append("The files use different currencies. Amounts are never added together or converted; each "
                     "figure is shown in its own file's currency and profit is not calculated.")
    return lines


def _source_rows(frame: pd.DataFrame | None, rows: pd.DataFrame, dataset: bm.PreparedDataset) -> pd.DataFrame:
    """The original columns of `frame` for the standardised `rows` (same index). If the
    original frame can't be matched, the standardised columns are used instead."""
    if frame is not None and frame.index.is_unique and rows.index.isin(frame.index).all():
        source = frame.loc[rows.index].copy()
    else:
        source = rows.drop(columns=[c for c in ("row", "status_group") if c in rows.columns]).copy()
    names = [str(column) for column in source.columns]
    source.columns = names
    source.insert(0, RECORD_ROW if RECORD_ROW not in names else f"{RECORD_ROW} (report)", rows["row"].values)
    return source.reset_index(drop=True)


def sales_records(frame: pd.DataFrame | None, sales: bm.PreparedDataset, result: bm.DashboardResult,
                  include_pending: bool) -> pd.DataFrame:
    """Every sales row that matches the date range and filters (any status), with
    whether it is counted in revenue under the current settings and why not."""
    rows = result.sales_rows
    if rows.empty:
        return _source_rows(frame, rows, sales).assign(**{RECORD_COUNTED: [], RECORD_NOTE: []})
    counted = rows.index.isin(result.revenue_rows.index)
    statuses = bm.counted_statuses(include_pending)

    def note(position: int) -> str:
        if counted[position]:
            return ""
        group = rows["status_group"].iloc[position]
        if group not in statuses:
            return NOT_COUNTED_NOTE.get(group, "Not counted in revenue.")
        return "No usable amount: not counted in revenue."

    source = _source_rows(frame, rows, sales)
    source[RECORD_COUNTED] = ["Yes" if flag else "No" for flag in counted]
    source[RECORD_NOTE] = [note(position) for position in range(len(rows))]
    return source


def expense_records(frame: pd.DataFrame | None, expenses: bm.PreparedDataset,
                    result: bm.DashboardResult) -> pd.DataFrame:
    """Every expense row counted in the selected dates (expenses have no product/category/region filter)."""
    return _source_rows(frame, result.expense_rows, expenses)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
def _insight_priority(insight: ins.Insight) -> int:
    return {ins.ATTENTION: 1, ins.POSITIVE: 3, ins.NEUTRAL: 5}.get(insight.tone, 6)


def _anomaly_priority(anomaly: an.Anomaly) -> int:
    if anomaly.unfavourable:
        return 0 if anomaly.severity == an.HIGH else 2
    return 4


def prioritised_findings(insights: list[ins.Insight], anomalies: list[an.Anomaly]) -> list[Finding]:
    """Insights and anomalies in one list, most actionable first: high-severity
    unfavourable anomalies, findings that need attention, medium unfavourable
    anomalies, positive findings, favourable anomalies, notes. Order within a
    group is kept as calculated."""
    found = []
    for insight in insights:
        status, label = TONE_LABELS.get(insight.tone, ("info", "Note"))
        found.append(Finding(_insight_priority(insight), status, label, insight.topic, insight.title,
                             insight.evidence[0] if insight.evidence else "", insight.recommendation))
    for anomaly in anomalies:
        status, label = SEVERITY_LABELS[anomaly.severity]
        effect = "unfavourable" if anomaly.unfavourable else "favourable"
        found.append(Finding(_anomaly_priority(anomaly), status, label, anomaly.area, anomaly.title,
                             anomaly.reason,
                             f"Review the {anomaly.period} records for {anomaly.subject} to confirm whether this "
                             f"{effect} change is genuine, a one-off or a recording error."))
    return sorted(found, key=lambda finding: finding.priority)


def executive_summary(data_kpis: dict[str, bm.Kpi], currency_of, period_text: str,
                      insights: ins.InsightReport, anomalies: an.AnomalyReport, findings: list[Finding]) -> list[str]:
    """A few plain sentences made only from calculated figures."""
    sentences = []
    revenue, orders = data_kpis.get("revenue"), data_kpis.get("orders")
    if revenue is not None and revenue.available:
        text = f"Revenue was {bm.format_kpi(revenue, currency_of('revenue'))}"
        if orders is not None and orders.available:
            text += f" from {bm.format_count(orders.value)} {orders.label.lower()}"
        sentences.append(f"{text} for {period_text}.")
    expenses = data_kpis.get("expenses")
    if expenses is not None and expenses.available:
        sentences.append(f"{expenses.label} were {bm.format_kpi(expenses, currency_of('expenses'))}.")
    profit, margin = data_kpis.get("profit"), data_kpis.get("margin")
    if profit is not None and profit.available:
        money = bm.format_money(abs(profit.value), currency_of("profit"))
        text = (f"{profit.label} was {money}" if profit.value >= 0 else
                f"The business made a {profit.label.lower().replace('profit', 'loss')} of {money}")
        if margin is not None and margin.available:
            text += f" (margin {bm.format_percent(margin.value)})"
        sentences.append(text + ".")
    elif profit is not None and profit.note:
        sentences.append(f"Profit is not shown: {profit.note}")
    attention = sum(f.status in ("warning", "error") for f in findings)
    positive = sum(f.status == "success" for f in findings)
    if findings:
        sentences.append(f"{attention:,} finding(s) need attention and {positive:,} are positive.")
    if anomalies.anomalies:
        sentences.append(f"{len(anomalies.anomalies):,} unusual month(s) were found "
                         f"({anomalies.count(an.HIGH):,} high severity) in {anomalies.complete_months:,} complete "
                         "month(s).")
    elif anomalies.checked:
        sentences.append(f"No unusual months were found in {anomalies.complete_months:,} complete month(s).")
    if insights.period and insights.compared_with:
        sentences.append(f"Trends compare {insights.period} with {insights.compared_with}, the latest complete "
                         "month in the period and the month before.")
    return sentences


# ---------------------------------------------------------------------------
# Building the snapshot
# ---------------------------------------------------------------------------
def build_report(*, file_name: str, version: str, companion_name: str | None,
                 sales: bm.PreparedDataset | None, expenses: bm.PreparedDataset | None,
                 sales_frame: pd.DataFrame | None, expense_frame: pd.DataFrame | None,
                 filters: bm.Filters, window: tuple[date, date] | None, full_range: tuple[date, date] | None,
                 frequency: str, include_pending: bool, include_errors: bool, today: date,
                 extra_notes: list[tuple[str, str]] | None = None,
                 generated_at: datetime | None = None) -> ReportData:
    """Calculate the report exactly as the Analysis pages do. `sales_frame` and
    `expense_frame` are the analysed versions of the files (for the source records)."""
    result = bm.compute_dashboard(sales, expenses, filters, include_pending)
    mixed = is_mixed_currency(sales, expenses)
    has_sales = sales is not None and "amount" in sales.rows.columns

    trend = bm.time_table(result, sales, expenses, filters, frequency) if window else pd.DataFrame(columns=["Period"])
    trend_note = bm.partial_period_note(trend, frequency, *window) if window and not trend.empty else None
    sales_trend = perf.trend_table(result, sales, filters, frequency) if window and has_sales else \
        pd.DataFrame(columns=["Period", "Revenue", "Orders", "Units", "Average order value"])

    sales_currency = (sales.currency if mixed else result.currency) if sales is not None else None
    expense_currency = (expenses.currency if mixed else result.currency) if expenses is not None else None
    breakdowns = []
    if has_sales:
        for by, title in (("category", "Revenue by category"), ("product", f"Top {br.TOP_ITEMS} products by revenue"),
                          ("region", "Revenue by region")):
            if sales.has(by):
                breakdowns.append(Breakdown(title, bm.breakdown(result.revenue_rows, by), "Revenue", "revenue",
                                            sales_currency))
    if expenses is not None and expenses.has("category"):
        breakdowns.append(Breakdown("Expenses by category", bm.breakdown(result.expense_rows, "category"), "Spent",
                                    "expenses", expense_currency))
    breakdowns = [item for item in breakdowns if not item.table.empty]

    dimensions = []
    if has_sales:
        for by in bm.DIMENSIONS:
            if sales.has(by):
                table = perf.dimension_table(result, by)
                if table.empty:
                    continue
                top, bottom, note = perf.top_and_bottom(table, "Revenue")
                dimensions.append(DimensionSection(by, table, top, bottom, note))

    insight_report = ins.build_insights(sales, expenses, filters, window, include_pending, today)
    anomaly_report = an.build_anomalies(sales, expenses, filters, window, include_pending, today)
    left_out = []
    if mixed:
        # The written findings state every amount in one currency, so findings that put
        # sales and expense amounts side by side would be mislabelled. Leave them out.
        reason = (f"left out of this report because the sales file uses {sales.currency} and the expense file "
                  f"uses {expenses.currency}, and no conversion rate is available.")
        kept = [i for i in insight_report.insights if i.topic not in ("Expenses", "Profit")]
        if len(kept) < len(insight_report.insights):
            left_out.append(f"{len(insight_report.insights) - len(kept):,} expense/profit insight(s) were {reason}")
        missing = dict(insight_report.missing)
        for topic in ("Expenses", "Profit"):
            missing[topic] = f"Expense and profit insights are {reason}"
        insight_report = ins.InsightReport(insight_report.period, insight_report.compared_with, kept, missing)
        kept_anomalies = [a for a in anomaly_report.anomalies if a.area not in ("Expenses", "Profit")]
        if len(kept_anomalies) < len(anomaly_report.anomalies):
            left_out.append(f"{len(anomaly_report.anomalies) - len(kept_anomalies):,} expense/profit anomaly "
                            f"finding(s) were {reason}")
        anomaly_missing = dict(anomaly_report.missing)
        for area in ("Expenses", "Profit"):
            anomaly_missing[area] = f"Expense and profit anomalies are {reason}"
        anomaly_report = an.AnomalyReport(kept_anomalies, anomaly_report.months_checked,
                                          anomaly_report.complete_months, anomaly_report.skipped, anomaly_missing,
                                          {k: v for k, v in anomaly_report.checked.items()
                                           if k not in ("Expenses", "Profit")})
    findings = prioritised_findings(insight_report.insights, anomaly_report.anomalies)

    def currency_of(key: str) -> str | None:
        if key not in MONEY_KPIS:
            return None
        if not mixed:
            return result.currency
        return expense_currency if key == "expenses" and expenses is not None else sales_currency

    period_text = (f"{bm.format_date(window[0])} – {bm.format_date(window[1])}" if window else "all rows")
    summary = executive_summary(result.kpis, currency_of, period_text, insight_report, anomaly_report, findings)

    notes: list[tuple[str, str]] = []
    for dataset in (sales, expenses):
        if dataset is not None and MIXED_SYMBOLS_TEXT in dataset.currency_note:
            notes.append(("error", f"{dataset.name} contains amounts with different currency symbols. Its totals "
                                   "add those amounts together without conversion, so they may be misleading. Put "
                                   "each currency in its own file (or convert the amounts) before relying on them."))
    if mixed:
        notes.append(("warning", currency_lines(sales, expenses, mixed)[-1]))
    notes += [("warning", text) for text in left_out]
    notes += [("warning", w) for dataset in (sales, expenses) if dataset for w in dataset.warnings]
    notes += list(extra_notes or [])
    notes += [("info", note) for note in result.notes]
    if trend_note:
        notes.append(("info", trend_note))
    notes += [("info", n) for dataset in (sales, expenses) if dataset for n in dataset.notes]
    notes += [("info", dataset.currency_note) for dataset in (sales, expenses) if dataset]
    notes = [(kind, text) for kind, text in dict.fromkeys(notes) if text]

    records = []
    if sales is not None:
        records.append(Records("Filtered sales records", sales.name, sales.version,
                               "Every sales row that matches the report's date range and filters, from the version "
                               "analysed. 'Report: counted in revenue' shows whether the row is counted under the "
                               "current settings, and why not.",
                               sales_records(sales_frame, sales, result, include_pending)))
    if expenses is not None and "amount" in expenses.rows.columns:
        records.append(Records("Filtered expense records", expenses.name, expenses.version,
                               "Every expense row counted in the report's date range. Expenses are not filtered by "
                               "product, category or region.", expense_records(expense_frame, expenses, result)))

    return ReportData(REPORT_TITLE, generated_at or datetime.now(), file_name, version, companion_name, sales,
                      expenses, filters, window, full_range, frequency, include_pending, include_errors, result,
                      mixed, currency_lines(sales, expenses, mixed), trend, trend_note, sales_trend, breakdowns,
                      dimensions, perf.profit_attribution(result, expenses), insight_report, anomaly_report,
                      findings, summary, notes, records, left_out)
