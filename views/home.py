"""Home: what BusinessPulse does and the one next step.

Two states share one layout:
- nothing loaded: start with your own file (primary) or the fictional demo;
- a dataset loaded: pick up where you left off (the Dashboard is the primary).
"""

import html

import streamlit as st

from config.settings import MAX_UPLOAD_MB
from ui import components as ui
from ui.state import current_dataset, load_demo

# The four steps follow the navigation groups: Prepare, Analyse, Share.
STEPS = [
    (":material/upload_file:", "Upload", "CSV or Excel, previewed before any calculation."),
    (":material/rule:", "Check and clean", "Problems explained; you approve every fix."),
    (":material/monitoring:", "Analyse", "Profit, trends and unusual changes by stated rules."),
    (":material/description:", "Share", "Export to an Excel workbook or a PDF report."),
]

# Shown as one quiet line with icons under the steps.
PROMISES = [
    (":material/lock:", "Stays on this computer"),
    (":material/draft:", "Original file never changed"),
    (":material/calculate:", "Every figure explained"),
]

DEMO_HELP = "Loads fictional sample sales and expenses and opens the dashboard."

ui.page_header(
    "See how your business is performing",
    "Turn sales and expense spreadsheets into revenue, profit and trends.",
)

summary = current_dataset()
with ui.card("home_start"):
    text_column, action_column = st.columns([3, 2], gap="medium", vertical_alignment="center")
    if summary is None:
        with text_column:
            ui.card_title(
                "Start with a spreadsheet",
                "One row per sale or expense. No file to hand? Try the fictional demo.",
                icon=":material/table_chart:",
            )
            st.markdown(f'<p class="bp-home-meta bp-mono">CSV · XLSX · up to {MAX_UPLOAD_MB} MB</p>',
                        unsafe_allow_html=True)
        with action_column:
            if st.button("Upload data", type="primary", icon=":material/arrow_forward:", width="stretch"):
                st.switch_page("views/upload.py")
            if st.button("Explore the demo", width="stretch", key="home_demo", help=DEMO_HELP):
                load_demo()
                st.switch_page("views/dashboard.py")
    else:
        sheet = f" · {summary.sheet_name}" if summary.sheet_name else ""
        second = f" · second file {summary.second_file}" if summary.second_file else ""
        with text_column:
            st.markdown(
                '<p class="bp-home-label">Current dataset</p>'
                f'<p class="bp-home-file bp-mono">{html.escape(summary.file_name)}</p>'
                f'<p class="bp-home-meta bp-mono">{html.escape(f"{summary.version}{sheet} · {summary.rows:,} rows{second}")}</p>',
                unsafe_allow_html=True,
            )
        with action_column:
            if st.button("Open the dashboard", type="primary", icon=":material/arrow_forward:", width="stretch"):
                st.switch_page("views/dashboard.py")
            if st.button("Check data quality", width="stretch"):
                st.switch_page("views/data_quality.py")
            upload, demo = st.columns(2, gap="small")
            if upload.button("Upload another file", type="tertiary", width="stretch"):
                st.switch_page("views/upload.py")
            if demo.button("Explore the demo", type="tertiary", width="stretch", key="home_demo",
                           help=DEMO_HELP + " Replaces the data loaded now."):
                load_demo()
                st.switch_page("views/dashboard.py")

ui.section_header("How it works")
with st.container(key="bp_home_steps"):
    for number, (column, (icon, title, text)) in enumerate(zip(st.columns(4, gap="small"), STEPS), start=1):
        with column, ui.card(f"home_step_{number}"):
            ui.card_title(title, text, icon=icon)
            st.markdown(f'<p class="bp-step-number">{number:02d}</p>', unsafe_allow_html=True)

with st.container(key="bp_home_promises"):
    st.caption("&emsp;".join(f"{icon} {label}" for icon, label in PROMISES))
