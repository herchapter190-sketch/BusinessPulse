"""Data Quality screen.

Shows the problems found by tools/data_quality.py for the file loaded on the
Upload screen. This page only reports problems; it never changes the data.
"""

import hashlib

import pandas as pd
import streamlit as st

from config import design, session_keys
from config.quality_rules import ERROR, INFO, ROLE_LABELS, WARNING, QualityRules
from tools import data_cleaning as dc
from tools.column_mapping import MANUAL, recognize_columns
from tools.data_quality import (
    QualityIssue, QualityReport, affected_rows_table, analyze_quality, columns_table, issues_table,
)
from tools.file_loader import dataset_signature
from ui import components as ui

NOT_IN_FILE = "— Not in this file —"
SEVERITY_KIND = {ERROR: "error", WARNING: "warning", INFO: "info"}
SEVERITY_RANK = {ERROR: 0, WARNING: 1, INFO: 2}
# Translucent tints read on both the light and the dark theme; the text keeps the
# theme's own colour (the severity word itself is the label, never colour alone).
SEVERITY_HIGHLIGHT = {level: design.STATUS_TINT[kind] for level, kind in SEVERITY_KIND.items()}
GRADE_KIND = {"Excellent": "success", "Good": "info", "Fair": "warning", "Poor": "error"}
MAX_INSPECT_ROWS = 500

# Where each kind of problem can be fixed. This only chooses the advice shown;
# the checks themselves and their severities come from tools/data_quality.py.
# Operation names are Data Cleaning's own titles, so the wording matches that page.
CLEANING_ACTION = {
    "extra_spaces": dc.OP_TRIM,
    "capitalization": dc.OP_CASE,
    "suspicious_category": dc.OP_CATEGORIES,
    "missing_value": dc.OP_MISSING,
    "duplicate_row": dc.OP_DUPLICATES,
    "currency_format": dc.OP_NUMBERS,
    "number_format": dc.OP_NUMBERS,
    "inconsistent_date_format": dc.OP_DATES,
    "text_in_number": dc.OP_EDIT,
    "invalid_date": dc.OP_EDIT,
    "not_a_date": dc.OP_EDIT,
    "placeholder_value": dc.OP_EDIT,
}
OUTSIDE_CLEANING = {
    "empty_column": "Check your original file. An unused column can be ignored.",
    "missing_role": "If your file has this column, choose it under Column recognition below.",
}
FIX, REVIEW, OTHER = "fix", "review", "other"


@st.cache_data(show_spinner=False, max_entries=6)
def cached_analysis(cache_key: str, _data: pd.DataFrame, overrides: tuple) -> QualityReport:
    # `cache_key` identifies the file, sheet and data version; `_data` is not hashed (it is large).
    mapping = recognize_columns(list(_data.columns), dict(overrides))
    return analyze_quality(_data, mapping, QualityRules())


def choose_version(loaded, signature: str) -> tuple[str, pd.DataFrame, str]:
    """Which version of the data to check: original, working copy or cleaned.
    Returns (label, table, cache key)."""
    cleaning = st.session_state.get(session_keys.CLEANING_SESSION)
    cleaned = st.session_state.get(session_keys.CLEANED_DATA)
    versions = {"Original upload": (loaded.data, f"{signature}|original")}
    if cleaning is not None and cleaning.signature == signature and cleaning.steps_applied:
        versions["Working copy"] = (cleaning.working, f"{signature}|working|{cleaning.version}")
    if cleaned is not None and cleaned.signature == signature:
        versions["Cleaned dataset"] = (cleaned.data, f"{signature}|cleaned|{cleaned.created_at}")
    if len(versions) == 1:
        return "Original upload", *versions["Original upload"]
    labels = list(versions)
    choice = st.segmented_control("Data to check", labels, default=labels[-1], key="quality_version",
                                  help="Compare the original upload with your cleaning work.") or labels[-1]
    return choice, *versions[choice]


# ---------------------------------------------------------------------------
def render_empty_state() -> None:
    ui.page_header("Data quality", "Find and understand problems in your data before you analyse it.",
                   eyebrow="Data")
    actions = ui.empty_state("quality", ":material/fact_check:", "No data loaded yet",
                             "Upload a file or load sample data first, then come back here.")
    if actions.button("Go to Upload data", type="primary", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")


def render_column_recognition(columns: list[str], signature: str) -> dict:
    """Let the user check and correct which column holds which information."""
    automatic = recognize_columns(columns)
    key_prefix = f"role_{hashlib.md5(signature.encode()).hexdigest()[:8]}"
    options = [NOT_IN_FILE] + columns
    choices: dict[str, str | None] = {}

    with st.expander("Column recognition: check which column holds which information",
                     icon=":material/view_column:"):
        st.caption("BusinessPulse recognised these columns from their names. If something is wrong or missing, "
                   "choose the right column. The checks update straight away. Your file is not changed.")
        grid = st.columns(2)
        for index, (role, label) in enumerate(ROLE_LABELS.items()):
            default_column = automatic.column_for(role)
            with grid[index % 2]:
                choice = st.selectbox(label, options, index=options.index(default_column) if default_column else 0,
                                      key=f"{key_prefix}_{role}")
            choices[role] = None if choice == NOT_IN_FILE else choice

        # Only the roles the user changed are passed on, so a change always wins
        # over the automatic choice for another role.
        overrides = {role: column for role, column in choices.items() if column != automatic.column_for(role)}
        final = recognize_columns(columns, overrides)
        moved = [(role, column) for role, column in choices.items()
                 if column and final.column_for(role) != column]
        for role, column in moved:
            st.info(f"'{column}' is now used as **{ROLE_LABELS[final.role_of(column)]}**, so it is no longer "
                    f"used as {ROLE_LABELS[role]}. A column can only have one role.", icon=":material/info:")
        if st.button("Reset to automatic recognition", key="reset_roles", icon=":material/restart_alt:"):
            for role in ROLE_LABELS:
                st.session_state.pop(f"{key_prefix}_{role}", None)
            st.rerun()
    return overrides


def next_step(issue: QualityIssue) -> tuple[str, str]:
    """(kind, advice) for one problem: FIX in Data Cleaning, REVIEW (a person
    decides whether it is wrong) or OTHER (fixed outside Data Cleaning)."""
    if issue.check in OUTSIDE_CLEANING:
        return OTHER, f"Note: {OUTSIDE_CLEANING[issue.check]}"
    operation = CLEANING_ACTION.get(issue.check)
    if operation is None and issue.severity == ERROR:
        operation = dc.OP_EDIT  # a confirmed problem without its own tool: correct or exclude the rows
    if operation is None:
        if issue.severity == INFO:
            return OTHER, "Note: for your information. No change needed."
        return REVIEW, f"Check: may be correct. If wrong, {dc.OPERATION_TITLES[dc.OP_EDIT]}."
    # "Fix:" always names a Data Cleaning tool (explained in the caption above the table).
    advice = f"Fix: {dc.OPERATION_TITLES[operation]}"
    if issue.severity == ERROR and operation == dc.OP_EDIT:
        advice += f" or {dc.OPERATION_TITLES[dc.OP_INVALID]}"
    return FIX, advice


def render_verdict(report: QualityReport) -> None:
    """One plain sentence saying whether the data is ready, from the existing counts."""
    errors, warnings = report.invalid_rows, report.warning_rows
    review = f" {warnings:,} more {'row needs' if warnings == 1 else 'rows need'} a quick check." if warnings else ""
    if errors:
        text = (f"{ui.status_badge('error', 'Needs fixing')} **{errors:,} {'row has' if errors == 1 else 'rows have'} "
                f"confirmed problems.**{review} Fix them in Data Cleaning before you rely on the figures.")
    elif warnings:
        text = (f"{ui.status_badge('warning', 'Review')} **No confirmed problems.** {warnings:,} "
                f"{'row looks' if warnings == 1 else 'rows look'} unusual and should be checked by a person.")
    else:
        text = (f"{ui.status_badge('success', 'Ready')} **No rows need fixing.** "
                + ("See the notes below." if report.issues else "This data can be analysed as it is."))
    st.markdown(text)


def render_score(report: QualityReport) -> None:
    score = report.score
    # Headline: the score, then the two figures that call for action.
    with st.container(key="bp_dq_kpis"):
        lead, invalid, warned = st.columns([1.2, 1, 1])
        with lead:
            ui.kpi_card("score", "Data-quality score", f"{score.score:g} / 100", lead=True,
                        status=(GRADE_KIND[score.grade], score.grade),
                        footnote=f"{score.clean_rows:,} of {score.total_rows:,} rows have no problems.")
        with invalid:
            ui.kpi_card("invalid", "Invalid records", f"{report.invalid_rows:,}",
                        help="Rows with at least one Error (a confirmed problem).",
                        status=("error", "Needs fixing") if report.invalid_rows else ("success", "None"))
        with warned:
            ui.kpi_card("warnings", "Rows with warnings", f"{report.warning_rows:,}",
                        help="Rows with Warnings only: probably wrong, but they need a person to check.",
                        status=("warning", "Review") if report.warning_rows else ("success", "None"))
    # Facts about the file: secondary, so no cards.
    with st.container(key="bp_dq_facts"):
        first, second, third, fourth = st.columns(4)
        with first:
            ui.kpi_card("total_rows", "Total rows", f"{report.row_count:,}")
        with second:
            ui.kpi_card("total_columns", "Total columns", f"{report.column_count:,}")
        with third:
            ui.kpi_card("missing", "Missing values", f"{report.missing_cells:,}",
                        help=f"Blank cells, in {report.columns_with_missing} column(s).")
        with fourth:
            ui.kpi_card("duplicates", "Duplicate rows", f"{report.duplicate_rows:,}",
                        help="Rows that are exact copies of an earlier row.")


def render_next_steps(report: QualityReport) -> None:
    """The Data Cleaning tools that fix the problems found, and what needs a person's judgement."""
    fixes: dict[str, set[str]] = {}
    reviews: list[str] = []
    for issue in report.issues:
        kind, _ = next_step(issue)
        if kind == FIX:
            operation = CLEANING_ACTION.get(issue.check, dc.OP_EDIT)
            fixes.setdefault(operation, set()).add(issue.column or "whole rows")
        elif kind == REVIEW:
            reviews.append(f"{issue.problem.lower()} in {issue.column}" if issue.column else issue.problem.lower())
    if not fixes and not reviews:
        return
    ui.section_header("What to do next")
    with ui.card("dq_next"):
        if fixes:
            heading, action = st.columns([3, 2], vertical_alignment="center")
            with heading:
                ui.card_label("Fix in Data Cleaning")
            if action.button("Fix problems in Data Cleaning", type="primary", icon=":material/cleaning_services:",
                             key="go_cleaning_next", width="stretch"):
                st.switch_page("views/data_cleaning.py")
            order = list(dc.OPERATION_TITLES)
            for operation in sorted(fixes, key=order.index):
                ui.list_row(f"dq_fix_{operation}", dc.OPERATION_ICONS[operation], dc.OPERATION_TITLES[operation],
                            ", ".join(sorted(fixes[operation])))
            if report.invalid_rows:
                ui.list_row("dq_fix_invalid_option", dc.OPERATION_ICONS[dc.OP_INVALID],
                            f"Or {dc.OPERATION_TITLES[dc.OP_INVALID].lower()}",
                            f"Leave the {report.invalid_rows:,} rows with errors out of your analysis.")
        if reviews:
            ui.card_label("Check yourself")
            ui.list_row("dq_review", ":material/visibility:", "May be correct (a return, a large order)",
                        "Change only if wrong: " + "; ".join(dict.fromkeys(reviews)) + ".")


def render_score_explanation(report: QualityReport) -> None:
    score = report.score
    score_column, levels_column = st.columns(2)
    with score_column, st.expander("How is the score calculated?", icon=":material/calculate:"):
        st.markdown(
            f"""
The score is the share of rows that can be used without doubt:

- A row with **no problems** counts as **1**. There are **{score.clean_rows:,}** of these.
- A row with **only warnings** counts as **{score.warning_row_weight:g}**, because it is probably fine but needs
  checking. There are **{score.warning_rows:,}** of these.
- A row with **at least one error** counts as **0**. There are **{score.error_rows:,}** of these.
- **Info** notes never affect the score.

Row score = ({score.clean_rows:,} + {score.warning_row_weight:g} × {score.warning_rows:,}) ÷ {score.total_rows:,} × 100
= **{score.row_score:g}**

Each completely empty column then removes a few points (here: {score.empty_columns} empty column(s),
−{score.empty_column_penalty:g} points). **Final score: {score.score:g} / 100 ({score.grade}).**

Grades: Excellent ≥ 98 · Good ≥ 90 · Fair ≥ 75 · Poor below 75.
"""
        )
    with levels_column, st.expander("What do Error, Warning and Info mean?", icon=":material/help:"):
        st.markdown(
            f"""
- {ui.status_badge("error", "Error")} **A confirmed problem.** The value can't be used as it is: for example an
  impossible date (30 February), text where a required number should be, a blank required field, or an exact
  duplicate row.
- {ui.status_badge("warning", "Warning")} **Probably a problem, but it could be legitimate.** For example, a
  negative quantity might be a return, and an unusually large value might be real. Please check these rows.
- {ui.status_badge("info", "Info")} **Something to be aware of.** It does not count against the score.
"""
        )


def style_severity(table: pd.DataFrame):
    return table.style.map(lambda value: f"background-color: {SEVERITY_HIGHLIGHT.get(value, 'transparent')}; "
                                         "font-weight: 600", subset=["Severity"])


def severity_summary(counts: dict[str, int]) -> str:
    """Badges such as '3 errors · 1 warning'. Levels with no problems are left
    out (no '0 infos'); Info counts read as '1 info note' / '2 info notes'."""
    nouns = {ERROR: ("error", "errors"), WARNING: ("warning", "warnings"), INFO: ("info note", "info notes")}
    return " ".join(ui.status_badge(SEVERITY_KIND[level], f"{counts[level]:,} {nouns[level][counts[level] != 1]}")
                    for level in (ERROR, WARNING, INFO) if counts.get(level))


def render_issues(report: QualityReport, data: pd.DataFrame) -> None:
    ui.section_header("Problems found")
    counts = report.severity_counts
    if not report.issues:
        st.success("**No problems found.** This data passed every check.", icon=":material/check_circle:")
        if st.button("Go to the dashboard", type="primary", icon=":material/dashboard:", key="go_dashboard"):
            st.switch_page("views/dashboard.py")
        return
    with ui.card("dq_issues"):
        render_issue_table(report, counts)
    ui.section_header("Inspect affected rows")
    with ui.card("dq_inspect"):
        render_inspector(report, data)


def render_issue_table(report: QualityReport, counts: dict[str, int]) -> None:
    summary_column, filter_column = st.columns([3, 2], vertical_alignment="bottom")
    summary_column.markdown(severity_summary(counts))
    with filter_column:
        selected = st.segmented_control(
            "Show", [ERROR, WARNING, INFO], selection_mode="multi", default=[ERROR, WARNING, INFO],
            key="severity_filter",
            help="Errors must be fixed or excluded · Warnings need a person to check · Info is for awareness. "
                 "In What to do, Fix names the Data Cleaning tool; Check means the value may be correct.")
    table = issues_table(report)
    table.insert(4, "What to do", [next_step(issue)[1] for issue in report.issues])
    table = table[table["Severity"].isin(selected or [])]
    # Most serious first; within a level the checks keep their usual order.
    table = table.sort_values("Severity", key=lambda column: column.map(SEVERITY_RANK), kind="stable")
    if table.empty:
        # Explain an empty filter result instead of showing a blank table.
        if selected:
            st.info(f"**Nothing to show.** This file has no problems at the selected level "
                    f"({', '.join(selected)}). Choose another level above.", icon=":material/info:")
        else:
            st.info("**Nothing to show.** Choose at least one level above (Error, Warning or Info).",
                    icon=":material/info:")
    else:
        st.dataframe(
            style_severity(table), hide_index=True, width="stretch",
            column_config={
                "Severity": st.column_config.TextColumn(width="small"),
                "Problem": st.column_config.TextColumn(width="medium"),
                "Affected rows": st.column_config.TextColumn(width="small"),
                "What to do": st.column_config.TextColumn(width="large"),
                "Example values": st.column_config.TextColumn(width="medium"),
                "Explanation": st.column_config.TextColumn("What it means", width="large"),
            },
        )


def render_inspector(report: QualityReport, data: pd.DataFrame) -> None:
    inspectable = [index for index, issue in enumerate(report.issues) if issue.rows]
    if not inspectable:
        st.caption("None of the problems above point to specific rows.")
        return
    choice = st.selectbox(
        "Choose a problem to see the rows it affects",
        inspectable,
        format_func=lambda index: (f"{report.issues[index].severity} · {report.issues[index].column or 'All columns'}"
                                   f" · {report.issues[index].problem} ({report.issues[index].affected_count:,} rows)"),
        key="inspect_issue",
    )
    issue = report.issues[choice]
    st.markdown(f"{ui.status_badge(SEVERITY_KIND[issue.severity], issue.severity)} {issue.explanation}  \n"
                f"**What to do:** {next_step(issue)[1]}"
                + (" (in Data Cleaning)" if next_step(issue)[0] == FIX else ""))
    rows = affected_rows_table(data, issue, MAX_INSPECT_ROWS)
    styled = rows.style
    if issue.column in rows.columns:
        styled = styled.set_properties(subset=[issue.column],
                                       **{"background-color": SEVERITY_HIGHLIGHT[issue.severity]})
    st.dataframe(styled, width="stretch", height=min(420, 38 + 35 * len(rows)))
    shown = len(rows)
    note = f"Showing {shown:,} of {issue.affected_count:,} affected rows" if issue.affected_count > shown \
        else f"Showing all {shown:,} affected rows"
    st.caption(f"{note}, exactly as written in the file. Row numbers match the Upload preview.")


# ---------------------------------------------------------------------------
loaded = st.session_state.get(session_keys.LOADED_DATA)
if loaded is None:
    render_empty_state()
    st.stop()

active = st.session_state.get(session_keys.ACTIVE_FILE, {})
signature = dataset_signature(loaded.file_name, loaded.sheet_name, active.get("content", b""))
source = f"{loaded.file_name}" + (f" · worksheet '{loaded.sheet_name}'" if loaded.sheet_name else "")
header_actions = ui.page_header("Data quality", "Every problem explained. Nothing in your data is changed here.",
                                eyebrow="Data", actions=1)
if header_actions.button("Fix problems", icon=":material/auto_fix_high:", key="go_cleaning", width="stretch",
                         help="Data cleaning finds the safe fixes and applies them with one click."):
    st.switch_page("views/data_cleaning.py")
version_label, data, cache_key = choose_version(loaded, signature)
st.caption(f"Checking the **{version_label.lower()}** of **{source}**.")

# The results come first on screen, but column recognition must run first in
# code (the checks depend on it), so it is drawn into a container further down.
results = st.container()
with st.container():
    ui.section_header("How your columns were read")
    recognised_note = st.empty()
    overrides = render_column_recognition([str(column) for column in data.columns], signature)
    columns_checked = st.container()
# Remember the user's corrections so the cleaning page and the dashboard use them too.
st.session_state.setdefault(session_keys.ROLE_OVERRIDES, {})[signature] = overrides
with st.spinner("Checking data quality..."):
    report = cached_analysis(cache_key, data, tuple(sorted(overrides.items(), key=lambda kv: kv[0])))

kind = report.mapping.dataset_kind()
manual = [m for m in report.mapping.matches.values() if m.confidence == MANUAL]
recognised = len(report.mapping.matches)
recognised_note.caption(
    f"Recognised {recognised} of {len(ROLE_LABELS)} common business fields"
    + (f" ({len(manual)} chosen by you)" if manual else "")
    + {"sales": " · looks like **sales** data", "expenses": " · looks like **expense** data"}.get(kind, ""))

with results:
    render_verdict(report)
    render_score(report)
    render_score_explanation(report)
    render_next_steps(report)
    render_issues(report, data)

with columns_checked, st.expander("Columns checked", icon=":material/table_chart:"):
    st.dataframe(columns_table(report), hide_index=True, width="stretch")
    st.caption("**Checked as** shows which checks were applied: Number, Date, Category (spelling and "
               "capitalization), Text, or Empty.")
