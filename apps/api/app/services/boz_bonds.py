"""BoZ Government Bond auction results from the official BoZ JSON:API (Drupal), no auth.

Verified 2026-11-12: the BoZ Government Bonds page reads `/jsonapi/node/bond_auction`
(one node per tender, title "07/2026/BA"; `field_bond_auction_date` is an ISO datetime with
an explicit +02:00 offset whose calendar date is the auction date: every 2026 value falls
on a Friday) with one included `paragraph--<N>_year(s)_bond` per tenor carrying ISIN,
coupon and yield. Field names differ per tenor and are matched strictly. Amounts, cut-off
and yield ranges exist only in the linked PDFs and are not modelled. Tenders before
12/2025 exist as empty placeholders and are rejected as "no published tenors".
"""

import re
from datetime import date, datetime, timezone

import httpx

from app.config import get_settings
from app.schemas.bonds import BOND_TENORS, GovernmentBondAuction
from app.schemas.tbills import RejectedTender
from app.services.boz_treasury import LUSAKA, _decimal

BOND_TIMEOUT_SECONDS = 10.0
BOND_USER_AGENT = "ZM-Terminal/0.1 (Bank of Zambia Government Bond adapter)"
RECENT_AUCTIONS = 5

_TITLE = re.compile(r"[0-9]{1,3}/[0-9]{4}(?:/BA)?")
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}")
_ISIN = re.compile(r"ZM[A-Z0-9]{9}[0-9]")
_PARAGRAPH = re.compile(r"paragraph--(2|3|5|7|10|15)_years?_bond")
_TENOR_LABEL = re.compile(r"([0-9]+)_years?")


def _auction_date(value: object) -> date:
    if not isinstance(value, str) or not _DATETIME.fullmatch(value):
        raise ValueError("Invalid auction date")
    return datetime.fromisoformat(value).astimezone(LUSAKA).date()


def _field(attributes: dict, years: int, name: str) -> object:
    # e.g. field_bond_isin, field_coupon_rate, field_3_year_coupon_rate, field_isin
    found = [v for k, v in attributes.items() if re.fullmatch(rf"field_(?:{years}_year_|bond_)?{name}", k)]
    if len(found) != 1:
        raise ValueError(f"Expected exactly one {name} field")
    return found[0]


def _parse_node(node: object, paragraphs: dict[str, dict], retrieved_at: datetime) -> list[GovernmentBondAuction]:
    if not isinstance(node, dict) or not isinstance(node.get("attributes"), dict):
        raise ValueError("Malformed tender node")
    attrs = node["attributes"]
    title = attrs.get("title")
    if not isinstance(title, str) or not _TITLE.fullmatch(title):
        raise ValueError("Invalid tender number")
    auction_date = _auction_date(attrs.get("field_bond_auction_date"))
    refs = (node.get("relationships") or {}).get("field_auction_results", {}).get("data")
    if not isinstance(refs, list):
        raise ValueError("Missing tenor references")
    rows: dict[int, GovernmentBondAuction] = {}
    for ref in refs:
        paragraph = paragraphs.get(ref.get("id")) if isinstance(ref, dict) else None
        if paragraph is None:
            raise ValueError("Referenced tenor record not included")
        match = _PARAGRAPH.fullmatch(str(paragraph.get("type")))
        if not match or not isinstance(paragraph.get("attributes"), dict):
            raise ValueError("Unrecognised tenor record")
        years = int(match.group(1))
        if years in rows:
            raise ValueError("Duplicate tenor within one tender")
        pattrs = paragraph["attributes"]
        if pattrs.get("status") is not True:
            raise ValueError("Unpublished tenor record")
        label = _field(pattrs, years, "tenor")
        label_match = _TENOR_LABEL.fullmatch(label) if isinstance(label, str) else None
        if not label_match or int(label_match.group(1)) != years:
            raise ValueError("Tenor label disagrees with record type")
        isin = _field(pattrs, years, "isin")
        if not isinstance(isin, str) or not _ISIN.fullmatch(isin):
            raise ValueError("Invalid ISIN")
        rows[years] = GovernmentBondAuction(
            tenor=f"{years}Y", tenor_years=years, auction_date=auction_date, tender_number=title,
            isin=isin, coupon_rate=_decimal(_field(pattrs, years, "coupon_rate")),
            yield_rate=_decimal(_field(pattrs, years, "yield_rate")), retrieved_at=retrieved_at,
        )
    if not rows:
        raise ValueError("Tender has no published tenors")
    return [rows[y] for y in sorted(rows)]


def _describe_rejected(node: object) -> RejectedTender:
    attrs = node.get("attributes") if isinstance(node, dict) else None
    attrs = attrs if isinstance(attrs, dict) else {}
    title = attrs.get("title")
    try:
        parsed = _auction_date(attrs.get("field_bond_auction_date"))
    except ValueError:
        parsed = None
    return RejectedTender(
        tender_number=title if isinstance(title, str) and _TITLE.fullmatch(title) else None,
        auction_date=parsed,
    )


def parse_bond_auctions(
    payload: object, *, retrieved_at: datetime
) -> tuple[list[GovernmentBondAuction], list[RejectedTender]]:
    """Return (valid rows, rejected tenders). A failing tender is rejected whole, never repaired.

    Structural problems with the document itself raise.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ValueError("Expected a non-empty JSON:API document")
    included = payload.get("included")
    if not isinstance(included, list):
        raise ValueError("Missing included tenor records")
    paragraphs = {i["id"]: i for i in included if isinstance(i, dict) and isinstance(i.get("id"), str)}
    rows: list[GovernmentBondAuction] = []
    rejected: list[RejectedTender] = []
    for node in payload["data"]:
        try:
            rows.extend(_parse_node(node, paragraphs, retrieved_at))
        except ValueError:  # includes pydantic ValidationError
            rejected.append(_describe_rejected(node))
    if not rows:
        raise ValueError("No valid Government Bond auction in the source")
    return rows, rejected


def latest_per_tenor(
    rows: list[GovernmentBondAuction], rejected: list[RejectedTender] | None = None
) -> list[GovernmentBondAuction]:
    """Newest auction per tenor among auctions that contain it.

    A newer valid auction that omits a tenor is not an error. Fallback rule (as for T-bills):
    `newer_rejected_tenders` lists rejected auctions with a readable date strictly after the
    row's auction date. No `stale` flag: BoZ publishes no machine-readable schedule and
    observed gaps (21 to 63 days in 2026) do not support a defensible threshold.
    """
    best: dict[str, GovernmentBondAuction] = {}
    for row in rows:
        if row.tenor not in best or row.auction_date > best[row.tenor].auction_date:
            best[row.tenor] = row
    return [
        best[t].model_copy(update={"newer_rejected_tenders": [
            r.tender_number or "unknown" for r in (rejected or [])
            if r.auction_date is not None and r.auction_date > best[t].auction_date
        ]})
        for t in BOND_TENORS if t in best
    ]


async def fetch_bond_payload(client: httpx.AsyncClient) -> object:
    response = await client.get(
        get_settings().boz_bond_auctions_url,
        params={
            "include": "field_auction_results",
            "sort": "-field_bond_auction_date",
            "page[limit]": str(RECENT_AUCTIONS),
        },
        timeout=BOND_TIMEOUT_SECONDS,
        headers={"User-Agent": BOND_USER_AGENT, "Accept": "application/vnd.api+json"},
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in (
        "application/vnd.api+json", "application/json",
    ):
        raise ValueError("Expected a JSON response")
    return response.json()


async def get_boz_bonds(
    *, client: httpx.AsyncClient | None = None
) -> tuple[list[GovernmentBondAuction], list[RejectedTender]]:
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await get_boz_bonds(client=owned)
    payload = await fetch_bond_payload(client)
    return parse_bond_auctions(payload, retrieved_at=datetime.now(timezone.utc))
