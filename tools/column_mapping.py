"""Recognise what business information each column holds, from its name.

Example: "Total Sales (USD)", "Revenue" and "sales_amount" are all recognised
as the *sales amount*. Files differ, so nothing is assumed: a role that no
column matches is simply reported as "not found", and the user can correct
or add matches by hand (`overrides`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config.quality_rules import (
    COLUMN_SYNONYMS,
    EXPENSE_CONTEXT_WORDS,
    GENERIC_AMOUNT_SYNONYMS,
    IGNORED_NAME_WORDS,
    ROLE_LABELS,
)

EXACT = "exact"
PARTIAL = "partial"
MANUAL = "manual"

_SYNONYM_TOKENS = {
    role: [tuple(synonym.split()) for synonym in synonyms] for role, synonyms in COLUMN_SYNONYMS.items()
}
_EXACT_LOOKUP: dict[str, tuple[str, str]] = {}
for _role, _synonyms in COLUMN_SYNONYMS.items():
    for _synonym in _synonyms:
        if _synonym in _EXACT_LOOKUP:
            raise ValueError(f"Synonym '{_synonym}' is listed for two roles")
        _EXACT_LOOKUP[_synonym] = (_role, _synonym)


@dataclass
class ColumnMatch:
    column: str
    role: str
    confidence: str      # "exact", "partial" or "manual"
    matched_on: str      # synonym that matched, or "chosen by you"


@dataclass
class ColumnMapping:
    columns: list[str]
    matches: dict[str, ColumnMatch] = field(default_factory=dict)   # role -> match

    def column_for(self, role: str) -> str | None:
        match = self.matches.get(role)
        return match.column if match else None

    def role_of(self, column: str) -> str | None:
        for role, match in self.matches.items():
            if match.column == column:
                return role
        return None

    @property
    def unmatched_columns(self) -> list[str]:
        matched = {match.column for match in self.matches.values()}
        return [column for column in self.columns if column not in matched]

    @property
    def missing_roles(self) -> list[str]:
        return [role for role in ROLE_LABELS if role not in self.matches]

    def as_overrides(self) -> dict[str, str | None]:
        return {role: self.column_for(role) for role in ROLE_LABELS}

    def dataset_kind(self) -> str:
        """A rough guess: "sales", "expenses", or "unknown"."""
        if "sales_amount" in self.matches or {"quantity", "unit_price"} <= self.matches.keys():
            return "sales"
        if "expense_amount" in self.matches:
            return "expenses"
        return "unknown"


def normalize_column_name(name: str) -> str:
    """'OrderDate' → 'order date', 'Total_Sales (USD)' → 'total sales usd', 'Order #' → 'order number'."""
    text = str(name)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)      # camelCase → camel Case
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)   # IDNumber → ID Number
    text = text.replace("#", " number ").replace("&", " and ")
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text)
    return " ".join(text.lower().split())


def _match_column(name: str) -> tuple[str, str, str] | None:
    """Return (role, confidence, synonym) for one column name, or None."""
    normalized = normalize_column_name(name)
    tokens = [token for token in normalized.split() if token not in IGNORED_NAME_WORDS]
    if not tokens:
        return None
    cleaned = " ".join(tokens)
    for candidate in (normalized, cleaned):
        if candidate in _EXACT_LOOKUP:
            role, synonym = _EXACT_LOOKUP[candidate]
            return role, EXACT, synonym

    # Partial match: a synonym appears as whole words inside the name.
    # Prefer a synonym at the end of the name (English puts the key word last:
    # "Customer Region" is a region), then the longest synonym.
    best: tuple[tuple[bool, int], str, str] | None = None
    for role, synonym_list in _SYNONYM_TOKENS.items():
        for synonym in synonym_list:
            size = len(synonym)
            for start in range(len(tokens) - size + 1):
                if tuple(tokens[start:start + size]) == synonym:
                    rank = (start + size == len(tokens), size)
                    if best is None or rank > best[0]:
                        best = (rank, role, " ".join(synonym))
    if best is None:
        return None
    return best[1], PARTIAL, best[2]


def recognize_columns(columns: list[str], overrides: dict[str, str | None] | None = None) -> ColumnMapping:
    """Match column names to roles. Each role gets at most one column and each
    column at most one role. `overrides` (role -> column, or None for "not in
    this file") replaces the automatic choice for the roles it mentions."""
    columns = [str(column) for column in columns]
    mapping = ColumnMapping(columns=columns)

    candidates: dict[str, list[tuple[int, int, ColumnMatch]]] = {}
    for position, column in enumerate(columns):
        result = _match_column(column)
        if result is None:
            continue
        role, confidence, synonym = result
        strength = 2 if confidence == EXACT else 1
        candidates.setdefault(role, []).append((strength, -position, ColumnMatch(column, role, confidence, synonym)))

    for role, options in candidates.items():
        options.sort(key=lambda option: (option[0], option[1]), reverse=True)
        mapping.matches[role] = options[0][2]

    _apply_expense_context(mapping)

    if overrides:
        for role, column in overrides.items():
            if role not in ROLE_LABELS:
                continue
            if column is None:
                mapping.matches.pop(role, None)
                continue
            if column not in columns:
                continue
            for other_role in [r for r, m in mapping.matches.items() if m.column == column and r != role]:
                mapping.matches.pop(other_role)
            current = mapping.matches.get(role)
            if current is None or current.column != column:
                mapping.matches[role] = ColumnMatch(column, role, MANUAL, "chosen by you")
    return mapping


def _apply_expense_context(mapping: ColumnMapping) -> None:
    """In an expense file, a generic 'Amount' column means an expense amount."""
    sales = mapping.matches.get("sales_amount")
    if sales is None or "expense_amount" in mapping.matches or sales.matched_on not in GENERIC_AMOUNT_SYNONYMS:
        return
    words = {word for column in mapping.columns for word in normalize_column_name(column).split()}
    if words & EXPENSE_CONTEXT_WORDS:
        mapping.matches.pop("sales_amount")
        mapping.matches["expense_amount"] = ColumnMatch(sales.column, "expense_amount", sales.confidence,
                                                        sales.matched_on + " (expense file)")
