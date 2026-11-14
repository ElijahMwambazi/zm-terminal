import asyncio
import logging
from typing import Annotated
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db import database_is_ready, get_engine
from app.schemas.btc import BtcRange, BtcUsdHistory, BtcUsdQuote, BtcZmwQuote
from app.schemas.fx import FxHistory, FxQuote
from app.schemas.agriculture import (
    AGRICULTURE_COMMODITIES, AgricultureCommodity, AgricultureHistory, AgricultureLatest, AgriculturePrice, AgricultureRange,
)
from app.schemas.oil import OIL_BENCHMARKS, OilBenchmark, OilHistory, OilLatest, OilObservation, OilRange
from app.schemas.oil_zm import OilZmHistory, OilZmMonth
from app.schemas.yield_curve import YieldCurve
from app.schemas.yield_spreads import YieldSpreads
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
from app.services.bitstamp_btc import get_bitstamp_btc_quote
from app.services.btc_store import fetch_btc_history
from app.services.btc_zmw import derive_btc_zmw
from app.services.agriculture_store import fetch_agriculture_history, fetch_latest_agriculture
from app.services.bond_store import BondConflictError, fetch_bond_history, save_bond_auctions
from app.services.boz_bonds import get_boz_bonds, latest_per_tenor as latest_bonds, unverified_tenors
from app.services.boz_treasury import LUSAKA, get_boz_tbills, latest_per_tenor
from app.services.fuel_buildup_store import fetch_build_up
from app.services.fuel_store import fetch_fuel_history, fetch_latest_fuel
from app.services.fx_store import FxConflictError, HistoryRange, fetch_fx_history, fetch_latest_fx_mid, save_fx_quote
from app.services.tbill_store import TbillConflictError, fetch_tbill_history, save_tbill_auctions
from app.services.oil_store import fetch_latest_oil, fetch_oil_history
from app.services.oil_zm import fetch_oil_zm
from app.services.yield_curve import fetch_yield_curve
from app.services.yield_spreads import derive_yield_spreads
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


async def _load_yield_curve(as_of: date) -> YieldCurve:
    try:
        curve = await asyncio.to_thread(fetch_yield_curve, as_of)
    except Exception as exc:
        logger.warning("Yield curve read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored auction data unavailable") from exc
    if curve is None:
        raise HTTPException(status_code=404, detail=f"No stored accepted BoZ auction observation on or before {as_of.isoformat()}")
    return curve


@router.get("/yield-curve/latest", response_model=YieldCurve)
async def yield_curve_latest() -> YieldCurve:
    """Derived snapshot as of Africa/Lusaka today: latest stored accepted observation per tenor (mixed auction vintages)."""
    return await _load_yield_curve(_lusaka_today())


def _parse_curve_date(value: str) -> date:
    try:
        as_of = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date must be a valid calendar date (YYYY-MM-DD)") from exc
    if as_of > _lusaka_today():
        raise HTTPException(status_code=422, detail="date must not be in the future")
    return as_of


@router.get("/yield-curve/as-of", response_model=YieldCurve)
async def yield_curve_as_of(
    date_: Annotated[str, Query(alias="date", pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")],
) -> YieldCurve:
    """Historical stored snapshot: each tenor's latest stored accepted observation on or before the (past or present) date."""
    return await _load_yield_curve(_parse_curve_date(date_))


@router.get("/yield-curve/spreads/latest", response_model=YieldSpreads)
async def yield_spreads_latest() -> YieldSpreads:
    """Four published-tenor spreads (bp) computed from the latest derived curve snapshot (mixed auction vintages possible)."""
    return derive_yield_spreads(await _load_yield_curve(_lusaka_today()))


@router.get("/yield-curve/spreads/as-of", response_model=YieldSpreads)
async def yield_spreads_as_of(
    date_: Annotated[str, Query(alias="date", pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")],
) -> YieldSpreads:
    """Spreads computed from exactly the curve snapshot /yield-curve/as-of returns for that date (no second selection)."""
    return derive_yield_spreads(await _load_yield_curve(_parse_curve_date(date_)))


async def _live_btc_quote() -> BtcUsdQuote:
    try:
        return await get_bitstamp_btc_quote()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Bitstamp request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Bitstamp BTC/USD failed (%s)", type_name(exc))
        raise HTTPException(status_code=502, detail="Bitstamp data unavailable or invalid") from exc


@router.get("/btc/usd/latest", response_model=BtcUsdQuote)
async def btc_usd_latest() -> BtcUsdQuote:
    """Live Bitstamp BTC/USD last trade (upstream request; nothing is persisted)."""
    return await _live_btc_quote()


@router.get("/btc/usd/history", response_model=BtcUsdHistory)
async def btc_usd_history(range: BtcRange = "1Y") -> BtcUsdHistory:
    """Stored completed daily BTC/USD candles only; never contacts Bitstamp."""
    try:
        candles = await asyncio.to_thread(fetch_btc_history, range, now=datetime.now(timezone.utc))
    except Exception as exc:
        logger.warning("BTC/USD history read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored BTC/USD history unavailable") from exc
    return BtcUsdHistory(range=range, count=len(candles), observations=candles)


@router.get("/btc/zmw/latest", response_model=BtcZmwQuote)
async def btc_zmw_latest() -> BtcZmwQuote:
    """Indicative derived BTC/ZMW: live Bitstamp last x the latest STORED BoZ USD/ZMW mid (no BoZ request, nothing persisted)."""
    try:
        fx = await asyncio.to_thread(fetch_latest_fx_mid)
    except Exception as exc:
        logger.warning("Stored USD/ZMW read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored USD/ZMW observations unavailable") from exc
    if fx is None:
        raise HTTPException(status_code=404, detail="No stored Bank of Zambia USD/ZMW observation to convert with")
    btc = await _live_btc_quote()
    try:
        return derive_btc_zmw(btc, fx)
    except ValueError as exc:
        logger.warning("BTC/ZMW derivation rejected the stored USD/ZMW observation (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored USD/ZMW observation is invalid") from exc


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


@router.get("/oil/latest", response_model=OilLatest)
async def oil_latest() -> OilLatest:
    """Newest stored World Bank Pink Sheet month per benchmark (monthly averages, USD/bbl); the World Bank is never called here."""
    try:
        observations = await asyncio.to_thread(fetch_latest_oil, today=_lusaka_today())
    except Exception as exc:
        logger.warning("Oil read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored oil benchmarks unavailable") from exc
    have = {o.benchmark for o in observations}
    return OilLatest(observations=observations, missing_benchmarks=[b for b in OIL_BENCHMARKS if b not in have])


@router.get("/oil/history", response_model=OilHistory)
async def oil_history(benchmark: OilBenchmark = "BRENT", range: OilRange = "1Y") -> OilHistory:
    """Stored months only, ascending; the World Bank is never called here."""
    try:
        points = await asyncio.to_thread(fetch_oil_history, benchmark, range, today=_lusaka_today())
    except Exception as exc:
        logger.warning("Oil history read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored oil benchmarks unavailable") from exc
    return OilHistory(benchmark=benchmark, range=range, count=len(points), observations=points)


@router.get("/oil/zm/latest", response_model=OilZmMonth)
async def oil_zm_latest() -> OilZmMonth:
    """Newest month with both a stored World Bank Brent average and stored BoZ USD/ZMW; derived on request, never persisted."""
    try:
        months, _ = await asyncio.to_thread(fetch_oil_zm, "ALL", today=_lusaka_today())
    except Exception as exc:
        logger.warning("OIL ZM read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored oil and FX data unavailable") from exc
    if not months:
        raise HTTPException(status_code=404, detail="No month has both stored World Bank Brent and stored BoZ USD/ZMW data")
    return months[-1]


@router.get("/oil/zm/history", response_model=OilZmHistory)
async def oil_zm_history(range: OilRange = "1Y") -> OilZmHistory:
    """Derived months ascending; months without FX are omitted (and counted), never filled. Stored data only."""
    try:
        months, omitted = await asyncio.to_thread(fetch_oil_zm, range, today=_lusaka_today())
    except Exception as exc:
        logger.warning("OIL ZM history read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored oil and FX data unavailable") from exc
    return OilZmHistory(range=range, count=len(months), brent_months_without_fx=omitted, observations=months)


@router.get("/oil/{benchmark}", response_model=OilObservation)
async def oil_benchmark(benchmark: OilBenchmark) -> OilObservation:
    try:
        observations = await asyncio.to_thread(fetch_latest_oil, today=_lusaka_today())
    except Exception as exc:
        logger.warning("Oil read failed (%s)", type_name(exc))
        raise HTTPException(status_code=503, detail="Stored oil benchmarks unavailable") from exc
    match = [o for o in observations if o.benchmark == benchmark]
    if not match:
        raise HTTPException(status_code=404, detail=f"No stored World Bank monthly price for {benchmark}")
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
