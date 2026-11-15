"""Manual import of official BoZ monthly USD/ZMW averages, 2010-01 onward, into fx_monthly_averages.

    python -m app.jobs.backfill_fx_monthly [--from YYYY-MM] [--to YYYY-MM] [--dry-run]

Source families are chosen from the requested observation months, one canonical source per month:
  2010-01..2013-12  BoZ Annual Reports, Table 13 (pinned SHA-256/size/page; old kwacha divided exactly by 1000 before 2013)
  2014-01..2026-09  BoZ Fortnightly Statistics, Table 02 (the Phase-1 chain)
Only vetted manifest URLs are requested (one sequential GET each, 2 s apart, no retries, HTTPS BoZ host only, no redirects,
size-capped); only publications needed for the range are fetched, plus the 2014 Annual Report when the range crosses the
2013/2014 boundary, where it is used for VALIDATION ONLY (its 2014 months must equal the Fortnightly months exactly; it never
supplies rows). Everything is parsed, normalized, ordering-checked, cross-checked, assembled (exactly one canonical row per month,
no cross-series overlap, no gaps, nothing interpolated) BEFORE anything is written. Persistence is one atomic, conflict-aware
transaction into a database that already has migration 013 (the importer never applies migrations). `--dry-run` never touches
PostgreSQL and works before migration 013. Only counts and months are printed: no rates, no PDF text.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone

import httpx

from app.services import boz_annual_source as annual
from app.services import boz_monthly_source as source
from app.services.fx_monthly_canonical import (
    CanonicalSetError, assemble, build_annual_observations, check_annual_overlaps,
)
from app.services.fx_monthly_combine import MonthlyConflictError, combine
from app.services.fx_monthly_store import save_monthly_averages

FIRST_MONTH = annual.FIRST_COVERED_MONTH  # 2010-01
LAST_MONTH = source.LAST_COVERED_MONTH  # latest vetted Fortnightly month
BOUNDARY = source.FIRST_COVERED_MONTH  # first Fortnightly month (2014-01); the last Annual month is the one before


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
    if start < FIRST_MONTH or end > LAST_MONTH:
        raise ImportRequestError(
            f"Range must lie within the months covered by the vetted publications ({FIRST_MONTH:%Y-%m} to {LAST_MONTH:%Y-%m})"
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
    reports = annual.select_canonical_reports(start, min(end, annual.LAST_COVERED_MONTH)) if start < BOUNDARY else []
    publications = source.select_publications(max(start, BOUNDARY), end) if end >= BOUNDARY else []
    crosses = start < BOUNDARY <= end
    validation = [annual.VALIDATION_REPORTS[0]] if crosses else []
    fetches = [(r, annual.fetch_report, annual.load_annual_report_async) for r in (*reports, *validation)]
    fetches += [(p, source.fetch_pdf, source.load_publication_async) for p in publications]
    loaded: list[tuple[object, object]] = []
    for index, (item, fetch, load) in enumerate(fetches):
        if index:
            await sleep(pause)  # sequential and polite, across both source families
        pdf = await fetch(item, client)
        loaded.append((item, await load(item, pdf)))
    annual_loaded = [(i, t) for i, t in loaded if isinstance(i, annual.AnnualReport)]
    canonical_loaded = [(i, t) for i, t in annual_loaded if i.role == "canonical"]
    fortnightly_loaded = [(i, t) for i, t in loaded if not isinstance(i, annual.AnnualReport)]
    annual_overlaps = check_annual_overlaps(annual_loaded)
    combined = combine(fortnightly_loaded)
    bridge = "not_applicable"
    if validation:  # validation only: nothing from this report ever enters the persistence set
        table_2014 = next(t for i, t in annual_loaded if i.role == "validation")
        annual.validate_2014_bridge(table_2014, combined.observations)
        bridge = "pass"
    try:
        assembled = assemble(build_annual_observations(canonical_loaded), combined.observations, start, end)
    except CanonicalSetError as exc:
        raise ImportRequestError(str(exc)) from exc
    if assembled.gaps:
        raise ImportRequestError(
            f"{len(assembled.gaps)} calendar month(s) are missing, first {assembled.gaps[0]:%Y-%m}; nothing was written")
    selected = list(assembled.observations)
    result: dict[str, object] = {
        "publications_fetched": len(fetches), "publications_parsed": len(loaded),
        "months_parsed": len(combined.observations), "overlaps_checked": combined.overlaps_checked + annual_overlaps,
        "months_selected": len(selected), "first_month": f"{selected[0].month:%Y-%m}" if selected else "none",
        "last_month": f"{selected[-1].month:%Y-%m}" if selected else "none", "gaps": 0,
        "annual_candidates": assembled.annual_count, "fortnightly_candidates": assembled.fortnightly_count,
        "source_conflicts": 0, "cross_series_conflicts": 0, "ordering_exceptions_used": assembled.ordering_exceptions_used,
        "bridge_2014": bridge, "inserted": 0, "already_present": 0,
    }
    if selected and not dry_run:
        # Persist only after every publication parsed and agreed; one transaction, all or nothing.
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_monthly_averages, selected, imported_at=now, engine=engine)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_fx_monthly", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=parse_month, default=FIRST_MONTH, metavar="YYYY-MM",
                        help=f"default: {FIRST_MONTH:%Y-%m}")
    parser.add_argument("--to", dest="end", type=parse_month, default=LAST_MONTH, metavar="YYYY-MM",
                        help=f"default: {LAST_MONTH:%Y-%m} (latest vetted month)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_import(args.start, args.end, dry_run=args.dry_run))
    except MonthlyConflictError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except (ImportRequestError, source.PublicationError, annual.AnnualSourceError) as exc:
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
