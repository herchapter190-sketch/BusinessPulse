"""Safe, user-controlled data cleaning.

Three versions of the data are kept apart:
1. Original     – the data as loaded. Never changed (the session keeps its own copy).
2. Working copy – where approved cleaning steps are applied, one at a time.
3. Cleaned data – a snapshot of the working copy, created only when the user approves it.

Every operation works in two steps:
- `propose_...()` builds a `Proposal`: what would change, which rows, before/after
  examples and risks. Nothing is changed.
- `CleaningSession.apply(proposal)` applies it to the working copy, records it in
  the audit log and keeps the previous state so it can be undone.

Rows are never deleted. "Excluding" a row removes it from the working copy only;
it stays in the original and is listed, with the reason, in `session.excluded`.
Row labels always refer to the original row (0-based), so row numbers shown to
the user (label + 1) match the Upload preview at every step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

from config.quality_rules import ERROR, QualityRules
from tools.data_quality import (
    SLASH_STYLE, _normalize_category, analyze_quality, classify_date, number_core, parse_number,
    suggest_category_fixes,
)
from tools.file_loader import blank_mask, display_value

MAX_UNDO_STEPS = 25
MAX_EXAMPLE_ROWS = 200

OP_TRIM = "trim_spaces"
OP_CASE = "capitalization"
OP_CATEGORIES = "normalize_categories"
OP_DATES = "convert_dates"
OP_NUMBERS = "convert_numbers"
OP_MISSING = "missing_values"
OP_DUPLICATES = "exclude_duplicates"
OP_INVALID = "exclude_invalid"
OP_EDIT = "manual_edit"

OPERATION_TITLES = {
    OP_TRIM: "Trim spaces",
    OP_CASE: "Standardize capitalization",
    OP_CATEGORIES: "Normalize category names",
    OP_DATES: "Convert dates",
    OP_NUMBERS: "Convert numbers",
    OP_MISSING: "Handle missing values",
    OP_DUPLICATES: "Exclude duplicate rows",
    OP_INVALID: "Exclude invalid records",
    OP_EDIT: "Edit a value",
}
# Material icon names for each tool (shown by the Data Quality and Data Cleaning screens).
OPERATION_ICONS = {
    OP_TRIM: ":material/content_cut:", OP_CASE: ":material/text_fields:",
    OP_CATEGORIES: ":material/category:", OP_DATES: ":material/calendar_month:",
    OP_NUMBERS: ":material/123:", OP_MISSING: ":material/check_box_outline_blank:",
    OP_DUPLICATES: ":material/content_copy:", OP_INVALID: ":material/block:",
    OP_EDIT: ":material/edit:",
}

CASE_MOST_COMMON = "most_common"
CASE_TITLE = "title"
CASE_UPPER = "upper"
CASE_LOWER = "lower"

ORDER_DAY_FIRST = "day_first"
ORDER_MONTH_FIRST = "month_first"
ORDER_LEAVE = "leave"

DECIMAL_AUTO = "auto"
DECIMAL_POINT = "point"
DECIMAL_COMMA = "comma"

MISSING_FILL_VALUE = "fill_value"
MISSING_FILL_ZERO = "fill_zero"
MISSING_FILL_MEAN = "fill_mean"
MISSING_FILL_MEDIAN = "fill_median"
MISSING_FILL_UNKNOWN = "fill_unknown"
MISSING_EXCLUDE = "exclude_rows"


class CleaningError(Exception):
    """An operation can't be applied; the message explains why in plain language."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Proposal:
    operation: str
    column: str | None                  # main column, None for whole-row operations
    description: str                    # what will change, in plain language
    risks: list[str]
    changed_rows: list[int]             # row labels that change (or are excluded)
    examples: pd.DataFrame              # before/after (or rows to exclude), first rows only
    base_version: int                   # working-copy version this preview was made from
    new_columns: dict[str, pd.Series] = field(default_factory=dict)
    excluded_rows: dict[int, str] = field(default_factory=dict)   # label -> reason
    blocking: str | None = None         # why Apply isn't possible yet (a decision is needed)
    requires_confirmation: bool = False  # excluding rows needs an explicit "I have reviewed" tick
    notes: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return OPERATION_TITLES[self.operation]

    @property
    def affected_count(self) -> int:
        return len(self.changed_rows)

    @property
    def can_apply(self) -> bool:
        return self.blocking is None and self.affected_count > 0


@dataclass
class AuditEntry:
    timestamp: str
    operation: str
    column: str
    rows_affected: int
    description: str
    status: str = "Applied"             # Applied / Undone / Info


@dataclass
class _Step:
    entry: AuditEntry
    working_before: pd.DataFrame
    excluded_before: dict[int, str]


@dataclass
class CleanedData:
    data: pd.DataFrame
    file_name: str
    sheet_name: str | None
    signature: str
    created_at: str
    original_rows: int
    excluded_rows: dict[int, str]
    audit: list[AuditEntry]

    @property
    def row_count(self) -> int:
        return len(self.data)


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class CleaningSession:
    def __init__(self, original: pd.DataFrame, signature: str, file_name: str, sheet_name: str | None = None,
                 max_undo: int = MAX_UNDO_STEPS):
        # Our own copy: nothing done here can reach the caller's table.
        self._original = original.copy(deep=True).reset_index(drop=True)
        self.working = self._original.copy(deep=True)
        self.signature = signature
        self.file_name = file_name
        self.sheet_name = sheet_name
        self.max_undo = max_undo
        self.excluded: dict[int, str] = {}
        self.audit: list[AuditEntry] = []
        self.version = 0
        self.approved_version: int | None = None
        self._steps: list[_Step] = []

    # -- read-only views -----------------------------------------------------
    @property
    def original(self) -> pd.DataFrame:
        """A copy of the original data (so it can't be changed by accident)."""
        return self._original.copy(deep=True)

    @property
    def original_row_count(self) -> int:
        return len(self._original)

    @property
    def steps_applied(self) -> int:
        return len(self._steps)

    @property
    def can_undo(self) -> bool:
        return bool(self._steps)

    @property
    def has_unapproved_changes(self) -> bool:
        if self.approved_version is None:
            return bool(self._steps)
        return self.version != self.approved_version

    def excluded_rows_table(self) -> pd.DataFrame:
        labels = sorted(self.excluded)
        table = _display_frame(self._original.loc[labels])
        table.insert(0, "Reason excluded", [self.excluded[label] for label in labels])
        return table

    # -- changes ---------------------------------------------------------------
    def apply(self, proposal: Proposal, confirmed: bool = False) -> AuditEntry:
        if proposal.base_version != self.version:
            raise CleaningError("The working copy changed after this preview was made. Please review the "
                                "preview again before applying.")
        if proposal.blocking:
            raise CleaningError(proposal.blocking)
        if proposal.affected_count == 0:
            raise CleaningError("Nothing would change, so there is nothing to apply.")
        if proposal.requires_confirmation and not confirmed:
            raise CleaningError("Please confirm that you have reviewed the rows that will be excluded.")

        entry = AuditEntry(_now(), proposal.title, proposal_columns(proposal), proposal.affected_count,
                           proposal.description)
        step = _Step(entry, self.working, dict(self.excluded))
        self._change(proposal)
        self._record(step)
        return entry

    def apply_fixes(self, plan: "FixPlan") -> AuditEntry:
        """Apply every safe fix of a plan as ONE step, so a single Undo reverses all of them."""
        if plan.base_version != self.version:
            raise CleaningError("The working copy changed after these fixes were found. Please review them again.")
        if not plan.safe:
            raise CleaningError("There are no safe fixes to apply.")
        step = _Step(None, self.working, dict(self.excluded))
        rows: set[int] = set()
        for proposal in plan.safe:
            self._change(proposal)
            rows.update(proposal.changed_rows)
        columns = ", ".join(dict.fromkeys(c for p in plan.safe for c in proposal_columns(p).split(", ")))
        step.entry = AuditEntry(_now(), "Apply safe fixes", columns, len(rows),
                                f"{len(plan.safe)} safe fix(es): " + "; ".join(
                                    f"{p.title} ({proposal_columns(p)})" for p in plan.safe) + ".")
        self._record(step)
        return step.entry

    def _change(self, proposal: Proposal) -> None:
        updated = self.working.copy()
        for column, values in proposal.new_columns.items():
            updated[column] = values
        if proposal.excluded_rows:
            updated = updated.drop(index=list(proposal.excluded_rows))
            self.excluded.update(proposal.excluded_rows)
        self.working = updated

    def _record(self, step: "_Step") -> None:
        self._steps.append(step)
        del self._steps[:-self.max_undo]
        self.audit.append(step.entry)
        self.version += 1

    def undo(self) -> AuditEntry | None:
        if not self._steps:
            return None
        step = self._steps.pop()
        self.working = step.working_before
        self.excluded = step.excluded_before
        step.entry.status = "Undone"
        entry = AuditEntry(_now(), "Undo", step.entry.column, step.entry.rows_affected,
                           f"Undid '{step.entry.operation}' from {step.entry.timestamp}: {step.entry.description}",
                           "Info")
        self.audit.append(entry)
        self.version += 1
        return entry

    def reset(self) -> AuditEntry:
        for step in self._steps:
            step.entry.status = "Undone"
        changed_steps = len(self._steps)
        self.working = self._original.copy(deep=True)
        self.excluded = {}
        self._steps = []
        entry = AuditEntry(_now(), "Reset to original", "(all)", len(self._original),
                           f"Working copy reset to the original data; {changed_steps} applied step(s) discarded.",
                           "Info")
        self.audit.append(entry)
        self.version += 1
        return entry

    def approve(self) -> CleanedData:
        entry = AuditEntry(_now(), "Cleaned dataset created", "(all)", len(self.working),
                           f"Approved the working copy as the cleaned dataset: {len(self.working):,} rows "
                           f"({len(self.excluded):,} excluded) after {len(self._steps)} applied step(s).", "Info")
        self.audit.append(entry)
        self.approved_version = self.version
        return CleanedData(
            data=self.working.copy(deep=True), file_name=self.file_name, sheet_name=self.sheet_name,
            signature=self.signature, created_at=entry.timestamp, original_rows=len(self._original),
            excluded_rows=dict(self.excluded), audit=[AuditEntry(**vars(e)) for e in self.audit],
        )

    def audit_table(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "Time": entry.timestamp, "Operation": entry.operation, "Column": entry.column,
            "Rows affected": entry.rows_affected, "Description": entry.description, "Status": entry.status,
        } for entry in self.audit], columns=["Time", "Operation", "Column", "Rows affected", "Description", "Status"])


def proposal_columns(proposal: Proposal) -> str:
    return proposal.column or ", ".join(proposal.new_columns) or "(whole rows)"


def dataset_for_analysis(loaded_data: pd.DataFrame, cleaned: CleanedData | None,
                         signature: str) -> tuple[pd.DataFrame, str]:
    """The table later stages should analyse: the approved cleaned data for this
    file if there is one, otherwise the original. Returns (table, "cleaned"/"original")."""
    if cleaned is not None and cleaned.signature == signature:
        return cleaned.data, "cleaned"
    return loaded_data, "original"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def _shown(value) -> str:
    text = display_value(value)
    if text == "":
        return "(blank)"
    if text != text.strip() or "  " in text:
        return f'"{text}"'
    return text


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    table = frame.copy()
    for column in table.columns:
        table[column] = table[column].map(display_value).astype(str)
    table.index = pd.Index([int(label) + 1 for label in frame.index], name="Row")
    return table


def preview_table(frame: pd.DataFrame, rows: int = 100) -> pd.DataFrame:
    """First rows of a table for display, numbered by original row (like the Upload preview)."""
    return _display_frame(frame.head(rows))


def _before_after(changes: list[tuple[int, str, object, object]]) -> pd.DataFrame:
    """Before/after examples. Changes the user can actually see (e.g. 19/02/2025 →
    2025-02-19) come first; changes of type only (text "12" → number 12) come after."""
    return _examples_and_visible(changes)[0]


def _examples_and_visible(changes: list[tuple[int, str, object, object]]) -> tuple[pd.DataFrame, int]:
    """The before/after examples and how many changes are visible on screen (each
    value is formatted once: on large columns formatting is the slow part)."""
    shown = [(label, column, _shown(before), _shown(after)) for label, column, before, after in changes]
    visible = sum(before != after for _, _, before, after in shown)
    shown.sort(key=lambda item: item[2] == item[3])  # stable: keeps row order within each group
    rows = [{"Row": label + 1, "Column": column, "Before": before, "After": after}
            for label, column, before, after in shown[:MAX_EXAMPLE_ROWS]]
    return pd.DataFrame(rows, columns=["Row", "Column", "Before", "After"]), visible


def _exclusion_examples(frame: pd.DataFrame, reasons: dict[int, str]) -> pd.DataFrame:
    labels = list(reasons)[:MAX_EXAMPLE_ROWS]
    table = _display_frame(frame.loc[labels])
    table.insert(0, "Reason", [reasons[label] for label in labels])
    return table


def _is_text(value) -> bool:
    return isinstance(value, str)


def _replace_core(value: str, new_core: str) -> str:
    """Replace the text of a value but keep any spaces around it."""
    core = value.strip()
    start = value.find(core)
    return value[:start] + new_core + value[start + len(core):]


def _blank_labels(frame: pd.DataFrame, column: str) -> list[int]:
    blanks = blank_mask(frame[[column]])[column]
    return [int(label) for label in blanks.index[blanks.to_numpy()]]


def _column_proposal(session: CleaningSession, operation: str, column: str, new_values: pd.Series,
                     description: str, risks: list[str], notes: list[str] | None = None,
                     blocking: str | None = None) -> Proposal:
    old_values = session.working[column]
    changes = []
    for label, before, after in zip(old_values.index, old_values.to_numpy(dtype=object),
                                    new_values.to_numpy(dtype=object)):
        if not _same(before, after):
            changes.append((int(label), column, before, after))
    notes = list(notes or [])
    retyped = not changes and _becomes_typed(old_values, new_values)
    if retyped:
        # The values already are numbers or dates, but the column is stored as mixed
        # "object" values (common in Excel files), so it isn't treated as numbers or
        # dates yet. Storing it with a real type is the change; no value changes.
        filled = ~blank_mask(old_values.to_frame())[column]
        changes = [(int(label), column, before, before)
                   for label, before in zip(old_values.index[filled.to_numpy()],
                                            old_values[filled].to_numpy(dtype=object))]
        kind = "numbers" if is_numeric_dtype(new_values) else "dates"
        notes.append(f"The values already are {kind}, but the column is stored as mixed values. It will be stored "
                     f"as {kind}; no value changes.")
    examples, visible = _examples_and_visible(changes)
    type_only = len(changes) - visible
    if type_only and not retyped:
        notes.append(f"{type_only:,} of the {len(changes):,} changed value(s) look the same on screen: they "
                     "change from text to a real number or date, which is what makes calculations possible.")
    return Proposal(operation, column, description, risks, [label for label, *_ in changes],
                    examples, session.version, {column: new_values}, blocking=blocking, notes=notes)


def _with_values(old: pd.Series, replacements: dict[int, object]) -> pd.Series:
    """A copy of `old` (as object values) with some values replaced, by row label.
    Built in one go: setting thousands of cells one by one is slow."""
    values = old.to_numpy(dtype=object, copy=True)
    position = {label: index for index, label in enumerate(old.index)}
    for label, value in replacements.items():
        values[position[label]] = value
    return pd.Series(values, index=old.index, name=old.name, dtype=object)


def _is_typed(values: pd.Series) -> bool:
    return (is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values)) or is_datetime64_any_dtype(values)


def _becomes_typed(old: pd.Series, new: pd.Series) -> bool:
    """True when a column stored as mixed values becomes a real number or date column."""
    return not _is_typed(old) and _is_typed(new) and bool((~blank_mask(old.to_frame())[old.name]).any())


def _same(before, after) -> bool:
    before_blank = before is None or (isinstance(before, float) and np.isnan(before)) or before is pd.NaT
    after_blank = after is None or (isinstance(after, float) and np.isnan(after)) or after is pd.NaT
    if before_blank or after_blank:
        # "" and NaN/NaT are both blank: changing one to the other is not a visible change.
        return (before_blank or before == "") and (after_blank or after == "")
    if type(before) is not type(after) and not (isinstance(before, (int, float)) and isinstance(after, (int, float))):
        return False
    return before == after


# ---------------------------------------------------------------------------
# 1. Trim spaces
# ---------------------------------------------------------------------------
def text_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns if frame[column].map(_is_text).any()]


def propose_trim(session: CleaningSession, columns: list[str], collapse_inner_spaces: bool = False) -> Proposal:
    def tidy(value):
        if not _is_text(value):
            return value
        text = value.strip()
        return re.sub(r" {2,}", " ", text) if collapse_inner_spaces else text

    changes, new_columns = [], {}
    for column in columns:
        old = session.working[column]
        new = old.map(tidy)
        column_changes = [(int(label), column, before, after)
                          for label, before, after in zip(old.index, old.to_numpy(dtype=object),
                                                          new.to_numpy(dtype=object))
                          if _is_text(before) and before != after]
        if column_changes:
            new_columns[column] = new
            changes.extend(column_changes)

    rows = sorted({label for label, *_ in changes})
    changed_columns = list(new_columns) or columns
    where = (f"'{changed_columns[0]}'" if len(changed_columns) == 1
             else f"{len(changed_columns)} columns ({', '.join(changed_columns)})")
    description = (f"Remove spaces at the start and end of values in {where}"
                   + (" and replace double spaces inside values with a single space." if collapse_inner_spaces
                      else "."))
    risks = ["Low risk: only spaces are removed; letters and numbers are not changed."]
    if collapse_inner_spaces:
        risks.append("Double spaces inside a value are also reduced to one space.")
    return Proposal(OP_TRIM, changed_columns[0] if len(changed_columns) == 1 else None, description, risks, rows,
                    _before_after(changes), session.version, new_columns,
                    notes=[f"{len(changes):,} cell(s) in {len(new_columns)} column(s) will change."])


# ---------------------------------------------------------------------------
# 2. Capitalization
# ---------------------------------------------------------------------------
def propose_capitalization(session: CleaningSession, column: str, style: str = CASE_MOST_COMMON) -> Proposal:
    old = session.working[column]
    texts = [value.strip() for value in old.tolist() if _is_text(value) and value.strip()]
    if style == CASE_MOST_COMMON:
        case_fixes, _ = suggest_category_fixes(texts, QualityRules())

        def convert(core: str) -> str:
            return case_fixes.get(core, core)
        how = "the most common spelling of each name"
        risks = ["Low risk: each value is changed only to a spelling that already appears in this column."]
    else:
        convert = {CASE_TITLE: str.title, CASE_UPPER: str.upper, CASE_LOWER: str.lower}[style]
        how = {CASE_TITLE: "Title Case", CASE_UPPER: "UPPER CASE", CASE_LOWER: "lower case"}[style]
        risks = ["Every text value in the column is changed. Abbreviations and brand names can be affected: "
                 "with Title Case, 'USB-C hub' becomes 'Usb-C Hub'."]

    new = old.map(lambda value: _replace_core(value, convert(value.strip())) if _is_text(value) and value.strip()
                  else value)
    return _column_proposal(session, OP_CASE, column, new,
                            f"Change the capitalization of values in '{column}' to {how}.", risks)


# ---------------------------------------------------------------------------
# 3. Category names
# ---------------------------------------------------------------------------
def suggest_category_replacements(session: CleaningSession, column: str,
                                  rules: QualityRules | None = None) -> pd.DataFrame:
    """Proposed spelling fixes for a category column, for the user to review."""
    rules = rules or QualityRules()
    values = [value.strip() for value in session.working[column].tolist() if _is_text(value) and value.strip()]
    values = [value for value in values if value.casefold() not in rules.placeholder_values]
    case_fixes, name_fixes = suggest_category_fixes(values, rules)
    counts = pd.Series(values).value_counts() if values else pd.Series(dtype=int)
    rows = []
    for current, target in name_fixes.items():
        rows.append({"Current value": current, "Rows": int(counts[current]), "Replace with": target,
                     "Reason": "Looks like a variant or misspelling of a more common name"})
    for current, target in case_fixes.items():
        if current not in name_fixes:
            rows.append({"Current value": current, "Rows": int(counts[current]), "Replace with": target,
                         "Reason": "Same name with different capital letters"})
    return pd.DataFrame(rows, columns=["Current value", "Rows", "Replace with", "Reason"])


def propose_category_normalization(session: CleaningSession, column: str,
                                   replacements: dict[str, str]) -> Proposal:
    replacements = {current: target.strip() for current, target in replacements.items()
                    if target and target.strip() and target.strip() != current}
    old = session.working[column]
    new = old.map(lambda value: replacements.get(value.strip(), value) if _is_text(value) else value)
    blocking = None if replacements else "Select at least one replacement to apply."
    listing = "; ".join(f"'{current}' → '{target}'" for current, target in list(replacements.items())[:8])
    more = f" and {len(replacements) - 8} more" if len(replacements) > 8 else ""
    return _column_proposal(
        session, OP_CATEGORIES, column, new,
        f"Replace {len(replacements)} category name(s) in '{column}': {listing}{more}.",
        ["Check each replacement: if two names really are different categories, untick that row.",
         "Spaces around the replaced values are removed at the same time."],
        blocking=blocking)


# ---------------------------------------------------------------------------
# 4 & 5. Dates
# ---------------------------------------------------------------------------
@dataclass
class DateEvidence:
    slash_dates: int = 0
    day_first_only: int = 0     # e.g. 25/12/2025 – can only be day first
    month_first_only: int = 0   # e.g. 12/25/2025 – can only be month first
    ambiguous: int = 0          # e.g. 03/05/2025 – could be either
    day_first_example: str = ""
    month_first_example: str = ""
    ambiguous_example: str = ""

    @property
    def needs_decision(self) -> bool:
        return self.ambiguous > 0

    @property
    def recommendation(self) -> str | None:
        if self.day_first_only and not self.month_first_only:
            return ORDER_DAY_FIRST
        if self.month_first_only and not self.day_first_only:
            return ORDER_MONTH_FIRST
        return None


def date_order_evidence(values: pd.Series) -> DateEvidence:
    evidence = DateEvidence()
    for value in values.tolist():   # plain Python values: iterating an Arrow-backed column is slow
        style, _, parts = classify_date(value)
        if style != SLASH_STYLE:
            continue
        first, second, _ = parts
        evidence.slash_dates += 1
        text = str(value).strip()
        if first > 12 >= second:
            evidence.day_first_only += 1
            evidence.day_first_example = evidence.day_first_example or text
        elif second > 12 >= first:
            evidence.month_first_only += 1
            evidence.month_first_example = evidence.month_first_example or text
        elif first <= 12 and second <= 12:
            evidence.ambiguous += 1
            evidence.ambiguous_example = evidence.ambiguous_example or text
    return evidence


def propose_date_conversion(session: CleaningSession, column: str, ambiguous_order: str | None = None) -> Proposal:
    """Convert date text in many formats to real dates. Dates like 03/05/2025 are
    only converted after the user says how to read them (`ambiguous_order`)."""
    old = session.working[column]
    evidence = date_order_evidence(old)
    blocking = None
    if evidence.needs_decision and ambiguous_order is None:
        blocking = (f"{evidence.ambiguous:,} date(s) such as {evidence.ambiguous_example} could be read as "
                    "day-first or month-first. Choose how to read them before applying.")

    converted: dict[int, pd.Timestamp] = {}
    single_reading, ambiguous_done, ambiguous_left, impossible, unreadable = 0, 0, [], [], []
    for label, value in zip(old.index, old.tolist()):
        if not _is_text(value) or not value.strip():
            continue
        style, parsed, parts = classify_date(value)
        if style == SLASH_STYLE:
            first, second, year = parts
            if first > 12 and second > 12:
                impossible.append(value)
                continue
            if first > 12 or second > 12:
                day, month = (first, second) if first > 12 else (second, first)
                single_reading += 1
            elif ambiguous_order in (ORDER_DAY_FIRST, ORDER_MONTH_FIRST):
                day, month = (first, second) if ambiguous_order == ORDER_DAY_FIRST else (second, first)
                ambiguous_done += 1
            else:
                ambiguous_left.append(value)
                continue
            try:
                parsed = datetime(year, month, day)
            except ValueError:
                impossible.append(value)
                continue
        elif style is None:
            unreadable.append(value)
            continue
        elif parsed is None:
            impossible.append(value)
            continue
        converted[int(label)] = pd.Timestamp(parsed)

    new = _with_values(old, converted)
    leftovers = len(ambiguous_left) + len(impossible) + len(unreadable)
    # Values that are neither text nor dates (e.g. plain numbers) would be read as
    # 1970 dates by pandas, so the column only becomes a date column without them.
    others = [value for value in new.tolist() if not _is_text(value)
              and not isinstance(value, (datetime, pd.Timestamp)) and not _same(value, None)]
    if leftovers == 0 and not others:
        new = pd.to_datetime(new.where(~blank_mask(old.to_frame())[column], pd.NaT))

    notes = [f"{len(converted):,} value(s) will be converted to dates."]
    risks = []
    if single_reading:
        notes.append(f"{single_reading:,} date(s) written with slashes can only be read one way "
                     f"(e.g. {evidence.day_first_example or evidence.month_first_example}) and are converted that way.")
    if ambiguous_done:
        order = "day first (DD/MM/YYYY)" if ambiguous_order == ORDER_DAY_FIRST else "month first (MM/DD/YYYY)"
        risks.append(f"{ambiguous_done:,} ambiguous date(s) are read as {order}, as you chose. If any of them were "
                     "written the other way round, they will be wrong.")
        if evidence.day_first_only and evidence.month_first_only:
            risks.append("This column contains dates that can only be day-first AND dates that can only be "
                         "month-first, so it mixes both conventions. Ambiguous dates may be wrong either way.")
    if ambiguous_left:
        notes.append(f"{len(ambiguous_left):,} ambiguous date(s) are left unchanged, as you chose.")
    if impossible:
        risks.append(f"{len(impossible):,} impossible date(s) (e.g. {impossible[0]}) can't be converted and are left "
                     "unchanged. You can fix them with 'Edit a value' or exclude them.")
    if unreadable:
        risks.append(f"{len(unreadable):,} value(s) that are not dates (e.g. {unreadable[0]}) are left unchanged.")
    if leftovers:
        risks.append("Because some values are left unchanged, this column will hold a mix of dates and text "
                     "until they are fixed.")
    if not risks:
        risks.append("Low risk: every date can be read in exactly one way.")
    return _column_proposal(session, OP_DATES, column, new,
                            f"Convert text dates in '{column}' to real date values (shown as YYYY-MM-DD).",
                            risks, notes, blocking)


# ---------------------------------------------------------------------------
# 6 & 7. Numbers
# ---------------------------------------------------------------------------
_COMMA_DECIMAL = re.compile(r"^[+-]?\d+,\d{1,2}$|^[+-]?\d{1,3}(\.\d{3})+,\d+$|^[+-]?\d{1,3}(\.\d{3}){2,}$")
_POINT_DECIMAL = re.compile(r"^[+-]?\d*\.(\d{1,2}|\d{4,})$|^[+-]?\d{1,3}(,\d{3})+\.\d+$|^[+-]?\d{1,3}(,\d{3}){2,}$")
_AMBIGUOUS_SEPARATOR = re.compile(r"^[+-]?\d{1,3}[.,]\d{3}$")


@dataclass
class DecimalEvidence:
    point: int = 0
    comma: int = 0
    ambiguous: int = 0
    point_example: str = ""
    comma_example: str = ""
    ambiguous_example: str = ""

    @property
    def detected(self) -> str | None:
        """The convention that can be identified reliably, or None if the column
        contains evidence for both (then the user must choose)."""
        if self.comma and self.point:
            return None
        return DECIMAL_COMMA if self.comma else DECIMAL_POINT


def decimal_evidence(values: pd.Series) -> DecimalEvidence:
    evidence = DecimalEvidence()
    for value in values.tolist():
        if not _is_text(value):
            continue
        core, _, _ = number_core(value)
        if _AMBIGUOUS_SEPARATOR.match(core):
            evidence.ambiguous += 1
            evidence.ambiguous_example = evidence.ambiguous_example or value.strip()
        elif _COMMA_DECIMAL.match(core):
            evidence.comma += 1
            evidence.comma_example = evidence.comma_example or value.strip()
        elif _POINT_DECIMAL.match(core):
            evidence.point += 1
            evidence.point_example = evidence.point_example or value.strip()
    return evidence


def parse_amount(value, decimal_comma: bool = False) -> float | None:
    """Read a number written with currency symbols, thousands separators,
    brackets or %, using a decimal point (default) or a decimal comma."""
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    if not _is_text(value):
        return None
    if not decimal_comma:
        return parse_number(value)[1]
    core, negative, _ = number_core(value)
    if re.fullmatch(r"[+-]?\d+(,\d+)?", core):
        number = float(core.replace(",", "."))
    elif re.fullmatch(r"[+-]?\d{1,3}(\.\d{3})+(,\d+)?", core):
        number = float(core.replace(".", "").replace(",", "."))
    else:
        return None
    return -abs(number) if negative else number


def propose_number_conversion(session: CleaningSession, column: str, convention: str = DECIMAL_AUTO,
                              placeholders_to_blank: bool = False, rules: QualityRules | None = None) -> Proposal:
    rules = rules or QualityRules()
    old = session.working[column]
    evidence = decimal_evidence(old)
    blocking = None
    if convention == DECIMAL_AUTO:
        chosen = evidence.detected
        if chosen is None:
            blocking = (f"This column has numbers with a decimal point (e.g. {evidence.point_example}) and numbers "
                        f"with a decimal comma (e.g. {evidence.comma_example}). Choose which convention it uses.")
            chosen = DECIMAL_POINT
    else:
        chosen = convention
    decimal_comma = chosen == DECIMAL_COMMA

    replacements: dict[int, object] = {}
    converted, to_blank, left = 0, 0, []
    for label, value in zip(old.index, old.tolist()):
        if not _is_text(value) or not value.strip():
            continue
        number = parse_amount(value, decimal_comma)
        if number is not None:
            replacements[label] = number
            converted += 1
        elif placeholders_to_blank and value.strip().casefold() in rules.placeholder_values:
            replacements[label] = np.nan
            to_blank += 1
        else:
            left.append(value.strip())
    new = _with_values(old, replacements)
    if not left:
        new = pd.to_numeric(new.where(~blank_mask(old.to_frame())[column], np.nan))

    convention_text = "decimal comma (12,50)" if decimal_comma else "decimal point (12.50)"
    notes = [f"{converted:,} value(s) will be converted to numbers using the {convention_text} convention."]
    if convention == DECIMAL_AUTO and blocking is None and (evidence.point or evidence.comma):
        notes.append(f"Detected the {convention_text} convention from values like "
                     f"{evidence.comma_example if decimal_comma else evidence.point_example}.")
    risks = ["Currency symbols, codes and thousands separators are removed; the amount itself is kept. "
             "Values in brackets, like (12.00), become negative numbers."]
    if evidence.ambiguous:
        # "1,234": comma → decimal under the comma convention, thousands under the point convention.
        # "1.234": the other way round.
        comma_separator = "," in evidence.ambiguous_example
        reads_as_decimal = comma_separator == decimal_comma
        meaning = "a decimal (1.234)" if reads_as_decimal else "thousands (1234)"
        risks.append(f"{evidence.ambiguous:,} value(s) such as {evidence.ambiguous_example} could mean thousands or a "
                     f"decimal. With the chosen convention they are read as {meaning}.")
    if to_blank:
        notes.append(f"{to_blank:,} placeholder value(s) such as N/A will become blank (missing).")
    if left:
        risks.append(f"{len(left):,} value(s) that are not numbers (e.g. {left[0]}) are left unchanged, so the column "
                     "will mix numbers and text until they are fixed. Tick 'Treat placeholders as blank' to turn "
                     "values like N/A into blanks, or fix them with 'Edit a value'.")
    return _column_proposal(session, OP_NUMBERS, column, new,
                            f"Convert values in '{column}' to plain numbers ({convention_text}).",
                            risks, notes, blocking)


# ---------------------------------------------------------------------------
# 8. Missing values
# ---------------------------------------------------------------------------
def propose_missing_values(session: CleaningSession, column: str, method: str,
                           fill_value: str | None = None) -> Proposal:
    frame = session.working
    old = frame[column]
    rows = _blank_labels(frame, column)
    numeric = is_numeric_dtype(old) and not pd.api.types.is_bool_dtype(old)
    is_dates = is_datetime64_any_dtype(old)
    blocking = None if rows else "This column has no missing values."

    if method == MISSING_EXCLUDE:
        reasons = {label: f"Missing value in '{column}'" for label in rows}
        return Proposal(OP_MISSING, column,
                        f"Exclude {len(rows):,} row(s) where '{column}' is blank from the working copy.",
                        ["Excluded rows are left out of all analysis. They are not deleted: they stay in the "
                         "original data and can be restored with Undo or Reset."],
                        rows, _exclusion_examples(frame, reasons), session.version, excluded_rows=reasons,
                        blocking=blocking, requires_confirmation=True)

    if method in (MISSING_FILL_ZERO, MISSING_FILL_MEAN, MISSING_FILL_MEDIAN) and not numeric:
        blocking = blocking or ("This column is not stored as numbers yet. Use 'Convert numbers' on it first, "
                                "then fill missing values.")
        value, text = None, ""
    elif method == MISSING_FILL_ZERO:
        value, text = 0.0, "0"
    elif method in (MISSING_FILL_MEAN, MISSING_FILL_MEDIAN):
        numbers = pd.to_numeric(old, errors="coerce").dropna()
        if numbers.empty:
            blocking = blocking or "There are no numbers in this column to calculate from."
            value, text = None, ""
        else:
            value = round(float(numbers.mean() if method == MISSING_FILL_MEAN else numbers.median()), 2)
            text = f"{value:g} (the {'average' if method == MISSING_FILL_MEAN else 'median'})"
    elif method == MISSING_FILL_UNKNOWN:
        value, text = "Unknown", "'Unknown'"
        if numeric or is_dates:
            blocking = blocking or "'Unknown' can't be put in a number or date column. Choose another option."
    elif method == MISSING_FILL_VALUE:
        raw = (fill_value or "").strip()
        value, text = raw, f"'{raw}'"
        if not raw:
            blocking = blocking or "Type the value to fill in."
        elif numeric:
            value = parse_amount(raw)
            if value is None:
                blocking = blocking or f"'{raw}' is not a number, but this column holds numbers."
        elif is_dates:
            style, parsed, _ = classify_date(raw)
            if style in (None, SLASH_STYLE) or parsed is None:
                blocking = blocking or "Type the date as YYYY-MM-DD, for example 2025-03-31."
                value = None
            else:
                value = pd.Timestamp(parsed)
    else:
        raise ValueError(f"Unknown method: {method}")

    new = old.copy()
    if value is not None and rows:
        if not (numeric or is_dates):
            new = new.astype(object)
        new.loc[rows] = value
    risks = {
        MISSING_FILL_ZERO: "Zeros are counted as real values: totals stay the same, but averages go down.",
        MISSING_FILL_MEAN: "The average is an estimate, not the real value. It makes the column look more "
                           "complete than it is.",
        MISSING_FILL_MEDIAN: "The median is an estimate, not the real value. It makes the column look more "
                             "complete than it is.",
        MISSING_FILL_UNKNOWN: "'Unknown' will appear as its own group in charts and breakdowns.",
        MISSING_FILL_VALUE: "The same value is used for every blank cell. Make sure it is correct for all of them.",
    }[method]
    return _column_proposal(session, OP_MISSING, column, new,
                            f"Fill {len(rows):,} blank cell(s) in '{column}' with {text}.", [risks],
                            blocking=blocking)


# ---------------------------------------------------------------------------
# 9. Duplicate rows
# ---------------------------------------------------------------------------
def find_duplicate_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Every row that is an exact copy of an earlier row: Row and 'Same as row'."""
    keys = pd.util.hash_pandas_object(frame.astype(str), index=False).to_numpy()
    first_seen: dict[int, int] = {}
    pairs = []
    for label, key in zip(frame.index, keys):
        if key in first_seen:
            pairs.append({"Row": int(label) + 1, "Same as row": first_seen[key] + 1})
        else:
            first_seen[key] = int(label)
    return pd.DataFrame(pairs, columns=["Row", "Same as row"])


def propose_exclude_duplicates(session: CleaningSession, row_numbers: list[int] | None = None) -> Proposal:
    """Exclude duplicate rows (by row number); the first copy of each is kept."""
    duplicates = find_duplicate_rows(session.working)
    original_of = dict(zip(duplicates["Row"], duplicates["Same as row"]))
    chosen = list(original_of) if row_numbers is None else [n for n in row_numbers if n in original_of]
    reasons = {number - 1: f"Duplicate of row {original_of[number]}" for number in chosen}
    blocking = None if reasons else ("No duplicate rows found." if duplicates.empty
                                     else "Select at least one duplicate row to exclude.")
    return Proposal(OP_DUPLICATES, None,
                    f"Exclude {len(reasons):,} duplicate row(s) from the working copy. The first copy of each is kept.",
                    ["Only exact copies (every value identical) are listed. Two genuine identical orders would also "
                     "look like this, so check them if your data can contain such rows.",
                     "Rows are not deleted: they stay in the original data and can be restored with Undo or Reset."],
                    list(reasons), _exclusion_examples(session.working, reasons), session.version,
                    excluded_rows=reasons, blocking=blocking, requires_confirmation=True)


# ---------------------------------------------------------------------------
# 10. Invalid records
# ---------------------------------------------------------------------------
def find_invalid_records(frame: pd.DataFrame, rules: QualityRules | None = None,
                         mapping=None) -> pd.DataFrame:
    """Rows with at least one Error in the data-quality checks, with the reasons."""
    report = analyze_quality(frame, mapping, rules)
    reasons: dict[int, list[str]] = {}
    for issue in report.issues:
        if issue.severity != ERROR:
            continue
        for position in issue.rows:
            label = int(frame.index[position])
            text = f"{issue.problem}" + (f" ({issue.column})" if issue.column else "")
            reasons.setdefault(label, []).append(text)
    return pd.DataFrame([{"Row": label + 1, "Problems": "; ".join(problems)}
                         for label, problems in sorted(reasons.items())], columns=["Row", "Problems"])


def propose_exclude_rows(session: CleaningSession, reasons_by_row_number: dict[int, str],
                         operation: str = OP_INVALID) -> Proposal:
    """Exclude specific rows (by row number), each with the reason shown to the user."""
    present = set(int(label) for label in session.working.index)
    reasons = {number - 1: reason for number, reason in reasons_by_row_number.items() if number - 1 in present}
    blocking = None if reasons else "Select at least one row to exclude."
    return Proposal(operation, None, f"Exclude {len(reasons):,} invalid record(s) from the working copy.",
                    ["Excluded rows are left out of all analysis, so totals will change.",
                     "Rows are not deleted: they stay in the original data and can be restored with Undo or Reset."],
                    sorted(reasons), _exclusion_examples(session.working, reasons), session.version,
                    excluded_rows=reasons, blocking=blocking, requires_confirmation=True)


# ---------------------------------------------------------------------------
# 11. Manual edit
# ---------------------------------------------------------------------------
def propose_manual_edit(session: CleaningSession, row_number: int, column: str, new_text: str) -> Proposal:
    frame = session.working
    label = row_number - 1
    if label not in frame.index:
        return Proposal(OP_EDIT, column, "", [], [], _before_after([]), session.version,
                        blocking=f"Row {row_number} is not in the working copy (it doesn't exist or was excluded).")
    old_column = frame[column]
    before = old_column.at[label]
    text = new_text if new_text is not None else ""
    blocking, value = None, text
    if is_numeric_dtype(old_column) and not pd.api.types.is_bool_dtype(old_column):
        value = np.nan if not text.strip() else parse_amount(text)
        if value is None:
            blocking = f"'{text}' is not a number, but this column holds numbers."
    elif is_datetime64_any_dtype(old_column):
        if not text.strip():
            value = pd.NaT
        else:
            style, parsed, _ = classify_date(text)
            if style in (None, SLASH_STYLE) or parsed is None:
                blocking = "Type the date as YYYY-MM-DD, for example 2025-03-31."
            else:
                value = pd.Timestamp(parsed)
    if blocking is None and _same(before, value):
        blocking = "The new value is the same as the current value."

    new = old_column.copy()
    if blocking is None:
        if not (is_numeric_dtype(new) or is_datetime64_any_dtype(new)):
            new = new.astype(object)
        new.at[label] = value
    shown_after = _shown(value) if blocking is None else ""
    return Proposal(OP_EDIT, column, f"Change row {row_number}, '{column}' from {_shown(before)} to {shown_after}.",
                    ["Manual edits are your own correction; make sure the new value is right."],
                    [label] if blocking is None else [],
                    _before_after([(label, column, before, value)] if blocking is None else []),
                    session.version, {column: new} if blocking is None else {}, blocking=blocking)


# ---------------------------------------------------------------------------
# 12. Recommended fixes: automatic detection, grouped into safe fixes (applied
#     together with one click) and items that need the user's decision.
# ---------------------------------------------------------------------------
@dataclass
class ReviewItem:
    """A problem that is never fixed automatically; `operation` is the tool that handles it."""
    operation: str
    title: str
    detail: str


@dataclass
class FixPlan:
    base_version: int                   # working-copy version the plan was made from
    safe: list[Proposal]                # in order; each was built on the result of the previous one
    review: list[ReviewItem]
    report_after: object                # data-quality report of the working copy after the safe fixes

    @property
    def safe_rows(self) -> int:
        return len({row for proposal in self.safe for row in proposal.changed_rows})


def _scratch_copy(session: CleaningSession) -> CleaningSession:
    """A throw-away session with the same working copy and version (nothing is copied twice)."""
    scratch = CleaningSession.__new__(CleaningSession)
    scratch.__dict__.update(session.__dict__)
    scratch.excluded, scratch.audit, scratch._steps = dict(session.excluded), [], []
    return scratch


def _equivalent_names(table: pd.DataFrame) -> tuple[dict[str, str], int]:
    """Split suggested replacements into clearly equivalent names (same letters and digits,
    only capitals, spaces or punctuation differ) and look-alikes that need a human."""
    safe, look_alikes = {}, 0
    for current, target in zip(table["Current value"], table["Replace with"]):
        if (_normalize_category(current) or current.casefold()) == (_normalize_category(target) or target.casefold()):
            safe[current] = target
        else:
            look_alikes += 1
    return safe, look_alikes


def plan_fixes(session: CleaningSession, report, rules: QualityRules | None = None) -> FixPlan:
    """Detect the problems in the working copy and group them.

    Safe fixes never remove rows, never fill blanks and never guess: they trim
    spaces, use the most common existing spelling of a name, merge names that
    differ only in capitals/spacing/punctuation, and convert numbers and dates that
    can be read in exactly one way. Everything else becomes a ReviewItem."""
    rules = rules or QualityRules()
    scratch = _scratch_copy(session)
    safe: list[Proposal] = []
    review: list[ReviewItem] = []

    def take(proposal: Proposal) -> None:
        if proposal.can_apply:
            scratch._change(proposal)
            scratch.version += 1
            safe.append(proposal)

    flagged = {check: {issue.column for issue in report.issues if issue.check == check and issue.column}
               for check in ("capitalization", "suspicious_category")}
    columns = [str(column) for column in session.working.columns]

    take(propose_trim(scratch, text_columns(scratch.working)))
    for column in columns:
        if column in flagged["capitalization"]:
            take(propose_capitalization(scratch, column, CASE_MOST_COMMON))
    for column in columns:
        if column in flagged["capitalization"] | flagged["suspicious_category"]:
            table = suggest_category_replacements(scratch, column, rules)
            if table.empty:
                continue
            equivalent, look_alikes = _equivalent_names(table)
            if equivalent:
                take(propose_category_normalization(scratch, column, equivalent))
            if look_alikes:
                review.append(ReviewItem(OP_CATEGORIES, "Similar names", f"{column}: {look_alikes} look-alike "
                                                                         "name(s) to check"))

    for profile in report.profiles:
        column = profile.column
        values = scratch.working[column]
        if profile.treated_as == "Number" and not _is_typed(values):
            evidence = decimal_evidence(values)
            proposal = propose_number_conversion(scratch, column, DECIMAL_AUTO, False, rules)
            if proposal.blocking:
                review.append(ReviewItem(OP_NUMBERS, "Number format", f"{column}: decimal point and comma mixed"))
                continue
            if evidence.ambiguous and not (evidence.point or evidence.comma):
                review.append(ReviewItem(OP_NUMBERS, "Number format",
                                         f"{column}: {evidence.ambiguous} value(s) like {evidence.ambiguous_example}"))
                continue
            take(proposal)
            left = [v for v in scratch.working[column] if _is_text(v) and v.strip()]
            if left:
                review.append(ReviewItem(OP_NUMBERS, "Text in a number column",
                                         f"{column}: {len(left)} value(s) like {left[0].strip()}"))
        elif profile.treated_as == "Date" and not is_datetime64_any_dtype(values):
            evidence = date_order_evidence(values)
            take(propose_date_conversion(scratch, column, ORDER_LEAVE if evidence.needs_decision else None))
            if evidence.needs_decision:
                review.append(ReviewItem(OP_DATES, "Ambiguous dates",
                                         f"{column}: {evidence.ambiguous} like {evidence.ambiguous_example}"))

    after = analyze_quality(scratch.working, report.mapping, rules)
    duplicates = len(find_duplicate_rows(scratch.working))
    if duplicates:
        review.append(ReviewItem(OP_DUPLICATES, "Duplicate rows", f"{duplicates:,} exact copies"))
    blanks = blank_mask(scratch.working).sum()
    blank_columns = [str(column) for column in scratch.working.columns if blanks[column]]
    if blank_columns:
        review.append(ReviewItem(OP_MISSING, "Missing values", f"{int(blanks.sum()):,} blank cell(s) in "
                                                               f"{len(blank_columns)} column(s)"))
    if after.invalid_rows:
        review.append(ReviewItem(OP_INVALID, "Rows with errors", f"{after.invalid_rows:,} row(s)"))
    return FixPlan(session.version, safe, review, after)
