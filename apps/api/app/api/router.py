from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db import database_is_ready, get_engine
from app.services.openbb import get_openbb_coverage, openbb_is_ready

router = APIRouter(prefix="/api/v1")


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
