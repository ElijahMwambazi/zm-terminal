"""Manual, bounded ERB monthly-publication import: press-statement retail prices + build-ups.

    python -m app.jobs.backfill_fuel_buildups --from 2026-01 [--to 2026-11] [--dry-run]

Discovers the monthly PDFs through ERB's media API (no hard-coded filenames), downloads the ones
in the requested pricing-month range sequentially (1 s apart, no retries, at most MAX_MONTHS
months), parses them with the strict parsers in app.services.erb_pdf and persists everything in
one transaction through the conflict-aware stores. Retail prices from a press statement use the
same identity as the XLSX archive (first day of the pricing month): identical values are fine, a
different value for the same identity is a conflict and nothing is written. A press statement
that disagrees with its build-up (see erb_pdf.reconcile) skips that month. Scanned (image-only)
PDFs are counted, never OCR'd. `--dry-run` never touches PostgreSQL. Only counts are printed.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

from app.services import erb_pdf
from app.services.erb_fuel import FuelRow
from app.services.fuel_buildup_store import save_fuel_publications
from app.services.fuel_store import FuelConflictError

MAX_MONTHS = 24
PAUSE_SECONDS = 1.0
LUSAKA = timezone(timedelta(hours=2))
KINDS = ("press", "pump", "wholesale", "jet")


def parse_month(value: str) -> date:
    year, month = value.split("-")
    if len(year) != 4 or len(month) != 2:
        raise ValueError("Months must be YYYY-MM")
    return date(int(year), int(month), 1)


def month_range(start: date, end: date, *, today: date) -> list[date]:
    if start > end:
        raise ValueError("--from must not be after --to")
    if end > today.replace(day=1):
        raise ValueError("--to must not be in the future")
    months, cursor = [], start
    while cursor <= end:
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    if len(months) > MAX_MONTHS:
        raise ValueError(f"Range exceeds {MAX_MONTHS} months; run in several bounded steps")
    return months


async def run_backfill(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None, engine=None,
    now: datetime | None = None, pause: float = PAUSE_SECONDS, sleep=asyncio.sleep,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    months = month_range(start, end, today=now.astimezone(LUSAKA).date())
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await run_backfill(start, end, dry_run=dry_run, client=owned, engine=engine, now=now,
                                      pause=pause, sleep=sleep)
    discovery = await erb_pdf.discover_publications(client)
    counts = {"months": len(months), "documents_found": 0, "documents_missing": 0, "scanned_documents": 0,
              "unsupported_documents": 0, "reconciliation_failures": 0, "ambiguous_documents": sum(1 for m, _ in discovery.ambiguous if m in months),
              "retail_rows": 0, "build_up_items": 0, "retail_inserted": 0, "retail_already_present": 0,
              "items_inserted": 0, "items_already_present": 0}
    retail: list[tuple[FuelRow, str]] = []
    builds: list[tuple[erb_pdf.ParsedBuildUp, str]] = []
    first_download = True
    for month in months:
        parsed: dict[str, object] = {}
        urls: dict[str, str] = {}
        for kind in KINDS:
            url = discovery.docs.get((month, kind))
            if url is None:
                counts["documents_missing"] += 1  # none published, or two different files (counted as ambiguous)
                continue
            counts["documents_found"] += 1
            if not first_download:
                await sleep(pause)
            first_download = False
            try:
                lines = erb_pdf.pdf_lines(await erb_pdf.download_pdf(client, url))
                parsed[kind] = {"press": erb_pdf.parse_press_statement, "pump": erb_pdf.parse_pump_buildup,
                                "wholesale": erb_pdf.parse_wholesale_buildup, "jet": erb_pdf.parse_jet_buildup}[kind](lines)
                urls[kind] = url
            except erb_pdf.ScannedPdfError:
                counts["scanned_documents"] += 1
            except erb_pdf.FuelPdfError:
                counts["unsupported_documents"] += 1
        press, pump = parsed.get("press"), parsed.get("pump")
        if press is not None and pump is not None:
            try:
                erb_pdf.reconcile(press, pump)
            except erb_pdf.FuelPdfError:
                counts["reconciliation_failures"] += 1
                continue  # neither value is replaced by the other; the whole month is skipped
        for kind in ("pump", "wholesale", "jet"):
            build = parsed.get(kind)
            if build is not None and build.month == month:
                builds.append((build, urls[kind]))
        if press is not None and press.month == month:
            retail += [(FuelRow(month, product, "retail", price, "litre"), urls["press"]) for product, price in press.prices.items()]
    counts["retail_rows"] = len(retail)
    counts["build_up_items"] = sum(len(b.items) for b, _ in builds)
    if (retail or builds) and not dry_run:
        counts.update(await asyncio.to_thread(save_fuel_publications, retail, builds, retrieved_at=now, engine=engine))
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.backfill_fuel_buildups", description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", type=parse_month, required=True, metavar="YYYY-MM")
    parser.add_argument("--to", dest="end", type=parse_month, metavar="YYYY-MM", help="default: the current month (Africa/Lusaka)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    end = args.end or datetime.now(timezone.utc).astimezone(LUSAKA).date().replace(day=1)
    try:
        result = asyncio.run(run_backfill(args.start, end, dry_run=args.dry_run))
    except FuelConflictError as exc:
        print(f"backfill failed: {exc} (conflicts={exc.count})", file=sys.stderr)
        return 1
    except (erb_pdf.FuelPdfError, ValueError) as exc:
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
