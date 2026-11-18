"""Stage 5 checks for the Business Dashboard screen (views/dashboard.py), run without a browser."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config import session_keys
from tools import business_metrics as bm
from tools import data_cleaning as dc
from tools.file_loader import dataset_signature, load_file
from tools.sample_data import read_sample

PAGE = str(Path(__file__).resolve().parent.parent / "views" / "dashboard.py")
TIMEOUT = 180


def open_dashboard(main: str | None = None, companion: str | None = None, cleaned_from_session=None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=TIMEOUT)
    if main:
        name, content = read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
    if companion:
        name, content = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": content, "source": "sample",
                                                          "sheet": None}
    if cleaned_from_session is not None:
        app.session_state[session_keys.CLEANING_SESSION] = cleaned_from_session
        app.session_state[session_keys.CLEANED_DATA] = cleaned_from_session.approve()
    app.run()
    assert not app.exception, app.exception
    return app


def kpis(app: AppTest) -> dict[str, str]:
    return {metric.label: metric.value for metric in app.metric}


def charts_shown(app: AppTest) -> int:
    return len(app.get("plotly_chart"))


def test_empty_state_offers_upload_and_demo():
    app = open_dashboard()
    assert any("No data loaded yet" in block.value for block in app.markdown)  # the empty-state card
    app.button(key="load_demo").click().run()
    assert not app.exception, app.exception
    values = kpis(app)
    assert values["Total revenue"] == "461,120.95"
    assert values["Net profit"] == "46,010.52"
    assert values["Profit margin"] == "10.0%"
    assert charts_shown(app) == 4  # trend + profit + revenue by category + expenses by category (Stage 10 overview)


def test_sales_only_dashboard_explains_missing_expenses():
    app = open_dashboard("sales_clean")
    values = kpis(app)
    assert values["Total revenue"] == "461,120.95"
    assert values["Total expenses"] == "—" and values["Profit"] == "—"
    assert any("Add an expense file" in caption.value for caption in app.caption)
    assert charts_shown(app) == 2  # trend + revenue by category; products and regions are on Performance


def test_missing_expenses_action_sits_under_financial_results_and_works():
    app = open_dashboard("sales_clean")
    labels = [expander.label for expander in app.expander]
    action = labels.index("Add a separate expense file")   # shown once, before the calculation details
    assert labels.count("Add a separate expense file") == 1
    assert action < labels.index("How are these figures calculated?")
    app.button(key="companion_sample").click().run()
    assert not app.exception, app.exception
    assert kpis(app)["Net profit"] == "46,010.52"
    assert "Second file: expenses_clean.csv" in [expander.label for expander in app.expander]


def test_sales_and_separate_expenses():
    app = open_dashboard("sales_clean", "expenses_clean")
    values = kpis(app)
    assert values["Total expenses"] == "415,110.43"
    assert values["Net profit"] == "46,010.52"
    assert charts_shown(app) == 4  # trend + profit + revenue by category + expenses by category (Stage 10 overview)


def test_expense_file_as_main_and_sales_as_second():
    app = open_dashboard("expenses_clean", "sales_clean")
    assert kpis(app)["Net profit"] == "46,010.52"


def test_two_sales_files_are_not_double_counted():
    app = open_dashboard("sales_clean", "sales_messy")
    assert kpis(app)["Total revenue"] == "461,120.95"
    assert any("Only the main file is used" in error.value for error in app.error)


def test_filters_update_kpis_and_reset_restores_them():
    app = open_dashboard("sales_clean", "expenses_clean")
    app.multiselect(key="dash_region").set_value(["North"]).run()
    assert not app.exception, app.exception
    filtered = kpis(app)
    assert filtered["Total revenue"] != "461,120.95"
    assert filtered["Profit"] == "—"  # expenses are not split by region

    app.date_input(key="dash_dates").set_value((date(2025, 12, 1), date(2025, 12, 31))).run()
    assert not app.exception, app.exception
    december_north = kpis(app)["Total revenue"]
    assert december_north != filtered["Total revenue"]

    app.button(key="reset_filters").click().run()
    assert not app.exception, app.exception
    assert kpis(app)["Total revenue"] == "461,120.95"
    assert kpis(app)["Net profit"] == "46,010.52"


def test_filter_with_no_matches_shows_an_empty_state():
    app = open_dashboard("sales_clean")
    app.multiselect(key="dash_product").set_value(["Yoga Mat"]).run()
    app.multiselect(key="dash_category").set_value(["Electronics"]).run()
    assert not app.exception, app.exception
    assert any("No sales match the selected filters" in box.value for box in app.info)
    assert kpis(app)["Total revenue"] == "0.00"


def test_messy_original_warns_about_excluded_rows():
    app = open_dashboard("sales_messy")
    joined = " ".join(warning.value for warning in app.warning)
    assert "45 row(s) with confirmed errors" in joined
    assert "ambiguous" in joined


def test_cleaned_dataset_is_used_by_default_and_original_is_available():
    loaded = load_file(*read_sample("sales_messy"))
    name, content = read_sample("sales_messy")
    session = dc.CleaningSession(loaded.data, dataset_signature(name, None, content), name)
    session.apply(dc.propose_exclude_duplicates(session), confirmed=True)
    app = open_dashboard("sales_messy", cleaned_from_session=session)

    version = app.segmented_control(key="dash_version")
    assert version.value == "Cleaned dataset"
    assert any("Cleaned dataset" in markdown.value for markdown in app.markdown)
    cleaned_revenue = kpis(app)["Total revenue"]

    version.set_value("Original upload").run()
    assert not app.exception, app.exception
    assert any("original upload" in caption.value for caption in app.caption)
    # Duplicates are confirmed errors in the original, so they are left out there too: same revenue.
    assert kpis(app)["Total revenue"] == cleaned_revenue


def test_unrecognised_file_gets_a_clear_message():
    app = AppTest.from_file(PAGE, default_timeout=TIMEOUT)
    content = b"colour,size\nred,10\nblue,12\n"
    app.session_state[session_keys.ACTIVE_FILE] = {"name": "things.csv", "content": content, "source": "upload"}
    app.session_state[session_keys.LOADED_DATA] = load_file("things.csv", content)
    app.run()
    assert not app.exception, app.exception
    assert any("couldn't find sales or expense amounts" in warning.value for warning in app.warning)
    assert not app.metric


def test_future_dated_orders_are_outside_the_default_range_but_can_be_included():
    """The messy sample has 4 orders mistyped into 2027. They must not stretch the
    default period, and the user must still be able to include them."""
    messy = bm.prepare_dataset(load_file(*read_sample("sales_messy")).data, "sales_messy.csv", "Original upload")
    count, latest = bm.future_dated(messy, None)  # against the real today, as the page does
    if not count:
        pytest.skip("The sample's future dates are no longer in the future.")
    app = open_dashboard("sales_messy")
    picker = app.date_input(key="dash_dates")
    start, end = picker.value
    assert end < latest and end <= date.today()
    assert any(f"{count} row(s) are dated after today" in w.value and "not deleted" in w.value for w in app.warning)
    default_revenue = kpis(app)["Total revenue"]

    picker.set_value((start, latest)).run()  # the picker still allows the future dates
    assert not app.exception, app.exception
    assert kpis(app)["Total revenue"] != default_revenue
    assert any("included in the selected period" in w.value for w in app.warning)


@pytest.mark.parametrize("setting", ["dash_pending"])
def test_settings_change_the_figures(setting):
    app = open_dashboard("sales_clean")
    before = kpis(app)["Total revenue"]
    app.checkbox(key=setting).check().run()
    assert not app.exception, app.exception
    assert kpis(app)["Total revenue"] != before  # pending orders now counted
