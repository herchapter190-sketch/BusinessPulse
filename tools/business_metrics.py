"""Business KPIs for the dashboard: revenue, expenses, profit, margin, orders,
units and average order value, plus the tables behind each chart.

Principles:
- Nothing is invented. A KPI whose inputs are missing is reported as
  unavailable with the reason, never as zero.
- Rows with confirmed errors (see tools/data_quality.py) are left out of every
  figure unless the user explicitly chooses to include them, and the number left
  out is always reported.
- Sales and expense files are never merged row by row. They are only combined as
  totals over the dates both files cover, so nothing is counted twice.
- The input table is only read; a separate standardised table is built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from config import business_rules as br
from config.quality_rules import ERROR, IGNORED_NAME_WORDS, WARNING, QualityRules
from tools.column_mapping import ColumnMapping, normalize_column_name, recognize_columns
from tools.data_cleaning import DECIMAL_COMMA, decimal_evidence, parse_amount
from tools.data_quality import SLASH_STYLE, analyze_quality, classify_date
from tools.file_loader import display_value

CURRENCY_SYMBOLS = "$€£¥₹"
DIMENSIONS = ("product", "category", "region")
DIMENSION_LABELS = {"product": "Product", "category": "Category", "region": "Region"}
FREQUENCIES = {"D": "Day", "W": "Week", "M": "Month", "Q": "Quarter"}
COUNTED_ALWAYS = {br.STATUS_REVENUE, br.STATUS_NONE}

KIND_MONEY = "money"
KIND_COUNT = "count"
KIND_PERCENT = "percent"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def format_money(value: float | None, currency: str | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    text = f"{abs(value):,.2f}"
    sign = "−" if value < 0 else ""
    return f"{sign}{currency} {text}" if currency else f"{sign}{text}"


def format_count(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def format_percent(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    return f"{value:.1f}%"


def format_date(value) -> str:
    return pd.Timestamp(value).strftime("%d %b %Y") if value is not None and not pd.isna(value) else "—"


# ---------------------------------------------------------------------------
# Preparing a dataset
# ---------------------------------------------------------------------------
@dataclass
class PreparedDataset:
    name: str                    # file name, e.g. "sales_clean.csv"
    version: str                 # "Cleaned dataset", "Original upload", ...
    kind: str                    # "sales", "expenses" or "unknown"
    mapping: ColumnMapping
    rows: pd.DataFrame           # standardised rows used for calculations
    source_rows: int             # rows in the version that was analysed
    error_rows_excluded: int
    amount_source: str | None    # how the money amount was found, or None
    currency: str | None
    currency_note: str
    has_dates: bool
    notes: list[str] = field(default_factory=list)       # information
    warnings: list[str] = field(default_factory=list)    # things that may affect the figures

    @property
    def date_range(self) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        if not self.has_dates or self.rows.empty:
            return None
        return self.rows["date"].min(), self.rows["date"].max()

    def has(self, column: str) -> bool:
        return column in self.rows.columns and self.rows[column].notna().any()

    @property
    def label(self) -> str:
        return f"{self.name} ({self.version.lower()})"


def _parse_dates(values: pd.Series) -> tuple[pd.Series, dict[str, int]]:
    """Read dates without guessing. Dates such as 03/05/2025, which could be read
    day-first or month-first, are NOT used; they are counted as ambiguous."""
    parsed, counts = [], {"blank": 0, "ambiguous": 0, "unreadable": 0}
    for value in values.to_numpy(dtype=object):
        if value is None or value is pd.NaT or (isinstance(value, float) and np.isnan(value)) or \
                (isinstance(value, str) and not value.strip()):
            parsed.append(pd.NaT)
            counts["blank"] += 1
            continue
        style, result, parts = classify_date(value)
        if style == SLASH_STYLE:
            first, second, year = parts
            if first > 12 >= second or second > 12 >= first:
                day, month = (first, second) if first > 12 else (second, first)
                try:
                    result = pd.Timestamp(year, month, day)
                except ValueError:
                    result = None
            else:
                counts["ambiguous" if first <= 12 and second <= 12 else "unreadable"] += 1
                parsed.append(pd.NaT)
                continue
        if result is None:
            counts["unreadable"] += 1
            parsed.append(pd.NaT)
        else:
            parsed.append(pd.Timestamp(result).normalize())
    return pd.Series(pd.to_datetime(parsed), index=values.index), counts


def _parse_numbers(values: pd.Series) -> tuple[pd.Series, int, str | None]:
    """Numbers as floats (NaN when blank or unreadable). Returns (numbers,
    unreadable count, warning)."""
    evidence = decimal_evidence(values)
    convention = evidence.detected
    warning = None
    if convention is None:
        warning = (f"'{values.name}' mixes decimal points and decimal commas, so some amounts may be read wrongly. "
                   "Convert the column on the Data cleaning page to choose the convention.")
    decimal_comma = convention == DECIMAL_COMMA
    numbers, unreadable = [], 0
    for value in values.to_numpy(dtype=object):
        if value is None or (isinstance(value, float) and np.isnan(value)) or \
                (isinstance(value, str) and not value.strip()):
            numbers.append(np.nan)
            continue
        number = parse_amount(value, decimal_comma)
        if number is None:
            unreadable += 1
            numbers.append(np.nan)
        else:
            numbers.append(number)
    return pd.Series(numbers, index=values.index, dtype=float), unreadable, warning


def _text(values: pd.Series) -> pd.Series:
    text = values.map(display_value).astype(object)
    return text.where(text.map(lambda value: value.strip() != ""), br.NOT_SPECIFIED)


def status_group(status: str) -> str:
    key = " ".join(str(status).strip().casefold().split())
    if key in br.REVENUE_STATUSES:
        return br.STATUS_REVENUE
    if key in br.PENDING_STATUSES:
        return br.STATUS_PENDING
    if key in br.RETURNED_STATUSES:
        return br.STATUS_RETURNED
    if key in br.CANCELLED_STATUSES:
        return br.STATUS_CANCELLED
    return br.STATUS_UNKNOWN


def detect_currency(frame: pd.DataFrame, column: str | None) -> tuple[str | None, str]:
    """A currency is reported only when the data states it clearly: a currency
    code in the column name, or the same symbol on (almost) every value."""
    if column is None:
        return None, "Currency not specified."
    codes = [word.upper() for word in normalize_column_name(column).split() if word in IGNORED_NAME_WORDS]
    if len(set(codes)) == 1:
        return codes[0], f"Currency {codes[0]} taken from the column name '{column}'."
    values = [value for value in frame[column] if isinstance(value, str) and value.strip()]
    found = {symbol: sum(symbol in value for value in values) for symbol in CURRENCY_SYMBOLS}
    used = {symbol: count for symbol, count in found.items() if count}
    if len(used) == 1 and values:
        symbol, count = next(iter(used.items()))
        if count / len(values) >= 0.9:
            return symbol, f"Currency symbol '{symbol}' found on the values in '{column}'."
        return None, (f"Some values in '{column}' include '{symbol}' but most don't, so the currency is not "
                      "specified.")
    if len(used) > 1:
        return None, f"'{column}' contains several different currency symbols; the currency is not specified."
    return None, "The data does not state a currency, so amounts are shown without a currency symbol."


def prepare_dataset(frame: pd.DataFrame, name: str, version: str, mapping: ColumnMapping | None = None,
                    include_error_rows: bool = False, rules: QualityRules | None = None) -> PreparedDataset:
    """Build the standardised table used for every KPI and chart. `frame` is not changed."""
    rules = rules or QualityRules()
    mapping = mapping or recognize_columns([str(column) for column in frame.columns])
    kind = mapping.dataset_kind()
    notes: list[str] = []
    warnings: list[str] = []

    # Rows with confirmed errors are not used unless the user says so.
    report = analyze_quality(frame, mapping, rules)
    error_positions = sorted({row for issue in report.issues if issue.severity == ERROR for row in issue.rows})
    data = frame
    if error_positions and not include_error_rows:
        keep = np.ones(len(frame), dtype=bool)
        keep[error_positions] = False
        data = frame.iloc[keep]
        warnings.append(f"{len(error_positions):,} row(s) with confirmed errors (blank required values, impossible "
                        "dates, text in number columns, exact duplicates…) are left out of every figure. Fix or "
                        "exclude them on the Data cleaning page, or choose to include them.")
    elif error_positions:
        warnings.append(f"{len(error_positions):,} row(s) with confirmed errors are INCLUDED, as you chose. Figures may "
                        "be wrong or counted twice.")
    for issue in report.issues:
        if issue.check == "duplicate_row" and issue.severity == WARNING:
            warnings.append(f"{issue.affected_count:,} row(s) are exact copies of another row. They are included, "
                            "because without an ID column they could be genuine repeat records.")
    split_names = sorted({issue.column for issue in report.issues
                          if issue.check in ("extra_spaces", "capitalization", "suspicious_category")})
    if split_names:
        warnings.append(f"Some names in {', '.join(split_names)} are written in different ways (extra spaces, "
                        "capital letters or spelling), so they appear as separate items in breakdowns and filters. "
                        "Fix them on the Data cleaning page.")
    extremes = sorted({issue.column for issue in report.issues if issue.check == "extreme_value"})
    if extremes:
        warnings.append(f"Extremely large or small values in {', '.join(extremes)} are included. Check them on the "
                        "Data quality page: if they are typing mistakes they distort the totals.")

    rows = pd.DataFrame(index=data.index)
    rows["row"] = data.index + 1

    has_dates = False
    date_column = mapping.column_for("date")
    if date_column is not None:
        dates, counts = _parse_dates(data[date_column])
        has_dates = dates.notna().any()
        rows["date"] = dates
        unusable = dates.isna()
        if unusable.any():
            parts = [f"{counts[key]:,} {label}" for key, label in
                     (("blank", "blank"), ("ambiguous", "ambiguous (e.g. 03/05/2025)"), ("unreadable", "unreadable"))
                     if counts[key]]
            warnings.append(f"{int(unusable.sum()):,} row(s) have no usable date ({', '.join(parts)}) and are left "
                            "out, because they can't be placed in a time period. Convert dates on the Data "
                            "cleaning page to include them.")
            rows = rows[~unusable]
            data = data.loc[rows.index]
    else:
        notes.append("No date column was recognised, so trends over time are not available.")

    amount_source = None
    if kind == "sales":
        amount_column = mapping.column_for("sales_amount")
    elif kind == "expenses":
        amount_column = mapping.column_for("expense_amount")
    else:
        amount_column = None

    for role, target in (("quantity", "quantity"), ("unit_price", "unit_price")):
        column = mapping.column_for(role)
        if column is not None:
            numbers, unreadable, warning = _parse_numbers(data[column])
            rows[target] = numbers
            if warning:
                warnings.append(warning)

    if amount_column is not None:
        numbers, unreadable, warning = _parse_numbers(data[amount_column])
        rows["amount"] = numbers
        amount_source = f"'{amount_column}'"
        if unreadable:
            warnings.append(f"{unreadable:,} value(s) in '{amount_column}' are not numbers and are left out of totals.")
        if warning:
            warnings.append(warning)
    elif kind == "sales" and {"quantity", "unit_price"} <= set(rows.columns):
        rows["amount"] = rows["quantity"] * rows["unit_price"]
        amount_source = f"quantity × unit price ('{mapping.column_for('quantity')}' × " \
                        f"'{mapping.column_for('unit_price')}')"
        notes.append("There is no sales-amount column, so revenue is calculated as quantity × unit price.")

    if kind == "sales" and mapping.column_for("expense_amount"):
        cost_column = mapping.column_for("expense_amount")
        rows["cost"], _, _ = _parse_numbers(data[cost_column])
        notes.append(f"'{cost_column}' is treated as the cost of each sale (used for gross profit).")

    id_column = mapping.column_for("id")
    if id_column is not None:
        ids = data[id_column].map(display_value).astype(object)
        rows["order_id"] = ids.where(ids.map(lambda value: value.strip() != ""), None)
    for role in DIMENSIONS + ("customer", "department"):
        column = mapping.column_for(role)
        if column is not None:
            rows[role] = _text(data[column])

    status_column = mapping.column_for("status")
    if status_column is not None:
        rows["status"] = _text(data[status_column])
        rows["status_group"] = rows["status"].map(status_group)
    else:
        rows["status_group"] = br.STATUS_NONE

    currency, currency_note = detect_currency(frame, amount_column)
    return PreparedDataset(name, version, kind, mapping, rows, len(frame), 0 if include_error_rows
                           else len(error_positions), amount_source, currency, currency_note, bool(has_dates),
                           notes, warnings)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Filters:
    start: date | None = None
    end: date | None = None
    products: tuple = ()
    categories: tuple = ()
    regions: tuple = ()

    @property
    def dimension_values(self) -> dict[str, tuple]:
        return {"product": self.products, "category": self.categories, "region": self.regions}

    @property
    def has_dimension_filters(self) -> bool:
        return any(self.dimension_values.values())


def apply_filters(dataset: PreparedDataset, filters: Filters, use_dimensions: bool = True) -> pd.DataFrame:
    rows = dataset.rows
    if dataset.has_dates:
        if filters.start is not None:
            rows = rows[rows["date"] >= pd.Timestamp(filters.start)]
        if filters.end is not None:
            rows = rows[rows["date"] < pd.Timestamp(filters.end) + pd.Timedelta(days=1)]
    if use_dimensions:
        for role, values in filters.dimension_values.items():
            if values and role in rows.columns:
                rows = rows[rows[role].isin(values)]
    return rows


def dimension_options(dataset: PreparedDataset | None) -> dict[str, list[str]]:
    """Values offered in each filter, most common first."""
    if dataset is None:
        return {}
    return {role: list(dataset.rows[role].value_counts().index) for role in DIMENSIONS if dataset.has(role)}


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
@dataclass
class Kpi:
    key: str
    label: str
    value: float | None
    kind: str                  # money / count / percent
    definition: str            # what it means
    calculation: str           # how it was calculated, with the actual numbers
    note: str = ""             # why it is unavailable, or a caveat

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class DashboardResult:
    kpis: dict[str, Kpi]
    revenue_rows: pd.DataFrame            # sales rows counted as revenue (after filters)
    sales_rows: pd.DataFrame              # all filtered sales rows, any status
    expense_rows: pd.DataFrame            # filtered expense rows
    profit_type: str | None               # "net", "gross" or None
    profit_period: tuple[pd.Timestamp, pd.Timestamp] | None
    currency: str | None
    notes: list[str]


def counted_statuses(include_pending: bool) -> set[str]:
    return COUNTED_ALWAYS | ({br.STATUS_PENDING} if include_pending else set())


def _status_note(sales_rows: pd.DataFrame, include_pending: bool, currency: str | None) -> str | None:
    if sales_rows.empty or (sales_rows["status_group"] == br.STATUS_NONE).all():
        return None
    groups = sales_rows.groupby("status_group")
    left_out = []
    for group, label in ((br.STATUS_RETURNED, "returned/refunded"), (br.STATUS_CANCELLED, "cancelled"),
                         (br.STATUS_PENDING, "pending"), (br.STATUS_UNKNOWN, "with an unrecognised status")):
        if group == br.STATUS_PENDING and include_pending:
            continue
        if group in groups.groups:
            part = groups.get_group(group)
            left_out.append(f"{len(part):,} {label} ({format_money(part['amount'].sum(), currency)})"
                            if "amount" in part else f"{len(part):,} {label}")
    if not left_out:
        return None
    counted = "completed and pending" if include_pending else "completed"
    return f"Revenue counts {counted} orders only. Not counted: {', '.join(left_out)}."


def compute_dashboard(sales: PreparedDataset | None, expenses: PreparedDataset | None, filters: Filters,
                      include_pending: bool = False) -> DashboardResult:
    """Calculate every KPI for the filtered data. Unavailable KPIs carry the reason."""
    notes: list[str] = []
    currency = (sales.currency if sales else None) or (expenses.currency if expenses else None)
    if sales and expenses and sales.currency and expenses.currency and sales.currency != expenses.currency:
        notes.append(f"The sales file uses {sales.currency} and the expense file uses {expenses.currency}. Profit is "
                     "not calculated because the amounts are in different currencies.")

    empty = pd.DataFrame(columns=["date", "amount"])
    sales_rows = apply_filters(sales, filters) if sales else empty
    has_amount = sales is not None and "amount" in sales_rows.columns
    statuses = counted_statuses(include_pending)
    revenue_rows = sales_rows[sales_rows["status_group"].isin(statuses)] if sales else empty
    if has_amount:
        missing_amount = revenue_rows["amount"].isna()
        revenue_rows = revenue_rows[~missing_amount]
    expense_rows = apply_filters(expenses, filters, use_dimensions=False) if expenses else empty
    expense_rows = expense_rows[expense_rows["amount"].notna()] if expenses and "amount" in expense_rows else empty

    kpis: dict[str, Kpi] = {}
    unavailable_sales = "No sales data: add or load a file with a sales/revenue amount (or quantity and unit price)."

    # Revenue
    if has_amount:
        revenue = float(revenue_rows["amount"].sum())
        negatives = revenue_rows["amount"] < 0
        note = ""
        if negatives.any():
            note = (f"Includes {int(negatives.sum()):,} negative amount(s) totalling "
                    f"{format_money(revenue_rows.loc[negatives, 'amount'].sum(), currency)} (refunds or credits), "
                    "which reduce revenue.")
        kpis["revenue"] = Kpi("revenue", "Total revenue", revenue, KIND_MONEY,
                              "Money from sales in the selected period and filters.",
                              f"Sum of {sales.amount_source} over {len(revenue_rows):,} counted row(s) "
                              f"= {format_money(revenue, currency)}.", note)
    else:
        revenue = None
        kpis["revenue"] = Kpi("revenue", "Total revenue", None, KIND_MONEY,
                              "Money from sales in the selected period and filters.", "", unavailable_sales)

    # Orders, units, average order value
    if sales is not None:
        if "order_id" in revenue_rows.columns:
            orders = float(revenue_rows["order_id"].dropna().nunique())
            label, how = "Orders", f"Number of different order IDs among {len(revenue_rows):,} counted row(s)"
        else:
            orders = float(len(revenue_rows))
            label, how = "Transactions", "Number of counted rows (there is no order-ID column)"
        kpis["orders"] = Kpi("orders", label, orders, KIND_COUNT, "How many sales were made.",
                             f"{how} = {format_count(orders)}.")
    else:
        kpis["orders"] = Kpi("orders", "Orders", None, KIND_COUNT, "How many sales were made.", "", unavailable_sales)

    if sales is not None and "quantity" in revenue_rows.columns:
        units = float(revenue_rows["quantity"].sum())
        missing = int(revenue_rows["quantity"].isna().sum())
        negative = int((revenue_rows["quantity"] < 0).sum())
        caveats = []
        if missing:
            caveats.append(f"{missing:,} counted row(s) have no quantity and add nothing")
        if negative:
            caveats.append(f"{negative:,} negative quantity value(s) reduce the total")
        kpis["units"] = Kpi("units", "Units sold", units, KIND_COUNT, "Total number of items sold.",
                            f"Sum of '{sales.mapping.column_for('quantity')}' over the counted rows = "
                            f"{format_count(units)}.", "; ".join(caveats).capitalize() + ("." if caveats else ""))
    else:
        kpis["units"] = Kpi("units", "Units sold", None, KIND_COUNT, "Total number of items sold.", "",
                            "No quantity column was recognised." if sales else unavailable_sales)

    orders_value = kpis["orders"].value
    if revenue is not None and orders_value:
        aov = revenue / orders_value
        kpis["aov"] = Kpi("aov", "Average order value", aov, KIND_MONEY, "Average revenue per order.",
                          f"Total revenue {format_money(revenue, currency)} ÷ {format_count(orders_value)} "
                          f"{kpis['orders'].label.lower()} = {format_money(aov, currency)}.")
    else:
        kpis["aov"] = Kpi("aov", "Average order value", None, KIND_MONEY, "Average revenue per order.", "",
                          "Needs revenue and at least one order." if sales else unavailable_sales)

    # Returns and refunds (information)
    if has_amount and "status_group" in sales_rows.columns:
        returned = sales_rows[sales_rows["status_group"] == br.STATUS_RETURNED]["amount"].sum()
        negative_total = -revenue_rows.loc[revenue_rows["amount"] < 0, "amount"].sum()
        total_returns = float(returned + negative_total)
        kpis["returns"] = Kpi("returns", "Returns and refunds", total_returns, KIND_MONEY,
                              "Value of returned or refunded orders. Not included in revenue.",
                              f"Returned/refunded orders {format_money(returned, currency)} + negative amounts "
                              f"{format_money(negative_total, currency)} = {format_money(total_returns, currency)}.")

    # Expenses
    expense_source = None
    if expenses is not None:
        expense_total = float(expense_rows["amount"].sum()) if "amount" in expense_rows else None
        if expense_total is None:
            kpis["expenses"] = Kpi("expenses", "Total expenses", None, KIND_MONEY, "Money spent in the period.", "",
                                   "The expense file has no recognised amount column.")
        else:
            note = ("Expenses are not recorded per product, category or region, so this is the total for the "
                    "selected dates only." if filters.has_dimension_filters else "")
            kpis["expenses"] = Kpi("expenses", "Total expenses", expense_total, KIND_MONEY,
                                   "Money spent in the selected period.",
                                   f"Sum of {expenses.amount_source} in {expenses.name} over {len(expense_rows):,} "
                                   f"row(s) = {format_money(expense_total, currency)}.", note)
            expense_source = "separate"
    elif sales is not None and "cost" in revenue_rows.columns:
        cost_total = float(revenue_rows["cost"].sum())
        kpis["expenses"] = Kpi("expenses", "Cost of sales", cost_total, KIND_MONEY,
                               "Cost of the items sold, from the sales file.",
                               f"Sum of '{sales.mapping.column_for('expense_amount')}' over the counted rows = "
                               f"{format_money(cost_total, currency)}.")
        expense_source = "in-row"
    else:
        kpis["expenses"] = Kpi("expenses", "Total expenses", None, KIND_MONEY, "Money spent in the period.", "",
                               "No expense data. Add an expense file on this page to see expenses and net profit.")

    # Profit and margin
    profit, profit_type, profit_period, profit_revenue, profit_note, profit_calc = None, None, None, None, "", ""
    different_currency = bool(sales and expenses and sales.currency and expenses.currency
                              and sales.currency != expenses.currency)
    if revenue is None or expense_source is None or not kpis["expenses"].available:
        profit_note = "Needs both revenue and expenses."
    elif different_currency:
        profit_note = "Sales and expenses are in different currencies."
    elif expense_source == "in-row":
        profit_type = "gross"
        cost = revenue_rows["cost"]
        both = revenue_rows[cost.notna()]
        profit_revenue = float(both["amount"].sum())
        profit = profit_revenue - float(both["cost"].sum())
        profit_calc = (f"Revenue {format_money(profit_revenue, currency)} − cost of sales "
                       f"{format_money(both['cost'].sum(), currency)} = {format_money(profit, currency)}.")
        if len(both) < len(revenue_rows):
            profit_note = f"{len(revenue_rows) - len(both):,} counted row(s) without a cost are left out of profit."
    elif filters.has_dimension_filters:
        profit_note = ("Not shown while filtering by product, category or region, because expenses are not "
                       "recorded per product, category or region.")
    elif not (sales.has_dates and expenses.has_dates):
        profit_note = "Both files need dates so that revenue and expenses can be matched to the same period."
    else:
        sales_start, sales_end = sales.date_range
        expense_start, expense_end = expenses.date_range
        start = max(sales_start, expense_start, pd.Timestamp(filters.start) if filters.start else sales_start)
        end = min(sales_end, expense_end, pd.Timestamp(filters.end) if filters.end else sales_end)
        if start > end:
            profit_note = "The sales and expense files don't cover any of the same dates in the selected period."
        else:
            profit_type = "net"
            profit_period = (start, end)
            in_period = revenue_rows[(revenue_rows["date"] >= start) & (revenue_rows["date"] <= end)]
            expense_in = expense_rows[(expense_rows["date"] >= start) & (expense_rows["date"] <= end)]
            profit_revenue = float(in_period["amount"].sum())
            spent = float(expense_in["amount"].sum())
            profit = profit_revenue - spent
            profit_calc = (f"Revenue {format_money(profit_revenue, currency)} − expenses {format_money(spent, currency)}"
                           f" = {format_money(profit, currency)}, for {format_date(start)} – {format_date(end)}.")
            selected = (revenue_rows["date"].min(), revenue_rows["date"].max()) if not revenue_rows.empty else None
            if selected and (start > selected[0] or end < selected[1]):
                profit_note = (f"Calculated only for {format_date(start)} – {format_date(end)}, the dates covered by "
                               "both the sales and the expense file.")
            if sales.currency is None or expenses.currency is None:
                notes.append("At least one file does not state its currency. Profit assumes both files use the same "
                             "currency; please check.")

    profit_label = {"gross": "Gross profit", "net": "Net profit"}.get(profit_type, "Profit")
    profit_definition = {"gross": "Revenue minus the cost of the items sold.",
                         "net": "Revenue minus all business expenses."}.get(profit_type,
                                                                            "Revenue minus expenses.")
    kpis["profit"] = Kpi("profit", profit_label, profit, KIND_MONEY, profit_definition, profit_calc, profit_note)

    if profit is not None and profit_revenue:
        margin = profit / profit_revenue * 100
        kpis["margin"] = Kpi("margin", "Profit margin", margin, KIND_PERCENT,
                             "The share of revenue that is left as profit.",
                             f"{profit_label} {format_money(profit, currency)} ÷ revenue "
                             f"{format_money(profit_revenue, currency)} × 100 = {format_percent(margin)}.",
                             profit_note if profit_type == "net" else "")
    else:
        reason = "Revenue is zero, so a margin can't be calculated." if profit is not None else \
            (profit_note or "Needs profit and revenue.")
        kpis["margin"] = Kpi("margin", "Profit margin", None, KIND_PERCENT,
                             "The share of revenue that is left as profit.", "", reason)

    if sales is not None:
        status_note = _status_note(sales_rows, include_pending, currency)
        if status_note:
            notes.append(status_note)
    return DashboardResult(kpis, revenue_rows, sales_rows, expense_rows, profit_type, profit_period, currency, notes)


def format_kpi(kpi: Kpi, currency: str | None) -> str:
    if not kpi.available:
        return "—"
    if kpi.kind == KIND_MONEY:
        return format_money(kpi.value, currency)
    if kpi.kind == KIND_PERCENT:
        return format_percent(kpi.value)
    return format_count(kpi.value)


def kpi_table(result: DashboardResult) -> pd.DataFrame:
    """Every KPI with its definition and calculation, for the 'How are these calculated?' view."""
    return pd.DataFrame([{
        "KPI": kpi.label,
        "Value": format_kpi(kpi, result.currency),
        "What it means": kpi.definition,
        "How it was calculated": kpi.calculation if kpi.available else f"Not available: {kpi.note}",
        "Notes": kpi.note if kpi.available else "",
    } for kpi in result.kpis.values()])


# ---------------------------------------------------------------------------
# Chart tables
# ---------------------------------------------------------------------------
def choose_frequency(start, end) -> str:
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    if days <= br.AUTO_DAILY_MAX_DAYS:
        return "D"
    if days <= br.AUTO_WEEKLY_MAX_DAYS:
        return "W"
    return "M"


def _period_start(dates: pd.Series, frequency: str) -> pd.Series:
    return dates.dt.to_period(frequency).dt.start_time


def _period_totals(rows: pd.DataFrame, frequency: str, start, end) -> pd.Series:
    """Totals per period from `start` to `end`. Periods inside the range with no
    rows really had no sales/expenses, so they are shown as 0."""
    periods = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq=frequency).start_time
    if rows.empty:
        return pd.Series(0.0, index=periods)
    totals = rows.groupby(_period_start(rows["date"], frequency))["amount"].sum()
    return totals.reindex(periods, fill_value=0.0)


def time_table(result: DashboardResult, sales: PreparedDataset | None, expenses: PreparedDataset | None,
               filters: Filters, frequency: str) -> pd.DataFrame:
    """Revenue, expenses and (where both are known) profit per period."""
    columns = {}
    span_start, span_end = [], []
    if sales is not None and sales.has_dates and "amount" in result.revenue_rows and not result.sales_rows.empty:
        start = max(sales.date_range[0], pd.Timestamp(filters.start)) if filters.start else sales.date_range[0]
        end = min(sales.date_range[1], pd.Timestamp(filters.end)) if filters.end else sales.date_range[1]
        columns["Revenue"] = _period_totals(result.revenue_rows, frequency, start, end)
        span_start.append(start)
        span_end.append(end)
    if expenses is not None and expenses.has_dates and "amount" in result.expense_rows:
        start = max(expenses.date_range[0], pd.Timestamp(filters.start)) if filters.start else expenses.date_range[0]
        end = min(expenses.date_range[1], pd.Timestamp(filters.end)) if filters.end else expenses.date_range[1]
        if start <= end:
            columns["Expenses"] = _period_totals(result.expense_rows, frequency, start, end)
            span_start.append(start)
            span_end.append(end)
    if not columns:
        return pd.DataFrame(columns=["Period"])
    table = pd.DataFrame(columns)
    if result.profit_type == "net" and result.profit_period:
        start, end = result.profit_period
        inside = (table.index >= _period_start(pd.Series([start]), frequency)[0]) & \
                 (table.index <= _period_start(pd.Series([end]), frequency)[0])
        table["Profit"] = np.where(inside, table.get("Revenue", 0) - table.get("Expenses", 0), np.nan)
    elif result.profit_type == "gross" and "cost" in result.revenue_rows:
        rows = result.revenue_rows[result.revenue_rows["cost"].notna()]
        profit = (rows.assign(amount=rows["amount"] - rows["cost"]))
        table["Profit"] = _period_totals(profit, frequency, min(span_start), max(span_end))
    table.index.name = "Period"
    return table.reset_index()


def partial_period_note(table: pd.DataFrame, frequency: str, start, end) -> str | None:
    """Warn when the first or last period is only partly covered (e.g. a month cut in half by the filter)."""
    if table.empty or frequency == "D":
        return None
    first, last = table["Period"].iloc[0], table["Period"].iloc[-1]
    period_end = (pd.Period(last, freq=frequency).end_time).normalize()
    partial = []
    if pd.Timestamp(start) > first:
        partial.append("first")
    if pd.Timestamp(end) < period_end:
        partial.append("last")
    if not partial:
        return None
    name = FREQUENCIES[frequency].lower()
    return f"The {' and '.join(partial)} {name} shown {'is' if len(partial) == 1 else 'are'} only partly covered, " \
           f"so {'it looks' if len(partial) == 1 else 'they look'} lower than a full {name}."


def breakdown(rows: pd.DataFrame, by: str, top: int = br.TOP_ITEMS) -> pd.DataFrame:
    """Revenue (or spending) per product/category/region, largest first. Items
    beyond `top` are combined into one 'All others' row."""
    if rows.empty or by not in rows.columns or "amount" not in rows.columns:
        return pd.DataFrame(columns=["Name", "Amount", "Share", "Rows", "Units"])
    grouped = rows.groupby(by)
    table = pd.DataFrame({"Amount": grouped["amount"].sum(), "Rows": grouped.size()})
    if "quantity" in rows.columns:
        table["Units"] = grouped["quantity"].sum()
    table = table.sort_values("Amount", ascending=False)
    if len(table) > top:
        rest = table.iloc[top:]
        others = pd.DataFrame({"Amount": [rest["Amount"].sum()], "Rows": [rest["Rows"].sum()]},
                              index=[f"All others ({len(rest)})"])
        if "Units" in table:
            others["Units"] = rest["Units"].sum()
        table = pd.concat([table.iloc[:top], others])
    total = table["Amount"].sum()
    table["Share"] = table["Amount"] / total * 100 if total else np.nan
    table.index.name = "Name"
    return table.reset_index()[["Name", "Amount", "Share", "Rows"] + (["Units"] if "Units" in table else [])]


def coverage_text(dataset: PreparedDataset) -> str:
    if not dataset.has_dates:
        return "no dates"
    start, end = dataset.date_range
    return f"{format_date(start)} – {format_date(end)}"


def available_range(sales: PreparedDataset | None, expenses: PreparedDataset | None) -> tuple[date, date] | None:
    """The full span of dates in the loaded data, including any dates in the future."""
    ranges = [d.date_range for d in (sales, expenses) if d is not None and d.date_range]
    if not ranges:
        return None
    return min(r[0] for r in ranges).date(), max(r[1] for r in ranges).date()


def default_range(sales: PreparedDataset | None, expenses: PreparedDataset | None,
                  today: date | None = None) -> tuple[date, date] | None:
    """The date range the dashboard starts with: from the first date to the last
    date that is not after `today`. Future dates are usually typing mistakes (the
    Data quality page reports them), and a single one would otherwise stretch every
    chart with empty months. The rows are not removed: the user can still widen
    the range to `available_range` to include them. If every date is in the
    future (for example a plan), the full span is used."""
    full = available_range(sales, expenses)
    if full is None:
        return None
    today = today or date.today()
    if full[1] <= today:
        return full
    past = [d.rows.loc[d.rows["date"] <= pd.Timestamp(today), "date"]
            for d in (sales, expenses) if d is not None and d.has_dates]
    latest = max((dates.max() for dates in past if not dates.empty), default=None)
    if latest is None:
        return full
    return full[0], latest.date()


def future_dated(sales: PreparedDataset | None, expenses: PreparedDataset | None,
                 today: date | None = None) -> tuple[int, date | None]:
    """How many usable rows are dated after `today`, and the latest such date."""
    limit = pd.Timestamp(today or date.today())
    dates = pd.concat([d.rows.loc[d.rows["date"] > limit, "date"] for d in (sales, expenses)
                       if d is not None and d.has_dates] or [pd.Series(dtype="datetime64[ns]")])
    return len(dates), (dates.max().date() if len(dates) else None)


def future_rows_note(count: int, latest: date | None, default_end: date, selected_end: date | None) -> str | None:
    """Explain what happens to future-dated rows in the selected period."""
    if not count:
        return None
    rows = f"{count:,} row(s) are dated after today (the latest is {format_date(latest)})"
    if selected_end is not None and pd.Timestamp(selected_end) < pd.Timestamp(latest):
        return (f"{rows}. These dates may be typing mistakes, so the default period ends on "
                f"{format_date(default_end)}, the last date that is not in the future. The rows are not deleted: "
                "widen the date range to include them, or look at 'Dates in the future' on the Data quality page.")
    return (f"{rows} and are included in the selected period. Check them on the Data quality page "
            "('Dates in the future'): if they are typing mistakes they distort the figures.")
