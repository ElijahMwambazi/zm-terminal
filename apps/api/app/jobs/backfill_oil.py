"""Manual, bounded Brent/WTI backfill through OpenBB (fred provider). Requires FRED_API_KEY on the OpenBB service.

    python -m app.jobs.backfill_oil --from 2025-01-01 --to 2026-11-01 [--benchmark BRENT] [--dry-run]

One OpenBB request per benchmark. Every row is validated strictly; all benchmarks are validated before
anything is written, then stored in one transaction. Existing rows are never overwritten.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone

import httpx

from app.schemas.oil import OIL_BENCHMARKS
from app.services.oil_store import OilConflictError, save_oil_observations
from app.services.openbb_oil import fetch_oil_series

MAX_SPAN_DAYS = 3700


class BackfillError(Exception):
    pass


async def run_backfill(
    start: date, end: date, benchmarks: tuple[str, ...] = OIL_BENCHMARKS, *, dry_run: bool = False,
    engine=None, now: datetime | None = None, client: httpx.AsyncClient | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    if start > end:
        raise BackfillError("--from must not be after --to")
    if end > now.date():
        raise BackfillError("--to must not be in the future")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise BackfillError(f"Range exceeds {MAX_SPAN_DAYS} days; run in several bounded steps")
    points = []
    for benchmark in benchmarks:
        points.extend(await fetch_oil_series(benchmark, start, end, client=client))
    result = {"fetched": len(points), "inserted": 0, "already_present": 0}
    if points and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_oil_observations, points, retrieved_at=now, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_oil", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, metavar="YYYY-MM-DD", help="default: today (UTC)")
    parser.add_argument("--benchmark", choices=OIL_BENCHMARKS, help="default: both")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; write nothing")
    args = parser.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).date()
    benchmarks = (args.benchmark,) if args.benchmark else OIL_BENCHMARKS
    try:
        result = asyncio.run(run_backfill(args.start, end, benchmarks, dry_run=args.dry_run))
    except (BackfillError, OilConflictError, ValueError) as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"backfill failed: OpenBB request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"backfill failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
