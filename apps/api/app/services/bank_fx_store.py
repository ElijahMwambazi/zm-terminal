"""PostgreSQL persistence for BoZ commercial bank FX snapshots (migration 017): insert-only and conflict-aware. No reads, no schema changes.

Identity of a snapshot is (source_record_id, source_revision_id). A new identity is inserted with its rate rows. An existing identity is a
no-op only if its complete stored SOURCE representation equals the supplied one: observation date, node ids and timestamps, title, the raw
`commercial_bank_rates_date` string, layout, and every rate row (source row order, slot presence, raw and normalised institution text,
market-average flag, source label, and each rate's exact text AND numeric value). Anything else for the same revision, even a formatting-only
change such as 9.9 -> 9.9000, raises BankFxConflictError and rolls the whole batch back; stored rows are never updated. A different revision id
is always a new snapshot, even with identical numbers. `retrieved_at` and `source_url` are local retrieval metadata (request location), not source
content: they are excluded from the comparison and the first stored values are kept. Every relation is schema-qualified (public.bank_fx_*).
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.db import get_engine
from app.services.bank_fx_models import BankFxRate, BankFxSnapshot

_SNAPSHOT_COLUMNS = (
    "id, observation_date, source_internal_nid, source_created, source_changed, source_revision_timestamp, source_title, "
    "source_rates_datetime_raw, source_rates_datetime, table_layout, rights_status"
)


class BankFxConflictError(Exception):
    """A stored node revision differs from the supplied one. Names only date/node/revision, never rate values."""


@dataclass(frozen=True)
class BankFxSaveResult:
    inserted: int
    already_present: int


def _snapshot_content(s: BankFxSnapshot) -> tuple:
    return (s.observation_date, s.source_internal_nid, s.source_created, s.source_changed, s.source_revision_timestamp, s.source_title,
            s.source_rates_datetime_raw, s.source_rates_datetime, s.table_layout, s.rights_status)


def _rate_content(r: BankFxRate) -> tuple:
    return (r.source_row_index, r.slot, r.institution_name_raw, r.institution_name, r.is_market_average,
            r.interbank_buy, r.interbank_buy_text, r.interbank_sell, r.interbank_sell_text,
            r.retail_buy, r.retail_buy_text, r.retail_sell, r.retail_sell_text, r.retail_source_label)


_RATE_COLUMNS = (
    "source_row_index, slot, institution_name_raw, institution_name, is_market_average, interbank_buy, interbank_buy_text, "
    "interbank_sell, interbank_sell_text, retail_buy, retail_buy_text, retail_sell, retail_sell_text, retail_source_label"
)


def _insert(connection: Connection, snapshot: BankFxSnapshot) -> bool:
    """Insert one snapshot and its rates; False when the (node, revision) identity already exists (nothing written)."""
    snapshot_id = connection.execute(text(
        "INSERT INTO public.bank_fx_snapshots (observation_date, source_record_id, source_revision_id, source_internal_nid, source_created, "
        "source_changed, source_revision_timestamp, source_title, source_rates_datetime_raw, source_rates_datetime, table_layout, "
        "retrieved_at, source_url, rights_status) VALUES (:observation_date, :source_record_id, :source_revision_id, :source_internal_nid, "
        ":source_created, :source_changed, :source_revision_timestamp, :source_title, :source_rates_datetime_raw, :source_rates_datetime, "
        ":table_layout, :retrieved_at, :source_url, :rights_status) "
        "ON CONFLICT (source_record_id, source_revision_id) DO NOTHING RETURNING id"
    ), {
        "observation_date": snapshot.observation_date, "source_record_id": snapshot.source_record_id,
        "source_revision_id": snapshot.source_revision_id, "source_internal_nid": snapshot.source_internal_nid,
        "source_created": snapshot.source_created, "source_changed": snapshot.source_changed,
        "source_revision_timestamp": snapshot.source_revision_timestamp, "source_title": snapshot.source_title,
        "source_rates_datetime_raw": snapshot.source_rates_datetime_raw, "source_rates_datetime": snapshot.source_rates_datetime,
        "table_layout": snapshot.table_layout, "retrieved_at": snapshot.retrieved_at, "source_url": snapshot.source_url,
        "rights_status": snapshot.rights_status,
    }).scalar()
    if snapshot_id is None:
        return False
    connection.execute(text(
        "INSERT INTO public.bank_fx_rates (snapshot_id, source_row_index, slot, institution_name_raw, institution_name, is_market_average, "
        "interbank_buy, interbank_buy_text, interbank_sell, interbank_sell_text, retail_buy, retail_buy_text, retail_sell, retail_sell_text, "
        "retail_source_label) VALUES (:snapshot_id, :source_row_index, :slot, :institution_name_raw, :institution_name, :is_market_average, "
        ":interbank_buy, :interbank_buy_text, :interbank_sell, :interbank_sell_text, :retail_buy, :retail_buy_text, :retail_sell, "
        ":retail_sell_text, :retail_source_label)"
    ), [{"snapshot_id": snapshot_id, **{name: getattr(r, name) for name in _RATE_COLUMNS.replace(" ", "").split(",")}} for r in snapshot.rates])
    return True


def _check_same(connection: Connection, snapshot: BankFxSnapshot) -> None:
    row = connection.execute(text(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM public.bank_fx_snapshots WHERE source_record_id = :record AND source_revision_id = :revision"
    ), {"record": snapshot.source_record_id, "revision": snapshot.source_revision_id}).one()
    stored_rates = connection.execute(text(
        f"SELECT {_RATE_COLUMNS} FROM public.bank_fx_rates WHERE snapshot_id = :id ORDER BY source_row_index, slot"
    ), {"id": row[0]}).all()
    expected = sorted(snapshot.rates, key=lambda r: (r.source_row_index, r.slot))
    if tuple(row[1:]) != _snapshot_content(snapshot) or [tuple(r) for r in stored_rates] != [_rate_content(r) for r in expected]:
        raise BankFxConflictError(
            f"stored Bank FX node {snapshot.source_record_id} revision {snapshot.source_revision_id} "
            f"(observation date {snapshot.observation_date}) differs from the supplied source content"
        )


def save_bank_fx_snapshots(snapshots: list[BankFxSnapshot], *, engine: Engine | None = None) -> BankFxSaveResult:
    """One transaction for the whole batch: new revisions are inserted, identical ones are no-ops, any difference rolls everything back."""
    inserted = already = 0
    with (engine or get_engine()).begin() as connection:
        for snapshot in snapshots:
            if _insert(connection, snapshot):
                inserted += 1
            else:
                _check_same(connection, snapshot)
                already += 1
    return BankFxSaveResult(inserted=inserted, already_present=already)
