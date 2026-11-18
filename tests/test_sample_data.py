"""Stage 1 checks: the sample datasets exist, are valid, and the messy copy
contains the deliberate quality problems documented in data/sample/README.md."""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import generate_sample_data as gen  # noqa: E402

SALES = SAMPLE_DIR / gen.SALES_FILE
EXPENSES = SAMPLE_DIR / gen.EXPENSES_FILE
MESSY = SAMPLE_DIR / gen.MESSY_SALES_FILE
ISSUE_LOG = SAMPLE_DIR / gen.ISSUE_LOG_FILE
CHECKSUMS = SAMPLE_DIR / gen.CHECKSUMS_FILE
README = SAMPLE_DIR / "README.md"

PERIOD_START = pd.Timestamp(gen.START_DATE)
PERIOD_END = pd.Timestamp(gen.END_DATE)


@pytest.fixture(scope="module")
def sales() -> pd.DataFrame:
    return pd.read_csv(SALES)


@pytest.fixture(scope="module")
def expenses() -> pd.DataFrame:
    return pd.read_csv(EXPENSES)


@pytest.fixture(scope="module")
def messy() -> pd.DataFrame:
    # Read everything as text so problems such as "N/A" or blank cells stay visible.
    return pd.read_csv(MESSY, dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def issue_log() -> pd.DataFrame:
    return pd.read_csv(ISSUE_LOG, dtype=str, keep_default_na=False)


# 1. Files exist ------------------------------------------------------------
@pytest.mark.parametrize("path", [SALES, EXPENSES, MESSY, ISSUE_LOG, CHECKSUMS, README])
def test_file_exists_and_is_not_empty(path):
    assert path.is_file(), f"Missing: {path.name}"
    assert path.stat().st_size > 0, f"Empty: {path.name}"


# 2 & 3. Clean data loads with the required columns -------------------------
def test_clean_sales_loads_with_required_columns(sales):
    assert list(sales.columns) == gen.SALES_COLUMNS
    assert len(sales) > 5000


def test_clean_expenses_loads_with_required_columns(expenses):
    assert list(expenses.columns) == gen.EXPENSE_COLUMNS
    assert len(expenses) > 300


def test_messy_sales_has_same_columns_as_clean(messy):
    assert list(messy.columns) == gen.SALES_COLUMNS


# 4. Unique IDs -------------------------------------------------------------
def test_clean_sales_order_ids_are_unique(sales):
    assert sales["order_id"].is_unique
    assert sales["order_id"].str.fullmatch(r"ORD-\d{6}").all()


def test_clean_expense_ids_are_unique(expenses):
    assert expenses["expense_id"].is_unique
    assert expenses["expense_id"].str.fullmatch(r"EXP-\d{5}").all()


# 5. Valid dates and numbers in clean data ----------------------------------
def test_clean_data_has_no_missing_values(sales, expenses):
    assert not sales.isna().any().any()
    assert not expenses.isna().any().any()


def test_clean_sales_dates_are_valid_and_cover_18_months(sales):
    dates = pd.to_datetime(sales["order_date"], format="%Y-%m-%d", errors="raise")
    assert dates.min() == PERIOD_START
    assert dates.max() == PERIOD_END
    assert dates.dt.to_period("M").nunique() == 18
    assert dates.is_monotonic_increasing


def test_clean_expense_dates_are_valid(expenses):
    dates = pd.to_datetime(expenses["expense_date"], format="%Y-%m-%d", errors="raise")
    assert dates.between(PERIOD_START, PERIOD_END).all()
    assert dates.dt.to_period("M").nunique() == 18


def test_clean_sales_numbers_are_valid(sales):
    assert pd.api.types.is_integer_dtype(sales["quantity"])
    assert (sales["quantity"] >= 1).all()
    assert (sales["unit_price"] > 0).all()
    assert ((sales["quantity"] * sales["unit_price"]) - sales["total_sales"]).abs().max() < 0.005


def test_clean_expense_amounts_are_positive(expenses):
    assert (expenses["amount"] > 0).all()


def test_clean_sales_categorical_values_are_valid(sales):
    assert set(sales["category"]) <= set(gen.CATEGORIES)
    assert set(sales["region"]) <= set(gen.REGIONS)
    assert set(sales["payment_method"]) <= set(gen.SALES_PAYMENT_METHODS)
    assert set(sales["order_status"]) <= set(gen.ORDER_STATUSES)


def test_clean_expense_categorical_values_are_valid(expenses):
    assert set(expenses["category"]) <= set(gen.EXPENSE_CATEGORIES)
    assert set(expenses["department"]) <= set(gen.DEPARTMENTS)
    assert set(expenses["payment_method"]) <= set(gen.EXPENSE_PAYMENT_METHODS)


def test_clean_sales_is_internally_consistent(sales):
    # Each product always belongs to one category; each customer to one region.
    assert (sales.groupby("product_name")["category"].nunique() == 1).all()
    assert (sales.groupby("customer_id")["region"].nunique() == 1).all()
    # A product has at most two prices: before and after the yearly price update.
    assert (sales.groupby("product_name")["unit_price"].nunique() <= 2).all()


# 6. Messy data contains the intended problems ------------------------------
def test_messy_has_missing_values(messy):
    for column, expected in [("customer_id", 25), ("region", 15), ("payment_method", 10),
                             ("quantity", 8), ("order_date", 5), ("order_id", 3)]:
        assert (messy[column] == "").sum() == expected, column


def test_messy_has_exact_duplicate_rows(messy):
    assert messy.duplicated().sum() == 20


def test_messy_has_conflicting_duplicate_order_ids(messy):
    distinct_rows = messy.drop_duplicates()
    ids = distinct_rows.loc[distinct_rows["order_id"] != "", "order_id"]
    assert ids.duplicated().sum() == 5


def test_messy_has_inconsistent_date_formats(messy):
    dates = messy["order_date"]
    assert dates.str.fullmatch(r"\d{2}/\d{2}/\d{4}").sum() == 40          # DD/MM/YYYY
    assert dates.str.fullmatch(r"\d{4}/\d{2}/\d{2}").sum() == 25          # YYYY/MM/DD
    assert dates.str.fullmatch(r"\d{2}-[A-Z][a-z]{2}-\d{4}").sum() == 25  # DD-Mon-YYYY


def test_messy_has_invalid_and_future_dates(messy):
    iso = messy.loc[messy["order_date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}"), "order_date"]
    parsed = pd.to_datetime(iso, format="%Y-%m-%d", errors="coerce")
    assert parsed.isna().sum() == 3            # dates that do not exist
    assert (parsed > PERIOD_END).sum() == 4    # dates in the future


def test_messy_has_extra_whitespace(messy):
    for column, expected in [("product_name", 20), ("category", 15), ("region", 15)]:
        values = messy[column]
        assert (values != values.str.strip()).sum() == expected, column


def test_messy_has_inconsistent_capitalization(messy):
    for column, allowed, expected in [("region", gen.REGIONS, 20),
                                      ("order_status", gen.ORDER_STATUSES, 15),
                                      ("payment_method", gen.SALES_PAYMENT_METHODS, 15)]:
        values = messy[column].str.strip()
        wrong_case = values.str.lower().isin([a.lower() for a in allowed]) & ~values.isin(list(allowed))
        assert wrong_case.sum() == expected, column


def test_messy_has_invalid_numbers(messy):
    quantity = pd.to_numeric(messy["quantity"], errors="coerce")
    unit_price = pd.to_numeric(messy["unit_price"], errors="coerce")
    assert (quantity < 0).sum() == 6
    assert (quantity == 0).sum() == 5
    assert (quantity >= 250).sum() == 3
    assert messy["unit_price"].str.startswith("$").sum() == 4
    assert (unit_price.isna() & ~messy["unit_price"].str.startswith("$")).sum() == 6


def test_messy_has_incorrect_category_names(messy):
    categories = messy["category"].str.strip()
    assert (~categories.isin(gen.CATEGORIES)).sum() == 20


def test_messy_has_unknown_statuses(messy):
    statuses = messy["order_status"].str.strip().str.lower()
    assert (~statuses.isin([s.lower() for s in gen.ORDER_STATUSES])).sum() == 3


def test_messy_has_total_mismatches(messy):
    numbers = messy[["quantity", "unit_price", "total_sales"]].apply(pd.to_numeric, errors="coerce")
    valid = numbers.notna().all(axis=1) & (numbers["quantity"] > 0) & (numbers["quantity"] < 250)
    mismatch = (numbers["quantity"] * numbers["unit_price"] - numbers["total_sales"]).abs() > 0.005
    assert (valid & mismatch).sum() == 4


def test_issue_log_matches_messy_file(messy, issue_log):
    """Every logged problem is really present at the logged row and column."""
    for issue in issue_log.itertuples():
        row = messy.iloc[int(issue.messy_row_number) - 1]
        assert row["order_id"] == issue.order_id
        if issue.column != "(entire row)":
            assert row[issue.column] == issue.messy_value
            assert issue.messy_value != issue.original_value


def test_messy_rows_without_issues_are_identical_to_clean(sales, messy, issue_log):
    """The messy file is a copy of the clean file; only logged rows were changed."""
    clean_text = pd.read_csv(SALES, dtype=str, keep_default_na=False).set_index("order_id")
    logged_rows = set(issue_log["messy_row_number"].astype(int))
    untouched = messy[~(messy.index + 1).isin(logged_rows)].set_index("order_id")
    # Each changed row has exactly one log entry; the 20 duplicate copies are extra rows.
    changed_clean_rows = len(issue_log) - 20
    assert len(untouched) == len(sales) - changed_clean_rows
    assert untouched.index.is_unique
    pd.testing.assert_frame_equal(untouched, clean_text.loc[untouched.index])


# 7. Original clean datasets are unchanged ----------------------------------
def test_sample_files_match_recorded_checksums():
    recorded = json.loads(CHECKSUMS.read_text(encoding="utf-8"))
    for name, expected in recorded.items():
        actual = hashlib.sha256((SAMPLE_DIR / name).read_bytes()).hexdigest()
        assert actual == expected, f"{name} has been modified since it was generated"


def test_generator_is_reproducible(tmp_path):
    """Regenerating into a separate folder gives byte-identical files and
    leaves the files in data/sample untouched."""
    before = {p.name: p.read_bytes() for p in SAMPLE_DIR.glob("*.csv")}
    gen.generate_all(tmp_path)
    for name, content in before.items():
        assert (tmp_path / name).read_bytes() == content, f"{name} is not reproducible"
        assert (SAMPLE_DIR / name).read_bytes() == content, f"{name} was modified"
