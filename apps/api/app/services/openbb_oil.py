"""Brent/WTI daily spot prices via the OpenBB boundary (fred provider, /commodity/price/spot).

Validated against openbb-fred 2.0.0: SERIES_MAP wti=DCOILWTICO, brent=DCOILBRENTEU; rows are
{date, symbol, commodity, price, unit}. Rows are parsed strictly and fail loudly; nothing is
converted, filled, interpolated or averaged. Missing days are simply absent (FRED has no value).
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.config import get_settings
from app.schemas.oil import OIL_SERIES, OIL_SOURCE_UNIT, OIL_STALE_AFTER_DAYS, OilPoint

OIL_TIMEOUT_SECONDS = 20.0
OIL_LATEST_WINDOW_DAYS = 30
OIL_STALE_AFTER = timedelta(days=OIL_STALE_AFTER_DAYS)  # heuristic; defined in app.schemas.oil
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # the provider contract; no other ISO forms
SPOT_PATH = "/api/v1/fred/commodity/price/spot"


def parse_oil_payload(raw_text: str, benchmark: str, *, today: date | None = None) -> list[OilPoint]:
    """Validate one OpenBB response; returns points ascending by date. Empty results are valid (no data)."""
    series = OIL_SERIES[benchmark]
    try:
        payload = json.loads(raw_text, parse_float=Decimal)  # keep the published digits; never go through float
    except json.JSONDecodeError as exc:
        raise ValueError("OpenBB oil response is not JSON") from exc
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("OpenBB oil response has no results list")
    today = today or datetime.now(timezone.utc).date()
    points: dict[date, OilPoint] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Oil row is not an object")
        if row.get("symbol") != series.series_id:
            raise ValueError("Oil row has an unexpected series")
        if row.get("unit") != OIL_SOURCE_UNIT:
            raise ValueError("Oil row has an unexpected unit")
        raw_date, raw_price = row.get("date"), row.get("price")
        if not isinstance(raw_date, str) or not _DATE_PATTERN.fullmatch(raw_date) or isinstance(raw_price, bool) or not isinstance(raw_price, (Decimal, int)):
            raise ValueError("Oil row has a malformed date or price")
        try:
            observed = date.fromisoformat(raw_date)
            value = Decimal(raw_price)
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Oil row has a malformed date or price") from exc
        if not value.is_finite():
            raise ValueError("Oil price is not finite")
        if observed > today:
            raise ValueError("Oil observation is in the future")
        if observed in points:
            raise ValueError("Duplicate oil observation date")
        points[observed] = OilPoint(benchmark=benchmark, observed_date=observed, value=value)
    return [points[d] for d in sorted(points)]


async def fetch_oil_series(
    benchmark: str, start: date, end: date, *, client: httpx.AsyncClient | None = None,
) -> list[OilPoint]:
    """One OpenBB request. [] when OpenBB reports no data (204); httpx errors on transport/status problems; ValueError on bad data."""
    params = {"provider": "fred", "commodity": OIL_SERIES[benchmark].openbb_commodity,
              "start_date": start.isoformat(), "end_date": end.isoformat()}
    url = f"{get_settings().openbb_url}{SPOT_PATH}"
    if client is None:
        async with httpx.AsyncClient(timeout=OIL_TIMEOUT_SECONDS) as owned:
            response = await owned.get(url, params=params)
    else:
        response = await client.get(url, params=params)
    response.raise_for_status()
    if response.status_code == 204:
        # OpenBB maps an empty provider result (EmptyDataError) to 204 with no body: valid, no observations.
        return []
    return parse_oil_payload(response.text, benchmark)  # any other empty/malformed body still fails closed


def is_stale(observed: date, *, today: date) -> bool:
    return today - observed > OIL_STALE_AFTER
