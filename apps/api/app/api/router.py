import asyncio
import logging
from typing import Annotated
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db import database_is_ready, get_engine
from app.schemas.fx import FxHistory, FxQuote
from app.schemas.agriculture import (
    AGRICULTURE_COMMODITIES, AgricultureCommodity, AgricultureHistory, AgricultureLatest, AgriculturePrice, AgricultureRange,
)
from app.schemas.fuel_buildup import BuildUpType, FuelBuildUp
from app.schemas.fuel import FUEL_PRODUCTS, FuelHistory, FuelLatest, FuelPrice, FuelProduct, FuelRange, PriceType
from app.schemas.bonds import (
    BOND_TENORS, BondTenor, GovernmentBondAuction, GovernmentBondsLatest, RejectedBondObservation,
    BondHistoryRange, GovernmentBondHistory,
)
from app.schemas.tbills import (
    TENORS, HistoryRange, RejectedTender, Tenor, TreasuryBillAuction, TreasuryBillHistory, TreasuryBillsLatest,
)
from app.services.boz import get_boz_fx_quote
from app.services.agriculture_store import fetch_agriculture_history, fetch_latest_agriculture
from app.services.bond_store import BondConflictError, fetch_bond_history, save_bond_auctions
from app.services.boz_bonds import get_boz_bonds, latest_per_tenor as latest_bonds, unverified_tenors
from app.services.boz_treasury import LUSAKA, get_boz_tbills, latest_per_tenor
from app.services.fuel_buildup_store import fetch_build_up
from app.services.fuel_store import fetch_fuel_history, fetch_latest_fuel
from app.services.fx_store import FxConflictError, HistoryRange, fetch_fx_history, save_fx_quote
from app.services.tbill_store import TbillConflictError, fetch_tbill_history, save_tbill_auctions
from app.services.provider_policy import PROVIDER_POLICIES, ProviderPolicy
from app.services.openbb import get_openbb_coverage, openbb_is_ready

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
type_name = lambda exc: type(exc).__name__


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


async def _load_tbills() -> tuple[list[TreasuryBillAuction], list[RejectedTender]]:
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
    rows, rejected = await _load_tbills()
    latest = latest_per_tenor(rows, datetime.now(timezone.utc), rejected)
    have = {r.tenor for r in latest}
    return TreasuryBillsLatest(
        tenors=latest, missing_tenors=[t for t in TENORS if t not in have], skipped_tenders=[r.tender_number or "unknown" for r in rejected]
    )


def _lusaka_today() -> date:
    return datetime.now(timezone.utc).astimezone(LUSAKA).date()


@router.get("/treasury-bills/history", response_model=TreasuryBillHistory)
async def treasury_bills_history(tenor: Tenor = "364D", range: HistoryRange = "1Y") -> TreasuryBillHistory:
    """Stored observations only: BoZ is never called here."""
    try:
        points = await asyncio.to_thread(fetch_tbill_history, int(tenor[:-1]), range, today=_lusaka_today())
    except Exception as exc:
        logger.warning("T-bill history read failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Stored history unavailable") from exc
    return TreasuryBillHistory(tenor=tenor, range=range, count=len(points), observations=points)


@router.get("/treasury-bills/{tenor}", response_model=TreasuryBillAuction)
async def treasury_bill(tenor: Tenor) -> TreasuryBillAuction:
    rows, rejected = await _load_tbills()
    match = [r for r in latest_per_tenor(rows, datetime.now(timezone.utc), rejected) if r.tenor == tenor]
    if not match:
        raise HTTPException(status_code=404, detail=f"No recent BoZ auction for {tenor}")
    return match[0]


async def _load_bonds() -> tuple[list[GovernmentBondAuction], list[RejectedBondObservation]]:
    try:
        rows, rejected = await get_boz_bonds()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Bank of Zambia request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Bank of Zambia data unavailable or invalid") from exc
    try:
        await asyncio.to_thread(save_bond_auctions, rows)
    except BondConflictError:
        logger.warning("Bond persistence conflict; live data returned unsaved")
    except Exception as exc:
        logger.warning("Bond persistence failed (%s); live data returned unsaved", type(exc).__name__)
    return rows, rejected


@router.get("/government-bonds/latest", response_model=GovernmentBondsLatest)
async def government_bonds_latest() -> GovernmentBondsLatest:
    rows, rejected = await _load_bonds()
    latest = latest_bonds(rows, rejected)
    have = {r.tenor for r in latest}
    skipped: list[str] = []
    for r in rejected:
        name = r.tender_number or "unknown"
        if name not in skipped:
            skipped.append(name)
    return GovernmentBondsLatest(
        tenors=latest, missing_tenors=[t for t in BOND_TENORS if t not in have],
        unverified_tenors=unverified_tenors(have, rejected), skipped_tenders=skipped,
    )


@router.get("/government-bonds/history", response_model=GovernmentBondHistory)
async def government_bonds_history(tenor: BondTenor = "10Y", range: BondHistoryRange = "1Y") -> GovernmentBondHistory:
    """Stored accepted observations only: BoZ is never called here."""
    try:
        points = await asyncio.to_thread(fetch_bond_history, int(tenor[:-1]), range, today=_lusaka_today())
    except Exception as exc:
        logger.warning("Bond history read failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Stored history unavailable") from exc
    return GovernmentBondHistory(tenor=tenor, range=range, count=len(points), observations=points)


@router.get("/government-bonds/{tenor}", response_model=GovernmentBondAuction)
async def government_bond(tenor: BondTenor) -> GovernmentBondAuction:
    rows, rejected = await _load_bonds()
    match = [r for r in latest_bonds(rows, rejected) if r.tenor == tenor]
    if not match:
        raise HTTPException(status_code=404, detail=f"No valid BoZ auction found for {tenor}")
    return match[0]


@router.get("/fuel/latest", response_model=FuelLatest)
async def fuel_latest() -> FuelLatest:
    """Newest stored retail price per product (ERB archive; BoZ-style live fetch is not used)."""
    try:
        prices = await asyncio.to_thread(fetch_latest_fuel)
    except Exception as exc:
        logger.warning("Fuel read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored fuel prices unavailable") from exc
    have = {p.product for p in prices}
    return FuelLatest(prices=prices, missing_products=[p for p in FUEL_PRODUCTS if p not in have])


@router.get("/fuel/history", response_model=FuelHistory)
async def fuel_history(product: FuelProduct = "PETROL", range: FuelRange = "1Y", type: PriceType = "retail") -> FuelHistory:
    """Stored observations only: ERB is never called here."""
    try:
        points = await asyncio.to_thread(fetch_fuel_history, product, type, range, today=_lusaka_today())
    except Exception as exc:
        logger.warning("Fuel history read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored fuel prices unavailable") from exc
    return FuelHistory(product=product, price_type=type, range=range, count=len(points), observations=points)


@router.get("/fuel/build-up", response_model=FuelBuildUp)
async def fuel_build_up(
    product: FuelProduct, type: BuildUpType = "pump",
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> FuelBuildUp:
    """Stored ERB build-up rows exactly as published; ERB is never called here."""
    try:
        found = await asyncio.to_thread(fetch_build_up, product, type, month)
    except Exception as exc:
        logger.warning("Fuel build-up read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored fuel prices unavailable") from exc
    if found is None:
        raise HTTPException(status_code=404, detail=f"No stored {type} build-up for {product}")
    return found


@router.get("/fuel/{product}", response_model=FuelPrice)
async def fuel_product(product: FuelProduct) -> FuelPrice:
    try:
        prices = await asyncio.to_thread(fetch_latest_fuel)
    except Exception as exc:
        logger.warning("Fuel read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored fuel prices unavailable") from exc
    match = [p for p in prices if p.product == product]
    if not match:
        raise HTTPException(status_code=404, detail=f"No stored retail price for {product}")
    return match[0]


@router.get("/agriculture/latest", response_model=AgricultureLatest)
async def agriculture_latest() -> AgricultureLatest:
    """Newest stored Ministry national-average grain price per commodity."""
    try:
        prices = await asyncio.to_thread(fetch_latest_agriculture)
    except Exception as exc:
        logger.warning("Agriculture read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored agriculture prices unavailable") from exc
    have = {p.commodity for p in prices}
    return AgricultureLatest(
        prices=prices,
        missing_commodities=[c for c in AGRICULTURE_COMMODITIES if c not in have],
    )


@router.get("/agriculture/history", response_model=AgricultureHistory)
async def agriculture_history(
    commodity: AgricultureCommodity = "MAIZE", range: AgricultureRange = "1Y",
) -> AgricultureHistory:
    """Stored Ministry observations only; the Ministry is never called here."""
    try:
        points = await asyncio.to_thread(fetch_agriculture_history, commodity, range, today=_lusaka_today())
    except Exception as exc:
        logger.warning("Agriculture history read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored agriculture prices unavailable") from exc
    return AgricultureHistory(
        commodity=commodity, range=range, count=len(points), observations=points,
    )


@router.get("/agriculture/{commodity}", response_model=AgriculturePrice)
async def agriculture_commodity(commodity: AgricultureCommodity) -> AgriculturePrice:
    try:
        prices = await asyncio.to_thread(fetch_latest_agriculture)
    except Exception as exc:
        logger.warning("Agriculture read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored agriculture prices unavailable") from exc
    match = [p for p in prices if p.commodity == commodity]
    if not match:
        raise HTTPException(status_code=404, detail=f"No stored Ministry national-average price for {commodity}")
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
