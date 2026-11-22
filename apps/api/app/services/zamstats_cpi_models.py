"""Result types and exact numeric grammar for the ZamStats CPI Phase 1 foundation (pure data, no I/O, no storage).

Phase 1 is exactly seven national series on the `2009=100` regime: the CPI index for All Items, Food and Non-Food; monthly
inflation for the same three; and annual inflation for All Items only. Published rates are authoritative source observations:
nothing here (or anywhere in the CPI foundation) calculates, repairs, rounds or fills a value. Source text is preserved for every
number, together with the sign notation, so a parenthesised zero stays distinguishable from an ordinary zero.
Rights status: review_required (local/internal use only).
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

REGIME = "2009=100"
RIGHTS_STATUS = "review_required"

METRICS = ("index", "monthly_inflation", "annual_inflation")
CATEGORIES = ("all_items", "food", "non_food")

# The seven approved series (metric, category). Annual Food/Non-Food are deliberately absent.
PHASE1_SERIES: tuple[tuple[str, str], ...] = (
    ("index", "all_items"), ("index", "food"), ("index", "non_food"),
    ("monthly_inflation", "all_items"), ("monthly_inflation", "food"), ("monthly_inflation", "non_food"),
    ("annual_inflation", "all_items"),
)
SERIES_NAMES: tuple[str, ...] = tuple(f"{m}_{c}" for m, c in PHASE1_SERIES)

# Implementation boundaries (not claims that earlier published rows do not exist).
BOUNDARY_FIRST_MONTH: dict[str, date] = {
    "index": date(2010, 1, 1), "monthly_inflation": date(2011, 1, 1), "annual_inflation": date(2011, 1, 1),
}

PLAIN, MINUS, PARENTHESES = "plain", "minus", "parentheses"
NOTATIONS = (PLAIN, MINUS, PARENTHESES)


class ZamstatsCpiError(Exception):
    """A manifest, publication, table or value failed verification. Messages name tables, pages, months and series, never source
    numeric values or PDF text."""


def series_name(metric: str, category: str) -> str:
    return f"{metric}_{category}"


def validate_series(metric: str, category: str) -> None:
    if metric not in METRICS or category not in CATEGORIES:
        raise ZamstatsCpiError("unknown metric or category")
    if metric == "annual_inflation" and category != "all_items":
        raise ZamstatsCpiError("annual inflation is Phase 1 for All Items only")


@dataclass(frozen=True)
class SourceNumber:
    """One printed number: exact text, parsed Decimal, and the sign notation as printed.

    `source_negative` is True for `-x` and `(x)`, including `(0.0)`: the parsed value of `(0.0)` is a mathematical zero but the
    source presented a rounded negative, and that evidence must survive independently of Decimal (and of SQL NUMERIC, which has
    no signed zero)."""
    source_value_text: str
    value: Decimal
    source_negative: bool
    source_notation: str


# Lexical grammars. Each layout family chooses exactly one; nothing else is accepted (no `+`, no bare `.5`, no exponent, no
# Unicode minus, no thousands separator in an observation).
INDEX_NUMBER = re.compile(r"^(?P<d>[0-9]{1,3}\.[0-9]{1,2})$")  # observed: 2 decimals, a trailing zero is sometimes dropped
RATE_NUMBER = re.compile(r"^(?:(?P<p>[0-9]{1,2}\.[0-9])|-(?P<m>[0-9]{1,2}\.[0-9])|\((?P<n>[0-9]{1,2}\.[0-9])\))$")  # 1 decimal


def parse_index_number(text: str) -> SourceNumber:
    m = INDEX_NUMBER.fullmatch(text) if isinstance(text, str) else None
    if not m:
        raise ZamstatsCpiError("index value does not match the vetted index grammar")
    return SourceNumber(text, Decimal(text), False, PLAIN)


def parse_rate_number(text: str) -> SourceNumber:
    m = RATE_NUMBER.fullmatch(text) if isinstance(text, str) else None
    if not m:
        raise ZamstatsCpiError("rate value does not match the vetted rate grammar")
    if m.group("p") is not None:
        return SourceNumber(text, Decimal(m.group("p")), False, PLAIN)
    if m.group("m") is not None:
        return SourceNumber(text, Decimal("-" + m.group("m")), True, MINUS)
    magnitude = Decimal(m.group("n"))
    # (0.0) is a mathematical zero (positive Decimal zero); the negative-source indication lives in source_negative/notation.
    return SourceNumber(text, -magnitude if magnitude != 0 else magnitude, True, PARENTHESES)


def parse_number(kind: str, text: str) -> SourceNumber:
    if kind == "index":
        return parse_index_number(text)
    if kind == "rate":
        return parse_rate_number(text)
    raise ZamstatsCpiError("unknown number kind")


@dataclass(frozen=True)
class SourceObservation:
    """One source cell of a vetted table: evidence, not yet a canonical observation."""
    month: date  # reference (observation) month, first-of-month
    metric: str
    category: str
    number: SourceNumber
    regime: str
    manifest_key: str  # the publication
    source_table: str  # e.g. "Annex 1.1"
    pdf_page: int
    source_row: int  # 1-based position of the row among the table's month rows
    layout_family: str
    canonical_for_table: bool = True  # False: a validation-only column (for example the annual table's Annual CPI column)

    def __post_init__(self) -> None:
        # The closed seven-series space holds for every instance, however it was built (not only through the table parsers).
        validate_series(self.metric, self.category)
        if self.month.day != 1:
            raise ZamstatsCpiError("observation month must be first-of-month")

    @property
    def series(self) -> str:
        return series_name(self.metric, self.category)


@dataclass(frozen=True)
class DuplicateRow:
    """An adjacent byte-identical printed row that the layout family explicitly authorised and the parser collapsed.
    Evidence is retained here, never silently dropped."""
    source_table: str
    pdf_page: int
    month: date
    source_row: int


@dataclass(frozen=True)
class ParsedTable:
    source_table: str
    pdf_page: int
    layout_family: str
    first_month: date
    last_month: date
    observations: tuple[SourceObservation, ...]
    duplicate_rows: tuple[DuplicateRow, ...] = ()


@dataclass(frozen=True)
class ParsedPublication:
    manifest_key: str
    reference_month: date
    tables: tuple[ParsedTable, ...]

    @property
    def observations(self) -> tuple[SourceObservation, ...]:
        return tuple(o for t in self.tables for o in t.observations)
