"""Manual, bounded import of completed daily BTC/USD candles from the Bitstamp public API.

    python -m app.jobs.backfill_btc [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--dry-run]

Pages forward with start + limit (<= 1000 candles per request, exclude_current_candle=true, about 1 s between pages; never
`end`), validates every candle strictly, then stores the selected candles in one atomic transaction. Zero-volume candles
are stored exactly as published; days Bitstamp does not return are never invented. Prints counts only; no raw payload,
candle value or file is written or logged.

--from is an operational start (default 2011-08-17: Bitstamp answers an earlier `start` with an empty list), not a claim
about when the market began; whatever the API publishes is what is stored. An already-stored candle with different values
is a conflict: the whole import stops and nothing is written. --dry-run validates and counts without touching PostgreSQL,
so it cannot detect conflicts with stored values.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone

import httpx

from app.services.bitstamp_btc import BitstampError, fetch_daily_candles
from app.services.btc_store import BtcConflictError, save_daily_candles

DEFAULT_START = date(2011, 8, 17)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class BackfillError(Exception):
    pass


def parse_date(text: str) -> date:
    if not _DATE.fullmatch(text):
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("not a valid calendar date") from exc


async def run_import(
    start: date, end: date | None, *, dry_run: bool = False, client: httpx.AsyncClient | None = None, engine=None,
    now: datetime | None = None, delay: float | None = None,
) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if end is not None and start > end:
        raise BackfillError("--from must not be after --to")
    kwargs = {} if delay is None else {"delay": delay}
    if client is None:
        async with httpx.AsyncClient() as owned:
            candles, pages = await fetch_daily_candles(start, client=owned, now=now, **kwargs)
    else:
        candles, pages = await fetch_daily_candles(start, client=client, now=now, **kwargs)
    selected = [c for c in candles if end is None or c.candle_open_at.date() <= end]
    result: dict[str, object] = {
        "pages": pages, "candles": len(selected), "zero_volume": sum(1 for c in selected if c.volume_btc == 0),
        "first": selected[0].candle_open_at.date().isoformat() if selected else None,
        "last": selected[-1].candle_open_at.date().isoformat() if selected else None,
        "inserted": 0, "already_present": 0,
    }
    if selected and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_daily_candles, selected, retrieved_at=now, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_btc", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=parse_date, default=DEFAULT_START, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=parse_date, metavar="YYYY-MM-DD", help="default: the newest completed candle")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; does not touch PostgreSQL")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_import(args.start, args.end, dry_run=args.dry_run))
    except (BackfillError, BtcConflictError, BitstampError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"import failed: Bitstamp request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"import failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
