"""Normalization of published BoZ monthly rates to canonical ZMW per USD (pure, exact, no I/O).

Separate from parsing: the parser keeps the printed values; this step derives the canonical ones.

    observation month before 2013-01  -> published in old kwacha (ZMK), divisor 1000: canonical = published / 1000
    observation month 2013-01 onward  -> published in rebased kwacha (ZMW), divisor 1: canonical = published

The regime depends on the observation month only (BoZ rebasing, effective 1 January 2013), never on the magnitude of a value.
Arithmetic is exact: Decimal.scaleb under a context that traps any rounding, so a result is either exact or an error.
Division by 1000 may add decimal places (4,504.49 becomes 4.50449); nothing is rounded.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, Inexact, InvalidOperation, Rounded

from app.services.boz_annual_table import PublishedRate, published_currency_for

DIVISORS = {"ZMK": 1000, "ZMW": 1}
_EXACT = Context(prec=60, traps=[Inexact, Rounded, InvalidOperation])


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedRate:
    month: date
    published_buy: Decimal
    published_mid: Decimal
    published_sell: Decimal
    published_currency: str  # 'ZMK' | 'ZMW'
    normalization_divisor: int  # 1000 | 1
    buy: Decimal  # canonical ZMW per USD
    mid: Decimal
    sell: Decimal


def _divide(value: Decimal, divisor: int) -> Decimal:
    if divisor == 1:
        return value
    if divisor == 1000:
        return value.scaleb(-3, context=_EXACT)  # exact decimal shift
    raise NormalizationError("Unsupported normalization divisor")


def normalize(rate: PublishedRate, currency: str | None = None) -> NormalizedRate:
    """Canonical ZMW values for one published row. `currency`, if given, must agree with the month rule."""
    expected = published_currency_for(rate.month)
    if currency is not None and currency != expected:
        raise NormalizationError(f"Published currency {currency} contradicts the month rule for {rate.month:%Y-%m}")
    divisor = DIVISORS[expected]
    return NormalizedRate(
        month=rate.month, published_buy=rate.buy, published_mid=rate.mid, published_sell=rate.sell,
        published_currency=expected, normalization_divisor=divisor,
        buy=_divide(rate.buy, divisor), mid=_divide(rate.mid, divisor), sell=_divide(rate.sell, divisor),
    )
