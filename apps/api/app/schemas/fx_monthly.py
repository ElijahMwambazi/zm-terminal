from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.btc import PlainDecimal

FxMonthlyRange = Literal["1Y", "3Y", "5Y", "ALL"]
# Count-based windows over STORED months (not approximated years): the most recent 12 / 36 / 60 stored months; ALL = every stored month.
FX_MONTHLY_RANGE_MONTHS: dict[str, int | None] = {"1Y": 12, "3Y": 36, "5Y": 60, "ALL": None}


class FxMonthlyPoint(BaseModel):
    """One official BoZ monthly average (ZMW per USD). `month` is YYYY-MM: a calendar month, not an observation timestamp."""

    month: Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]
    buy: PlainDecimal = Field(gt=0, allow_inf_nan=False)
    mid: PlainDecimal = Field(gt=0, allow_inf_nan=False)  # the BoZ-published mid, never derived from buy and sell
    sell: PlainDecimal = Field(gt=0, allow_inf_nan=False)
    source: Literal["boz"] = "boz"
    source_series: str
    source_publication: str
    source_url: str
    source_page: int
    published_precision: int


class FxMonthlyHistory(BaseModel):
    symbol: Literal["USDZMW"] = "USDZMW"
    range: FxMonthlyRange
    count: int
    first_month: str | None
    last_month: str | None
    observations: list[FxMonthlyPoint]
