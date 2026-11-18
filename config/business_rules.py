"""Business rules for KPI calculations. Change these to match how your business works.

Statuses are compared in lower case with spaces trimmed.
"""

# Orders with these statuses count as revenue.
REVENUE_STATUSES = frozenset({
    "completed", "complete", "paid", "delivered", "shipped", "fulfilled", "closed", "success",
    "successful", "settled", "invoiced", "done",
})
# Not finished yet: left out of revenue unless the user chooses to include them.
PENDING_STATUSES = frozenset({
    "pending", "processing", "in progress", "on hold", "open", "awaiting payment", "unpaid", "new", "draft",
})
# Money went back to the customer: never counted as revenue, reported separately.
RETURNED_STATUSES = frozenset({"returned", "refunded", "refund", "return", "partially refunded", "chargeback"})
# The sale did not happen: never counted.
CANCELLED_STATUSES = frozenset({"cancelled", "canceled", "void", "voided", "failed", "rejected", "declined",
                                "abandoned"})

# Status groups (used in data notes and tests).
STATUS_REVENUE = "revenue"
STATUS_PENDING = "pending"
STATUS_RETURNED = "returned"
STATUS_CANCELLED = "cancelled"
STATUS_UNKNOWN = "unrecognised"
STATUS_NONE = "no status column"

# Number of items shown in "top" charts; the rest are combined into "All others".
TOP_ITEMS = 10

# Label used for blank product / category / region values.
NOT_SPECIFIED = "(not specified)"

# Time grouping chosen automatically from the length of the selected period.
AUTO_DAILY_MAX_DAYS = 45
AUTO_WEEKLY_MAX_DAYS = 180

# ---------------------------------------------------------------------------
# Period comparisons (tools/period_comparison.py)
# ---------------------------------------------------------------------------
# A period is "complete" only when the file covers every day of it AND it has no
# unusually long stretch of days without any records. A stretch is unusual when it
# is at least GAP_MIN_DAYS long AND longer than GAP_MULTIPLIER × the file's usual
# longest quiet stretch (the 90th percentile of the empty days between records).
# Example: daily sales -> usual stretch 0 days -> a 14-day silence is flagged.
# A file with one record a month -> usual stretch ~30 days -> nothing flagged.
GAP_MIN_DAYS = 14
GAP_MULTIPLIER = 3
GAP_PERCENTILE = 90

# Return and cancellation rates are only shown when statuses can be trusted: at
# most this share of the period's orders may have a status BusinessPulse doesn't
# recognise (for example "TBD" or "???").
MAX_UNRECOGNISED_STATUS_SHARE = 0.05

# Insights page (Stage 6.4): the thresholds that turn figures into findings.
# A month-on-month change smaller than this (in %) is described as "steady".
INSIGHT_STEADY_PCT = 2.0
# Expenses growing this many percentage points faster (or slower) than revenue is flagged.
INSIGHT_GROWTH_GAP_PP = 2.0
# One product, category or region with at least this share (%) of revenue is a
# concentration risk (only checked when there are at least 3 of them).
INSIGHT_CONCENTRATION_SHARE = 40.0
# This many month-on-month rises (or falls) in a row is reported as a trend.
INSIGHT_STREAK_MONTHS = 3

# Anomalies page (Stage 7): a complete month is compared with the complete months
# before it using a robust z-score: 0.6745 × (value − median) ÷ MAD, where MAD is the
# median absolute deviation (Iglewicz & Hoaglin). Medians are used so one odd month
# cannot hide another.
# At least this many earlier complete months are needed before a month is checked.
ANOMALY_MIN_HISTORY = 6
# At most this many earlier complete months form the baseline.
ANOMALY_MAX_HISTORY = 12
# Robust z-score at or above which a month is unusual, and the severity bands.
ANOMALY_Z_MEDIUM = 3.5
ANOMALY_Z_HIGH = 5.0
# Ignore deviations smaller than this share (%) of the typical (median) value, so
# very steady figures don't flag tiny changes.
ANOMALY_MIN_DEVIATION_PCT = 15.0
# Products, categories and regions with less than this share (%) of revenue in the
# selected dates are not checked: their figures are too small to be reliable.
ANOMALY_MIN_ITEM_SHARE = 3.0
# Products, categories and regions are checked on their share of each month's revenue
# (so a season that lifts every item is not flagged item by item). A share must move
# at least this many percentage points from its typical value to be flagged.
ANOMALY_MIN_SHARE_POINTS = 1.0
