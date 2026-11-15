"""Assemble the single canonical monthly USD/ZMW observation set from the two vetted source families (pure, no I/O).

    2010-01 .. 2013-12  BoZ Annual Reports, Table 13 (published old/rebased kwacha, normalized to canonical ZMW)
    2014-01 .. latest   BoZ Fortnightly Statistics, Table 02 (existing Phase-1 chain)

The database identity includes `source_series`, so it could technically hold one month twice under two series. This module is
the write-side gate: before anything is persisted there must be exactly one observation per month, each family may supply only
its approved window, and an overlap is reported (month and series ids, never rates) and never resolved silently. The 2014 Annual
Report is validation-only and can never enter the set.
"""

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from app.services import boz_annual_source as annual
from app.services import boz_annual_table as tab
from app.services.boz_monthly_table import SOURCE_SERIES as FORTNIGHTLY_SERIES
from app.services.boz_monthly_source import FIRST_COVERED_MONTH as FORTNIGHTLY_FIRST
from app.services.fx_monthly_combine import MonthlyConflictError, MonthlyObservation, find_gaps
from app.services.fx_monthly_normalize import normalize

ANNUAL_FIRST = annual.FIRST_COVERED_MONTH  # 2010-01
ANNUAL_LAST = annual.LAST_COVERED_MONTH  # 2013-12
MAX_SHOWN = 5


class CanonicalSetError(Exception):
    """The candidate set violates the canonical rules (out-of-window source, cross-series overlap). Months and ids only."""


def series_of(o: MonthlyObservation) -> str:
    return o.source_series or FORTNIGHTLY_SERIES


def build_annual_observations(
    loaded: Sequence[tuple[annual.AnnualReport, tab.ParsedAnnualTable]],
) -> list[MonthlyObservation]:
    """Canonical observations from canonical reports only, each limited to its persistence window and normalized exactly."""
    out: list[MonthlyObservation] = []
    for report, table in loaded:
        if report.role != "canonical":
            raise CanonicalSetError(f"{report.filename}: a validation report cannot supply canonical observations")
        for rate in annual.window_rows(report, table):
            n = normalize(rate)
            out.append(MonthlyObservation(
                month=n.month, buy=n.buy, mid=n.mid, sell=n.sell, publication_title=report.title, source_url=report.url,
                source_page=report.pdf_page, source_series=tab.ANNUAL_SOURCE_SERIES, source_table=tab.ANNUAL_SOURCE_TABLE,
                published_precision=tab.ANNUAL_PUBLISHED_PRECISION, published_buy=n.published_buy,
                published_mid=n.published_mid, published_sell=n.published_sell, published_currency=n.published_currency,
                normalization_divisor=n.normalization_divisor,
            ))
    return out


def check_annual_overlaps(loaded: Sequence[tuple[annual.AnnualReport, tab.ParsedAnnualTable]]) -> int:
    """Every month (2010-01 onward) printed by more than one loaded report must carry identical PRINTED values: the comparison
    is on published values, so normalization can never hide a disagreement. Returns the number of repeat occurrences compared."""
    first: dict[date, tuple[str, tab.PublishedRate]] = {}
    overlaps, conflicts = 0, []
    for report, table in loaded:
        for rate in table.rows:
            if rate.month < ANNUAL_FIRST:
                continue
            seen = first.get(rate.month)
            if seen is None:
                first[rate.month] = (report.key, rate)
                continue
            overlaps += 1
            if (seen[1].buy, seen[1].mid, seen[1].sell) != (rate.buy, rate.mid, rate.sell):
                conflicts.append(f"{rate.month:%Y-%m} ({seen[0]} vs {report.key})")
    if conflicts:
        raise MonthlyConflictError(len(conflicts), sorted(conflicts))
    return overlaps


@dataclass(frozen=True)
class Assembled:
    observations: tuple[MonthlyObservation, ...]  # requested range, ascending, one per month
    annual_count: int
    fortnightly_count: int
    gaps: tuple[date, ...]
    ordering_exceptions_used: int


def assemble(
    annual_obs: Sequence[MonthlyObservation], fortnightly_obs: Sequence[MonthlyObservation], start: date, end: date,
) -> Assembled:
    for o in annual_obs:
        if series_of(o) != tab.ANNUAL_SOURCE_SERIES or not ANNUAL_FIRST <= o.month <= ANNUAL_LAST:
            raise CanonicalSetError(f"{o.month:%Y-%m}: Annual Report observation outside its approved window "
                                    f"({ANNUAL_FIRST:%Y-%m} to {ANNUAL_LAST:%Y-%m})")
    for o in fortnightly_obs:
        if series_of(o) != FORTNIGHTLY_SERIES or o.month < FORTNIGHTLY_FIRST:
            raise CanonicalSetError(f"{o.month:%Y-%m}: Fortnightly observation outside its approved window "
                                    f"(from {FORTNIGHTLY_FIRST:%Y-%m})")
    by_month: dict[date, list[str]] = {}
    for o in (*annual_obs, *fortnightly_obs):
        if start <= o.month <= end:
            by_month.setdefault(o.month, []).append(series_of(o))
    clashes = [f"{m:%Y-%m} ({' vs '.join(sorted(ids))})" for m, ids in sorted(by_month.items()) if len(ids) > 1]
    if clashes:
        shown = ", ".join(clashes[:MAX_SHOWN]) + (f" ... and {len(clashes) - MAX_SHOWN} more" if len(clashes) > MAX_SHOWN else "")
        raise CanonicalSetError(f"{len(clashes)} month(s) have more than one canonical observation: {shown}; nothing was written")
    chosen = sorted((o for o in (*annual_obs, *fortnightly_obs) if start <= o.month <= end), key=lambda o: o.month)
    exception_months = {e.month for e in tab.ORDERING_EXCEPTIONS}
    return Assembled(
        observations=tuple(chosen),
        annual_count=sum(1 for o in chosen if series_of(o) == tab.ANNUAL_SOURCE_SERIES),
        fortnightly_count=sum(1 for o in chosen if series_of(o) == FORTNIGHTLY_SERIES),
        gaps=tuple(find_gaps(chosen, start, end)),
        ordering_exceptions_used=sum(1 for o in chosen if series_of(o) == tab.ANNUAL_SOURCE_SERIES and o.month in exception_months),
    )
