"""Data Cleaning screen.

The user chooses an operation, reviews exactly what it would change, and only
then applies it to the *working copy*. The original data is never changed.
All logic lives in tools/data_cleaning.py; this page only shows it.
"""

import pandas as pd
import streamlit as st
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

from config import session_keys
from config.quality_rules import QualityRules
from tools import data_cleaning as dc
from tools.column_mapping import recognize_columns
from tools.data_quality import analyze_quality
from tools.file_loader import blank_mask, dataset_signature, display_value
from ui import components as ui

OP_KEY = "cleaning_operation"
FLASH_KEY = "cleaning_flash"
PLAN_KEY = session_keys.CLEANING_PLAN
OPTION_PREFIX = "clean_opt_"
OPERATION_ICONS = dc.OPERATION_ICONS
OPERATION_HELP = {
    dc.OP_TRIM: "Remove spaces at the start and end of text values.",
    dc.OP_CASE: "Make the same name use the same capital letters everywhere.",
    dc.OP_CATEGORIES: "Merge misspelled or differently written names into one spelling.",
    dc.OP_DATES: "Turn date text in any common format into real dates.",
    dc.OP_NUMBERS: "Turn text such as $1,200.00 into real numbers.",
    dc.OP_MISSING: "Decide what to do with blank cells in a column.",
    dc.OP_DUPLICATES: "Review exact duplicate rows and exclude the extra copies.",
    dc.OP_INVALID: "Review rows with confirmed errors and exclude the ones you choose.",
    dc.OP_EDIT: "Correct a single value by hand.",
}
# What each tool does to the working copy, said before the user starts, so a
# tool that removes rows is never a surprise. Wording only; no logic depends on it.
SAFE_FIX_TITLES = {   # short names for the grouped "Recommended fixes" rows
    dc.OP_TRIM: "Trim extra spaces", dc.OP_CASE: "Unify capital letters", dc.OP_CATEGORIES: "Merge identical names",
    dc.OP_DATES: "Convert dates", dc.OP_NUMBERS: "Convert numbers",
}
CHANGES_VALUES, REMOVES_ROWS, EITHER ="Changes values", "Removes rows", "Fills blanks or removes rows"
OPERATION_EFFECT = {
    dc.OP_TRIM: CHANGES_VALUES, dc.OP_CASE: CHANGES_VALUES, dc.OP_CATEGORIES: CHANGES_VALUES,
    dc.OP_DATES: CHANGES_VALUES, dc.OP_NUMBERS: CHANGES_VALUES, dc.OP_MISSING: EITHER,
    dc.OP_DUPLICATES: REMOVES_ROWS, dc.OP_INVALID: REMOVES_ROWS, dc.OP_EDIT: CHANGES_VALUES,
}


def opt(name: str) -> str:
    return f"{OPTION_PREFIX}{name}"


# ---------------------------------------------------------------------------
# Callbacks (run before the page redraws, so they may reset widgets)
# ---------------------------------------------------------------------------
def clear_operation() -> None:
    st.session_state[OP_KEY] = None
    for key in [key for key in st.session_state if str(key).startswith(OPTION_PREFIX)]:
        del st.session_state[key]


def open_tool(operation: str) -> None:
    """Open a suggested tool (same as choosing it in step 1)."""
    clear_operation()
    st.session_state[OP_KEY] = operation


def apply_proposal(session: dc.CleaningSession, proposal: dc.Proposal, confirmed: bool) -> None:
    try:
        entry = session.apply(proposal, confirmed=confirmed)
    except dc.CleaningError as error:
        st.session_state[FLASH_KEY] = ("error", str(error))
        return
    st.session_state[FLASH_KEY] = ("success", f"Applied **{entry.operation}** to {entry.rows_affected:,} row(s). "
                                              "You can undo it with **Undo last step**.")
    clear_operation()


def undo_last(session: dc.CleaningSession) -> None:
    entry = session.undo()
    if entry:
        st.session_state[FLASH_KEY] = ("success", f"{entry.description}")


def reset_working(session: dc.CleaningSession) -> None:
    session.reset()
    st.session_state[FLASH_KEY] = ("success", "The working copy was reset to the original data.")
    clear_operation()


def approve(session: dc.CleaningSession) -> None:
    cleaned = session.approve()
    st.session_state[session_keys.CLEANED_DATA] = cleaned
    st.session_state[FLASH_KEY] = ("success", f"Cleaned dataset created with {cleaned.row_count:,} rows. Later "
                                              "analysis will use it. The original upload is still available.")


# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=6)
def working_report(signature: str, version: int, overrides: tuple, _frame: pd.DataFrame):
    mapping = recognize_columns([str(column) for column in _frame.columns], dict(overrides))
    return analyze_quality(_frame, mapping, QualityRules())


def render_empty_state() -> None:
    ui.page_header("Data cleaning", "Fix problems step by step, with a preview and an undo for every change.",
                   eyebrow="Data")
    actions = ui.empty_state("cleaning", ":material/cleaning_services:", "No data loaded yet",
                             "Upload a file or load sample data first.")
    if actions.button("Go to Upload data", type="primary", icon=":material/upload_file:", width="stretch"):
        st.switch_page("views/upload.py")


def get_session(loaded) -> dc.CleaningSession:
    active = st.session_state.get(session_keys.ACTIVE_FILE, {})
    signature = dataset_signature(loaded.file_name, loaded.sheet_name, active.get("content", b""))
    session = st.session_state.get(session_keys.CLEANING_SESSION)
    if session is None or session.signature != signature:
        session = dc.CleaningSession(loaded.data, signature, loaded.file_name, loaded.sheet_name)
        st.session_state[session_keys.CLEANING_SESSION] = session
    return session


def issue_columns(report, check: str) -> list[str]:
    return [issue.column for issue in report.issues if issue.check == check and issue.column]


def default_index(options: list, preferred: list) -> int:
    for value in preferred:
        if value in options:
            return options.index(value)
    return 0


# ---------------------------------------------------------------------------
# Option panels: each returns a Proposal (or None while choices are incomplete)
# ---------------------------------------------------------------------------
def options_trim(session, report):
    text_cols = dc.text_columns(session.working)
    if not text_cols:
        st.info("There are no text columns to trim.")
        return None
    flagged = issue_columns(report, "extra_spaces")
    columns = st.multiselect("Columns", text_cols, default=[c for c in flagged if c in text_cols] or text_cols,
                             key=opt("trim_columns"))
    inner = st.checkbox("Also replace double spaces inside values with a single space", key=opt("trim_inner"))
    return dc.propose_trim(session, columns, inner) if columns else None


def options_case(session, report):
    text_cols = dc.text_columns(session.working)
    if not text_cols:
        st.info("There are no text columns.")
        return None
    column = st.selectbox("Column", text_cols, index=default_index(text_cols, issue_columns(report, "capitalization")),
                          key=opt("case_column"))
    styles = {dc.CASE_MOST_COMMON: "Match the most common spelling (recommended)", dc.CASE_TITLE: "Title Case",
              dc.CASE_UPPER: "UPPER CASE", dc.CASE_LOWER: "lower case"}
    style = st.radio("Capitalization", list(styles), format_func=styles.get, key=opt("case_style"))
    return dc.propose_capitalization(session, column, style)


def options_categories(session, report):
    text_cols = dc.text_columns(session.working)
    if not text_cols:
        st.info("There are no text columns.")
        return None
    column = st.selectbox("Column", text_cols,
                          index=default_index(text_cols, issue_columns(report, "suspicious_category")
                                              + issue_columns(report, "capitalization")),
                          key=opt("cat_column"))
    table = dc.suggest_category_replacements(session, column)
    if table.empty:
        st.success("No category names in this column look inconsistent.", icon=":material/check_circle:")
        return None
    st.caption("Review each suggested replacement. Untick any you don't want, or type a different name.")
    table.insert(0, "Apply", True)
    edited = st.data_editor(
        table, hide_index=True, width="stretch", key=opt(f"cat_editor_{column}_{session.version}"),
        disabled=["Current value", "Rows", "Reason"],
        column_config={"Apply": st.column_config.CheckboxColumn(width="small"),
                       "Replace with": st.column_config.TextColumn(required=True)},
    )
    replacements = {row["Current value"]: row["Replace with"] for _, row in edited.iterrows() if row["Apply"]}
    return dc.propose_category_normalization(session, column, replacements)


def options_dates(session, report):
    columns = [str(c) for c in session.working.columns]
    date_role = report.mapping.column_for("date")
    column = st.selectbox("Column", columns, index=default_index(columns, [date_role] if date_role else []),
                          key=opt("date_column"))
    evidence = dc.date_order_evidence(session.working[column])
    order = None
    if evidence.slash_dates:
        st.markdown("**Dates written with slashes**")
        lines = [f"- {evidence.day_first_only:,} can only be day-first"
                 + (f" (e.g. {evidence.day_first_example})" if evidence.day_first_example else ""),
                 f"- {evidence.month_first_only:,} can only be month-first"
                 + (f" (e.g. {evidence.month_first_example})" if evidence.month_first_example else ""),
                 f"- **{evidence.ambiguous:,} could be either**"
                 + (f" (e.g. {evidence.ambiguous_example})" if evidence.ambiguous_example else "")]
        st.markdown("\n".join(lines))
        if evidence.ambiguous:
            labels = {dc.ORDER_DAY_FIRST: "Day first: 03/05/2025 = 3 May 2025",
                      dc.ORDER_MONTH_FIRST: "Month first: 03/05/2025 = 5 March 2025",
                      dc.ORDER_LEAVE: "Don't convert the ambiguous dates (leave them as they are)"}
            recommendation = evidence.recommendation
            if recommendation:
                st.caption(f"Recommended: **{labels[recommendation].split(':')[0]}**, because other dates in this "
                           "column can only be read that way. BusinessPulse will not choose for you.")
            else:
                st.caption("Nothing in this column shows which order is used. Please check with whoever "
                           "created the file. BusinessPulse will not choose for you.")
            order = st.radio("How should the ambiguous dates be read?", list(labels), format_func=labels.get,
                             index=None, key=opt("date_order"))
    return dc.propose_date_conversion(session, column, order)


def options_numbers(session, report):
    columns = [str(c) for c in session.working.columns]
    text_number_columns = [p.column for p in report.profiles
                           if p.treated_as == "Number" and not is_numeric_dtype(session.working[p.column])]
    column = st.selectbox("Column", columns, index=default_index(columns, text_number_columns),
                          key=opt("num_column"))
    evidence = dc.decimal_evidence(session.working[column])
    conventions = {dc.DECIMAL_AUTO: "Detect automatically", dc.DECIMAL_POINT: "Decimal point: 1,234.50",
                   dc.DECIMAL_COMMA: "Decimal comma: 1.234,50"}
    convention = st.radio("Number format", list(conventions), format_func=conventions.get, horizontal=True,
                          key=opt("num_convention"))
    if evidence.point or evidence.comma or evidence.ambiguous:
        st.caption(f"Found {evidence.point:,} value(s) with a decimal point, {evidence.comma:,} with a decimal comma, "
                   f"and {evidence.ambiguous:,} that could be either (like 1,234).")
    blanks = st.checkbox("Treat placeholders such as N/A, unknown or TBD as blank (missing)", key=opt("num_blank"))
    return dc.propose_number_conversion(session, column, convention, blanks)


def options_missing(session, report):
    frame = session.working
    counts = blank_mask(frame).sum()
    with_missing = [str(c) for c in frame.columns if counts[c] > 0]
    if not with_missing:
        st.success("No column has missing values.", icon=":material/check_circle:")
        return None
    column = st.selectbox("Column", with_missing, format_func=lambda c: f"{c} ({int(counts[c]):,} blank)",
                          key=opt("missing_column"))
    numeric = is_numeric_dtype(frame[column])
    methods = {dc.MISSING_FILL_VALUE: "Fill with a value I type", dc.MISSING_FILL_UNKNOWN: "Fill with 'Unknown'",
               dc.MISSING_FILL_ZERO: "Fill with 0", dc.MISSING_FILL_MEDIAN: "Fill with the median",
               dc.MISSING_FILL_MEAN: "Fill with the average", dc.MISSING_EXCLUDE: "Exclude these rows"}
    if numeric:
        methods.pop(dc.MISSING_FILL_UNKNOWN)
    else:
        st.caption("Number-based options appear once this column has been converted with 'Convert numbers'.")
        for key in (dc.MISSING_FILL_ZERO, dc.MISSING_FILL_MEDIAN, dc.MISSING_FILL_MEAN):
            methods.pop(key)
    method = st.radio("What should happen to the blank cells?", list(methods), format_func=methods.get,
                      index=None, key=opt("missing_method"))
    if method is None:
        st.caption("Leaving blanks as they are is also fine: choose nothing and the column stays unchanged.")
        return None
    value = st.text_input("Value to fill in", key=opt("missing_value")) if method == dc.MISSING_FILL_VALUE else None
    return dc.propose_missing_values(session, column, method, value)


def options_duplicates(session, report):
    duplicates = dc.find_duplicate_rows(session.working)
    if duplicates.empty:
        st.success("There are no duplicate rows.", icon=":material/check_circle:")
        return None
    st.caption(f"{len(duplicates):,} row(s) are exact copies of an earlier row. The earlier copy is always kept. "
               "Untick any row you want to keep.")
    table = duplicates.copy()
    table.insert(0, "Exclude", True)
    edited = st.data_editor(table, hide_index=True, key=opt(f"dup_editor_{session.version}"),
                            disabled=["Row", "Same as row"])
    return dc.propose_exclude_duplicates(session, [int(r) for r in edited.loc[edited["Exclude"], "Row"]])


def options_invalid(session, report):
    invalid = dc.find_invalid_records(session.working, QualityRules(), report.mapping)
    if invalid.empty:
        st.success("No rows have confirmed errors.", icon=":material/check_circle:")
        return None
    st.caption(f"{len(invalid):,} row(s) have at least one Error. Untick any row you want to keep. You could also "
               "fix individual values with 'Edit a value' instead of excluding the row.")
    table = invalid.copy()
    table.insert(0, "Exclude", True)
    edited = st.data_editor(table, hide_index=True, width="stretch", key=opt(f"invalid_editor_{session.version}"),
                            disabled=["Row", "Problems"],
                            column_config={"Problems": st.column_config.TextColumn(width="large")})
    chosen = edited[edited["Exclude"]]
    return dc.propose_exclude_rows(session, dict(zip(chosen["Row"].astype(int), chosen["Problems"])))


def options_edit(session, report):
    frame = session.working
    left, right = st.columns(2)
    with left:
        row_number = int(st.number_input("Row number", min_value=1, max_value=session.original_row_count, step=1,
                                         key=opt("edit_row")))
    with right:
        column = st.selectbox("Column", [str(c) for c in frame.columns], key=opt("edit_column"))
    label = row_number - 1
    if label not in frame.index:
        st.warning(f"Row {row_number} is not in the working copy (it was excluded).", icon=":material/warning:")
        return None
    current = display_value(frame.at[label, column])
    st.markdown(f"Current value: `{current}`" if current else "Current value: *(blank)*")
    if is_datetime64_any_dtype(frame[column]):
        st.caption("This column holds dates: type the new date as YYYY-MM-DD.")
    elif is_numeric_dtype(frame[column]):
        st.caption("This column holds numbers: type a number, or leave empty for a blank.")
    new_value = st.text_input("New value", value=current, key=opt(f"edit_value_{label}_{column}"))
    return dc.propose_manual_edit(session, row_number, column, new_value)


OPTION_PANELS = {
    dc.OP_TRIM: options_trim, dc.OP_CASE: options_case, dc.OP_CATEGORIES: options_categories,
    dc.OP_DATES: options_dates, dc.OP_NUMBERS: options_numbers, dc.OP_MISSING: options_missing,
    dc.OP_DUPLICATES: options_duplicates, dc.OP_INVALID: options_invalid, dc.OP_EDIT: options_edit,
}


def step_label(number: int, title: str) -> None:
    """A numbered workflow step heading (1 Choose, 2 Settings, 3 Preview)."""
    st.markdown(f'<p class="bp-card-title">{number}. {title}</p>', unsafe_allow_html=True)


def render_review(session: dc.CleaningSession, proposal: dc.Proposal) -> None:
    with ui.card("review"):
        step_label(3, "Preview and apply")
        st.markdown(ui.status_badge("info", "Preview · not applied yet", icon=":material/visibility:"))
        with st.container(key="bp_dc_review_facts"):
            first, second, third = st.columns([1.4, 1.2, 1])
            first.metric("Operation", proposal.title)
            second.metric("Column", proposal.column or ("Several" if proposal.new_columns else "Whole rows"))
            third.metric("Rows affected", f"{proposal.affected_count:,}")
        if proposal.description:
            st.markdown(proposal.description)
        for note in proposal.notes:
            st.caption(f":material/info: {note}")
        if proposal.risks:
            st.warning("**Risks and limitations**\n\n" + "\n".join(f"- {risk}" for risk in proposal.risks),
                       icon=":material/report:")
        if proposal.blocking:
            st.info(f"**Needs your decision:** {proposal.blocking}", icon=":material/touch_app:")
        if not proposal.examples.empty:
            label = "Rows that will be excluded" if proposal.excluded_rows else "Before and after"
            st.markdown(f"**{label}**")
            st.dataframe(proposal.examples, width="stretch", hide_index=not proposal.excluded_rows,
                         height=min(400, 38 + 35 * len(proposal.examples)))
            if proposal.affected_count > len(proposal.examples):
                st.caption(f"Showing the first {len(proposal.examples):,} of {proposal.affected_count:,}.")
        elif proposal.blocking is None:
            st.caption("Nothing would change with these settings.")

        if proposal.excluded_rows and proposal.can_apply:
            count = len(proposal.excluded_rows)
            st.warning(f"**Applying removes {count:,} {'row' if count == 1 else 'rows'} from the working copy.** "
                       "They stay in the original data and come back with **Undo last step** or "
                       "**Reset to original**.", icon=":material/warning:")
        confirmed = True
        if proposal.requires_confirmation and proposal.can_apply:
            confirmed = st.checkbox(f"I have reviewed the {proposal.affected_count:,} row(s) listed above and want to "
                                    "exclude them from the working copy.", key=opt("confirm"))
        with st.container(key="bp_dc_apply"):
            apply_column, cancel_column, _ = st.columns([1.3, 1, 3])
            apply_column.button("Apply change", type="primary", icon=":material/check:", key="apply_operation",
                                disabled=not (proposal.can_apply and confirmed), width="stretch",
                                on_click=apply_proposal, args=(session, proposal, confirmed))
            cancel_column.button("Cancel", icon=":material/close:", key="cancel_operation", width="stretch",
                                 on_click=clear_operation)


def get_plan(session: dc.CleaningSession, report, overrides: tuple) -> dc.FixPlan:
    """The recommended fixes for the current working copy, worked out once per version."""
    key = (session.signature, session.version, overrides)
    cached = st.session_state.get(PLAN_KEY)
    if cached is None or cached[0] != key:
        with st.spinner("Checking for fixes..."):
            cached = (key, dc.plan_fixes(session, report, QualityRules()))
        st.session_state[PLAN_KEY] = cached
    return cached[1]


def apply_safe(session: dc.CleaningSession, plan: dc.FixPlan) -> None:
    try:
        entry = session.apply_fixes(plan)
    except dc.CleaningError as error:
        st.session_state[FLASH_KEY] = ("error", str(error))
        return
    st.session_state[FLASH_KEY] = ("success", f"Applied {len(plan.safe)} safe fixes to {entry.rows_affected:,} "
                                              "row(s). **Undo last step** reverses all of them.")
    clear_operation()


def fixes_preview(plan: dc.FixPlan) -> tuple[pd.DataFrame, int]:
    """One before/after table for all safe fixes (visible changes only) and the
    number of values whose storage type changes but that look the same."""
    parts, type_only = [], 0
    for proposal in plan.safe:
        examples = proposal.examples
        visible = examples[examples["Before"] != examples["After"]]
        type_only += proposal.affected_count - len(visible) if proposal.new_columns else 0
        parts.append(visible.head(25).assign(Fix=proposal.title))
    if not parts:
        return pd.DataFrame(columns=["Fix", "Row", "Column", "Before", "After"]), type_only
    table = pd.concat(parts, ignore_index=True)
    return table[["Fix", "Row", "Column", "Before", "After"]], max(type_only, 0)


def render_recommended(session: dc.CleaningSession, plan: dc.FixPlan, report, tool_open: bool) -> None:
    """Automatic detection: safe fixes (one click, one undo) and items that need a decision."""
    with ui.card("dc_fixes"):
        head, score = st.columns([3, 1.4], vertical_alignment="center")
        with head:
            ui.card_label("Recommended fixes")
        after = plan.report_after.score.score
        if plan.safe and after != report.score.score:
            score.markdown(ui.status_badge("success", f"Quality {report.score.score:g} → {after:g}",
                                           ":material/trending_up:"))
        if plan.safe:
            groups: dict[str, list[dc.Proposal]] = {}
            for proposal in plan.safe:      # one row per kind of fix, e.g. all number conversions together
                groups.setdefault(proposal.operation, []).append(proposal)
            for operation, proposals in groups.items():
                columns = ", ".join(dict.fromkeys(c for p in proposals for c in dc.proposal_columns(p).split(", ")))
                rows = len({row for p in proposals for row in p.changed_rows})
                ui.list_row(f"dc_fix_{operation}", OPERATION_ICONS[operation], SAFE_FIX_TITLES[operation],
                            f"{columns} · {rows:,} row(s)")
            table, type_only = fixes_preview(plan)
            with st.expander(f"Preview before / after ({len(table):,} visible change(s))", icon=":material/visibility:"):
                st.dataframe(table, hide_index=True, width="stretch", height=min(420, 38 + 35 * max(len(table), 1)))
                if type_only:
                    st.caption(f"+ {type_only:,} value(s) that look the same but become real numbers or dates.")
            st.button(f"Apply all safe fixes ({plan.safe_rows:,} rows)", type="secondary" if tool_open else "primary",
                      icon=":material/auto_fix_high:", key="apply_safe_fixes", width="stretch",
                      on_click=apply_safe, args=(session, plan),
                      help="Trims spaces, unifies capitals and clearly equal names, and converts numbers and dates "
                           "that can only be read one way. Never removes rows or fills blanks. One undo reverses it.")
        else:
            st.markdown(ui.status_badge("success", "No safe fixes needed"))

    if plan.review:
        current = st.session_state.get(OP_KEY)
        grouped: dict[str, list[dc.ReviewItem]] = {}
        for item in plan.review:
            grouped.setdefault(item.operation, []).append(item)
        with ui.card("dc_suggested"):
            ui.card_label("Needs your decision")
            for op, items in grouped.items():
                action = ui.list_row(f"dc_suggest_{op}", OPERATION_ICONS[op], items[0].title,
                                     " · ".join(item.detail for item in items))
                action.button("Review", icon=":material/chevron_right:", type="tertiary", key=f"open_{op}",
                              disabled=current == op, on_click=open_tool, args=(op,),
                              help=f"Open {dc.OPERATION_TITLES[op]}")


def render_tools(session: dc.CleaningSession, report, plan: dc.FixPlan) -> None:
    review_ops = {item.operation for item in plan.review}
    operation = st.session_state.get(OP_KEY)
    proposal = None
    with st.expander("All cleaning tools", icon=":material/build:", expanded=operation is not None):
        with ui.card("choose_tool"):
            step_label(1, "Choose a cleaning tool")
            operation = st.selectbox(
                "Cleaning tool", list(dc.OPERATION_TITLES), index=None, placeholder="Choose a tool...", key=OP_KEY,
                label_visibility="collapsed",
                format_func=lambda op: dc.OPERATION_TITLES[op] + ("  ·  suggested" if op in review_ops else ""))
            if operation is None:
                return
            st.caption(f"{OPERATION_ICONS[operation]} {OPERATION_HELP[operation]} "
                       f"**{OPERATION_EFFECT[operation]}**.")
            step_label(2, "Settings")
            proposal = OPTION_PANELS[operation](session, report)
            if proposal is None:
                st.button("Cancel", icon=":material/close:", key="cancel_operation", on_click=clear_operation)
        if proposal is not None:
            render_review(session, proposal)


def render_action_bar(session: dc.CleaningSession, plan: dc.FixPlan) -> None:
    # One filled button per view: an open tool's Apply, else Apply all safe fixes, else saving.
    secondary = st.session_state.get(OP_KEY) is not None or bool(plan.safe)
    with st.container(key="bp_dc_actions"):
        approve_column, undo_column, reset_column = st.columns([1.4, 1, 1])
        approve_column.button("Create cleaned dataset", type="secondary" if secondary else "primary",
                              icon=":material/task_alt:", width="stretch",
                              key="approve", disabled=not session.has_unapproved_changes,
                              on_click=approve, args=(session,),
                              help="Save the working copy as the cleaned dataset used by every analysis page.")
        undo_column.button("Undo last step", icon=":material/undo:", disabled=not session.can_undo, width="stretch",
                           key="undo_step", on_click=undo_last, args=(session,))
        with reset_column.popover("Restore original", icon=":material/restart_alt:", width="stretch",
                                  disabled=session.steps_applied == 0):
            st.markdown(f"Undo all **{session.steps_applied}** step(s) and bring back every excluded row?")
            st.button("Yes, restore the original data", type="primary", key="confirm_reset",
                      on_click=reset_working, args=(session,))


# ---------------------------------------------------------------------------
loaded = st.session_state.get(session_keys.LOADED_DATA)
if loaded is None:
    render_empty_state()
    st.stop()

session = get_session(loaded)
role_overrides = st.session_state.get(session_keys.ROLE_OVERRIDES, {}).get(session.signature, {})
report = working_report(session.signature, session.version, tuple(sorted(role_overrides.items())), session.working)
cleaned = st.session_state.get(session_keys.CLEANED_DATA)
cleaned = cleaned if cleaned is not None and cleaned.signature == session.signature else None

overrides = tuple(sorted(role_overrides.items()))
plan = get_plan(session, report, overrides)

header_actions = ui.page_header("Data cleaning", "Fix problems in a copy of your data. The original never changes.",
                                eyebrow="Data", actions=1)
if header_actions.button("Check quality", icon=":material/fact_check:", width="stretch", key="go_quality"):
    st.switch_page("views/data_quality.py")

flash = st.session_state.pop(FLASH_KEY, None)
if flash:
    if flash[0] == "success":
        st.success(flash[1], icon=":material/check_circle:")
    else:
        st.error(flash[1], icon=":material/error:")

with st.container(key="bp_dc_kpis"):
    metric_columns = st.columns(5)
    with metric_columns[0]:
        ui.kpi_card("original_rows", "Original rows", f"{session.original_row_count:,}")
    with metric_columns[1]:
        ui.kpi_card("working_rows", "Working-copy rows", f"{len(session.working):,}")
    with metric_columns[2]:
        ui.kpi_card("excluded", "Excluded rows", f"{len(session.excluded):,}")
    with metric_columns[3]:
        ui.kpi_card("steps", "Steps applied", f"{session.steps_applied:,}")
    with metric_columns[4]:
        grade_kind = {"Excellent": "success", "Good": "info", "Fair": "warning", "Poor": "error"}[report.score.grade]
        ui.kpi_card("score", "Quality score", f"{report.score.score:g}",
                    help="Data-quality score of the working copy (see the Data quality page).",
                    status=(grade_kind, report.score.grade))

if cleaned is not None:
    status_column, link_column = st.columns([3, 1], vertical_alignment="center")
    status_column.success(f"**Cleaned dataset ready** · {cleaned.row_count:,} rows · used by every analysis page.",
                          icon=":material/task_alt:")
    if link_column.button("Open the dashboard", icon=":material/monitoring:", key="go_dashboard", width="stretch"):
        st.switch_page("views/dashboard.py")
    if session.has_unapproved_changes:
        st.warning("Newer changes are **not yet** in the cleaned dataset. Click **Create cleaned dataset** to "
                   "include them.", icon=":material/sync_problem:")

render_action_bar(session, plan)
tool_is_open = st.session_state.get(OP_KEY) is not None
render_recommended(session, plan, report, tool_is_open)
render_tools(session, report, plan)

working_tab, original_tab, excluded_tab, history_tab = st.tabs(
    ["Working copy", "Original data", f"Excluded rows ({len(session.excluded):,})", "Cleaning history"])
with working_tab:
    st.dataframe(dc.preview_table(session.working), width="stretch", height=420)
    st.caption(f"First 100 of {len(session.working):,} rows · original row numbers")
with original_tab:
    st.dataframe(dc.preview_table(session.original), width="stretch", height=420)
    st.caption(f"First 100 of {session.original_row_count:,} rows · exactly as uploaded, never changed")
with excluded_tab:
    if session.excluded:
        st.dataframe(session.excluded_rows_table(), width="stretch")
    else:
        st.caption("No rows excluded.")
with history_tab:
    history = session.audit_table()
    if history.empty:
        st.caption("No cleaning steps yet.")
    else:
        st.dataframe(history.iloc[::-1], hide_index=True, width="stretch",
                     column_config={"Description": st.column_config.TextColumn(width="large")})
