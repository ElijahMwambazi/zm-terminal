"""Strict Ministry of Agriculture market-bulletin ingestion (Layout B only).

The first supported source family is the April 2025-January 2026 style bulletin:
Table 3 contains one national ZMW/kg monthly series and Table 1 repeats the
last two months. Printed percentage-change columns are deliberately ignored.
Older ZMK/MT + ZMK/Kg layouts are recognised as unsupported, not repaired.
"""

import io
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import urlparse

import httpx

from app.config import get_settings

MOA_SOURCE = "Ministry of Agriculture, Department of Agribusiness and Marketing"
MOA_INDEX_URL = "https://www.agriculture.gov.zm/monthly-market-information-bullet/"
ALLOWED_HOSTS = {"www.agriculture.gov.zm", "agriculture.gov.zm"}
MAX_PDF_BYTES = 8 * 1024 * 1024
MONTHS = (
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
)
PRICE_POSITIONS = (0, 1, 3, 5, 7, 9, 11, 13, 15, 17)


class AgricultureSourceError(ValueError):
    pass


class UnsupportedLayoutError(AgricultureSourceError):
    pass


@dataclass(frozen=True)
class Bulletin:
    source_url: str
    publication_date: date
    title: str


@dataclass(frozen=True)
class AgricultureRow:
    commodity: str
    period_start: date
    period_end: date
    publication_date: date
    publication_date_source: str
    source_price: Decimal
    normalized_zmw_per_kg: Decimal
    source_period_label: str
    source_url: str
    retrieved_at: datetime
    market_type: str = "national_average"
    provider: str = "moa"
    grade: str | None = None
    variety: str | None = None
    location_scope: str = "national"
    location: str | None = None
    price_basis: str = "indicative_average"
    source_currency: str = "ZMW"
    source_unit: str = "kg"
    source: str = MOA_SOURCE


def _official_pdf(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_HOSTS
        and parsed.path.lower().startswith("/wp-content/uploads/")
        and parsed.path.lower().endswith(".pdf")
    )


def _publication_date(value: object) -> date:
    if not isinstance(value, str):
        raise AgricultureSourceError("Ministry media item has no publication date")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise AgricultureSourceError("Ministry media publication date is invalid") from exc


async def discover_bulletins(client: httpx.AsyncClient) -> list[Bulletin]:
    """Search the official WordPress media library and return unique official PDFs."""
    settings = get_settings()
    found: dict[str, Bulletin] = {}
    for term in ("market bulletin", "market"):
        response = await client.get(
            settings.moa_media_url,
            params={"search": term, "mime_type": "application/pdf", "per_page": 100},
            timeout=20.0,
            headers={"User-Agent": "ZM-Terminal/0.1 (Ministry of Agriculture market bulletin importer)"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise AgricultureSourceError("Ministry media API returned an unexpected shape")
        for item in payload:
            if not isinstance(item, dict):
                raise AgricultureSourceError("Ministry media API contains an invalid item")
            url = item.get("source_url")
            mime = item.get("mime_type")
            title_obj = item.get("title") if isinstance(item.get("title"), dict) else {}
            title = unescape(str(title_obj.get("rendered") or ""))
            if not isinstance(url, str) or mime != "application/pdf" or not _official_pdf(url):
                continue
            searchable = f"{title} {url}".lower()
            if not any(word in searchable for word in ("market", "bulletin", "april-2025-to-january-2026")):
                continue
            bulletin = Bulletin(url, _publication_date(item.get("date")), title)
            found[url] = bulletin
    return sorted(found.values(), key=lambda x: (x.publication_date, x.source_url))


async def fetch_pdf(client: httpx.AsyncClient, bulletin: Bulletin) -> bytes:
    if not _official_pdf(bulletin.source_url):
        raise AgricultureSourceError("Refusing non-Ministry PDF URL")
    response = await client.get(
        bulletin.source_url,
        timeout=20.0,
        headers={"User-Agent": "ZM-Terminal/0.1 (Ministry of Agriculture market bulletin importer)"},
    )
    response.raise_for_status()
    ctype = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/pdf":
        raise AgricultureSourceError("Ministry bulletin did not return application/pdf")
    data = response.content
    if not data or len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF"):
        raise AgricultureSourceError("Ministry bulletin PDF is empty, oversized or invalid")
    return data


def extract_pdf_text(data: bytes) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not 1 <= len(pdf.pages) <= 30:
                raise AgricultureSourceError("Unexpected Ministry bulletin page count")
            parts = [page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages]
    except AgricultureSourceError:
        raise
    except Exception as exc:
        raise AgricultureSourceError("Ministry bulletin PDF could not be read") from exc
    text = "\n".join(parts)
    if not text.strip():
        raise AgricultureSourceError("Ministry bulletin has no usable text layer")
    return text


def _month_number(name: str) -> int:
    try:
        return MONTHS.index(name.upper()) + 1
    except ValueError as exc:
        raise AgricultureSourceError("Unknown month in bulletin") from exc


def _period(text: str) -> tuple[date, date]:
    normalized = re.sub(r"\s+", " ", text)
    month_re = "|".join(m.title() for m in MONTHS)
    match = re.search(
        rf"Bulletin\s+No\s*:\s*[^\n]*?(\d{{1,2}})\D{{0,5}}({month_re})\s+(\d{{4}})\s*(?:to|[–—-])\s*"
        rf"(\d{{1,2}})\D{{0,5}}({month_re})\s+(\d{{4}})",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        raise AgricultureSourceError("Bulletin reporting period not identified")
    first = date(int(match.group(3)), _month_number(match.group(2)), int(match.group(1)))
    last = date(int(match.group(6)), _month_number(match.group(5)), int(match.group(4)))
    if first > last:
        raise AgricultureSourceError("Bulletin reporting period is inverted")
    return first, last


def _month_starts(first: date, last: date) -> list[date]:
    cur = date(first.year, first.month, 1)
    end = date(last.year, last.month, 1)
    out: list[date] = []
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)
    return out


def _numeric_tokens(value: str) -> list[Decimal]:
    tokens = re.findall(r"(?<![A-Za-z])-?\d+(?:,\d{3})*(?:\.\d+)?", value)
    try:
        return [Decimal(token.replace(",", "")) for token in tokens]
    except InvalidOperation as exc:
        raise AgricultureSourceError("Invalid numeric value in Ministry bulletin") from exc


def _between(text: str, start_pattern: str, end_pattern: str | None = None) -> str:
    start = re.search(start_pattern, text, re.IGNORECASE)
    if not start:
        raise AgricultureSourceError("Required Ministry bulletin table not found")
    tail = text[start.end():]
    if end_pattern:
        end = re.search(end_pattern, tail, re.IGNORECASE)
        if end:
            tail = tail[:end.start()]
    return tail


def _row_values(region: str, label: str, next_label: str, expected: int) -> list[Decimal]:
    match = re.search(
        rf"\b{label}\s*\(\s*Kg\s*\)\s+(.*?)(?=\s+{next_label}\s*\(\s*Kg\s*\))",
        region,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise AgricultureSourceError(f"{label} grain row not found")
    values = _numeric_tokens(match.group(1))
    if len(values) != expected:
        raise AgricultureSourceError(f"{label} grain row has an unexpected column count")
    return values


def _table1_values(region: str, label: str, next_label: str) -> tuple[Decimal, Decimal]:
    match = re.search(rf"\b{label}\s+(.*?)(?=\s+{next_label}\s)", region, re.IGNORECASE | re.DOTALL)
    if not match:
        raise AgricultureSourceError(f"Table 1 {label} row not found")
    values = _numeric_tokens(match.group(1))
    if len(values) != 3:
        raise AgricultureSourceError(f"Table 1 {label} row has an unexpected column count")
    return values[0], values[1]


def parse_layout_b_text(
    text: str, *, publication_date: date, source_url: str, retrieved_at: datetime,
) -> list[AgricultureRow]:
    upper = text.upper()
    if "TABLE 3" not in upper or "PERCENTAGE CHANGE IN COMMODITY PRICES" not in upper:
        if "ZMK/MT" in upper or "NATIONAL COMMODITY AVERAGE PRICES" in upper:
            raise UnsupportedLayoutError("Known older Ministry bulletin layout is not supported")
        raise UnsupportedLayoutError("Ministry bulletin layout is not supported")
    if "ALL PRICES ARE IN ZMW" not in upper:
        raise AgricultureSourceError("Layout B currency statement is missing")

    first, last = _period(text)
    months = _month_starts(first, last)
    if len(months) != 10:
        raise AgricultureSourceError("Layout B reporting period does not contain ten calendar months")

    table3 = _between(text, r"Table\s*3\s*:\s*Percentage\s+Change\s+in\s+Commodity\s+Prices")
    maize_pos = re.search(r"\bMaize\s*\(\s*Kg\s*\)", table3, re.IGNORECASE)
    if not maize_pos:
        raise AgricultureSourceError("Table 3 maize row not found")
    header = table3[:maize_pos.start()].upper()
    cursor = 0
    for month in months:
        name = MONTHS[month.month - 1]
        pos = header.find(name, cursor)
        if pos < 0:
            raise AgricultureSourceError("Table 3 month headings do not match the reporting period")
        cursor = pos + len(name)

    maize_all = _row_values(table3, "Maize", "Sorghum", 20)
    soya_all = _row_values(table3, "Soya\s+Beans", "Mixed\s+Beans", 20)
    maize_prices = [maize_all[i] for i in PRICE_POSITIONS]
    soya_prices = [soya_all[i] for i in PRICE_POSITIONS]
    if any(value <= 0 for value in maize_prices + soya_prices):
        raise AgricultureSourceError("Ministry grain price is not positive")

    table1 = _between(text, r"Table\s*1\s*:\s*National\s+Average\s+Commodity\s+Prices", r"Graph\s*1")
    if "ZMW/KG" not in table1.upper():
        raise AgricultureSourceError("Table 1 grain unit is not ZMW/KG")
    maize_dec, maize_jan = _table1_values(table1, "Maize", "Sorghum")
    soya_dec, soya_jan = _table1_values(table1, "Soya\s+Beans", "Mixed\s+Beans")
    if (maize_dec, maize_jan) != (maize_prices[-2], maize_prices[-1]):
        raise AgricultureSourceError("Table 1 and Table 3 maize prices disagree")
    if (soya_dec, soya_jan) != (soya_prices[-2], soya_prices[-1]):
        raise AgricultureSourceError("Table 1 and Table 3 soya prices disagree")

    rows: list[AgricultureRow] = []
    for commodity, prices in (("MAIZE", maize_prices), ("SOYA_BEANS", soya_prices)):
        for month, value in zip(months, prices, strict=True):
            rows.append(AgricultureRow(
                commodity=commodity,
                period_start=month,
                period_end=date(month.year, month.month, monthrange(month.year, month.month)[1]),
                publication_date=publication_date,
                publication_date_source="media_upload",
                source_price=value,
                normalized_zmw_per_kg=value,
                source_period_label=MONTHS[month.month - 1],
                source_url=source_url,
                retrieved_at=retrieved_at,
            ))
    return rows


def parse_pdf(data: bytes, *, publication_date: date, source_url: str, retrieved_at: datetime) -> list[AgricultureRow]:
    return parse_layout_b_text(
        extract_pdf_text(data), publication_date=publication_date, source_url=source_url, retrieved_at=retrieved_at,
    )
