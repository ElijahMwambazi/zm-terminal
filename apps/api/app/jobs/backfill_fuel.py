"""Manual, bounded ERB fuel-price import from the official Pump and Wholesale Prices archive.

    python -m app.jobs.backfill_fuel --from 2012-01-01 [--to 2026-11-12] [--dry-run]

Locates the official archive via ERB's WordPress media API, downloads it into memory (nothing
is written to disk), parses the whole workbook first (a layout/format surprise fails the job),
keeps rows whose pricing month falls in the requested range, and persists them in one
transaction through the conflict-aware store (new = insert, identical = already present,
different = conflict and nothing is written). `--dry-run` never touches PostgreSQL. Only
counts are printed: no prices, no workbook content. See app.services.erb_fuel for the
precision rule.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone

import httpx

from app.services import erb_fuel
from app.services.fuel_store import FuelConflictError, save_fuel_prices


def validate_range(start: date, end: date, *, today: date) -> None:
    if start > end:
        raise ValueError("--from must not be after --to")
    if end > today:
        raise ValueError("--to must not be in the future")


async def run_backfill(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None,
    engine=None, now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    validate_range(start, end, today=now.date())
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await run_backfill(start, end, dry_run=dry_run, client=owned, engine=engine, now=now)
    workbook, url = await erb_fuel.fetch_archive(client)
    rows, stats = erb_fuel.parse_workbook(workbook)
    selected = [r for r in rows if start <= r.effective_date <= end]
    result = {
        "records_examined": stats.records_examined, "valid_rows": len(rows), "in_range": len(selected),
        "missing_cells": stats.missing_cells, "hidden_precision_cells": stats.hidden_precision_cells, "invalid_value_cells": stats.invalid_value_cells,
        "ambiguous_duplicate_cells": stats.ambiguous_duplicate_cells, "inserted": 0, "already_present": 0,
        "conflicts": 0,
    }
    if selected and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_fuel_prices, selected, retrieved_at=now, source_url=url, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_fuel", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, metavar="YYYY-MM-DD", help="default: today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).date()
    try:
        result = asyncio.run(run_backfill(args.start, end, dry_run=args.dry_run))
    except FuelConflictError as exc:
        print(f"backfill failed: {exc} (conflicts={exc.count})", file=sys.stderr)
        return 1
    except (erb_fuel.FuelArchiveError, ValueError) as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"backfill failed: ERB request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"backfill failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
