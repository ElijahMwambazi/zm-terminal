"""Manual, bounded Brent/WTI monthly import from the official World Bank Pink Sheet workbook.

    python -m app.jobs.backfill_oil --from 2025-01 [--to 2026-10] [--dry-run]

Discovers the monthly workbook on the World Bank Commodity Markets page, downloads it once (no API key), parses it
strictly, then stores the requested months in one atomic transaction. Months outside the workbook, and months a
series does not publish, are never invented. Existing rows are never overwritten: a revised published value is a
conflict. Prints counts only; no raw workbook or price series is stored or logged.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone

import httpx

from app.services.oil_store import OilConflictError, save_oil_observations
from app.services.worldbank_oil import (
    WorldBankOilError, discover_workbook_url, download_workbook, new_client, parse_workbook,
)

_MONTH = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


class BackfillError(Exception):
    pass


def parse_month(text: str) -> date:
    match = _MONTH.fullmatch(text)
    if not match:
        raise argparse.ArgumentTypeError("expected YYYY-MM")
    return date(int(match.group(1)), int(match.group(2)), 1)


async def run_import(
    start: date, end: date | None, *, dry_run: bool = False, client: httpx.AsyncClient | None = None,
    engine=None, now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    if end is not None and start > end:
        raise BackfillError("--from must not be after --to")
    if client is None:
        async with new_client() as owned:
            url = await discover_workbook_url(owned)
            source = await download_workbook(owned, url)
    else:
        url = await discover_workbook_url(client)
        source = await download_workbook(client, url)
    book = parse_workbook(source)
    end = end or book.last_month
    if start < book.first_month or end > book.last_month:
        raise BackfillError(f"Requested months must lie within the workbook ({book.first_month:%Y-%m} to {book.last_month:%Y-%m})")
    selected = [p for p in book.observations if start <= p.period_start <= end]
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    result = {"months_requested": months, "selected": len(selected),
              "not_published": months * 2 - len(selected), "inserted": 0, "already_present": 0}
    if selected and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_oil_observations, selected, publication_date=book.publication_date, source_url=url,
            retrieved_at=now, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_oil", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=parse_month, required=True, metavar="YYYY-MM")
    parser.add_argument("--to", dest="end", type=parse_month, metavar="YYYY-MM", help="default: newest month in the workbook")
    parser.add_argument("--dry-run", action="store_true", help="download and validate only; write nothing")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_import(args.start, args.end, dry_run=args.dry_run))
    except (BackfillError, OilConflictError, WorldBankOilError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"import failed: World Bank request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"import failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
