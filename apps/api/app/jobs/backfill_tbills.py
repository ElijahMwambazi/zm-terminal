"""Manual, bounded Treasury Bill history import from the official BoZ JSON:API.

    python -m app.jobs.backfill_tbills --from 2026-01-01 [--to 2026-11-12] [--dry-run]

Uses the same `tbills_auction_results` collection and the same strict parser as the live
adapter (whole-tender rejection, exact decimals). The collection supports a date filter, so
only the requested range is fetched, newest first, 50 tenders per page, following `links.next`
sequentially (1 s apart, no retries, at most MAX_PAGES pages; a truncated search fails). Every
page is validated before anything is written; persistence is one transaction through the
existing conflict-aware store (new = insert, identical = already present, different = conflict
and nothing is written). Empty legacy tenders are counted as "no results", not as rejected.
Only counts are printed; raw responses and values are never stored or logged.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone

import httpx

from app.config import get_settings
from app.schemas.tbills import TreasuryBillAuction
from app.services.boz_treasury import TBILL_USER_AGENT, parse_tbill_auctions
from app.services.jsonapi import TruncatedSearchError, date_range_params, fetch_pages, validate_range
from app.services.tbill_store import TbillConflictError, save_tbill_auctions

PAGE_SIZE = 50
MAX_PAGES = 10
PAUSE_SECONDS = 1.0


def _empty_titles(payload: object) -> set[str]:
    nodes = payload.get("data") if isinstance(payload, dict) else None
    out: set[str] = set()
    for node in nodes if isinstance(nodes, list) else []:
        try:
            if node["relationships"]["field_tbills_auction_results"]["data"] == []:
                out.add(node["attributes"]["title"])
        except (KeyError, TypeError):
            pass
    return out


async def run_backfill(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None,
    engine=None, now: datetime | None = None, pause: float = PAUSE_SECONDS, sleep=asyncio.sleep,
    max_pages: int = MAX_PAGES,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    validate_range(start, end, today=now.date())
    params = {
        "include": "field_tbills_auction_results", "sort": "-field_tbills_auction_date",
        "page[limit]": str(PAGE_SIZE),
        **date_range_params("field_tbills_auction_date", start.isoformat(), end.isoformat()),
    }
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await run_backfill(start, end, dry_run=dry_run, client=owned, engine=engine, now=now,
                                      pause=pause, sleep=sleep, max_pages=max_pages)
    pages = await fetch_pages(client, get_settings().boz_treasury_bills_url, params,
                              user_agent=TBILL_USER_AGENT, max_pages=max_pages, pause=pause, sleep=sleep)
    rows: dict[tuple, TreasuryBillAuction] = {}
    fetched = no_results = out_of_range = 0
    rejected: set[tuple] = set()
    for payload in pages:
        data = payload.get("data") if isinstance(payload, dict) else None
        if data == []:
            continue  # an empty range is legitimate
        parsed_rows, parsed_rejected = parse_tbill_auctions(payload, retrieved_at=now, require_rows=False)
        fetched += len(data)
        empties = _empty_titles(payload)
        no_results += len(empties)
        rejected |= {(r.tender_number, r.auction_date) for r in parsed_rejected if r.tender_number not in empties}
        for row in parsed_rows:
            if not (start <= row.auction_date <= end):
                out_of_range += 1
                continue
            key = (row.auction_date, row.tender_number, row.tenor_days)
            old = rows.setdefault(key, row)
            if (old.isin, old.discount_rate, old.yield_rate) != (row.isin, row.discount_rate, row.yield_rate):
                raise ValueError("Conflicting duplicate tender across pages")
    result = {"pages": len(pages), "tenders_fetched": fetched, "no_results": no_results,
              "valid_rows": len(rows), "rejected_tenders": len(rejected), "out_of_range": out_of_range,
              "inserted": 0, "already_present": 0}
    if rows and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_tbill_auctions, sorted(rows.values(), key=lambda r: (r.auction_date, r.tenor_days)), engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_tbills", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, metavar="YYYY-MM-DD", help="default: today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).date()
    try:
        result = asyncio.run(run_backfill(args.start, end, dry_run=args.dry_run))
    except (TruncatedSearchError, TbillConflictError, ValueError) as exc:
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
