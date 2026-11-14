"""OIL ZM: a derived monthly indicator computed on request from stored data (nothing derived is persisted).

    zmw_per_bbl = brent_usd_per_bbl * usd_zmw_monthly_avg

- brent_usd_per_bbl: the stored World Bank Pink Sheet Brent monthly average for the calendar month.
- usd_zmw_monthly_avg: from the stored BoZ USD/ZMW `mid` observations (the same stored population the USD/ZMW history
  shows). Observations are assigned to Africa/Lusaka calendar dates, each day's observations are averaged, and the
  month's value is the arithmetic mean of those daily means. Days are weighted equally, so a day with more intraday
  observations does not count more than one with fewer. Weekends, holidays and any day without a stored observation
  are simply absent: nothing is interpolated, forward-filled or synthesized, and buy/sell are never used.
- A month is derived only when both a stored Brent average and at least one stored FX day exist. Availability is the
  overlap of the two stored histories (BoZ machine-readable history begins 2026-01-22), never extended from elsewhere.

All arithmetic is Decimal at 40 significant digits and is not rounded; rounding is presentation only. No upstream
source is contacted. This is not a World Bank or BoZ series and not a Zambia pump price.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.oil_zm import OilZmMonth
from app.services.boz_treasury import LUSAKA
from app.services.oil_store import is_stale, range_start

MATH_PRECISION = 40


@dataclass(frozen=True)
class FxMid:
    observed_at: datetime  # timezone-aware
    mid: Decimal


@dataclass(frozen=True)
class BrentMonth:
    period_start: date
    period_end: date
    value: Decimal
    publication_date: date
    source_url: str


def lusaka_date(moment: datetime) -> date:
    return moment.astimezone(LUSAKA).date()


def _plain(value: Decimal) -> Decimal:
    """Drop only trailing zeros left by the 40-digit context (2496.7750000 -> 2496.775); the numeric value is unchanged."""
    return Decimal(format(value.normalize(), "f"))


def derive_oil_zm(brent: list[BrentMonth], fx: list[FxMid], *, today: date) -> tuple[list[OilZmMonth], int]:
    """Returns (derived months ascending, number of given Brent months that have no FX day and are omitted).

    `fx` must be the full stored FX population (not just the requested range): its earliest day defines where the
    stored source window begins.
    """
    with localcontext() as context:
        context.prec = MATH_PRECISION
        by_month: dict[tuple[int, int], dict[date, list[FxMid]]] = {}
        for observation in fx:
            day = lusaka_date(observation.observed_at)
            by_month.setdefault((day.year, day.month), {}).setdefault(day, []).append(observation)
        window_first = min((lusaka_date(o.observed_at) for o in fx), default=None)

        months: list[OilZmMonth] = []
        omitted = 0
        for month in sorted(brent, key=lambda b: b.period_start):
            days = by_month.get((month.period_start.year, month.period_start.month))
            if not days:
                omitted += 1
                continue
            daily_means = [sum((o.mid for o in day_obs), Decimal(0)) / len(day_obs) for _, day_obs in sorted(days.items())]
            monthly_fx = sum(daily_means, Decimal(0)) / len(daily_means)
            observations = [o for day_obs in days.values() for o in day_obs]
            first = min(observations, key=lambda o: o.observed_at)
            last = max(observations, key=lambda o: o.observed_at)
            starts_inside_month = (window_first is not None and (window_first.year, window_first.month) == (
                month.period_start.year, month.period_start.month) and window_first.day > 1)
            months.append(OilZmMonth(
                period_start=month.period_start, period_end=month.period_end,
                zmw_per_bbl=_plain(month.value * monthly_fx), brent_usd_per_bbl=month.value, usd_zmw_monthly_avg=_plain(monthly_fx),
                fx_day_count=len(days), fx_observation_count=len(observations),
                fx_coverage="partial_source_window" if starts_inside_month else "observed_month",
                fx_first_date=min(days), fx_last_date=max(days),
                fx_first_observation=first.observed_at, fx_last_observation=last.observed_at,
                brent_source_url=month.source_url, brent_publication_date=month.publication_date,
                stale=is_stale(month.period_start, today=today)))
    return months, omitted


_BRENT = text(
    """
    SELECT period_start, period_end, value, publication_date, source_url FROM global_oil_prices
    WHERE provider = 'world_bank' AND benchmark = 'BRENT'
      AND (CAST(:since AS DATE) IS NULL OR period_start >= :since)
    ORDER BY period_start ASC
    """
)
# The same stored BoZ population as the USD/ZMW history (complete buy/mid/sell rows); only `mid` is used.
_FX = text(
    """
    SELECT observed_at, mid FROM fx_rates
    WHERE base_currency = 'USD' AND quote_currency = 'ZMW' AND source = 'boz'
      AND buy IS NOT NULL AND mid IS NOT NULL AND sell IS NOT NULL
    ORDER BY observed_at ASC
    """
)


def fetch_oil_zm(range_: str, *, today: date, engine: Engine | None = None) -> tuple[list[OilZmMonth], int]:
    """Derive from PostgreSQL only. The range limits Brent months; FX is read in full for the source-window start."""
    since = range_start(range_, today)
    with (engine or get_engine()).connect() as connection:
        brent = [BrentMonth(**row) for row in connection.execute(_BRENT, {"since": since}).mappings().all()]
        fx = [FxMid(**row) for row in connection.execute(_FX).mappings().all()]
    return derive_oil_zm(brent, fx, today=today)
