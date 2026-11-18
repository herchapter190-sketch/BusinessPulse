"""Session helpers shared by several pages and the app shell (memory only)."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from config import session_keys
from config.quality_rules import QualityRules
from tools.column_mapping import recognize_columns
from tools.data_quality import analyze_quality
from tools.file_loader import dataset_signature, load_file
from tools.sample_data import read_sample

# Widget-key prefix of each Analysis page; filters are shared between them (ui/analysis_context.py).
ANALYSIS_PAGE_PREFIXES = ("dash", "cmp", "perf", "rep")
FILTER_NAMES = ("dates", "product", "category", "region")
DASHBOARD_FILTER_KEYS = tuple(f"{prefix}_{name}" for prefix in ANALYSIS_PAGE_PREFIXES for name in FILTER_NAMES)


@dataclass
class DatasetSummary:
    file_name: str
    sheet_name: str | None
    rows: int
    version: str             # "Cleaned", "Cleaning in progress" or "Original"
    second_file: str | None  # the dashboard's separate expense/sales file, if any
    quality: tuple[float, str, int] | None = None   # (score, grade, rows with errors) of the version in use


@st.cache_data(show_spinner=False, max_entries=6)
def _quality(cache_key: str, overrides: tuple, _frame) -> tuple[float, str, int]:
    """The data-quality score of one version of the data (worked out once per version)."""
    mapping = recognize_columns([str(column) for column in _frame.columns], dict(overrides))
    report = analyze_quality(_frame, mapping, QualityRules())
    return report.score.score, report.score.grade, report.invalid_rows


def current_dataset() -> DatasetSummary | None:
    """What is loaded right now, for the sidebar card: the one place that always
    shows the overall data-quality status of the data the analysis pages use."""
    loaded = st.session_state.get(session_keys.LOADED_DATA)
    if loaded is None:
        return None
    active = st.session_state.get(session_keys.ACTIVE_FILE, {})
    signature = dataset_signature(loaded.file_name, loaded.sheet_name, active.get("content", b""))
    cleaned = st.session_state.get(session_keys.CLEANED_DATA)
    cleaning = st.session_state.get(session_keys.CLEANING_SESSION)
    rows, frame, key = loaded.row_count, loaded.data, f"{signature}|original"
    version = "Original"
    if cleaned is not None and cleaned.signature == signature:
        version, rows, frame, key = "Cleaned", cleaned.row_count, cleaned.data, f"{signature}|{cleaned.created_at}"
    elif cleaning is not None and cleaning.signature == signature and cleaning.steps_applied:
        version, rows = "Cleaning in progress", len(cleaning.working)
        frame, key = cleaning.working, f"{signature}|working|{cleaning.version}"
    overrides = tuple(sorted(st.session_state.get(session_keys.ROLE_OVERRIDES, {}).get(signature, {}).items()))
    try:
        quality = _quality(key, overrides, frame)
    except Exception:  # noqa: BLE001 - the card must never break a page; the Data quality page explains problems
        quality = None
    companion = st.session_state.get(session_keys.COMPANION_FILE)
    return DatasetSummary(loaded.file_name, loaded.sheet_name, rows, version,
                          companion["name"] if companion else None, quality)


def reset_dashboard_filters() -> None:
    """Clear the date and product/category/region filters on every Analysis page."""
    for key in DASHBOARD_FILTER_KEYS:
        st.session_state.pop(key, None)
    shared = st.session_state.get(session_keys.ANALYSIS_SHARED)
    if shared:
        for name in FILTER_NAMES:
            shared.pop(name, None)


def set_companion_sample(key: str) -> None:
    name, content = read_sample(key)
    st.session_state[session_keys.COMPANION_FILE] = {"name": name, "content": content, "source": "sample",
                                                     "sheet": None}
    reset_dashboard_filters()


def load_demo() -> None:
    """Load the fictional sample sales file as the main dataset and the sample
    expenses as the dashboard's second file. Replaces any loaded file."""
    for key in session_keys.FILE_SCOPED:
        st.session_state.pop(key, None)
    name, content = read_sample("sales_clean")
    st.session_state[session_keys.ACTIVE_FILE] = {"name": name, "content": content, "source": "sample"}
    st.session_state[session_keys.LOADED_DATA] = load_file(name, content)
    set_companion_sample("expenses_clean")
