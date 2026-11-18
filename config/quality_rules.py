"""Configurable rules for column recognition and data-quality checks.

Change the values here to adjust how strict BusinessPulse is. Every rule is
explained so its effect is clear. No rule ever changes the data itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Column roles: the business meaning BusinessPulse looks for in a column.
# ---------------------------------------------------------------------------
ROLE_LABELS = {
    "id": "Record ID (order / transaction / invoice)",
    "date": "Date",
    "sales_amount": "Sales / revenue amount",
    "expense_amount": "Expense / cost amount",
    "quantity": "Quantity",
    "unit_price": "Unit price",
    "product": "Product / item",
    "category": "Category",
    "customer": "Customer",
    "region": "Region / location",
    "payment_method": "Payment method",
    "status": "Status",
    "department": "Department",
    "description": "Description",
}

NUMERIC_ROLES = frozenset({"sales_amount", "expense_amount", "quantity", "unit_price"})
DATE_ROLES = frozenset({"date"})
# Columns whose values are a short list of names, checked for spelling/capitalization.
CATEGORY_ROLES = frozenset({"product", "category", "region", "payment_method", "status", "department"})
# Columns that are identifiers or free text: never treated as categories.
FREE_TEXT_ROLES = frozenset({"id", "customer", "description"})

# Column-name synonyms. Names are compared after "normalizing": lower case,
# punctuation and underscores become spaces, "OrderDate" becomes "order date".
COLUMN_SYNONYMS = {
    "id": ["order id", "order number", "order no", "order", "transaction id", "transaction number",
           "transaction no", "transaction", "txn id", "txn ref", "transaction ref", "invoice id",
           "invoice number", "invoice no", "invoice", "receipt number", "receipt no", "receipt id",
           "sale id", "sales id", "expense id", "record id", "reference", "reference number", "ref", "id"],
    "date": ["date", "order date", "transaction date", "txn date", "invoice date", "sale date", "sales date",
             "expense date", "purchase date", "payment date", "posting date", "booking date", "day"],
    "sales_amount": ["revenue", "sales", "total sales", "sales amount", "sale amount", "total revenue",
                     "gross sales", "net sales", "sales value", "order total", "order value", "order amount",
                     "line total", "subtotal", "turnover", "income", "amount", "total", "total amount", "value"],
    "expense_amount": ["expense", "expenses", "expense amount", "cost", "costs", "total cost", "spending",
                       "spend", "amount spent", "expenditure", "outgoing", "outgoings"],
    "quantity": ["quantity", "qty", "units", "unit sold", "units sold", "quantity sold", "number of units",
                 "no of units", "pieces", "pcs", "volume", "count"],
    "unit_price": ["unit price", "price", "price per unit", "price each", "unit cost", "rate", "selling price",
                   "sale price", "list price", "unit rate"],
    "product": ["product", "product name", "item", "item name", "item description", "sku name", "article",
                "service", "product description", "goods"],
    "category": ["category", "product category", "expense category", "item category", "type",
                 "category name", "product type", "group", "product group", "segment", "class"],
    "customer": ["customer", "customer id", "customer number", "customer no", "customer name", "client",
                 "client id", "client name", "buyer", "account", "account id"],
    "region": ["region", "location", "area", "territory", "state", "province", "city", "country", "zone",
               "branch", "store", "market", "sales region"],
    "payment_method": ["payment method", "payment type", "payment", "payment mode", "method of payment",
                       "pay method", "tender", "paid by", "paid with"],
    "status": ["status", "order status", "payment status", "transaction status", "state of order"],
    "department": ["department", "dept", "team", "cost center", "cost centre", "division", "business unit"],
    "description": ["description", "details", "notes", "note", "memo", "narrative", "comment", "comments",
                    "remarks"],
}

# Generic money words such as "Amount" mean revenue in a sales file but cost in
# an expense file. If any column name contains one of these words, a generic
# amount column is treated as an expense amount instead of sales.
GENERIC_AMOUNT_SYNONYMS = frozenset({"amount", "total", "total amount", "value"})
EXPENSE_CONTEXT_WORDS = frozenset({"expense", "expenses", "spend", "spending", "vendor", "supplier",
                                   "department", "dept", "cost", "costs", "expenditure"})

# Words ignored when matching names, e.g. "Revenue (USD)" matches "revenue".
IGNORED_NAME_WORDS = frozenset({"usd", "eur", "gbp", "pkr", "inr", "aud", "cad", "jpy", "cny", "aed", "sar"})


# ---------------------------------------------------------------------------
# Data-quality rules
# ---------------------------------------------------------------------------
ERROR = "Error"
WARNING = "Warning"
INFO = "Info"


def _default_sign_rules() -> dict[str, dict[str, str | None]]:
    # What a negative or zero value means for each kind of number.
    # None = allowed (not reported). Negatives are never automatically errors
    # for money or quantities, because refunds, returns and credits are real.
    return {
        "quantity": {"negative": WARNING, "zero": WARNING},
        "unit_price": {"negative": WARNING, "zero": WARNING},
        "sales_amount": {"negative": WARNING, "zero": WARNING},
        "expense_amount": {"negative": WARNING, "zero": WARNING},
    }


@dataclass
class QualityRules:
    # Missing values in these roles are errors (they are needed for calculations).
    # Missing values anywhere else are warnings.
    required_roles: frozenset = frozenset({"id", "date", "sales_amount", "expense_amount", "quantity", "unit_price"})

    sign_rules: dict = field(default_factory=_default_sign_rules)

    # A column without a recognised role is treated as numbers (or dates) when at
    # least this share of its filled values look like numbers (or dates).
    expected_type_min_share: float = 0.9
    # A column is "mixed" when a second kind of value (numbers, dates or text)
    # makes up at least this share of its filled values.
    mixed_type_min_share: float = 0.1

    # Extreme values: outside Q1 - k×IQR … Q3 + k×IQR (IQR = middle 50% spread).
    # A large k means only truly extreme values (like a quantity of 999 where
    # 1–5 is normal) are reported. Needs at least `extreme_value_min_count` numbers.
    extreme_value_iqr_multiplier: float = 10.0
    extreme_value_min_count: int = 20

    # Total check: |quantity × unit price − total| larger than this is reported.
    total_tolerance: float = 0.01

    # Dates after the reference date (default: today) are reported as future dates.
    reference_date: date | None = None
    min_plausible_year: int = 1990
    # How to read 03/05/2025 when a column gives no clue: day first (3 May).
    day_first_default: bool = True

    # Category checks apply to text columns with at most this many different values.
    category_max_distinct: int = 100
    # Spelling check: names at least this similar (0–1) are compared...
    similar_name_threshold: float = 0.85
    # ...and the rarer one is reported when it is used at most this fraction as often.
    rare_variant_ratio: float = 0.1

    # Values that mean "no real value was entered".
    placeholder_values: frozenset = frozenset({
        "n/a", "na", "#n/a", "null", "none", "nil", "unknown", "tbd", "tba", "?", "??", "???",
        "-", "--", "missing", "not available", "not applicable", ".",
    })

    # Score: rows with warnings only count this much (0–1); rows with errors count 0.
    warning_row_weight: float = 0.5
    # Score points removed for each completely empty column, up to the maximum.
    empty_column_penalty: float = 2.0
    max_empty_column_penalty: float = 10.0
    # (minimum score, grade), checked from the top.
    grades: tuple = ((98.0, "Excellent"), (90.0, "Good"), (75.0, "Fair"), (0.0, "Poor"))

    def today(self) -> date:
        return self.reference_date or date.today()
