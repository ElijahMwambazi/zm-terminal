"""Manual Ministry of Agriculture national-average grain-price import.

    python -m app.jobs.backfill_agriculture --from 2025-04 [--to 2026-01] [--dry-run]

Discovers official Ministry PDFs through the public WordPress media API, accepts the verified
Layout B family only, parses the complete document before persistence, then writes one atomic
batch. Output is counts only; raw PDF text and prices are never logged or stored.
"""

import argparse
import asyncio
import ssl
import sys
from datetime import date, datetime, timezone

import httpx

from app.config import get_settings
from app.services import moa_agriculture
from app.services.agriculture_store import AgricultureConflictError, save_agriculture_prices


def parse_month(value: str) -> date:
    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM") from exc
    if len(value) != 7:
        raise argparse.ArgumentTypeError("expected YYYY-MM")
    return parsed


def validate_range(start: date, end: date, *, today: date) -> None:
    current = date(today.year, today.month, 1)
    if start > end:
        raise ValueError("--from must not be after --to")
    if end > current:
        raise ValueError("--to must not be in the future")


def _ssl_context() -> ssl.SSLContext:
    settings = get_settings()
    return ssl.create_default_context(cafile=settings.moa_ca_bundle or None)


async def run_backfill(
    start: date, end: date, *, dry_run: bool = False, client: httpx.AsyncClient | None = None,
    engine=None, now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    validate_range(start, end, today=now.date())
    if client is None:
        async with httpx.AsyncClient(verify=_ssl_context()) as owned:
            return await run_backfill(start, end, dry_run=dry_run, client=owned, engine=engine, now=now)

    bulletins = await moa_agriculture.discover_bulletins(client)
    all_rows = []
    supported = unsupported = 0
    for bulletin in bulletins:
        data = await moa_agriculture.fetch_pdf(client, bulletin)
        try:
            rows = moa_agriculture.parse_pdf(
                data, publication_date=bulletin.publication_date,
                source_url=bulletin.source_url, retrieved_at=now,
            )
        except moa_agriculture.UnsupportedLayoutError:
            unsupported += 1
            continue
        supported += 1
        all_rows.extend(rows)

    # Collapse repeated publication of the same observation only when the value is identical.
    unique = {}
    for row in all_rows:
        identity = (
            row.provider, row.commodity, row.period_start, row.period_end, row.grade, row.variety,
            row.location_scope, row.location, row.source_unit,
        )
        existing = unique.get(identity)
        if existing is not None and (
            existing.source_price != row.source_price
            or existing.normalized_zmw_per_kg != row.normalized_zmw_per_kg
        ):
            raise moa_agriculture.AgricultureSourceError("Conflicting Ministry publications for the same observation")
        unique[identity] = row

    selected = [row for row in unique.values() if start <= row.period_start <= end]
    selected.sort(key=lambda row: (row.period_start, row.commodity))
    result = {
        "documents_discovered": len(bulletins),
        "documents_supported": supported,
        "documents_unsupported": unsupported,
        "observations_parsed": len(unique),
        "in_range": len(selected),
        "maize_observations": sum(row.commodity == "MAIZE" for row in selected),
        "soya_observations": sum(row.commodity == "SOYA_BEANS" for row in selected),
        "inserted": 0,
        "already_present": 0,
        "conflicts": 0,
    }
    if selected and not dry_run:
        result["inserted"], result["already_present"] = await asyncio.to_thread(
            save_agriculture_prices, selected, engine=engine,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.backfill_agriculture", description=__doc__.split("\n")[0],
    )
    parser.add_argument("--from", dest="start", type=parse_month, required=True, metavar="YYYY-MM")
    parser.add_argument("--to", dest="end", type=parse_month, metavar="YYYY-MM", help="default: current UTC month")
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only; never touches PostgreSQL")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    end = args.end or date(now.year, now.month, 1)
    try:
        result = asyncio.run(run_backfill(args.start, end, dry_run=args.dry_run, now=now))
    except AgricultureConflictError as exc:
        print(f"backfill failed: agriculture persistence conflict (conflicts={exc.count})", file=sys.stderr)
        return 1
    except moa_agriculture.AgricultureSourceError as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"backfill failed: Ministry request error ({type(exc).__name__})", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"backfill failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(("dry-run " if args.dry_run else "") + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
