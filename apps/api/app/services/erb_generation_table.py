"""Strict interpretation of ERB 2025 Annual Statistical Bulletin Tables 13-16 (generation by station) and the Table 12 check.

Pure functions over the `pdftotext -layout` text of the three pinned PDF pages (22, 23, 24). Specification and every pin:
docs/research/zambia-energy-data-sources.md section 19. Orientation: ROWS are years (2013..2025), COLUMNS are stations plus one
diagnostic total column. A table is accepted only if its exact caption, ordered columns, 13 consecutive year rows and per-row
source-value-cell count match the pins; anything else fails the whole parse. No scanning outside the pinned pages.

Cells: a number (thousands separators removed, exact Decimal, printed precision kept), or `-`. `-` is allowed only in Tables 14
and 15 and means "not reported" (never zero); a printed `0` is the number zero. Totals are read for diagnostics only.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.services.erb_generation_models import (
    DIESEL, FOOTING_COMPUTED, FOOTING_PARTIAL, IPP, LARGE_HYDRO, MINI_HYDRO, NOT_REPORTED, REPORTED,
    ErbGenerationError, FootingDiagnostic, GenerationObservation, Table12Diagnostic, TableTotal,
)

YEARS = tuple(range(2013, 2026))
NUMBER_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
YEAR_ROW_RE = re.compile(r"^\s*(20\d\d)\s+\S")
DASH = "-"
MAX_LINES = 400


@dataclass(frozen=True)
class TableSpec:
    number: int
    caption: str  # normalized (single spaces)
    group: str
    stations: tuple[str, ...]  # exact source column identities, left to right
    total_label: str
    pdf_pages: tuple[int, ...]  # first page holds the caption; later pages are continuations (repeated header, no caption)
    dash_allowed: bool
    boundary: str | None  # exact heading that must be the next non-blank line after the rows; None: only the printed page follows

    @property
    def name(self) -> str:
        return f"Table {self.number}"

    @property
    def row_width(self) -> int:  # source value cells excluding the year: stations + the total
        return len(self.stations) + 1


TABLES: tuple[TableSpec, ...] = (
    TableSpec(13, "Table 13: Electricity generation from ZESCO's large hydro power plants (GWh), 2013-2025", LARGE_HYDRO,
              ("Kafue Gorge", "Kariba North Bank", "Victoria falls"), "Total (GWh)", (22,), False,
              "2.1.2 ZESCO\u2019s Electricity Generation from Small and Mini Hydro Power Stations"),
    TableSpec(14, "Table 14: Electricity generation from ZESCO's mini- hydro power plants (GWh), 2013-2025", MINI_HYDRO,
              ("Chishimba Falls", "Lunzua", "Lusiwasi Lower", "Lusiwasi Upper", "Musonda Falls", "Shiwan'gandu"),
              "Total (GWh)", (22, 23), True, None),
    TableSpec(15, "Table 15: Electricity generation from ZESCO's Diesel power plants (GWh), 2013-2025", DIESEL,
              ("Chama", "Itezhi tezhi", "Kabompo", "Luangwa", "Lundazi", "Shang'ombo", "Zambezi"), "Total (GWh)", (24,), True,
              "2.1.4 Electricity Generation from Independent Power Producers"),
    TableSpec(16, "Table 16: Electricity generation from independent power producers (GWh), 2013-2025", IPP,
              ("Bangweulu Power Limited", "Itezhi tezhi Power Corporation", "Kafue Gorge Lower", "Kariba North Bank Extension",
               "Lunsemfwa Hydro Power Limited", "Maamba Energy Limited", "Ndola Energy Corporation Limited",
               "Kafue Gorge Mini Hydro", "Chisamba", "Solar century", "United Capital Fertiliser", "Ngonye Power Limited",
               "Dangote", "CEC Emergency Generators"), "Total GWh", (24,), False, None),
)
TABLE_BY_NUMBER = {t.number: t for t in TABLES}
PINNED_PAGES = {22: 15, 23: 16, 24: 17}  # PDF page -> printed page

TABLE12_CAPTION = "Table 12: National Electricity Generation 2025"
TABLE12_HEADER = "Generator Generation (GWh)"
TABLE12_LABELS = ("ZESCO’s Major-Hydro", "ZESCO’s Mini-Hydro", "ZESCO’s Diesel", "IPPs", "Total")

# Pinned source discrepancies. Residual convention everywhere: PUBLISHED TOTAL minus the sum of the printed values, so a positive
# residual means the printed total exceeds the printed values. Nothing is repaired. Every other table-year must foot exactly; a
# different residual means the source is not the pinned one and fails closed.
EXPECTED_RESIDUALS = {(16, 2024): Decimal("0.05"), (16, 2025): Decimal("0.01")}
EXPECTED_TABLE12_DIFFERENCE = Decimal("2.50")  # Table 16 total 2025 minus Table 12 IPPs 2025
EXPECTED_TABLE12_RESIDUAL = Decimal("2.50")  # Table 12 printed total minus the sum of its four generator rows


def normalize(line: str) -> str:
    return " ".join(line.split())


def _lines(text: str) -> list[str]:
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        raise ErbGenerationError("a pinned page has more lines than expected")
    return lines


def parse_cell(token: str, *, dash_allowed: bool, where: str) -> Decimal | None:
    if token == DASH:
        if not dash_allowed:
            raise ErbGenerationError(f"{where}: a dash is not allowed in this table")
        return None
    if not NUMBER_RE.match(token):
        raise ErbGenerationError(f"{where}: unexpected cell token")
    return Decimal(token.replace(",", ""))


def _find_unique(lines: list[str], prefix: str, expected: str, label: str) -> int:
    hits = [i for i, line in enumerate(lines) if normalize(line).startswith(prefix)]
    if len(hits) != 1:
        raise ErbGenerationError(f"{label}: expected exactly one caption on the pinned page, found {len(hits)}")
    if normalize(lines[hits[0]]) != expected:
        raise ErbGenerationError(f"{label}: caption differs from the pinned caption")
    return hits[0]


def _year_block(lines: list[str], start: int, label: str) -> list[tuple[int, list[str]]]:
    """Consecutive year rows starting at `start`; the block ends at the first line that is not a year row."""
    rows: list[tuple[int, list[str]]] = []
    i = start
    while i < len(lines) and YEAR_ROW_RE.match(lines[i]):
        parts = lines[i].split()
        rows.append((int(parts[0]), parts[1:]))
        i += 1
    if not rows:
        raise ErbGenerationError(f"{label}: no year rows follow the header")
    return rows


def _check_region_end(spec: TableSpec, lines: list[str], end: int, printed: int) -> None:
    """The table region must end exactly at its approved boundary. Blank lines never end the check: the next non-blank line must
    be the exact boundary heading (Tables 13 and 15) or the printed page number with nothing after it (Tables 14 and 16), so a
    trailing or duplicate year row after whitespace is rejected rather than ignored."""
    rest = [normalize(line) for line in lines[end:] if line.strip()]
    if spec.boundary is not None:
        if not rest or rest[0] != normalize(spec.boundary):
            raise ErbGenerationError(f"{spec.name}: unexpected content after the table rows (expected the next section heading)")
    elif rest != [str(printed)]:
        raise ErbGenerationError(f"{spec.name}: unexpected content after the table rows (expected only printed page {printed})")


def _expected_header(spec: TableSpec) -> str:
    return normalize(" ".join(("Year", *spec.stations, spec.total_label)))


def _stacked_header_columns(header_lines: list[str], first_row: str, label: str) -> list[str]:
    """Table 16's header is stacked over several lines. Every header word joins the column whose first-row value is nearest
    (by character position); words of one column are joined top to bottom with single spaces. Whitespace-only normalization."""
    centers = [(m.start() + m.end()) / 2 for m in re.finditer(r"\S+", first_row)]
    columns: list[list[str]] = [[] for _ in centers]
    for line in header_lines:
        for m in re.finditer(r"\S+", line):
            c = (m.start() + m.end()) / 2
            columns[min(range(len(centers)), key=lambda k: abs(centers[k] - c))].append(m.group())
    return [" ".join(words) for words in columns]


def _read_table(spec: TableSpec, pages: dict[int, str], consumed: dict[int, set[int]]) -> list[tuple[int, int, list[str]]]:
    """(year, pdf_page, cells) rows of one table, verified against the pinned identity. Cells are raw tokens."""
    rows: list[tuple[int, int, list[str]]] = []
    label = spec.name
    for position, page in enumerate(spec.pdf_pages):
        lines = _lines(pages[page])
        printed = PINNED_PAGES[page]
        if position == 0:
            at = _find_unique(lines, f"Table {spec.number}:", spec.caption, label)
            header_at = at + 1
        else:  # continuation: repeated header, no caption, nothing else on the page
            if any(normalize(l).startswith(f"Table {spec.number}") for l in lines):
                raise ErbGenerationError(f"{label}: caption repeated on a continuation page")
            nonblank = [i for i, l in enumerate(lines) if l.strip()]
            header_at = nonblank[0]
        if spec.number == 16:
            first = next((i for i in range(header_at, len(lines)) if YEAR_ROW_RE.match(lines[i])), None)
            if first is None:
                raise ErbGenerationError(f"{label}: no year rows follow the header")
            first_cells = lines[first].split()[1:]  # the header is placed by the first row's cell positions: check that row first
            if len(first_cells) != spec.row_width:
                raise ErbGenerationError(f"{label} {lines[first].split()[0]}: {len(first_cells)} source value cells, expected {spec.row_width}")
            for token in first_cells:
                parse_cell(token, dash_allowed=spec.dash_allowed, where=f"{label} {lines[first].split()[0]}")
            names = _stacked_header_columns(lines[header_at:first], lines[first], label)  # the Year label lands on the year column
            if names != ["Year", *spec.stations, spec.total_label]:
                raise ErbGenerationError(f"{label}: header columns differ from the pinned ordered columns")
            body_at = first
        else:
            if normalize(lines[header_at]) != _expected_header(spec):
                raise ErbGenerationError(f"{label}: header differs from the pinned ordered columns")
            body_at = header_at + 1
        block = _year_block(lines, body_at, label)
        for year, cells in block:
            if len(cells) != spec.row_width:
                raise ErbGenerationError(f"{label} {year}: {len(cells)} source value cells, expected {spec.row_width}")
            rows.append((year, page, cells))
        end = body_at + len(block)
        consumed.setdefault(page, set()).update(range(body_at, end))
        _check_region_end(spec, lines, end, printed)
    if [y for y, _, _ in rows] != list(YEARS):
        raise ErbGenerationError(f"{label}: years are not exactly 2013..2025 in ascending order, once each")
    return rows


def parse_generation_tables(
    pages: dict[int, str], *, source_url: str, source_publication: str, publication_year: int, source_sha256: str,
) -> tuple[list[GenerationObservation], list[TableTotal], list[FootingDiagnostic]]:
    if set(pages) != set(PINNED_PAGES):
        raise ErbGenerationError("exactly the pinned pages 22, 23 and 24 are required")
    for page, printed in PINNED_PAGES.items():
        tail = [normalize(l) for l in _lines(pages[page]) if l.strip()]
        if not tail or tail[-1] != str(printed):
            raise ErbGenerationError(f"PDF page {page}: printed page number {printed} not found")
    observations: list[GenerationObservation] = []
    totals: list[TableTotal] = []
    footing: list[FootingDiagnostic] = []
    consumed: dict[int, set[int]] = {}
    for spec in TABLES:
        for year, page, cells in _read_table(spec, pages, consumed):
            where = f"{spec.name} {year}"
            values = [parse_cell(t, dash_allowed=spec.dash_allowed, where=where) for t in cells]
            total = parse_cell(cells[-1], dash_allowed=False, where=f"{where} total")
            assert total is not None
            totals.append(TableTotal(spec.name, year, total, page))
            for station, token, value in zip(spec.stations, cells, values):
                observations.append(GenerationObservation(
                    observation_year=year, station_name=station, station_group=spec.group, generation_gwh=value,
                    value_state=REPORTED if value is not None else NOT_REPORTED, source_table=spec.name,
                    source_pdf_page=page, source_printed_page=PINNED_PAGES[page], source_url=source_url,
                    source_publication=source_publication, source_sha256=source_sha256, publication_year=publication_year,
                ))
            station_values = values[:-1]
            missing = sum(1 for v in station_values if v is None)
            reported_sum = sum((v for v in station_values if v is not None), Decimal(0))
            residual = total - reported_sum
            expected = EXPECTED_RESIDUALS.get((spec.number, year), Decimal(0))
            if residual != expected:
                raise ErbGenerationError(f"{where}: footing residual differs from the pinned source value")
            footing.append(FootingDiagnostic(spec.name, year, FOOTING_PARTIAL if missing else FOOTING_COMPUTED, missing,
                                             reported_sum, total, residual))
    for page in PINNED_PAGES:  # every year-shaped line on a pinned page must belong to an accepted table row
        year_lines = {i for i, line in enumerate(_lines(pages[page])) if YEAR_ROW_RE.match(line)}
        if year_lines != consumed.get(page, set()):
            raise ErbGenerationError(f"PDF page {page}: a year-shaped row is outside the accepted table rows")
    return observations, totals, footing


def check_grid(observations: list[GenerationObservation]) -> None:
    per_table = {t.name: len(t.stations) * len(YEARS) for t in TABLES}  # 39, 78, 91, 182
    counts: dict[str, int] = {}
    for o in observations:
        counts[o.source_table] = counts.get(o.source_table, 0) + 1
    if counts != per_table or len(observations) != sum(per_table.values()) or len(observations) != 390:
        raise ErbGenerationError("the station/year grid is not the expected 390 observations")
    if len({(o.source_table, o.station_name, o.observation_year) for o in observations}) != len(observations):
        raise ErbGenerationError("duplicate station/year observation")


def parse_table12_diagnostic(page22: str, table16_total_2025: Decimal) -> Table12Diagnostic:
    lines = _lines(page22)
    at = _find_unique(lines, "Table 12:", TABLE12_CAPTION, "Table 12")
    if normalize(lines[at + 1]) != TABLE12_HEADER:
        raise ErbGenerationError("Table 12: header differs from the pinned header")
    values: list[Decimal] = []
    for offset, label in enumerate(TABLE12_LABELS, start=2):
        parts = normalize(lines[at + offset]).rsplit(" ", 1)
        if len(parts) != 2 or parts[0] != label:
            raise ErbGenerationError("Table 12: row labels differ from the pinned rows")
        values.append(parse_cell(parts[1], dash_allowed=False, where=f"Table 12 {label}"))  # type: ignore[arg-type]
    major, mini, diesel, ipp, total = values
    rows_sum = major + mini + diesel + ipp
    difference = table16_total_2025 - ipp
    residual = total - rows_sum
    if difference != EXPECTED_TABLE12_DIFFERENCE or residual != EXPECTED_TABLE12_RESIDUAL:
        raise ErbGenerationError("Table 12 / Table 16 relationship differs from the pinned source values")
    return Table12Diagnostic(table16_total_2025, ipp, difference, total, rows_sum, residual)
