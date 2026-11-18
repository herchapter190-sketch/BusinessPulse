"""BusinessPulse — application entry point.

Launch from the project folder with:
    .\\.venv\\Scripts\\python.exe -m streamlit run app.py

This file only sets up the app shell: page settings, logo, design-system styles,
navigation and the current-dataset card in the sidebar. Each screen lives in
views/, shared interface pieces in ui/, and all data processing in tools/.

The screens folder is deliberately not called "pages/": Streamlit gives that
name a special meaning and, on a fresh start, could open a screen's address
directly without this file's frame (logo, styles, navigation).
"""

import html

import streamlit as st

from config.settings import APP_NAME
from ui import components as ui
from ui.state import current_dataset

st.set_page_config(page_title=APP_NAME, page_icon=str(ui.ICON_FILE), layout="wide")
st.logo(str(ui.LOGO_FILE), icon_image=str(ui.ICON_FILE), size="large")
ui.inject_styles()

# Navigation follows the order a user works in: prepare the data, analyse it,
# then share the results. Home sits above the groups (the "" section has no
# header). Page addresses come from the file names, so labels and groups can
# change without breaking links or st.switch_page calls.
navigation = st.navigation({
    "": [
        st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
    ],
    "Prepare": [
        st.Page("views/upload.py", title="Upload data", icon=":material/upload_file:"),
        st.Page("views/data_quality.py", title="Data quality", icon=":material/fact_check:"),
        st.Page("views/data_cleaning.py", title="Data cleaning", icon=":material/cleaning_services:"),
    ],
    "Analyse": [
        st.Page("views/dashboard.py", title="Dashboard", icon=":material/monitoring:"),
        st.Page("views/comparisons.py", title="Comparisons", icon=":material/compare_arrows:"),
        st.Page("views/performance.py", title="Performance", icon=":material/leaderboard:"),
        st.Page("views/anomalies.py", title="Anomalies", icon=":material/troubleshoot:"),
        st.Page("views/insights.py", title="Insights", icon=":material/lightbulb:"),
    ],
    "Share": [
        st.Page("views/reports.py", title="Reports", icon=":material/summarize:"),
    ],
})

def dataset_card_html() -> str:
    """The sidebar's current-dataset card, as one markdown block (keeps the label spacing)."""
    summary = current_dataset()
    label = '<p class="bp-sidebar-label">Current dataset</p>'
    if summary is None:
        return (label + '<p class="bp-sidebar-empty">No data loaded</p>'
                '<p class="bp-sidebar-meta">Upload a file or load the demo to begin.</p>')
    sheet = f" · {html.escape(summary.sheet_name)}" if summary.sheet_name else ""
    second = f"<br>Second file: {html.escape(summary.second_file)}" if summary.second_file else ""
    quality = ""
    if summary.quality:
        score, grade, error_rows = summary.quality
        errors = f" · {error_rows:,} rows with errors" if error_rows else ""
        quality = f'<p class="bp-sidebar-meta">Quality {score:g} · {grade}{errors}</p>'
    return (label + f'<p class="bp-sidebar-file">{html.escape(summary.file_name)}</p>'
            f'<p class="bp-sidebar-meta">{summary.version}{sheet} · {summary.rows:,} rows{second}</p>' + quality)


# Current dataset, shown under the navigation on every page. Pages can load or
# clean data while they run (after this point), so the card is drawn once now
# and redrawn after the page has finished; otherwise it lags one step behind.
with st.sidebar, st.container(key="bp_sidebar_dataset"):
    dataset_card = st.empty()
dataset_card.markdown(dataset_card_html(), unsafe_allow_html=True)

with st.sidebar:
    ui.theme_switch()

navigation.run()
dataset_card.markdown(dataset_card_html(), unsafe_allow_html=True)
