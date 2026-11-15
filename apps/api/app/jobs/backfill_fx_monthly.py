"""Manual import of official BoZ monthly USD/ZMW averages from vetted Fortnightly Statistics PDFs (Table 02, 2014-01 onward).

    python -m app.jobs.backfill_fx_monthly [--from YYYY-MM] [--to YYYY-MM] [--dry-run]

Only the explicitly vetted publications in app.services.boz_monthly_source.PUBLICATIONS are requested (one sequential GET each,
2 s apart, no retries, HTTPS BoZ host only, no redirects, size-capped). Every needed PDF is fetched, text-extracted, strictly
interpreted (Table 02 "Monthly Averages", BoZ columns, buy <= mid <= sell) and cross-checked against the other publications
BEFORE anything is written; overlapping months must be numerically identical or the import fails. The published mid is stored
as published. The requested months must be gap-free (nothing is interpolated or forward-filled). Persistence is one atomic,
conflict-aware transaction (new = insert, identical = already present, different = conflict and nothing is written).
`--dry-run` never touches PostgreSQL. Only counts and months are printed: no rates, no PDF text. Phase 1 only: no 2010-2013
Annual Report months and no ZMK conversion.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone

import httpx

from app.services import boz_monthly_source as source
from app.services.fx_monthly_combine import MonthlyConflictError, combine, find_gaps
from app.services.fx_monthly_store import save_monthly_averages


class ImportRequestError(Exception):
    """A request or data problem that stops the import before any write. Never carries rates."""


def parse_month(value: str) -> date:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError("expected YYYY-MM")
    return date(int(value[:4]), int(value[5:7]), 1)


def validate_range(start: date, end: date, *, today: date) -> None:
    if start > end:
        raise ImportRequestError("--from must not be after --to")
    if end > today.replace(day=1):
        raise ImportRequestError("--to must not be in the future")
    if start < source.FIRST_COVERED_MONTH or end > source.LAST_COVERED_MONTH:
        raise ImportRequestError(
            f"Range must lie within the months covered by the vetted publications "
            f"({source.FIRST_COVERED_MONTH:%Y-%m} to {source.LAST_COVERED_MONTH:%Y-%m})"
        )


async def run_import(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None, engine=None,
    now: datetime | None = None, pause: float = source.REQUEST_PAUSE_SECONDS, sleep=asyncio.sleep,
) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    validate_range(start, end, today=now.date())
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await run_import(start, end, dry_run=dry_run, client=owned, engine=engine, now=now, pause=pause, sleep=sleep)
    publications = source.select_publications(start, end)
    parsed = []
    for index, publication in enumerate(publications):
        if index:
            await sleep(pause)  # sequential and polite
        pdf = await source.fetch_pdf(publication, client)
        parsed.append((publication, await source.load_publication_async(publication, pdf)))
    combined = combine(parsed)
    gaps = find_gaps(combined.observations, start, end)
    if gaps:
        raise ImportRequestError(f"{len(gaps)} calendar month(s) are missing, first {gaps[0]:%Y-%m}; nothing was written")
    selected = [o for o in combined.observations if start <= o.month <= end]
    result: dict[str, object] = {
        "publications_fetched": len(publications), "publications_parsed": len(parsed),
        "months_parsed": len(combined.observations), "overlaps_checked": combined.overlaps_checked,
        "months_selected": len(selected), "first_month": f"{selected[0].month:%Y-%m}" if selected else "none",
        "last_month": f"{selected[-1].month:%Y-%m}" if selected else "none", "gaps": 0, "inserted": 0, "already_present": 0,
    }
    if selected and not dry_run:
        # Persist only after every publication parsed and agreed; one transaction, all or nothing.
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_monthly_averages, selected, imported_at=now, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_fx_monthly", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=parse_month, default=source.FIRST_COVERED_MONTH, metavar="YYYY-MM",
                        help=f"default: {source.FIRST_COVERED_MONTH:%Y-%m}")
    parser.add_argument("--to", dest="end", type=parse_month, default=source.LAST_COVERED_MONTH, metavar="YYYY-MM",
                        help=f"default: {source.LAST_COVERED_MONTH:%Y-%m} (latest vetted month)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_import(args.start, args.end, dry_run=args.dry_run))
    except MonthlyConflictError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except (ImportRequestError, source.PublicationError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"import failed: Bank of Zambia request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"import failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
