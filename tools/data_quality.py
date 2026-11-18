"""Data-quality analysis: find and explain problems in a loaded table.

The analysis only *reads* the data. Nothing is cleaned, converted or changed.

Every problem is reported as a `QualityIssue` with a severity:
- Error   – a confirmed problem that will make calculations wrong or impossible
            (e.g. an impossible date, text in a required number column).
- Warning – something that is probably wrong but could be legitimate and needs
            a person to check it (e.g. a negative quantity may be a return).
- Info    – a note that does not affect the score.

Row positions in issues are 0-based positions in the table; the screen shows
them as row numbers starting at 1, the same numbering as the preview.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache

import numpy as np
import pandas as pd

from config.quality_rules import (
    CATEGORY_ROLES, DATE_ROLES, ERROR, FREE_TEXT_ROLES, INFO, NUMERIC_ROLES, ROLE_LABELS, WARNING, QualityRules,
)
from tools.column_mapping import ColumnMapping, recognize_columns
from tools.file_loader import blank_mask, display_value

SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

# How a column is treated during the checks.
KIND_NUMBER = "Number"
KIND_DATE = "Date"
KIND_CATEGORY = "Category"
KIND_TEXT = "Text"
KIND_EMPTY = "Empty"

MAX_EXAMPLES = 5


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class QualityIssue:
    check: str                 # short code, e.g. "missing_value"
    column: str | None         # None for whole-row checks such as duplicates
    problem: str               # short plain-language title
    severity: str              # Error / Warning / Info
    rows: list[int]            # 0-based row positions affected
    examples: list[str]
    explanation: str
    scope: str = "rows"        # "rows", or "column" for problems with a whole column

    @property
    def affected_count(self) -> int:
        return len(self.rows)


@dataclass
class ColumnProfile:
    column: str
    role: str | None
    treated_as: str
    filled: int
    missing: int


@dataclass
class QualityScore:
    score: float
    grade: str
    total_rows: int
    error_rows: int
    warning_rows: int          # rows with warnings but no errors
    clean_rows: int
    row_score: float
    empty_columns: int
    empty_column_penalty: float
    warning_row_weight: float


@dataclass
class QualityReport:
    row_count: int
    column_count: int
    mapping: ColumnMapping
    issues: list[QualityIssue]
    profiles: list[ColumnProfile]
    missing_cells: int
    columns_with_missing: int
    duplicate_rows: int
    score: QualityScore
    severity_counts: dict[str, int] = field(default_factory=dict)

    @property
    def invalid_rows(self) -> int:
        return self.score.error_rows

    @property
    def warning_rows(self) -> int:
        return self.score.warning_rows

    def find(self, check: str, column: str | None = None) -> list[QualityIssue]:
        return [issue for issue in self.issues
                if issue.check == check and (column is None or issue.column == column)]

    def rows_for(self, check: str, column: str | None = None) -> set[int]:
        return {row for issue in self.find(check, column) for row in issue.rows}


# ---------------------------------------------------------------------------
# Reading single values
# ---------------------------------------------------------------------------
NUM_PLAIN, NUM_CURRENCY, NUM_FORMATTED, NUM_TEXT = "plain", "currency", "formatted", "text"
_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_CURRENCY_SYMBOLS = re.compile(r"[$€£¥₹]")
_CURRENCY_CODES = re.compile(r"^(USD|EUR|GBP|PKR|INR|AUD|CAD|JPY|CNY|AED|SAR)|(USD|EUR|GBP|PKR|INR|AUD|CAD|JPY|CNY|AED|SAR)$",
                             re.IGNORECASE)


def parse_number(value) -> tuple[str, float | None]:
    """Classify one value as a number: plain, with a currency mark, otherwise
    formatted (thousands separators, brackets, %), or text (not a number)."""
    if isinstance(value, (bool, np.bool_)):
        return NUM_TEXT, None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return NUM_PLAIN, float(value)
    if not isinstance(value, str):
        return NUM_TEXT, None
    text = value.strip()
    if _PLAIN_NUMBER.match(text):
        return NUM_PLAIN, float(text)

    text, negative, has_currency = number_core(text)
    if _PLAIN_NUMBER.match(text):
        number = float(text)
    elif _THOUSANDS.match(text):
        number = float(text.replace(",", ""))
    else:
        return NUM_TEXT, None
    if negative:
        number = -abs(number)
    return (NUM_CURRENCY if has_currency else NUM_FORMATTED), number


def number_core(text: str) -> tuple[str, bool, bool]:
    """Strip number decoration. Returns (digits text, negative-in-brackets, had currency mark).
    "($1,200.00)" → ("1,200.00", True, True); "15%" → ("15", False, False)."""
    text = text.strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text, symbols = _CURRENCY_SYMBOLS.subn("", text)
    text = text.replace(" ", "")
    text, codes = _CURRENCY_CODES.subn("", text)
    if text.endswith("%"):
        text = text[:-1]
    return text, negative, bool(symbols or codes)


SLASH_STYLE = "slash"
NATIVE_DATE_STYLE = "Excel date"
_DATE_STYLES = [
    ("YYYY-MM-DD", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"), ("%Y-%m-%d",)),
    ("YYYY-MM-DD hh:mm", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2})?$"),
     ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")),
    ("YYYY/MM/DD", re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"), ("%Y/%m/%d",)),
    (SLASH_STYLE, re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), ()),
    ("DD-MM-YYYY", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"), ("%d-%m-%Y",)),
    ("DD.MM.YYYY", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$"), ("%d.%m.%Y",)),
    ("DD-Mon-YYYY", re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"), ("%d-%b-%Y",)),
    ("DD Month YYYY", re.compile(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$"), ("%d %b %Y", "%d %B %Y")),
    ("Month DD, YYYY", re.compile(r"^[A-Za-z]{3,9} \d{1,2},? \d{4}$"),
     ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y")),
]


def classify_date(value) -> tuple[str | None, datetime | None, tuple | None]:
    """Return (format style, parsed date, slash parts).

    style None            → not written as a date at all
    style set, date None  → written like a date but impossible (e.g. 2025-02-30)
    style "slash"         → dd/mm/yyyy or mm/dd/yyyy; resolved per column (parts given)
    """
    if isinstance(value, (pd.Timestamp, datetime)):
        return NATIVE_DATE_STYLE, pd.Timestamp(value).to_pydatetime(), None
    if isinstance(value, date):
        return NATIVE_DATE_STYLE, datetime(value.year, value.month, value.day), None
    if not isinstance(value, str):
        return None, None, None
    return _classify_date_text(value.strip())


@lru_cache(maxsize=65536)
def _classify_date_text(text: str) -> tuple[str | None, datetime | None, tuple | None]:
    """classify_date for text. Cached: a date column repeats the same few hundred
    dates many times, and parsing is the slow part (the result never changes)."""
    for style, pattern, formats in _DATE_STYLES:
        if not pattern.match(text):
            continue
        if style == SLASH_STYLE:
            first, second, year = (int(part) for part in text.split("/"))
            return SLASH_STYLE, None, (first, second, year)
        for date_format in formats:
            try:
                return style, datetime.strptime(text, date_format), None
            except ValueError:
                continue
        return style, None, None
    return None, None, None


def _shown(value) -> str:
    """Example value for display; quotes reveal extra spaces, blanks are named."""
    text = display_value(value)
    if text == "":
        return "(blank)"
    if text != text.strip() or "  " in text:
        return f'"{text}"'
    return text if len(text) <= 40 else text[:37] + "..."


def _examples(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        shown = value if isinstance(value, str) and value.startswith(("Row ", "(")) else _shown(value)
        if shown not in seen:
            seen.append(shown)
        if len(seen) == MAX_EXAMPLES:
            break
    return seen


def _normalize_category(text: str) -> str:
    text = text.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


# ---------------------------------------------------------------------------
# Column checks
# ---------------------------------------------------------------------------
class _ColumnChecker:
    def __init__(self, name: str, series: pd.Series, blank: pd.Series, role: str | None,
                 rules: QualityRules, issues: list[QualityIssue]):
        self.name = name
        self.role = role
        self.rules = rules
        self.issues = issues
        values = series.to_numpy(dtype=object)
        self.labels = series.index.to_numpy()   # original row labels (differ from positions after exclusions)
        self.filled = [(int(pos), values[pos]) for pos in np.flatnonzero(~blank.to_numpy())]
        self.missing = [int(pos) for pos in np.flatnonzero(blank.to_numpy())]
        self.numbers: dict[int, float] = {}

    def add(self, check, problem, severity, rows, examples, explanation, scope="rows"):
        self.issues.append(QualityIssue(check, self.name, problem, severity, sorted(rows), examples,
                                        explanation, scope))

    # -- run all checks; returns how the column was treated ------------------
    def run(self) -> str:
        if not self.filled:
            self.add("empty_column", "Column is completely empty", WARNING, [], [],
                     "Every cell in this column is blank. It may be an unused column or an export mistake. "
                     "It is ignored in calculations.", scope="column")
            return KIND_EMPTY

        self._check_missing()
        self._check_spaces()

        # Each distinct value is classified once (columns repeat values a lot).
        number_cache: dict = {}
        date_cache: dict = {}
        number_info, date_info = {}, {}
        for pos, value in self.filled:
            key = (type(value), value)
            if key not in number_cache:
                number_cache[key] = parse_number(value)
                date_cache[key] = classify_date(value)
            number_info[pos] = number_cache[key]
            date_info[pos] = date_cache[key]
        count = len(self.filled)
        number_share = sum(kind != NUM_TEXT for kind, _ in number_info.values()) / count
        date_share = sum(style is not None for style, _, _ in date_info.values()) / count
        minimum = self.rules.expected_type_min_share

        if self.role in NUMERIC_ROLES or (self.role is None and number_share >= minimum and date_share < minimum):
            self._check_numbers(number_info)
            return KIND_NUMBER
        if self.role in DATE_ROLES or (self.role is None and date_share >= minimum):
            self._check_dates(date_info)
            return KIND_DATE

        self._check_mixed_types(number_info, date_info)
        self._check_placeholders()
        if self._is_category_column():
            self._check_categories()
            return KIND_CATEGORY
        return KIND_TEXT

    # -- missing values and spaces -------------------------------------------
    def _check_missing(self):
        if not self.missing:
            return
        required = self.role in self.rules.required_roles
        label = ROLE_LABELS.get(self.role, "")
        explanation = (f"Blank cells in the {label.lower()} column. This value is needed for calculations, "
                       "so these rows can't be fully used until it is filled in."
                       if required else
                       "Some cells are blank. Rows can still be used, but anything that relies on this "
                       "column will leave them out.")
        self.add("missing_value", "Missing values", ERROR if required else WARNING, self.missing,
                 [f"Row {int(self.labels[pos]) + 1}" for pos in self.missing[:MAX_EXAMPLES]], explanation)

    def _check_spaces(self):
        rows = [pos for pos, value in self.filled
                if isinstance(value, str) and (value != value.strip() or "  " in value)]
        if rows:
            values = dict(self.filled)
            self.add("extra_spaces", "Extra spaces", WARNING, rows, _examples(values[pos] for pos in rows),
                     "Spaces at the start or end (or double spaces) make identical values look different, "
                     "so they may be counted as separate items in totals and charts.")

    # -- numbers ---------------------------------------------------------------
    def _check_numbers(self, number_info: dict[int, tuple[str, float | None]]):
        values = dict(self.filled)
        by_kind: dict[str, list[int]] = defaultdict(list)
        for pos, (kind, number) in number_info.items():
            by_kind[kind].append(pos)
            if number is not None:
                self.numbers[pos] = number

        if by_kind[NUM_TEXT]:
            confirmed = self.role in NUMERIC_ROLES
            self.add("text_in_number", "Text in a number column", ERROR if confirmed else WARNING,
                     by_kind[NUM_TEXT], _examples(values[pos] for pos in by_kind[NUM_TEXT]),
                     "These values are not numbers, so they can't be used in calculations. "
                     "Placeholders like N/A usually mean the value is unknown."
                     + ("" if confirmed else " This column was treated as numbers because most of its values are."))
        if by_kind[NUM_CURRENCY]:
            self.add("currency_format", "Currency symbols in numbers", WARNING, by_kind[NUM_CURRENCY],
                     _examples(values[pos] for pos in by_kind[NUM_CURRENCY]),
                     "Some values include a currency symbol or code (e.g. $ or USD) while others don't. "
                     "The number can be read, but the formatting is inconsistent and should be standardised.")
        if by_kind[NUM_FORMATTED]:
            self.add("number_format", "Inconsistent number formatting", WARNING, by_kind[NUM_FORMATTED],
                     _examples(values[pos] for pos in by_kind[NUM_FORMATTED]),
                     "Some numbers are written with thousands separators, brackets or a % sign. They can be "
                     "read, but the formatting differs from the rest of the column.")

        sign_rule = self.rules.sign_rules.get(self.role, {})
        negative = [pos for pos, number in self.numbers.items() if number < 0]
        zero = [pos for pos, number in self.numbers.items() if number == 0]
        meaning = {"quantity": "a return or correction", "unit_price": "a discount or credit line",
                   "sales_amount": "a refund", "expense_amount": "a refund or credit"}.get(self.role, "")
        if negative and sign_rule.get("negative"):
            self.add("negative_value", "Negative values", sign_rule["negative"], negative,
                     _examples(values[pos] for pos in negative),
                     f"Negative values are unusual here. They may be valid (for example {meaning}), "
                     "so please check them rather than assume they are wrong.")
        if zero and sign_rule.get("zero"):
            self.add("zero_value", "Zero values", sign_rule["zero"], zero, _examples(values[pos] for pos in zero),
                     "A value of zero is unusual here and may be a placeholder or data-entry mistake.")
        if self.role in NUMERIC_ROLES:
            self._check_extremes(values)

    def _check_extremes(self, values: dict):
        numbers = pd.Series(self.numbers, dtype=float)
        if len(numbers) < self.rules.extreme_value_min_count:
            return
        q1, q3 = numbers.quantile(0.25), numbers.quantile(0.75)
        spread = q3 - q1
        if spread <= 0:
            return
        k = self.rules.extreme_value_iqr_multiplier
        low, high = q1 - k * spread, q3 + k * spread
        rows = [int(pos) for pos, number in numbers.items() if number < low or number > high]
        if rows:
            self.add("extreme_value", "Extremely large or small values", WARNING, rows,
                     _examples(values[pos] for pos in rows),
                     f"Most values in this column are between {q1:,.2f} and {q3:,.2f}. These values are far "
                     f"outside that range (below {low:,.2f} or above {high:,.2f}) and may be typing mistakes.")

    # -- dates -------------------------------------------------------------------
    def _check_dates(self, date_info: dict[int, tuple]):
        values = dict(self.filled)
        rules = self.rules
        slash_parts = {pos: parts for pos, (style, _, parts) in date_info.items() if style == SLASH_STYLE}
        day_first_evidence = sum(first > 12 for first, _, _ in slash_parts.values())
        month_first_evidence = sum(second > 12 for _, second, _ in slash_parts.values())
        if day_first_evidence != month_first_evidence:
            column_day_first = day_first_evidence > month_first_evidence
        else:
            column_day_first = rules.day_first_default

        resolved: dict[int, tuple[str | None, datetime | None]] = {}
        for pos, (style, parsed, parts) in date_info.items():
            if style != SLASH_STYLE:
                resolved[pos] = (style, parsed)
                continue
            first, second, year = parts
            day_first = True if first > 12 else False if second > 12 else column_day_first
            day, month = (first, second) if day_first else (second, first)
            try:
                parsed = datetime(year, month, day)
            except ValueError:
                parsed = None
            resolved[pos] = ("DD/MM/YYYY" if day_first else "MM/DD/YYYY", parsed)

        not_dates = [pos for pos, (style, _) in resolved.items() if style is None]
        impossible = [pos for pos, (style, parsed) in resolved.items() if style is not None and parsed is None]
        valid = {pos: (style, parsed) for pos, (style, parsed) in resolved.items() if parsed is not None}

        if impossible:
            self.add("invalid_date", "Impossible dates", ERROR, impossible, _examples(values[p] for p in impossible),
                     "These look like dates but do not exist (for example 30 February or month 13).")
        if not_dates:
            confirmed = self.role in DATE_ROLES
            self.add("not_a_date", "Values that are not dates", ERROR if confirmed else WARNING, not_dates,
                     _examples(values[p] for p in not_dates),
                     "These values in a date column can't be read as dates in any common format.")

        style_counts = Counter(style for style, _ in valid.values())
        if len(style_counts) > 1:
            main_style = style_counts.most_common(1)[0][0]
            others = [pos for pos, (style, _) in valid.items() if style != main_style]
            summary = ", ".join(f"{style} ({count:,})" for style, count in style_counts.most_common())
            self.add("inconsistent_date_format", "Inconsistent date formats", WARNING, others,
                     _examples(f"{display_value(values[p]).strip()} ({valid[p][0]})" for p in others),
                     f"Dates are written in {len(style_counts)} different ways: {summary}. The most common "
                     f"format is {main_style}. Mixed formats are easy to misread, so they should be standardised.")

        if slash_parts and day_first_evidence == 0 and month_first_evidence == 0:
            order = "day first (DD/MM/YYYY)" if rules.day_first_default else "month first (MM/DD/YYYY)"
            self.add("ambiguous_date_order", "Day and month order unclear", INFO, list(slash_parts),
                     _examples(values[p] for p in slash_parts),
                     f"Dates like 03/05/2025 could mean 3 May or 5 March. Nothing in this column settles it, "
                     f"so BusinessPulse assumes {order}. Change `day_first_default` in the settings if needed.")

        today = rules.today()
        future = [pos for pos, (_, parsed) in valid.items() if parsed.date() > today]
        if future:
            self.add("future_date", "Dates in the future", WARNING, future, _examples(values[p] for p in future),
                     f"These dates are after today ({today.isoformat()}). They may be typing mistakes, or "
                     "planned/scheduled records.")
        too_old = [pos for pos, (_, parsed) in valid.items() if parsed.year < rules.min_plausible_year]
        if too_old:
            self.add("old_date", "Unusually old dates", WARNING, too_old, _examples(values[p] for p in too_old),
                     f"These dates are before {rules.min_plausible_year}, which is unusual for business records.")

    # -- text ---------------------------------------------------------------------
    def _check_mixed_types(self, number_info, date_info):
        kinds: dict[str, list[int]] = defaultdict(list)
        for pos, _ in self.filled:
            if date_info[pos][0] is not None:
                kinds["dates"].append(pos)
            elif number_info[pos][0] != NUM_TEXT:
                kinds["numbers"].append(pos)
            else:
                kinds["text"].append(pos)
        if len(kinds) < 2 or self.role == "description":
            return
        total = len(self.filled)
        main_kind = max(kinds, key=lambda kind: len(kinds[kind]))
        minority = [pos for kind, rows in kinds.items() if kind != main_kind for pos in rows]
        if len(minority) / total < self.rules.mixed_type_min_share:
            return
        values = dict(self.filled)
        summary = ", ".join(f"{kind} ({len(rows):,})" for kind, rows in sorted(kinds.items(), key=lambda kv: -len(kv[1])))
        self.add("mixed_types", "Mixed data types", WARNING, minority, _examples(values[p] for p in minority),
                 f"This column mixes different kinds of values: {summary}. A column should hold one kind of "
                 f"value; the rows listed are the ones that differ from the most common kind ({main_kind}).")

    def _check_placeholders(self):
        placeholders = self.rules.placeholder_values
        rows = [pos for pos, value in self.filled
                if isinstance(value, str) and value.strip().casefold() in placeholders]
        if rows:
            values = dict(self.filled)
            self.add("placeholder_value", "Placeholder instead of a real value", WARNING, rows,
                     _examples(values[p] for p in rows),
                     "Values such as 'Unknown', 'N/A' or '???' are placeholders: the real value was not "
                     "entered. They will be grouped as if they were a real category.")

    def _text_values(self) -> dict[int, str]:
        placeholders = self.rules.placeholder_values
        return {pos: str(value).strip() for pos, value in self.filled
                if not (isinstance(value, str) and value.strip().casefold() in placeholders)}

    def _is_category_column(self) -> bool:
        if self.role in CATEGORY_ROLES:
            return True
        if self.role in FREE_TEXT_ROLES:
            return False
        texts = self._text_values()
        distinct = len({text.casefold() for text in texts.values()})
        return len(texts) >= 10 and distinct <= self.rules.category_max_distinct and distinct <= len(texts) / 2

    def _check_categories(self):
        texts = self._text_values()
        if not texts:
            return
        case_fixes, name_fixes = suggest_category_fixes(list(texts.values()), self.rules)

        case_rows = [pos for pos, text in texts.items() if text in case_fixes]
        if case_rows:
            self.add("capitalization", "Inconsistent capitalization", WARNING, case_rows,
                     _examples(f"{texts[p]} → {case_fixes[texts[p]]}" for p in case_rows),
                     "The same name is written with different capital letters. Most tools treat these as "
                     "different values, which splits totals. The suggested spelling is the most common one.")

        rows = [pos for pos, text in texts.items() if text in name_fixes]
        if rows:
            self.add("suspicious_category", "Suspicious category names", WARNING, rows,
                     _examples(f"{texts[p]} → {name_fixes[texts[p]]}" for p in rows),
                     "These names are rare and look like a misspelling or variation of a more common name. "
                     "If they mean the same thing, they should be spelled the same way so totals are not split.")


def suggest_category_fixes(values: list[str], rules: QualityRules) -> tuple[dict[str, str], dict[str, str]]:
    """Suggest consistent spellings for a list of category names (already trimmed).

    Returns (case_fixes, name_fixes), each {current spelling: suggested spelling}:
    - case_fixes: same name with different capital letters ("north" → "North").
    - name_fixes: different punctuation ("Home and Kitchen" → "Home & Kitchen"), or a rare
      name that looks like a misspelling/shortening of a common one ("Electronis" → "Electronics").
    The suggested spelling is always the most common existing spelling.
    """
    spelling_counts = Counter(values)
    by_case: dict[str, Counter] = defaultdict(Counter)
    for text, count in spelling_counts.items():
        by_case[text.casefold()][text] = count
    preferred = {key: spellings.most_common(1)[0][0] for key, spellings in by_case.items()}
    case_fixes = {text: preferred[text.casefold()] for text in spelling_counts if text != preferred[text.casefold()]}

    def norm_of(text: str) -> str:
        return _normalize_category(text) or text.casefold()

    key_counts: Counter = Counter()
    key_spelling: dict[str, Counter] = defaultdict(Counter)
    case_keys_of_norm: dict[str, Counter] = defaultdict(Counter)
    for text, count in spelling_counts.items():
        norm = norm_of(text)
        key_counts[norm] += count
        key_spelling[norm][preferred[text.casefold()]] += count
        case_keys_of_norm[norm][text.casefold()] += count

    name_fixes: dict[str, str] = {}
    # Same letters, different punctuation ("Home and Kitchen" vs "Home & Kitchen").
    for text in spelling_counts:
        main_case_key = case_keys_of_norm[norm_of(text)].most_common(1)[0][0]
        if text.casefold() != main_case_key:
            name_fixes[text] = preferred[main_case_key]

    # Rare names that look like a misspelling or shortening of a common one.
    keys = sorted(key_counts, key=lambda key: -key_counts[key])
    replacement_key: dict[str, str] = {}
    for rare in keys:
        for common in keys:
            if common == rare or key_counts[rare] > rules.rare_variant_ratio * key_counts[common]:
                continue
            similar = difflib.SequenceMatcher(None, rare, common).ratio() >= rules.similar_name_threshold
            rare_words, common_words = rare.split(), common.split()
            shortened = len(rare_words) < len(common_words) and common_words[:len(rare_words)] == rare_words
            if similar or shortened:
                replacement_key[rare] = common
                break
    for text in spelling_counts:
        norm = norm_of(text)
        if norm in replacement_key and text not in name_fixes:
            name_fixes[text] = key_spelling[replacement_key[norm]].most_common(1)[0][0]
    return case_fixes, name_fixes


# ---------------------------------------------------------------------------
# Whole-table checks
# ---------------------------------------------------------------------------
def _check_duplicates(data: pd.DataFrame, blanks: pd.DataFrame, mapping: ColumnMapping,
                      issues: list[QualityIssue]) -> list[int]:
    keys = pd.util.hash_pandas_object(data.astype(str), index=False).to_numpy()
    first_seen: dict[int, int] = {}
    duplicate_of: dict[int, int] = {}
    for pos, key in enumerate(keys):
        if key in first_seen:
            duplicate_of[pos] = first_seen[key]
        else:
            first_seen[key] = pos
    duplicate_rows = sorted(duplicate_of)
    id_column = mapping.column_for("id")
    labels = data.index.to_numpy()
    if duplicate_rows:
        has_id = id_column is not None
        issues.append(QualityIssue(
            "duplicate_row", None, "Duplicate rows", ERROR if has_id else WARNING, duplicate_rows,
            [f"Row {int(labels[pos]) + 1} = row {int(labels[duplicate_of[pos]]) + 1}"
             for pos in duplicate_rows[:MAX_EXAMPLES]],
            "These rows are exact copies of an earlier row (every value is identical), so they would be counted "
            "twice." + (" The ID is repeated too, which confirms the copy." if has_id else
                        " Without an ID column this could also be two genuine identical records, so please check."),
        ))

    if id_column is not None:
        ids = data[id_column].astype(str).str.strip()
        # Ignore blank IDs (reported as missing) and exact copies (reported above).
        not_a_copy = np.ones(len(data), dtype=bool)
        not_a_copy[duplicate_rows] = False
        positions = np.flatnonzero(~blanks[id_column].to_numpy() & not_a_copy)
        repeated = ids.iloc[positions].duplicated(keep=False).to_numpy()
        rows = [int(pos) for pos, flag in zip(positions, repeated) if flag]
        if rows:
            issues.append(QualityIssue(
                "duplicate_id", id_column, "Same ID used more than once", WARNING, rows,
                _examples(ids.iloc[p] for p in rows),
                "These IDs appear on more than one row, but the rows are not identical. An ID should normally "
                "identify one record. If your file lists several items of one order on separate rows, this is "
                "expected and can be ignored.",
            ))
    return duplicate_rows


def _check_totals(data: pd.DataFrame, mapping: ColumnMapping, numbers: dict[str, dict[int, float]],
                  rules: QualityRules, issues: list[QualityIssue]) -> None:
    quantity, price, total = (mapping.column_for(role) for role in ("quantity", "unit_price", "sales_amount"))
    if not (quantity and price and total):
        return
    q, p, t = numbers.get(quantity, {}), numbers.get(price, {}), numbers.get(total, {})
    rows = [pos for pos in sorted(set(q) & set(p) & set(t)) if abs(q[pos] * p[pos] - t[pos]) > rules.total_tolerance]
    if rows:
        issues.append(QualityIssue(
            "total_mismatch", total, "Total doesn't equal quantity × unit price", WARNING, rows,
            [f"Row {int(data.index[pos]) + 1}: {q[pos]:g} × {p[pos]:,.2f} = {q[pos] * p[pos]:,.2f}, "
             f"but total is {t[pos]:,.2f}" for pos in rows[:MAX_EXAMPLES]],
            f"For these rows, {quantity} × {price} does not match {total} (allowed difference: "
            f"{rules.total_tolerance}). This can be a calculation or typing mistake, or a discount, tax or "
            "fee that is included in the total.",
        ))


def _check_missing_roles(mapping: ColumnMapping, issues: list[QualityIssue]) -> None:
    if mapping.column_for("date") is None:
        issues.append(QualityIssue(
            "missing_role", None, "No date column recognised", INFO, [], [],
            "No column was recognised as a date. Trends over time will not be available unless you choose "
            "the date column under 'Column recognition'.", scope="column"))
    if not ({"sales_amount", "expense_amount"} & mapping.matches.keys()):
        issues.append(QualityIssue(
            "missing_role", None, "No sales or expense amount recognised", INFO, [], [],
            "No column was recognised as a sales or expense amount, so revenue, cost and profit can't be "
            "calculated yet. If one of your columns holds amounts, choose it under 'Column recognition'.",
            scope="column"))


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------
def calculate_score(row_count: int, issues: list[QualityIssue], rules: QualityRules) -> QualityScore:
    """Score = share of usable rows (rows with errors count 0, rows with only
    warnings count `warning_row_weight`), minus a small penalty per completely
    empty column. Info notes do not count."""
    error_rows = {row for issue in issues if issue.severity == ERROR for row in issue.rows}
    warning_rows = {row for issue in issues if issue.severity == WARNING for row in issue.rows} - error_rows
    clean_rows = row_count - len(error_rows) - len(warning_rows)
    row_score = 100.0 if row_count == 0 else \
        100.0 * (clean_rows + rules.warning_row_weight * len(warning_rows)) / row_count
    empty_columns = sum(1 for issue in issues if issue.check == "empty_column")
    penalty = min(empty_columns * rules.empty_column_penalty, rules.max_empty_column_penalty)
    score = round(max(0.0, row_score - penalty), 1)
    grade = next(label for minimum, label in rules.grades if score >= minimum)
    return QualityScore(score, grade, row_count, len(error_rows), len(warning_rows), clean_rows,
                        round(row_score, 1), empty_columns, penalty, rules.warning_row_weight)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def analyze_quality(data: pd.DataFrame, mapping: ColumnMapping | None = None,
                    rules: QualityRules | None = None) -> QualityReport:
    """Check every column and the table as a whole. `data` is not changed."""
    rules = rules or QualityRules()
    mapping = mapping or recognize_columns(list(data.columns))
    blanks = blank_mask(data)
    issues: list[QualityIssue] = []
    profiles: list[ColumnProfile] = []
    numbers: dict[str, dict[int, float]] = {}

    for column in data.columns:
        role = mapping.role_of(column)
        checker = _ColumnChecker(column, data[column], blanks[column], role, rules, issues)
        treated_as = checker.run()
        numbers[column] = checker.numbers
        profiles.append(ColumnProfile(column, role, treated_as, len(checker.filled), len(checker.missing)))

    duplicate_rows = _check_duplicates(data, blanks, mapping, issues)
    _check_totals(data, mapping, numbers, rules, issues)
    _check_missing_roles(mapping, issues)

    issues.sort(key=lambda issue: (SEVERITY_ORDER[issue.severity], -issue.affected_count, issue.column or ""))
    missing_per_column = blanks.sum()
    return QualityReport(
        row_count=len(data),
        column_count=len(data.columns),
        mapping=mapping,
        issues=issues,
        profiles=profiles,
        missing_cells=int(missing_per_column.sum()),
        columns_with_missing=int((missing_per_column > 0).sum()),
        duplicate_rows=len(duplicate_rows),
        score=calculate_score(len(data), issues, rules),
        severity_counts={level: sum(issue.severity == level for issue in issues) for level in SEVERITY_ORDER},
    )


# ---------------------------------------------------------------------------
# Tables for display
# ---------------------------------------------------------------------------
def issues_table(report: QualityReport) -> pd.DataFrame:
    rows = []
    for issue in report.issues:
        affected = (f"{report.row_count:,} (whole column)" if issue.check == "empty_column"
                    else "—" if issue.scope == "column" else f"{issue.affected_count:,}")
        rows.append({
            "Severity": issue.severity,
            "Column": issue.column or "(all columns)",
            "Problem": issue.problem,
            "Affected rows": affected,
            "Example values": ", ".join(issue.examples),
            "Explanation": issue.explanation,
        })
    return pd.DataFrame(rows, columns=["Severity", "Column", "Problem", "Affected rows", "Example values",
                                       "Explanation"])


def columns_table(report: QualityReport) -> pd.DataFrame:
    issue_counts = Counter(issue.column for issue in report.issues if issue.column)
    return pd.DataFrame([{
        "Column": profile.column,
        "Recognised as": ROLE_LABELS.get(profile.role, "—") if profile.role else "—",
        "Checked as": profile.treated_as,
        "Filled": profile.filled,
        "Missing": profile.missing,
        "Problems found": issue_counts.get(profile.column, 0),
    } for profile in report.profiles])


def affected_rows_table(data: pd.DataFrame, issue: QualityIssue, limit: int = 500) -> pd.DataFrame:
    """The affected rows exactly as written in the file, numbered like the preview."""
    positions = issue.rows[:limit]
    table = data.iloc[positions].copy()
    for column in table.columns:
        table[column] = table[column].map(display_value).astype(str)
    table.index = pd.Index([int(data.index[pos]) + 1 for pos in positions], name="Row")
    return table
