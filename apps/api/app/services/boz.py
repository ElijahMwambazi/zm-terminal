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
# Source-selection policy (not a validity rule). The BoZ website's own client selects the
# "latest" USD rates from rows whose UTC hour is 7, 10 or 13 (09:30/12:30/15:30 Africa/Lusaka;
# `STANDARD_UTC_HOURS` in its mapLatestUsdRates). Its series chart applies no hour filter, so
# this policy mirrors BoZ's headline-rate selection rather than its series. Only the hour is
# compared, exactly as the site does. A row outside these hours is valid but *not selected*;
# it is never treated as malformed. Unlike the site, there is no fallback to other hours when
# no slot row exists: selection fails closed instead of publishing an unselected row.
OFFICIAL_SLOT_UTC_HOURS = frozenset({7, 10, 13})
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


def parse_boz_observations(payload: object, *, allow_empty: bool = False) -> dict[datetime, tuple[Decimal, Decimal, Decimal]]:
    """Validate every row; return {observed_at: (buy, mid, sell)}.

    Identical duplicates collapse; conflicting rates at any timestamp fail. An empty
    array is only accepted when `allow_empty` (a bounded historical window may be empty).
    """
    if not isinstance(payload, list) or (not payload and not allow_empty):
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
    return observations


def select_official(
    observations: dict[datetime, tuple[Decimal, Decimal, Decimal]],
) -> tuple[dict[datetime, tuple[Decimal, Decimal, Decimal]], list[datetime]]:
    """Split validated observations into (selected, not-selected timestamps) by slot hour."""
    selected = {t: r for t, r in observations.items() if t.hour in OFFICIAL_SLOT_UTC_HOURS}
    return selected, sorted(t for t in observations if t not in selected)


def parse_boz_quote(payload: object, *, retrieved_at: datetime) -> FxQuote:
    """Validate all rows; never hide malformed data by falling back to an older row.

    Every row is validated, but only rows in an official slot (see OFFICIAL_SLOT_UTC_HOURS)
    are candidates for the latest quote.
    """
    observations, _ = select_official(parse_boz_observations(payload))
    if not observations:
        raise ValueError("No observation in an official publication slot")
    observed_at = max(observations)
    buy, mid, sell = observations[observed_at]
    return FxQuote(
        buy=buy, mid=mid, sell=sell,
        observed_at=observed_at, retrieved_at=retrieved_at,
        stale=is_stale(observed_at, retrieved_at),
    )


async def fetch_boz_payload(client: httpx.AsyncClient, params: dict[str, str] | None = None) -> object:
    """One GET against the configured BoZ view; returns the decoded JSON."""
    response = await client.get(
        get_settings().boz_usd_rates_url,
        params=params,
        timeout=BOZ_TIMEOUT_SECONDS,
        headers={"User-Agent": BOZ_USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("Expected a JSON response")
    return response.json()


async def get_boz_fx_quote(*, client: httpx.AsyncClient | None = None) -> FxQuote:
    """The optional client is a transport seam for offline tests."""
    if client is None:
        async with httpx.AsyncClient() as owned_client:
            return await get_boz_fx_quote(client=owned_client)
    payload = await fetch_boz_payload(client)
    return parse_boz_quote(payload, retrieved_at=datetime.now(timezone.utc))
