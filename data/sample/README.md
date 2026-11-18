# BusinessPulse Sample Data

All data in this folder is **fictional**. It describes an imaginary small online
shop. There are no real people, companies or brands. Customers appear only as
IDs (`CUST-0001`), never as names or contact details.

The files are created by [`tools/generate_sample_data.py`](../../tools/generate_sample_data.py)
using a fixed random seed. Running the script again produces exactly the same files.

```powershell
.\.venv\Scripts\python.exe tools\generate_sample_data.py
```

| File | Rows | Period | Purpose |
|---|---|---|---|
| `sales_clean.csv` | 7,990 | 2025-01-01 to 2026-06-30 | Clean sales transactions |
| `expenses_clean.csv` | 499 | 2025-01-01 to 2026-06-30 | Clean business expenses |
| `sales_messy.csv` | 8,010 | same as sales | Sales copy with deliberate quality problems |
| `sales_messy_issue_log.csv` | 339 | — | Answer key: every problem introduced into the messy file |
| `checksums.json` | — | — | SHA-256 fingerprints that reveal any accidental edit |

Format: CSV, UTF-8, comma-separated, one header row, dates as `YYYY-MM-DD`,
money in the shop's currency with two decimals (no currency symbol).

---

## 1. `sales_clean.csv`

One row per order line (one product per order).

| Column | Type | Description |
|---|---|---|
| `order_id` | text | Unique ID, `ORD-100001` onwards, in date order |
| `order_date` | date | Date the order was placed |
| `customer_id` | text | Fictional customer ID (`CUST-0001` – `CUST-0450`) |
| `product_name` | text | One of 21 generic products |
| `category` | text | Electronics, Home & Kitchen, Office Supplies, Sports & Outdoors, Beauty & Personal Care |
| `quantity` | whole number | Units ordered, 1–5 |
| `unit_price` | number | Price per unit on the order date |
| `total_sales` | number | `quantity × unit_price` (the order's value) |
| `region` | text | North, South, East, West, Central |
| `payment_method` | text | Credit Card, Debit Card, Digital Wallet, Bank Transfer, Cash |
| `order_status` | text | Completed, Returned, Cancelled, Pending |

Built-in patterns (so the analysis has something real to find):

- **Growth:** order volume rises about 1.5% per month.
- **Seasonality:** quieter in January/February, with a holiday peak in November/December.
  Electronics sell more strongly during the holidays.
- **Price update:** prices rose about 5% on 2026-01-01, so each product has two prices.
- **Consistency:** a product always has the same category, and a customer always has
  the same region. Most customers use the same payment method each time.
- **Statuses:** about 4% Returned and 3% Cancelled. `Pending` appears only in the
  last week (2026-06-24 to 06-30), for orders that are not finished yet.

> **Note:** `total_sales` is the order's value whatever its status. Whether returned,
> cancelled or pending orders count as revenue is decided in the analysis stage.
> Counting only Completed orders, June 2026 looks lower because some of its
> last-week orders are still Pending.

## 2. `expenses_clean.csv`

| Column | Type | Description |
|---|---|---|
| `expense_id` | text | Unique ID, `EXP-00001` onwards, in date order |
| `expense_date` | date | Date the expense was paid |
| `category` | text | One of 13 expense categories (below) |
| `description` | text | What the expense was for |
| `amount` | number | Amount paid (always positive) |
| `department` | text | Finance, Human Resources, IT, Marketing, Operations, Sales |
| `payment_method` | text | Bank Transfer, Company Card, Direct Debit, Cash |

| Category | Pattern |
|---|---|
| Salaries | Six team payrolls on the 28th, with a 3% pay rise from 2026 |
| Rent | 1st of each month; goes up in 2026 |
| Utilities | Electricity (higher in winter and summer), water, internet |
| Software Subscriptions | Four fixed monthly subscriptions |
| Insurance | Quarterly premium (Jan, Apr, Jul, Oct) |
| Professional Services | Quarterly accounting review, plus a monthly website retainer |
| Marketing | Ad spend of about 5% of the month's sales (8% in Oct/Nov), monthly content production, and a one-off trade show booth in September 2025 |
| Shipping & Logistics | Weekly carrier invoice, about 7.5% of the previous week's completed sales |
| Packaging | Twice a month, linked to sales volume |
| Bank & Payment Fees | 2.2% of the month's card and digital-wallet sales |
| Office Supplies, Travel | Small, irregular purchases |
| Equipment | Four one-off purchases (laptops, shelving, label printer) |

Shipping, packaging, marketing and card fees are **calculated from the sales file**,
so the two datasets match. Using completed sales as revenue, the business makes
about a 10% profit overall. Some quiet months make a loss and the holiday months
are strongly profitable.

## 3. `sales_messy.csv` — deliberate data-quality problems

This is a copy of `sales_clean.csv` (same columns) in which **339 problems** were
introduced on purpose to test the data-quality features. Each changed row has
exactly one problem, and every problem is listed in `sales_messy_issue_log.csv`
with its row number, column, original value and new value. Rows not listed in the
log are identical to the clean file. Where only one value was damaged (for
example a bad quantity), the other values were left as they were.

| Problem group | `issue_type` in log | Column(s) | Count | Example |
|---|---|---|---|---|
| Missing values | `missing_value` | customer_id 25, region 15, payment_method 10, quantity 8, order_date 5 | 63 | blank cell |
| Duplicate records | `exact_duplicate` | entire row | 20 | identical row 1–5 rows after the original |
| Duplicate records | `duplicate_order_id` | order_id | 5 | a different order reusing an existing ID |
| Inconsistent date formats | `inconsistent_date_format` | order_date | 90 | `05/03/2025` (DD/MM/YYYY, 40), `2025/03/05` (25), `05-Mar-2025` (25) |
| Extra spaces | `extra_whitespace` | product_name 20, category 15, region 15 | 50 | `"  North"`, `"Yoga Mat   "` |
| Inconsistent capitalization | `inconsistent_capitalization` | region 20, order_status 15, payment_method 15 | 50 | `north`, `COMPLETED`, `credit card` |
| Invalid numbers | `invalid_number` | quantity 11, unit_price 10 | 21 | negative quantity (6), zero quantity (5), `N/A`/`unknown`/`TBD` price (6), `$24.99` (4) |
| Invalid numbers | `implausible_value` | quantity | 3 | quantity of 250, 500 or 999 |
| Incorrect category names | `incorrect_category` | category | 20 | `Electronis`, `Home and Kitchen`, `Beauty` |
| Invalid records | `future_date` | order_date | 4 | a date in 2027 |
| Invalid records | `impossible_date` | order_date | 3 | `2025-02-30`, `2025-04-31`, `2025-13-05` |
| Invalid records | `total_mismatch` | total_sales | 4 | total ≠ quantity × unit_price |
| Invalid records | `unknown_status` | order_status | 3 | `Unknown`, `???`, `TBD` |
| Invalid records | `missing_order_id` | order_id | 3 | blank order ID |

Totals: 319 changed rows + 20 added duplicate rows = 339 log entries;
7,990 + 20 = 8,010 rows.

**Date note:** dates written with slashes and the day first (`DD/MM/YYYY`) are
day-first by design. A date such as `05/03/2025` means 5 March 2025.

## 4. Checking the files

`checksums.json` stores a SHA-256 fingerprint of each CSV. The automated tests in
`tests/test_sample_data.py` compare against it, so any accidental change to a
sample file makes the tests fail. Run them with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```
