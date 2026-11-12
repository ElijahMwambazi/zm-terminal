"""Manual, bounded USD/ZMW historical backfill from the official BoZ JSON view.

    python -m app.jobs.backfill_usdzmw --from 2026-01-22 --to 2026-11-12 [--dry-run]

Uses the same endpoint as the live adapter with the `time[min]`/`time[max]` filters the
BoZ website itself uses. The endpoint returns at most 500 rows per request with no
paging, so the range is split into <=31-day windows and any window that reaches the
cap is treated as possibly truncated and fails. Every row is validated strictly; valid rows
outside the official slots (boz.OFFICIAL_SLOT_UTC_HOURS, the same policy the live adapter
uses) are reported as "not selected", never stored and never called invalid. Nothing is
inferred or interpolated; raw responses are never stored or logged.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.schemas.fx import BOZ_SOURCE_URL
from app.services import boz
from app.services.fx_store import FxConflictError, save_fx_observations

WINDOW_DAYS = 31
MAX_SPAN_DAYS = 400
ROW_CAP = 500  # observed upstream per-request cap; reaching it means truncation is possible
REQUEST_PAUSE_SECONDS = 2.0
EARLIEST_VERIFIED_DATE = date(2026, 1, 22)  # no earlier rows were found in the view

Observations = dict[datetime, tuple[Decimal, Decimal, Decimal]]


class BackfillError(Exception):
    pass


def plan_windows(start: date, end: date, *, today: date) -> list[tuple[date, date]]:
    if start > end:
        raise BackfillError("--from must not be after --to")
    if end > today:
        raise BackfillError("--to must not be in the future")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise BackfillError(f"Range exceeds {MAX_SPAN_DAYS} days; run in several bounded steps")
    windows, cursor = [], start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=WINDOW_DAYS - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


async def fetch_observations(
    windows: list[tuple[date, date]], *, client: httpx.AsyncClient,
    now: datetime, pause: float = REQUEST_PAUSE_SECONDS, sleep=asyncio.sleep,
) -> tuple[Observations, list[datetime]]:
    """Returns (selected official-slot observations, timestamps of valid but not-selected rows)."""
    merged: Observations = {}
    for index, (first, last) in enumerate(windows):
        if index:
            await sleep(pause)  # sequential and polite; no retries
        payload = await boz.fetch_boz_payload(client, {
            "time[min]": f"{first.isoformat()} 00:00:00",
            "time[max]": f"{last.isoformat()} 23:59:59",
        })
        if isinstance(payload, list) and len(payload) >= ROW_CAP:
            raise BackfillError(f"Window {first}..{last} returned {len(payload)} rows (cap); results may be truncated")
        for observed_at, rates in boz.parse_boz_observations(payload, allow_empty=True).items():
            if observed_at > now:
                raise ValueError("Observation is in the future")
            if merged.setdefault(observed_at, rates) != rates:
                raise ValueError("Conflicting observations at the same timestamp")
    return boz.select_official(merged)


async def run_backfill(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None,
    engine=None, now: datetime | None = None, pause: float = REQUEST_PAUSE_SECONDS, sleep=asyncio.sleep,
) -> dict[str, int]:
    """Returns counts; `not_selected` timestamps are also written to stderr."""
    now = now or datetime.now(timezone.utc)
    windows = plan_windows(start, end, today=now.date())
    if client is None:
        async with httpx.AsyncClient() as owned:
            observations, not_selected = await fetch_observations(windows, client=owned, now=now, pause=pause, sleep=sleep)
    else:
        observations, not_selected = await fetch_observations(windows, client=client, now=now, pause=pause, sleep=sleep)
    for stamp in not_selected:
        print(f"not selected (valid row outside official slots) at {stamp.isoformat()}", file=sys.stderr)
    result = {"windows": len(windows), "selected": len(observations), "not_selected": len(not_selected),
              "inserted": 0, "already_present": 0}
    if observations and not dry_run:
        # Persist only after every window validated; one transaction, all or nothing.
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_fx_observations, observations,
            retrieved_at=now, source="boz", source_url=BOZ_SOURCE_URL, engine=engine,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_usdzmw", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="default: today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; write nothing")
    args = parser.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).date()
    if args.start < EARLIEST_VERIFIED_DATE:
        print(f"note: BoZ rows before {EARLIEST_VERIFIED_DATE} were not found; earlier windows are likely empty", file=sys.stderr)
    try:
        result = asyncio.run(run_backfill(args.start, end, dry_run=args.dry_run))
    except (BackfillError, FxConflictError, ValueError) as exc:
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
