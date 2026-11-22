"""Pure layout parsers for the ZamStats Monthly Bulletin CPI tables (no I/O, no storage, no network, no database).

Each parser works on the text of ONE already-extracted PDF page and a manifest `TableSpec`. A table is accepted only if
- its exact title anchor matches the pinned number of lines and its pinned closing boundary appears exactly the pinned number of
  times after it (a missing or duplicated boundary fails; `Source:` is the default, a different real boundary is pinned per table);
- the ordered column headings match the pinned heading line exactly (a swapped, missing, duplicated, unknown or extra heading
  fails: categories are never inferred from numeric position);
- EVERY line of the bounded table block is classified (month row, year label, wrapped continuation, explicitly authorised
  duplicate row, pinned header/weight/base line, or an explicitly allowed footer line); a malformed month-like line, a row of the wrong
  width or any unclassified text fails, nothing is silently skipped;
- its month rows form one contiguous run that starts and ends exactly where the manifest pins it, every year is assigned explicitly,
  every number matches the family's vetted grammar, and the page reports no base period other than `2009 = 100`.
Published rates are authoritative observations: nothing is calculated, repaired, rounded or inferred. A duplicated printed row is
collapsed only when BOTH the spec and the module's AUTHORIZED_DUPLICATE_ROWS registry bind that exception to this exact manifest
publication, table and month (the permission is not transferable by building a similar spec), and it is then reported. Error
messages name tables, pages and months, never source values or text.

Layout families (each positively identified by its spec, no fuzzy matching):
- `vertical_labelled`: one row per month, two or three value columns; the year appears either as a label on its own line inside
  its 12-month block or as a prefix on one row of the block.
- `annual_rows`: `Year  Month  Annual CPI  Annual Inflation Rate`, the year printed on every row.
- `quoted_months`: `Mon 'YY - Mon 'YY  Total  Food  Non-Food` rows (month-on-month rates), the month stated explicitly per row.
- `horizontal_13`: twelve or thirteen consecutive month columns; the rows Total, Food and Non-Food carry their fixed weights (or none
  in one layout). The header month NAMES are validated; header YEARS are deliberately not used because printed years in these headers
  contain typos. Month identity is instead anchored independently by the manifest: the exact pinned page and table, the ordered fixed
  window, the pinned first and last month and the layout contract. That independent anchor must stay: do not loosen it.
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

# (manifest key, table id, month): the ONLY adjacent duplicated printed rows that may be collapsed. Bound to the vetted publication
# and table; a spec cannot grant this permission to another context.
AUTHORIZED_DUPLICATE_ROWS: frozenset = frozenset({
    ("zam-cpi-2012-12", "Consumer Index Numbers of Consumer Prices - Food and Non-Food (National)", date(2010, 12, 1)),
})

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_TOKEN = r"[(+\-.\d][^\s]*"  # a loose numeric-looking token; the strict grammar is applied afterwards
_BASE = re.compile(r"\b((?:19|20)\d\d)\s*=\s*100\b")
_HORIZONTAL_WEIGHTS = {"total": r"1[, ]?000(?:\.0{1,2})?", "food": r"534\.85", "non_food": r"465\.15"}
# Lines allowed inside any table block besides data: the weight line, the base label, a wrapped "(%)" title tail and a wrapped
# "Month" column caption. Table-specific footer lines are pinned per table (`extra_allowed`).
_GENERIC_ALLOWED = (
    r"Weight:?(?: [0-9][0-9., ]*)?", r"Month", r"(?:(?:19|20)\d\d )?\(%\)", r"(?:19|20)\d\d = 100",
)


@dataclass(frozen=True)
class TableSpec:
    """One pinned table of one vetted publication."""
    table_id: str  # source table identity, e.g. "Annex 1.1", "Table 5" (printed)
    family: str
    pdf_page: int
    title_regex: str  # matched against the lines of the page; must match exactly `title_matches` of them
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
    # Ordered column headings: a whitespace-normalised pattern that must FULL-match exactly one line of the table block. Required
    # for every family except horizontal_13, whose Total/Food/Non-Food row labels and fixed weights carry the column identity.
    header_regex: str | None = None
    boundary_regex: str = r"^\s*Source\s*:"  # the pinned closing boundary of the table block (the real one, per table)
    boundary_count: int = 1  # how many such lines are printed consecutively at the closing boundary (missing or duplicated fails)
    extra_allowed: tuple[str, ...] = ()  # table-specific footer/caption lines allowed inside the block (normalised full-match)


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
    if spec.family != HORIZONTAL_13 and not spec.header_regex:
        raise ZamstatsCpiError(f"{spec.table_id}: the ordered column headings are not pinned")
    if spec.boundary_count < 1 or not spec.boundary_regex:
        raise ZamstatsCpiError(f"{spec.table_id}: the closing boundary is not pinned")
    for pattern in (spec.title_regex, spec.boundary_regex, *(spec.extra_allowed), *((spec.header_regex,) if spec.header_regex else ())):
        try:
            re.compile(pattern)
        except re.error:
            raise ZamstatsCpiError(f"{spec.table_id}: an anchor pattern is not a valid regular expression") from None


def _norm(line: str) -> str:
    return " ".join(line.split())


def _table_block(page_text: str, spec: TableSpec) -> list[str]:
    """The page lines from the title anchor to the pinned closing boundary (exclusive). Fails on a wrong number of title anchors, a
    missing boundary or a duplicated boundary."""
    lines = page_text.splitlines()
    anchors = [i for i, line in enumerate(lines) if re.search(spec.title_regex, line, re.IGNORECASE)]
    if len(anchors) != spec.title_matches or not 1 <= spec.title_pick <= spec.title_matches:
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): table title matched {len(anchors)} times, expected {spec.title_matches}")
    start = anchors[spec.title_pick - 1]
    first = next((i for i in range(start + 1, len(lines)) if re.match(spec.boundary_regex, lines[i], re.IGNORECASE)), None)
    if first is None:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the closing boundary is missing")
    # the closing boundary is the first match; consecutive boundary lines right after it (blank lines aside) are a duplicated
    # boundary and must equal the pinned count
    run = 1
    for line in lines[first + 1:]:
        if not line.strip():
            continue
        if re.match(spec.boundary_regex, line, re.IGNORECASE):
            run += 1
            continue
        break
    if run != spec.boundary_count:
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): the closing boundary is printed {run} times in a row, expected {spec.boundary_count}")
    return lines[start:first]


def _check_base(page_text: str, spec: TableSpec) -> None:
    bases = {m.group(1) for m in _BASE.finditer(page_text)}
    if bases - {"2009"}:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): reports a base period other than {REGIME}")
    if spec.base_on_page and "2009" not in bases:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the {REGIME} base label is missing")


def _is_allowed(normalised: str, spec: TableSpec) -> bool:
    return any(re.fullmatch(pattern, normalised) for pattern in (*_GENERIC_ALLOWED, *spec.extra_allowed))


def _month_like(normalised: str) -> bool:
    """A line that starts like a month row (an optional year, then a month name or a quoted `Mon 'YY` label)."""
    parts = normalised.split(" ")
    if parts and re.fullmatch(r"(?:19|20)\d\d", parts[0]) and len(parts) > 1:
        parts = parts[1:]
    return bool(parts) and (parts[0].lower().strip("'") in _MONTHS or bool(re.match(r"^[A-Za-z]+\s*['’]\d\d\b", " ".join(parts))))


def _label_only(line: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d\d", line))  # a printed year label on its own line (the quoted layout does not rely on it)


def _unexpected(spec: TableSpec, what: str, number: int) -> ZamstatsCpiError:
    return ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): {what} at table line {number}")


def _check_header(block: list[str], spec: TableSpec, counted: int) -> None:
    if counted != 1:
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): the ordered column headings matched {counted} lines, expected exactly 1")


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


def _row_regex(spec: TableSpec, prefix: str) -> re.Pattern:
    n = len(spec.columns)
    return re.compile(rf"^{prefix}(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{n - 1}}})$")


# ---------------------------------------------------------------- vertical_labelled
def _parse_vertical_labelled(block: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    row_re = _row_regex(spec, r"(?:(?P<y>(?:19|20)\d\d) )?(?P<mon>[A-Za-z]+) ")
    events: list[tuple] = []  # ("row", month_no, prefix_year|None, tokens, normalised) / ("label", year)
    headings = 0
    for number, raw in enumerate(block, start=1):
        line = _norm(raw)
        if not line or number == 1:
            continue  # the first block line is the title anchor itself
        if spec.header_regex and re.fullmatch(spec.header_regex, line):
            headings += 1
            continue
        if _label_only(line):
            events.append(("label", int(line)))
            continue
        if _month_like(line):
            m = row_re.match(line)
            if not m or m.group("mon").lower() not in _MONTHS:
                raise _unexpected(spec, "malformed month row", number)
            events.append(("row", _MONTHS[m.group("mon").lower()], m.group("y"), m.group("v").split(), line))
            continue
        if _is_allowed(line, spec):
            continue
        raise _unexpected(spec, "unclassified line", number)
    _check_header(block, spec, headings)

    # Adjacent byte-identical printed rows: only a duplicate bound to THIS publication, table and month (spec AND registry) is
    # collapsed (and reported); otherwise fail closed.
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
    bound = {m for (key, table, m) in AUTHORIZED_DUPLICATE_ROWS if key == manifest_key and table == spec.table_id}
    authorised = set(spec.authorized_duplicate_months) & bound
    if len(duplicate_positions) != len(authorised):
        raise ZamstatsCpiError(
            f"{spec.table_id} (page {spec.pdf_page}): {len(duplicate_positions)} duplicated printed rows, "
            f"{len(authorised)} authorised for this publication and table")

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
        if month not in authorised:
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): duplicated row is not an authorised month")
        duplicate_rows.append(DuplicateRow(spec.table_id, spec.pdf_page, month, position))
    return _finish(spec, manifest_key, dated, duplicate_rows)


# ---------------------------------------------------------------- annual_rows
def _parse_annual_rows(block: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    row_re = _row_regex(spec, r"(?P<y>(?:19|20)\d\d) (?P<mon>[A-Za-z]+) ")
    dated: list[tuple[date, list[str]]] = []
    headings = 0
    for number, raw in enumerate(block, start=1):
        line = _norm(raw)
        if not line or number == 1:
            continue
        if spec.header_regex and re.fullmatch(spec.header_regex, line):
            headings += 1
            continue
        if _month_like(line):
            m = row_re.match(line)
            if not m or m.group("mon").lower() not in _MONTHS:
                raise _unexpected(spec, "malformed month row", number)
            dated.append((date(int(m.group("y")), _MONTHS[m.group("mon").lower()], 1), m.group("v").split()))
            continue
        if _is_allowed(line, spec):
            continue
        raise _unexpected(spec, "unclassified line", number)
    _check_header(block, spec, headings)
    return _finish(spec, manifest_key, dated, [])


# ---------------------------------------------------------------- quoted_months
def _parse_quoted_months(block: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    n = len(spec.columns)
    row_re = re.compile(
        rf"^(?:(?:19|20)\d\d )?(?P<m1>[A-Za-z]+) ?['’](?P<y1>\d\d) ?- ?(?P<m2>[A-Za-z]+) ?['’](?P<y2>\d\d) "
        rf"(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{n - 1}}})$")
    dated: list[tuple[date, list[str]]] = []
    headings = 0
    for number, raw in enumerate(block, start=1):
        line = _norm(raw)
        if not line or number == 1:
            continue
        if spec.header_regex and re.fullmatch(spec.header_regex, line):
            headings += 1
            continue
        if _label_only(line):
            continue
        if _month_like(line):
            m = row_re.match(line)
            if not m or m.group("m1").lower() not in _MONTHS or m.group("m2").lower() not in _MONTHS:
                raise _unexpected(spec, "malformed month row", number)
            current = date(2000 + int(m.group("y1")), _MONTHS[m.group("m1").lower()], 1)
            previous = date(2000 + int(m.group("y2")), _MONTHS[m.group("m2").lower()], 1)
            if previous != _add_month(current, -1):
                raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): a row does not compare with the preceding month")
            dated.append((current, m.group("v").split()))
            continue
        if _is_allowed(line, spec):
            continue
        raise _unexpected(spec, "unclassified line", number)
    _check_header(block, spec, headings)
    return _finish(spec, manifest_key, dated, [])


# ---------------------------------------------------------------- horizontal_13
def _parse_horizontal_13(block: list[str], spec: TableSpec, manifest_key: str) -> ParsedTable:
    if [c[1] for c in spec.columns] != ["all_items", "food", "non_food"] or len({c[0] for c in spec.columns}) != 1:
        raise ZamstatsCpiError(f"{spec.table_id}: horizontal_13 needs the All Items, Food, Non-Food columns of one metric")
    width = _months_between(spec.first_month, spec.last_month) + 1
    if width not in (12, 13):
        raise ZamstatsCpiError(f"{spec.table_id}: horizontal tables pin twelve or thirteen consecutive months")
    names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    month_token = re.compile(rf"\b({names})\b", re.IGNORECASE)
    values = rf"(?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{width - 1}}})"
    tail = spec.wrapped_non_food_tail
    if not spec.horizontal_weights:
        if tail:
            raise ZamstatsCpiError(f"{spec.table_id}: a weightless layout has no wrapped Non-Food variant")
        patterns = {"all_items": rf"Total {values}", "food": rf"Food {values}", "non_food": rf"Non-Food {values}"}
    else:
        patterns = {
            "all_items": rf"Total {_HORIZONTAL_WEIGHTS['total']} {values}",
            "food": rf"Food {_HORIZONTAL_WEIGHTS['food']} {values}",
            # the "Non-" / "Food" words may sit on separate physical lines; the fixed weight identifies the row
            "non_food": rf"(?:Non-? ?(?:Food )?)?{_HORIZONTAL_WEIGHTS['non_food']} {values}",
        }
    if tail:
        head = width - tail
        patterns["non_food"] = rf"{_HORIZONTAL_WEIGHTS['non_food']} (?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{head - 1}}})"
        tail_row = re.compile(rf"^Non-? (?P<v>{_TOKEN}(?:\s+{_TOKEN}){{{tail - 1}}})$")
    compiled = {category: re.compile(f"^{pattern}$") for category, pattern in patterns.items()}
    found: dict[str, list[list[str]]] = {category: [] for category in compiled}
    tails: list[list[str]] = []
    headers: list[list[int]] = []
    year_line = re.compile(r"^(?:\d{2}|\d{4})(?: (?:\d{2}|\d{4})){%d}$" % (width - 1))
    for number, raw in enumerate(block, start=1):
        line = _norm(raw)
        if not line or number == 1:
            continue
        tokens = month_token.findall(line)
        if len(tokens) == width and not re.sub(rf"\b(?:{names})\b|[-\d\s]", "", line, flags=re.IGNORECASE):
            headers.append([_MONTHS[t.lower()] for t in tokens])
            continue
        hit = False
        for category, pattern in compiled.items():
            m = pattern.match(line)
            if m:
                found[category].append(m.group("v").split())
                hit = True
                break
        if hit:
            continue
        if tail and tail_row.match(line):
            tails.append(tail_row.match(line).group("v").split())
            continue
        if year_line.match(line) or re.fullmatch(r"Food|Non-|Non-Food", line) or _is_allowed(line, spec):
            continue
        raise _unexpected(spec, "unclassified line", number)
    if len(headers) != 1:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): found {len(headers)} {width}-month header lines, expected 1")
    expected = [_add_month(spec.first_month, i).month for i in range(width)]
    if headers[0] != expected:
        raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the header months do not match the pinned window")
    rows: dict[str, list[str]] = {}
    for category, matches in found.items():
        if len(matches) != 1:
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): {category} row matched {len(matches)} times, expected 1")
        rows[category] = matches[0]
    if tail:
        if len(tails) != 1:
            raise ZamstatsCpiError(f"{spec.table_id} (page {spec.pdf_page}): the wrapped non_food row was not found exactly once")
        rows["non_food"] = rows["non_food"] + tails[0]
    dated = [(_add_month(spec.first_month, i), [rows["all_items"][i], rows["food"][i], rows["non_food"][i]]) for i in range(width)]
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
    return _PARSERS[spec.family](_table_block(page_text, spec), spec, manifest_key)
