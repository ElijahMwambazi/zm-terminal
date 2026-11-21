"""Stored-only ERB electricity generation (Energy Phase 1). Values are exactly the stored, published ERB figures (GWh).

Local/internal only: every response carries the selected publication's stored `rights_status` (currently `review_required`),
which is a workflow state, never permission to redistribute. A `not_reported` station cell is a printed dash and is never zero.
"""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, PlainSerializer, model_validator

# JSON decimals are plain-notation strings exactly as stored ('393.40', never 393.4 or '3.934E+2'); no rounding and no float.
PlainDecimal = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json")]

EnergyStationGroup = Literal["zesco_large_hydro", "zesco_mini_hydro", "zesco_diesel", "independent_power_producer"]
ENERGY_STATION_GROUPS: tuple[str, ...] = ("zesco_large_hydro", "zesco_mini_hydro", "zesco_diesel", "independent_power_producer")
EnergyValueState = Literal["reported", "not_reported"]


def _check_state(model) -> None:
    present = model.generation_gwh is not None and model.published_precision is not None
    absent = model.generation_gwh is None and model.published_precision is None
    if (model.value_state == "reported" and not present) or (model.value_state == "not_reported" and not absent):
        raise ValueError("value_state is inconsistent with generation_gwh / published_precision")


class EnergyGenerationObservation(BaseModel):
    """One station/year cell of a publication. `reported`: value and printed precision present. `not_reported`: both null."""

    station_group: EnergyStationGroup
    station_name: str
    observation_year: int
    value_state: EnergyValueState
    generation_gwh: PlainDecimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    published_precision: int | None = Field(default=None, ge=0)
    source_table: str
    source_pdf_page: int
    source_printed_page: int

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        _check_state(self)
        return self


class EnergyGenerationGroupTotal(BaseModel):
    """The published ERB Total-column value for a station group and year. Never a sum of station observations."""

    station_group: EnergyStationGroup
    observation_year: int
    total_gwh: PlainDecimal = Field(ge=0, allow_inf_nan=False)
    published_precision: int = Field(ge=0)
    source_table: str
    source_pdf_page: int
    source_printed_page: int


class EnergyGenerationSnapshot(BaseModel):
    """The newest observation year of ONE approved publication revision and its current import (stations and totals from the same import)."""

    publisher: str
    source_series: str
    publication_year: int
    revision_sequence: int
    title: str
    cover_date: str | None
    source_url: str
    rights_status: str
    observation_year: int
    stations: list[EnergyGenerationObservation]
    group_totals: list[EnergyGenerationGroupTotal]


class EnergyGenerationLatest(BaseModel):
    snapshot: EnergyGenerationSnapshot | None  # null: nothing stored yet


class EnergyGenerationHistoryPoint(BaseModel):
    """Canonical value for the year: the newest approved publication revision (current import) that contains the station and year."""

    observation_year: int
    value_state: EnergyValueState
    generation_gwh: PlainDecimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    published_precision: int | None = Field(default=None, ge=0)
    publication_year: int
    revision_sequence: int
    source_table: str
    source_pdf_page: int
    source_printed_page: int

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        _check_state(self)
        return self


class EnergyGenerationHistory(BaseModel):
    station_group: EnergyStationGroup
    station_name: str
    count: int
    rights_status: str  # of the newest publication supplying a point
    observations: list[EnergyGenerationHistoryPoint]


class EnergyGenerationGroupHistoryPoint(BaseModel):
    station_group: EnergyStationGroup
    observation_year: int
    total_gwh: PlainDecimal = Field(ge=0, allow_inf_nan=False)
    published_precision: int = Field(ge=0)
    publication_year: int
    revision_sequence: int
    source_table: str
    source_pdf_page: int
    source_printed_page: int


class EnergyGenerationGroupHistory(BaseModel):
    station_group: EnergyStationGroup
    count: int
    rights_status: str | None  # null only when nothing is stored for the group
    observations: list[EnergyGenerationGroupHistoryPoint]
