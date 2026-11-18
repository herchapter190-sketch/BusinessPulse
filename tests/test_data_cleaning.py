"""Stage 4 checks for tools/data_cleaning.py.

Uses the sample datasets (read only) and small in-memory tables. Numbered
sections match the Stage 4 test list.
"""

import hashlib
import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from pandas.api.types import is_datetime64_any_dtype, is_float_dtype

from config.quality_rules import QualityRules
from tools import data_cleaning as dc
from tools.data_quality import analyze_quality
from tools.file_loader import blank_mask, load_file
from tools.sample_data import SAMPLE_DIR, read_sample

RULES = QualityRules(reference_date=date(2026, 11, 17))


def session_for(table: pd.DataFrame) -> dc.CleaningSession:
    return dc.CleaningSession(table, "test-signature", "test.csv")


def text_table(rows: dict) -> pd.DataFrame:
    return pd.DataFrame({column: [str(value) for value in values] for column, values in rows.items()})


@pytest.fixture
def messy_loaded():
    return load_file(*read_sample("sales_messy"))


@pytest.fixture
def messy_session(messy_loaded):
    return session_for(messy_loaded.data)


# ---------------------------------------------------------------------------
# 1. Original data remains unchanged
# ---------------------------------------------------------------------------
def run_full_cleaning(session: dc.CleaningSession) -> None:
    session.apply(dc.propose_trim(session, dc.text_columns(session.working)))
    session.apply(dc.propose_capitalization(session, "region"))
    table = dc.suggest_category_replacements(session, "category")
    session.apply(dc.propose_category_normalization(session, "category",
                                                    dict(zip(table["Current value"], table["Replace with"]))))
    session.apply(dc.propose_date_conversion(session, "order_date", dc.ORDER_DAY_FIRST))
    session.apply(dc.propose_number_conversion(session, "unit_price", placeholders_to_blank=True))
    session.apply(dc.propose_exclude_duplicates(session), confirmed=True)


def test_original_data_is_never_changed(messy_loaded, messy_session):
    before = messy_loaded.data.copy(deep=True)
    run_full_cleaning(messy_session)
    pd.testing.assert_frame_equal(messy_loaded.data, before)           # the caller's table
    pd.testing.assert_frame_equal(messy_session.original, before)      # the session's own original
    assert messy_session.original_row_count == len(before)


def test_session_original_cannot_be_changed_through_its_property(messy_session):
    copy = messy_session.original
    copy.iloc[0, 0] = "CHANGED"
    assert messy_session.original.iloc[0, 0] != "CHANGED"


def test_sample_files_on_disk_are_unchanged(messy_session):
    run_full_cleaning(messy_session)
    recorded = json.loads((SAMPLE_DIR / "checksums.json").read_text(encoding="utf-8"))
    for name, expected in recorded.items():
        assert hashlib.sha256((SAMPLE_DIR / name).read_bytes()).hexdigest() == expected


def test_proposals_do_not_change_anything_until_applied(messy_session):
    before = messy_session.working.copy()
    dc.propose_trim(messy_session, dc.text_columns(messy_session.working))
    dc.propose_date_conversion(messy_session, "order_date", dc.ORDER_DAY_FIRST)
    dc.propose_exclude_duplicates(messy_session)
    pd.testing.assert_frame_equal(messy_session.working, before)
    assert messy_session.version == 0 and not messy_session.audit


# ---------------------------------------------------------------------------
# 2. Trimming spaces
# ---------------------------------------------------------------------------
def test_trim_removes_leading_and_trailing_spaces():
    session = session_for(text_table({"Region": ["  North", "South ", " East ", "West"], "Qty": ["1", "2", "3", "4"]}))
    proposal = dc.propose_trim(session, ["Region", "Qty"])
    assert proposal.affected_count == 3 and list(proposal.new_columns) == ["Region"]
    assert proposal.examples.iloc[0].to_dict() == {"Row": 1, "Column": "Region", "Before": '"  North"', "After": "North"}
    session.apply(proposal)
    assert list(session.working["Region"]) == ["North", "South", "East", "West"]


def test_trim_can_also_collapse_double_spaces():
    session = session_for(text_table({"Name": ["Yoga  Mat ", "Pen"]}))
    assert dc.propose_trim(session, ["Name"]).new_columns["Name"][0] == "Yoga  Mat"
    assert dc.propose_trim(session, ["Name"], collapse_inner_spaces=True).new_columns["Name"][0] == "Yoga Mat"


def test_trim_on_messy_sample_fixes_every_logged_whitespace_problem(messy_session):
    messy_session.apply(dc.propose_trim(messy_session, dc.text_columns(messy_session.working)))
    report = analyze_quality(messy_session.working, rules=RULES)
    assert not report.find("extra_spaces")


# ---------------------------------------------------------------------------
# 3. Text normalization
# ---------------------------------------------------------------------------
def test_capitalization_matches_most_common_spelling():
    session = session_for(text_table({"Region": ["North"] * 5 + ["north", "NORTH", " south", "South", "South"]}))
    session.apply(dc.propose_capitalization(session, "Region"))
    assert list(session.working["Region"]) == ["North"] * 7 + [" South", "South", "South"]  # spaces kept


@pytest.mark.parametrize("style, expected", [
    (dc.CASE_TITLE, ["Credit Card", "Bank Transfer"]),
    (dc.CASE_UPPER, ["CREDIT CARD", "BANK TRANSFER"]),
    (dc.CASE_LOWER, ["credit card", "bank transfer"]),
])
def test_capitalization_styles(style, expected):
    session = session_for(text_table({"Payment": ["credit CARD", "Bank transfer"]}))
    session.apply(dc.propose_capitalization(session, "Payment", style))
    assert list(session.working["Payment"]) == expected


def test_category_normalization_preview_and_selection(messy_session):
    messy_session.apply(dc.propose_trim(messy_session, ["category"]))
    table = dc.suggest_category_replacements(messy_session, "category")
    suggested = dict(zip(table["Current value"], table["Replace with"]))
    assert suggested["Electronis"] == "Electronics"
    assert suggested["Home and Kitchen"] == "Home & Kitchen"
    assert suggested["Beauty"] == "Beauty & Personal Care"
    assert table["Rows"].sum() == 20

    # The user keeps "Beauty" as it is and applies the rest.
    chosen = {current: target for current, target in suggested.items() if current != "Beauty"}
    proposal = dc.propose_category_normalization(messy_session, "category", chosen)
    assert proposal.affected_count == 18
    messy_session.apply(proposal)
    remaining = set(messy_session.working["category"])
    assert "Beauty" in remaining and "Electronis" not in remaining


def test_category_normalization_without_selection_is_blocked(messy_session):
    proposal = dc.propose_category_normalization(messy_session, "category", {})
    assert proposal.blocking and not proposal.can_apply
    with pytest.raises(dc.CleaningError):
        messy_session.apply(proposal)


# ---------------------------------------------------------------------------
# 4. Date conversion
# ---------------------------------------------------------------------------
def test_dates_in_many_formats_become_real_dates():
    session = session_for(text_table({"Date": ["2025-03-05", "2025/03/06", "07-Mar-2025", "March 8, 2025",
                                               "25/03/2025", ""]}))
    proposal = dc.propose_date_conversion(session, "Date")
    assert proposal.blocking is None  # 25/03/2025 can only be day-first; nothing ambiguous
    session.apply(proposal)
    column = session.working["Date"]
    assert is_datetime64_any_dtype(column)
    assert [d.date() if not pd.isna(d) else None for d in column] == [
        date(2025, 3, 5), date(2025, 3, 6), date(2025, 3, 7), date(2025, 3, 8), date(2025, 3, 25), None]


def test_impossible_and_unreadable_dates_are_left_unchanged():
    session = session_for(text_table({"Date": ["2025-02-30", "soon", "2025-01-01"]}))
    proposal = dc.propose_date_conversion(session, "Date")
    assert proposal.affected_count == 1
    assert any("impossible" in risk for risk in proposal.risks)
    assert any("not dates" in risk for risk in proposal.risks)
    session.apply(proposal)
    assert list(session.working["Date"][:2]) == ["2025-02-30", "soon"]


def test_converted_dates_make_the_format_problem_disappear(messy_session):
    messy_session.apply(dc.propose_date_conversion(messy_session, "order_date", dc.ORDER_DAY_FIRST))
    report = analyze_quality(messy_session.working, rules=RULES)
    assert not report.find("inconsistent_date_format")
    assert report.find("invalid_date")  # the 3 impossible dates are still reported


# ---------------------------------------------------------------------------
# 5. Ambiguous dates are handled safely
# ---------------------------------------------------------------------------
def test_ambiguous_dates_are_never_converted_without_a_decision():
    session = session_for(text_table({"Date": ["03/05/2025", "04/06/2025", "2025-01-01"]}))
    evidence = dc.date_order_evidence(session.working["Date"])
    assert evidence.ambiguous == 2 and evidence.recommendation is None
    proposal = dc.propose_date_conversion(session, "Date")
    assert proposal.blocking and "day-first or month-first" in proposal.blocking
    with pytest.raises(dc.CleaningError):
        session.apply(proposal)


@pytest.mark.parametrize("order, expected", [
    (dc.ORDER_DAY_FIRST, date(2025, 5, 3)),
    (dc.ORDER_MONTH_FIRST, date(2025, 3, 5)),
])
def test_ambiguous_dates_follow_the_users_choice(order, expected):
    session = session_for(text_table({"Date": ["03/05/2025"]}))
    proposal = dc.propose_date_conversion(session, "Date", order)
    assert any("as you chose" in risk for risk in proposal.risks)
    session.apply(proposal)
    assert session.working["Date"][0].date() == expected


def test_user_can_leave_ambiguous_dates_unconverted():
    session = session_for(text_table({"Date": ["03/05/2025", "25/12/2025"]}))
    proposal = dc.propose_date_conversion(session, "Date", dc.ORDER_LEAVE)
    session.apply(proposal)
    assert session.working["Date"][0] == "03/05/2025"
    assert session.working["Date"][1] == pd.Timestamp(2025, 12, 25)


def test_evidence_recommends_but_does_not_decide(messy_session):
    evidence = dc.date_order_evidence(messy_session.working["order_date"])
    assert evidence.day_first_only > 0 and evidence.month_first_only == 0
    assert evidence.recommendation == dc.ORDER_DAY_FIRST
    assert dc.propose_date_conversion(messy_session, "order_date").blocking  # still requires a choice


def test_column_mixing_both_conventions_is_warned_about():
    session = session_for(text_table({"Date": ["25/12/2025", "12/25/2025", "03/04/2025"]}))
    proposal = dc.propose_date_conversion(session, "Date", dc.ORDER_DAY_FIRST)
    assert any("mixes both conventions" in risk for risk in proposal.risks)


# ---------------------------------------------------------------------------
# 6. Numeric conversion (currency, thousands, decimal conventions)
# ---------------------------------------------------------------------------
def test_currency_and_thousands_separators_are_converted():
    session = session_for(text_table({"Amount": ["$1,200.50", "€99", "1,500", "(30.00)", "12.5", "USD 7"]}))
    proposal = dc.propose_number_conversion(session, "Amount")
    session.apply(proposal)
    column = session.working["Amount"]
    assert is_float_dtype(column)
    assert list(column) == [1200.5, 99.0, 1500.0, -30.0, 12.5, 7.0]


def test_decimal_comma_is_detected_when_reliable():
    session = session_for(text_table({"Amount": ["12,50", "1.234,56", "7"]}))
    evidence = dc.decimal_evidence(session.working["Amount"])
    assert evidence.detected == dc.DECIMAL_COMMA
    session.apply(dc.propose_number_conversion(session, "Amount"))
    assert list(session.working["Amount"]) == [12.5, 1234.56, 7.0]


def test_conflicting_decimal_conventions_require_a_choice():
    session = session_for(text_table({"Amount": ["12.50", "12,50"]}))
    proposal = dc.propose_number_conversion(session, "Amount")
    assert proposal.blocking and "Choose which convention" in proposal.blocking
    chosen = dc.propose_number_conversion(session, "Amount", dc.DECIMAL_POINT)
    assert chosen.blocking is None


def test_ambiguous_separator_is_explained():
    session = session_for(text_table({"Amount": ["1,234", "5.50"]}))
    proposal = dc.propose_number_conversion(session, "Amount")
    assert any("could mean thousands or a decimal" in risk for risk in proposal.risks)
    session.apply(proposal)
    assert session.working["Amount"][0] == 1234.0


def test_text_values_stay_unless_placeholders_become_blank(messy_session):
    kept = dc.propose_number_conversion(messy_session, "unit_price")
    assert any("not numbers" in risk for risk in kept.risks)
    blanked = dc.propose_number_conversion(messy_session, "unit_price", placeholders_to_blank=True)
    messy_session.apply(blanked)
    column = messy_session.working["unit_price"]
    assert is_float_dtype(column) and column.isna().sum() == 6
    assert column[messy_session.working.index[0]] == 38.5


# ---------------------------------------------------------------------------
# 7. Missing-value handling
# ---------------------------------------------------------------------------
def numeric_session() -> dc.CleaningSession:
    session = session_for(text_table({"Sales": ["10", "", "30", "", "50"], "Region": ["N", "", "S", "N", ""]}))
    session.apply(dc.propose_number_conversion(session, "Sales"))
    return session


@pytest.mark.parametrize("method, value, expected", [
    (dc.MISSING_FILL_ZERO, None, 0.0),
    (dc.MISSING_FILL_MEAN, None, 30.0),
    (dc.MISSING_FILL_MEDIAN, None, 30.0),
    (dc.MISSING_FILL_VALUE, "12.5", 12.5),
])
def test_numeric_missing_values_are_filled(method, value, expected):
    session = numeric_session()
    proposal = dc.propose_missing_values(session, "Sales", method, value)
    assert proposal.affected_count == 2
    assert proposal.risks
    session.apply(proposal)
    assert list(session.working["Sales"]) == [10.0, expected, 30.0, expected, 50.0]


def test_text_missing_values_are_filled_with_unknown():
    session = numeric_session()
    session.apply(dc.propose_missing_values(session, "Region", dc.MISSING_FILL_UNKNOWN))
    assert list(session.working["Region"]) == ["N", "Unknown", "S", "N", "Unknown"]


def test_numeric_fill_needs_a_number_column():
    session = session_for(text_table({"Sales": ["10", ""]}))
    proposal = dc.propose_missing_values(session, "Sales", dc.MISSING_FILL_MEAN)
    assert proposal.blocking and "Convert numbers" in proposal.blocking


def test_excluding_rows_with_missing_values_needs_confirmation():
    session = numeric_session()
    proposal = dc.propose_missing_values(session, "Sales", dc.MISSING_EXCLUDE)
    assert proposal.requires_confirmation and proposal.affected_count == 2
    with pytest.raises(dc.CleaningError, match="confirm"):
        session.apply(proposal)
    session.apply(proposal, confirmed=True)
    assert list(session.working.index) == [0, 2, 4]
    assert session.excluded == {1: "Missing value in 'Sales'", 3: "Missing value in 'Sales'"}


def test_column_without_missing_values_is_blocked():
    session = session_for(text_table({"A": ["1", "2"]}))
    assert dc.propose_missing_values(session, "A", dc.MISSING_FILL_VALUE, "x").blocking


# ---------------------------------------------------------------------------
# 8. Duplicate review
# ---------------------------------------------------------------------------
def test_duplicates_are_listed_with_their_original(messy_session):
    duplicates = dc.find_duplicate_rows(messy_session.working)
    assert len(duplicates) == 20
    assert duplicates.iloc[0].to_dict() == {"Row": 119, "Same as row": 114}


def test_user_chooses_which_duplicates_to_exclude(messy_session):
    duplicates = dc.find_duplicate_rows(messy_session.working)
    keep_one = list(duplicates["Row"][1:])
    proposal = dc.propose_exclude_duplicates(messy_session, keep_one)
    assert proposal.affected_count == 19
    assert set(proposal.examples["Reason"].str.startswith("Duplicate of row")) == {True}
    messy_session.apply(proposal, confirmed=True)
    assert len(messy_session.working) == 8010 - 19
    assert len(dc.find_duplicate_rows(messy_session.working)) == 1  # the one the user kept
    assert 118 in messy_session.working.index  # row 119 was kept


# ---------------------------------------------------------------------------
# 9. Excluding invalid rows requires approval
# ---------------------------------------------------------------------------
def test_invalid_records_are_shown_before_exclusion(messy_session):
    invalid = dc.find_invalid_records(messy_session.working, RULES)
    assert len(invalid) == 45
    assert (invalid["Problems"] != "").all()
    proposal = dc.propose_exclude_rows(messy_session, dict(zip(invalid["Row"], invalid["Problems"])))
    assert proposal.requires_confirmation
    assert list(proposal.examples.index[:3]) == list(invalid["Row"][:3])  # exactly these rows are shown
    assert "Reason" in proposal.examples.columns

    with pytest.raises(dc.CleaningError, match="confirm"):
        messy_session.apply(proposal)
    assert len(messy_session.working) == 8010  # nothing excluded without approval

    messy_session.apply(proposal, confirmed=True)
    assert len(messy_session.working) == 8010 - 45
    assert analyze_quality(messy_session.working, rules=RULES).invalid_rows == 0


def test_excluded_rows_stay_available_with_reasons(messy_session):
    invalid = dc.find_invalid_records(messy_session.working, RULES)
    messy_session.apply(dc.propose_exclude_rows(messy_session, dict(zip(invalid["Row"], invalid["Problems"]))),
                        confirmed=True)
    table = messy_session.excluded_rows_table()
    assert len(table) == 45 and list(table.index) == list(invalid["Row"])
    assert list(table["Reason excluded"]) == list(invalid["Problems"])
    assert len(messy_session.original) == 8010


def test_rows_keep_original_numbers_after_exclusion(messy_session):
    invalid = dc.find_invalid_records(messy_session.working, RULES)
    messy_session.apply(dc.propose_exclude_rows(messy_session, dict(zip(invalid["Row"], invalid["Problems"]))),
                        confirmed=True)
    report = analyze_quality(messy_session.working, rules=RULES)
    issue = report.find("missing_value", "customer_id")[0]
    assert issue.examples[0] == "Row 20"  # same row number as in the original file


# ---------------------------------------------------------------------------
# Manual edits
# ---------------------------------------------------------------------------
def test_manual_edit_of_text_number_and_date_columns(messy_session):
    proposal = dc.propose_manual_edit(messy_session, 4978, "order_date", "2025-03-01")
    assert proposal.examples.iloc[0]["Before"] != proposal.examples.iloc[0]["After"]
    messy_session.apply(proposal)
    assert messy_session.working.at[4977, "order_date"] == "2025-03-01"

    messy_session.apply(dc.propose_number_conversion(messy_session, "quantity"))
    messy_session.apply(dc.propose_manual_edit(messy_session, 1, "quantity", "3"))
    assert messy_session.working.at[0, "quantity"] == 3.0
    assert dc.propose_manual_edit(messy_session, 1, "quantity", "three").blocking


def test_manual_edit_of_missing_or_excluded_row_is_blocked(messy_session):
    assert dc.propose_manual_edit(messy_session, 999999, "region", "x").blocking
    assert dc.propose_manual_edit(messy_session, 1, "region", "North").blocking  # same as now


# ---------------------------------------------------------------------------
# 10 & 11. Undo and reset
# ---------------------------------------------------------------------------
def test_undo_restores_the_previous_working_copy(messy_session):
    start = messy_session.working.copy()
    messy_session.apply(dc.propose_trim(messy_session, dc.text_columns(messy_session.working)))
    after_trim = messy_session.working.copy()
    messy_session.apply(dc.propose_exclude_duplicates(messy_session), confirmed=True)

    messy_session.undo()
    pd.testing.assert_frame_equal(messy_session.working, after_trim)
    assert messy_session.excluded == {}
    messy_session.undo()
    pd.testing.assert_frame_equal(messy_session.working, start)
    assert messy_session.undo() is None  # nothing left to undo
    assert not messy_session.can_undo


def test_stale_preview_cannot_be_applied(messy_session):
    old_preview = dc.propose_trim(messy_session, ["region"])
    messy_session.apply(dc.propose_capitalization(messy_session, "region"))
    with pytest.raises(dc.CleaningError, match="changed after this preview"):
        messy_session.apply(old_preview)


def test_reset_restores_the_original(messy_session):
    run_full_cleaning(messy_session)
    assert messy_session.steps_applied == 6
    messy_session.reset()
    pd.testing.assert_frame_equal(messy_session.working, messy_session.original)
    assert messy_session.excluded == {} and messy_session.steps_applied == 0
    assert all(entry.status in ("Undone", "Info") for entry in messy_session.audit)


def test_undo_history_is_limited():
    session = dc.CleaningSession(text_table({"A": [" x"] * 3}), "s", "f.csv", max_undo=2)
    for value in ("a", "b", "c"):
        session.apply(dc.propose_manual_edit(session, 1, "A", value))
    assert session.steps_applied == 2


# ---------------------------------------------------------------------------
# 12. Audit log
# ---------------------------------------------------------------------------
def test_audit_log_describes_each_change(messy_session):
    messy_session.apply(dc.propose_trim(messy_session, ["product_name"]))
    messy_session.apply(dc.propose_exclude_duplicates(messy_session), confirmed=True)
    messy_session.undo()
    log = messy_session.audit_table()
    assert list(log.columns) == ["Time", "Operation", "Column", "Rows affected", "Description", "Status"]
    assert list(log["Operation"]) == ["Trim spaces", "Exclude duplicate rows", "Undo"]
    assert list(log["Column"]) == ["product_name", "(whole rows)", "(whole rows)"]
    assert list(log["Rows affected"]) == [20, 20, 20]
    assert list(log["Status"]) == ["Applied", "Undone", "Info"]
    assert "product_name" in log["Description"][0]
    assert "Exclude duplicate rows" in log["Description"][2]
    for timestamp in log["Time"]:
        assert pd.Timestamp(timestamp)  # a real date and time


def test_audit_counts_match_the_actual_changes(messy_session):
    before = messy_session.working.copy()
    entry = messy_session.apply(dc.propose_capitalization(messy_session, "payment_method"))
    actually_changed = (before["payment_method"] != messy_session.working["payment_method"]).sum()
    assert entry.rows_affected == actually_changed == 15


# ---------------------------------------------------------------------------
# 13. Cleaned data for later stages
# ---------------------------------------------------------------------------
def test_approved_cleaned_data_is_a_separate_snapshot(messy_loaded, messy_session):
    run_full_cleaning(messy_session)
    cleaned = messy_session.approve()
    assert cleaned.row_count == len(messy_session.working) == 8010 - 20
    assert not messy_session.has_unapproved_changes

    messy_session.apply(dc.propose_capitalization(messy_session, "order_status"))
    assert messy_session.has_unapproved_changes
    assert set(cleaned.data["order_status"]) != set(messy_session.working["order_status"])  # snapshot unchanged
    assert cleaned.audit[-1].operation == "Cleaned dataset created"


def test_dataset_for_analysis_prefers_matching_cleaned_data(messy_loaded, messy_session):
    run_full_cleaning(messy_session)
    cleaned = messy_session.approve()
    data, label = dc.dataset_for_analysis(messy_loaded.data, cleaned, "test-signature")
    assert label == "cleaned" and data is cleaned.data
    data, label = dc.dataset_for_analysis(messy_loaded.data, cleaned, "another-file")
    assert label == "original" and data is messy_loaded.data
    assert dc.dataset_for_analysis(messy_loaded.data, None, "test-signature")[1] == "original"


def test_clean_sample_needs_no_changes():
    session = session_for(load_file(*read_sample("sales_clean")).data)
    assert dc.propose_trim(session, dc.text_columns(session.working)).affected_count == 0
    assert dc.find_duplicate_rows(session.working).empty
    assert dc.find_invalid_records(session.working, RULES).empty
    assert dc.suggest_category_replacements(session, "category").empty


def test_cleaned_numbers_and_dates_are_usable_for_calculations(messy_session):
    run_full_cleaning(messy_session)
    messy_session.apply(dc.propose_number_conversion(messy_session, "total_sales"))
    cleaned = messy_session.approve()
    assert is_float_dtype(cleaned.data["total_sales"])
    assert cleaned.data["total_sales"].sum() > 0
    dates = cleaned.data["order_date"]
    assert sum(isinstance(value, pd.Timestamp) for value in dates) > 7900


# ---------------------------------------------------------------------------
# Columns that already hold real numbers or dates but are stored as mixed
# "object" values (common in Excel files) can be converted (Stage 10 fix).
# ---------------------------------------------------------------------------
def test_number_column_stored_as_mixed_values_can_be_converted():
    frame = pd.DataFrame({"amount": pd.Series([12.5, 3, None, 7.25], dtype=object)})
    session = dc.CleaningSession(frame, "sig", "file.xlsx")
    proposal = dc.propose_number_conversion(session, "amount")
    assert proposal.can_apply and proposal.affected_count == 3   # the three filled cells
    assert any("no value changes" in note for note in proposal.notes)
    session.apply(proposal)
    assert session.working["amount"].dtype.kind == "f"
    assert session.working["amount"].tolist()[:2] == [12.5, 3.0]


def test_date_column_stored_as_mixed_values_can_be_converted_but_numbers_are_never_dates():
    frame = pd.DataFrame({"day": pd.Series([datetime(2025, 3, 1), datetime(2025, 3, 2), None], dtype=object),
                          "odd": pd.Series([datetime(2025, 3, 1), 45000, None], dtype=object)})
    session = dc.CleaningSession(frame, "sig", "file.xlsx")
    proposal = dc.propose_date_conversion(session, "day")
    assert proposal.can_apply
    session.apply(proposal)
    assert str(session.working["day"].dtype).startswith("datetime64")
    assert not dc.propose_date_conversion(session, "odd").can_apply   # 45000 is not turned into a 1970 date


def test_fix_plan_is_safe_and_one_undo_restores_everything():
    from tools.column_mapping import recognize_columns
    name, content = read_sample("sales_messy")
    frame = load_file(name, content).data
    session = dc.CleaningSession(frame, "sig", name)
    report = analyze_quality(frame, recognize_columns(list(frame.columns), {}), QualityRules())
    plan = dc.plan_fixes(session, report)
    assert plan.safe and plan.report_after.score.score > report.score.score
    assert all(not proposal.excluded_rows for proposal in plan.safe)           # never removes rows
    assert {item.operation for item in plan.review} >= {dc.OP_DUPLICATES, dc.OP_MISSING, dc.OP_DATES}
    blanks_before = int(blank_mask(frame).sum().sum())
    session.apply_fixes(plan)
    assert session.steps_applied == 1 and len(session.working) == len(frame)
    assert int(blank_mask(session.working).sum().sum()) <= blanks_before        # never fills blanks
    with pytest.raises(dc.CleaningError):
        session.apply_fixes(plan)                                              # a stale plan is refused
    session.undo()
    pd.testing.assert_frame_equal(session.working, session.original)
