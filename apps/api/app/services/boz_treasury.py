"""BoZ Treasury Bill auction results from the official BoZ JSON:API (Drupal), no auth.

Verified 2026-11-12: the BoZ Treasury Bills page is an Angular app reading
`/jsonapi/node/tbills_auction_results` (one node per tender, title "23/2026", date in
`field_tbills_auction_date`) with one included `paragraph--<N>_days_*` per tenor. Richer
fields (amounts, cut-off, ranges) exist only in the linked PDFs and are not modelled.
"""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.config import get_settings
from app.schemas.tbills import TENORS, RejectedTender, TreasuryBillAuction

TBILL_TIMEOUT_SECONDS = 10.0
TBILL_USER_AGENT = "ZM-Terminal/0.1 (Bank of Zambia Treasury Bill adapter)"
RECENT_TENDERS = 5
# Heuristic, not a BoZ calendar: tenders are roughly fortnightly.
TBILL_STALE_AFTER = timedelta(days=21)
LUSAKA = timezone(timedelta(hours=2))  # Africa/Lusaka is UTC+2 year-round

_TITLE = re.compile(r"[0-9]{1,3}/[0-9]{4}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_RATE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_ISIN = re.compile(r"ZM[A-Z0-9]{9}[0-9]")
_PARAGRAPH = re.compile(r"paragraph--(91|182|273|364)_(?:days_)?tbills?")
# BoZ field names vary per tenor; accept only the verified spellings.
_YIELD_FIELDS = {"yield_rate"}
_DISCOUNT_FIELDS = {"discount_rate", "discounted_rate", "discount"}


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or not _RATE.fullmatch(value):
        raise ValueError("Invalid published decimal rate")
    rate = Decimal(value)
    if not rate.is_finite() or not (0 < rate < 100):
        raise ValueError("Rate out of range")
    return rate


def _pick(attributes: dict, tenor_days: int, suffixes: set[str], label: str) -> str:
    # e.g. field_91_days_yield_rate, field_273_yield_rate, field_91_discounted_rate
    found = [
        value for key, value in attributes.items()
        if (m := re.fullmatch(rf"field_{tenor_days}_(?:days_)?(?:tbills?_)?(.+)", key)) and m.group(1) in suffixes
    ]
    if len(found) != 1:
        raise ValueError(f"Expected exactly one {label} field")
    return found[0]


def _isin(attributes: dict, tenor_days: int) -> str:
    found = [v for k, v in attributes.items() if re.fullmatch(rf"field_{tenor_days}_(?:days_)?(?:tbills?_)?isin", k)]
    if len(found) != 1 or not isinstance(found[0], str) or not _ISIN.fullmatch(found[0]):
        raise ValueError("Expected exactly one valid ISIN")
    return found[0]


def _parse_node(node: object, paragraphs: dict[str, dict], retrieved_at: datetime) -> list[TreasuryBillAuction]:
    if not isinstance(node, dict) or not isinstance(node.get("attributes"), dict):
        raise ValueError("Malformed tender node")
    attrs = node["attributes"]
    title, date_text = attrs.get("title"), attrs.get("field_tbills_auction_date")
    if not isinstance(title, str) or not _TITLE.fullmatch(title):
        raise ValueError("Invalid tender number")
    if not isinstance(date_text, str) or not _DATE.fullmatch(date_text):
        raise ValueError("Invalid auction date")
    auction_date = date.fromisoformat(date_text)
    refs = (node.get("relationships") or {}).get("field_tbills_auction_results", {}).get("data")
    if not isinstance(refs, list):
        raise ValueError("Missing tenor references")
    rows: dict[int, TreasuryBillAuction] = {}
    for ref in refs:
        paragraph = paragraphs.get(ref.get("id")) if isinstance(ref, dict) else None
        if paragraph is None:
            raise ValueError("Referenced tenor record not included")
        match = _PARAGRAPH.fullmatch(str(paragraph.get("type")))
        if not match or not isinstance(paragraph.get("attributes"), dict):
            raise ValueError("Unrecognised tenor record")
        days = int(match.group(1))
        if days in rows:
            raise ValueError("Duplicate tenor within one tender")
        pattrs = paragraph["attributes"]
        if pattrs.get("status") is not True:
            raise ValueError("Unpublished tenor record")
        rows[days] = TreasuryBillAuction(
            tenor=f"{days}D", tenor_days=days, auction_date=auction_date, tender_number=title,
            isin=_isin(pattrs, days),
            yield_rate=_decimal(_pick(pattrs, days, _YIELD_FIELDS, "yield")),
            discount_rate=_decimal(_pick(pattrs, days, _DISCOUNT_FIELDS, "discount")),
            retrieved_at=retrieved_at,
        )
    if not rows:
        raise ValueError("Tender has no published tenors")
    return [rows[d] for d in sorted(rows)]


def _describe_rejected(node: object) -> RejectedTender:
    attrs = node.get("attributes") if isinstance(node, dict) else None
    attrs = attrs if isinstance(attrs, dict) else {}
    title, day = attrs.get("title"), attrs.get("field_tbills_auction_date")
    parsed = None
    if isinstance(day, str) and _DATE.fullmatch(day):
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            pass
    return RejectedTender(
        tender_number=title if isinstance(title, str) and _TITLE.fullmatch(title) else None,
        auction_date=parsed,
    )


def parse_tbill_auctions(
    payload: object, *, retrieved_at: datetime
) -> tuple[list[TreasuryBillAuction], list[RejectedTender]]:
    """Return (valid auction rows newest tender first, tenders rejected).

    Structural problems with the document itself raise. A tender that fails validation is
    rejected whole (never partially used, never repaired) and reported by number.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ValueError("Expected a non-empty JSON:API document")
    included = payload.get("included")
    if not isinstance(included, list):
        raise ValueError("Missing included tenor records")
    paragraphs = {i["id"]: i for i in included if isinstance(i, dict) and isinstance(i.get("id"), str)}
    rows: list[TreasuryBillAuction] = []
    skipped: list[RejectedTender] = []
    for node in payload["data"]:
        try:
            rows.extend(_parse_node(node, paragraphs, retrieved_at))
        except ValueError:  # includes pydantic ValidationError
            skipped.append(_describe_rejected(node))
    if not rows:
        raise ValueError("No valid Treasury Bill tender in the source")
    return rows, skipped


def latest_per_tenor(
    rows: list[TreasuryBillAuction], now: datetime, rejected: list[RejectedTender] | None = None
) -> list[TreasuryBillAuction]:
    """Newest auction date per tenor, with a stale flag against Africa/Lusaka today.

    Fallback rule: a row lists in `newer_rejected_tenders` every rejected tender with a
    readable date strictly after the row's auction date (it might have carried that tenor).
    Rejected tenders that are older, same-dated or undated never mark a row as a fallback;
    a valid newer tender that merely omits a tenor is not an error either.
    """
    today = now.astimezone(LUSAKA).date()
    best: dict[str, TreasuryBillAuction] = {}
    for row in rows:
        if row.tenor not in best or row.auction_date > best[row.tenor].auction_date:
            best[row.tenor] = row
    return [
        best[t].model_copy(update={
            "stale": today - best[t].auction_date > TBILL_STALE_AFTER,
            "newer_rejected_tenders": [
                r.tender_number or "unknown" for r in (rejected or [])
                if r.auction_date is not None and r.auction_date > best[t].auction_date
            ],
        })
        for t in TENORS if t in best
    ]


async def fetch_tbill_payload(client: httpx.AsyncClient) -> object:
    response = await client.get(
        get_settings().boz_treasury_bills_url,
        params={
            "include": "field_tbills_auction_results",
            "sort": "-field_tbills_auction_date",
            "page[limit]": str(RECENT_TENDERS),
        },
        timeout=TBILL_TIMEOUT_SECONDS,
        headers={"User-Agent": TBILL_USER_AGENT, "Accept": "application/vnd.api+json"},
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in (
        "application/vnd.api+json", "application/json",
    ):
        raise ValueError("Expected a JSON response")
    return response.json()


async def get_boz_tbills(
    *, client: httpx.AsyncClient | None = None
) -> tuple[list[TreasuryBillAuction], list[RejectedTender]]:
    """All valid rows from the most recent tenders (newest first) plus the rejected tenders."""
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await get_boz_tbills(client=owned)
    payload = await fetch_tbill_payload(client)
    return parse_tbill_auctions(payload, retrieved_at=datetime.now(timezone.utc))
