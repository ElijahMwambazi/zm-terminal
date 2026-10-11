"""BoZ USD observations, independent of OpenBB and database availability."""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser

import httpx

from app.config import get_settings
from app.schemas.fx import FxQuote


BOZ_TIMEOUT_SECONDS = 10.0
BOZ_USER_AGENT = "ZM-Terminal/0.1 (Bank of Zambia USDZMW adapter)"
# Application heuristic, not a BoZ SLA or a holiday/publication calendar.
FX_STALE_AFTER = timedelta(hours=96)
_RATE_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)"
)


class _TimeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.timestamp: str | None = None
        self.opened = False
        self.closed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "time":
            raise ValueError("Unexpected timestamp HTML element")
        values = [value for key, value in attrs if key == "datetime"]
        if self.opened or len(values) != 1 or not values[0]:
            raise ValueError("Expected one time element with a datetime attribute")
        self.opened = True
        self.timestamp = values[0]

    def handle_endtag(self, tag: str) -> None:
        if tag != "time" or not self.opened or self.closed:
            raise ValueError("Malformed timestamp HTML")
        self.closed = True


def _parse_timestamp(fragment: object) -> datetime:
    if not isinstance(fragment, str):
        raise ValueError("Missing timestamp HTML")
    parser = _TimeParser()
    parser.feed(fragment)
    parser.close()
    if not parser.closed or not parser.timestamp:
        raise ValueError("Incomplete timestamp HTML")
    if not _UTC_TIMESTAMP_PATTERN.fullmatch(parser.timestamp):
        raise ValueError("Expected an explicit UTC timestamp")
    return datetime.fromisoformat(parser.timestamp.replace("Z", "+00:00"))


def _parse_rate(value: object) -> Decimal:
    # Require the verified string representation; never go through float.
    if not isinstance(value, str) or not _RATE_PATTERN.fullmatch(value):
        raise ValueError("Invalid published decimal rate")
    rate = Decimal(value)
    if not rate.is_finite() or rate <= 0:
        raise ValueError("Rate must be finite and positive")
    return rate


def is_stale(observed_at: datetime, now: datetime) -> bool:
    """Older than 96 hours is stale; unknown or future observations fail."""
    for value in (observed_at, now):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Freshness requires timezone-aware timestamps")
    age = now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
    if age < timedelta(0):
        raise ValueError("Observation is in the future")
    return age > FX_STALE_AFTER


def parse_boz_quote(payload: object, *, retrieved_at: datetime) -> FxQuote:
    """Validate all rows; never hide malformed data by falling back to an older row.

    Select across all hours, unlike the website's undocumented hour filter.
    Identical duplicates collapse; conflicting rates at any timestamp fail.
    """
    if not isinstance(payload, list) or not payload:
        raise ValueError("Expected a non-empty observation array")
    observations: dict[datetime, tuple[Decimal, Decimal, Decimal]] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("Expected an observation object")
        observed_at = _parse_timestamp(row.get("time"))
        rates = tuple(_parse_rate(row.get(field)) for field in ("buying", "mid_rate", "selling"))
        if observed_at in observations:
            previous = observations[observed_at]
            if previous != rates:
                raise ValueError("Conflicting observations at the same timestamp")
            # Stable even if equal decimals have different trailing-zero precision.
            rates = min(previous, rates, key=lambda values: tuple(map(str, values)))
        observations[observed_at] = rates

    observed_at = max(observations)
    buy, mid, sell = observations[observed_at]
    return FxQuote(
        buy=buy, mid=mid, sell=sell,
        observed_at=observed_at, retrieved_at=retrieved_at,
        stale=is_stale(observed_at, retrieved_at),
    )


async def get_boz_fx_quote(*, client: httpx.AsyncClient | None = None) -> FxQuote:
    """The optional client is a transport seam for offline tests."""
    if client is None:
        async with httpx.AsyncClient() as owned_client:
            return await get_boz_fx_quote(client=owned_client)
    response = await client.get(
        get_settings().boz_usd_rates_url,
        timeout=BOZ_TIMEOUT_SECONDS,
        headers={"User-Agent": BOZ_USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("Expected a JSON response")
    payload = response.json()
    return parse_boz_quote(payload, retrieved_at=datetime.now(timezone.utc))
