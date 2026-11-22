"""Strict BoZ commercial bank daily rates parser (pure) and the exact-date network seam for the future importer.

Source: the Drupal JSON:API node type `commercial_bank_daily_rates` on www.boz.zm, the endpoint the BoZ page itself calls. Contract:
docs/research/boz-commercial-bank-fx.md (two table layouts v1/v2, 1-4 decimal strings, blank slots, one market-average row, no
`buy <= sell` rule). The parser performs no I/O and fails closed (BankFxError) on anything it does not recognise. Rights remain
review_required: local/internal use only.
"""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from collections.abc import AsyncIterable, Callable
from uuid import UUID

import httpx

from app.config import get_settings
from app.services.bank_fx_models import (
    LAYOUT_V1, LAYOUT_V2, MARKET_AVERAGE_NAME, RETAIL_SOURCE_LABEL, RIGHTS_STATUS, SLOTS, BankFxError, BankFxRate, BankFxSnapshot,
)

NODE_TYPE = "node--commercial_bank_daily_rates"
BANK_FX_PAGE_URL = "https://www.boz.zm/markets-securities/commercial-banks-rates"
BANK_FX_HOST = "www.boz.zm"
BANK_FX_USER_AGENT = "ZM-Terminal/0.1 (Bank of Zambia commercial bank FX adapter)"
BANK_FX_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1_048_576  # about 7 KB per node observed; this is a safety cap, not an expectation
PAGE_LIMIT = 10  # comfortably above the 3 nodes observed for one date; there is no pagination (links.next fails the import)
JSONAPI_MEDIA_TYPE = "application/vnd.api+json"
READ_CHUNK_BYTES = 65_536
TABLE_DIGEST_VERSION = 1

_RATE = re.compile(r"[0-9]+\.[0-9]{1,4}")  # matched with fullmatch: no whitespace, sign, comma, exponent or fifth decimal
_ROW_KEY = re.compile(r"0|[1-9][0-9]*")
_CELLS = tuple(str(i) for i in range(13))
_SLOT_LABELS = {1: "9:30", 5: "12:30", 9: "15:30"}  # first cell of each slot quartet -> the source label
_SLOT_BY_LABEL = dict(zip(_SLOT_LABELS.values(), SLOTS))
_HEADER_ROWS = 3


def _norm(value: str) -> str:
    """The BoZ page's own name normalisation (JavaScript's whitespace class also matches the BOM): collapse runs of whitespace, trim the ends."""
    return re.sub(r"[\s\ufeff]+", " ", value).strip()


def _mapping(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise BankFxError(f"{what} must be an object")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise BankFxError(f"{what} must be a non-empty string")
    return value


def _positive_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BankFxError(f"{what} must be a positive integer")
    return value


def _aware(value: object, what: str) -> datetime:
    """An ISO datetime string that carries an explicit UTC offset; the offset is the source's own."""
    text = _text(value, what)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise BankFxError(f"{what} is not an ISO datetime") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BankFxError(f"{what} must carry an explicit timezone offset")
    return parsed


def parse_rate(value: object, what: str = "rate") -> Decimal:
    """A published rate: a JSON string, ^[0-9]+\\.[0-9]{1,4}$, strictly positive. Never through float."""
    if not isinstance(value, str) or _RATE.fullmatch(value) is None:
        raise BankFxError(f"{what} is not a plain 1-4 decimal rate string")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise BankFxError(f"{what} must be greater than zero")
    return number


def _table_digest(value: dict, row_keys: list[int]) -> str:
    """SHA-256 over a canonical serialisation of the validated table: numeric row order, every cell string (empty ones included), each row's
    `weight` when present, and the `caption`. JSON with sorted keys and fixed separators, so it never depends on object key order or repr()."""
    rows = []
    for index in row_keys:
        row = value[str(index)]
        entry: dict = {"i": index, "c": [row[cell] for cell in _CELLS]}
        if "weight" in row:
            entry["w"] = row["weight"]
        rows.append(entry)
    canonical = {"v": TABLE_DIGEST_VERSION, "rows": rows}
    if "caption" in value:
        canonical["caption"] = value["caption"]
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _table_rows(field: object) -> tuple[list[tuple[int, list[str]]], str]:
    """The CMS table as (row key, 13 cell strings) in numeric key order plus the source-table digest. Only the keys 0..N-1 are rows
    (contiguous, canonical); `caption` is the only other key allowed. A row's `weight` is never identity or ordering, but it is part of
    the digest (source content)."""
    value = _mapping(_mapping(field, "field_commercial_bank_rates").get("value"), "field_commercial_bank_rates.value")
    row_keys = []
    for key in value:
        if _ROW_KEY.fullmatch(key):
            row_keys.append(int(key))
        elif key != "caption":
            raise BankFxError("unexpected key in the rates table")
    row_keys.sort()
    if not row_keys or row_keys != list(range(len(row_keys))):
        raise BankFxError("table row keys are not the contiguous numbers 0..N-1")
    rows = []
    for index in row_keys:
        row = _mapping(value[str(index)], f"table row {index}")
        extra = set(row) - set(_CELLS) - {"weight"}
        if extra or any(cell not in row for cell in _CELLS):
            raise BankFxError(f"table row {index} does not have exactly the cells 0..12")
        if any(not isinstance(row[cell], str) for cell in _CELLS):
            raise BankFxError(f"table row {index} has a non-string cell")
        rows.append((index, [row[cell] for cell in _CELLS]))
    if len(rows) <= _HEADER_ROWS:
        raise BankFxError("the table has no institution rows")
    return rows, _table_digest(value, row_keys)


def _detect_layout(rows: list[tuple[int, list[str]]]) -> str:
    """Exactly the two researched header families; anything else (including a changed label) fails closed."""
    r0, r1, r2 = (tuple(rows[i][1]) for i in range(3))  # exact source strings: no trimming, collapsing or case folding
    blank = lambda row, cells: all(row[c] == "" for c in cells)
    slot_cells = set(_SLOT_LABELS)
    slots_ok = all(r0[c] == label for c, label in _SLOT_LABELS.items()) and blank(r0, set(range(1, 13)) - slot_cells)
    sides_ok = all(r2[c] == ("Buy" if c % 2 == 1 else "Sell") for c in range(1, 13)) and r2[0] == ""

    def groups(interbank: str, retail: str) -> bool:
        return (all(r1[c] == interbank for c in (1, 5, 9)) and all(r1[c] == retail for c in (3, 7, 11))
                and blank(r1, (2, 4, 6, 8, 10, 12)))

    if slots_ok and sides_ok and r0[0] == "Bank Name" and r1[0] == "" and groups("INTERBANK", "BOARD RATES"):
        return LAYOUT_V2
    # v1: the first cell of row 0 holds the source's date text. This is the ONLY header cell read tolerantly (BOM/no-break space are
    # whitespace to the page); it must still contain text.
    if slots_ok and sides_ok and _norm(r0[0]) and r1[0] == "Bank Name" and groups("Interbank Rates", "Retail Rates"):
        return LAYOUT_V1
    raise BankFxError("unknown or malformed table layout")


def _parse_node(item: object, *, retrieved_at: datetime, source_url: str) -> BankFxSnapshot:
    node = _mapping(item, "data item")
    if node.get("type") != NODE_TYPE:
        raise BankFxError("unexpected JSON:API resource type")
    try:
        record_id = UUID(_text(node.get("id"), "id"))
    except ValueError:
        raise BankFxError("id is not a UUID") from None
    attributes = _mapping(node.get("attributes"), "attributes")
    nid = _positive_int(attributes.get("drupal_internal__nid"), "drupal_internal__nid")
    vid = _positive_int(attributes.get("drupal_internal__vid"), "drupal_internal__vid")
    created = _aware(attributes.get("created"), "created")
    changed = _aware(attributes.get("changed"), "changed")
    revised = _aware(attributes.get("revision_timestamp"), "revision_timestamp")
    title = _text(attributes.get("title"), "title")
    raw_datetime = attributes.get("commercial_bank_rates_date")
    rates_datetime = _aware(raw_datetime, "commercial_bank_rates_date")
    observation = rates_datetime.date()  # taken at the supplied offset, before any UTC conversion

    rows, table_sha256 = _table_rows(attributes.get("field_commercial_bank_rates"))
    layout = _detect_layout(rows)
    label = rows[1][1][3]  # the exact retail group label as published; _detect_layout proved it equals the researched string for the layout
    if label != RETAIL_SOURCE_LABEL[layout]:
        raise BankFxError("retail group label does not match the layout")
    rates: list[BankFxRate] = []
    seen: set[str] = set()
    averages = 0
    for index, cells in rows[_HEADER_ROWS:]:
        raw_name = cells[0]
        name = _norm(raw_name)
        if not name:
            raise BankFxError(f"table row {index} has a blank institution name")
        if name in seen:
            raise BankFxError("duplicate institution name in one node")
        seen.add(name)
        is_average = name.lower() == MARKET_AVERAGE_NAME
        averages += is_average
        for first, slot_label in _SLOT_LABELS.items():
            quartet = cells[first:first + 4]
            if all(cell == "" for cell in quartet):
                continue  # unpublished slot for this institution: no row, never zero or null
            values = [parse_rate(cell, f"row {index} {slot_label}") for cell in quartet]
            rates.append(BankFxRate(
                source_row_index=index, slot=_SLOT_BY_LABEL[slot_label], institution_name_raw=raw_name, institution_name=name,
                is_market_average=is_average,
                interbank_buy=values[0], interbank_buy_text=quartet[0], interbank_sell=values[1], interbank_sell_text=quartet[1],
                retail_buy=values[2], retail_buy_text=quartet[2], retail_sell=values[3], retail_sell_text=quartet[3],
                retail_source_label=label,
            ))
    if averages != 1:
        raise BankFxError("expected exactly one market average row")
    if not rates:
        raise BankFxError("the node publishes no slot")
    return BankFxSnapshot(
        observation_date=observation, source_record_id=record_id, source_revision_id=vid, source_internal_nid=nid,
        source_created=created, source_changed=changed, source_revision_timestamp=revised, source_title=title,
        source_rates_datetime_raw=raw_datetime, source_rates_datetime=rates_datetime, table_layout=layout,
        source_table_sha256=table_sha256,
        retrieved_at=retrieved_at, source_url=source_url, rights_status=RIGHTS_STATUS, rates=tuple(rates),
    )


def parse_bank_fx_document(
    document: object, *, retrieved_at: datetime, source_url: str = BANK_FX_PAGE_URL, requested_date: date | None = None,
    allow_empty: bool = False,
) -> list[BankFxSnapshot]:
    """Every node of one complete JSON:API response as snapshots, in response order. Pure.

    `links.next` present means the response is truncated: rejected. An empty `data` is `no publication` and is returned as [] only when
    the caller allows it (an exact-date request); no other date is ever substituted. With `requested_date` every node's observation date
    (at its own offset) must equal it. A repeated (node uuid, revision id) inside one response is rejected.
    """
    if retrieved_at.tzinfo is None:
        raise BankFxError("retrieved_at must be timezone-aware")
    body = _mapping(document, "document")
    links = body.get("links")
    if links is not None and _mapping(links, "links").get("next"):
        raise BankFxError("the response is paginated (links.next); refusing to import a truncated result")
    data = body.get("data")
    if not isinstance(data, list):
        raise BankFxError("data must be a list")
    if not data:
        if allow_empty:
            return []
        raise BankFxError("the response contains no publication")
    snapshots = [_parse_node(item, retrieved_at=retrieved_at, source_url=source_url) for item in data]
    identities = [(s.source_record_id, s.source_revision_id) for s in snapshots]
    if len(set(identities)) != len(identities):
        raise BankFxError("duplicate node revision in one response")
    if requested_date is not None and any(s.observation_date != requested_date for s in snapshots):
        raise BankFxError("a returned node is for a different observation date than requested")
    return snapshots


# ----------------------------------------------------------------------------------------------- network seam (not called yet)
def _check_url(url: str) -> None:
    parts = httpx.URL(url)
    if parts.scheme != "https" or parts.host != BANK_FX_HOST or parts.userinfo or parts.port not in (None, 443):
        raise BankFxError("Unexpected BoZ commercial bank rates URL")


def exact_date_url(day: date, *, base: str | None = None, limit: int = PAGE_LIMIT) -> str:
    """The researched page query for one calendar date (brackets and colons literal, exactly as the BoZ page sends them)."""
    base = base or get_settings().boz_commercial_bank_rates_url
    _check_url(base)
    prefix = "filter[commercial_bank_rates_date]"
    return (f"{base}?{prefix}[operator]=BETWEEN&{prefix}[value][0]={day.isoformat()}T00:00:00"
            f"&{prefix}[value][1]={day.isoformat()}T23:59:59&sort=-created&page[limit]={limit}")


async def read_capped(chunks: AsyncIterable[bytes], limit: int = MAX_RESPONSE_BYTES, *, observe: Callable[[int], None] | None = None) -> bytes:
    """Accumulate raw chunks without ever holding more than `limit` bytes: each chunk is checked against the remaining capacity BEFORE
    it is appended. `observe` (tests only) receives the accumulator size after every append."""
    buffer = bytearray()
    async for chunk in chunks:
        if len(chunk) > limit - len(buffer):
            raise BankFxError("Bank of Zambia response is too large")
        buffer.extend(chunk)
        if observe is not None:
            observe(len(buffer))
    return bytes(buffer)


def _require_identity_encoding(response: httpx.Response) -> None:
    """Phase 1 does not decompress: any Content-Encoding other than identity is rejected before a body byte is read."""
    for header in response.headers.get_list("content-encoding"):
        if any(token.strip().lower() != "identity" for token in header.split(",")):
            raise BankFxError("Unsupported Content-Encoding from the Bank of Zambia (identity only)")


async def fetch_bank_fx_payload(client: httpx.AsyncClient, day: date) -> object:
    """Exactly one GET for one exact date: identity transfer only, no redirects, no retries, no pagination, JSON:API content type, and a
    hard cap on the raw (never decoded) bytes held."""
    url = exact_date_url(day)
    headers = {"Accept": JSONAPI_MEDIA_TYPE, "Accept-Encoding": "identity"}
    try:
        async with client.stream("GET", url, follow_redirects=False, headers=headers) as response:
            if response.is_redirect:
                raise BankFxError("Unexpected redirect from the Bank of Zambia")
            response.raise_for_status()
            _require_identity_encoding(response)
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != JSONAPI_MEDIA_TYPE:
                raise BankFxError("Expected a JSON:API response from the Bank of Zambia")
            body = await read_capped(response.aiter_raw(READ_CHUNK_BYTES))
    except httpx.HTTPError:
        raise BankFxError("Bank of Zambia request failed") from None
    try:
        return json.loads(body)
    except ValueError:
        raise BankFxError("Bank of Zambia response is not valid JSON") from None


async def fetch_bank_fx_snapshots(client: httpx.AsyncClient, day: date, *, retrieved_at: datetime) -> list[BankFxSnapshot]:
    """One request, parsed for the requested date; an empty result is `no publication` ([]), never another date."""
    payload = await fetch_bank_fx_payload(client, day)
    return parse_bank_fx_document(payload, retrieved_at=retrieved_at, requested_date=day, allow_empty=True)


def new_client() -> httpx.AsyncClient:
    """Never follows redirects; one source, one request."""
    return httpx.AsyncClient(headers={"User-Agent": BANK_FX_USER_AGENT, "Accept-Encoding": "identity"}, timeout=BANK_FX_TIMEOUT_SECONDS, follow_redirects=False)
