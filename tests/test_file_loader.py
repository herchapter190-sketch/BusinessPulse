"""Stage 2 checks for tools/file_loader.py and tools/sample_data.py.

Test files are created in pytest's temporary folder (tmp_path) or in memory.
The sample datasets in data/sample/ are only read, never changed.
"""

import hashlib
import io
import json
import tomllib
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from config.settings import MAX_UPLOAD_MB, PREVIEW_ROWS
from tools.file_loader import (
    TYPE_DATE, TYPE_DECIMAL, TYPE_EMPTY, TYPE_TEXT, TYPE_TRUE_FALSE, TYPE_WHOLE_NUMBER,
    FileLoadError, check_file_size, describe_columns, detect_column_type, format_file_size,
    format_share, get_file_type, list_sheets, load_file, make_preview, make_unique_column_names,
)
from tools.sample_data import SAMPLE_DATASETS, SAMPLE_DIR, SAMPLE_WORKBOOK_KEY, read_sample

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers that build temporary test files
# ---------------------------------------------------------------------------
def write_temp_file(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def make_workbook(sheets: dict[str, list[list]]) -> bytes:
    """Build an .xlsx in memory. `sheets` maps sheet name -> rows (lists of cells)."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, rows in sheets.items():
        sheet = workbook.create_sheet(sheet_name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def expect_error(file_name: str, content: bytes, contains: str, sheet: str | None = None) -> FileLoadError:
    with pytest.raises(FileLoadError) as caught:
        load_file(file_name, content, sheet)
    assert contains.lower() in caught.value.message.lower(), caught.value.message
    assert caught.value.hint, "every error should tell the user what to do"
    return caught.value


# ---------------------------------------------------------------------------
# CSV uploads
# ---------------------------------------------------------------------------
def test_csv_basic_upload(tmp_path):
    path = write_temp_file(tmp_path, "orders.csv", b"id,amount,date\n001,10.50,2025-01-01\n002,7,2025-01-02\n")
    loaded = load_file(path.name, path.read_bytes())
    assert loaded.file_type == "csv"
    assert (loaded.row_count, loaded.column_count) == (2, 3)
    assert list(loaded.data.columns) == ["id", "amount", "date"]
    assert loaded.file_size_bytes == path.stat().st_size


def test_csv_values_are_kept_exactly_as_written():
    loaded = load_file("ids.csv", b"code,price\n00123,10.50\n,3.00\n")
    assert loaded.data.loc[0, "code"] == "00123"   # leading zeros kept
    assert loaded.data.loc[0, "price"] == "10.50"  # trailing zero kept
    assert loaded.data.loc[1, "code"] == ""        # blank stays blank


def test_csv_with_semicolons_and_utf8_bom():
    content = "﻿name;city\nAna;Zürich\nBo;Köln\n".encode("utf-8")
    loaded = load_file("people.csv", content)
    assert loaded.delimiter == ";"
    assert loaded.encoding == "UTF-8"
    assert list(loaded.data.columns) == ["name", "city"]  # BOM not part of the heading
    assert loaded.data.loc[0, "city"] == "Zürich"


def test_csv_saved_by_excel_in_windows_encoding():
    loaded = load_file("legacy.csv", "item,price\nCafé,2.50\n".encode("cp1252"))
    assert loaded.encoding == "Windows-1252"
    assert loaded.data.loc[0, "item"] == "Café"


def test_csv_with_tabs_and_quoted_commas():
    loaded = load_file("tabbed.csv", b"product\tnote\nMug\t\"red, large\"\n")
    assert loaded.delimiter == "\t"
    assert loaded.data.loc[0, "note"] == "red, large"


def test_csv_ignores_completely_empty_rows_and_reports_it():
    loaded = load_file("gaps.csv", b"a,b\n1,2\n,\n3,4\n")
    assert loaded.row_count == 2
    assert any("empty row" in note for note in loaded.notes)


def test_csv_with_ragged_rows_gives_clear_error():
    error = expect_error("ragged.csv", b"a,b\n1,2\n3,4,5\n", "different number of values")
    assert "line 3" in error.hint


def test_original_file_is_not_modified(tmp_path):
    path = write_temp_file(tmp_path, "keep.csv", b"a,a,\n1,2,3\n")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    load_file(path.name, path.read_bytes())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# ---------------------------------------------------------------------------
# Excel uploads and worksheets
# ---------------------------------------------------------------------------
def test_excel_single_sheet(tmp_path):
    content = make_workbook({"Data": [["Date", "Amount"], [datetime(2025, 3, 1), 12.5], [datetime(2025, 3, 2), 8]]})
    path = write_temp_file(tmp_path, "book.xlsx", content)
    loaded = load_file(path.name, path.read_bytes())
    assert loaded.file_type == "xlsx"
    assert loaded.sheet_name == "Data"
    assert (loaded.row_count, loaded.column_count) == (2, 2)


def test_excel_lists_all_sheets_and_loads_the_chosen_one():
    content = make_workbook({
        "Sales": [["order", "total"], ["A", 1], ["B", 2], ["C", 3]],
        "Costs": [["item", "cost", "dept"], ["Rent", 900, "Ops"]],
        "Notes": [["text"], ["hello"]],
    })
    assert list_sheets(content) == ["Sales", "Costs", "Notes"]
    first = load_file("multi.xlsx", content)
    assert first.sheet_name == "Sales" and first.row_count == 3
    second = load_file("multi.xlsx", content, "Costs")
    assert second.sheet_name == "Costs"
    assert list(second.data.columns) == ["item", "cost", "dept"]
    assert second.sheet_names == ["Sales", "Costs", "Notes"]


def test_excel_missing_sheet_name_gives_clear_error():
    content = make_workbook({"Sales": [["a"], [1]]})
    error = expect_error("book.xlsx", content, "not found", sheet="Nope")
    assert "Sales" in error.hint


def test_excel_empty_sheet_does_not_block_other_sheets():
    content = make_workbook({"Blank": [], "Data": [["x"], [1]]})
    expect_error("book.xlsx", content, "empty", sheet="Blank")
    assert load_file("book.xlsx", content, "Data").row_count == 1


def test_excel_heading_row_below_blank_rows():
    content = make_workbook({"S": [[None, None], [None, None], ["name", "qty"], ["pen", 3]]})
    loaded = load_file("offset.xlsx", content)
    assert list(loaded.data.columns) == ["name", "qty"]
    assert any("above the column headings" in note for note in loaded.notes)


def test_excel_numeric_headings_become_text():
    content = make_workbook({"S": [["Region", 2025, 2026], ["North", 10, 12]]})
    assert list(load_file("years.xlsx", content).data.columns) == ["Region", "2025", "2026"]


# ---------------------------------------------------------------------------
# Empty, invalid, unsupported and oversized files
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["empty.csv", "empty.xlsx"])
def test_zero_byte_file(name):
    expect_error(name, b"", "empty")


def test_csv_with_only_blank_lines():
    expect_error("blank.csv", b"\r\n  \n\n", "empty")


def test_csv_with_headings_but_no_rows():
    expect_error("headers.csv", b"a,b,c\n", "no data rows")


def test_excel_with_headings_but_no_rows():
    expect_error("headers.xlsx", make_workbook({"S": [["a", "b"]]}), "no data rows")


def test_corrupted_excel_file(tmp_path):
    path = write_temp_file(tmp_path, "broken.xlsx", b"this is not really a spreadsheet")
    expect_error(path.name, path.read_bytes(), "can't be opened as an excel workbook")


def test_truncated_excel_file():
    content = make_workbook({"S": [["a"], [1]]})
    expect_error("cut.xlsx", content[: len(content) // 2], "can't be opened as an excel workbook")


def test_excel_file_renamed_to_csv():
    expect_error("really_excel.csv", make_workbook({"S": [["a"], [1]]}), "looks like an excel workbook")


def test_binary_file_renamed_to_csv():
    expect_error("image.csv", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00", "doesn't look like a csv")


@pytest.mark.parametrize("name, contains", [
    ("report.pdf", "not supported"),
    ("old.xls", ".xls"),
    ("macro.xlsm", "not supported"),
    ("noextension", "not supported"),
])
def test_unsupported_file_types(name, contains):
    expect_error(name, b"some content", contains)


def test_file_type_check_ignores_capital_letters():
    assert get_file_type("DATA.CSV") == "csv"
    assert get_file_type("Book.XLSX") == "xlsx"


def test_file_too_large():
    with pytest.raises(FileLoadError) as caught:
        check_file_size(int(MAX_UPLOAD_MB * 1024 * 1024) + 1)
    assert "too large" in caught.value.message
    check_file_size(int(MAX_UPLOAD_MB * 1024 * 1024))  # exactly at the limit is fine


def test_upload_limit_matches_streamlit_setting():
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["maxUploadSize"] == MAX_UPLOAD_MB


def test_format_file_size():
    assert format_file_size(512) == "512 bytes"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(5 * 1024 * 1024) == "5.0 MB"


# ---------------------------------------------------------------------------
# Duplicate and blank column names
# ---------------------------------------------------------------------------
def test_unique_names_for_duplicate_and_blank_headings():
    names, renamed = make_unique_column_names(["Amount", "Amount", "", "Amount (2)", None])
    assert names == ["Amount", "Amount (3)", "Column 3", "Amount (2)", "Column 5"]
    assert len(set(names)) == len(names)
    assert [(r.position, r.original, r.new_name, r.reason) for r in renamed] == [
        (2, "Amount", "Amount (3)", "duplicate heading"),
        (3, "", "Column 3", "blank heading"),
        (5, "", "Column 5", "blank heading"),
    ]


def test_generated_name_never_clashes_with_real_heading():
    names, _ = make_unique_column_names(["Column 2", ""])
    assert names == ["Column 2", "Column 2 (2)"]


def test_csv_with_duplicate_headings_keeps_every_column():
    loaded = load_file("dupes.csv", b"total,total,total\n1,2,3\n")
    assert list(loaded.data.columns) == ["total", "total (2)", "total (3)"]
    assert loaded.data.iloc[0].tolist() == ["1", "2", "3"]
    assert len(loaded.renamed_columns) == 2


def test_excel_with_duplicate_and_blank_headings():
    content = make_workbook({"S": [["Name", "Name", None, "Qty"], ["a", "b", "c", 1]]})
    loaded = load_file("dupes.xlsx", content)
    assert list(loaded.data.columns) == ["Name", "Name (2)", "Column 3", "Qty"]


# ---------------------------------------------------------------------------
# Preview and column types
# ---------------------------------------------------------------------------
def big_csv(rows: int) -> bytes:
    lines = ["n,value"] + [f"{i},{i * 1.5}" for i in range(1, rows + 1)]
    return ("\n".join(lines) + "\n").encode()


def test_preview_shows_first_100_rows_only():
    loaded = load_file("big.csv", big_csv(250))
    preview = make_preview(loaded.data)
    assert len(preview) == PREVIEW_ROWS == 100
    assert preview.iloc[0]["n"] == "1" and preview.iloc[-1]["n"] == "100"
    assert preview.index[0] == 1 and preview.index[-1] == 100  # rows numbered from 1


def test_preview_of_small_file_shows_all_rows():
    assert len(make_preview(load_file("small.csv", big_csv(30)).data)) == 30


def test_preview_does_not_change_the_loaded_data():
    content = make_workbook({"S": [["when", "qty"], [datetime(2025, 1, 5), 2], [None, 3.0]]})
    loaded = load_file("book.xlsx", content)
    before = loaded.data.copy()
    preview = make_preview(loaded.data)
    pd.testing.assert_frame_equal(loaded.data, before)
    assert preview["when"].tolist() == ["2025-01-05", ""]  # dates shown without a time
    assert preview["qty"].tolist() == ["2", "3"]


@pytest.mark.parametrize("values, expected_type", [
    (["1", "2", "30"], TYPE_WHOLE_NUMBER),
    (["1.5", "2", "3.25"], TYPE_DECIMAL),
    (["2025-01-01", "01/02/2025", "03-Mar-2025"], TYPE_DATE),
    (["yes", "No", "TRUE"], TYPE_TRUE_FALSE),
    (["North", "South", "12"], TYPE_TEXT),
    ([datetime(2025, 1, 1), datetime(2025, 2, 1)], TYPE_DATE),
    ([1, 2.5, 3], TYPE_DECIMAL),
    ([True, False], TYPE_TRUE_FALSE),
    ([], TYPE_EMPTY),
])
def test_detect_column_type(values, expected_type):
    detected, _ = detect_column_type(pd.Series(values, dtype=object))
    assert detected == expected_type


def test_type_share_reveals_bad_values():
    detected, share = detect_column_type(pd.Series(["1", "2", "3", "N/A"], dtype=object))
    assert detected == TYPE_WHOLE_NUMBER
    assert share == 0.75
    assert format_share(0.9996) == "99.9%"  # never rounded up to 100%
    assert format_share(1.0) == "100%"


def test_describe_columns_counts_filled_and_empty():
    loaded = load_file("mix.csv", b"name,qty,notes\nA,1,\nB,,\nC,3,\n")
    table = describe_columns(loaded.data).set_index("Column")
    assert table.loc["qty", "Filled"] == 2 and table.loc["qty", "Empty"] == 1
    assert table.loc["notes", "Detected type"] == TYPE_EMPTY
    assert table.loc["name", "Example values"] == "A, B, C"


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sample", SAMPLE_DATASETS, ids=lambda s: s.key)
def test_every_sample_dataset_loads(sample):
    name, content = read_sample(sample.key)
    loaded = load_file(name, content)
    assert loaded.row_count > 0
    assert not loaded.renamed_columns


def test_sample_workbook_has_three_sheets():
    name, content = read_sample(SAMPLE_WORKBOOK_KEY)
    assert name.endswith(".xlsx")
    assert list_sheets(content) == ["Sales", "Expenses", "About"]
    assert load_file(name, content, "Expenses").row_count == 499
    sales_types = describe_columns(load_file(name, content, "Sales").data).set_index("Column")["Detected type"]
    assert sales_types["order_date"] == TYPE_DATE
    assert sales_types["quantity"] == TYPE_WHOLE_NUMBER


def test_messy_sample_shows_imperfect_type_matches():
    name, content = read_sample("sales_messy")
    table = describe_columns(load_file(name, content).data).set_index("Column")
    assert table.loc["unit_price", "Values matching type"] != "100%"  # "N/A", "$24.99" ...
    assert table.loc["order_date", "Values matching type"] != "100%"  # impossible dates
    assert table.loc["customer_id", "Empty"] == 25


def test_loading_samples_does_not_change_sample_files():
    recorded = json.loads((SAMPLE_DIR / "checksums.json").read_text(encoding="utf-8"))
    for sample in SAMPLE_DATASETS:
        load_file(*read_sample(sample.key))
    for file_name, expected in recorded.items():
        assert hashlib.sha256((SAMPLE_DIR / file_name).read_bytes()).hexdigest() == expected
    assert not any(SAMPLE_DIR.glob("*.xlsx")), "the sample workbook must not be saved to disk"
