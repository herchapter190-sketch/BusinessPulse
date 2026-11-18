"""Stage 7 checks for anomaly detection (tools/anomalies.py) and the Anomalies screen."""

import math
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config import business_rules as br
from config import session_keys
from config.quality_rules import QualityRules
from tools import anomalies as an
from tools import business_metrics as bm
from tools import period_comparison as pc
from tools.file_loader import load_file
from tools.sample_data import read_sample

ROOT = Path(__file__).resolve().parent.parent
PAGE = str(ROOT / "views" / "anomalies.py")


def series(values):
    starts = pd.date_range("2025-01-01", periods=len(values), freq="MS")
    return [(f"M{i}", start, value) for i, (start, value) in enumerate(zip(starts, values))]


# ---------------------------------------------------------------------------
# The statistical rule
# ---------------------------------------------------------------------------
def test_robust_score_matches_the_formula():
    history = [100, 102, 98, 101, 99, 100]
    score = an.robust_score(130, history)
    median = 100.0
    mad = float(pd.Series([abs(h - median) for h in history]).median())
    assert score.median == median and score.mad == mad
    assert score.z == pytest.approx(0.6745 * 30 / mad)
    assert score.deviation_pct == pytest.approx(30.0)
    assert an.severity(score) == an.HIGH


def test_too_little_history_is_never_scored():
    score = an.robust_score(1000, [100] * (br.ANOMALY_MIN_HISTORY - 1))
    assert score.z is None and an.severity(score) is None


def test_thresholds_set_severity():
    history = [100, 110, 90, 105, 95, 100]          # median 100, MAD 5
    z = lambda v: 0.6745 * (v - 100) / 5                                  # noqa: E731
    assert an.severity(an.robust_score(120, history)) is None                    # z 2.7
    medium = an.robust_score(128, history)
    assert br.ANOMALY_Z_MEDIUM <= z(128) < br.ANOMALY_Z_HIGH and an.severity(medium) == an.MEDIUM
    assert an.severity(an.robust_score(140, history)) == an.HIGH                 # z 5.4
    assert an.severity(an.robust_score(60, history)) == an.HIGH                  # drops too


def test_small_changes_are_ignored_even_with_a_high_score():
    history = [100.0, 100.2, 99.8, 100.1, 99.9, 100.0]     # extremely steady
    score = an.robust_score(105, history)                  # z is huge, but only 5% off
    assert abs(score.z) > br.ANOMALY_Z_HIGH and an.severity(score) is None


def test_identical_history_uses_fallbacks():
    flat = [100.0] * 6
    assert an.robust_score(100, flat).z == 0
    assert math.isinf(an.robust_score(200, flat).z) and an.severity(an.robust_score(200, flat)) == an.HIGH
    nearly = [100.0] * 5 + [110.0]                         # MAD 0, mean deviation > 0
    assert an.robust_score(200, nearly).z == pytest.approx(100 / (1.2533 * 10 / 6))


def test_scan_skips_incomplete_months_and_uses_only_earlier_months():
    values = [100, 104, 96, 102, 98, 100, None, 300, 101]
    found = an.scan_series(series(values))
    assert [label for label, *_ in found] == ["M7"]          # the None month is neither checked nor a baseline
    assert found[0][3].history == 6
    assert an.scan_series(series([300, 100, 104, 96, 102, 98, 100])) == []   # first month has no baseline


def test_min_difference_blocks_tiny_moves():
    values = [5.0, 5.1, 4.9, 5.0, 5.05, 4.95, 6.5]
    assert an.scan_series(series(values))
    assert an.scan_series(series(values), min_difference=2.0) == []


# ---------------------------------------------------------------------------
# With the sample data
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sample():
    sales = bm.prepare_dataset(load_file(*read_sample("sales_clean")).data, "sales_clean.csv", "Original upload")
    expenses = bm.prepare_dataset(load_file(*read_sample("expenses_clean")).data, "expenses_clean.csv",
                                  "Original upload")
    today = QualityRules().today()
    return sales, expenses, bm.default_range(sales, expenses, today), today


@pytest.fixture(scope="module")
def report(sample):
    s, e, window, today = sample
    return an.build_anomalies(s, e, bm.Filters(*window), window, today=today)


def test_sample_data_is_checked_in_every_area(report):
    assert report.missing == {}
    assert set(report.checked) == set(an.AREAS)
    assert report.complete_months == 18 and report.months_checked == 18 - br.ANOMALY_MIN_HISTORY
    assert report.anomalies and all(a.evidence and a.reason for a in report.anomalies)
    assert all(a.severity in (an.HIGH, an.MEDIUM) for a in report.anomalies)


def test_flagged_revenue_matches_the_dashboard_figures(sample, report):
    s, e, _, today = sample
    revenue = [a for a in report.for_area("Revenue")]
    assert revenue, "the sample's November/December peak should be flagged"
    for anomaly in revenue:
        span = pc.period_of(anomaly.start, "M").span
        dashboard = bm.compute_dashboard(s, e, bm.Filters(span.start.date(), span.end.date()))
        assert anomaly.value == pytest.approx(dashboard.kpis["revenue"].value)
        assert bm.format_money(anomaly.value, dashboard.currency) in anomaly.evidence[0]


def test_dimension_findings_use_revenue_share(report):
    for anomaly in report.for_area("Products") + report.for_area("Regions"):
        assert "share of revenue" in anomaly.title
        assert 0 <= anomaly.value <= 100
        assert abs(anomaly.value - anomaly.typical) >= br.ANOMALY_MIN_SHARE_POINTS


def test_findings_are_sorted_by_severity(report):
    ranks = [0 if a.severity == an.HIGH else 1 for a in report.anomalies]
    assert ranks == sorted(ranks)


def test_short_date_range_reports_insufficient_data(sample):
    s, e, _, today = sample
    window = (pd.Timestamp("2026-03-01").date(), pd.Timestamp("2026-06-30").date())
    short = an.build_anomalies(s, e, bm.Filters(*window), window, today=today)
    assert short.anomalies == [] and short.checked == {}
    assert all("Needs at least" in short.missing[area] for area in an.AREAS)


def test_partial_months_are_skipped(sample):
    s, e, window, today = sample
    frame = load_file(*read_sample("sales_clean")).data
    cut = bm.prepare_dataset(frame[frame["order_date"] <= "2026-06-15"], "cut.csv", "Original upload")
    result = an.build_anomalies(cut, e, bm.Filters(*window), window, today=today)
    assert any(item.startswith("Jun 2026") for item in result.skipped)
    assert not any(a.period == "Jun 2026" for a in result.anomalies if a.area != "Expenses")


def test_missing_datasets_are_explained(sample):
    s, e, window, today = sample
    no_sales = an.build_anomalies(None, e, bm.Filters(*window), window, today=today)
    assert no_sales.missing["Products"] == "Needs sales data." and "Profit" in no_sales.missing
    assert all(a.area == "Expenses" for a in no_sales.anomalies)
    no_expenses = an.build_anomalies(s, None, bm.Filters(*window), window, today=today)
    assert "Expenses" in no_expenses.missing and "Profit" in no_expenses.missing
    nothing = an.build_anomalies(None, None, bm.Filters(), None)
    assert set(nothing.missing) == set(an.AREAS) and not nothing.anomalies


def test_dimension_filters_are_respected(sample):
    s, e, window, today = sample
    product = sorted(s.rows["product"].dropna().unique())[0]
    filtered = an.build_anomalies(s, e, bm.Filters(*window, products=(product,)), window, today=today)
    assert {a.subject for a in filtered.for_area("Products")} <= {product}
    assert not filtered.for_area("Products")          # one product is always 100% of the filtered revenue


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def open_page(main=None, companion=None, content=None) -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=180)
    if main or content:
        name, data = content or read_sample(main)
        app.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": data, "source": "sample"}
        app.session_state[session_keys.LOADED_DATA] = load_file(name, data)
    if companion:
        name, data = read_sample(companion)
        app.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": data, "source": "sample",
                                                          "sheet": None}
    app.run()
    assert not app.exception, app.exception
    return app


def test_page_only_uses_defined_business_rules():
    """Regression: the page must not reference a business rule that config/business_rules.py lacks."""
    import re
    used = set(re.findall(r"\bbr\.([A-Z_][A-Z0-9_]*)", Path(PAGE).read_text(encoding="utf-8")))
    assert "ANOMALY_MIN_SHARE_POINTS" in used
    missing = sorted(name for name in used if not hasattr(br, name))
    assert not missing, f"views/anomalies.py uses undefined business rules: {missing}"
    assert br.ANOMALY_MIN_SHARE_POINTS == 1.0  # documented in README: "at least 1 percentage point"


def test_page_empty_state_and_demo():
    app = open_page()
    assert any("No data loaded yet" in block.value for block in app.markdown)
    app.button(key="load_demo").click()
    app.run()
    assert not app.exception, app.exception
    assert {m.label for m in app.metric} == {"Anomalies", "High severity", "Complete months"}
    assert any("Why it was flagged" in block.value for block in app.markdown)


def test_page_explains_unchecked_areas_for_sales_only():
    app = open_page("sales_clean")
    captions = "\n".join(block.value for block in app.caption)
    assert "Not checked" in captions


def test_page_rejects_file_without_amounts():
    app = open_page(content=("things.csv", b"colour,size\nred,10\nblue,12\n"))
    assert any("couldn't find sales or expense amounts" in w.value for w in app.warning)
