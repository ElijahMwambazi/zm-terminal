"""Copper-Kwacha correlation: a descriptive statistic computed on request from stored data (nothing is persisted).

Variables: ONLY the two underlying inputs of the matched COPPER ZM months, `copper_usd_per_mt` and
`usd_zmw_observed_days_avg`. `zmw_per_mt` is never used: it is the mechanical product of the two.

Changes, not levels: for adjacent matched months t-1 -> t,  change = level_t / level_(t-1) - 1  (Decimal fractions).
A candidate change is eligible only if (precedence, one reason per pair so counts never double-count):
  1. gap:       the two months are not consecutive calendar months;
  2. partial:   either endpoint has fx_coverage = partial_source_window;
  3. nonpositive: any of the four endpoint levels is <= 0 (this is an analytical rule of this methodology only; the stored
     source values are never altered or rejected).
Sensitivity (beta = Sxy / Sxx, x = Copper change, y = USD/ZMW change) is computed on exactly the same eligible sample.
Pearson r uses Decimal at 40 significant digits (no float, no numpy). Zero variance means no coefficient (never a division by
zero). A full-sample r needs at least 6 eligible paired changes. Rolling windows of 6 or 12 consecutive eligible CHANGES
(7 or 13 consecutive valid monthly levels) never cross an ineligible change. No p-values, strength labels, R-squared or
regression. No upstream source is contacted: the matched months come from the same stored-data path as COPPER ZM.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from sqlalchemy.engine import Engine

from app.schemas.copper_kwacha import (
    CORRELATION_WINDOWS, MIN_FULL_SAMPLE, SMALL_SAMPLE_BELOW, CopperKwachaCorrelation, CopperKwachaSensitivity, FullSample,
    RollingPoint,
)
from app.schemas.copper_zm import CopperZmMonth
from app.services.copper_zm import fetch_copper_zm

MATH_PRECISION = 40


@dataclass(frozen=True)
class Change:
    end_month: date
    copper: Decimal  # fractional change
    fx: Decimal


def consecutive_months(earlier: date, later: date) -> bool:
    """True when `later` is the calendar month immediately after `earlier` (December -> January included)."""
    return (later.year * 12 + later.month) - (earlier.year * 12 + earlier.month) == 1


def is_constant(values: list[Decimal]) -> bool:
    """Exact Decimal equality of every value: zero variance is a property of the inputs, decided before any rounded mean
    (a repeating Decimal such as -2/3 at 40 digits averaged and divided can differ slightly from the identical inputs)."""
    return all(value == values[0] for value in values)


def pearson(xs: list[Decimal], ys: list[Decimal]) -> Decimal | None:
    """Pearson product-moment correlation in Decimal; None when fewer than two pairs or either series has zero variance."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    # Zero variance is known exactly when all analytical values are equal. Decide it here, before any mean: a repeating
    # Decimal (e.g. -2/3 rounded to 40 digits) averaged and divided can differ from the identical inputs by rounding and
    # would otherwise leave a spurious tiny variance (and r = 1).
    if is_constant(xs) or is_constant(ys):
        return None
    with localcontext() as context:
        context.prec = MATH_PRECISION
        n = Decimal(len(xs))
        mean_x, mean_y = sum(xs, Decimal(0)) / n, sum(ys, Decimal(0)) / n
        sxx = sum(((x - mean_x) ** 2 for x in xs), Decimal(0))
        syy = sum(((y - mean_y) ** 2 for y in ys), Decimal(0))
        if sxx == 0 or syy == 0:
            return None
        sxy = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)), Decimal(0))
        r = sxy / (sxx * syy).sqrt()
        return max(Decimal(-1), min(Decimal(1), r))  # rounding at 40 digits can never push r outside [-1, 1]


def sensitivity_beta(xs: list[Decimal], ys: list[Decimal]) -> Decimal | None:
    """Historical sensitivity of y to x: Sxy / Sxx on mean-centred Decimal values (the 1/N factors cancel).
    x = Copper USD fractional change, y = USD/ZMW fractional change. None when x has zero variance (never the y variance as
    a substitute denominator). A constant y with varying x is a valid beta of exactly 0."""
    if len(xs) != len(ys) or len(xs) < 2 or is_constant(xs):
        return None
    if is_constant(ys):
        return Decimal(0)  # covariance is exactly zero; do not let a rounded mean of y leave a spurious residue
    with localcontext() as context:
        context.prec = MATH_PRECISION
        n = Decimal(len(xs))
        mean_x, mean_y = sum(xs, Decimal(0)) / n, sum(ys, Decimal(0)) / n
        sxx = sum(((x - mean_x) ** 2 for x in xs), Decimal(0))
        if sxx == 0:
            return None
        return sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)), Decimal(0)) / sxx


def build_changes(months: list[CopperZmMonth]) -> tuple[list[Change | str], int]:
    """Walk adjacent matched months ascending. Returns one entry per candidate pair: a Change, or the exclusion reason
    ('gap' | 'partial' | 'nonpositive'), plus the number of candidates."""
    ordered = sorted(months, key=lambda m: m.period_start)
    entries: list[Change | str] = []
    with localcontext() as context:
        context.prec = MATH_PRECISION
        for previous, current in zip(ordered, ordered[1:]):
            if not consecutive_months(previous.period_start, current.period_start):
                entries.append("gap")
            elif "partial_source_window" in (previous.fx_coverage, current.fx_coverage):
                entries.append("partial")
            elif min(previous.copper_usd_per_mt, current.copper_usd_per_mt,
                     previous.usd_zmw_observed_days_avg, current.usd_zmw_observed_days_avg) <= 0:
                entries.append("nonpositive")
            else:
                entries.append(Change(
                    current.period_start,
                    current.copper_usd_per_mt / previous.copper_usd_per_mt - 1,
                    current.usd_zmw_observed_days_avg / previous.usd_zmw_observed_days_avg - 1))
    return entries, len(entries)


def rolling_correlation(entries: list[Change | str], window: int) -> list[RollingPoint]:
    """One point per eligible change that completes a run of `window` consecutive eligible changes (an exclusion breaks the run)."""
    points: list[RollingPoint] = []
    run: list[Change] = []
    for entry in entries:
        if isinstance(entry, str):
            run = []
            continue
        run.append(entry)
        if len(run) >= window:
            tail = run[-window:]
            points.append(RollingPoint(window_end_month=tail[-1].end_month,
                                       correlation=pearson([c.copper for c in tail], [c.fx for c in tail]), sample_size=window))
    return points


def full_sample(used: list[Change]) -> FullSample:
    n = len(used)
    first, last = (used[0].end_month, used[-1].end_month) if used else (None, None)
    small = n < SMALL_SAMPLE_BELOW
    if n < MIN_FULL_SAMPLE:
        return FullSample(status="insufficient_sample", sample_size=n, first_change_month=first, last_change_month=last, small_sample=small,
                          reason=f"At least {MIN_FULL_SAMPLE} eligible monthly changes are needed; {n} available.")
    r = pearson([c.copper for c in used], [c.fx for c in used])
    if r is None:
        return FullSample(status="zero_variance", sample_size=n, first_change_month=first, last_change_month=last, small_sample=small,
                          reason="One of the two change series is constant, so a correlation is undefined.")
    return FullSample(status="available", correlation=r, sample_size=n, first_change_month=first, last_change_month=last, small_sample=small)


@dataclass(frozen=True)
class Eligibility:
    """The one eligible-change sample shared by correlation and sensitivity (same rules, same exclusion accounting)."""

    entries: list[Change | str]
    used: list[Change]
    matched_months: int
    candidate_changes: int
    excluded_gap: int
    excluded_partial: int
    excluded_nonpositive: int


def eligibility(months: list[CopperZmMonth]) -> Eligibility:
    entries, candidates = build_changes(months)
    used = [e for e in entries if isinstance(e, Change)]
    count = lambda reason: sum(1 for e in entries if e == reason)  # noqa: E731
    return Eligibility(entries, used, len(months), candidates, count("gap"), count("partial"), count("nonpositive"))


def analyse(months: list[CopperZmMonth], range_: str, window: str) -> CopperKwachaCorrelation:
    sample = eligibility(months)
    entries, candidates, used = sample.entries, sample.candidate_changes, sample.used
    count = lambda reason: sum(1 for e in entries if e == reason)  # noqa: E731
    size = CORRELATION_WINDOWS[window]
    rolling = rolling_correlation(entries, size)
    return CopperKwachaCorrelation(
        range=range_, window=window, window_changes=size, matched_months=len(months), candidate_changes=candidates,
        changes_used=len(used), changes_excluded_gap=count("gap"), changes_excluded_partial=count("partial"),
        changes_excluded_nonpositive=count("nonpositive"), full_sample=full_sample(used), rolling=rolling,
        rolling_reason=None if rolling else f"Not enough consecutive eligible monthly changes yet: a {window} window needs {size} uninterrupted changes.")


def fetch_copper_kwacha_correlation(range_: str, window: str, *, today: date, engine: Engine | None = None) -> CopperKwachaCorrelation:
    """Stored data only: the same matched months as COPPER ZM for the range."""
    months, _ = fetch_copper_zm(range_, today=today, engine=engine)
    return analyse(months, range_, window)


def analyse_sensitivity(months: list[CopperZmMonth], range_: str) -> CopperKwachaSensitivity:
    """Full-sample historical sensitivity (beta) on exactly the sample the correlation uses."""
    sample = eligibility(months)
    used = sample.used
    n = len(used)
    first, last = (used[0].end_month, used[-1].end_month) if used else (None, None)
    common = dict(
        range=range_, matched_months=sample.matched_months, candidate_changes=sample.candidate_changes, changes_used=n,
        changes_excluded_gap=sample.excluded_gap, changes_excluded_partial=sample.excluded_partial,
        changes_excluded_nonpositive=sample.excluded_nonpositive, first_change_month=first, last_change_month=last, sample_size=n)
    if n < MIN_FULL_SAMPLE:
        return CopperKwachaSensitivity(**common, status="insufficient_sample", small_sample=n < SMALL_SAMPLE_BELOW,
                                       reason=f"At least {MIN_FULL_SAMPLE} eligible monthly changes are needed; {n} available.")
    beta = sensitivity_beta([c.copper for c in used], [c.fx for c in used])
    if beta is None:
        return CopperKwachaSensitivity(**common, status="zero_copper_variance", small_sample=n < SMALL_SAMPLE_BELOW,
                                       reason="The Copper change series is constant, so a sensitivity to Copper changes is undefined.")
    return CopperKwachaSensitivity(**common, status="available", beta=beta, small_sample=n < SMALL_SAMPLE_BELOW)


def fetch_copper_kwacha_sensitivity(range_: str, *, today: date, engine: Engine | None = None) -> CopperKwachaSensitivity:
    """Stored data only: the same matched months as COPPER ZM (and the correlation) for the range."""
    months, _ = fetch_copper_zm(range_, today=today, engine=engine)
    return analyse_sensitivity(months, range_)
