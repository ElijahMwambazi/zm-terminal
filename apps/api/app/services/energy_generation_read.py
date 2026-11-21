"""Read-only access to stored ERB electricity generation (migration 014 tables and views). PostgreSQL only: no network, no parser, no importer.

Every statement is a SELECT on a schema-qualified `public.energy_*` relation (never the caller's search_path), run on a connection that is
READ ONLY at the database level (so even a coding mistake cannot write). Selection semantics (design sections 10 and 12):
  * latest: the newest APPROVED publication (publication_year, then revision_sequence), its single CURRENT import, that import's newest
    observation year; stations and published totals come from that one import, never from a mix of vintages;
  * station history: public.energy_generation_canonical (newest approved publication wins per station/year, with omission fallback);
  * group totals: the same rule applied to energy_generation_group_totals. import_sequence never ranks different publications.
Values are the stored published figures; nothing is summed, repaired or interpolated.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.db import get_engine
from app.schemas.energy import (
    EnergyGenerationGroupHistory, EnergyGenerationGroupHistoryPoint, EnergyGenerationGroupTotal, EnergyGenerationHistory,
    EnergyGenerationHistoryPoint, EnergyGenerationLatest, EnergyGenerationObservation, EnergyGenerationSnapshot,
)

ERB_SOURCE_SERIES = "erb_statistical_bulletin"
_GROUP_ORDER = "array_position(ARRAY['zesco_large_hydro','zesco_mini_hydro','zesco_diesel','independent_power_producer'], {col})"


def _read_only(engine: Engine | None) -> Connection:
    return (engine or get_engine()).connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)


_LATEST_PUBLICATION = text(
    "SELECT v.publication_id, v.import_id, v.source_series, v.publication_year, v.revision_sequence, v.title, v.source_url, "
    "v.rights_status, p.publisher, p.cover_date "
    "FROM public.energy_publication_current_imports v JOIN public.energy_publications p ON p.id = v.publication_id "
    "WHERE v.publication_status = 'approved' AND v.source_series = :series "
    "ORDER BY v.publication_year DESC, v.revision_sequence DESC LIMIT 1"
)
_LATEST_YEAR = text("SELECT max(observation_year) FROM public.energy_generation_observations WHERE import_id = :import_id")
_STATIONS = text(
    "SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh, o.published_precision, "
    "o.source_table, o.source_pdf_page, o.source_printed_page "
    "FROM public.energy_generation_observations o JOIN public.energy_source_stations s ON s.id = o.source_station_id "
    "WHERE o.import_id = :import_id AND o.observation_year = :year "
    f"ORDER BY {_GROUP_ORDER.format(col='s.station_group')}, s.station_name"
)
_TOTALS = text(
    "SELECT t.station_group, t.observation_year, t.total_gwh, t.published_precision, t.source_table, t.source_pdf_page, "
    "t.source_printed_page FROM public.energy_generation_group_totals t WHERE t.import_id = :import_id AND t.observation_year = :year "
    f"ORDER BY {_GROUP_ORDER.format(col='t.station_group')}"
)


def fetch_latest_generation(*, engine: Engine | None = None) -> EnergyGenerationLatest:
    with _read_only(engine) as c:
        pub = c.execute(_LATEST_PUBLICATION, {"series": ERB_SOURCE_SERIES}).mappings().one_or_none()
        year = None if pub is None else c.execute(_LATEST_YEAR, {"import_id": pub["import_id"]}).scalar()
        if pub is None or year is None:
            return EnergyGenerationLatest(snapshot=None)
        params = {"import_id": pub["import_id"], "year": year}
        stations = c.execute(_STATIONS, params).mappings().all()
        totals = c.execute(_TOTALS, params).mappings().all()
    return EnergyGenerationLatest(snapshot=EnergyGenerationSnapshot(
        publisher=pub["publisher"], source_series=pub["source_series"], publication_year=pub["publication_year"],
        revision_sequence=pub["revision_sequence"], title=pub["title"], cover_date=pub["cover_date"], source_url=pub["source_url"],
        rights_status=pub["rights_status"], observation_year=year,
        stations=[EnergyGenerationObservation(**r) for r in stations], group_totals=[EnergyGenerationGroupTotal(**r) for r in totals]))


_STATION_HISTORY = text(
    "SELECT c.observation_year, c.value_state, c.generation_gwh, c.published_precision, c.publication_year, c.revision_sequence, "
    "c.source_table, c.source_pdf_page, c.source_printed_page, p.rights_status "
    "FROM public.energy_generation_canonical c JOIN public.energy_publications p ON p.id = c.publication_id "
    "WHERE c.source_series = :series AND c.station_group = :group AND c.station_name = :name ORDER BY c.observation_year"
)


def fetch_station_history(station_group: str, station_name: str, *, engine: Engine | None = None) -> EnergyGenerationHistory | None:
    """Canonical annual history of the exact (station_group, station_name) identity; None when nothing is stored for it."""
    with _read_only(engine) as c:
        rows = c.execute(_STATION_HISTORY, {"series": ERB_SOURCE_SERIES, "group": station_group, "name": station_name}).mappings().all()
    if not rows:
        return None
    newest = max(rows, key=lambda r: (r["publication_year"], r["revision_sequence"]))
    points = [EnergyGenerationHistoryPoint(**{k: v for k, v in r.items() if k != "rights_status"}) for r in rows]
    return EnergyGenerationHistory(station_group=station_group, station_name=station_name, count=len(points),
                                   rights_status=newest["rights_status"], observations=points)


_GROUP_HISTORY = text(
    "SELECT DISTINCT ON (t.observation_year) t.station_group, t.observation_year, t.total_gwh, t.published_precision, "
    "e.publication_year, e.revision_sequence, t.source_table, t.source_pdf_page, t.source_printed_page, e.rights_status "
    "FROM public.energy_generation_group_totals t "
    "JOIN public.energy_publication_current_imports e ON e.import_id = t.import_id "
    "WHERE e.publication_status = 'approved' AND e.source_series = :series AND t.station_group = :group "
    "ORDER BY t.observation_year, e.publication_year DESC, e.revision_sequence DESC"
)


def fetch_group_history(station_group: str, *, engine: Engine | None = None) -> EnergyGenerationGroupHistory:
    """Canonical PUBLISHED group totals per year (newest approved publication revision wins; omission falls back to an older one)."""
    with _read_only(engine) as c:
        rows = c.execute(_GROUP_HISTORY, {"series": ERB_SOURCE_SERIES, "group": station_group}).mappings().all()
    newest = max(rows, key=lambda r: (r["publication_year"], r["revision_sequence"]), default=None)
    return EnergyGenerationGroupHistory(
        station_group=station_group, count=len(rows), rights_status=None if newest is None else newest["rights_status"],
        observations=[EnergyGenerationGroupHistoryPoint(**{k: v for k, v in r.items() if k != "rights_status"}) for r in rows])
