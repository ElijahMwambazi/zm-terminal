"""Manual, bounded Government Bond history import from the official BoZ JSON:API.

    python -m app.jobs.backfill_bonds --from 2025-12-01 [--to 2026-11-12] [--dry-run]

Uses the live adapter's parser (`parse_bond_auctions`): a tender-level failure rejects the
whole tender, a malformed tenor rejects only that tenor, nothing is repaired, empty
"no results" tenders are not malformed. The `bond_auction` collection supports a date filter
(`field_bond_auction_date`, datetimes with a +02:00 offset), so only the range is fetched,
newest first, 50 per page, following `links.next` sequentially (1 s apart, no retries, at
most MAX_PAGES pages; a truncated search fails). All pages are validated before anything is
written; persistence is one transaction through the existing conflict-aware store (identity
is auction date + tender number + tenor, never the ISIN, so reopened bonds give many rows).
Only counts are printed; raw responses and values are never stored or logged.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone

import httpx

from app.config import get_settings
from app.schemas.bonds import GovernmentBondAuction
from app.services.bond_store import BondConflictError, save_bond_auctions
from app.services.boz_bonds import BOND_USER_AGENT, parse_bond_auctions
from app.services.jsonapi import TruncatedSearchError, date_range_params, fetch_pages, validate_range

PAGE_SIZE = 50
MAX_PAGES = 10
PAUSE_SECONDS = 1.0


def _no_results(payload: object) -> int:
    nodes = payload.get("data") if isinstance(payload, dict) else None
    count = 0
    for node in nodes if isinstance(nodes, list) else []:
        try:
            count += node["relationships"]["field_auction_results"]["data"] == []
        except (KeyError, TypeError):
            pass
    return count


async def run_backfill(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None,
    engine=None, now: datetime | None = None, pause: float = PAUSE_SECONDS, sleep=asyncio.sleep,
    max_pages: int = MAX_PAGES,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    validate_range(start, end, today=now.date())
    params = {
        "include": "field_auction_results", "sort": "-field_bond_auction_date",
        "page[limit]": str(PAGE_SIZE),
        **date_range_params("field_bond_auction_date", f"{start.isoformat()}T00:00:00+02:00",
                            f"{end.isoformat()}T23:59:59+02:00"),
    }
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await run_backfill(start, end, dry_run=dry_run, client=owned, engine=engine, now=now,
                                      pause=pause, sleep=sleep, max_pages=max_pages)
    pages = await fetch_pages(client, get_settings().boz_bond_auctions_url, params,
                              user_agent=BOND_USER_AGENT, max_pages=max_pages, pause=pause, sleep=sleep)
    rows: dict[tuple, GovernmentBondAuction] = {}
    fetched = no_results = out_of_range = tender_level = tenor_level = 0
    for payload in pages:
        data = payload.get("data") if isinstance(payload, dict) else None
        if data == []:
            continue  # an empty range is legitimate
        parsed_rows, rejected = parse_bond_auctions(payload, retrieved_at=now, require_rows=False)
        fetched += len(data)
        no_results += _no_results(payload)
        tender_level += sum(r.scope == "tender" for r in rejected)
        tenor_level += sum(r.scope == "tenor" for r in rejected)
        for row in parsed_rows:
            if not (start <= row.auction_date <= end):
                out_of_range += 1
                continue
            key = (row.auction_date, row.tender_number, row.tenor_years)
            old = rows.setdefault(key, row)
            if (old.isin, old.coupon_rate, old.yield_rate) != (row.isin, row.coupon_rate, row.yield_rate):
                raise ValueError("Conflicting duplicate auction across pages")
    result = {"pages": len(pages), "auctions_fetched": fetched, "no_results": no_results,
              "valid_rows": len(rows), "tender_level_rejections": tender_level,
              "tenor_level_rejections": tenor_level, "out_of_range": out_of_range,
              "inserted": 0, "already_present": 0}
    if rows and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_bond_auctions, sorted(rows.values(), key=lambda r: (r.auction_date, r.tenor_years)), engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_bonds", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, metavar="YYYY-MM-DD", help="default: today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).date()
    try:
        result = asyncio.run(run_backfill(args.start, end, dry_run=args.dry_run))
    except (TruncatedSearchError, BondConflictError, ValueError) as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"backfill failed: Bank of Zambia request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"backfill failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
