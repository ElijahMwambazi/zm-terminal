"""Result types for the ERB 2025 Statistical Bulletin generation-by-station parser (pure data, no I/O, no storage).

Observations are the 390 station/year cells of Tables 13-16. Table totals, footing residuals and the Table 12 cross-table check
are diagnostics returned separately: a total is never a station and nothing here is repaired or reconciled.
"""

from dataclasses import dataclass
from decimal import Decimal

REPORTED = "reported"  # a printed number, including a printed numeric zero
NOT_REPORTED = "not_reported"  # a printed dash: not reported in the source, never zero

LARGE_HYDRO = "zesco_large_hydro"
MINI_HYDRO = "zesco_mini_hydro"
DIESEL = "zesco_diesel"
IPP = "independent_power_producer"

FOOTING_COMPUTED = "computed"  # every station value was reported
FOOTING_PARTIAL = "reported_values_only"  # some stations are dashes; the sum covers the reported stations only


class ErbGenerationError(Exception):
    """The source, a table or a value failed verification. Messages name tables, years and columns, never PDF text."""


@dataclass(frozen=True)
class GenerationObservation:
    observation_year: int
    station_name: str  # exactly the source column identity (no cross-table merging)
    station_group: str
    generation_gwh: Decimal | None  # None only when value_state is NOT_REPORTED
    value_state: str
    source_table: str  # "Table 13" .. "Table 16"
    source_pdf_page: int
    source_printed_page: int
    source_url: str
    source_publication: str
    source_sha256: str
    publication_year: int


@dataclass(frozen=True)
class TableTotal:
    """The published total column of one table-year. Diagnostic only; never an observation."""
    source_table: str
    observation_year: int
    published_total_gwh: Decimal
    source_pdf_page: int


@dataclass(frozen=True)
class FootingDiagnostic:
    """Published total versus the sum of the printed station values of one table-year. Nothing is repaired."""
    source_table: str
    observation_year: int
    status: str  # FOOTING_COMPUTED or FOOTING_PARTIAL
    stations_not_reported: int
    reported_sum_gwh: Decimal  # sum of reported stations only; a dash is NOT counted as zero
    published_total_gwh: Decimal
    residual_gwh: Decimal  # published_total_gwh - reported_sum_gwh (positive: the printed total exceeds the printed values)


@dataclass(frozen=True)
class Table12Diagnostic:
    """2025 cross-table check. Both published figures are preserved; the CEC relationship is deliberately not asserted here."""
    table16_total_gwh: Decimal  # Table 16 "Total GWh", 2025
    table12_ipp_gwh: Decimal  # Table 12 "IPPs", 2025
    table16_minus_table12_ipp_gwh: Decimal
    table12_printed_total_gwh: Decimal
    table12_rows_sum_gwh: Decimal  # the four printed Table 12 generator rows
    table12_footing_residual_gwh: Decimal  # printed total - rows sum (positive: the printed total exceeds the rows)


@dataclass(frozen=True)
class ParseResult:
    source_url: str
    source_sha256: str
    source_publication: str
    publication_year: int
    observations: tuple[GenerationObservation, ...]
    table_totals: tuple[TableTotal, ...]
    footing: tuple[FootingDiagnostic, ...]
    table12: Table12Diagnostic
