"""Upload & preview screen.

All file reading and checking happens in tools/file_loader.py; this page only
shows the results. Uploaded files are kept in memory for the current browser
session only. They are never written to disk or sent anywhere.
"""

import pandas as pd
import streamlit as st

from config import session_keys
from config.settings import MAX_UPLOAD_MB, PREVIEW_ROWS
from ui import components as ui
from tools.file_loader import (
    FileLoadError,
    LoadedData,
    describe_columns,
    describe_csv_format,
    format_file_size,
    get_file_type,
    list_sheets,
    load_file,
    make_preview,
)
from tools.sample_data import SAMPLE_DATASETS, SAMPLES_BY_KEY, read_sample

# Session-state keys (shared with later pages).
ACTIVE_FILE_KEY = session_keys.ACTIVE_FILE
LOADED_DATA_KEY = session_keys.LOADED_DATA
SHEET_KEY = session_keys.SELECTED_SHEET
UPLOADER_VERSION_KEY = session_keys.UPLOADER_VERSION


# ---------------------------------------------------------------------------
# Cached wrappers: avoid re-reading the same file on every click.
# The cache lives in this computer's memory only.
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=4)
def cached_sheet_names(content: bytes) -> list[str]:
    return list_sheets(content)


@st.cache_data(show_spinner=False, max_entries=4)
def cached_load(file_name: str, content: bytes, sheet_name: str | None) -> LoadedData:
    return load_file(file_name, content, sheet_name)


@st.cache_data(show_spinner=False, max_entries=4)
def cached_describe(file_name: str, content: bytes, sheet_name: str | None) -> pd.DataFrame:
    return describe_columns(cached_load(file_name, content, sheet_name).data)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_sample(key: str) -> tuple[str, bytes]:
    return read_sample(key)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def set_active_file(name: str, content: bytes, source: str) -> None:
    # A new file starts fresh: any cleaning work belonged to the previous file.
    for key in session_keys.FILE_SCOPED:
        st.session_state.pop(key, None)
    st.session_state[ACTIVE_FILE_KEY] = {"name": name, "content": content, "source": source}


def remove_active_file() -> None:
    for key in session_keys.FILE_SCOPED:
        st.session_state.pop(key, None)
    # A new uploader key gives a fresh, empty upload box.
    st.session_state[UPLOADER_VERSION_KEY] = st.session_state.get(UPLOADER_VERSION_KEY, 0) + 1


def show_file_error(error: FileLoadError) -> None:
    st.error(f"**{error.message}**\n\n{error.hint}", icon=":material/error:")


# ---------------------------------------------------------------------------
# Screen: nothing loaded yet
# ---------------------------------------------------------------------------
def render_start_screen() -> None:
    ui.page_header("Upload data", "Load a spreadsheet. It stays on this computer and is never changed.",
                   eyebrow="Data")

    upload_column, sample_column = st.columns([3, 2], gap="large")

    with upload_column:
        with ui.card("upload_file"):
            ui.card_title("Upload your own file", "CSV or Excel. BusinessPulse reads a copy in memory.",
                          ":material/upload_file:")
            uploaded = st.file_uploader(
                "Drag and drop a file here, or click **Browse files**",
                key=f"uploader_{st.session_state.get(UPLOADER_VERSION_KEY, 0)}",
                help=f"Accepted formats: .csv and .xlsx. Maximum size: {MAX_UPLOAD_MB} MB.",
            )
            st.caption(f"Accepted: **CSV** (.csv) and **Excel** (.xlsx) · Up to {MAX_UPLOAD_MB} MB")
            if uploaded is not None:
                set_active_file(uploaded.name, uploaded.getvalue(), "upload")
                st.rerun()

    with sample_column:
        with ui.card("sample_data"):
            ui.card_title("Try sample data", "No file handy? Explore BusinessPulse with fictional example data.",
                          ":material/dataset:")
            choice = st.selectbox(
                "Sample dataset",
                options=[sample.key for sample in SAMPLE_DATASETS],
                format_func=lambda key: SAMPLES_BY_KEY[key].label,
                key="sample_choice",
            )
            st.caption(SAMPLES_BY_KEY[choice].description)
            if st.button("Load sample data", type="primary", key="load_sample", icon=":material/dataset:"):
                with st.spinner("Loading sample data..."):
                    name, content = cached_sample(choice)
                set_active_file(name, content, "sample")
                st.rerun()

    with st.expander("How should my file be laid out?", icon=":material/help:"):
        st.markdown(
            """
- **First row = column headings**, for example `Date`, `Product`, `Quantity`, `Price`.
- **One row per record**, such as one sale or one expense. Avoid totals rows and merged cells.
- **One table per sheet.** In Excel workbooks you can choose which worksheet to use.
- **Save as .csv or .xlsx.** Older `.xls` files must be re-saved as `.xlsx` first.
- Your original file is **never modified**. BusinessPulse works on a copy in memory.
"""
        )


# ---------------------------------------------------------------------------
# Screen: a file is loaded
# ---------------------------------------------------------------------------
def render_loaded_screen(active: dict) -> None:
    name, content, source = active["name"], active["content"], active["source"]

    actions = ui.page_header("Upload data", "Check that the file was read correctly before moving on.",
                             eyebrow="Data", actions=1)
    with actions:
        if st.button("Remove file", icon=":material/delete:", key="remove_file", width="stretch"):
            remove_active_file()
            st.rerun()
    source_label = "Sample data" if source == "sample" else "Your file"

    # The file card: what is loaded, the worksheet choice and the next steps.
    file_card = ui.card("up_file")
    with file_card:
        about_column, next_column = st.columns([3, 2], vertical_alignment="center", gap="large")
        with about_column:
            ui.card_title(name, source_label, ":material/description:", mono=True)
        next_area = next_column.container()

    # Excel: choose a worksheet first (an empty sheet must not block choosing another).
    sheet_name = None
    try:
        file_type = get_file_type(name)
        if file_type == "xlsx":
            sheet_names = cached_sheet_names(content)
            if len(sheet_names) > 1:
                sheet_name = about_column.selectbox(
                    f"Worksheet ({len(sheet_names)} in this workbook)", sheet_names, key=SHEET_KEY,
                    help="This workbook has more than one worksheet. Choose the one that holds your data.",
                )
            else:
                sheet_name = sheet_names[0]
        with st.spinner("Reading file..."):
            loaded = cached_load(name, content, sheet_name)
    except FileLoadError as error:
        st.session_state.pop(LOADED_DATA_KEY, None)
        show_file_error(error)
        st.caption("Choose **Remove file** to try a different file.")
        return
    except Exception as error:  # an unexpected bug, not a problem with the user's file
        st.session_state.pop(LOADED_DATA_KEY, None)
        st.error("**Something unexpected went wrong while reading this file.**\n\n"
                 "Please try another file. If this keeps happening, the technical details below can help.",
                 icon=":material/bug_report:")
        with st.expander("Technical details"):
            st.exception(error)
        return

    st.session_state[LOADED_DATA_KEY] = loaded

    with next_area:
        if next_area.button("Next: check data quality", type="primary", icon=":material/fact_check:",
                            key="go_quality", width="stretch"):
            st.switch_page("views/data_quality.py")
        if next_area.button("Go to data cleaning", icon=":material/cleaning_services:", key="go_cleaning",
                            width="stretch"):
            st.switch_page("views/data_cleaning.py")
    cleaning = st.session_state.get(session_keys.CLEANING_SESSION)
    if cleaning is not None and cleaning.steps_applied:
        with file_card:
            st.caption(f":material/cleaning_services: {cleaning.steps_applied} cleaning step(s) applied. Removing "
                       "the file or loading another one discards them.")

    with st.container(key="bp_up_facts"):
        tiles = st.columns(4)
        with tiles[0]:
            ui.kpi_card("file_size", "File size", format_file_size(loaded.file_size_bytes))
        with tiles[1]:
            ui.kpi_card("rows", "Rows", f"{loaded.row_count:,}")
        with tiles[2]:
            ui.kpi_card("columns", "Columns", f"{loaded.column_count:,}")
        with tiles[3]:
            ui.kpi_card("format", "Format", "Excel" if loaded.file_type == "xlsx" else "CSV",
                        footnote=f"Worksheet: {loaded.sheet_name}" if loaded.sheet_name else "")

    ui.notes_panel([("info", note) for note in loaded.notes], title="Reading notes")
    if loaded.renamed_columns:
        st.warning(
            f"**{len(loaded.renamed_columns)} column heading(s) were blank or repeated.** "
            "Each column needs a unique name, so BusinessPulse gave them the names below. "
            "Your original file is unchanged.",
            icon=":material/edit_note:",
        )
        st.dataframe(
            pd.DataFrame([{"Position": r.position, "Heading in file": r.original or "(blank)",
                           "Name used": r.new_name, "Reason": r.reason} for r in loaded.renamed_columns]),
            hide_index=True, width="stretch",
        )

    with ui.card("up_preview"):
        preview_tab, columns_tab = st.tabs([f"Preview (first {PREVIEW_ROWS} rows)", "Columns and data types"])
        with preview_tab:
            st.dataframe(make_preview(loaded.data), width="stretch", height=420)
            shown = min(PREVIEW_ROWS, loaded.row_count)
            details = describe_csv_format(loaded)
            st.caption(f"Showing {shown:,} of {loaded.row_count:,} rows exactly as written in the file. {details}")
        with columns_tab:
            st.dataframe(cached_describe(name, content, sheet_name), hide_index=True, width="stretch")
            st.caption(
                "**Detected type** is a first guess from the values. Below 100% **values matching type** means "
                "some values don't fit (e.g. text in a number column). The next step checks them."
            )


# ---------------------------------------------------------------------------
active_file = st.session_state.get(ACTIVE_FILE_KEY)
if active_file is None:
    render_start_screen()
else:
    render_loaded_screen(active_file)
