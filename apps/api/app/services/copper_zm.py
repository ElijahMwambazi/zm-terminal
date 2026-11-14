"""COPPER ZM: a derived monthly indicator computed on request from stored data (nothing derived is persisted).

    zmw_per_mt = copper_usd_per_mt * usd_zmw_observed_days_avg

- copper_usd_per_mt: the stored IMF PCPS Copper monthly average (series PCOPP; nominal USD per metric ton).
- usd_zmw_observed_days_avg: exactly the OIL ZM methodology and the same stored BoZ population (the FX query, the
  Africa/Lusaka day assignment, the per-day mean, the equal-day-weight mean, and the partial_source_window rule are shared
  with `oil_zm`, so the two derived indicators can never disagree about a month's FX component). Days without a stored
  observation are simply absent: nothing is interpolated, filled or synthesized; buy/sell are never used.
- A month is derived only when a stored Copper month and at least one stored FX day exist in the same calendar month
  (the overlap of the two stored histories, never extended from elsewhere).

Arithmetic is Decimal at 40 significant digits and unrounded; rounding is presentation only. Any finite stored value is
multiplied exactly (no positivity assumption). No upstream source is contacted. Not an IMF, LME or BoZ series, not a Zambia
copper price. Local/internal use: public, API, commercial and derived-public rights remain review_required.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.copper_zm import CopperZmMonth
from app.services.copper_store import is_stale, range_start
from app.services.oil_zm import _FX, MATH_PRECISION, FxMid, _plain, lusaka_date


@dataclass(frozen=True)
class CopperRow:
    period_start: date
    period_end: date
    value: Decimal
    source_definition: str
    source_url: str
    retrieved_at: datetime


def derive_copper_zm(copper: list[CopperRow], fx: list[FxMid], *, today: date) -> tuple[list[CopperZmMonth], int]:
    """Returns (derived months ascending, number of given Copper months with no FX day, which are omitted).

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

        months: list[CopperZmMonth] = []
        omitted = 0
        for month in sorted(copper, key=lambda c: c.period_start):
            days = by_month.get((month.period_start.year, month.period_start.month))
            if not days:
                omitted += 1
                continue
            daily_means = [sum((o.mid for o in day_obs), Decimal(0)) / len(day_obs) for _, day_obs in sorted(days.items())]
            monthly_fx = sum(daily_means, Decimal(0)) / len(daily_means)  # each observed day weighted equally
            observations = [o for day_obs in days.values() for o in day_obs]
            first = min(observations, key=lambda o: o.observed_at)
            last = max(observations, key=lambda o: o.observed_at)
            starts_inside_month = (window_first is not None and (window_first.year, window_first.month) == (
                month.period_start.year, month.period_start.month) and window_first.day > 1)
            months.append(CopperZmMonth(
                period_start=month.period_start, period_end=month.period_end,
                zmw_per_mt=_plain(month.value * monthly_fx), copper_usd_per_mt=month.value,
                usd_zmw_observed_days_avg=_plain(monthly_fx),
                fx_day_count=len(days), fx_observation_count=len(observations),
                fx_coverage="partial_source_window" if starts_inside_month else "observed_days",
                fx_first_date=min(days), fx_last_date=max(days),
                fx_first_observation=first.observed_at, fx_last_observation=last.observed_at,
                copper_source_definition=month.source_definition, copper_source_url=month.source_url,
                copper_retrieved_at=month.retrieved_at,
                stale=is_stale(month.period_start, today=today)))
    return months, omitted


_COPPER = text(
    """
    SELECT period_start, period_end, value, source_definition, source_url, retrieved_at FROM global_copper_prices
    WHERE provider = 'imf' AND benchmark = 'COPPER'
      AND (CAST(:since AS DATE) IS NULL OR period_start >= :since)
    ORDER BY period_start ASC
    """
)


def fetch_copper_zm(range_: str, *, today: date, engine: Engine | None = None) -> tuple[list[CopperZmMonth], int]:
    """Derive from PostgreSQL only. The range limits Copper months; FX is read in full for the source-window start."""
    since = range_start(range_, today)
    with (engine or get_engine()).connect() as connection:
        copper = [CopperRow(**row) for row in connection.execute(_COPPER, {"since": since}).mappings().all()]
        fx = [FxMid(**row) for row in connection.execute(_FX).mappings().all()]
    return derive_copper_zm(copper, fx, today=today)
