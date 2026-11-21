"""Structural storage baseline for the derived Copper analytics regression tests.

New source-backed tables require an explicit entry explaining their persistence role.
There is no last-migration-number restriction: alterations/indexes on approved tables
are allowed. Unregistered tables/views fail regardless of their spelling or purpose.
All migration DDL runs in a unique, rolled-back schema of the disposable test DB.
"""

from pathlib import Path
from uuid import uuid4

from sqlalchemy import text


APPROVED_STORAGE = {
    "instruments": "instrument catalog",
    "market_prices": "source market observations",
    "fx_rates": "source intraday BoZ observations",
    "macro_observations": "source macro observations",
    "watchlist_items": "user watchlist",
    "treasury_bill_auctions": "source BoZ auctions",
    "government_bond_auctions": "source BoZ auctions",
    "zambia_fuel_prices": "source ERB prices",
    "zambia_fuel_build_up_items": "source ERB build-up items",
    "zambia_agricultural_prices": "source ministry prices",
    "global_oil_prices": "source monthly oil prices",
    "btc_usd_daily": "source Bitstamp candles",
    "global_copper_prices": "source IMF Copper observations, not derived analytics",
    "fx_monthly_averages": "source BoZ published monthly observations, not derived analytics",
    "energy_publication_candidates": "administrative source-review log, no observations",
    "energy_publications": "source ERB accepted publication revisions",
    "energy_generation_imports": "local import revisions of accepted publications",
    "energy_source_stations": "source ERB station identities as printed",
    "energy_generation_observations": "source ERB station/year observations, not derived analytics",
    "energy_generation_group_totals": "source ERB published group totals, not derived analytics",
    "bank_fx_snapshots": "source BoZ commercial bank rate node revisions, immutable, not derived analytics",
    "bank_fx_rates": "source BoZ commercial bank rates per snapshot, slot and institution, not derived analytics",
    "bank_fx_canonical_snapshots": "read-only selection view over source snapshots (one per observation date), stores nothing",
    "energy_publication_current_imports": "read-only view mapping publications to current imports, stores nothing",
    "energy_generation_canonical": "read-only selection view over source observations, stores nothing",
}


def schema_objects(connection, schema="public"):
    """Tables, views, sequences and indexes: endpoint calls must create none."""
    return set(connection.execute(text(
        "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :schema"
    ), {"schema": schema}).all())


def assert_no_derived_storage(engine, init: Path, *, extra_sql=()):
    schema = "storage_contract_" + uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # Include schema-qualified DDL too: an explicit public.new_table must
            # not escape inspection merely because search_path is isolated.
            catalog = text(
                "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') "
                "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"
            )
            before = set(connection.execute(catalog).all())
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            # 015 creates/grants an application role: use a throwaway name (rolled back) so the real one is never touched.
            connection.execute(text(f"SET LOCAL zm.app_role = 'zm_contract_{uuid4().hex[:12]}'"))
            for migration in sorted(init.glob("*.sql")):
                connection.execute(text(migration.read_text()))
            for statement in extra_sql:
                connection.execute(text(statement))
            relations = {name for namespace, name in set(connection.execute(catalog).all()) - before}
            assert {"fx_rates", "global_copper_prices"} <= relations
            unexpected = relations - APPROVED_STORAGE.keys()
            assert not unexpected, f"Unregistered persistent relations: {sorted(unexpected)}"
        finally:
            transaction.rollback()  # schema, data, sequences, indexes and DDL all disappear
