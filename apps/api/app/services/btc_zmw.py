"""Live derived BTC/ZMW: Bitstamp BTC/USD last trade x the latest STORED Bank of Zambia USD/ZMW mid.

    btc_zmw = btc_usd_last * usd_zmw_mid

- The BTC leg is a live Bitstamp ticker supplied by the caller. The FX leg is the newest complete stored BoZ row (see
  `fx_store.fetch_latest_fx_mid`); this module never contacts BoZ, and nothing derived is persisted.
- Bitcoin trades continuously but BoZ publishes only at business-day slots, so the two legs have different ages. Both are
  reported separately; `fx_stale` is the unchanged BoZ rule (`boz.is_stale`, 96 hours) and `btc_stale` the ticker heuristic.
  `derived_stale` is only their OR. A weekend normally shows a current BTC leg and an older, not-yet-stale FX leg.
- Decimal at 40 significant digits, never rounded; rounding is presentation only. Not an executable BTC/ZMW quote.
"""

from datetime import datetime, timezone
from decimal import Decimal, localcontext

from app.schemas.btc import BtcUsdQuote, BtcZmwQuote
from app.services.boz import is_stale as fx_is_stale
from app.services.fx_store import LatestFxMid

MATH_PRECISION = 40


def _age_seconds(observed_at: datetime, now: datetime) -> int:
    return max(0, int((now - observed_at).total_seconds()))  # a skew within tolerance is reported as age 0


def derive_btc_zmw(btc: BtcUsdQuote, fx: LatestFxMid, *, now: datetime | None = None) -> BtcZmwQuote:
    """Raises ValueError (from the unchanged BoZ freshness helper) if the stored FX observation is in the future."""
    derived_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    fx_stale = fx_is_stale(fx.observed_at, derived_at)  # BoZ semantics reused unchanged
    with localcontext() as context:
        context.prec = MATH_PRECISION
        value = btc.last * fx.mid
    return BtcZmwQuote(
        btc_zmw=value, btc_usd_last=btc.last, btc_observed_at=btc.observed_at, btc_source_url=btc.source_url,
        btc_age_seconds=_age_seconds(btc.observed_at, derived_at), btc_stale=btc.stale,
        usd_zmw_mid=fx.mid, fx_observed_at=fx.observed_at, fx_source_url=fx.source_url,
        fx_age_seconds=_age_seconds(fx.observed_at, derived_at), fx_stale=fx_stale,
        derived_at=derived_at, derived_stale=btc.stale or fx_stale,
    )
