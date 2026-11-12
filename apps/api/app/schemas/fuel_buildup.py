from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, field_validator

from app.schemas.fuel import FuelProduct

BuildUpType = Literal["pump", "wholesale"]


class BuildUpComponent(BaseModel):
    """One row of an ERB build-up table, exactly as published. `amount` None means ERB printed "-"."""

    code: str
    label: str
    amount: Decimal | None
    currency: str | None
    unit: str | None
    note: str | None = None


class FuelBuildUp(BaseModel):
    product: FuelProduct
    effective_date: date  # first day of the pricing month
    publication_month: str  # YYYY-MM
    build_up_type: BuildUpType
    components: list[BuildUpComponent]  # in ERB's published order
    source: Literal["erb"] = "erb"
    source_url: str
    retrieved_at: AwareDatetime

    @field_validator("retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)
