"""Stage 2 checks for the upload screen (views/upload.py), run without a browser.

Streamlit's test tool cannot simulate dragging a file into the upload box, so
the upload path is tested through the same functions in test_file_loader.py,
and the screen is tested here with the built-in sample data.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_PAGE = str(PROJECT_ROOT / "views" / "upload.py")
TIMEOUT = 60


def start_page() -> AppTest:
    app = AppTest.from_file(UPLOAD_PAGE, default_timeout=TIMEOUT)
    app.run()
    assert not app.exception, app.exception
    return app


def metric_values(app: AppTest) -> dict[str, str]:
    return {metric.label: metric.value for metric in app.metric}


def load_sample(app: AppTest, key: str) -> AppTest:
    app.selectbox(key="sample_choice").select(key).run()
    app.button(key="load_sample").click().run()
    assert not app.exception, app.exception
    return app


def test_start_screen_shows_upload_box_and_sample_option():
    app = start_page()
    assert app.title[0].value == "Upload data"
    assert app.button(key="load_sample") is not None
    assert len(app.selectbox(key="sample_choice").options) == 4
    assert not app.error


def test_loading_sample_csv_shows_details_and_preview():
    app = load_sample(start_page(), "sales_clean")
    metrics = metric_values(app)
    assert metrics["Rows"] == "7,990"
    assert metrics["Columns"] == "11"
    assert metrics["Format"] == "CSV"
    preview, column_types = app.dataframe[0].value, app.dataframe[1].value
    assert len(preview) == 100
    assert list(column_types["Column"])[:2] == ["order_id", "order_date"]
    assert "loaded_data" in app.session_state


def test_sample_workbook_allows_choosing_a_worksheet():
    app = load_sample(start_page(), "sample_workbook")
    sheet_box = app.selectbox(key="selected_sheet")
    assert sheet_box.options == ["Sales", "Expenses", "About"]
    assert metric_values(app)["Rows"] == "7,990"

    sheet_box.select("Expenses").run()
    assert not app.exception, app.exception
    assert metric_values(app)["Rows"] == "499"
    assert metric_values(app)["Columns"] == "7"

    app.selectbox(key="selected_sheet").select("About").run()
    assert metric_values(app)["Rows"] == "4"


def test_remove_file_returns_to_start_screen():
    app = load_sample(start_page(), "expenses_clean")
    assert app.metric
    app.button(key="remove_file").click().run()
    assert not app.exception, app.exception
    assert not app.metric
    assert "active_file" not in app.session_state
    assert "loaded_data" not in app.session_state
    assert app.button(key="load_sample") is not None


def test_problem_file_shows_friendly_error_not_a_crash():
    app = start_page()
    app.session_state["active_file"] = {"name": "broken.xlsx", "content": b"not a workbook", "source": "upload"}
    app.run()
    assert not app.exception
    assert "can't be opened as an Excel workbook" in app.error[0].value
    assert app.button(key="remove_file") is not None  # user can recover
