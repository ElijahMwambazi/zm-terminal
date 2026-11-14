from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, PlainSerializer, field_validator

BITSTAMP_API_URL = "https://www.bitstamp.net/api/"
BITSTAMP_TICKER_URL = "https://www.bitstamp.net/api/v2/ticker/btcusd/"
BITSTAMP_OHLC_URL = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
# Application heuristic, not a Bitstamp SLA: Bitcoin trades continuously, so a ticker older than this is flagged.
BTC_STALE_AFTER_SECONDS = 300

BtcRange = Literal["1M", "3M", "6M", "1Y", "3Y", "5Y", "ALL"]
BTC_RANGES: tuple[str, ...] = ("1M", "3M", "6M", "1Y", "3Y", "5Y", "ALL")

BTC_ZMW_METHODOLOGY_ID = "btc_zmw_live_v1"
BTC_ZMW_METHODOLOGY = (
    "btc_zmw = Bitstamp BTC/USD last trade x the latest stored Bank of Zambia USD/ZMW mid (Decimal, no rounding). "
    "The two inputs are observed at different times; each age is reported."
)
BTC_ZMW_NOTICE = (
    "Indicative derived conversion from Bitstamp BTC/USD last trade and the latest stored Bank of Zambia USD/ZMW mid. "
    "Not an executable BTC/ZMW market quote."
)


# JSON decimals are plain-notation strings exactly as held ('0.00000000', never '0E-8'); no rounding or float.
PlainDecimal = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json")]


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


class BtcUsdQuote(BaseModel):
    """Bitstamp BTC/USD spot. `last` is the latest executed trade; the *_24h fields are Bitstamp's rolling 24-hour values."""

    symbol: Literal["BTCUSD"] = "BTCUSD"
    base_currency: Literal["BTC"] = "BTC"
    quote_currency: Literal["USD"] = "USD"
    market: Literal["BTC/USD"] = "BTC/USD"
    market_type: Literal["SPOT"] = "SPOT"
    price_type: Literal["last_trade"] = "last_trade"
    last: PlainDecimal = Field(gt=0, allow_inf_nan=False)
    bid: PlainDecimal = Field(gt=0, allow_inf_nan=False)  # highest buy order
    ask: PlainDecimal = Field(gt=0, allow_inf_nan=False)
    high_24h: PlainDecimal = Field(gt=0, allow_inf_nan=False)  # rolling last 24 hours, not the UTC day
    low_24h: PlainDecimal = Field(gt=0, allow_inf_nan=False)
    vwap_24h: PlainDecimal = Field(gt=0, allow_inf_nan=False)
    volume_24h_btc: PlainDecimal = Field(ge=0, allow_inf_nan=False)
    observed_at: AwareDatetime  # Bitstamp ticker timestamp
    retrieved_at: AwareDatetime
    source: Literal["bitstamp"] = "bitstamp"
    source_url: str = BITSTAMP_TICKER_URL
    stale: bool
    stale_after_seconds: int = BTC_STALE_AFTER_SECONDS

    _normalize = field_validator("observed_at", "retrieved_at")(_utc)


class BtcDailyCandle(BaseModel):
    """One completed UTC-day candle exactly as stored: close is the last trade of that day, not an average."""

    candle_open_at: AwareDatetime
    open: PlainDecimal
    high: PlainDecimal
    low: PlainDecimal
    close: PlainDecimal
    volume_btc: PlainDecimal

    _normalize = field_validator("candle_open_at")(_utc)


class BtcUsdHistory(BaseModel):
    market: Literal["BTC/USD"] = "BTC/USD"
    provider: Literal["bitstamp"] = "bitstamp"
    interval: Literal["1d"] = "1d"
    range: BtcRange
    count: int
    observations: list[BtcDailyCandle]


class BtcZmwQuote(BaseModel):
    """DERIVED live conversion: never an exchange-traded BTC/ZMW price, dealer, P2P or mobile-money quote."""

    classification: Literal["derived"] = "derived"
    derived: Literal[True] = True
    symbol: Literal["BTCZMW"] = "BTCZMW"
    methodology_id: Literal["btc_zmw_live_v1"] = BTC_ZMW_METHODOLOGY_ID
    methodology: str = BTC_ZMW_METHODOLOGY
    notice: str = BTC_ZMW_NOTICE
    btc_zmw: PlainDecimal = Field(allow_inf_nan=False)
    currency: Literal["ZMW"] = "ZMW"
    unit: Literal["bitcoin"] = "bitcoin"
    btc_usd_last: PlainDecimal = Field(allow_inf_nan=False)
    btc_source: Literal["bitstamp"] = "bitstamp"
    btc_market: Literal["BTC/USD"] = "BTC/USD"
    btc_price_type: Literal["last_trade"] = "last_trade"
    btc_observed_at: AwareDatetime
    btc_source_url: str
    btc_age_seconds: int
    btc_stale: bool
    usd_zmw_mid: PlainDecimal = Field(allow_inf_nan=False)
    fx_source: Literal["boz"] = "boz"
    fx_pair: Literal["USDZMW"] = "USDZMW"
    fx_price: Literal["mid"] = "mid"
    fx_observed_at: AwareDatetime
    fx_source_url: str
    fx_age_seconds: int
    fx_stale: bool  # the unchanged BoZ rule (96 hours)
    derived_at: AwareDatetime
    derived_stale: bool  # btc_stale or fx_stale; the individual states are always reported too

    _normalize = field_validator("btc_observed_at", "fx_observed_at", "derived_at")(_utc)
