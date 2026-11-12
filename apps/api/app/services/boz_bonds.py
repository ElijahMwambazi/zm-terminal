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
from app.schemas.bonds import BOND_TENORS, GovernmentBondAuction, RejectedBondObservation
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
_PARAGRAPH = re.compile(r"paragraph--([0-9]+)_years?_bond")
SUPPORTED_YEARS = {2, 3, 5, 7, 10, 15}
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


def _parse_record(paragraph: dict, years: int, auction_date: date, title: str, retrieved_at: datetime) -> GovernmentBondAuction:
    """Tenor-level validation: any ValueError rejects only this record. Values are never repaired."""
    attrs = paragraph.get("attributes")
    if not isinstance(attrs, dict) or attrs.get("status") is not True:
        raise ValueError("Unpublished or malformed tenor record")
    label = _field(attrs, years, "tenor")
    label_match = _TENOR_LABEL.fullmatch(label) if isinstance(label, str) else None
    if not label_match or int(label_match.group(1)) != years:
        raise ValueError("Tenor label disagrees with record type")
    isin = _field(attrs, years, "isin")
    if not isinstance(isin, str) or not _ISIN.fullmatch(isin):
        raise ValueError("Invalid ISIN")
    return GovernmentBondAuction(
        tenor=f"{years}Y", tenor_years=years, auction_date=auction_date, tender_number=title,
        isin=isin, coupon_rate=_decimal(_field(attrs, years, "coupon_rate")),
        yield_rate=_decimal(_field(attrs, years, "yield_rate")), retrieved_at=retrieved_at,
    )


def _parse_node(
    node: object, paragraphs: dict[str, dict], retrieved_at: datetime
) -> tuple[list[GovernmentBondAuction], list[RejectedBondObservation]]:
    """Returns (accepted rows, tenor-level rejections). A tender with no result records yields
    ([], []): that is "no results", not malformed data.

    Raises ValueError for tender-level failures (the tender cannot be trusted): bad tender
    number or date, malformed relationships, duplicate references, a referenced record that is
    missing, or two records for the same tenor.
    """
    if not isinstance(node, dict) or not isinstance(node.get("attributes"), dict):
        raise ValueError("Malformed tender node")
    attrs = node["attributes"]
    title = attrs.get("title")
    if not isinstance(title, str) or not _TITLE.fullmatch(title):
        raise ValueError("Invalid tender number")
    auction_date = _auction_date(attrs.get("field_bond_auction_date"))
    relationships = node.get("relationships")
    results = relationships.get("field_auction_results") if isinstance(relationships, dict) else None
    refs = results.get("data") if isinstance(results, dict) else None
    if not isinstance(refs, list):
        raise ValueError("Missing tenor references")
    ids = [ref.get("id") if isinstance(ref, dict) else None for ref in refs]
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Malformed or duplicate tenor references")
    records: list[tuple[int | None, dict]] = []
    seen: set[int] = set()
    for rid in ids:
        paragraph = paragraphs.get(rid)
        if not isinstance(paragraph, dict):
            raise ValueError("Referenced tenor record not included")
        match = _PARAGRAPH.fullmatch(str(paragraph.get("type")))
        years = int(match.group(1)) if match else None
        if years is not None and years not in SUPPORTED_YEARS:
            continue  # a valid bond tenor this adapter does not model: not malformed
        if years is not None:
            if years in seen:
                raise ValueError("Duplicate tenor within one tender")
            seen.add(years)
        records.append((years, paragraph))
    rows: dict[int, GovernmentBondAuction] = {}
    rejected: list[RejectedBondObservation] = []
    for years, paragraph in records:
        try:
            if years is None:
                raise ValueError("Unrecognised tenor record")
            rows[years] = _parse_record(paragraph, years, auction_date, title, retrieved_at)
        except ValueError:  # includes pydantic ValidationError; reason text is deliberately dropped
            rejected.append(RejectedBondObservation(
                tender_number=title, auction_date=auction_date, scope="tenor",
                affected_tenors=None if years is None else (f"{years}Y",),
            ))
    return [rows[y] for y in sorted(rows)], rejected


def _tender_rejection(node: object) -> RejectedBondObservation:
    attrs = node.get("attributes") if isinstance(node, dict) else None
    attrs = attrs if isinstance(attrs, dict) else {}
    title = attrs.get("title")
    try:
        parsed = _auction_date(attrs.get("field_bond_auction_date"))
    except ValueError:
        parsed = None
    return RejectedBondObservation(
        tender_number=title if isinstance(title, str) and _TITLE.fullmatch(title) else None,
        auction_date=parsed, affected_tenors=None, scope="tender",
    )


def parse_bond_auctions(
    payload: object, *, retrieved_at: datetime, require_rows: bool = True
) -> tuple[list[GovernmentBondAuction], list[RejectedBondObservation]]:
    """Return (accepted rows, rejected observations). Nothing is repaired.

    Structural problems with the document itself raise. A page without any accepted row is
    only an error when `require_rows`.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ValueError("Expected a non-empty JSON:API document")
    included = payload.get("included", [])  # JSON:API omits it when nothing is included
    if not isinstance(included, list):
        raise ValueError("Malformed included tenor records")
    paragraphs = {i["id"]: i for i in included if isinstance(i, dict) and isinstance(i.get("id"), str)}
    rows: list[GovernmentBondAuction] = []
    rejected: list[RejectedBondObservation] = []
    for node in payload["data"]:
        try:
            node_rows, node_rejected = _parse_node(node, paragraphs, retrieved_at)
        except ValueError:  # tender-level: the whole tender is rejected
            rejected.append(_tender_rejection(node))
            continue
        rows.extend(node_rows)
        rejected.extend(node_rejected)
    if require_rows and not rows:
        raise ValueError("No valid Government Bond auction in the source")
    return rows, rejected


def latest_per_tenor(
    rows: list[GovernmentBondAuction], rejected: list[RejectedBondObservation] | None = None
) -> list[GovernmentBondAuction]:
    """Newest auction per tenor among auctions that contain it.

    A newer valid auction that omits a tenor is not an error. Fallback rule: a row lists in
    `newer_rejected_tenders` the tenders with a rejected record dated strictly after the row's
    auction that could have carried THIS tenor (its own record, or a tender-level rejection).
    A rejected record for another tenor, an older rejection, or one with an unreadable date
    never marks the row. Tenders with no results are not rejections at all. No `stale` flag:
    BoZ publishes no machine-readable schedule and observed gaps (21 to 63 days in 2026) do
    not support a defensible threshold.
    """
    best: dict[str, GovernmentBondAuction] = {}
    for row in rows:
        if row.tenor not in best or row.auction_date > best[row.tenor].auction_date:
            best[row.tenor] = row
    out = []
    for t in BOND_TENORS:
        if t not in best:
            continue
        flagged: list[str] = []
        for r in rejected or []:
            if r.auction_date is not None and r.auction_date > best[t].auction_date and r.could_affect(t):
                name = r.tender_number or "unknown"
                if name not in flagged:
                    flagged.append(name)
        out.append(best[t].model_copy(update={"newer_rejected_tenders": flagged}))
    return out


def unverified_tenors(
    found: set[str], rejected: list[RejectedBondObservation]
) -> list[str]:
    """Tenors with no accepted row where some rejected record could have carried the value."""
    return [t for t in BOND_TENORS if t not in found and any(r.could_affect(t) for r in rejected)]


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
) -> tuple[list[GovernmentBondAuction], list[RejectedBondObservation]]:
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
    rejected: dict[tuple, RejectedBondObservation] = {}
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
            rejected[(r.tender_number, r.auction_date, r.affected_tenors, r.scope)] = r
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
    return ordered, list(rejected.values())
