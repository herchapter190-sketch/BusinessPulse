"""Performance analysis: sales over time and per product, category and region,
with top and bottom performers (Stage 6.3). No Streamlit code here.

Every figure starts from bm.compute_dashboard(), so the Performance page counts
exactly the same rows as the Dashboard (same version, filters, pending and
error-row choices). Nothing is invented:
- a figure whose inputs are missing is NaN (shown as "—"), never 0;
- profit is only split by product, category or region when each sale carries its
  own cost (gross profit). Expenses from a separate file are business-wide, so
  they can't be attributed to a product, category or region.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tools import business_metrics as bm

PERFORMERS = 5          # items in each of the top and bottom lists
DIMENSION_PLURALS = {"product": "products", "category": "categories", "region": "regions"}

# Column name -> (kind, definition). The page shows these as the metric definitions.
METRICS = {
    "Revenue": (bm.KIND_MONEY, "Sum of the sales amounts of counted orders (completed, plus pending if you chose "
                               "to count them), as on the Dashboard."),
    "Share": (bm.KIND_PERCENT, "This item's revenue ÷ the revenue of all items shown × 100."),
    "Orders": (bm.KIND_COUNT, "Different order IDs with at least one counted row for this item (or counted rows when "
                              "there is no order-ID column). One order can contain several products, so the "
                              "orders of all products can add up to more than the total."),
    "Units": (bm.KIND_COUNT, "Sum of the quantity column over the counted rows. Shown as — when no quantity is "
                             "recorded."),
    "Average order value": (bm.KIND_MONEY, "Revenue ÷ orders. Shown as — when there are no orders."),
    "Gross profit": (bm.KIND_MONEY, "Revenue − cost of sales, over the rows that have a cost. Only available when "
                                    "the sales file has a cost for each sale."),
    "Gross margin": (bm.KIND_PERCENT, "Gross profit ÷ revenue of the rows that have a cost × 100."),
}


# ---------------------------------------------------------------------------
# Can profit be split by product, category or region?
# ---------------------------------------------------------------------------
def profit_attribution(result: bm.DashboardResult, expenses: bm.PreparedDataset | None) -> tuple[bool, str]:
    """(available, explanation). Available only for gross profit (a cost on each sale)."""
    if result.profit_type == "gross":
        return True, ("Gross profit per item uses the cost recorded on each sale. Rows without a cost are left out "
                      "of profit and margin.")
    if expenses is not None:
        return False, ("Profit and margin are not shown per product, category or region: the expenses come from a "
                       "separate file and are not recorded per product, category or region, so they can't be "
                       "attributed to any of them. Total profit for the selected dates is on the Dashboard.")
    return False, ("Profit and margin are not shown per product, category or region: there is no cost or expense "
                   "data. Add an expense file, or a cost column in the sales file, to calculate profit.")


# ---------------------------------------------------------------------------
# Per product / category / region
# ---------------------------------------------------------------------------
def dimension_table(result: bm.DashboardResult, by: str) -> pd.DataFrame:
    """One row per item of `by` (product, category or region), largest revenue first.
    Only items with counted sales in the selected period and filters appear."""
    rows = result.revenue_rows
    columns = ["Name", "Revenue", "Share", "Orders", "Units", "Average order value"]
    if rows.empty or by not in rows.columns or "amount" not in rows.columns:
        return pd.DataFrame(columns=columns)
    grouped = rows.groupby(by)
    table = pd.DataFrame({"Revenue": grouped["amount"].sum()})
    if "order_id" in rows.columns:
        table["Orders"] = grouped["order_id"].nunique(dropna=True).astype(float)
    else:
        table["Orders"] = grouped.size().astype(float)
    if "quantity" in rows.columns:
        table["Units"] = grouped["quantity"].sum(min_count=1)     # all missing -> NaN, not 0
    else:
        table["Units"] = np.nan
    table["Average order value"] = (table["Revenue"] / table["Orders"]).where(table["Orders"] > 0)
    total = table["Revenue"].sum()
    table["Share"] = table["Revenue"] / total * 100 if total > 0 else np.nan
    if result.profit_type == "gross" and "cost" in rows.columns:
        costed = rows[rows["cost"].notna()]
        by_item = costed.groupby(by)
        costed_revenue = by_item["amount"].sum().reindex(table.index)
        profit = (by_item["amount"].sum() - by_item["cost"].sum()).reindex(table.index)
        table["Gross profit"] = profit
        table["Gross margin"] = (profit / costed_revenue * 100).where(costed_revenue != 0)
        columns += ["Gross profit", "Gross margin"]
    table = table.sort_values("Revenue", ascending=False, kind="stable")
    table.index.name = "Name"
    return table.reset_index()[columns]


def ranking_metrics(table: pd.DataFrame) -> list[str]:
    """Columns that can rank items: those with at least one figure."""
    return [column for column in ("Revenue", "Gross profit", "Gross margin", "Units", "Orders",
                                  "Average order value") if column in table and table[column].notna().any()]


def top_and_bottom(table: pd.DataFrame, metric: str, count: int = PERFORMERS) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """The highest and lowest items by `metric`, without overlap. Returns (top,
    bottom, note). With fewer than two items that have a figure, both are empty."""
    ranked = table[table[metric].notna()].sort_values(metric, ascending=False, kind="stable")
    left_out = len(table) - len(ranked)
    note = f"{left_out:,} item(s) have no {metric.lower()} figure and are not ranked." if left_out else ""
    if len(ranked) < 2:
        return ranked.iloc[0:0], ranked.iloc[0:0], note
    size = min(count, len(ranked) // 2)
    top = ranked.head(size)
    bottom = ranked.tail(size).iloc[::-1]          # lowest first
    return top, bottom, note


# ---------------------------------------------------------------------------
# Over time
# ---------------------------------------------------------------------------
def trend_table(result: bm.DashboardResult, sales: bm.PreparedDataset | None, filters: bm.Filters,
                frequency: str) -> pd.DataFrame:
    """Revenue, orders, units and average order value per period. Revenue comes from
    bm.time_table (identical to the Dashboard chart). As there, a period inside the
    selected dates with no counted sales shows 0 revenue and 0 orders; units are —
    for a period whose rows have no quantity, and average order value is — when a
    period has no orders."""
    base = bm.time_table(result, sales, None, filters, frequency)
    if base.empty or "Revenue" not in base:
        return pd.DataFrame(columns=["Period", "Revenue", "Orders", "Units", "Average order value"])
    table = base[["Period", "Revenue"]].set_index("Period")
    rows = result.revenue_rows
    period = rows["date"].dt.to_period(frequency).dt.start_time
    grouped = rows.groupby(period)
    orders = grouped["order_id"].nunique(dropna=True) if "order_id" in rows.columns else grouped.size()
    table["Orders"] = orders.reindex(table.index).fillna(0).astype(float)
    if "quantity" in rows.columns:
        units = grouped["quantity"].sum(min_count=1).reindex(table.index)
        has_rows = grouped.size().reindex(table.index).fillna(0) > 0
        table["Units"] = units.where(has_rows, 0.0)
    else:
        table["Units"] = np.nan
    table["Average order value"] = (table["Revenue"] / table["Orders"]).where(table["Orders"] > 0)
    return table.reset_index()


def format_table(table: pd.DataFrame, currency: str | None, orders_label: str = "Orders") -> pd.DataFrame:
    """Display copy: money, counts and percentages formatted, missing figures as —."""
    shown = table.copy()
    for column in shown.columns:
        kind = METRICS.get(column, (None,))[0]
        if kind == bm.KIND_MONEY:
            shown[column] = shown[column].map(lambda value: bm.format_money(value, currency))
        elif kind == bm.KIND_PERCENT:
            shown[column] = shown[column].map(bm.format_percent)
        elif kind == bm.KIND_COUNT:
            shown[column] = shown[column].map(bm.format_count)
    return shown.rename(columns={"Orders": orders_label})
