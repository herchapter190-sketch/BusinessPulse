"""Stage 3 checks for column recognition (tools/column_mapping.py)."""

import pytest

from config.quality_rules import COLUMN_SYNONYMS
from tools.column_mapping import EXACT, MANUAL, PARTIAL, normalize_column_name, recognize_columns


@pytest.mark.parametrize("name, expected", [
    ("OrderDate", "order date"),
    ("Total_Sales (USD)", "total sales usd"),
    ("order-id", "order id"),
    ("Order #", "order number"),
    ("CustomerID", "customer id"),
    ("  Unit   Price ", "unit price"),
    ("Home & Kitchen", "home and kitchen"),
])
def test_normalize_column_name(name, expected):
    assert normalize_column_name(name) == expected


@pytest.mark.parametrize("column, role", [
    # The examples from the project brief
    ("Revenue", "sales_amount"), ("Sales", "sales_amount"), ("Total Sales", "sales_amount"),
    ("Amount", "sales_amount"),
    ("Expense", "expense_amount"), ("Cost", "expense_amount"), ("Spending", "expense_amount"),
    ("Order ID", "id"), ("Transaction ID", "id"),
    ("Order Date", "date"), ("Date", "date"), ("Transaction Date", "date"),
    ("Product", "product"), ("Item", "product"),
    ("Quantity", "quantity"), ("Units", "quantity"),
    ("Region", "region"), ("Location", "region"),
    # Other common spellings
    ("order_id", "id"), ("OrderNumber", "id"), ("Invoice No.", "id"), ("Txn Ref", "id"),
    ("total_sales", "sales_amount"), ("Net Sales (EUR)", "sales_amount"), ("Revenue USD", "sales_amount"),
    ("Qty", "quantity"), ("Units Sold", "quantity"),
    ("unit_price", "unit_price"), ("Price Each", "unit_price"),
    ("Payment Method", "payment_method"), ("PaymentType", "payment_method"),
    ("order_status", "status"), ("Dept", "department"), ("Notes", "description"),
])
def test_recognises_common_names(column, role):
    assert recognize_columns([column]).column_for(role) == column


def test_partial_match_prefers_the_last_word():
    mapping = recognize_columns(["Customer Region", "Sales Rep Notes"])
    assert mapping.column_for("region") == "Customer Region"
    assert mapping.matches["region"].confidence == PARTIAL
    assert mapping.column_for("description") == "Sales Rep Notes"


def test_exact_match_beats_partial_match():
    mapping = recognize_columns(["Ship Date", "Order Date"])
    assert mapping.column_for("date") == "Order Date"
    assert mapping.matches["date"].confidence == EXACT


def test_unfamiliar_columns_are_left_unmatched():
    mapping = recognize_columns(["Colour", "Weight kg", "Warehouse Bin"])
    assert mapping.matches == {}
    assert mapping.unmatched_columns == ["Colour", "Weight kg", "Warehouse Bin"]
    assert mapping.dataset_kind() == "unknown"


def test_generic_amount_means_expense_in_an_expense_file():
    mapping = recognize_columns(["expense_id", "expense_date", "category", "amount", "department"])
    assert mapping.column_for("expense_amount") == "amount"
    assert mapping.column_for("sales_amount") is None
    assert mapping.dataset_kind() == "expenses"


def test_generic_amount_means_sales_in_a_sales_file():
    mapping = recognize_columns(["Order ID", "Date", "Product", "Amount"])
    assert mapping.column_for("sales_amount") == "Amount"
    assert mapping.dataset_kind() == "sales"


def test_each_column_gets_at_most_one_role():
    mapping = recognize_columns(["Date", "Transaction Date", "Sales", "Revenue"])
    columns = [match.column for match in mapping.matches.values()]
    assert len(columns) == len(set(columns))


def test_manual_choice_overrides_automatic_recognition():
    columns = ["Ref", "When", "Value", "Area"]
    automatic = recognize_columns(columns)
    assert automatic.column_for("date") is None
    mapping = recognize_columns(columns, {"date": "When", "region": None, "description": "Area"})
    assert mapping.column_for("date") == "When"
    assert mapping.matches["date"].confidence == MANUAL
    assert mapping.column_for("region") is None
    assert mapping.column_for("description") == "Area"


def test_manual_choice_moves_a_column_from_its_old_role():
    mapping = recognize_columns(["Sales", "Date"], {"expense_amount": "Sales"})
    assert mapping.column_for("expense_amount") == "Sales"
    assert mapping.column_for("sales_amount") is None


def test_unknown_override_values_are_ignored():
    mapping = recognize_columns(["Date"], {"date": "No such column", "not_a_role": "Date"})
    assert mapping.column_for("date") == "Date"


def test_synonym_lists_have_no_duplicates():
    every = [synonym for synonyms in COLUMN_SYNONYMS.values() for synonym in synonyms]
    assert len(every) == len(set(every))
