"""Stage 0 checks: dependencies are installed and the app starts without errors."""

import sys
from importlib.metadata import version
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_VERSIONS = {
    "streamlit": "1.67.0",
    "pandas": "3.1.1",
    "plotly": "7.1.0",
    "openpyxl": "3.1.5",
    "reportlab": "5.1.0",
    "pytest": "9.2.0",
}


def test_running_inside_project_virtual_environment():
    assert sys.prefix != sys.base_prefix, "Tests must run inside .venv"
    assert Path(sys.prefix).resolve() == (PROJECT_ROOT / ".venv").resolve()


def test_dependency_versions_match_requirements():
    for package, expected in EXPECTED_VERSIONS.items():
        assert version(package) == expected, f"{package}: {version(package)} != {expected}"


def test_core_libraries_import():
    import openpyxl  # noqa: F401
    import pandas  # noqa: F401
    import plotly.express  # noqa: F401
    import reportlab.pdfgen.canvas  # noqa: F401


def test_no_special_streamlit_pages_folder():
    """A folder called "pages" next to app.py makes Streamlit list its files on
    its own; on a fresh start a direct address such as /dashboard then opened
    without the app frame. Screens live in views/ instead (Stage 9.5)."""
    assert not (PROJECT_ROOT / "pages").exists()
    assert (PROJECT_ROOT / "views" / "home.py").is_file()


def test_page_addresses_unchanged():
    """Page URLs come from the file names, so they must not change with the folder."""
    names = {path.stem for path in (PROJECT_ROOT / "views").glob("*.py")}
    assert names == {"home", "upload", "data_quality", "data_cleaning", "dashboard",
                     "comparisons", "performance", "anomalies", "insights", "reports"}
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    for name in names:
        assert f'st.Page("views/{name}.py"' in app_source


def test_app_starts_and_shows_welcome_screen():
    app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=30)
    assert not app.exception, f"App raised: {app.exception}"
    assert app.title[0].value == "See how your business is performing"
    labels = [button.label for button in app.button]
    assert "Upload data" in labels and "Explore the demo" in labels
    # Exactly one filled primary button per view.
    assert sum(button.proto.type == "primary" for button in app.button) == 1


def test_home_shows_loaded_dataset_and_resume_actions():
    app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=30)
    app.button(key="home_demo").click().run(timeout=30)       # loads the demo, opens the dashboard
    app.switch_page("views/home.py").run(timeout=30)
    assert not app.exception, f"App raised: {app.exception}"
    text = " ".join(block.value for block in app.markdown)
    assert "Current dataset" in text and "sales_clean.csv" in text
    primary = [button.label for button in app.button if button.proto.type == "primary"]
    assert primary == ["Open the dashboard"]
