"""Brent and WTI monthly prices from the World Bank Commodity Price Data (The Pink Sheet) workbook.

Verified 2026-11-14 against the official workbook:
- Discovery: https://www.worldbank.org/en/research/commodity-markets links "Monthly prices" to
  https://thedocs.worldbank.org/en/doc/<hash>/related/CMO-Historical-Data-Monthly.xlsx. The <hash> changes between
  releases, so the page is the discovery mechanism; exactly one such link must be present.
- Sheet "Monthly Prices". Rows 1-4: title "World Bank Commodity Price Data (The Pink Sheet)", "monthly prices in
  nominal US dollars, 1960 to present", a note, and "Updated on November 03, 2026". Then a header row whose cells are
  series labels ("Crude oil, Brent", "Crude oil, WTI", ...), a unit row ("($/bbl)"), and one row per month keyed
  "YYYYMmm" in column A ("2026M10"), consecutive from 1960M01. Commodities are columns, months are rows.
- Missing cells (e.g. WTI before 1982) hold the character "…". They are "not published", never zero.
- Cells are numbers displayed with one decimal ("0.0"); the PDF edition prints the same monthly averages.
- Past months can be revised between releases (the workbook's "Mismatch Details" sheet lists release-to-release
  differences). A revised value surfaces here as a persistence conflict, never as a silent overwrite.

The parse fails closed on any layout change: sheet, title, header labels, units, month keys, gaps and unusable cells.
Nothing is interpolated, forward-filled or repaired, and unrelated commodity columns are never read.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.schemas.oil import OIL_BENCHMARKS, OIL_SERIES, OIL_SOURCE_UNIT, OilPoint

WB_PAGE_HOST = "www.worldbank.org"
WB_FILE_HOST = "thedocs.worldbank.org"
WB_USER_AGENT = "ZM-Terminal/0.1 (World Bank Pink Sheet adapter)"
WB_TIMEOUT_SECONDS = 30.0
MAX_PAGE_BYTES = 3_000_000
MAX_XLSX_BYTES = 15_000_000  # the real file is about 0.6 MB
MAX_UNCOMPRESSED_BYTES = 100_000_000
WORKBOOK_NAME = "CMO-Historical-Data-Monthly.xlsx"
SHEET_NAME = "Monthly Prices"
TITLE = "World Bank Commodity Price Data (The Pink Sheet)"
SUBTITLE_PREFIX = "monthly prices in nominal US dollars"
MISSING_MARKER = "…"
HEADER_SEARCH_ROWS = 12

_LINK = re.compile(r'href="(https://thedocs\.worldbank\.org/[^"\s]*/' + re.escape(WORKBOOK_NAME) + r')"')
_MONTH_KEY = re.compile(r"([0-9]{4})M(0[1-9]|1[0-2])")
_UPDATED = re.compile(r"Updated on ([A-Z][a-z]+ [0-9]{1,2}, [0-9]{4})")


class WorldBankOilError(ValueError):
    """The workbook (or its discovery page) is not in the verified layout, or contains an unusable value."""


@dataclass(frozen=True)
class OilWorkbook:
    publication_date: date  # the workbook's own "Updated on" date
    observations: list[OilPoint]  # every published Brent/WTI month, ascending by month then benchmark
    first_month: date  # first and last month rows present in the sheet (not necessarily published for every series)
    last_month: date
    missing_cells: int


def _month_end(first: date) -> date:
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _normalize_cell(value: object) -> Decimal | None:
    """One price cell -> Decimal at published precision; None if not published; WorldBankOilError if unusable."""
    if value is None or (isinstance(value, str) and value.strip() in ("", MISSING_MARKER)):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldBankOilError("Unexpected price cell type")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise WorldBankOilError("Price is not finite")
    price = Decimal(f"{number:.15g}")
    if price.as_tuple().exponent < -2 or abs(price) >= Decimal("100000"):
        raise WorldBankOilError("Unexpected price precision or magnitude")
    return price


def parse_workbook(source: bytes) -> OilWorkbook:
    """Parse the whole workbook; never partially returns on a layout failure."""
    from openpyxl import load_workbook  # lazy: only the import job needs it, not the API runtime

    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise WorldBankOilError("Workbook is unexpectedly large when uncompressed")
        book = load_workbook(io.BytesIO(source), read_only=True, data_only=True)
    except WorldBankOilError:
        raise
    except Exception as exc:  # corrupt/unsupported file; do not leak library internals
        raise WorldBankOilError("Workbook could not be read") from exc
    sheets = [ws for ws in book.worksheets if ws.title == SHEET_NAME]
    if len(sheets) != 1:
        raise WorldBankOilError("Expected exactly one 'Monthly Prices' worksheet")
    rows = [tuple(row) for row in sheets[0].iter_rows(values_only=True)]
    book.close()

    cell = lambda r, c: rows[r][c] if r < len(rows) and c < len(rows[r]) else None
    if str(cell(0, 0) or "").strip() != TITLE or not str(cell(1, 0) or "").startswith(SUBTITLE_PREFIX):
        raise WorldBankOilError("Unexpected workbook title")
    updated = [m.group(1) for r in range(0, 6) if isinstance(cell(r, 0), str) for m in [_UPDATED.search(cell(r, 0))] if m]
    if len(updated) != 1:
        raise WorldBankOilError("Expected exactly one 'Updated on' date")
    try:
        publication_date = datetime.strptime(updated[0], "%B %d, %Y").date()
    except ValueError as exc:
        raise WorldBankOilError("Unreadable 'Updated on' date") from exc

    # Locate columns by exact label, never by position.
    header_rows = [r for r in range(min(len(rows), HEADER_SEARCH_ROWS))
                   if any(isinstance(v, str) and v.strip() == OIL_SERIES["BRENT"].source_series for v in rows[r])]
    if len(header_rows) != 1:
        raise WorldBankOilError("Header row not found exactly once")
    header_row = header_rows[0]
    columns: dict[str, int] = {}
    for benchmark in OIL_BENCHMARKS:
        label = OIL_SERIES[benchmark].source_series
        matches = [c for c, v in enumerate(rows[header_row]) if isinstance(v, str) and v.strip() == label]
        if len(matches) != 1:
            raise WorldBankOilError(f"Series label for {benchmark} not found exactly once")
        columns[benchmark] = matches[0]
        if str(cell(header_row + 1, matches[0]) or "").strip() != OIL_SOURCE_UNIT:
            raise WorldBankOilError(f"Unexpected unit for {benchmark}")

    observations: list[OilPoint] = []
    missing = 0
    previous: date | None = None
    first_month: date | None = None
    for r in range(header_row + 2, len(rows)):
        key = cell(r, 0)
        if key is None and all(v is None for v in rows[r]):
            continue  # trailing blank row
        match = _MONTH_KEY.fullmatch(key.strip()) if isinstance(key, str) else None
        if not match:
            raise WorldBankOilError("Unexpected row key in the monthly data")
        month = date(int(match.group(1)), int(match.group(2)), 1)
        if previous is not None and month != date(previous.year + (previous.month == 12), previous.month % 12 + 1, 1):
            raise WorldBankOilError("Months are not consecutive")
        previous = month
        first_month = first_month or month
        for benchmark in OIL_BENCHMARKS:
            value = _normalize_cell(cell(r, columns[benchmark]))
            if value is None:
                missing += 1
            else:
                observations.append(OilPoint(benchmark=benchmark, period_start=month, period_end=_month_end(month), value=value))
    if first_month is None or previous is None:
        raise WorldBankOilError("No monthly rows found")
    return OilWorkbook(publication_date, observations, first_month, previous, missing)


def _check_url(url: str, host: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != host:
        raise WorldBankOilError("Unexpected World Bank URL")


async def _get_limited(client: httpx.AsyncClient, url: str, host: str, limit: int) -> bytes:
    _check_url(url, host)
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        _check_url(str(response.url), host)  # redirects must stay on the official host
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise WorldBankOilError("World Bank response is too large")
    return bytes(body)


async def discover_workbook_url(client: httpx.AsyncClient) -> str:
    """The monthly workbook link on the official Commodity Markets page; exactly one distinct link must exist."""
    page = (await _get_limited(client, get_settings().worldbank_commodity_page_url, WB_PAGE_HOST, MAX_PAGE_BYTES)).decode("utf-8", "replace")
    links = sorted(set(_LINK.findall(page)))
    if len(links) != 1:
        raise WorldBankOilError("Expected exactly one monthly workbook link on the World Bank page")
    return links[0]


async def download_workbook(client: httpx.AsyncClient, url: str) -> bytes:
    body = await _get_limited(client, url, WB_FILE_HOST, MAX_XLSX_BYTES)
    if not body.startswith(b"PK"):
        raise WorldBankOilError("Download is not an XLSX file")
    return body


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": WB_USER_AGENT}, timeout=WB_TIMEOUT_SECONDS, follow_redirects=True)
