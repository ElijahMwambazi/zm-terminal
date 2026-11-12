"""ERB Zambia fuel prices from the official "Pump and Wholesale Prices" XLSX archive.

Verified 2026-11-12: ERB's public WordPress media API lists the archive
(`/wp-json/wp/v2/media?search=PUMP AND WHOLESALE PRICES&mime_type=application/zip`), a ZIP holding
one XLSX with sheets "Wholesale Prices" and "Retail Prices " (trailing space) of monthly prices
since 2012: Year, Month, then Petrol, Diesel, Kerosene, Jet A-1 (K/L), Low Sulphur Gas oil (K/L);
the wholesale sheet adds HFO/LPG/Bitumen columns (unit "Kg/L", ambiguous, ignored).

Precision: ERB archive prices are normalized to the official workbook's displayed ZMW
precision. Binary Excel floating-point representation is not treated as additional published
precision: cells formatted with two decimals become Decimal(f"{v:.2f}") and must equal the
stored value within float noise (1e-9); a cell whose stored value carries real precision hidden
by the format is rejected and counted, never rounded. "General" cells display up to 15
significant digits, so they are normalized through that representation. Any other number
format, header, sheet or value type fails the parse. "-", blank and missing cells are
"not published" (None), never zero. Nothing is interpolated or forward-filled.
"""

import io
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from app.config import get_settings

ERB_HOST = "www.erb.org.zm"
ERB_USER_AGENT = "ZM-Terminal/0.1 (ERB Zambia fuel adapter)"
ERB_TIMEOUT_SECONDS = 20.0
MAX_ZIP_BYTES = 5_000_000
MAX_XLSX_BYTES = 20_000_000
MAX_UNCOMPRESSED_BYTES = 100_000_000

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
TWO_DP_FORMATS = {"0.00", "#,##0.00"}
PRODUCT_COLUMNS = {  # header -> (product, unit)
    "Petrol (K/L)": ("PETROL", "litre"), "Diesel (K/L)": ("DIESEL", "litre"),
    "Kerosene (K/L)": ("KEROSENE", "litre"), "Jet A-1 (K/L)": ("JET_A1", "litre"),
    "Low Sulphur Gas oil (K/L)": ("LSGO", "litre"),
}
IGNORED_COLUMNS = {"HFO (Kg/L)", "LPG (Kg/L)", "Bitumen (Kg/L)"}  # unit is ambiguous; not modelled
SHEETS = {  # stripped sheet name -> price_type
    "Retail Prices": "retail", "Wholesale Prices": "wholesale",
}
EXPECTED_HEADERS = {
    "retail": ["Year", "Month", "Petrol (K/L)", "Diesel (K/L)", "Kerosene (K/L)", "Jet A-1 (K/L)",
               "Low Sulphur Gas oil (K/L)"],
    "wholesale": ["Year", "Month", "Petrol (K/L)", "Diesel (K/L)", "Kerosene (K/L)", "Jet A-1 (K/L)",
                  "HFO (Kg/L)", "LPG (Kg/L)", "Bitumen (Kg/L)", "Low Sulphur Gas oil (K/L)"],
}


class FuelArchiveError(ValueError):
    """The workbook is not in the verified layout, or contains an unusable value."""


class _HiddenPrecision(Exception):
    pass


@dataclass(frozen=True)
class FuelRow:
    effective_date: date
    product: str
    price_type: str
    price_zmw: Decimal
    unit: str


@dataclass
class ParseStats:
    records_examined: int = 0
    valid_rows: int = 0
    missing_cells: int = 0
    hidden_precision_cells: int = 0
    ambiguous_duplicate_cells: int = 0
    identical_duplicate_cells: int = 0


def normalize_price(value: object, number_format: str) -> Decimal | None:
    """One price cell -> Decimal at displayed precision, None if not published.

    Raises FuelArchiveError for unusable values/formats and _HiddenPrecision when the stored
    value carries precision the two-decimal display hides.
    """
    if value is None or (isinstance(value, str) and value.strip() in ("", "-")):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FuelArchiveError("Unexpected price cell type")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise FuelArchiveError("Price must be finite and positive")
    if number_format in TWO_DP_FORMATS:
        price = Decimal(f"{number:.2f}")
        if abs(float(price) - number) > 1e-9:
            raise _HiddenPrecision()
        return price
    if number_format == "General":
        price = Decimal(f"{number:.15g}")
        if price.as_tuple().exponent < -8:
            raise FuelArchiveError("Unexpected decimal precision")
        return price if price.as_tuple().exponent <= -2 else price.quantize(Decimal("0.01"))
    raise FuelArchiveError("Unexpected number format")


def parse_workbook(source: bytes) -> tuple[list[FuelRow], ParseStats]:
    """Parse the whole archive workbook; never partially returns on a layout failure."""
    from openpyxl import load_workbook  # lazy: only the import job needs it, not the API runtime

    try:
        book = load_workbook(io.BytesIO(source), data_only=True)
    except Exception as exc:  # corrupt/unsupported file; do not leak library internals
        raise FuelArchiveError("Workbook could not be read") from exc
    sheets = {}
    for ws in book.worksheets:
        kind = SHEETS.get(ws.title.strip())
        if kind:
            if kind in sheets:
                raise FuelArchiveError("Duplicate worksheet")
            sheets[kind] = ws
    if set(sheets) != {"retail", "wholesale"}:
        raise FuelArchiveError("Unexpected worksheets")
    stats = ParseStats()
    rows: list[FuelRow] = []
    for kind in ("retail", "wholesale"):
        rows.extend(_parse_sheet(sheets[kind], kind, stats))
    stats.valid_rows = len(rows)
    return rows, stats


def _parse_sheet(ws, kind: str, stats: ParseStats) -> list[FuelRow]:
    expected = EXPECTED_HEADERS[kind]
    width = len(expected)
    header = [c.value.strip() if isinstance(c.value, str) else c.value for c in ws[1]]
    if header[:width] != expected or any(h is not None for h in header[width:]):
        raise FuelArchiveError("Unexpected worksheet header")
    seen: dict[tuple[date, str], list[Decimal]] = {}
    units: dict[str, str] = {}
    for row in ws.iter_rows(min_row=2):
        year, month = row[0].value, row[1].value
        cells = row[:width]
        extra = [c.value for c in row[width:]]
        if any(v not in (None, "") for v in extra):
            raise FuelArchiveError("Unexpected data outside the verified columns")
        if year is None and month is None:
            if any(c.value not in (None, "") for c in cells[2:]):
                raise FuelArchiveError("Price row without a date")
            continue
        if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100:
            raise FuelArchiveError("Invalid year")
        if not isinstance(month, str) or month.strip() not in MONTHS:
            raise FuelArchiveError("Invalid month")
        effective = date(year, MONTHS[month.strip()], 1)
        stats.records_examined += 1
        for cell, head in zip(cells[2:], expected[2:]):
            if head in IGNORED_COLUMNS:
                continue
            product, unit = PRODUCT_COLUMNS[head]
            units[product] = unit
            try:
                price = normalize_price(cell.value, cell.number_format)
            except _HiddenPrecision:
                stats.hidden_precision_cells += 1
                continue
            if price is None:
                stats.missing_cells += 1
                continue
            seen.setdefault((effective, product), []).append(price)
    out: list[FuelRow] = []
    for (effective, product), prices in sorted(seen.items()):
        if len(prices) > 1:
            if len(set(prices)) > 1:
                # Two different prices for one month (a mid-month change) and no day to tell
                # them apart: ambiguous, so neither is used. Never resolved by row order.
                stats.ambiguous_duplicate_cells += 1
                continue
            stats.identical_duplicate_cells += 1
        out.append(FuelRow(effective, product, kind, prices[0], units[product]))
    return out


# ---------------- official source retrieval (no files are written) ----------------
def _is_erb_url(url: object) -> bool:
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and parts.scheme == "https" and parts.netloc == ERB_HOST)


async def fetch_archive(client: httpx.AsyncClient) -> tuple[bytes, str]:
    """Locate the archive through ERB's media API and return (xlsx bytes, archive URL)."""
    meta = await client.get(
        get_settings().erb_media_url,
        params={"search": "PUMP AND WHOLESALE PRICES", "mime_type": "application/zip", "per_page": "10",
                "orderby": "date", "order": "desc", "_fields": "date,source_url,mime_type"},
        timeout=ERB_TIMEOUT_SECONDS, headers={"User-Agent": ERB_USER_AGENT, "Accept": "application/json"},
    )
    meta.raise_for_status()
    items = meta.json()
    if not isinstance(items, list):
        raise FuelArchiveError("Unexpected media listing")
    candidates = [
        i for i in items if isinstance(i, dict) and _is_erb_url(i.get("source_url"))
        and re.fullmatch(r".*/PUMP-AND-WHOLESALE-PRICES[^/]*\.zip", i["source_url"], re.IGNORECASE)
    ]
    if not candidates:
        raise FuelArchiveError("Official archive not found")
    url = max(candidates, key=lambda i: str(i.get("date")))["source_url"]
    data = bytearray()
    async with client.stream("GET", url, timeout=ERB_TIMEOUT_SECONDS,
                             headers={"User-Agent": ERB_USER_AGENT}) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            data += chunk
            if len(data) > MAX_ZIP_BYTES:
                raise FuelArchiveError("Archive is larger than expected")
    return _extract_xlsx(bytes(data)), url


def _extract_xlsx(zip_bytes: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            files = [i for i in archive.infolist() if not i.is_dir()]
            if len(files) != 1 or not files[0].filename.lower().endswith(".xlsx") or ".." in files[0].filename:
                raise FuelArchiveError("Unexpected archive contents")
            if files[0].file_size > MAX_XLSX_BYTES:
                raise FuelArchiveError("Workbook is larger than expected")
            data = archive.read(files[0])
    except zipfile.BadZipFile as exc:
        raise FuelArchiveError("Archive could not be read") from exc
    try:  # guard against a decompression bomb inside the workbook itself
        with zipfile.ZipFile(io.BytesIO(data)) as workbook:
            if sum(i.file_size for i in workbook.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise FuelArchiveError("Workbook is larger than expected")
    except zipfile.BadZipFile as exc:
        raise FuelArchiveError("Workbook could not be read") from exc
    return data
