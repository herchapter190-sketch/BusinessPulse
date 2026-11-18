"""Access to the fictional sample datasets in data/sample/.

Sample files are only ever read. The multi-sheet Excel workbook is built in
memory each time it is needed and is never saved to disk.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"
SAMPLE_WORKBOOK_KEY = "sample_workbook"
SAMPLE_WORKBOOK_NAME = "businesspulse_sample_workbook.xlsx"


@dataclass(frozen=True)
class SampleDataset:
    key: str
    label: str
    file_name: str  # file in data/sample, or SAMPLE_WORKBOOK_NAME for the generated workbook
    description: str


SAMPLE_DATASETS = [
    SampleDataset("sales_clean", "Sales (clean CSV)", "sales_clean.csv",
                  "7,990 fictional orders over 18 months. No data problems."),
    SampleDataset("expenses_clean", "Expenses (clean CSV)", "expenses_clean.csv",
                  "499 fictional business expenses over the same 18 months."),
    SampleDataset("sales_messy", "Sales with data problems (CSV)", "sales_messy.csv",
                  "The sales data with 339 deliberate problems: blanks, duplicates, odd dates and more."),
    SampleDataset(SAMPLE_WORKBOOK_KEY, "Sales + expenses workbook (Excel, 3 sheets)", SAMPLE_WORKBOOK_NAME,
                  "An Excel workbook with Sales, Expenses and About sheets, for trying worksheet selection."),
]
SAMPLES_BY_KEY = {sample.key: sample for sample in SAMPLE_DATASETS}


def build_sample_workbook() -> bytes:
    """Build the 3-sheet sample workbook in memory and return its bytes."""
    sales = pd.read_csv(SAMPLE_DIR / "sales_clean.csv", parse_dates=["order_date"])
    expenses = pd.read_csv(SAMPLE_DIR / "expenses_clean.csv", parse_dates=["expense_date"])
    about = pd.DataFrame({
        "Item": ["Contents", "Data", "Period", "Source"],
        "Details": [
            "Sales and Expenses sheets for a fictional online shop",
            "All figures are fictional and for demonstration only",
            "January 2025 to June 2026",
            "Built from the BusinessPulse sample CSV files",
        ],
    })
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl", date_format="YYYY-MM-DD",
                        datetime_format="YYYY-MM-DD") as writer:
        sales.to_excel(writer, sheet_name="Sales", index=False)
        expenses.to_excel(writer, sheet_name="Expenses", index=False)
        about.to_excel(writer, sheet_name="About", index=False)
    return buffer.getvalue()


def read_sample(key: str) -> tuple[str, bytes]:
    """Return (file name, file bytes) for a sample dataset. Files are read, never changed."""
    if key not in SAMPLES_BY_KEY:
        raise KeyError(f"Unknown sample dataset: {key}")
    sample = SAMPLES_BY_KEY[key]
    if key == SAMPLE_WORKBOOK_KEY:
        return sample.file_name, build_sample_workbook()
    return sample.file_name, (SAMPLE_DIR / sample.file_name).read_bytes()
