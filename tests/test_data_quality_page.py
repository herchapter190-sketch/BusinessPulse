"""Stage 3 checks for the Data Quality screen (views/data_quality.py), run without a browser."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from config import session_keys
from tools.file_loader import load_file
from tools.sample_data import read_sample

PAGE = str(Path(__file__).resolve().parent.parent / "views" / "data_quality.py")
TIMEOUT = 90


def open_page_with(sample_key: str | None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=TIMEOUT)
    if sample_key:
        name, content = read_sample(sample_key)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
    app.run()
    assert not app.exception, app.exception
    return app


def metrics(app: AppTest) -> dict[str, str]:
    return {metric.label: metric.value for metric in app.metric}


def test_without_data_the_page_explains_what_to_do():
    app = open_page_with(None)
    assert any("No data loaded yet" in block.value for block in app.markdown)  # the empty-state card
    assert not app.metric


def test_clean_data_shows_full_score_and_no_problems():
    app = open_page_with("expenses_clean")
    values = metrics(app)
    assert values["Total rows"] == "499"
    assert values["Total columns"] == "7"
    assert values["Invalid records"] == "0"
    assert any("No problems found" in box.value for box in app.success)
    assert app.button(key="go_dashboard")
    assert any("Ready" in block.value for block in app.markdown)


def test_messy_data_shows_summary_issue_table_and_affected_rows():
    app = open_page_with("sales_messy")
    values = metrics(app)
    assert values["Total rows"] == "8,010"
    assert values["Missing values"] == "66"
    assert values["Duplicate rows"] == "20"
    assert values["Invalid records"] == "45"

    issue_table = app.dataframe[0].value
    assert list(issue_table.columns) == ["Severity", "Column", "Problem", "Affected rows", "What to do",
                                         "Example values", "Explanation"]
    assert {"Error", "Warning"} <= set(issue_table["Severity"])
    # Most serious first, and every problem says what to do about it.
    ranks = issue_table["Severity"].map({"Error": 0, "Warning": 1, "Info": 2}).tolist()
    assert ranks == sorted(ranks)
    assert issue_table["What to do"].str.len().gt(0).all()
    spaces = issue_table[issue_table["Problem"] == "Extra spaces"]
    assert (spaces["What to do"] == "Fix: Trim spaces").all()
    impossible = issue_table[issue_table["Problem"] == "Impossible dates"]
    assert (impossible["What to do"] == "Fix: Edit a value or Exclude invalid records").all()
    assert issue_table["What to do"].str.match(r"(Fix|Check|Note): ").all()
    assert app.button(key="go_cleaning_next")  # the one primary action leads to Data Cleaning
    assert any("Needs fixing" in block.value and "45 rows" in block.value for block in app.markdown)

    # Inspect rows for a chosen problem.
    inspector = app.selectbox(key="inspect_issue")
    position = next(i for i, label in enumerate(inspector.options) if "Impossible dates" in label)
    inspector.select_index(position).run()
    assert not app.exception, app.exception
    affected = app.dataframe[1].value
    assert list(affected.index) == [int(row) for row in affected.index]
    assert sorted(affected["order_date"]) == ["2025-02-30", "2025-04-31", "2025-13-05"]


def test_severity_summary_leaves_out_levels_with_no_problems():
    app = open_page_with("sales_messy")  # errors and warnings, but no Info notes
    summary = next(block.value for block in app.markdown if "warnings" in block.value and "errors" in block.value)
    assert "6 errors" in summary and "19 warnings" in summary
    assert "info" not in summary.lower() and " 0 " not in f" {summary} "


def test_future_dated_rows_can_be_inspected():
    app = open_page_with("sales_messy")
    inspector = app.selectbox(key="inspect_issue")
    labels = [label for label in inspector.options if "Dates in the future" in label]
    if not labels:
        return  # the sample's 2027 dates are no longer in the future
    inspector.select_index(inspector.options.index(labels[0])).run()
    assert not app.exception, app.exception
    assert all(value.startswith("2027-") for value in app.dataframe[1].value["order_date"])


def test_severity_filter_hides_other_levels():
    app = open_page_with("sales_messy")
    app.segmented_control(key="severity_filter").set_value(["Error"]).run()
    assert not app.exception, app.exception
    assert set(app.dataframe[0].value["Severity"]) == {"Error"}


def test_severity_filter_with_no_matches_explains_instead_of_blank_table():
    app = open_page_with("sales_messy")  # no Info notes in this file
    app.segmented_control(key="severity_filter").set_value(["Info"]).run()
    assert not app.exception, app.exception
    assert any("no problems at the selected level (Info)" in box.value for box in app.info)
    app.segmented_control(key="severity_filter").set_value([]).run()
    assert any("Choose at least one level" in box.value for box in app.info)


def test_changing_column_recognition_updates_the_checks():
    app = open_page_with("sales_messy")
    role_box = next(box for box in app.selectbox if box.label == "Record ID (order / transaction / invoice)")
    role_box.select("— Not in this file —").run()
    assert not app.exception, app.exception
    # Without an ID column, duplicate rows are only a warning, so fewer rows are invalid.
    assert int(metrics(app)["Invalid records"].replace(",", "")) < 45
