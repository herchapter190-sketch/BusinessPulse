"""Load uploaded CSV and Excel files safely, and describe their contents.

This module has no user-interface code, so it can be tested on its own and
reused by any page.

Data-safety rules followed here:
- The uploaded file's bytes are only read, never changed or written to disk.
- CSV values are kept exactly as written (as text). Converting columns to
  numbers or dates is a separate, explained step that happens later.
- Anything changed while loading (renamed headings, ignored blank rows) is
  reported back in `LoadedData.notes` / `LoadedData.renamed_columns`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from config.settings import MAX_UPLOAD_MB, PREVIEW_ROWS, SUPPORTED_EXTENSIONS, TYPE_DETECTION_SAMPLE_SIZE

BYTES_PER_MB = 1024 * 1024
CSV_DELIMITERS = ",;\t|"
DELIMITER_NAMES = {",": "commas", ";": "semicolons", "\t": "tabs", "|": "vertical bars"}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%b-%Y",
                "%d %b %Y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y")
TRUE_FALSE_WORDS = {"true", "false", "yes", "no"}

# Friendly names for detected column types.
TYPE_WHOLE_NUMBER = "Whole number"
TYPE_DECIMAL = "Decimal number"
TYPE_DATE = "Date"
TYPE_TRUE_FALSE = "True/False"
TYPE_TEXT = "Text"
TYPE_EMPTY = "Empty"


class FileLoadError(Exception):
    """A problem with the file that the user can fix. `message` says what went
    wrong in plain language; `hint` says what to do about it."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class RenamedColumn:
    position: int          # 1-based column position in the file
    original: str          # heading as written in the file ("" if blank)
    new_name: str          # unique name used by BusinessPulse
    reason: str            # "blank heading" or "duplicate heading"


@dataclass
class LoadedData:
    file_name: str
    file_type: str                     # "csv" or "xlsx"
    file_size_bytes: int
    data: pd.DataFrame
    sheet_name: str | None = None      # Excel only
    sheet_names: list[str] = field(default_factory=list)
    encoding: str | None = None        # CSV only
    delimiter: str | None = None       # CSV only
    renamed_columns: list[RenamedColumn] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.data)

    @property
    def column_count(self) -> int:
        return len(self.data.columns)


# ---------------------------------------------------------------------------
# Basic checks
# ---------------------------------------------------------------------------
def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    if size_bytes < BYTES_PER_MB:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / BYTES_PER_MB:.1f} MB"


def get_file_type(file_name: str) -> str:
    """Return "csv" or "xlsx", or raise FileLoadError for anything else."""
    extension = Path(file_name).suffix.lower()
    if extension in SUPPORTED_EXTENSIONS:
        return extension.lstrip(".")
    if extension == ".xls":
        raise FileLoadError(
            "Older Excel files (.xls) are not supported.",
            "Open the file in Excel and choose File → Save As → Excel Workbook (.xlsx), then upload the new file.",
        )
    if extension in (".xlsm", ".xlsb", ".ods", ".numbers"):
        raise FileLoadError(
            f"'{extension}' spreadsheets are not supported.",
            "Save the spreadsheet as an Excel Workbook (.xlsx) or as CSV, then upload it again.",
        )
    shown = extension or "no extension"
    raise FileLoadError(
        f"This file type ({shown}) is not supported.",
        "BusinessPulse accepts CSV files (.csv) and Excel workbooks (.xlsx).",
    )


def dataset_signature(file_name: str, sheet_name: str | None, content: bytes) -> str:
    """Identifies one file + worksheet, so cleaning work is never mixed up between files."""
    digest = hashlib.sha256(content).hexdigest()[:16]
    return f"{file_name}|{sheet_name or ''}|{digest}"


def check_file_size(size_bytes: int, max_mb: float = MAX_UPLOAD_MB) -> None:
    if size_bytes == 0:
        raise FileLoadError("The file is empty (0 bytes).",
                            "Check that you picked the right file and that it was saved correctly.")
    if size_bytes > max_mb * BYTES_PER_MB:
        raise FileLoadError(
            f"The file is too large ({format_file_size(size_bytes)}). The limit is {max_mb} MB.",
            "Try removing columns or rows you don't need, or split the data into smaller files.",
        )


# ---------------------------------------------------------------------------
# Column headings
# ---------------------------------------------------------------------------
def _heading_text(value) -> str:
    """Turn a header cell into text. Blank cells become ""."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel stores 2025 as 2025.0
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text if text.strip() else ""


def make_unique_column_names(headings: list) -> tuple[list[str], list[RenamedColumn]]:
    """Give every column a unique, non-blank name.

    Blank headings become "Column N" (N = position). Repeated headings get a
    number: "Amount", "Amount (2)", ... A generated name never clashes with a
    heading that already exists elsewhere in the file.
    """
    originals = [_heading_text(value) for value in headings]
    existing = {name for name in originals if name}
    used: set[str] = set()
    names: list[str] = []
    renamed: list[RenamedColumn] = []

    for position, original in enumerate(originals, start=1):
        base = original or f"Column {position}"
        candidate, counter = base, 2
        while candidate in used or (candidate != original and candidate in existing):
            candidate = f"{base} ({counter})"
            counter += 1
        if candidate != original:
            reason = "blank heading" if not original else "duplicate heading"
            renamed.append(RenamedColumn(position, original, candidate, reason))
        used.add(candidate)
        names.append(candidate)
    return names, renamed


# ---------------------------------------------------------------------------
# Shared table handling
# ---------------------------------------------------------------------------
def blank_mask(frame: pd.DataFrame) -> pd.DataFrame:
    """True where a cell is empty: missing, or text containing only spaces."""
    if frame.empty:
        return frame.isna()
    spaces_only = frame.apply(lambda column: column.astype(str).str.strip().eq("")).astype(bool)
    return frame.isna() | spaces_only


def _table_from_raw(raw: pd.DataFrame, source_label: str) -> tuple[pd.DataFrame, list[RenamedColumn], list[str]]:
    """Use the first non-empty row as headings and the rest as data."""
    notes: list[str] = []
    blanks = blank_mask(raw)

    empty_rows = blanks.all(axis=1)
    empty_columns = blanks.all(axis=0)
    if empty_rows.all():
        raise FileLoadError(f"{source_label} has no data in it.",
                            "Check that you picked the right file or worksheet.")

    raw = raw.loc[~empty_rows, ~empty_columns]
    if empty_columns.sum():
        notes.append(f"Ignored {int(empty_columns.sum())} completely empty column(s).")

    headings = raw.iloc[0].tolist()
    body = raw.iloc[1:]
    leading_blank_rows = int(empty_rows.loc[:raw.index[0]].sum())
    other_blank_rows = int(empty_rows.sum()) - leading_blank_rows
    if leading_blank_rows:
        notes.append(f"Skipped {leading_blank_rows} empty row(s) above the column headings.")
    if other_blank_rows:
        notes.append(f"Ignored {other_blank_rows} completely empty row(s) below the column headings.")

    if body.empty:
        raise FileLoadError(
            f"{source_label} has column headings but no data rows.",
            "Add at least one row of data below the headings.",
        )

    names, renamed = make_unique_column_names(headings)
    body = body.reset_index(drop=True)
    body.columns = names
    return body, renamed, notes


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _decode_text(content: bytes) -> tuple[str, str]:
    """Decode CSV bytes, trying the encodings that spreadsheets usually produce."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16"), "UTF-16"
    for encoding, label in (("utf-8-sig", "UTF-8"), ("cp1252", "Windows-1252")):
        try:
            return content.decode(encoding), label
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1"), "Latin-1"  # never fails


def _detect_delimiter(text: str) -> str:
    sample = text[:64 * 1024]
    try:
        return csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        return ","


def read_csv(file_name: str, content: bytes) -> LoadedData:
    if content.startswith(b"PK\x03\x04"):
        raise FileLoadError(
            "This file looks like an Excel workbook, but its name ends in .csv.",
            "Rename it so it ends in .xlsx, or open it in Excel and save it as CSV.",
        )
    text, encoding = _decode_text(content)
    if "\x00" in text[:8192]:
        raise FileLoadError(
            "This file doesn't look like a CSV text file. It may be damaged or in a different format.",
            "Open it in Excel or a text editor and save it again as CSV (comma delimited).",
        )
    if not text.strip():
        raise FileLoadError("The file is empty.", "Check that you picked the right file.")

    delimiter = _detect_delimiter(text)
    try:
        raw = pd.read_csv(io.StringIO(text), sep=delimiter, header=None, dtype=str,
                          keep_default_na=False, skip_blank_lines=True)
    except pd.errors.EmptyDataError:
        raise FileLoadError("The file is empty.", "Check that you picked the right file.") from None
    except pd.errors.ParserError as error:
        raise FileLoadError(
            "Some rows have a different number of values than the heading row, so the file can't be read reliably.",
            f"Check the file in a text editor or Excel. Details: {str(error).strip()}",
        ) from None

    table, renamed, notes = _table_from_raw(raw, "The file")
    return LoadedData(file_name=file_name, file_type="csv", file_size_bytes=len(content), data=table,
                      encoding=encoding, delimiter=delimiter, renamed_columns=renamed, notes=notes)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _damaged_excel_error() -> FileLoadError:
    return FileLoadError(
        "This file can't be opened as an Excel workbook. It may be damaged, password-protected, "
        "or not really an .xlsx file.",
        "Open it in Excel, remove any password, and save it again as Excel Workbook (.xlsx).",
    )


def list_sheets(content: bytes) -> list[str]:
    """Names of the worksheets in an .xlsx file, in workbook order."""
    if not content.startswith(b"PK\x03\x04"):
        raise _damaged_excel_error()
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:  # openpyxl raises many different errors for damaged files
        raise _damaged_excel_error() from None
    try:
        names = list(workbook.sheetnames)
    finally:
        workbook.close()
    if not names:
        raise FileLoadError("This workbook has no worksheets.", "Add a worksheet with data and save it again.")
    return names


def read_excel(file_name: str, content: bytes, sheet_name: str | None = None) -> LoadedData:
    sheet_names = list_sheets(content)
    sheet = sheet_name if sheet_name is not None else sheet_names[0]
    if sheet not in sheet_names:
        raise FileLoadError(f"The worksheet '{sheet}' was not found in this workbook.",
                            f"Available worksheets: {', '.join(sheet_names)}.")
    try:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet, header=None, dtype=object, engine="openpyxl")
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise _damaged_excel_error() from None

    if raw.empty:
        raise FileLoadError(f"The worksheet '{sheet}' is empty.",
                            "Choose another worksheet, or add data to this one." if len(sheet_names) > 1
                            else "Add data to the worksheet and save the file again.")
    table, renamed, notes = _table_from_raw(raw, f"The worksheet '{sheet}'")
    return LoadedData(file_name=file_name, file_type="xlsx", file_size_bytes=len(content), data=table,
                      sheet_name=sheet, sheet_names=sheet_names, renamed_columns=renamed, notes=notes)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def load_file(file_name: str, content: bytes, sheet_name: str | None = None) -> LoadedData:
    """Check and read an uploaded file. Raises FileLoadError with a friendly
    message if the file can't be used. `content` is never modified."""
    file_type = get_file_type(file_name)
    check_file_size(len(content))
    if file_type == "csv":
        return read_csv(file_name, content)
    return read_excel(file_name, content, sheet_name)


# ---------------------------------------------------------------------------
# Preview and column description
# ---------------------------------------------------------------------------
def display_value(value) -> str:
    """A cell value as the user would read it ("" for blanks, dates without 00:00)."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NaT:
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def make_preview(data: pd.DataFrame, rows: int = PREVIEW_ROWS) -> pd.DataFrame:
    """First `rows` rows, with every value shown as text and rows numbered from 1.
    Returns a new table; `data` is not changed."""
    preview = data.head(rows).copy()
    for column in preview.columns:
        preview[column] = preview[column].map(display_value).astype(str)
    preview.index = pd.RangeIndex(1, len(preview) + 1, name="Row")
    return preview


def _is_number(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_))


def detect_column_type(values: pd.Series) -> tuple[str, float]:
    """Best-guess type for a column and the share of filled values that fit it.

    `values` should contain filled cells only. Returns e.g. ("Date", 0.98).
    A type is chosen only when at least half of the values fit it; otherwise "Text".
    """
    if values.empty:
        return TYPE_EMPTY, 1.0
    values = values.head(TYPE_DETECTION_SAMPLE_SIZE).astype(object)
    total = len(values)

    is_bool = values.map(lambda v: isinstance(v, (bool, np.bool_)))
    is_native_number = values.map(_is_number)
    is_native_date = values.map(lambda v: isinstance(v, (datetime, date, pd.Timestamp)))
    is_text = values.map(lambda v: isinstance(v, str))

    text = values[is_text].astype(str).str.strip()
    numbers_from_text = pd.to_numeric(text, errors="coerce")
    dates_from_text = pd.Series(False, index=text.index)
    for date_format in DATE_FORMATS:
        dates_from_text |= pd.to_datetime(text, format=date_format, errors="coerce").notna()
    bools_from_text = text.str.lower().isin(TRUE_FALSE_WORDS)

    number_count = int(is_native_number.sum() + numbers_from_text.notna().sum())
    date_count = int(is_native_date.sum() + dates_from_text.sum())
    bool_count = int(is_bool.sum() + bools_from_text.sum())

    best_type, best_count = max(
        [(TYPE_DATE, date_count), (TYPE_DECIMAL, number_count), (TYPE_TRUE_FALSE, bool_count)],
        key=lambda pair: pair[1],
    )
    if best_count * 2 < total:
        return TYPE_TEXT, 1.0  # any value can be read as text

    if best_type == TYPE_DECIMAL:
        all_numbers = pd.concat([pd.to_numeric(values[is_native_number]), numbers_from_text.dropna()])
        if (all_numbers % 1 == 0).all():
            best_type = TYPE_WHOLE_NUMBER
    return best_type, best_count / total


def format_share(share: float) -> str:
    """Percentage for display. Never rounds up to 100% unless every value fits,
    so a handful of bad values is not hidden."""
    if share >= 1:
        return "100%"
    return f"{min(share * 100, 99.9):.1f}%"


def describe_columns(data: pd.DataFrame) -> pd.DataFrame:
    """One row per column: detected type, how well values fit it, filled and
    empty counts, and a few example values."""
    blanks = blank_mask(data)
    rows = []
    for column in data.columns:
        filled = data.loc[~blanks[column], column]
        detected, share = detect_column_type(filled)
        examples = []
        for value in filled.head(200):
            shown = display_value(value)
            if shown not in examples:
                examples.append(shown if len(shown) <= 30 else shown[:27] + "...")
            if len(examples) == 3:
                break
        rows.append({
            "Column": column,
            "Detected type": detected,
            "Values matching type": format_share(share),
            "Filled": int(len(filled)),
            "Empty": int(blanks[column].sum()),
            "Example values": ", ".join(examples),
        })
    return pd.DataFrame(rows)


def describe_csv_format(loaded: LoadedData) -> str:
    """Short plain-language description of how a CSV file was read."""
    if loaded.file_type != "csv":
        return ""
    separator = DELIMITER_NAMES.get(loaded.delimiter or ",", repr(loaded.delimiter))
    return f"Read as {loaded.encoding} text with values separated by {separator}."
