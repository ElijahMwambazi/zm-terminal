"""Manual, explicit import of the BoZ commercial bank USD/ZMW rates for ONE calendar date.

    python -m app.jobs.import_bank_fx --date YYYY-MM-DD [--dry-run]

One invocation = one requested date = exactly one request to www.boz.zm. There is no range, backfill, scheduling, or latest/previous-day
fallback: a date BoZ has not published (empty `data`) is reported as `no_publication=1` and nothing is written. The whole returned batch
(every node revision of that date) is validated by the strict parser first and then saved atomically; a stored same-revision conflict
rolls the batch back.

--dry-run performs the one source request and the full validation but NEVER opens PostgreSQL, so it cannot detect conflicts with stored
data. Output is counts and layouts only: never rates, raw payloads, bank names or connection strings. Rights stay review_required.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone

import httpx

from app.services.bank_fx_models import BankFxError
from app.services.bank_fx_store import BankFxConflictError, save_bank_fx_snapshots
from app.services.boz_bank_fx import fetch_bank_fx_snapshots, new_client

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_date(value: str) -> date:
    """Exactly YYYY-MM-DD and a real calendar date."""
    try:
        if not _DATE.fullmatch(value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a real calendar date in YYYY-MM-DD form") from None


async def _fetch(day: date, client: httpx.AsyncClient | None, now: datetime):
    if client is not None:
        return await fetch_bank_fx_snapshots(client, day, retrieved_at=now)
    async with new_client() as owned:
        return await fetch_bank_fx_snapshots(owned, day, retrieved_at=now)


def run_import(day: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None, engine=None,
               now: datetime | None = None) -> dict[str, object]:
    """One request, full validation, then (unless dry-run) one atomic save. Returns operational counts only."""
    now = now or datetime.now(timezone.utc)
    snapshots = asyncio.run(_fetch(day, client, now))
    result: dict[str, object] = {
        "date": day.isoformat(), "mode": "dry-run" if dry_run else "import", "snapshots": len(snapshots),
        "rate_rows": sum(len(s.rates) for s in snapshots), "layouts": ",".join(sorted({s.table_layout for s in snapshots})) or "none",
    }
    if not snapshots:
        result["no_publication"] = 1
        return result
    result["no_publication"] = 0
    if dry_run:
        result["stored_conflicts"] = "not_checked"  # a dry run never opens PostgreSQL
        return result
    saved = save_bank_fx_snapshots(snapshots, engine=engine)
    result["inserted"], result["already_present"] = saved.inserted, saved.already_present
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.import_bank_fx", description=__doc__.split("\n")[0])
    parser.add_argument("--date", required=True, type=parse_date, help="the one observation date to import (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="one source request and full validation, but never opens PostgreSQL (cannot detect stored conflicts)")
    args = parser.parse_args(argv)
    try:
        result = run_import(args.date, dry_run=args.dry_run)
    except (BankFxError, BankFxConflictError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)  # these messages carry no rate values or payload text
        return 1
    except Exception as exc:  # network/database: the type only, never a URL, credentials or response content
        print(f"import failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("bank_fx_import " + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
