"""Stage 3 checks for the data-quality analysis (tools/data_quality.py).

Covers: clean sales, clean expenses, the messy sales file (verified against its
issue log), an unfamiliar dataset, missing columns, empty columns and mixed types.
Sample files are only read; test tables are built in memory.
"""

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from config.quality_rules import ERROR, INFO, WARNING, QualityRules
from tools.column_mapping import recognize_columns
from tools.data_quality import (
    NUM_CURRENCY, NUM_FORMATTED, NUM_PLAIN, NUM_TEXT,
    affected_rows_table, analyze_quality, calculate_score, classify_date, columns_table, issues_table,
    parse_number,
)
from tools.file_loader import load_file
from tools.sample_data import SAMPLE_DIR, read_sample

RULES = QualityRules(reference_date=date(2026, 11, 17))


def analyze(table: pd.DataFrame, rules: QualityRules = RULES, overrides=None):
    return analyze_quality(table, recognize_columns(list(table.columns), overrides), rules)


def text_table(rows: dict) -> pd.DataFrame:
    """Build a table the way a CSV upload looks: every value is text."""
    return pd.DataFrame({column: [str(value) for value in values] for column, values in rows.items()})


def checks(report) -> set[tuple[str, str | None]]:
    return {(issue.check, issue.column) for issue in report.issues}


@pytest.fixture(scope="module")
def messy():
    loaded = load_file(*read_sample("sales_messy"))
    return loaded.data, analyze(loaded.data)


# ---------------------------------------------------------------------------
# Reading single values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value, kind, number", [
    ("12", NUM_PLAIN, 12.0), ("-3.5", NUM_PLAIN, -3.5), (7, NUM_PLAIN, 7.0), (2.25, NUM_PLAIN, 2.25),
    ("$24.99", NUM_CURRENCY, 24.99), ("€1,200.00", NUM_CURRENCY, 1200.0), ("USD 15", NUM_CURRENCY, 15.0),
    ("-$5.00", NUM_CURRENCY, -5.0),
    ("1,234.50", NUM_FORMATTED, 1234.5), ("(12.00)", NUM_FORMATTED, -12.0), ("15%", NUM_FORMATTED, 15.0),
    ("N/A", NUM_TEXT, None), ("twelve", NUM_TEXT, None), ("12,50", NUM_TEXT, None), (True, NUM_TEXT, None),
])
def test_parse_number(value, kind, number):
    assert parse_number(value) == (kind, number)


@pytest.mark.parametrize("value, style, valid", [
    ("2025-03-05", "YYYY-MM-DD", True),
    ("2025/03/05", "YYYY/MM/DD", True),
    ("05-Mar-2025", "DD-Mon-YYYY", True),
    ("March 5, 2025", "Month DD, YYYY", True),
    ("2025-02-30", "YYYY-MM-DD", False),
    ("2025-13-05", "YYYY-MM-DD", False),
    (datetime(2025, 3, 5), "Excel date", True),
    ("next tuesday", None, False),
])
def test_classify_date(value, style, valid):
    found_style, parsed, _ = classify_date(value)
    assert found_style == style
    assert (parsed is not None) == valid


# ---------------------------------------------------------------------------
# 1 & 2. Clean sample data: no false alarms
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key, kind", [("sales_clean", "sales"), ("expenses_clean", "expenses")])
def test_clean_samples_have_no_problems(key, kind):
    loaded = load_file(*read_sample(key))
    report = analyze(loaded.data)
    assert report.issues == [], issues_table(report).to_string()
    assert report.score.score == 100.0
    assert report.score.grade == "Excellent"
    assert (report.missing_cells, report.duplicate_rows, report.invalid_rows, report.warning_rows) == (0, 0, 0, 0)
    assert report.mapping.dataset_kind() == kind


def test_clean_sales_columns_are_recognised():
    report = analyze(load_file(*read_sample("sales_clean")).data)
    expected = {"id": "order_id", "date": "order_date", "customer": "customer_id", "product": "product_name",
                "category": "category", "quantity": "quantity", "unit_price": "unit_price",
                "sales_amount": "total_sales", "region": "region", "payment_method": "payment_method",
                "status": "order_status"}
    assert {role: match.column for role, match in report.mapping.matches.items()} == expected


def test_analysis_does_not_change_the_data():
    data = load_file(*read_sample("sales_messy")).data
    before = data.copy()
    analyze(data)
    pd.testing.assert_frame_equal(data, before)


# ---------------------------------------------------------------------------
# 3. Messy sample data, verified against the issue log
# ---------------------------------------------------------------------------
LOG_TO_CHECK = {
    "missing_value": "missing_value", "missing_order_id": "missing_value",
    "exact_duplicate": "duplicate_row", "duplicate_order_id": "duplicate_id",
    "inconsistent_date_format": "inconsistent_date_format", "impossible_date": "invalid_date",
    "future_date": "future_date", "extra_whitespace": "extra_spaces",
    "inconsistent_capitalization": "capitalization", "incorrect_category": "suspicious_category",
    "implausible_value": "extreme_value", "total_mismatch": "total_mismatch", "unknown_status": "placeholder_value",
}
INVALID_NUMBER_TO_CHECK = {
    "negative quantity": "negative_value", "zero quantity": "zero_value",
    "text instead of a number": "text_in_number", "currency symbol in number": "currency_format",
}


def test_every_logged_problem_is_detected(messy):
    _, report = messy
    log = pd.read_csv(SAMPLE_DIR / "sales_messy_issue_log.csv", dtype=str, keep_default_na=False)
    missed = []
    for entry in log.itertuples():
        if entry.issue_type == "invalid_number":
            check = INVALID_NUMBER_TO_CHECK[entry.detail]
        else:
            check = LOG_TO_CHECK[entry.issue_type]
        column = None if check == "duplicate_row" else entry.column
        if int(entry.messy_row_number) - 1 not in report.rows_for(check, column):
            missed.append((entry.messy_row_number, entry.issue_type, entry.column, entry.messy_value))
    assert missed == [], f"{len(missed)} logged problems were not detected: {missed[:10]}"


def test_messy_rows_flagged_outside_the_log_are_only_reused_id_originals(messy):
    _, report = messy
    log = pd.read_csv(SAMPLE_DIR / "sales_messy_issue_log.csv", dtype=str, keep_default_na=False)
    logged_rows = set(log["messy_row_number"].astype(int) - 1)
    for issue in report.issues:
        outside = set(issue.rows) - logged_rows
        if issue.check == "duplicate_id":
            assert len(outside) == 5  # the five orders whose IDs were reused
        else:
            assert not outside, f"{issue.check} on {issue.column} flags rows with no known problem"


def test_messy_summary_numbers(messy):
    _, report = messy
    assert report.row_count == 8010 and report.column_count == 11
    assert report.missing_cells == 66        # 25 + 15 + 10 + 8 + 5 + 3 blank order IDs
    assert report.duplicate_rows == 20
    assert report.invalid_rows == 45         # rows with at least one Error
    assert report.score.score < 100
    assert report.score.grade == "Good"


@pytest.mark.parametrize("check, column, severity, count", [
    ("duplicate_row", None, ERROR, 20),
    ("missing_value", "order_id", ERROR, 3),
    ("missing_value", "order_date", ERROR, 5),
    ("missing_value", "quantity", ERROR, 8),
    ("missing_value", "customer_id", WARNING, 25),   # not needed for calculations
    ("invalid_date", "order_date", ERROR, 3),
    ("text_in_number", "unit_price", ERROR, 6),
    ("currency_format", "unit_price", WARNING, 4),
    ("negative_value", "quantity", WARNING, 6),       # could be a return
    ("zero_value", "quantity", WARNING, 5),
    ("extreme_value", "quantity", WARNING, 3),
    ("inconsistent_date_format", "order_date", WARNING, 90),
    ("future_date", "order_date", WARNING, 4),
    ("suspicious_category", "category", WARNING, 20),
    ("placeholder_value", "order_status", WARNING, 3),
    ("duplicate_id", "order_id", WARNING, 10),
])
def test_messy_severity_and_counts(messy, check, column, severity, count):
    _, report = messy
    found = report.find(check, column)
    assert len(found) == 1, f"expected one {check} issue on {column}"
    assert found[0].severity == severity
    assert found[0].affected_count == count


def test_every_issue_has_examples_and_explanation(messy):
    _, report = messy
    for issue in report.issues:
        assert issue.explanation
        if issue.rows:
            assert issue.examples


def test_affected_rows_table_matches_preview_numbering(messy):
    data, report = messy
    issue = report.find("invalid_date", "order_date")[0]
    table = affected_rows_table(data, issue)
    assert list(table.index) == [row + 1 for row in issue.rows]
    assert set(table["order_date"]) == {"2025-02-30", "2025-04-31", "2025-13-05"}


# ---------------------------------------------------------------------------
# 4. Unfamiliar dataset with different column names
# ---------------------------------------------------------------------------
def test_unfamiliar_column_names_are_recognised_and_checked():
    table = text_table({
        "Txn Ref": ["T1", "T2", "T3", "T3", "T5"],
        "Txn Date": ["2026-01-05", "2026-01-06", "06/01/2026", "2026-01-31", "2026-02-30"],
        "Item": ["Lamp", "Lamp", "lamp", "Desk", "Desk"],
        "Units": ["1", "2", "-1", "1", "abc"],
        "Price Each": ["10", "10", "10", "50", "50"],
        "Net Sales (EUR)": ["10", "20", "-10", "50", "50"],
        "Location": ["Paris", "Lyon", "Paris", "Paris", "Lyon"],
    })
    report = analyze(table)
    roles = {role: match.column for role, match in report.mapping.matches.items()}
    assert roles == {"id": "Txn Ref", "date": "Txn Date", "product": "Item", "quantity": "Units",
                     "unit_price": "Price Each", "sales_amount": "Net Sales (EUR)", "region": "Location"}
    found = checks(report)
    assert ("duplicate_id", "Txn Ref") in found
    assert ("invalid_date", "Txn Date") in found
    assert ("inconsistent_date_format", "Txn Date") in found
    assert ("text_in_number", "Units") in found
    assert ("negative_value", "Units") in found
    assert ("capitalization", "Item") in found


# ---------------------------------------------------------------------------
# 5. Dataset with missing columns
# ---------------------------------------------------------------------------
def test_dataset_with_missing_columns_is_checked_without_errors():
    table = text_table({"Product": ["Pen", "Pen", "Cup"], "Revenue": ["5", "7.5", "3"]})
    report = analyze(table)
    assert report.mapping.missing_roles  # many roles not present: that is fine
    assert not report.find("total_mismatch")  # needs quantity and price, which are absent
    missing_notes = report.find("missing_role")
    assert [issue.problem for issue in missing_notes] == ["No date column recognised"]
    assert all(issue.severity == INFO for issue in missing_notes)
    assert report.score.score == 100.0  # missing columns are not data errors


def test_no_amount_column_is_explained():
    report = analyze(text_table({"Name": ["a", "b"], "Date": ["2025-01-01", "2025-01-02"]}))
    assert any(issue.problem == "No sales or expense amount recognised" for issue in report.issues)


# ---------------------------------------------------------------------------
# 6. Dataset with entirely empty columns
# ---------------------------------------------------------------------------
def test_completely_empty_columns():
    table = text_table({"Date": ["2025-01-01", "2025-01-02", "2025-01-03"], "Sales": ["1", "2", "3"],
                        "Notes": ["", "", ""], "Extra": ["  ", "", ""]})
    report = analyze(table)
    empty = report.find("empty_column")
    assert {issue.column for issue in empty} == {"Notes", "Extra"}
    assert all(issue.severity == WARNING and issue.scope == "column" for issue in empty)
    assert not report.find("missing_value")      # reported once as an empty column, not as missing cells
    assert report.missing_cells == 6
    assert report.score.row_score == 100.0        # rows themselves are fine...
    assert report.score.score == 96.0             # ...minus 2 points per empty column
    profiles = {profile.column: profile.treated_as for profile in report.profiles}
    assert profiles["Notes"] == "Empty"


# ---------------------------------------------------------------------------
# 7. Dataset with mixed data types
# ---------------------------------------------------------------------------
def test_mixed_data_types_in_an_unrecognised_column():
    table = text_table({"Reading": ["12", "15", "18", "20", "hello", "n/a", "2025-01-01", "22", "19", "abc"]})
    report = analyze(table)
    mixed = report.find("mixed_types", "Reading")
    assert len(mixed) == 1 and mixed[0].severity == WARNING
    assert len(mixed[0].rows) == 4  # the values that are not numbers


def test_mixed_excel_types_in_a_number_column():
    table = pd.DataFrame({"Quantity": [1, 2, "three", 4.0, datetime(2025, 1, 1)]}, dtype=object)
    report = analyze(table)
    issue = report.find("text_in_number", "Quantity")[0]
    assert issue.severity == ERROR
    assert issue.rows == [2, 4]


def test_a_few_numbers_in_a_text_column_are_not_mixed():
    names = [f"Product {letter}" for letter in "ABCDEFGHIJKLMNOPQRS"] + ["2025"]
    assert not analyze(text_table({"Label": names})).find("mixed_types")


# ---------------------------------------------------------------------------
# Rules for specific problems
# ---------------------------------------------------------------------------
def test_negative_amounts_are_warnings_not_errors():
    table = text_table({"Date": ["2025-01-01"] * 4, "Revenue": ["100", "-20", "50", "0"]})
    report = analyze(table)
    assert report.find("negative_value", "Revenue")[0].severity == WARNING
    assert report.find("zero_value", "Revenue")[0].severity == WARNING
    assert report.invalid_rows == 0


def test_negative_numbers_in_unrecognised_columns_are_not_reported():
    report = analyze(text_table({"Temperature change": ["-3", "2", "-1", "4"]}))
    assert not report.find("negative_value")


def test_sign_rules_are_configurable():
    rules = QualityRules(reference_date=date(2026, 11, 17))
    rules.sign_rules["quantity"] = {"negative": ERROR, "zero": None}
    table = text_table({"Qty": ["1", "-2", "0", "3"]})
    report = analyze(table, rules)
    assert report.find("negative_value", "Qty")[0].severity == ERROR
    assert not report.find("zero_value")


def test_future_dates_use_the_reference_date():
    table = text_table({"Date": ["2026-11-01", "2026-12-25"], "Sales": ["1", "2"]})
    assert analyze(table, QualityRules(reference_date=date(2026, 11, 17))).find("future_date")[0].rows == [1]
    assert not analyze(table, QualityRules(reference_date=date(2027, 1, 1))).find("future_date")


def test_day_month_order_is_inferred_or_flagged():
    clear = text_table({"Date": ["25/12/2025", "03/01/2026", "15/01/2026"]})
    assert not analyze(clear).find("ambiguous_date_order")
    unclear = text_table({"Date": ["03/01/2026", "04/02/2026", "05/03/2026"]})
    note = analyze(unclear).find("ambiguous_date_order")
    assert len(note) == 1 and note[0].severity == INFO


def test_duplicates_without_an_id_column_are_warnings():
    table = text_table({"Product": ["Pen", "Pen", "Cup"], "Sales": ["5", "5", "3"]})
    issue = analyze(table).find("duplicate_row")[0]
    assert issue.severity == WARNING and issue.rows == [1]


def test_currency_and_thousands_formatting():
    table = text_table({"Sales": ["1200.00", "$1,200.00", "1,500.25", "(30.00)", "900"]})
    report = analyze(table)
    assert report.find("currency_format", "Sales")[0].rows == [1]
    assert report.find("number_format", "Sales")[0].rows == [2, 3]
    assert report.find("negative_value", "Sales")[0].rows == [3]  # (30.00) is a negative number


def test_extra_spaces_and_placeholders_in_any_text_column():
    table = text_table({"Notes": [" ok", "fine", "TBD", "good  day", "fine"]})
    report = analyze(table)
    assert report.find("extra_spaces", "Notes")[0].rows == [0, 3]
    assert report.find("placeholder_value", "Notes")[0].rows == [2]


def test_suspicious_names_need_a_common_counterpart():
    common = ["Electronics"] * 30 + ["Garden"] * 30
    table = text_table({"Category": common + ["Electronis", "Gardn", "Toys"]})
    issue = analyze(table).find("suspicious_category", "Category")[0]
    assert issue.rows == [60, 61]  # "Toys" is rare but not a misspelling of anything
    assert "Electronis → Electronics" in issue.examples


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------
def test_score_formula_is_as_documented():
    from tools.data_quality import QualityIssue
    issues = [
        QualityIssue("x", "a", "p", ERROR, [0, 1], [], "e"),
        QualityIssue("y", "a", "p", WARNING, [1, 2, 3], [], "e"),
        QualityIssue("z", "a", "p", INFO, [4, 5], [], "e"),
    ]
    score = calculate_score(10, issues, RULES)
    # rows 0,1 have errors; rows 2,3 warnings only; rows 4..9 count as clean (Info doesn't count)
    assert (score.error_rows, score.warning_rows, score.clean_rows) == (2, 2, 6)
    assert score.score == pytest.approx(100 * (6 + 0.5 * 2) / 10)
    assert score.grade == "Poor"


def test_score_of_empty_table():
    report = analyze(pd.DataFrame({"a": pd.Series([], dtype=object)}))
    assert report.row_count == 0


# ---------------------------------------------------------------------------
# Display tables
# ---------------------------------------------------------------------------
def test_display_tables_have_the_documented_columns(messy):
    _, report = messy
    assert list(issues_table(report).columns) == ["Severity", "Column", "Problem", "Affected rows",
                                                  "Example values", "Explanation"]
    columns = columns_table(report)
    assert list(columns["Column"]) == list(report.mapping.columns)
    assert columns.set_index("Column").loc["order_date", "Checked as"] == "Date"
