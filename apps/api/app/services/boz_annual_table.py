"""Strict interpretation of BoZ Annual Report Table 13 "KWACHA/US DOLLAR EXCHANGE RATES, Monthly Average" (pure functions, no I/O).

Input is the `pdftotext -layout` text of ONE pinned page (see pdf_layout_text / boz_annual_source). Only the vetted layouts of
the 2010 to 2014 Annual Reports (docs/research/boz-2010-2013-monthly-fx-source-chain.md) are accepted. Page identity is
checked explicitly: the heading line (`KWACHA/US DOLLAR EXCHANGE RATES` + `TABLE 13`), the Bank of Zambia / Bureau column groups,
the exact label order `Buying Selling Mid Buying Selling Mid`, `Period` / `Monthly Average`, the printed page number, and the end
marker `Source: Bank of Zambia`.

PUBLISHED COLUMN ORDER (not the Fortnightly buy/mid/sell order): the six numbers of a row are, left to right,
    Bank of Zambia Buying, Selling, Mid, then Bureau Buying, Selling, Mid.
The Bureau columns are shape-checked and discarded. The result stores the Bank of Zambia triple under the right names.

Row grammar: `[<year>] <Month> <6 numbers>` where numbers are `1,234.56` or `5.28` style with exactly two decimals. Year context
comes from exactly one of two vetted forms: an INLINE year before the month name, or a STANDALONE year line (the 2010 report)
that applies to the next month row. A month row with no year context, a year that conflicts with a standalone year,
consecutive standalone years, a year with no month row, a non-increasing year, a duplicate or out-of-order month, a gap
inside a year, anything other than the vetted line shapes, or a missing end marker fails the whole table. The year is never
inferred from the publication date.

Values are Decimal built from the printed text (thousands separators removed) and are never rounded, scaled or repaired here.
Normalization to canonical ZMW is a separate step (fx_monthly_normalize). Ordering: buy <= mid <= sell, except the narrow,
exactly-valued ORDERING_EXCEPTIONS below. The ordering rule is enforced from `ordering_from` (the report's persistence or
validation window) onward; earlier rows of the table (December rows from 1995, monthly 2008 to 2009) are structure-checked
only and never used. Observed while building this: the 2010 report prints 2008-08 with mid > selling. It is outside every
approved window, is not an approved exception and cannot be persisted.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ANNUAL_SOURCE_SERIES = "annual_report_table_13_monthly_average"
ANNUAL_SOURCE_TABLE = "Table 13"
ANNUAL_PUBLISHED_PRECISION = 2
REBASING_MONTH = date(2013, 1, 1)  # BoZ rebasing (divide by 1,000) took effect on 1 January 2013

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
MAX_ROWS = 80


class AnnualTableError(ValueError):
    """The page is not the vetted Table 13 layout, or a row failed validation. Never carries rate values."""


@dataclass(frozen=True)
class PublishedRate:
    month: date  # first day of the month; an identity, not an observation date
    buy: Decimal  # Bank of Zambia buying, exactly as printed
    mid: Decimal  # Bank of Zambia mid, exactly as printed
    sell: Decimal  # Bank of Zambia selling, exactly as printed


@dataclass(frozen=True)
class ParsedAnnualTable:
    rows: tuple[PublishedRate, ...]  # ascending, unique months (December-only rows of earlier years included)


@dataclass(frozen=True)
class OrderingException:
    """A source-proven published ordering violation. Tied to one series, one month and the exact printed values."""
    series: str
    month: date
    buy: Decimal
    mid: Decimal
    sell: Decimal
    reason: str


# The only two historical violations of buy <= mid <= sell, each repeated identically in every report that prints it
# (docs/research/boz-2010-2013-monthly-fx-source-chain.md, section 6). Printed values, not corrections.
ORDERING_EXCEPTIONS: tuple[OrderingException, ...] = (
    OrderingException(ANNUAL_SOURCE_SERIES, date(2012, 4, 1), Decimal("5283.60"), Decimal("5240.97"), Decimal("5250.97"),
                      "published selling < buying and mid < selling (Annual Reports 2012, 2013, 2014 Table 13)"),
    OrderingException(ANNUAL_SOURCE_SERIES, date(2013, 12, 1), Decimal("5.52"), Decimal("5.54"), Decimal("5.53"),
                      "published mid > selling (Annual Reports 2013, 2014 Table 13)"),
)
_EXCEPTIONS = {(e.series, e.month): e for e in ORDERING_EXCEPTIONS}

_NUMBER = r"\d{1,3}(?:,\d{3})*\.\d{2}"
_ROW = re.compile(r"^(?:(\d{4}) )?(%s) ((?:%s ){5}%s)$" % ("|".join(MONTHS), _NUMBER, _NUMBER))
_YEAR = re.compile(r"^(\d{4})$")
_PAGE_NUMBER = re.compile(r"^\d{1,3}$")
# The only heading lines accepted (whole line, whitespace-collapsed, case-insensitive): the exchange-rate title immediately
# followed by "TABLE 13" on the same line. 2010 prefixes "BANK OF ZAMBIA AND BUREAUX"; 2011-2013 and 2014 (mixed case) do not.
_VETTED_HEADINGS = frozenset({
    "KWACHA/US DOLLAR EXCHANGE RATES TABLE 13",
    "BANK OF ZAMBIA AND BUREAUX KWACHA/US DOLLAR EXCHANGE RATES TABLE 13",
})
_ANY_TABLE_REF = re.compile(r"\bTABLE\s*\d+\b", re.IGNORECASE)
_TITLE_WORDS = re.compile(r"EXCHANGE RATES", re.IGNORECASE)


def _check_identity(header: list[str]) -> None:
    """Exactly one header line is a vetted heading; no other line may carry a table reference or the title (so a Table 14
    heading, an inserted suffix/prefix, or a duplicate/competing candidate all fail closed)."""
    matches = [line for line in header if line.upper() in _VETTED_HEADINGS]
    others = [line for line in header if line.upper() not in _VETTED_HEADINGS and (_ANY_TABLE_REF.search(line) or _TITLE_WORDS.search(line))]
    if len(matches) != 1 or others:
        raise AnnualTableError("Unexpected table identity (heading / Table 13)")
_GROUPS = re.compile(r"Bank of Zambia Rates ?Bureau Rates", re.IGNORECASE)
_LABELS = "Buying Selling Mid Buying Selling Mid"
_END = re.compile(r"^Source: Bank of Zambia")


def published_currency_for(month: date) -> str:
    """'ZMK' (old kwacha) before 2013-01, 'ZMW' (rebased) from 2013-01. By observation month only, never by magnitude."""
    return "ZMK" if month < REBASING_MONTH else "ZMW"


def _lines(page_text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in page_text.splitlines() if line.strip()]


def _decimal(text: str) -> Decimal:
    value = Decimal(text.replace(",", ""))  # the regex guarantees digits with exactly two decimals
    if value <= 0:
        raise AnnualTableError("Non-positive rate in Table 13")
    return value


def check_ordering(rate: PublishedRate, series: str = ANNUAL_SOURCE_SERIES) -> None:
    """buy <= mid <= sell, unless this exact month and these exact printed values are a registered exception.
    An exception month must also carry exactly the registered values, so an altered row there fails."""
    exception = _EXCEPTIONS.get((series, rate.month))
    if exception is not None:
        if (rate.buy, rate.mid, rate.sell) != (exception.buy, exception.mid, exception.sell):
            raise AnnualTableError(f"Registered exception month {rate.month:%Y-%m} does not carry the approved published values")
        return
    if not rate.buy <= rate.mid <= rate.sell:
        raise AnnualTableError(f"Ordering violated (buy <= mid <= sell) in {rate.month:%Y-%m}")


def parse_table13(page_text: str, *, printed_page: int, ordering_from: date) -> ParsedAnnualTable:
    lines = _lines(page_text)
    first_data = next((i for i, line in enumerate(lines) if _ROW.match(line) or _YEAR.match(line) and int(line) != printed_page), None)
    if first_data is None:
        raise AnnualTableError("No Table 13 rows were found on the page")
    header = lines[:first_data]
    _check_identity(header)
    joined = " ".join(header)
    if not _GROUPS.search(joined):
        raise AnnualTableError("Unexpected Table 13 column groups")
    if not re.search(r"\bPeriod\b", joined) or "Monthly Average" not in joined or _LABELS not in joined:
        raise AnnualTableError("Unexpected Table 13 column labels")
    if str(printed_page) not in [line for line in lines if _PAGE_NUMBER.match(line)]:
        raise AnnualTableError("Printed page number does not match the pinned page")

    rows: list[PublishedRate] = []
    standalone: int | None = None  # a standalone year line waiting for its first month row
    previous: date | None = None
    ended = False
    for line in lines[first_data:]:
        if _END.match(line):
            ended = True
            break
        if len(rows) > MAX_ROWS:
            raise AnnualTableError("Table 13 is longer than expected")
        if _PAGE_NUMBER.match(line) and int(line) == printed_page:
            continue  # the printed page number may sit anywhere on the page
        if (match := _YEAR.match(line)):
            if standalone is not None:
                raise AnnualTableError("Consecutive standalone year lines")
            standalone = int(match.group(1))
            if previous is not None and standalone <= previous.year:
                raise AnnualTableError("Year labels are not increasing")
            continue
        match = _ROW.match(line)
        if not match:
            raise AnnualTableError("Unexpected line in the Table 13 body")
        inline = int(match.group(1)) if match.group(1) else None
        if inline is not None and standalone is not None:
            raise AnnualTableError("Year given both inline and as a standalone line")
        year = inline if inline is not None else standalone
        month_number = MONTHS.index(match.group(2)) + 1
        if year is not None:  # this row opens a year: Table 13 years start at December (December-only) or January
            if previous is not None and year <= previous.year:
                raise AnnualTableError("Year labels are not increasing")
            if month_number not in (1, 12):
                raise AnnualTableError(f"A year must start at January or December ({year}-{month_number:02d})")
        else:  # unlabelled: only the next month of the year already in force
            if previous is None or previous.month == 12:
                raise AnnualTableError("Month row without a year context")
            year = previous.year
            if month_number != previous.month + 1:
                raise AnnualTableError(f"Months are not consecutive at {year}-{month_number:02d}")
        month = date(year, month_number, 1)
        numbers = [_decimal(token) for token in match.group(3).split()]  # Bank buy, sell, mid, Bureau buy, sell, mid
        rate = PublishedRate(month=month, buy=numbers[0], mid=numbers[2], sell=numbers[1])
        if month >= ordering_from:  # rows before the window are structure-checked only; they are never persisted
            check_ordering(rate)
        rows.append(rate)
        previous, standalone = month, None
    if not ended:
        raise AnnualTableError("Table 13 is truncated (no source line follows)")
    if standalone is not None:
        raise AnnualTableError("Year label without a month row")
    if not rows:
        raise AnnualTableError("Empty Table 13")
    return ParsedAnnualTable(rows=tuple(rows))
