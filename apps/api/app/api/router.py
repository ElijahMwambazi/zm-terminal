import asyncio
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db import database_is_ready, get_engine
from app.schemas.fx import FxHistory, FxQuote
from app.schemas.tbills import TENORS, Tenor, TreasuryBillAuction, TreasuryBillsLatest
from app.services.boz import get_boz_fx_quote
from app.services.boz_treasury import get_boz_tbills, latest_per_tenor
from app.services.fx_store import FxConflictError, HistoryRange, fetch_fx_history, save_fx_quote
from app.services.tbill_store import TbillConflictError, save_tbill_auctions
from app.services.provider_policy import PROVIDER_POLICIES, ProviderPolicy
from app.services.openbb import get_openbb_coverage, openbb_is_ready

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


@router.get("/fx/USDZMW", response_model=FxQuote)
async def usd_zmw_quote() -> FxQuote:
    try:
        quote = await get_boz_fx_quote()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Bank of Zambia request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Bank of Zambia data unavailable or invalid") from exc
    # A valid live quote is still returned if storage fails; only the exception type is logged.
    try:
        await asyncio.to_thread(save_fx_quote, quote)
    except FxConflictError:
        logger.warning("USDZMW persistence conflict; live quote returned unsaved")
    except Exception as exc:
        logger.warning("USDZMW persistence failed (%s); quote returned unsaved", type(exc).__name__)
    return quote


@router.get("/fx/USDZMW/history", response_model=FxHistory)
async def usd_zmw_history(range: HistoryRange = "1M") -> FxHistory:
    try:
        points = await asyncio.to_thread(fetch_fx_history, range, now=datetime.now(timezone.utc))
    except Exception as exc:
        logger.warning("USDZMW history read failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Stored history unavailable") from exc
    return FxHistory(range=range, count=len(points), observations=points)


async def _load_tbills() -> tuple[list[TreasuryBillAuction], list[str]]:
    try:
        rows, skipped = await get_boz_tbills()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Bank of Zambia request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Bank of Zambia data unavailable or invalid") from exc
    try:
        await asyncio.to_thread(save_tbill_auctions, rows)
    except TbillConflictError:
        logger.warning("T-bill persistence conflict; live data returned unsaved")
    except Exception as exc:
        logger.warning("T-bill persistence failed (%s); live data returned unsaved", type(exc).__name__)
    return rows, skipped


@router.get("/treasury-bills/latest", response_model=TreasuryBillsLatest)
async def treasury_bills_latest() -> TreasuryBillsLatest:
    rows, skipped = await _load_tbills()
    latest = latest_per_tenor(rows, datetime.now(timezone.utc))
    have = {r.tenor for r in latest}
    return TreasuryBillsLatest(
        tenors=latest, missing_tenors=[t for t in TENORS if t not in have], skipped_tenders=skipped
    )


@router.get("/treasury-bills/{tenor}", response_model=TreasuryBillAuction)
async def treasury_bill(tenor: Tenor) -> TreasuryBillAuction:
    rows, _ = await _load_tbills()
    match = [r for r in latest_per_tenor(rows, datetime.now(timezone.utc)) if r.tenor == tenor]
    if not match:
        raise HTTPException(status_code=404, detail=f"No recent BoZ auction for {tenor}")
    return match[0]


@router.get("/providers", response_model=list[ProviderPolicy])
def providers() -> list[ProviderPolicy]:
    return list(PROVIDER_POLICIES.values())


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/system")
async def system_status() -> dict:
    return {
        "api": True,
        "database": database_is_ready(),
        "openbb": await openbb_is_ready(),
    }


@router.get("/instruments")
def instruments() -> dict:
    if not database_is_ready():
        raise HTTPException(status_code=503, detail="Database is not ready")

    query = text(
        """
        SELECT symbol, name, asset_class, currency, exchange, country, provider
        FROM instruments
        ORDER BY asset_class, symbol
        """
    )
    with get_engine().connect() as connection:
        rows = [dict(row._mapping) for row in connection.execute(query)]

    return {"results": rows}


@router.get("/markets/overview")
def market_overview() -> dict:
    return {
        "status": "scaffold",
        "message": "Market data adapters are not connected yet.",
        "symbols": ["USDZMW", "LASI", "CECZ", "ZNCO", "BTCUSD", "BTCZMW", "COPPER"],
    }


@router.get("/openbb/coverage")
async def openbb_coverage() -> dict:
    try:
        return await get_openbb_coverage()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenBB unavailable: {exc}") from exc
