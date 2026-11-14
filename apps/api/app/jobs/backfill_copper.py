"""Manual, bounded import of the monthly IMF Copper benchmark (PCPS series G001.PCOPP.USD.M).

    python -m app.jobs.backfill_copper --from 1992-01 [--to 2026-10] [--dry-run]

Makes exactly one request to the IMF SDMX API (no key, no bulk dataset download), parses the series strictly, then stores the
requested months in one atomic transaction. Months the source does not publish are never invented (they are counted as
not_published). Prints counts only; no raw response or price series is stored or logged.

Revisions: the source history may be revisable. The importer never overwrites a stored month: if a stored month differs from
the published value the whole import stops and names the month (never a price); nothing is written. Narrow `--from` past the
conflicting month to keep importing newer months. Revision reconciliation is deferred.

--dry-run validates the request, the parser and the range selection but does not query or write PostgreSQL, so it cannot
detect conflicts against values already stored.

Local/internal use: public display, API redistribution and commercial use of IMF Copper data remain review_required.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone

import httpx

from app.services.copper_store import CopperConflictError, save_copper_months
from app.services.imf_pcps import IMF_COPPER_URL, ImfCopperError, fetch_copper_payload, new_client, parse_copper_series

_MONTH = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


class BackfillError(Exception):
    pass


def parse_month(text: str) -> date:
    match = _MONTH.fullmatch(text)
    if not match:
        raise argparse.ArgumentTypeError("expected YYYY-MM")
    try:
        return date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:  # year 0000 or month 00/13: a calendar error, not a traceback
        raise argparse.ArgumentTypeError("expected a valid calendar month YYYY-MM") from exc


async def run_import(
    start: date, end: date | None, *, dry_run: bool = False, client: httpx.AsyncClient | None = None, engine=None,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    if end is not None and start > end:
        raise BackfillError("--from must not be after --to")  # explicit range: no request needed to reject it
    if client is None:
        async with new_client() as owned:
            payload = await fetch_copper_payload(owned)
    else:
        payload = await fetch_copper_payload(client)
    months = parse_copper_series(payload)
    first, last = months[0].period_start, months[-1].period_start
    end = end or last
    if start > end:  # only reachable when --to was omitted; the range is never clamped
        raise BackfillError(f"--from is after the newest available IMF month ({last:%Y-%m})")
    if start < first or end > last:
        raise BackfillError(f"Requested months must lie within the IMF series ({first:%Y-%m} to {last:%Y-%m})")
    selected = [m for m in months if start <= m.period_start <= end]
    requested = (end.year - start.year) * 12 + end.month - start.month + 1
    result = {"months_requested": requested, "selected": len(selected), "not_published": requested - len(selected),
              "inserted": 0, "already_present": 0}
    if selected and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_copper_months, selected, source_url=IMF_COPPER_URL, retrieved_at=now, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_copper", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=parse_month, required=True, metavar="YYYY-MM")
    parser.add_argument("--to", dest="end", type=parse_month, metavar="YYYY-MM", help="default: the newest month the IMF publishes")
    parser.add_argument("--dry-run", action="store_true",
                        help="request, validate, parse and select the range; does not touch PostgreSQL, so it cannot detect conflicts with stored values")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_import(args.start, args.end, dry_run=args.dry_run))
    except (BackfillError, CopperConflictError, ImfCopperError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"import failed: IMF request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"import failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
