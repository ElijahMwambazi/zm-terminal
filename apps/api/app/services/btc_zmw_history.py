"""Historical derived BTC/ZMW, computed on request from STORED data only (nothing derived is persisted).

    btc_zmw = btc_usd_daily_close * usd_zmw_daily_mid_avg

- btc_usd_daily_close: the stored completed Bitstamp BTC/USD `close` of a UTC calendar day (the candle's last trade; not an
  average, VWAP or live price).
- usd_zmw_daily_mid_avg: the arithmetic mean of all complete stored BoZ USD/ZMW `mid` observations (buy, mid and sell present,
  source boz) assigned to the Africa/Lusaka calendar date equal to the candle's UTC date. Every observation has equal weight.
  Date association is explicit: a BoZ observation belongs to `observed_at` converted to Africa/Lusaka (UTC+2, no DST); a candle
  belongs to the UTC date of its open. The two inputs are NOT simultaneous: the candle covers the whole UTC day and closes at
  24:00 UTC, while the BoZ observations occur earlier in the Zambia business day.
- A candle whose date has no eligible BoZ observation is omitted and counted (`btc_days_without_fx`): no forward-fill,
  backward-fill, interpolation, nearest-observation substitution, zero or null point. The first derived date therefore comes
  from the overlap of the stored histories (BoZ machine-readable history begins 2026-01-22), never from another FX source.
- Decimal at 40 significant digits, never rounded; rounding is presentation only. Not an official or executable BTC/ZMW price.
  This differs from the live quote (current Bitstamp last x the latest stored mid) and must not be used as a fallback for it.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext

from sqlalchemy.engine import Engine

from app.schemas.btc import BtcDailyCandle, BtcZmwDailyPoint, BtcZmwHistory
from app.services.boz_treasury import LUSAKA
from app.services.btc_store import fetch_btc_history
from app.services.fx_store import fetch_boz_mid_observations, fetch_first_boz_observation

MATH_PRECISION = 40


class InvalidHistoryInputError(ValueError):
    """A stored BTC close or BoZ mid is non-finite or non-positive; no point is derived from it."""


def _check_positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise InvalidHistoryInputError(f"Stored {name} is not a positive finite number")


def lusaka_date(observed_at: datetime) -> date:
    """The Africa/Lusaka calendar date of a BoZ observation (an aware timestamp is required)."""
    if observed_at.utcoffset() is None:
        raise InvalidHistoryInputError("BoZ observation timestamp is not timezone-aware")
    return observed_at.astimezone(LUSAKA).date()


def derive_btc_zmw_history(
    candles: list[BtcDailyCandle], fx_observations: list[tuple[datetime, Decimal]],
) -> tuple[list[BtcZmwDailyPoint], int]:
    """Pure derivation: (points ascending by date, number of candles omitted because the date has no BoZ observation)."""
    by_date: dict[date, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for observed_at, mid in fx_observations:
        _check_positive(mid, "USD/ZMW mid")
        by_date[lusaka_date(observed_at)].append((observed_at, mid))
    points: list[BtcZmwDailyPoint] = []
    omitted = 0
    for candle in sorted(candles, key=lambda c: c.candle_open_at):
        _check_positive(candle.close, "BTC/USD close")
        day = candle.candle_open_at.astimezone(timezone.utc).date()
        observations = by_date.get(day)
        if not observations:
            omitted += 1
            continue
        mids = [mid for _, mid in observations]
        stamps = [observed_at for observed_at, _ in observations]
        with localcontext() as context:
            context.prec = MATH_PRECISION
            mean = sum(mids, Decimal(0)) / len(mids)
            value = candle.close * mean
        points.append(BtcZmwDailyPoint(
            date=day, btc_zmw=value, btc_usd_close=candle.close, btc_candle_open_at=candle.candle_open_at,
            usd_zmw_daily_mid_avg=mean, fx_observation_count=len(mids),
            fx_first_observation=min(stamps), fx_last_observation=max(stamps)))
    return points, omitted


def _lusaka_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=LUSAKA).astimezone(timezone.utc)


def fetch_btc_zmw_history(range_: str, *, now: datetime | None = None, engine: Engine | None = None) -> BtcZmwHistory:
    """Read the stored BTC candles of the range and only the BoZ rows that can fall on their dates; derive. Read-only."""
    candles = fetch_btc_history(range_, now=now, engine=engine)
    first_boz = fetch_first_boz_observation(engine=engine)
    fx_stored_from = None if first_boz is None else lusaka_date(first_boz)
    if not candles:
        return BtcZmwHistory(range=range_, count=0, btc_days_without_fx=0, fx_stored_from=fx_stored_from, observations=[])
    first_day = candles[0].candle_open_at.astimezone(timezone.utc).date()
    last_day = candles[-1].candle_open_at.astimezone(timezone.utc).date()
    fx = fetch_boz_mid_observations(_lusaka_midnight_utc(first_day), _lusaka_midnight_utc(last_day + timedelta(days=1)), engine=engine)
    points, omitted = derive_btc_zmw_history(candles, fx)
    return BtcZmwHistory(range=range_, count=len(points), btc_days_without_fx=omitted, fx_stored_from=fx_stored_from, observations=points)
