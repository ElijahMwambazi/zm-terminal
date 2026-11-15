"""Strict interpretation of BoZ Fortnightly Statistics Table 02 "Monthly Averages" (pure functions, no I/O).

Input is the extracted text of each PDF page (see boz_monthly_source for fetching and extraction). The parser only
accepts the block it can positively identify:

    Table 02 / KWACHA/US DOLLAR EXCHANGE RATES  (same line or adjacent lines)
    Bank of Zambia Rates Bureau Rates           (BoZ columns first: a shifted layout is rejected)
    [Period]
    Buying Selling Mid-rate Buying Selling Mid-rate
    Monthly Averages                            (or "Monthly" / "Averages" on two lines)
    <year> / <Month> buy sell mid bureau-buy bureau-sell bureau-mid ...
    Weekly Average(s)                           (end marker; absent = truncated block = rejected)

Rules (see docs/research/boz-deeper-fx-history.md, gates 2, 3, 5 and 7):
- Exactly one Table 02 header across all pages; zero or several is an error.
- Every line in the monthly block must be a year label or a month row with six two-decimal numbers (BoZ buy/sell/mid, then
  Bureau buy/sell/mid, which are shape-checked and discarded). Anything else fails the publication.
- Months must be consecutive calendar months with an explicit year label; the year is never inferred.
- Values are Decimal built from the published text; nothing is rounded, extended, derived or repaired.
- Hard ordering rule: buy <= mid <= sell. ORDERING_EXCEPTIONS (named, source-proven) is empty in phase 1.
- The mid is the published mid. Its distance from (buy + sell) / 2 is NOT a validity rule (BoZ's monthly-mid methodology is
  not established); midpoint_deviation() exists for diagnostics and tests only.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

SOURCE = "boz"
SOURCE_SERIES = "fortnightly_table_02_monthly_average"
SOURCE_TABLE = "Table 02"
PUBLISHED_PRECISION = 2
CURRENCY_REGIME = "ZMW"
MAX_BLOCK_ROWS = 120

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

# Phase 1 (2014-01 onward, rebased kwacha) has no known ordering violation. A future entry must name the month and be backed
# by a published source (publication, URL, page, exact values, reason); an unlisted violation always fails.
ORDERING_EXCEPTIONS: frozenset[date] = frozenset()

_HEADING = "KWACHA/US DOLLAR EXCHANGE RATES"
_TABLE_TOKEN = re.compile(r"\bTable 02\b")
_BOZ_COLUMNS = "Bank of Zambia Rates Bureau Rates"
_COLUMN_LABELS = "Buying Selling Mid-rate Buying Selling Mid-rate"
_VALUE = r"\d{1,3}\.\d{2}"
_ROW = re.compile(r"^(%s) (%s) (%s) (%s) (%s) (%s) (%s)$" % ("|".join(MONTHS), *([_VALUE] * 6)))
_YEAR = re.compile(r"^(20\d{2})$")


class Table02Error(ValueError):
    """The publication does not contain exactly one parseable Table 02 monthly block."""


class Table02NotFound(Table02Error):
    """No Table 02 header was found on any page (for example a one-page issue without the table)."""


@dataclass(frozen=True)
class MonthlyRate:
    month: date  # first day of the month; an identity, not an observation date
    buy: Decimal
    mid: Decimal
    sell: Decimal


@dataclass(frozen=True)
class ParsedTable:
    page: int  # 1-based PDF page
    rows: tuple[MonthlyRate, ...]


def midpoint_deviation(rate: MonthlyRate) -> Decimal:
    """Published mid minus (buy + sell) / 2. Diagnostic only: never a validity rule, never used to alter a value."""
    return rate.mid - (rate.buy + rate.sell) / 2


def _lines(page_text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in page_text.splitlines() if line.strip()]


def _header_indexes(lines: list[str]) -> list[int]:
    found = []
    for i, line in enumerate(lines):
        if not _TABLE_TOKEN.search(line):
            continue
        # Vetted forms: heading + table number on one line, or two exact adjacent
        # lines in either order. Never borrow a heading carrying another table id.
        if line == f"{_HEADING} Table 02":
            found.append(i)
        elif line == "Table 02":
            adjacent = [lines[j] for j in (i - 1, i + 1) if 0 <= j < len(lines)]
            matches = adjacent.count(_HEADING)
            if matches > 1:
                raise Table02Error("Ambiguous headings adjacent to Table 02")
            if matches != 1:
                raise Table02NotFound("Table 02 has no exact associated heading")
            heading_index = i - 1 if i and lines[i - 1] == _HEADING else i + 1
            beyond_heading = heading_index + (heading_index - i)
            if 0 <= beyond_heading < len(lines) and re.fullmatch(r"Table \d+[A-Za-z]?", lines[beyond_heading]):
                raise Table02Error("Ambiguous table numbers adjacent to the exchange-rate heading")
            found.append(i)
        else:
            raise Table02NotFound("Unsupported Table 02 heading form")
    return found


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def _decimal(text: str) -> Decimal:
    value = Decimal(text)  # the regex has already guaranteed plain digits with two decimals
    if value <= 0:
        raise Table02Error("Non-positive rate in monthly block")
    return value


def _check_ordering(rate: MonthlyRate) -> None:
    if not rate.buy <= rate.mid <= rate.sell and rate.month not in ORDERING_EXCEPTIONS:
        raise Table02Error(f"Ordering violated (buy <= mid <= sell) in {rate.month:%Y-%m}")


def parse_page(lines: list[str], header: int) -> tuple[MonthlyRate, ...]:
    rest = lines[header + 1:]
    if rest and rest[0] == _HEADING:
        rest = rest[1:]  # heading on its own line after "Table 02"
    if not rest or rest[0] != _BOZ_COLUMNS:
        raise Table02Error("Unexpected Table 02 column header layout")
    rest = rest[1:]
    if rest and rest[0] == "Period":
        rest = rest[1:]
    if not rest or rest[0] != _COLUMN_LABELS:
        raise Table02Error("Unexpected Table 02 column labels")
    rest = rest[1:]
    if rest[:1] == ["Monthly Averages"]:
        rest = rest[1:]
    elif rest[:2] == ["Monthly", "Averages"]:
        rest = rest[2:]
    else:
        raise Table02Error("Monthly Averages block not found")

    rows: list[MonthlyRate] = []
    year: int | None = None
    pending_year = False
    previous: date | None = None
    for index, line in enumerate(rest):
        if line in ("Weekly Average", "Weekly Averages") or (
            line == "Weekly" and rest[index + 1: index + 2] in (["Average"], ["Averages"])
        ):
            if pending_year:
                raise Table02Error("Year label without a month row")
            if not rows:
                raise Table02Error("Empty monthly block")
            return tuple(rows)
        if len(rows) > MAX_BLOCK_ROWS:
            raise Table02Error("Monthly block is longer than expected")
        if (match := _YEAR.match(line)):
            if pending_year:
                raise Table02Error("Ambiguous year labels")
            year, pending_year = int(match.group(1)), True
            continue
        if (match := _ROW.match(line)):
            if year is None:
                raise Table02Error("Month row without a year label")
            month = date(year, MONTHS.index(match.group(1)) + 1, 1)
            if previous is not None and month != _next_month(previous):
                raise Table02Error(f"Monthly rows are not consecutive at {month:%Y-%m}")
            buy, sell, mid = (_decimal(match.group(i)) for i in (2, 3, 4))
            for bureau in (5, 6, 7):
                _decimal(match.group(bureau))
            rate = MonthlyRate(month=month, buy=buy, mid=mid, sell=sell)
            _check_ordering(rate)
            rows.append(rate)
            previous, pending_year = month, False
            continue
        raise Table02Error("Unexpected line in the Table 02 monthly block")
    raise Table02Error("Monthly block is truncated (no weekly section follows)")


def parse_table02(pages: Sequence[str]) -> ParsedTable:
    """The single Table 02 monthly block across the extracted page texts, or a Table02Error."""
    candidates = []
    for number, page_text in enumerate(pages, start=1):
        lines = _lines(page_text)
        for header in _header_indexes(lines):
            candidates.append((number, lines, header))
    if not candidates:
        raise Table02NotFound("Table 02 was not found")
    if len(candidates) > 1:
        raise Table02Error("More than one Table 02 header was found")
    number, lines, header = candidates[0]
    return ParsedTable(page=number, rows=parse_page(lines, header))
