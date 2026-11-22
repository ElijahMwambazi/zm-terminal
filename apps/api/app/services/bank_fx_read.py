"""Read-only access to stored BoZ commercial bank FX (migration 017). PostgreSQL only: no network, no parser, no importer.

Every statement is a SELECT on a schema-qualified `public.bank_fx_*` relation, on a connection that is READ ONLY at the database level.
The canonical snapshot per observation date comes from the view `public.bank_fx_canonical_snapshots` (node-first, revision-second; no
competing rule here). Child rates are fetched for exactly the selected snapshot_id, so rows of other nodes or revisions are never merged.
`latest` means the canonical snapshot of the newest observation date stored locally. Rates are the exact stored text; nothing is computed.
"""

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.db import get_engine
from app.schemas.bank_fx import BANK_FX_SLOTS, BankFxInstitutionRate, BankFxLatest, BankFxSlotSnapshot, BankFxSnapshotRead


class BankFxReadError(Exception):
    """Stored canonical data is internally inconsistent and cannot be represented safely."""


def _read_only(engine: Engine | None) -> Connection:
    return (engine or get_engine()).connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)


_SNAPSHOT_COLUMNS = (
    "snapshot_id, observation_date, source_record_id, source_revision_id, source_internal_nid, source_created, source_changed, "
    "source_revision_timestamp, source_title, source_rates_datetime_raw, source_rates_datetime, table_layout, retrieved_at, source_url, "
    "rights_status"
)
_LATEST = text(f"SELECT {_SNAPSHOT_COLUMNS} FROM public.bank_fx_canonical_snapshots ORDER BY observation_date DESC LIMIT 1")
_BY_DATE = text(f"SELECT {_SNAPSHOT_COLUMNS} FROM public.bank_fx_canonical_snapshots WHERE observation_date = :day")
_RATES = text(
    "SELECT slot, source_row_index, institution_name, institution_name_raw, is_market_average, interbank_buy_text, interbank_sell_text, "
    "retail_buy_text, retail_sell_text, retail_source_label FROM public.bank_fx_rates WHERE snapshot_id = :snapshot_id "
    "ORDER BY array_position(ARRAY['09:30','12:30','15:30'], slot), source_row_index"
)


def _rate(row) -> BankFxInstitutionRate:
    return BankFxInstitutionRate(
        source_row_index=row["source_row_index"], institution_name=row["institution_name"], institution_name_raw=row["institution_name_raw"],
        interbank_buy=row["interbank_buy_text"], interbank_sell=row["interbank_sell_text"],
        retail_buy=row["retail_buy_text"], retail_sell=row["retail_sell_text"])


def _slots(rows) -> list[BankFxSlotSnapshot]:
    """Slot by slot, preserving source row order; fails closed on anything the response cannot honestly represent."""
    slots = []
    for slot in BANK_FX_SLOTS:
        chosen = [r for r in rows if r["slot"] == slot]
        if not chosen:
            continue  # unpublished slot: absent, never invented
        labels = {r["retail_source_label"] for r in chosen}
        averages = [r for r in chosen if r["is_market_average"]]
        if len(labels) != 1 or len(averages) > 1:
            raise BankFxReadError("stored Bank FX slot is inconsistent")
        slots.append(BankFxSlotSnapshot(
            slot=slot, retail_source_label=labels.pop(), banks=[_rate(r) for r in chosen if not r["is_market_average"]],
            market_average=_rate(averages[0]) if averages else None))
    if len({r["slot"] for r in rows}) != len(slots):
        raise BankFxReadError("stored Bank FX row has an unknown slot")
    return slots


def _load(connection: Connection, statement, params: dict) -> BankFxSnapshotRead | None:
    snapshot = connection.execute(statement, params).mappings().one_or_none()
    if snapshot is None:
        return None
    rows = connection.execute(_RATES, {"snapshot_id": snapshot["snapshot_id"]}).mappings().all()
    values = {k: v for k, v in snapshot.items() if k != "snapshot_id"}
    values["source_record_id"] = str(values["source_record_id"])
    return BankFxSnapshotRead(**values, slots=_slots(rows))


def fetch_latest_bank_fx(*, engine: Engine | None = None) -> BankFxLatest:
    """Canonical snapshot of the newest observation date stored locally; {snapshot: None} when nothing is stored."""
    with _read_only(engine) as connection:
        return BankFxLatest(snapshot=_load(connection, _LATEST, {}))


def fetch_bank_fx_date(day: date, *, engine: Engine | None = None) -> BankFxSnapshotRead | None:
    """Canonical snapshot of exactly this observation date; None when absent (no fallback to any other date)."""
    with _read_only(engine) as connection:
        return _load(connection, _BY_DATE, {"day": day})
