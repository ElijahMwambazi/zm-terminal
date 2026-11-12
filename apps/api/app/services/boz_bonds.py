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
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.schemas.bonds import BOND_TENORS, GovernmentBondAuction
from app.schemas.tbills import RejectedTender
from app.services.boz_treasury import LUSAKA, _decimal

BOND_TIMEOUT_SECONDS = 10.0
BOND_USER_AGENT = "ZM-Terminal/0.1 (Bank of Zambia Government Bond adapter)"
# Pagination: newest-first pages of BOND_PAGE_SIZE auctions, followed via the API's own
# `links.next`, sequentially, until all six tenors have a valid row or the archive ends.
# Hitting MAX_BOND_PAGES first fails closed (never reported as "missing").
BOND_PAGE_SIZE = 50  # the JSON:API maximum; the whole archive is ~110 tenders, almost all empty placeholders
MAX_BOND_PAGES = 10

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
    refs = (node.get("relationships") or {}).get("field_auction_results", {}).get("data") if isinstance(node, dict) else None
    return RejectedTender(
        tender_number=title if isinstance(title, str) and _TITLE.fullmatch(title) else None,
        auction_date=parsed,
        no_results=refs == [],
    )


def parse_bond_auctions(
    payload: object, *, retrieved_at: datetime, require_rows: bool = True
) -> tuple[list[GovernmentBondAuction], list[RejectedTender]]:
    """Return (valid rows, rejected tenders). A failing tender is rejected whole, never repaired.

    Structural problems with the document itself raise. A page whose tenders are all
    rejected (e.g. empty legacy placeholders) is only an error when `require_rows`.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ValueError("Expected a non-empty JSON:API document")
    included = payload.get("included", [])  # JSON:API omits it when nothing is included
    if not isinstance(included, list):
        raise ValueError("Malformed included tenor records")
    paragraphs = {i["id"]: i for i in included if isinstance(i, dict) and isinstance(i.get("id"), str)}
    rows: list[GovernmentBondAuction] = []
    rejected: list[RejectedTender] = []
    for node in payload["data"]:
        try:
            rows.extend(_parse_node(node, paragraphs, retrieved_at))
        except ValueError:  # includes pydantic ValidationError
            rejected.append(_describe_rejected(node))
    if require_rows and not rows:
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


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, str] | None) -> object:
    response = await client.get(
        url,
        params=params,
        timeout=BOND_TIMEOUT_SECONDS,
        headers={"User-Agent": BOND_USER_AGENT, "Accept": "application/vnd.api+json"},
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in (
        "application/vnd.api+json", "application/json",
    ):
        raise ValueError("Expected a JSON response")
    return response.json()


def _next_url(payload: object, base_url: str) -> str | None:
    """The page's own `links.next`, accepted only if it targets the configured BoZ endpoint."""
    links = payload.get("links") if isinstance(payload, dict) else None
    nxt = links.get("next") if isinstance(links, dict) else None
    if nxt is None:
        return None
    href = nxt.get("href") if isinstance(nxt, dict) else None
    if not isinstance(href, str):
        raise ValueError("Malformed next link")
    got, want = urlsplit(href), urlsplit(base_url)
    if (got.scheme, got.netloc, got.path) != (want.scheme, want.netloc, want.path):
        raise ValueError("Next link leaves the BoZ endpoint")
    return href


def _merge(
    rows: dict[tuple, GovernmentBondAuction], new: list[GovernmentBondAuction]
) -> None:
    """Add rows keyed by auction identity. Identical duplicates collapse; conflicting ones fail."""
    for row in new:
        key = (row.auction_date, row.tender_number, row.tenor_years)
        old = rows.get(key)
        if old is None:
            rows[key] = row
        elif (old.isin, old.coupon_rate, old.yield_rate) != (row.isin, row.coupon_rate, row.yield_rate):
            raise ValueError("Conflicting duplicate auction across pages")


async def get_boz_bonds(
    *, client: httpx.AsyncClient | None = None
) -> tuple[list[GovernmentBondAuction], list[RejectedTender]]:
    """Newest-first traversal until every tenor has a valid row or the archive ends.

    Returns (valid rows, rejected tenders) from every page fetched. Raises if the page cap
    is hit while tenors are still unfound and more pages exist, so `missing` always means
    "not in the whole archive".
    """
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await get_boz_bonds(client=owned)
    base_url = get_settings().boz_bond_auctions_url
    retrieved_at = datetime.now(timezone.utc)
    rows: dict[tuple, GovernmentBondAuction] = {}
    rejected: dict[tuple, RejectedTender] = {}
    url: str | None = base_url
    params: dict[str, str] | None = {
        "include": "field_auction_results",
        "sort": "-field_bond_auction_date",
        "page[limit]": str(BOND_PAGE_SIZE),
    }
    for page in range(1, MAX_BOND_PAGES + 1):
        payload = await _get(client, url, params)
        page_rows, page_rejected = parse_bond_auctions(payload, retrieved_at=retrieved_at, require_rows=False)
        _merge(rows, page_rows)
        for r in page_rejected:
            rejected[(r.tender_number, r.auction_date)] = r
        if {r.tenor for r in rows.values()} >= set(BOND_TENORS):
            break
        url, params = _next_url(payload, base_url), None
        if url is None:
            break
        if page == MAX_BOND_PAGES:
            raise ValueError("Archive search truncated by the page cap")
    if not rows:
        raise ValueError("No valid Government Bond auction in the source")
    ordered = sorted(rows.values(), key=lambda r: (r.auction_date, r.tenor_years), reverse=True)
    newest = ordered[0].auction_date
    # Empty placeholders no newer than the newest valid auction carry no data and cannot make
    # any row a fallback; drop them so the rejected list is not flooded by legacy placeholders.
    kept = [r for r in rejected.values() if not (r.no_results and r.auction_date is not None and r.auction_date <= newest)]
    return ordered, kept
