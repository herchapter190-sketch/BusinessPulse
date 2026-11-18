"""Generate fictional sample datasets for BusinessPulse.

Creates these files (default folder: data/sample/):
    sales_clean.csv            clean sales transactions
    expenses_clean.csv         clean business expenses
    sales_messy.csv            copy of the sales data with deliberate quality problems
    sales_messy_issue_log.csv  row-by-row record of every problem that was introduced
    checksums.json             SHA-256 fingerprints used to detect accidental edits

All data is fictional. A fixed random seed is used, so running the script again
produces byte-identical files.

Usage (run from the project folder):
    .\\.venv\\Scripts\\python.exe tools\\generate_sample_data.py
    .\\.venv\\Scripts\\python.exe tools\\generate_sample_data.py --output-dir .tmp\\sample_check
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "sample"

# ---------------------------------------------------------------------------
# Fixed settings — changing any of these changes the generated data.
# ---------------------------------------------------------------------------
SEED = 20250101
START_DATE = date(2025, 1, 1)
END_DATE = date(2026, 6, 30)  # 18 full months
PRICE_INCREASE_DATE = date(2026, 1, 1)  # yearly price-list update (+5%)

SALES_FILE = "sales_clean.csv"
EXPENSES_FILE = "expenses_clean.csv"
MESSY_SALES_FILE = "sales_messy.csv"
ISSUE_LOG_FILE = "sales_messy_issue_log.csv"
CHECKSUMS_FILE = "checksums.json"

SALES_COLUMNS = [
    "order_id", "order_date", "customer_id", "product_name", "category",
    "quantity", "unit_price", "total_sales", "region", "payment_method", "order_status",
]
EXPENSE_COLUMNS = [
    "expense_id", "expense_date", "category", "description",
    "amount", "department", "payment_method",
]
ISSUE_LOG_COLUMNS = [
    "messy_row_number", "order_id", "column", "issue_type", "detail",
    "original_value", "messy_value",
]

# (product name, category, base unit price, relative popularity)
PRODUCTS = [
    ("Wireless Mouse", "Electronics", 24.99, 9),
    ("Bluetooth Speaker", "Electronics", 59.99, 6),
    ("USB-C Charging Hub", "Electronics", 39.99, 6),
    ("Noise-Cancelling Headphones", "Electronics", 149.99, 3),
    ("Portable Power Bank", "Electronics", 34.99, 7),
    ("Ceramic Mug Set", "Home & Kitchen", 22.50, 7),
    ("Stainless Steel Water Bottle", "Home & Kitchen", 18.99, 9),
    ("Non-Stick Frying Pan", "Home & Kitchen", 44.00, 5),
    ("Bamboo Cutting Board", "Home & Kitchen", 27.50, 5),
    ("Hardcover Notebook", "Office Supplies", 12.99, 10),
    ("Gel Pen Pack", "Office Supplies", 8.49, 10),
    ("Desk Organizer", "Office Supplies", 29.99, 5),
    ("Ergonomic Office Chair", "Office Supplies", 219.00, 2),
    ("Yoga Mat", "Sports & Outdoors", 32.00, 6),
    ("Resistance Band Set", "Sports & Outdoors", 24.00, 6),
    ("Camping Lantern", "Sports & Outdoors", 38.50, 4),
    ("Hiking Daypack", "Sports & Outdoors", 64.99, 3),
    ("Natural Lip Balm Set", "Beauty & Personal Care", 9.99, 8),
    ("Moisturizing Hand Cream", "Beauty & Personal Care", 14.50, 8),
    ("Bamboo Hair Brush", "Beauty & Personal Care", 16.99, 5),
    ("Aromatherapy Diffuser", "Beauty & Personal Care", 42.00, 4),
]
CATEGORIES = sorted({category for _, category, _, _ in PRODUCTS})
REGIONS = {"North": 24, "South": 18, "East": 22, "West": 21, "Central": 15}
SALES_PAYMENT_METHODS = {"Credit Card": 40, "Debit Card": 25, "Digital Wallet": 20, "Bank Transfer": 10, "Cash": 5}
CARD_PAYMENT_METHODS = {"Credit Card", "Debit Card", "Digital Wallet"}
ORDER_STATUSES = ["Completed", "Returned", "Cancelled", "Pending"]

NUMBER_OF_CUSTOMERS = 450
BASE_ORDERS_PER_MONTH = 400
MONTHLY_GROWTH = 0.015  # +1.5% more orders each month
# Relative order volume by calendar month (holiday peak in Nov/Dec).
SEASONALITY = {1: 0.85, 2: 0.80, 3: 0.95, 4: 1.00, 5: 1.00, 6: 0.95,
               7: 0.95, 8: 1.00, 9: 1.00, 10: 1.05, 11: 1.30, 12: 1.50}
HOLIDAY_ELECTRONICS_BOOST = 1.6  # electronics sell more in Nov/Dec

EXPENSE_CATEGORIES = [
    "Bank & Payment Fees", "Equipment", "Insurance", "Marketing", "Office Supplies",
    "Packaging", "Professional Services", "Rent", "Salaries", "Shipping & Logistics",
    "Software Subscriptions", "Travel", "Utilities",
]
DEPARTMENTS = ["Finance", "Human Resources", "IT", "Marketing", "Operations", "Sales"]
EXPENSE_PAYMENT_METHODS = ["Bank Transfer", "Cash", "Company Card", "Direct Debit"]

MONTH_ABBREVIATIONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def iter_months(start: date = START_DATE, end: date = END_DATE):
    """Yield (year, month) pairs from start to end inclusive."""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def unit_price_on(base_price: float, order_date: date) -> float:
    """Price list in force on a date. From 2026 prices rise ~5%, ending in .49/.99."""
    if order_date < PRICE_INCREASE_DATE:
        return base_price
    return round(round(base_price * 1.05 * 2) / 2 - 0.01, 2)


def money(value: float) -> str:
    return f"{value:.2f}"


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
def make_customers(rng: random.Random) -> list[dict]:
    """Fictional customers: an ID, a home region, a preferred payment method,
    and an activity weight (a few customers order much more often than others)."""
    customers = []
    for number in range(1, NUMBER_OF_CUSTOMERS + 1):
        customers.append({
            "customer_id": f"CUST-{number:04d}",
            "region": rng.choices(list(REGIONS), weights=list(REGIONS.values()))[0],
            "preferred_payment": rng.choices(list(SALES_PAYMENT_METHODS),
                                             weights=list(SALES_PAYMENT_METHODS.values()))[0],
            "activity": rng.paretovariate(1.5),
        })
    return customers


def make_order(rng: random.Random, order_date: date, customers: list[dict],
               customer_weights: list[float]) -> dict:
    holiday = order_date.month in (11, 12)
    product_weights = [
        popularity * (HOLIDAY_ELECTRONICS_BOOST if holiday and category == "Electronics" else 1)
        for _, category, _, popularity in PRODUCTS
    ]
    name, category, base_price, _ = rng.choices(PRODUCTS, weights=product_weights)[0]
    customer = rng.choices(customers, weights=customer_weights)[0]

    if rng.random() < 0.75:
        payment = customer["preferred_payment"]
    else:
        payment = rng.choices(list(SALES_PAYMENT_METHODS), weights=list(SALES_PAYMENT_METHODS.values()))[0]

    if base_price >= 100:
        quantity = rng.choices([1, 2], weights=[90, 10])[0]
    else:
        quantity = rng.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 9, 6])[0]

    unit_price = unit_price_on(base_price, order_date)

    # Orders in the final week may not be finished yet.
    if order_date > END_DATE - timedelta(days=7) and rng.random() < 0.45:
        status = "Pending"
    else:
        roll = rng.random()
        status = "Returned" if roll < 0.04 else "Cancelled" if roll < 0.07 else "Completed"

    return {
        "order_date": order_date,
        "customer_id": customer["customer_id"],
        "product_name": name,
        "category": category,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_sales": round(quantity * unit_price, 2),
        "region": customer["region"],
        "payment_method": payment,
        "order_status": status,
    }


def generate_sales(rng: random.Random) -> list[dict]:
    customers = make_customers(rng)
    customer_weights = [c["activity"] for c in customers]
    orders = []
    for month_index, (year, month) in enumerate(iter_months()):
        expected = BASE_ORDERS_PER_MONTH * (1 + MONTHLY_GROWTH * month_index) * SEASONALITY[month]
        order_count = round(expected * rng.uniform(0.92, 1.08))
        first_day, last_day = month_bounds(year, month)
        span = (last_day - first_day).days + 1
        for _ in range(order_count):
            order_date = first_day + timedelta(days=rng.randrange(span))
            orders.append(make_order(rng, order_date, customers, customer_weights))

    orders.sort(key=lambda order: order["order_date"])  # stable sort keeps it deterministic
    for number, order in enumerate(orders, start=1):
        order["order_id"] = f"ORD-{100000 + number}"
    return orders


def format_sales_row(order: dict) -> dict:
    return {
        "order_id": order["order_id"],
        "order_date": order["order_date"].isoformat(),
        "customer_id": order["customer_id"],
        "product_name": order["product_name"],
        "category": order["category"],
        "quantity": str(order["quantity"]),
        "unit_price": money(order["unit_price"]),
        "total_sales": money(order["total_sales"]),
        "region": order["region"],
        "payment_method": order["payment_method"],
        "order_status": order["order_status"],
    }


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
def generate_expenses(rng: random.Random, sales: list[dict]) -> list[dict]:
    """Expenses for the same period. Shipping, packaging, marketing and card fees
    are derived from the actual sales so the two datasets are consistent."""
    revenue_by_day: dict[date, float] = defaultdict(float)
    revenue_by_month: dict[tuple[int, int], float] = defaultdict(float)
    card_revenue_by_month: dict[tuple[int, int], float] = defaultdict(float)
    for order in sales:
        if order["order_status"] != "Completed":
            continue
        key = (order["order_date"].year, order["order_date"].month)
        revenue_by_day[order["order_date"]] += order["total_sales"]
        revenue_by_month[key] += order["total_sales"]
        if order["payment_method"] in CARD_PAYMENT_METHODS:
            card_revenue_by_month[key] += order["total_sales"]

    items: list[tuple] = []

    def add(day: date, category: str, description: str, amount: float, department: str, payment: str):
        items.append((day, category, description, round(amount, 2), department, payment))

    salaries = {"Sales": 3000, "Operations": 2900, "Marketing": 2100,
                "Finance": 1900, "IT": 1700, "Human Resources": 1400}
    electricity_season = {1: 1.35, 2: 1.30, 3: 1.10, 4: 0.95, 5: 0.90, 6: 1.00,
                          7: 1.15, 8: 1.15, 9: 0.95, 10: 0.95, 11: 1.10, 12: 1.30}
    software = [
        ("Accounting software subscription", 59.00, "Finance"),
        ("Cloud storage plan", 24.00, "IT"),
        ("Online store platform subscription", 129.00, "IT"),
        ("Email marketing tool subscription", 45.00, "Marketing"),
    ]
    one_off_equipment = [
        (date(2025, 3, 14), "Laptop replacement", 1250.00, "IT"),
        (date(2025, 6, 9), "Warehouse shelving units", 1800.00, "Operations"),
        (date(2026, 2, 17), "Laptop replacement", 1290.00, "IT"),
        (date(2026, 5, 6), "Label printer", 340.00, "Operations"),
    ]
    office_supply_items = ["Printer paper and toner", "Stationery restock",
                           "Cleaning supplies", "Breakroom supplies"]
    travel_items = ["Client visit travel", "Supplier meeting travel", "Trade fair attendance travel"]

    for year, month in iter_months():
        key = (year, month)
        revenue = revenue_by_month[key]
        raise_factor = 1.03 if year >= 2026 else 1.0  # annual pay rise

        add(date(year, month, 1), "Rent", "Monthly office and warehouse rent",
            2500.00 if year >= 2026 else 2400.00, "Operations", "Bank Transfer")
        for department, salary in salaries.items():
            add(date(year, month, 28), "Salaries", f"{department} team salaries",
                salary * raise_factor, department, "Bank Transfer")

        add(date(year, month, 10), "Utilities", "Electricity bill",
            420 * electricity_season[month] * rng.uniform(0.95, 1.05), "Operations", "Direct Debit")
        add(date(year, month, 10), "Utilities", "Water bill",
            65 * rng.uniform(0.9, 1.1), "Operations", "Direct Debit")
        add(date(year, month, 12), "Utilities", "Business internet service", 89.99, "IT", "Direct Debit")

        for description, amount, department in software:
            add(date(year, month, 5), "Software Subscriptions", description, amount, department, "Company Card")

        if month in (1, 4, 7, 10):
            add(date(year, month, 15), "Insurance", "Quarterly business insurance premium",
                1150.00, "Finance", "Bank Transfer")
            add(date(year, month, 20), "Professional Services", "Quarterly accounting review",
                750.00, "Finance", "Bank Transfer")
        add(date(year, month, 25), "Professional Services", "Website maintenance retainer",
            280.00, "IT", "Bank Transfer")

        # Marketing: ad budget ~5% of the month's revenue, boosted before the holidays.
        ad_share = 0.08 if month in (10, 11) else 0.05
        add(date(year, month, 3), "Marketing", "Online advertising campaign",
            revenue * ad_share * rng.uniform(0.9, 1.1), "Marketing", "Company Card")
        add(date(year, month, 18), "Marketing", "Social media content production",
            350.00, "Marketing", "Company Card")
        if key == (2025, 9):
            add(date(2025, 9, 22), "Marketing", "Trade show booth rental", 2400.00, "Marketing", "Bank Transfer")

        for day in (8, 22):
            add(date(year, month, day), "Packaging", "Packaging materials order",
                revenue * 0.012 * rng.uniform(0.9, 1.1), "Operations", "Bank Transfer")

        for _ in range(rng.choice([1, 2])):
            payment = "Cash" if rng.random() < 0.3 else "Company Card"
            add(date(year, month, rng.randint(2, 27)), "Office Supplies", rng.choice(office_supply_items),
                rng.uniform(45, 180), rng.choice(["Finance", "Operations", "Human Resources"]), payment)

        for _ in range(rng.choice([0, 1, 1, 2])):
            add(date(year, month, rng.randint(2, 27)), "Travel", rng.choice(travel_items),
                rng.uniform(120, 650), "Sales", "Company Card")

        _, last_day = month_bounds(year, month)
        add(last_day, "Bank & Payment Fees", "Card payment processing fees",
            card_revenue_by_month[key] * 0.022, "Finance", "Direct Debit")

    for day, description, amount, department in one_off_equipment:
        add(day, "Equipment", description, amount, department, "Company Card")

    # Shipping: weekly carrier invoice every Monday, ~7.5% of the previous week's revenue.
    monday = START_DATE + timedelta(days=(7 - START_DATE.weekday()) % 7)
    while monday <= END_DATE:
        week_revenue = sum(revenue_by_day[monday - timedelta(days=offset)] for offset in range(1, 8))
        if week_revenue > 0:
            add(monday, "Shipping & Logistics", "Weekly carrier invoice",
                week_revenue * 0.075 * rng.uniform(0.95, 1.05), "Operations", "Bank Transfer")
        monday += timedelta(days=7)

    items.sort(key=lambda item: (item[0], item[1], item[2], item[4]))
    expenses = []
    for number, (day, category, description, amount, department, payment) in enumerate(items, start=1):
        expenses.append({
            "expense_id": f"EXP-{number:05d}",
            "expense_date": day.isoformat(),
            "category": category,
            "description": description,
            "amount": money(amount),
            "department": department,
            "payment_method": payment,
        })
    return expenses


# ---------------------------------------------------------------------------
# Messy sales
# ---------------------------------------------------------------------------
INCORRECT_CATEGORY_NAMES = {
    "Electronics": ["Electronic", "Electronis"],
    "Home & Kitchen": ["Home and Kitchen", "Home & Kitchn"],
    "Office Supplies": ["Office Suplies", "Office Supply"],
    "Sports & Outdoors": ["Sport & Outdoors", "Sports and Outdoor"],
    "Beauty & Personal Care": ["Beauty", "Beauty & Personal-Care"],
}


def make_messy_sales(rng: random.Random, clean_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (messy_rows, issue_log). Works on copies; clean_rows is not changed.

    Each problem is applied to a different row, so every affected row has exactly
    one deliberate problem and can be traced in the issue log.
    """
    entries = [{"data": dict(row), "issues": []} for row in clean_rows]
    unused = list(range(len(entries)))
    rng.shuffle(unused)

    def take(count: int, condition=lambda data: True) -> list[dict]:
        chosen = []
        for index in unused:
            if condition(entries[index]["data"]):
                chosen.append(index)
                if len(chosen) == count:
                    break
        for index in chosen:
            unused.remove(index)
        return [entries[index] for index in chosen]

    def change(entry: dict, column: str, issue_type: str, detail: str, new_value: str):
        original = entry["data"][column]
        entry["data"][column] = new_value
        entry["issues"].append((column, issue_type, detail, original, new_value))

    # 1. Missing values
    for column, count in [("customer_id", 25), ("region", 15), ("payment_method", 10),
                          ("quantity", 8), ("order_date", 5)]:
        for entry in take(count):
            change(entry, column, "missing_value", "blank cell", "")

    # 2. Inconsistent date formats (same date, different written format)
    def reformat(entry: dict, style: str):
        day = date.fromisoformat(entry["data"]["order_date"])
        if style == "DD/MM/YYYY":
            text = f"{day.day:02d}/{day.month:02d}/{day.year}"
        elif style == "YYYY/MM/DD":
            text = f"{day.year}/{day.month:02d}/{day.day:02d}"
        else:  # DD-Mon-YYYY
            text = f"{day.day:02d}-{MONTH_ABBREVIATIONS[day.month - 1]}-{day.year}"
        change(entry, "order_date", "inconsistent_date_format", style, text)

    for style, count in [("DD/MM/YYYY", 40), ("YYYY/MM/DD", 25), ("DD-Mon-YYYY", 25)]:
        for entry in take(count):
            reformat(entry, style)

    # 3. Extra spaces in text fields
    for column, count in [("product_name", 20), ("category", 15), ("region", 15)]:
        for entry in take(count):
            value = entry["data"][column]
            padded = rng.choice([f"  {value}", f"{value}   ", f" {value} "])
            change(entry, column, "extra_whitespace", "leading/trailing spaces", padded)

    # 4. Inconsistent capitalization
    for column, count in [("region", 20), ("order_status", 15), ("payment_method", 15)]:
        for entry in take(count):
            value = entry["data"][column]
            change(entry, column, "inconsistent_capitalization", "case changed",
                   rng.choice([value.lower(), value.upper()]))

    # 5. Invalid numerical values
    for entry in take(6, lambda data: data["order_status"] == "Completed"):
        change(entry, "quantity", "invalid_number", "negative quantity", f"-{entry['data']['quantity']}")
    for entry in take(5):
        change(entry, "quantity", "invalid_number", "zero quantity", "0")
    for entry in take(6):
        change(entry, "unit_price", "invalid_number", "text instead of a number",
               rng.choice(["N/A", "unknown", "TBD"]))
    for entry in take(4):
        change(entry, "unit_price", "invalid_number", "currency symbol in number",
               f"${entry['data']['unit_price']}")
    for entry in take(3):
        change(entry, "quantity", "implausible_value", "unrealistically large quantity",
               str(rng.choice([250, 500, 999])))

    # 6. Incorrect category names
    for entry in take(20):
        category = entry["data"]["category"]
        change(entry, "category", "incorrect_category", "misspelled or non-standard name",
               rng.choice(INCORRECT_CATEGORY_NAMES[category]))

    # 7. Invalid records
    for entry in take(4):
        day = date.fromisoformat(entry["data"]["order_date"])
        change(entry, "order_date", "future_date", "date after the data period",
               day.replace(year=2027).isoformat())
    for impossible in ["2025-02-30", "2025-04-31", "2025-13-05"]:
        entry = take(1)[0]
        change(entry, "order_date", "impossible_date", "date that does not exist", impossible)
    for entry in take(4):
        wrong_total = float(entry["data"]["total_sales"]) * rng.choice([10, 1.1, 0.5])
        change(entry, "total_sales", "total_mismatch", "total_sales does not equal quantity x unit_price",
               money(wrong_total))
    for entry in take(3):
        change(entry, "order_status", "unknown_status", "status not in the allowed list",
               rng.choice(["Unknown", "???", "TBD"]))
    for entry in take(3):
        change(entry, "order_id", "missing_order_id", "blank order ID", "")

    # 8. Duplicate order IDs with conflicting details (different orders share one ID)
    targets = take(5)
    for target, entry in zip(targets, take(5)):
        change(entry, "order_id", "duplicate_order_id", "order ID reused by a different order",
               target["data"]["order_id"])

    # 9. Exact duplicate rows (e.g. the same order exported twice), placed 1-5 rows later
    copies_after: dict[int, list[dict]] = defaultdict(list)
    position_of = {id(entry): position for position, entry in enumerate(entries)}
    for original in take(20):
        copy = {"data": dict(original["data"]), "issues": []}
        copy["issues"].append(("(entire row)", "exact_duplicate", "identical copy of another row", "", ""))
        insert_after = min(position_of[id(original)] + rng.randint(1, 5), len(entries) - 1)
        copies_after[insert_after].append(copy)

    final_entries = []
    for position, entry in enumerate(entries):
        final_entries.append(entry)
        final_entries.extend(copies_after.get(position, []))

    messy_rows, issue_log = [], []
    for row_number, entry in enumerate(final_entries, start=1):
        messy_rows.append(entry["data"])
        for column, issue_type, detail, original, new_value in entry["issues"]:
            issue_log.append({
                "messy_row_number": str(row_number),
                "order_id": entry["data"]["order_id"],
                "column": column,
                "issue_type": issue_type,
                "detail": detail,
                "original_value": original,
                "messy_value": new_value,
            })
    return messy_rows, issue_log


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    # "\n" line endings and UTF-8 keep the files byte-identical on every computer.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_all(output_dir: Path) -> dict:
    """Generate every sample file into output_dir and return a short summary."""
    output_dir.mkdir(parents=True, exist_ok=True)

    sales = generate_sales(random.Random(SEED))
    sales_rows = [format_sales_row(order) for order in sales]
    expense_rows = generate_expenses(random.Random(SEED + 1), sales)
    messy_rows, issue_log = make_messy_sales(random.Random(SEED + 2), sales_rows)

    write_csv(output_dir / SALES_FILE, SALES_COLUMNS, sales_rows)
    write_csv(output_dir / EXPENSES_FILE, EXPENSE_COLUMNS, expense_rows)
    write_csv(output_dir / MESSY_SALES_FILE, SALES_COLUMNS, messy_rows)
    write_csv(output_dir / ISSUE_LOG_FILE, ISSUE_LOG_COLUMNS, issue_log)

    checksums = {name: sha256_of(output_dir / name)
                 for name in (SALES_FILE, EXPENSES_FILE, MESSY_SALES_FILE, ISSUE_LOG_FILE)}
    (output_dir / CHECKSUMS_FILE).write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")

    return {
        "sales_rows": len(sales_rows),
        "expense_rows": len(expense_rows),
        "messy_rows": len(messy_rows),
        "issues_logged": len(issue_log),
        "sales_dates": (sales_rows[0]["order_date"], sales_rows[-1]["order_date"]),
        "expense_dates": (expense_rows[0]["expense_date"], expense_rows[-1]["expense_date"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate fictional BusinessPulse sample data.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="Folder to write the files to (default: data/sample).")
    args = parser.parse_args()

    summary = generate_all(args.output_dir)
    print(f"Sample data written to: {args.output_dir.resolve()}")
    print(f"  {SALES_FILE:<28}{summary['sales_rows']:>6} rows  "
          f"{summary['sales_dates'][0]} to {summary['sales_dates'][1]}")
    print(f"  {EXPENSES_FILE:<28}{summary['expense_rows']:>6} rows  "
          f"{summary['expense_dates'][0]} to {summary['expense_dates'][1]}")
    print(f"  {MESSY_SALES_FILE:<28}{summary['messy_rows']:>6} rows")
    print(f"  {ISSUE_LOG_FILE:<28}{summary['issues_logged']:>6} issues")


if __name__ == "__main__":
    main()
