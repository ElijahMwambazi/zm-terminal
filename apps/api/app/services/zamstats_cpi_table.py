"""Pure layout parsers for the ZamStats Monthly Bulletin CPI tables (no I/O, no storage, no network, no database).

Each parser works on the text of ONE already-extracted PDF page and a manifest `TableSpec`. A table is accepted only if its
exact title anchor matches exactly once, its printed month rows form one contiguous run that starts and ends exactly where the
manifest pins it, every year assignment is explicit, every number matches the family's vetted grammar, and the page reports no
base period other than `2009 = 100`. Anything else fails closed. Published rates are authoritative observations: nothing is
calculated, repaired, rounded or inferred, and a duplicated printed row is collapsed only when the spec explicitly authorises it
(and is then reported, never silently dropped). Error messages name tables, pages and months, never source values or text.

Layout families (each positively identified by its spec, no fuzzy matching):
- `vertical_labelled`: one row per month, three value columns (Total, Food, Non-Food); the year appears either as a label on its
  own line inside its 12-month block or as a prefix on one row of the block.
- `annual_rows`: `Year  Month  Annual CPI  Annual Inflation Rate`, the year printed on every row.
- `quoted_months`: `Mon 'YY - Mon 'YY  Total  Food  Non-Food` rows (month-on-month rates), the month stated explicitly per row.
- `horizontal_13`: twelve or thirteen consecutive month columns; the rows Total, Food and Non-Food carry their fixed weights. The header
  month names are validated; header YEARS are not used (printed years in these headers contain typos), the year comes from the
  manifest's pinned first month.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.services.zamstats_cpi_models import (
    REGIME, DuplicateRow, ParsedTable, SourceObservation, ZamstatsCpiError, parse_number, validate_series,
)

VERTICAL_LABELLED = "vertical_labelled"
ANNUAL_ROWS = "annual_rows"
QUOTED_MONTHS = "quoted_months"
HORIZONTAL_13 = "horizontal_13"
FAMILIES = (VERTICAL_LABELLED, ANNUAL_ROWS, QUOTED_MONTHS, HORIZONTAL_13)

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_TOKEN = r"[(+\-.\d][^\s]*"  # a loose numeric-looking token; the strict grammar is applied afterwards
_BASE = re.compile(r"\b((?:19|20)\d\d)\s*=\s*100\b")
_LABEL = re.compile(r"^\s*((?:19|20)\d\d)\s*$")
_HORIZONTAL_WEIGHTS = {"total": r"1[, ]?000(?:\.0{1,2})?", "food": r"534\.85", "non_food": r"465\.15"}


@dataclass(frozen=True)
class TableSpec:
    """One pinned table of one vetted publication."""
    table_id: str  # source table identity, e.g. "Annex 1.1", "Table 5" (printed)
    family: str
    pdf_page: int
    title_regex: str  # must match exactly one line of the page
    first_month: date  # printed extent of the month rows (first-of-month)
    last_month: date
    columns: tuple[tuple[str, str, bool], ...]  # (metric, category, canonical_for_table) per value column, left to right
    authorized_duplicate_months: frozenset = frozenset()  # adjacent identical printed rows that may be collapsed (reported)
    base_on_page: bool = True  # the page itself must print `2009 = 100`; False when the publication pins it on another page
    title_matches: int = 1  # how many lines the title anchor is pinned to match (a bulletin may print the same heading twice)
    title_pick: int = 1  # which of those matches is the table title (1-based)
    wrapped_non_food_tail: int = 0  # horizontal_13 only: values of the Non-Food row printed on the "Non-" line just above it
    horizontal_weights: bool = True  # horizontal_13 only: False for the layout whose rows carry no weight column
    canonical_first: date | None = None  # months this table may canonically supply (its canonical columns only); None: evidence only
    canonical_last: date | None = None


def _months_between(first: date, last: date) -> int:
    return (last.year - first.year) * 12 + last.month - first.month


def _add_month(d: date, n: int = 1) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _kind(metric: str) -> str:
    return "index" if metric == "index" else "rate"


def validate_spec(spec: TableSpec) -> None:
    if spec.family not in FAMILIES:
        raise ZamstatsCpiError(f"{spec.table_id}: unknown layout family")
    for metric, category, _canonical in spec.columns:
        validate_series(metric, category)
    if spec.first_month.day != 1 or spec.last_month.day != 1 or spec.last_month < spec.first_month:
        raise ZamstatsCpiError(f"{spec.table_id}: invalid pinned month range")
    if (spec.canonical_first is None) != (spec.canonical_last is None):
        raise ZamstatsCpiError(f"{spec.table_id}: a canonical window needs both ends")
    if spec.canonical_first is not None and not (
            spec.first_month <= spec.canonical_first <= spec.canonical_last <= spec.last_month and spec.canonical_first.day == 1
            and spec.canonical_last.day == 1):
        raise ZamstatsCpiError(f"{spec.table_id}: the canonical window is not inside the printed month range")


def _table_lines(page_text: str, spec: TableSpec) -> list[str]:
    """The page lines from the single title anchor to the table's `Source:` line (exclusive); fails on zero or several anchors."""
    lines = page_text.splitlines()
    anchors = [i for i, line in enumerate(lines) if re.search(spec.title_regex, line, re.IGNORECASE)]
    if len(anchors) != spec.title_matches or not 1 <= spec.title_pick <= spec.title_matches:
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): table title matched {len(anchors)} times, expected {spec.title_matches}")
    rest = lines[anchors[spec.title_pick - 1]:]
    for i, line in enumerate(rest):
        if i > 0 and re.match(r"^\s*Source\s*:", line, re.IGNORECASE):
            return rest[:i]
    return rest


def _check_base(page_text: str, spec: TableSpec) -> None:
    bases = {m.group(1) for m in _BASE.finditer(page_text)}
    if bases - {"2009"}:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): reports a base period other than {REGIME}")
    if spec.base_on_page and "2009" not in bases:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the {REGIME} base label is missing")


def _month_of(name: str, spec: TableSpec) -> int:
    n = _MONTHS.get(name.lower())
    if n is None:
        raise ZamstatsCpiError(f"{spec.table_id}: unknown month name")
    return n


def _finish(spec: TableSpec, manifest_key: str, rows: list[tuple[date, list[str]]], duplicates: list[DuplicateRow]) -> ParsedTable:
    """Common checks: contiguous months, exact pinned first/last month, then strict number parsing into observations."""
    if not rows:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): no month rows found")
    months = [m for m, _ in rows]
    for i in range(1, len(months)):
        if months[i] != _add_month(months[i - 1]):
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): month sequence breaks after {months[i - 1]:%Y-%m}")
    if months[0] != spec.first_month or months[-1] != spec.last_month:
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): rows cover {months[0]:%Y-%m} to {months[-1]:%Y-%m}, "
            f"expected {spec.first_month:%Y-%m} to {spec.last_month:%Y-%m}")
    observations: list[SourceObservation] = []
    for position, (month, tokens) in enumerate(rows, start=1):
        if len(tokens) != len(spec.columns):
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): unexpected column count at {month:%Y-%m}")
        for (metric, category, canonical), token in zip(spec.columns, tokens):
            try:
                number = parse_number(_kind(metric), token)
            except ZamstatsCpiError as exc:
                raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}) {month:%Y-%m} {metric}_{category}: {exc}") from None
            observations.append(SourceObservation(
                month, metric, category, number, REGIME, manifest_key, spec.table_id, spec.pdf_page, position, spec.family,
                canonical))
    return ParsedTable(spec.table_id, spec.pdf_page, spec.family, months[0], months[-1], tuple(observations), tuple(duplicates))


# ---------------------------------------------------------------- vertical_labelled
def _parse_vertical_labelled(lines: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    n = len(spec.columns)
    row_re = re.compile(rf"^\s*(?:(?P<y>(?:19|20)\d\d)\s+)?(?P<mon>[A-Za-z]+)\s+(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{n - 1}}})\s*$")
    events: list[tuple] = []  # ("row", month_no, prefix_year|None, tokens, normalised) / ("label", year)
    for line in lines:
        m = row_re.match(line)
        if m and m.group("mon").lower() in _MONTHS:
            events.append(("row", _MONTHS[m.group("mon").lower()], m.group("y"), m.group("v").split(), " ".join(line.split())))
            continue
        label = _LABEL.match(line)
        if label:
            events.append(("label", int(label.group(1))))

    # Adjacent byte-identical printed rows: only a spec-authorised duplicate is collapsed (and reported); otherwise fail closed.
    collapsed: list[tuple] = []
    duplicate_positions: list[int] = []
    previous_row = None
    for event in events:
        if event[0] == "row" and previous_row is not None and event[4] == previous_row[4] and event[2] is None:
            duplicate_positions.append(sum(1 for e in collapsed if e[0] == "row"))
            continue
        collapsed.append(event)
        if event[0] == "row":
            previous_row = event
    if len(duplicate_positions) != len(spec.authorized_duplicate_months):
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): {len(duplicate_positions)} duplicated printed rows, "
            f"{len(spec.authorized_duplicate_months)} authorised")

    rows = [e for e in collapsed if e[0] == "row"]
    blocks: list[list[int]] = []  # row indices per 12-month block (a new block starts when the month number does not increase)
    for i, row in enumerate(rows):
        if i == 0 or row[1] <= rows[i - 1][1]:
            if i > 0 and row[1] == rows[i - 1][1]:
                raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the same month is printed twice in a row")
            blocks.append([])
        blocks[-1].append(i)
    labels: list[tuple[int, int]] = []  # (year, number of rows printed before it)
    seen_rows = 0
    for event in collapsed:
        if event[0] == "row":
            seen_rows += 1
        else:
            labels.append((event[1], seen_rows))
    block_years: list[int | None] = [None] * len(blocks)
    for b, indices in enumerate(blocks):
        found = [int(rows[i][2]) for i in indices if rows[i][2] is not None]
        found += [year for year, position in labels if indices[0] < position <= indices[-1]]
        if len(found) != 1:
            raise ZamstatsCpiError(
                f"{spec.table_id} (page {spec.pdf_page}): year block {b + 1} has {len(found)} year labels, expected exactly 1")
        block_years[b] = found[0]
    if len(blocks) != len(labels) + sum(1 for r in rows if r[2] is not None):
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): year labels do not match the month blocks")
    for b in range(1, len(blocks)):
        if block_years[b] != block_years[b - 1] + 1:  # type: ignore[operator]
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): year labels are not consecutive")

    dated: list[tuple[date, list[str]]] = []
    for b, indices in enumerate(blocks):
        for i in indices:
            dated.append((date(block_years[b], rows[i][1], 1), rows[i][3]))  # type: ignore[arg-type]
    duplicate_rows: list[DuplicateRow] = []
    for position in duplicate_positions:
        month = dated[position - 1][0]  # the duplicate repeats the row printed just before it
        if month not in spec.authorized_duplicate_months:
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): duplicated row is not an authorised month")
        duplicate_rows.append(DuplicateRow(spec.table_id, spec.pdf_page, month, position))
    return _finish(spec, manifest_key, dated, duplicate_rows)


# ---------------------------------------------------------------- annual_rows
def _parse_annual_rows(lines: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    n = len(spec.columns)
    row_re = re.compile(rf"^\s*(?P<y>(?:19|20)\d\d)\s+(?P<mon>[A-Za-z]+)\s+(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{n - 1}}})\s*$")
    dated: list[tuple[date, list[str]]] = []
    for line in lines:
        m = row_re.match(line)
        if m and m.group("mon").lower() in _MONTHS:
            dated.append((date(int(m.group("y")), _MONTHS[m.group("mon").lower()], 1), m.group("v").split()))
    return _finish(spec, manifest_key, dated, [])


# ---------------------------------------------------------------- quoted_months
def _parse_quoted_months(lines: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    n = len(spec.columns)
    row_re = re.compile(
        rf"^\s*(?:(?:19|20)\d\d\s+)?(?P<m1>[A-Za-z]+)\s*[’']\s*(?P<y1>\d\d)\s*-\s*(?P<m2>[A-Za-z]+)\s*[’']\s*(?P<y2>\d\d)\s+"
        rf"(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{n - 1}}})\s*$")
    dated: list[tuple[date, list[str]]] = []
    for line in lines:
        m = row_re.match(line)
        if not m or m.group("m1").lower() not in _MONTHS or m.group("m2").lower() not in _MONTHS:
            continue
        current = date(2000 + int(m.group("y1")), _MONTHS[m.group("m1").lower()], 1)
        previous = date(2000 + int(m.group("y2")), _MONTHS[m.group("m2").lower()], 1)
        if previous != _add_month(current, -1):
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): a row does not compare with the preceding month")
        dated.append((current, m.group("v").split()))
    return _finish(spec, manifest_key, dated, [])


# ---------------------------------------------------------------- horizontal_13
def _parse_horizontal_13(lines: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    if [c[1] for c in spec.columns] != ["all_items", "food", "non_food"] or len({c[0] for c in spec.columns}) != 1:
        raise ZamstatsCpiError(f"{spec.table_id}: horizontal_13 needs the All Items, Food, Non-Food columns of one metric")
    width = _months_between(spec.first_month, spec.last_month) + 1
    if width not in (12, 13):
        raise ZamstatsCpiError(f"{spec.table_id}: horizontal tables pin twelve or thirteen consecutive months")
    names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    month_token = re.compile(rf"\b({names})\b", re.IGNORECASE)
    header = [line for line in lines if len(month_token.findall(line)) == width]
    if len(header) != 1:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): found {len(header)} {width}-month header lines, expected 1")
    header_months = [_MONTHS[t.lower()] for t in month_token.findall(header[0])]
    expected = [_add_month(spec.first_month, i).month for i in range(width)]
    if header_months != expected:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the header months do not match the pinned window")
    values = rf"(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{width - 1}}})"
    if not spec.horizontal_weights:
        if spec.wrapped_non_food_tail:
            raise ZamstatsCpiError(f"{spec.table_id}: a weightless layout has no wrapped Non-Food variant")
        patterns = {
            "all_items": re.compile(rf"^\s*Total\s+{values}\s*$"), "food": re.compile(rf"^\s*Food\s+{values}\s*$"),
            "non_food": re.compile(rf"^\s*Non-Food\s+{values}\s*$"),
        }
    else:
        patterns = {
            "all_items": re.compile(rf"^\s*Total\s+{_HORIZONTAL_WEIGHTS['total']}\s+{values}\s*$"),
            "food": re.compile(rf"^\s*Food\s+{_HORIZONTAL_WEIGHTS['food']}\s+{values}\s*$"),
            # the "Non-" / "Food" words may sit on separate physical lines; the fixed weight identifies the row
            "non_food": re.compile(rf"^\s*(?:Non-?\s*(?:Food\s+)?)?{_HORIZONTAL_WEIGHTS['non_food']}\s+{values}\s*$"),
        }
    found: dict[str, list[str]] = {}
    tail = spec.wrapped_non_food_tail
    if tail:  # explicit layout variant: the last `tail` Non-Food values are printed on the "Non-" line directly above the weight
        head = width - tail
        weight_row = re.compile(rf"^\s*{_HORIZONTAL_WEIGHTS['non_food']}\s+(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{head - 1}}})\s*$")
        tail_row = re.compile(rf"^\s*Non-?\s+(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{tail - 1}}})\s*$")
        positions = [i for i, line in enumerate(lines) if weight_row.match(line)]
        if len(positions) != 1 or positions[0] == 0 or not tail_row.match(lines[positions[0] - 1]):
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the wrapped non_food row was not found exactly once")
        found["non_food"] = weight_row.match(lines[positions[0]]).group("v").split() + tail_row.match(lines[positions[0] - 1]).group("v").split()
        patterns.pop("non_food")
    for category, pattern in patterns.items():
        matches = [pattern.match(line) for line in lines]
        matches = [m for m in matches if m]
        if len(matches) != 1:
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): {category} row matched {len(matches)} times, expected 1")
        found[category] = matches[0].group("v").split()
    dated = [(_add_month(spec.first_month, i), [found["all_items"][i], found["food"][i], found["non_food"][i]]) for i in range(width)]
    return _finish(spec, manifest_key, dated, [])


_PARSERS = {
    VERTICAL_LABELLED: _parse_vertical_labelled, ANNUAL_ROWS: _parse_annual_rows,
    QUOTED_MONTHS: _parse_quoted_months, HORIZONTAL_13: _parse_horizontal_13,
}


def parse_table(page_text: str, spec: TableSpec, manifest_key: str) -> ParsedTable:
    """Parse one pinned table from one page of text. Pure; raises ZamstatsCpiError on any identification or content failure."""
    validate_spec(spec)
    if not isinstance(page_text, str) or not page_text.strip():
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): page text is empty")
    _check_base(page_text, spec)
    lines = _table_lines(page_text, spec)
    return _PARSERS[spec.family](lines, spec, manifest_key)
