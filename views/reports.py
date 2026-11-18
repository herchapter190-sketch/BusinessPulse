"""Reports screen (Stage 8): download the analysis as an Excel workbook or a PDF report.

Uses the same shared controls as the other Analysis pages (data version, second
file, counting options, date range and filters; ui/analysis_context.py), so the
exported figures are exactly those shown in the app. The report snapshot is built
in tools/report_data.py; the files are written by tools/excel_report.py and
tools/pdf_report.py only when a download button is clicked, in memory (nothing is
saved on disk).
"""

import logging

import streamlit as st

from config.quality_rules import QualityRules
from tools import business_metrics as bm
from tools import excel_report, pdf_report
from tools import report_data as rd
from ui import analysis_context as ctx
from ui import components as ui
from ui.state import load_demo

PREFIX = "rep"
FREQUENCY_OPTIONS = {"auto": "Automatic", "D": "Day", "W": "Week", "M": "Month", "Q": "Quarter"}
LOGGER = logging.getLogger(__name__)


def render_empty_state() -> None:
    ui.page_header("Reports", "Download your analysis as an Excel workbook or a PDF report.", eyebrow="Analysis")
    actions = ui.empty_state("reports", ":material/summarize:", "No data loaded yet",
                             "Upload a sales or expense file first, or try the demo with fictional sample data.")
    left, right = actions.columns(2)
    if left.button("Go to Upload data", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")
    right.button("Load demo data", type="primary", icon=":material/play_arrow:", width="stretch",
                 key="load_demo", on_click=load_demo, help="Loads the fictional sample sales and expense files.")


def pdf_download(report: rd.ReportData):
    """Called only when the button is clicked. A failure gives a one-page PDF that says why."""
    def build() -> bytes:
        try:
            return pdf_report.pdf_bytes(report)
        except Exception as error:  # noqa: BLE001 - shown to the user inside the file
            LOGGER.exception("PDF report failed")
            return pdf_report.error_pdf(f"{type(error).__name__}: {error}")
    return build


def excel_download(report: rd.ReportData):
    def build() -> bytes:
        try:
            return excel_report.excel_bytes(report)
        except Exception as error:  # noqa: BLE001 - shown to the user inside the file
            LOGGER.exception("Excel report failed")
            return excel_report.error_workbook(f"{type(error).__name__}: {error}")
    return build


def render_contents(report: rd.ReportData) -> None:
    """What the report will contain, and what is left out (with why)."""
    insight_count, anomaly_count = len(report.insights.insights), len(report.anomalies.anomalies)
    items = [
        ("Key figures", "success", f"{sum(k.available for k in report.kpis())} of {len(report.kpis())} available"),
        ("Trends", "success" if not report.trend.empty else "neutral",
         "Per " + rd_frequency(report) if not report.trend.empty else "Not available: no dates in the period"),
        ("Breakdowns", "success" if report.breakdowns else "neutral",
         f"{len(report.breakdowns)} breakdown(s)" if report.breakdowns else "No product, category or region data"),
        ("Performance", "success" if report.dimensions else "neutral",
         ", ".join(d.by for d in report.dimensions) if report.dimensions else "No product, category or region data"),
        ("Insights", "success" if insight_count else "neutral", f"{insight_count:,} finding(s)"),
        ("Anomalies", "success" if report.anomalies.checked else "neutral",
         f"{anomaly_count:,} unusual month(s)" if report.anomalies.checked else "Not enough complete months"),
    ]
    items += [(r.title, "info", f"{len(r.frame):,} row(s), Excel only") for r in report.records]
    with ui.card("report_contents"):
        ui.card_label("What the report contains")
        for name, kind, text in items:
            st.markdown(f"{ui.status_badge(kind, name)} {text}")


def rd_frequency(report: rd.ReportData) -> str:
    return FREQUENCY_OPTIONS.get(report.frequency, report.frequency).lower()


# ---------------------------------------------------------------------------
loaded, signature = ctx.loaded_file()
if loaded is None:
    render_empty_state()
    st.stop()

header_actions = ui.page_header("Reports", "Download the analysis as a PDF report or an Excel workbook.",
                                eyebrow="Analysis", actions=1)
with header_actions.popover("Settings", icon=":material/tune:", width="stretch"):
    frequency_choice = st.selectbox("Group dates by", list(FREQUENCY_OPTIONS), format_func=FREQUENCY_OPTIONS.get,
                                    key="rep_frequency")
    include_pending, include_errors = ctx.counting_settings(PREFIX)

data = ctx.select_data(loaded, signature, include_errors, PREFIX)
sales, expenses = data.sales, data.expenses
if not data.has_amounts:
    roles = ", ".join(sorted(data.main.mapping.matches)) or "none"
    st.warning(f"**BusinessPulse couldn't find sales or expense amounts in {loaded.file_name}.** A report needs a "
               "column with sales/revenue amounts (or quantity and unit price), or a column with expense amounts. "
               f"Recognised fields: {roles}. Check **Column recognition** on the Data quality page if a column was "
               "missed.", icon=":material/warning:")
    ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)
    st.stop()

ctx.render_companion_controls(data.main.kind, data.companion_name, data.companion_error)

today = QualityRules().today()
full_range = bm.available_range(sales, expenses)
start_range = bm.default_range(sales, expenses, today)
with st.container(key="bp_rep_filters"):   # Reports-only wrapper: lets the filter row wrap on narrow screens
    filters = ctx.render_filters(sales, full_range, start_range, PREFIX)
window = (filters.start or start_range[0], filters.end or start_range[1]) if full_range else None
frequency = (frequency_choice if frequency_choice != "auto" else bm.choose_frequency(*window)) if window else "M"

# The analysed versions of the files, for the filtered source records.
main_frame = ctx.main_versions(loaded, signature)[data.version]
companion_frame = ctx.load_companion()[0]
frame_of = {id(data.main): main_frame}
future_count, latest_future = bm.future_dated(sales, expenses, today)
future_note = bm.future_rows_note(future_count, latest_future, start_range[1], filters.end) if full_range else None
extra_notes = [("warning", future_note)] if future_note else []
if data.companion_error:
    extra_notes.append(("warning", data.companion_error))

try:
    with st.spinner("Preparing the report..."):
        report = rd.build_report(
            file_name=loaded.file_name, version=data.version, companion_name=data.companion_name,
            sales=sales, expenses=expenses,
            sales_frame=frame_of.get(id(sales), companion_frame) if sales is not None else None,
            expense_frame=frame_of.get(id(expenses), companion_frame) if expenses is not None else None,
            filters=filters, window=window, full_range=full_range, frequency=frequency,
            include_pending=include_pending, include_errors=include_errors, today=today, extra_notes=extra_notes)
except Exception as error:  # noqa: BLE001 - explained to the user
    LOGGER.exception("Report preparation failed")
    st.error(f"**The report could not be prepared.** {type(error).__name__}: {error}. Try resetting the filters or "
             "choosing another data version.", icon=":material/error:")
    st.stop()

if window:
    st.caption(f"**Reporting period:** {report.period_text} · data available {bm.format_date(full_range[0])} – "
               f"{bm.format_date(full_range[1])} · {report.counting_text.lower()}")
ui.notes_panel([("error" if kind == "error" else kind, text) for kind, text in report.notes
                if kind in ("error", "warning")])

if sales is not None and report.result.sales_rows.empty and (filters.has_dimension_filters or filters.start):
    st.info("**No sales match the selected filters.** The report can still be downloaded, but sales figures will "
            "show as not available. Change the filters or click **Reset filters**.",
            icon=":material/filter_alt_off:")

left, right = st.columns([3, 2], gap="medium")
with left:
    with ui.card("report_downloads"):
        ui.card_label("Download")
        st.download_button("Download PDF report", pdf_download(report), file_name=f"{report.file_stem}.pdf",
                           mime="application/pdf", type="primary", icon=":material/picture_as_pdf:",
                           key="download_pdf", width="stretch", on_click="ignore")
        st.caption("Summary, key figures, actions, charts, insights and anomalies.")
        st.download_button("Download Excel workbook", excel_download(report), file_name=f"{report.file_stem}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/table_view:", key="download_excel", width="stretch", on_click="ignore")
        st.caption("Every calculated table, plus the filtered source records.")
        st.caption(":material/lock: Created when you click. Nothing is saved or sent anywhere.")
with right:
    render_contents(report)

if report.summary:
    ui.section_header("Executive summary preview", "The opening paragraph of the PDF report.")
    with ui.card("report_summary"):
        st.markdown(" ".join(report.summary).replace("$", "\\$"))
