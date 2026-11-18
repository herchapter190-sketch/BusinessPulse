"""Stage 6.2 checks for the Comparisons screen (views/comparisons.py), run without a browser."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config import session_keys
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import period_comparison as pc
from tools import data_cleaning as dc
from tools.file_loader import dataset_signature, load_file
from tools.sample_data import read_sample

ROOT = Path(__file__).resolve().parent.parent
PAGE = str(ROOT / "views" / "comparisons.py")
TIMEOUT = 180


def open_page(main: str | None = None, companion: str | None = None, content: tuple[str, bytes] | None = None,
              session=None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=TIMEOUT)
    if main or content:
        name, data = content or read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": data, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, data)
    if companion:
        name, data = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": data, "source": "sample",
                                                          "sheet": None}
    if session is not None:
        app.session_state[session_keys.CLEANING_SESSION] = session
        app.session_state[session_keys.CLEANED_DATA] = session.approve()
    app.run()
    assert not app.exception, app.exception
    return app


def cards(app: AppTest) -> dict[str, tuple[str, str]]:
    """KPI label -> (value, change text)."""
    return {metric.label: (metric.value, metric.proto.delta) for metric in app.metric}


def text(app: AppTest) -> str:
    parts = [block.value for group in (app.markdown, app.caption, app.info, app.warning) for block in group]
    return "\n".join(parts)


def rerun(app: AppTest) -> AppTest:
    app.run()
    assert not app.exception, app.exception
    return app


@pytest.fixture(scope="module")
def sample():
    sales = bm.prepare_dataset(load_file(*read_sample("sales_clean")).data, "sales_clean.csv", "Original upload")
    expenses = bm.prepare_dataset(load_file(*read_sample("expenses_clean")).data, "expenses_clean.csv",
                                  "Original upload")
    return sales, expenses


# ---------------------------------------------------------------------------
# Empty states and unusable files
# ---------------------------------------------------------------------------
def test_empty_state_offers_upload_and_demo():
    app = open_page()
    assert any("No data loaded yet" in block.value for block in app.markdown)
    app.button(key="load_demo").click()
    rerun(app)
    assert cards(app)["Total revenue"][0] == "22,561.65"     # Jun 2026, the latest complete month


def test_file_without_amounts_gets_a_clear_message():
    app = open_page(content=("things.csv", b"colour,size\nred,10\nblue,12\n"))
    assert any("couldn't find sales or expense amounts" in w.value and "Comparisons need" in w.value
               for w in app.warning)
    assert not app.metric


def test_file_without_dates_explains_that_comparisons_need_dates():
    app = open_page(content=("no_dates.csv", b"product_name,total_sales\nMug,10\nLamp,20\n"))
    assert "Comparisons need dates" in text(app)
    assert not app.metric


# ---------------------------------------------------------------------------
# Month, quarter, year; previous period and last year
# ---------------------------------------------------------------------------
def test_default_month_comparison_with_demo_data():
    app = open_page("sales_clean", "expenses_clean")
    values = cards(app)
    assert values["Total revenue"] == ("22,561.65", "−29.1% (−9,259.34)")
    assert values["Profit margin"][1] == "−26.0 pp"               # percentage points, not %
    assert values["Return rate"][1].endswith(" pp")
    assert "May 2026: 31,820.99" in text(app)
    # The pending orders that make June look lower are explained, not hidden.
    assert any("54 pending order(s)" in w.value for w in app.warning)
    assert len(app.get("plotly_chart")) == 2  # money bars + every-period chart


def test_headline_reads_the_lead_change_in_words():
    app = open_page("sales_clean", "expenses_clean")
    values = cards(app)
    label = app.metric[0].label
    assert "profit" in label.lower()                                # the headline figure comes first
    profit_change = values[label][1]
    sentence = next(block.value for block in app.markdown if "from May 2026 to Jun 2026" in block.value)
    direction = "fell" if profit_change.startswith("−") else "rose"
    unsigned = profit_change.replace("+", "").replace("−", "")
    assert f"**{label} {direction} by {unsigned}**" in sentence      # same figures, said in words
    assert ":material/arrow_" in sentence                           # direction never shown by colour alone

    sales_only = open_page("sales_clean")                           # no profit: revenue is the headline
    assert sales_only.metric[0].label == "Total revenue"
    assert any("**Total revenue fell by 29.1% (9,259.34)**" in block.value for block in sales_only.markdown)


@pytest.mark.parametrize("mode, expected", [(pc.MODE_PREVIOUS, "+17.4% (+12,516.24)"),
                                            (pc.MODE_LAST_YEAR, "+14.0% (+10,338.24)")])
def test_quarter_comparisons(mode, expected):
    app = open_page("sales_clean", "expenses_clean")
    app.segmented_control(key="cmp_frequency").set_value("Q")
    app.segmented_control(key="cmp_mode").set_value(mode)
    rerun(app)
    assert cards(app)["Total revenue"] == ("84,343.89", expected)
    assert "Growth gap" in cards(app)


def test_year_comparison_explains_missing_history_and_offers_to_date():
    app = open_page("sales_clean", "expenses_clean")
    app.segmented_control(key="cmp_frequency").set_value("Y")
    rerun(app)
    assert app.selectbox(key="cmp_period_Y").value == "2025"      # the only complete year
    assert cards(app)["Total revenue"][1] == ""                    # no change: no 2024 in the data
    assert "There is no data for 2024" in text(app) and "two years of data" in text(app)

    app.selectbox(key="cmp_period_Y").set_value("2026")
    rerun(app)
    assert "2026 is not complete" in text(app)
    assert cards(app)["Total revenue"][1] == ""                    # not finished: no change
    assert not app.toggle(key="cmp_to_date").disabled
    app.toggle(key="cmp_to_date").set_value(True)
    rerun(app)
    assert "1 Jan – 30 Jun 2026" in text(app) and "1 Jan – 30 Jun 2025" in text(app)
    assert cards(app)["Total revenue"][1].startswith("+")          # first half of 2026 vs 2025


def test_to_date_matches_the_engine(sample):
    sales, expenses = sample
    expected = pc.compare_periods(sales, expenses, bm.Filters(), pc.period_of(pd.Timestamp("2026-01-01"), "Y"),
                                  pc.MODE_PREVIOUS, to_date=True, today=QualityRules().today())
    app = open_page("sales_clean", "expenses_clean")
    app.segmented_control(key="cmp_frequency").set_value("Y")
    rerun(app)
    app.selectbox(key="cmp_period_Y").set_value("2026")
    rerun(app)
    app.toggle(key="cmp_to_date").set_value(True)
    rerun(app)
    assert cards(app)["Total revenue"][1] == pc.change_text(expected.changes["revenue"], None)


def test_to_date_is_disabled_for_complete_periods():
    app = open_page("sales_clean")
    assert app.toggle(key="cmp_to_date").disabled


def test_month_without_previous_data():
    app = open_page("sales_clean")
    app.selectbox(key="cmp_period_M").set_value("Jan 2025")
    rerun(app)
    assert "There is no data for Dec 2024" in text(app)
    revenue = cards(app)["Total revenue"]
    assert revenue[0] != "—" and revenue[1] == ""         # January's own figure, but no change


# ---------------------------------------------------------------------------
# Missing data, files and settings
# ---------------------------------------------------------------------------
def test_sales_only_explains_what_needs_expenses():
    app = open_page("sales_clean")
    values = cards(app)
    assert values["Total expenses"][0] == "—" and values["Profit"][0] == "—"
    assert "Growth gap" not in values
    assert "Needs expense data" in text(app)


def test_expense_file_only_hides_sales_figures():
    app = open_page("expenses_clean")
    values = cards(app)
    assert values["Total expenses"][0] != "—" and values["Total expenses"][1]
    assert "Orders" not in values and "Return rate" not in values


def test_dimension_filter_hides_profit_and_growth_gap():
    app = open_page("sales_clean", "expenses_clean")
    app.multiselect(key="cmp_region").set_value(["North"])
    rerun(app)
    values = cards(app)
    assert values["Total revenue"][1]                       # revenue still compared, for North only
    assert values["Profit"][0] == "—" and "Net profit" not in values
    assert "not recorded per product, category or region" in text(app)
    # Expenses are still shown, but clearly as the total for the dates, not North's expenses.
    assert any("Expenses are not recorded per product, category or region, so this is the total for the "
               "selected dates only." in caption.value for caption in app.caption)


def test_filters_with_no_matching_sales():
    app = open_page("sales_clean")
    app.multiselect(key="cmp_product").set_value(["Yoga Mat"])
    app.multiselect(key="cmp_category").set_value(["Electronics"])
    rerun(app)
    assert any("No sales match the selected filters" in box.value for box in app.info)


def test_pending_setting_changes_the_figures():
    app = open_page("sales_clean")
    before = cards(app)["Total revenue"][0]
    app.checkbox(key="cmp_pending").check()
    rerun(app)
    assert cards(app)["Total revenue"][0] != before
    assert not any("pending order(s)" in w.value for w in app.warning)


def test_chart_metric_can_be_changed():
    app = open_page("sales_clean", "expenses_clean")
    for metric in ("profit", "margin", "orders"):
        app.selectbox(key="cmp_chart_metric").set_value(metric)
        rerun(app)
        assert len(app.get("plotly_chart")) == 2  # money bars + every-period chart


def test_messy_data_marks_incomplete_periods():
    app = open_page("sales_messy")
    assert app.selectbox(key="cmp_period_M").value == "Jun 2026"
    assert any("confirmed errors" in w.value for w in app.warning)
    full = app.date_input(key="cmp_dates")
    full.set_value((date(2025, 1, 1), date(2027, 12, 3)))      # include the mistyped 2027 dates
    rerun(app)
    options = app.selectbox(key="cmp_period_M").options
    assert any("no records" in option for option in options)
    assert any("possible missing data" in option or "not finished" in option for option in options)
    flagged = next(option for option in options if "no records" in option)
    app.selectbox(key="cmp_period_M").set_value(flagged.split(" · ")[0])
    rerun(app)
    assert "is not complete" in text(app)
    assert cards(app)["Total revenue"] == ("—", "")             # no records: unknown, never 0
    assert cards(app)["Orders"] == ("—", "")
    month_label = flagged.split(" · ")[0]
    assert any(caption.value == f":material/info: {month_label}: no records" for caption in app.caption)


def test_cleaned_version_is_used_by_default():
    name, content = read_sample("sales_messy")
    session = dc.CleaningSession(load_file(name, content).data, dataset_signature(name, None, content), name)
    session.apply(dc.propose_exclude_duplicates(session), confirmed=True)
    app = open_page("sales_messy", session=session)
    assert app.segmented_control(key="cmp_version").value == "Cleaned dataset"


# ---------------------------------------------------------------------------
# Choices shared with the Dashboard
# ---------------------------------------------------------------------------
def test_choices_carry_over_between_dashboard_and_comparisons():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    for key, target in (("sales_clean", "main"), ("expenses_clean", "companion")):
        name, content = read_sample(key)
        if target == "main":
            app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
            app.session_state[session_keys.LOADED_DATA] = load_file(name, content)
        else:
            app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": content,
                                                              "source": "sample", "sheet": None}
    app.switch_page("views/dashboard.py")
    rerun(app)
    app.multiselect(key="dash_region").set_value(["North"])
    app.date_input(key="dash_dates").set_value((date(2025, 6, 1), date(2026, 6, 30)))
    app.checkbox(key="dash_pending").check()
    rerun(app)

    app.switch_page("views/comparisons.py")
    rerun(app)
    assert app.multiselect(key="cmp_region").value == ["North"]
    assert app.date_input(key="cmp_dates").value == (date(2025, 6, 1), date(2026, 6, 30))
    assert app.checkbox(key="cmp_pending").value is True

    app.button(key="reset_filters").click()
    rerun(app)
    app.switch_page("views/dashboard.py")
    rerun(app)
    assert app.multiselect(key="dash_region").value == []
    assert app.checkbox(key="dash_pending").value is True       # settings are not filters: kept


def test_remembered_values_that_no_longer_exist_are_ignored():
    app = open_page("sales_clean")
    app.session_state[session_keys.ANALYSIS_SHARED] = {"region": ("Atlantis",), "dates": (date(2000, 1, 1),
                                                                                          date(2000, 2, 1)),
                                                       "version": "Cleaned dataset"}
    fresh = AppTest.from_file(PAGE, default_timeout=TIMEOUT)
    for key in (session_keys.ACTIVE_FILE, session_keys.LOADED_DATA, session_keys.ANALYSIS_SHARED):
        fresh.session_state[key] = app.session_state[key]
    rerun(fresh)
    assert fresh.multiselect(key="cmp_region").value == []
    assert fresh.date_input(key="cmp_dates").value == (date(2025, 1, 1), date(2026, 6, 30))
    assert fresh.segmented_control(key="cmp_version").value == "Original upload"
