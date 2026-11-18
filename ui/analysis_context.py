"""Shared data context for the Analysis pages.

Every Analysis page (the Dashboard now; Comparisons, Performance and Insights in
Stage 6) must analyse exactly the same data in the same way. This module decides:

- which version of the main file is used (Cleaned > Working copy > Original),
- the optional second file (for example expenses next to sales), which is never
  merged row by row with the main file,
- which dataset is the sales data and which is the expense data,

and draws the shared controls: the version selector, the second-file panel, the
counting options and the filter bar. All calculations stay in
tools/business_metrics.py; this module only chooses and prepares the inputs.

Moved here unchanged from views/dashboard.py in Stage 6.1 (same widget keys,
labels and order), so the Dashboard behaves exactly as before.

Shared choices (Stage 6.2). Each page keeps its own widget keys (prefix "dash"
for the Dashboard, "cmp" for Comparisons): Streamlit gives widgets on different
pages different identities and forgets a page's widgets when you leave it. So
the last choices are also remembered in session_keys.ANALYSIS_SHARED, and a
widget that is being created afresh (for example after arriving from another
page) starts from the remembered choice, if it is still valid. While a page's
widgets exist, they are used exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from config import session_keys
from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools.column_mapping import recognize_columns
from tools.file_loader import FileLoadError, LoadedData, dataset_signature, list_sheets, load_file
from ui import components as ui
from ui.state import reset_dashboard_filters as reset_filters, set_companion_sample

COMPANION_UPLOADER_VERSION = "companion_uploader_version"
VERSION_CLEANED = "Cleaned dataset"
VERSION_WORKING = "Working copy (not approved)"
VERSION_ORIGINAL = "Original upload"


# ---------------------------------------------------------------------------
# Cached preparation (the frame itself is not hashed; the key identifies it)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=8)
def prepared(cache_key: str, _frame: pd.DataFrame, name: str, version: str, overrides: tuple,
             include_errors: bool) -> bm.PreparedDataset:
    mapping = recognize_columns([str(column) for column in _frame.columns], dict(overrides))
    return bm.prepare_dataset(_frame, name, version, mapping, include_errors, QualityRules())


@st.cache_data(show_spinner=False, max_entries=4)
def companion_sheets(content: bytes) -> list[str]:
    return list_sheets(content)


# ---------------------------------------------------------------------------
# The loaded file and its versions
# ---------------------------------------------------------------------------
def loaded_file() -> tuple[LoadedData | None, str | None]:
    """The main loaded file and its signature, or (None, None) when nothing is loaded."""
    loaded = st.session_state.get(session_keys.LOADED_DATA)
    if loaded is None:
        return None, None
    active = st.session_state.get(session_keys.ACTIVE_FILE, {})
    return loaded, dataset_signature(loaded.file_name, loaded.sheet_name, active.get("content", b""))


def main_versions(loaded, signature: str) -> dict[str, pd.DataFrame]:
    """Versions of the main file, in order of preference: cleaned > working copy > original."""
    versions: dict[str, pd.DataFrame] = {}
    cleaned = st.session_state.get(session_keys.CLEANED_DATA)
    if cleaned is not None and cleaned.signature == signature:
        versions[VERSION_CLEANED] = cleaned.data
    cleaning = st.session_state.get(session_keys.CLEANING_SESSION)
    if cleaning is not None and cleaning.signature == signature and cleaning.steps_applied \
            and cleaning.has_unapproved_changes:
        versions[VERSION_WORKING] = cleaning.working
    versions[VERSION_ORIGINAL] = loaded.data
    return versions


# ---------------------------------------------------------------------------
# The second file
# ---------------------------------------------------------------------------
def remove_companion() -> None:
    st.session_state.pop(session_keys.COMPANION_FILE, None)
    st.session_state[COMPANION_UPLOADER_VERSION] = st.session_state.get(COMPANION_UPLOADER_VERSION, 0) + 1
    reset_filters()


def load_companion() -> tuple[pd.DataFrame | None, str | None, str | None, str | None]:
    """The second file, if any: (frame, name, signature, error message)."""
    companion = st.session_state.get(session_keys.COMPANION_FILE)
    if companion is None:
        return None, None, None, None
    try:
        sheet = companion.get("sheet")
        if companion["name"].lower().endswith(".xlsx"):
            sheets = companion_sheets(companion["content"])
            sheet = sheet if sheet in sheets else sheets[0]
        loaded = load_file(companion["name"], companion["content"], sheet)
    except FileLoadError as error:
        return None, companion["name"], None, f"{error.message} {error.hint}"
    signature = dataset_signature(companion["name"], loaded.sheet_name, companion["content"])
    return loaded.data, companion["name"], signature, None


def render_companion_controls(main_kind: str, companion_name: str | None, companion_error: str | None) -> None:
    other = "expense" if main_kind != "expenses" else "sales"
    with st.expander(f"Add a separate {other} file" if companion_name is None else
                     f"Second file: {companion_name}", icon=":material/add_chart:",
                     expanded=companion_error is not None):
        st.caption(f"Sales and expenses are often kept in different files. Add the {other} file here to see "
                   "expenses and profit. The two files are kept separate and only compared as totals for the "
                   "dates both cover, so nothing is counted twice. The second file is used as uploaded "
                   "(it does not go through the cleaning steps); rows with confirmed errors are still left out.")
        if companion_error:
            st.error(companion_error, icon=":material/error:")
        if companion_name is not None:
            st.button("Remove second file", icon=":material/delete:", key="remove_companion", on_click=remove_companion)
            return
        uploaded = st.file_uploader(f"Upload a {other} file (.csv or .xlsx)",
                                    key=f"companion_{st.session_state.get(COMPANION_UPLOADER_VERSION, 0)}")
        if uploaded is not None:
            st.session_state[session_keys.COMPANION_FILE] = {"name": uploaded.name, "content": uploaded.getvalue(),
                                                             "source": "upload", "sheet": None}
            reset_filters()
            st.rerun()
        sample = "expenses_clean" if other == "expense" else "sales_clean"
        st.button(f"Use the sample {other} file", key="companion_sample", icon=":material/dataset:",
                  on_click=set_companion_sample, args=(sample,))


# ---------------------------------------------------------------------------
# Choosing the data to analyse
# ---------------------------------------------------------------------------
@dataclass
class AnalysisData:
    """The datasets an Analysis page works on, after the version and second file are resolved."""
    file_name: str
    version: str
    main: bm.PreparedDataset
    sales: bm.PreparedDataset | None
    expenses: bm.PreparedDataset | None
    companion_name: str | None
    companion_error: str | None

    @property
    def has_amounts(self) -> bool:
        return self.sales is not None or self.expenses is not None

    @property
    def currency(self) -> str | None:
        return (self.sales.currency if self.sales else None) or (self.expenses.currency if self.expenses else None)

    @property
    def currency_text(self) -> str:
        sales, expenses = self.sales, self.expenses
        if sales and expenses and sales.currency and expenses.currency and sales.currency != expenses.currency:
            return f"Mixed: {sales.currency} and {expenses.currency}"
        return self.currency if self.currency else "Not specified"


# ---------------------------------------------------------------------------
# Choices shared between the Analysis pages
# ---------------------------------------------------------------------------
def remembered(widget_key: str, name: str, fallback, valid=lambda value: True):
    """The starting value for a widget: the choice remembered from another Analysis
    page when the widget is being created afresh and that choice is still valid,
    otherwise `fallback` (the page's normal default)."""
    if widget_key in st.session_state:
        return fallback
    shared = st.session_state.get(session_keys.ANALYSIS_SHARED) or {}
    if name in shared and valid(shared[name]):
        return shared[name]
    return fallback


def remember(name: str, value) -> None:
    shared = st.session_state.get(session_keys.ANALYSIS_SHARED)
    if shared is None:
        shared = {}
        st.session_state[session_keys.ANALYSIS_SHARED] = shared
    shared[name] = value


def counting_settings(prefix: str = "dash") -> tuple[bool, bool]:
    """The two counting options shared by the Analysis pages: (include pending, include error rows)."""
    is_bool = lambda value: isinstance(value, bool)  # noqa: E731
    include_pending = st.checkbox("Count pending orders as revenue", key=f"{prefix}_pending",
                                  value=remembered(f"{prefix}_pending", "pending", False, is_bool),
                                  help="By default only completed orders count as revenue.")
    include_errors = st.checkbox("Include rows with confirmed errors", key=f"{prefix}_errors",
                                 value=remembered(f"{prefix}_errors", "errors", False, is_bool),
                                 help="Not recommended: these rows have problems such as impossible dates or "
                                      "text in number columns.")
    remember("pending", include_pending)
    remember("errors", include_errors)
    return include_pending, include_errors


def select_data(loaded: LoadedData, signature: str, include_errors: bool, prefix: str = "dash") -> AnalysisData:
    """Show the version selector, prepare the chosen version and the second file, and
    decide which is sales and which is expenses. A second file of the same kind as the
    main file is not used, so nothing is counted twice."""
    overrides = st.session_state.get(session_keys.ROLE_OVERRIDES, {}).get(signature, {})
    versions = main_versions(loaded, signature)
    labels = list(versions)
    # A remembered choice only counts while the same versions exist: once cleaning
    # creates a new version (working copy or cleaned dataset), the newest is shown.
    start = remembered(f"{prefix}_version", "version", (labels[0], tuple(labels)),
                       lambda value: isinstance(value, tuple) and value[1] == tuple(labels))[0]
    version = st.segmented_control("Main file version", labels, default=start, key=f"{prefix}_version",
                                   help="The cleaned dataset is used when you have approved one.") or labels[0]
    remember("version", (version, tuple(labels)))
    if version == VERSION_WORKING:
        st.caption(":material/info: You are viewing cleaning work that has not been approved yet.")
    elif version == VERSION_ORIGINAL and len(labels) > 1:
        st.caption(":material/info: You are viewing the original upload, without your cleaning steps.")

    frame = versions[version]
    cache_key = f"{signature}|{version}|" + (
        str(st.session_state[session_keys.CLEANING_SESSION].version)
        if version.startswith("Working") else
        st.session_state[session_keys.CLEANED_DATA].created_at if version == VERSION_CLEANED else "")
    with st.spinner("Calculating..."):
        main = prepared(cache_key, frame, loaded.file_name, version, tuple(sorted(overrides.items())), include_errors)

    companion_frame, companion_name, companion_signature, companion_error = load_companion()
    companion = None
    if companion_frame is not None:
        if companion_signature == signature:
            companion_error = "The second file is the same as the main file, so it is not used again."
        else:
            companion = prepared(f"companion|{companion_signature}", companion_frame, companion_name,
                                 VERSION_ORIGINAL, (), include_errors)

    sales = main if main.kind == "sales" else None
    expenses = main if main.kind == "expenses" else None
    if companion is not None:
        if companion.kind == "sales" and sales is None:
            sales = companion
        elif companion.kind == "expenses" and expenses is None:
            expenses = companion
        elif companion.kind == "unknown":
            companion_error = (f"No sales or expense amount was recognised in {companion.name}, so it is not used.")
        else:
            companion_error = (f"Both files look like {companion.kind} data. Only the main file is used, so nothing "
                               "is counted twice.")
    return AnalysisData(loaded.file_name, version, main, sales, expenses, companion_name, companion_error)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def render_filters(sales: bm.PreparedDataset | None, full_range: tuple[date, date] | None,
                   start_range: tuple[date, date] | None, prefix: str = "dash") -> bm.Filters:
    options = bm.dimension_options(sales)
    with ui.filter_bar():
        return _filter_controls(options, full_range, start_range, prefix)


def _valid_dates(full_range: tuple[date, date]):
    def check(value) -> bool:
        return (isinstance(value, tuple) and len(value) == 2
                and all(isinstance(day, date) and full_range[0] <= day <= full_range[1] for day in value)
                and value[0] <= value[1])
    return check


def _filter_controls(options: dict, full_range: tuple[date, date] | None,
                     start_range: tuple[date, date] | None, prefix: str = "dash") -> bm.Filters:
    """full_range: every date in the data (the limits of the picker); start_range:
    the period shown at first, which leaves out future dates (see bm.default_range)."""
    slots = st.columns([2] + [1.4] * len(options) + [1], vertical_alignment="bottom")
    start = end = None
    if full_range:
        start_range = start_range or full_range
        key = f"{prefix}_dates"
        picked = slots[0].date_input("Date range", value=remembered(key, "dates", start_range,
                                                                    _valid_dates(full_range)),
                                     min_value=full_range[0], max_value=full_range[1], key=key, format="DD/MM/YYYY")
        if isinstance(picked, (tuple, list)):
            start = picked[0] if len(picked) > 0 else start_range[0]
            end = picked[1] if len(picked) > 1 else start_range[1]
            if len(picked) == 2:       # a half-picked range (one date clicked) is not remembered
                remember("dates", tuple(picked))
    else:
        slots[0].caption("No dates available for a date filter.")
    chosen = {}
    for slot, (role, values) in zip(slots[1:], options.items()):
        key = f"{prefix}_{role}"
        start_values = remembered(key, role, None, lambda value: isinstance(value, tuple) and len(value) > 0)
        if start_values is not None:   # keep only values that still exist in this data
            start_values = [value for value in start_values if value in values] or None
        chosen[role] = tuple(slot.multiselect(bm.DIMENSION_LABELS[role], values, default=start_values, key=key,
                                              placeholder="All"))
        remember(role, chosen[role])
    slots[-1].button("Reset filters", icon=":material/filter_alt_off:", key="reset_filters", width="stretch",
                     type="tertiary", on_click=reset_filters)
    return bm.Filters(start, end, chosen.get("product", ()), chosen.get("category", ()), chosen.get("region", ()))
