from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.btc import PlainDecimal
from app.schemas.copper import CopperRange

CorrelationWindow = Literal["6M", "12M"]
CORRELATION_WINDOWS: dict[str, int] = {"6M": 6, "12M": 12}  # number of consecutive eligible monthly CHANGES, not levels
MIN_FULL_SAMPLE = 6
SMALL_SAMPLE_BELOW = 12
CORR_METHODOLOGY_ID = "copper_kwacha_corr_v1"
CORR_METHODOLOGY = (
    "Pearson correlation between consecutive month-over-month fractional changes in the IMF PCPS Copper USD benchmark and the "
    "stored BoZ USD/ZMW observed-days average. Pairs crossing missing months, the partial FX source-start month or "
    "nonpositive analytical inputs are excluded. Rolling windows require consecutive eligible monthly changes.")
CORR_NOTICE = ("Descriptive analytical statistic. Correlation does not establish causation or predictive power. "
               "The sample reflects only the overlap of stored IMF Copper and stored BoZ FX history.")
CORR_BASIS_NOTE = ("Calculated from month-over-month changes (value / previous month - 1), not from price levels, so that "
                   "trending levels are not read as short-run co-movement. This does not remove every statistical issue.")
SMALL_SAMPLE_WARNING = "Small sample: this coefficient is based on fewer than 12 monthly changes and may be unstable."

FullSampleStatus = Literal["available", "insufficient_sample", "zero_variance"]


class FullSample(BaseModel):
    status: FullSampleStatus
    correlation: PlainDecimal | None = Field(default=None, allow_inf_nan=False)  # Pearson r in [-1, 1]
    sample_size: int  # eligible paired monthly changes used (or available, when insufficient)
    first_change_month: date | None  # the later month of the first / last eligible change
    last_change_month: date | None
    small_sample: bool  # sample_size < 12 (descriptive caution, not a significance statement)
    reason: str | None = None


class RollingPoint(BaseModel):
    window_end_month: date  # the month ending the window's final change
    correlation: PlainDecimal | None = Field(default=None, allow_inf_nan=False)  # null only when a window has zero variance
    sample_size: int  # always the window size


class CopperKwachaCorrelation(BaseModel):
    range: CopperRange
    window: CorrelationWindow
    window_changes: int
    methodology_id: str = CORR_METHODOLOGY_ID
    methodology: str = CORR_METHODOLOGY
    basis_note: str = CORR_BASIS_NOTE
    notice: str = CORR_NOTICE
    variables: list[str] = ["copper_usd_per_mt", "usd_zmw_observed_days_avg"]
    matched_months: int
    candidate_changes: int  # adjacent pairs of the matched level sequence
    changes_used: int
    changes_excluded_gap: int  # not consecutive calendar months
    changes_excluded_partial: int  # either endpoint has fx_coverage partial_source_window (and no gap)
    changes_excluded_nonpositive: int  # any of the four levels <= 0 (and no gap, not partial)
    full_sample: FullSample
    rolling: list[RollingPoint]
    rolling_reason: str | None = None


SENS_METHODOLOGY_ID = "copper_kwacha_beta_v1"
SENS_METHODOLOGY = (
    "Historical sample sensitivity: the covariance of consecutive month-over-month fractional changes in the stored BoZ USD/ZMW "
    "observed-days average with those of the IMF PCPS Copper USD benchmark, divided by the variance of the Copper changes "
    "(beta = Sxy / Sxx). Computed on exactly the eligible monthly-change sample used by the correlation: pairs crossing missing "
    "months, the partial FX source-start month or nonpositive analytical inputs are excluded.")
SENS_NOTICE = ("Descriptive analytical statistic for the stored sample, not a causal estimate, forecast, hedge ratio or trading signal. "
               "The sample reflects only the overlap of stored IMF Copper and stored BoZ FX history.")
SENS_UNITS = "Fractional USD/ZMW change per unit fractional Copper USD change (dependent: USD/ZMW; explanatory: Copper USD)."
SENS_SIGN_NOTE = ("USD/ZMW is quoted as kwacha per US dollar. A positive USD/ZMW change means the kwacha depreciated against "
                  "the dollar; a negative change means it appreciated.")
SENS_SMALL_SAMPLE_WARNING = "Small sample: this sensitivity estimate is based on fewer than 12 monthly changes and may be unstable."

SensitivityStatus = Literal["available", "insufficient_sample", "zero_copper_variance"]


class CopperKwachaSensitivity(BaseModel):
    range: CopperRange
    methodology_id: str = SENS_METHODOLOGY_ID
    methodology: str = SENS_METHODOLOGY
    notice: str = SENS_NOTICE
    units: str = SENS_UNITS
    sign_note: str = SENS_SIGN_NOTE
    dependent: str = "usd_zmw_observed_days_avg"
    explanatory: str = "copper_usd_per_mt"
    matched_months: int
    candidate_changes: int
    changes_used: int
    changes_excluded_gap: int
    changes_excluded_partial: int
    changes_excluded_nonpositive: int
    first_change_month: date | None
    last_change_month: date | None
    sample_size: int
    status: SensitivityStatus
    beta: PlainDecimal | None = Field(default=None, allow_inf_nan=False)
    small_sample: bool  # sample_size < 12 (descriptive caution, not a significance statement)
    reason: str | None = None
