"""Stage 4 checks for the Data Cleaning screen and its links to other pages,
run without a browser."""

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from config import session_keys
from tools import data_cleaning as dc
from tools.file_loader import dataset_signature, load_file
from tools.sample_data import read_sample

PAGES = Path(__file__).resolve().parent.parent / "views"
TIMEOUT = 120


def open_page(page: str, sample_key: str | None = "sales_messy", app: AppTest | None = None) -> AppTest:
    new_app = AppTest.from_file(str(PAGES / page), default_timeout=TIMEOUT)
    if app is not None:  # carry the app's data over, like moving between pages in the browser
        for key in session_keys.FILE_SCOPED:
            if key in app.session_state:
                new_app.session_state[key] = app.session_state[key]
    elif sample_key:
        name, content = read_sample(sample_key)
        new_app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
        new_app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
    new_app.run()
    assert not new_app.exception, new_app.exception
    return new_app


def session_of(app: AppTest) -> dc.CleaningSession:
    return app.session_state[session_keys.CLEANING_SESSION]


def metrics(app: AppTest) -> dict[str, str]:
    return {metric.label: metric.value for metric in app.metric}


def choose(app: AppTest, operation: str) -> AppTest:
    app.selectbox(key="cleaning_operation").set_value(operation).run()
    assert not app.exception, app.exception
    return app


def click(app: AppTest, key: str) -> AppTest:
    app.button(key=key).click().run()
    assert not app.exception, app.exception
    return app


def test_without_data_the_page_explains_what_to_do():
    app = open_page("data_cleaning.py", sample_key=None)
    assert any("No data loaded yet" in block.value for block in app.markdown)  # the empty-state card


def test_page_starts_with_original_and_working_copy_equal():
    app = open_page("data_cleaning.py")
    values = metrics(app)
    assert values["Original rows"] == values["Working-copy rows"] == "8,010"
    assert values["Steps applied"] == "0"
    session = session_of(app)
    pd.testing.assert_frame_equal(session.working, session.original)
    assert app.button(key="undo_step").disabled
    assert app.button(key="approve").disabled


def test_review_then_apply_then_undo():
    app = choose(open_page("data_cleaning.py"), dc.OP_TRIM)
    review_metrics = metrics(app)
    assert review_metrics["Operation"] == "Trim spaces"
    assert review_metrics["Rows affected"] == "50"
    assert session_of(app).steps_applied == 0  # previewing changes nothing

    click(app, "apply_operation")
    session = session_of(app)
    assert session.steps_applied == 1
    assert metrics(app)["Steps applied"] == "1"
    assert any("Applied" in box.value for box in app.success)
    assert app.selectbox(key="cleaning_operation").value is None  # operation closed after applying

    click(app, "undo_step")
    assert session_of(app).steps_applied == 0
    pd.testing.assert_frame_equal(session_of(app).working, session_of(app).original)


def test_cancel_discards_the_proposal():
    app = choose(open_page("data_cleaning.py"), dc.OP_CASE)
    click(app, "cancel_operation")
    assert app.selectbox(key="cleaning_operation").value is None
    assert session_of(app).steps_applied == 0


def test_ambiguous_dates_need_a_decision_in_the_screen():
    app = choose(open_page("data_cleaning.py"), dc.OP_DATES)
    assert app.button(key="apply_operation").disabled
    assert any("Needs your decision" in box.value for box in app.info)
    order = app.radio(key="clean_opt_date_order")
    assert order.value is None  # nothing is pre-selected
    order.set_value(dc.ORDER_DAY_FIRST).run()
    assert not app.button(key="apply_operation").disabled
    click(app, "apply_operation")
    assert session_of(app).audit[-1].operation == "Convert dates"


def test_excluding_duplicates_requires_the_review_tick():
    app = choose(open_page("data_cleaning.py"), dc.OP_DUPLICATES)
    assert app.button(key="apply_operation").disabled  # not confirmed yet
    app.checkbox(key="clean_opt_confirm").check().run()
    assert not app.button(key="apply_operation").disabled
    click(app, "apply_operation")
    assert metrics(app)["Excluded rows"] == "20"
    assert metrics(app)["Working-copy rows"] == "7,990"


def test_preview_warns_before_removing_rows_and_keeps_one_main_button():
    app = open_page("data_cleaning.py")
    # No tool open and safe fixes found: applying them is the main action, saving is secondary.
    assert app.button(key="apply_safe_fixes").proto.type == "primary"
    assert app.button(key="approve").proto.type == "secondary"
    choose(app, dc.OP_DUPLICATES)
    assert any("removes 20 rows" in box.value for box in app.warning)
    assert app.button(key="approve").proto.type == "secondary"  # while previewing, Apply is the main action
    assert app.button(key="apply_operation").proto.type == "primary"
    assert session_of(app).steps_applied == 0  # previewing changes nothing


def test_reset_restores_original_and_approve_shares_cleaned_data():
    app = choose(open_page("data_cleaning.py"), dc.OP_TRIM)
    click(app, "apply_operation")
    click(app, "confirm_reset")
    assert session_of(app).steps_applied == 0
    assert metrics(app)["Working-copy rows"] == "8,010"

    choose(app, dc.OP_TRIM)
    click(app, "apply_operation")
    click(app, "approve")
    cleaned = app.session_state[session_keys.CLEANED_DATA]
    assert isinstance(cleaned, dc.CleanedData)
    assert cleaned.signature == session_of(app).signature
    assert any("Cleaned dataset ready" in box.value for box in app.success)


def test_history_tab_lists_every_step():
    app = choose(open_page("data_cleaning.py"), dc.OP_TRIM)
    click(app, "apply_operation")
    click(app, "undo_step")
    history = next(frame.value for frame in app.dataframe if "Operation" in frame.value.columns)
    assert list(history["Operation"]) == ["Undo", "Trim spaces"]  # newest first
    assert list(history["Status"]) == ["Info", "Undone"]


def test_cleaned_data_reaches_the_data_quality_page():
    app = choose(open_page("data_cleaning.py"), dc.OP_DUPLICATES)
    app.checkbox(key="clean_opt_confirm").check().run()
    click(app, "apply_operation")
    click(app, "approve")

    quality = open_page("data_quality.py", app=app)
    version = quality.segmented_control(key="quality_version")
    assert version.value == "Cleaned dataset"  # chosen by default once it exists
    assert metrics(quality)["Total rows"] == "7,990"
    assert metrics(quality)["Duplicate rows"] == "0"

    version.set_value("Original upload").run()
    assert metrics(quality)["Total rows"] == "8,010"  # the original stays available for comparison


def test_new_file_on_upload_page_discards_old_cleaning_work():
    app = choose(open_page("data_cleaning.py"), dc.OP_TRIM)
    click(app, "apply_operation")
    upload = open_page("upload.py", app=app)
    upload.button(key="remove_file").click().run()
    assert session_keys.CLEANING_SESSION not in upload.session_state
    assert session_keys.CLEANED_DATA not in upload.session_state


def test_session_is_tied_to_the_loaded_file():
    app = open_page("data_cleaning.py")
    name, content = read_sample("sales_messy")
    assert session_of(app).signature == dataset_signature(name, None, content)


def test_suggested_tool_opens_from_its_row():
    app = open_page("data_cleaning.py")
    click(app, f"open_{dc.OP_MISSING}")   # a "needs your decision" row (trimming is a safe fix now)
    assert app.selectbox(key="cleaning_operation").value == dc.OP_MISSING
    assert app.button(key=f"open_{dc.OP_MISSING}").disabled  # already open
    assert session_of(app).steps_applied == 0  # opening a tool changes nothing


def test_apply_all_safe_fixes_is_one_undoable_step_that_never_removes_rows():
    app = open_page("data_cleaning.py")
    assert any("Recommended fixes" in block.value for block in app.markdown)
    before = session_of(app).working.copy()
    click(app, "apply_safe_fixes")
    session = session_of(app)
    assert session.steps_applied == 1                       # every safe fix in one step
    assert session.audit[-1].operation == "Apply safe fixes"
    assert len(session.working) == len(before) == 8010      # nothing excluded, nothing deleted
    assert not session.excluded
    assert session.working["total_sales"].dtype.kind == "f"  # numbers are real numbers now
    # Risky problems are left for the user: duplicates and blanks are still there.
    assert len(dc.find_duplicate_rows(session.working)) == 20
    assert app.button(key=f"open_{dc.OP_DUPLICATES}") is not None
    assert app.button(key=f"open_{dc.OP_MISSING}") is not None
    assert app.button(key="approve").proto.type == "primary"   # no safe fixes left: saving is the main action
    click(app, "undo_step")
    pd.testing.assert_frame_equal(session_of(app).working, before)


def test_review_item_opens_its_tool():
    app = open_page("data_cleaning.py")
    click(app, f"open_{dc.OP_DUPLICATES}")
    assert app.selectbox(key="cleaning_operation").value == dc.OP_DUPLICATES
    assert app.button(key="apply_operation").disabled          # excluding rows still needs the review tick


def test_safe_fixes_reach_the_dashboard_even_after_it_showed_the_original():
    app = open_page("data_cleaning.py")
    dashboard = open_page("dashboard.py", app=app)
    assert dashboard.segmented_control(key="dash_version").value == "Original upload"
    original_categories = len(dashboard.multiselect(key="dash_category").options)
    click(app, "apply_safe_fixes")
    app.session_state[session_keys.ANALYSIS_SHARED] = dashboard.session_state[session_keys.ANALYSIS_SHARED]
    dashboard = open_page("dashboard.py", app=app)
    assert dashboard.segmented_control(key="dash_version").value == "Working copy (not approved)"
    # Names written in different ways are now one item each in filters and breakdowns.
    assert len(dashboard.multiselect(key="dash_category").options) < original_categories
