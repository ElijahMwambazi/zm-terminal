"""ERB generation importer (parser result -> migration 014 storage). All data is SYNTHETIC (the committed erb_bulletin_2025_pdf*.txt
fixtures); the real bulletin is never read. PostgreSQL tests are opt-in via TEST_DATABASE_URL and run in the session database
created by the shared disposable-database guard, with `public` reset to migration 014 only before each test.
"""

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.services import energy_generation_store as store
from app.services import erb_generation_source as src
from app.services.erb_generation_models import ErbGenerationError, NOT_REPORTED, REPORTED
from disposable_db import reset_public
from test_erb_generation import pages, parse, replace_cell, table_row, edit

M014 = Path(__file__).parents[3] / "db/init/014_energy_generation_vintages.sql"
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
D = Decimal
M = src.BULLETIN_2025
ENERGY_TABLES = ("energy_generation_group_totals", "energy_generation_observations", "energy_generation_imports",
                 "energy_publication_candidates", "energy_publications", "energy_source_stations")


# ------------------------------------------------------------------------------------------------ offline (no database)
def test_expected_key_sets_come_from_the_pinned_table_specification():
    obs, totals = store.expected_observation_keys(), store.expected_total_keys()
    assert (len(obs), len(totals)) == (390, 52)
    per_group = {g: len({n for gg, n, _ in obs if gg == g}) for g in {g for g, _, _ in obs}}
    assert per_group == {"zesco_large_hydro": 3, "zesco_mini_hydro": 6, "zesco_diesel": 7, "independent_power_producer": 14}
    assert {y for _, _, y in obs} == set(range(2013, 2026)) == {y for _, y in totals}


def test_valid_fixture_parse_passes_pre_transaction_validation():
    store.validate_parse_result(parse())


def test_parser_keeps_printed_precision_in_the_decimal_itself():
    base = parse()
    row = table_row(22, "Table 13:", 2013)
    one_decimal = parse(edit(pages(), 22, row, replace_cell(row, 1, str(base.observations[0].generation_gwh.quantize(D("0.1"))))))
    first = lambda r: next(o for o in r.observations if o.station_name == "Kafue Gorge" and o.observation_year == 2013)
    assert first(base).generation_gwh == first(one_decimal).generation_gwh  # numerically equal ...
    assert store.observation_rows(base)[("zesco_large_hydro", "Kafue Gorge", 2013)][2] == 2
    assert store.observation_rows(one_decimal)[("zesco_large_hydro", "Kafue Gorge", 2013)][2] == 1  # ... precision differs


def test_dashes_and_zeros_map_to_distinct_stored_forms():
    rows = store.observation_rows(parse())
    dashes = [v for v in rows.values() if v[0] == NOT_REPORTED]
    zeros = [v for v in rows.values() if v[0] == REPORTED and v[1] == 0]
    assert dashes and zeros
    assert all(v[1] is None and v[2] is None for v in dashes)
    assert all(v[2] is not None for v in zeros)


def swap_observation(result, replacement_of, new_key):
    obs = list(result.observations)
    i = next(i for i, o in enumerate(obs) if (o.station_group, o.station_name, o.observation_year) == replacement_of)
    obs[i] = dataclasses.replace(obs[i], **new_key)
    return dataclasses.replace(result, observations=tuple(obs))


KEY = ("zesco_large_hydro", "Kafue Gorge", 2013)


@pytest.mark.parametrize("make", [
    lambda r: dataclasses.replace(r, observations=r.observations[:-1]),  # missing key
    lambda r: dataclasses.replace(r, observations=r.observations + (r.observations[0],)),  # duplicate key
    lambda r: swap_observation(r, KEY, {"observation_year": 2013, "station_name": "Kafue Gorge "}),  # wrong station name
    lambda r: swap_observation(r, KEY, {"station_group": "zesco_diesel"}),  # wrong group
    lambda r: swap_observation(r, KEY, {"observation_year": 2030}),  # wrong year coverage
    lambda r: swap_observation(r, KEY, {"observation_year": 2014}),  # duplicate + missing, same count (390)
    lambda r: dataclasses.replace(r, table_totals=r.table_totals[:-1]),  # missing group total
    lambda r: dataclasses.replace(r, table_totals=r.table_totals + (r.table_totals[0],)),  # duplicate/unexpected group total
    lambda r: dataclasses.replace(r, table_totals=r.table_totals[:-1] + (dataclasses.replace(r.table_totals[0]),)),  # dup + missing, same count
    lambda r: dataclasses.replace(r, table_totals=r.table_totals[:-1] + (dataclasses.replace(r.table_totals[-1], source_table="Table 99"),)),
])
def test_key_set_mismatches_are_rejected_before_any_database_work(make):
    bad = make(parse())
    with pytest.raises(ErbGenerationError):
        store.validate_parse_result(bad)
    # no engine is ever needed: the importer validates first, so an unusable engine proves zero database access
    with pytest.raises(ErbGenerationError):
        store.import_erb_2025(create_engine("postgresql+psycopg://invalid.invalid/none"), bad, retrieved_at=NOW)


def test_source_identity_and_diagnostics_are_checked_before_storage():
    result = parse()
    for bad in (dataclasses.replace(result, source_sha256="0" * 64),
                dataclasses.replace(result, publication_year=2024),
                dataclasses.replace(result, table12=dataclasses.replace(result.table12, table16_minus_table12_ipp_gwh=D("0"))),
                dataclasses.replace(result, footing=result.footing[:-1]),
                dataclasses.replace(result, footing=(dataclasses.replace(result.footing[0], residual_gwh=D("1")),) + result.footing[1:])):
        with pytest.raises(ErbGenerationError):
            store.validate_parse_result(bad)


def test_changed_bytes_are_refused_by_the_source_layer_before_any_import():
    with pytest.raises(ErbGenerationError):
        store.import_bulletin_bytes(create_engine("postgresql+psycopg://invalid.invalid/none"), b"%PDF-1.7 other bytes", retrieved_at=NOW)


def reject_before_database(bad):
    """Validation fails, and the importer raises the same error without ever needing a database (an unusable engine proves it)."""
    with pytest.raises(ErbGenerationError):
        store.validate_parse_result(bad)
    with pytest.raises(ErbGenerationError):
        store.import_erb_2025(create_engine("postgresql+psycopg://invalid.invalid/none"), bad, retrieved_at=NOW)


def first_index(result, table, page=None):
    return next(i for i, o in enumerate(result.observations) if o.source_table == table and (page is None or o.source_pdf_page == page))


def test_provenance_must_name_a_page_of_the_claimed_table():
    r = parse()
    for table, page, printed in (("Table 13", 23, 16), ("Table 15", 22, 15), ("Table 15", 23, 16), ("Table 16", 22, 15),
                                 ("Table 16", 23, 16), ("Table 14", 24, 17)):
        reject_before_database(with_obs(r, first_index(r, table), source_pdf_page=page, source_printed_page=printed))
    reject_before_database(with_obs(r, 0, source_pdf_page=22, source_printed_page=16))  # right page, wrong printed page
    reject_before_database(with_obs(r, 0, source_table="Table 99"))
    for table, page in (("Table 13", 23), ("Table 15", 22), ("Table 16", 23), ("Table 14", 24)):  # totals
        i = next(i for i, t in enumerate(r.table_totals) if t.source_table == table)
        totals = list(r.table_totals)
        totals[i] = dataclasses.replace(totals[i], source_pdf_page=page)
        reject_before_database(dataclasses.replace(r, table_totals=tuple(totals)))


def test_table_14_continuation_rows_on_page_23_are_accepted():
    r = parse()
    assert {o.source_pdf_page for o in r.observations if o.source_table == "Table 14"} == {22, 23}
    store.validate_parse_result(r)
    store.validate_parse_result(changed("page"))  # a Table 14 row on either of its own pages is fine


def replace_footing(result, index, **fields):
    f = list(result.footing)
    f[index] = dataclasses.replace(f[index], **fields)
    return dataclasses.replace(result, footing=tuple(f))


def test_diagnostics_must_agree_with_the_incoming_observations_and_totals():
    r = parse()
    o = r.observations[0]
    stale_station = with_obs(r, 0, generation_gwh=o.generation_gwh + D("1.00"))  # data moved, diagnostics left stale
    stale_total = dataclasses.replace(r, table_totals=(dataclasses.replace(r.table_totals[0], published_total_gwh=r.table_totals[0].published_total_gwh + D("1.00")),) + r.table_totals[1:])
    dashed = next(i for i, f in enumerate(r.footing) if f.stations_not_reported)
    for bad in (
        stale_station, stale_total,
        dataclasses.replace(r, footing=r.footing[1:]),  # missing diagnostic
        dataclasses.replace(r, footing=r.footing[:-1] + (r.footing[0],)),  # duplicate (and one missing): same count
        dataclasses.replace(r, footing=r.footing + (r.footing[0],)),  # extra
        replace_footing(r, dashed, stations_not_reported=r.footing[dashed].stations_not_reported + 1),
        replace_footing(r, 0, reported_sum_gwh=r.footing[0].reported_sum_gwh + D("1")),
        replace_footing(r, dashed, status="computed"),
        replace_footing(r, 0, status="reported_values_only"),
        replace_footing(r, 0, residual_gwh=D("1")),
        replace_footing(r, 0, published_total_gwh=r.footing[0].published_total_gwh + D("1")),
        replace_footing(r, 0, observation_year=2099),
        dataclasses.replace(r, table12=dataclasses.replace(r.table12, table16_minus_table12_ipp_gwh=r.table12.table16_minus_table12_ipp_gwh + D("1"))),
        dataclasses.replace(r, table12=dataclasses.replace(r.table12, table12_footing_residual_gwh=r.table12.table12_footing_residual_gwh + D("1"))),
        dataclasses.replace(r, table12=dataclasses.replace(r.table12, table16_total_gwh=r.table12.table16_total_gwh + D("1"),
                                                           table16_minus_table12_ipp_gwh=r.table12.table16_minus_table12_ipp_gwh + D("1"))),  # self-consistent but not Table 16 / 2025
    ):
        reject_before_database(bad)


def test_diagnostics_that_agree_but_break_the_pinned_residual_policy_are_rejected():
    r = changed("value")  # internally consistent ...
    store.validate_parse_result(r)
    o = r.observations[0]
    broken = with_obs(r, 0, generation_gwh=o.generation_gwh + D("1.00"))  # ... then only a station moves: residual -1.00, diagnostic recomputed
    f = r.footing[0]
    broken = replace_footing(broken, 0, reported_sum_gwh=f.reported_sum_gwh + D("1.00"), residual_gwh=f.residual_gwh - D("1.00"))
    reject_before_database(broken)


# -------------------------------------------------------------------------------------------------------------- database
@pytest.fixture
def engine(disposable_database):
    _url, eng = disposable_database
    reset_public(eng)
    with eng.begin() as c:
        c.execute(text(M014.read_text()))
    yield eng


@pytest.fixture
def ready(engine):
    store.register_vetted_candidate(engine, discovered_at=NOW)
    return engine


def q(engine: Engine, sql: str, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def count(engine: Engine, table: str) -> int:
    return q(engine, f"SELECT count(*) FROM public.{table}")[0][0]


def counts(engine: Engine) -> dict:
    return {t: count(engine, t) for t in ENERGY_TABLES}


def run(engine, result=None):
    return store.import_erb_2025(engine, result or parse(), retrieved_at=NOW)


def test_missing_schema_raises_a_clear_error_and_writes_nothing(disposable_database):
    _url, eng = disposable_database
    reset_public(eng)
    with pytest.raises(store.EnergySchemaNotMigratedError, match="Energy schema not migrated"):
        run(eng)
    with pytest.raises(store.EnergySchemaNotMigratedError):
        store.register_vetted_candidate(eng, discovered_at=NOW)
    assert q(eng, "SELECT count(*) FROM pg_class WHERE relname LIKE 'energy_%'")[0][0] == 0  # nothing created either


def test_partial_schema_is_not_accepted(engine):
    with engine.begin() as c:
        c.execute(text("DROP VIEW energy_generation_canonical"))
        c.execute(text("DROP VIEW energy_publication_current_imports"))
        c.execute(text("DROP TABLE energy_generation_group_totals"))
    with pytest.raises(store.EnergySchemaNotMigratedError):
        run(engine)


def test_candidate_registration_is_narrow_and_idempotent(engine):
    first = store.register_vetted_candidate(engine, discovered_at=NOW)
    again = store.register_vetted_candidate(engine, discovered_at=NOW)
    assert first.created and not again.created and first.candidate_id == again.candidate_id
    row = q(engine, "SELECT source_series, nominal_publication_year, source_url, source_sha256, source_size_bytes, review_status, "
                    "accepted_publication_id FROM public.energy_publication_candidates")[0]
    assert tuple(row) == (store.SOURCE_SERIES, 2025, M.url, M.sha256, M.size_bytes, "pending", None)
    other = dataclasses.replace(M, sha256="1" * 64)
    with pytest.raises(store.EnergyCandidateError):
        store.register_vetted_candidate(engine, discovered_at=NOW, manifest=other)
    assert count(engine, "energy_publication_candidates") == 1


def test_import_requires_a_registered_pending_candidate_and_writes_nothing_without_one(engine):
    with pytest.raises(store.EnergyCandidateError, match="register_vetted_candidate"):
        run(engine)
    assert set(counts(engine).values()) == {0}


def test_first_import_commits_exactly_the_accepted_publication_state(ready):
    r = run(ready)
    assert (r.status, r.revision_sequence, r.import_sequence, r.observations_inserted, r.totals_inserted,
            r.stations_inserted, r.stations_reused) == (store.IMPORTED, 1, 1, 390, 52, 30, 0)
    assert counts(ready) == {"energy_generation_group_totals": 52, "energy_generation_observations": 390,
                             "energy_generation_imports": 1, "energy_publication_candidates": 1, "energy_publications": 1,
                             "energy_source_stations": 30}
    pub = q(ready, "SELECT * FROM public.energy_publications")[0]._mapping
    assert (pub["publisher"], pub["source_series"], pub["publication_year"], pub["revision_sequence"], pub["publication_status"],
            pub["supersedes_publication_id"], pub["title"], pub["cover_date"], pub["source_url"], pub["source_sha256"],
            pub["source_size_bytes"], pub["pdf_pages"], pub["rights_status"], pub["first_retrieved_at"]) == (
        "ERB", "erb_statistical_bulletin", 2025, 1, "approved", None, "2025 ANNUAL STATISTICAL BULLETIN", "February 2026", M.url,
        M.sha256, M.size_bytes, 42, "review_required", NOW)
    imp = q(ready, "SELECT * FROM public.energy_generation_imports")[0]._mapping
    assert (imp["publication_id"], imp["import_sequence"], imp["import_status"], imp["expected_observation_count"],
            imp["expected_group_total_count"], imp["importer_version"], imp["correction_reason"]) == (
        r.publication_id, 1, "current", 390, 52, store.IMPORTER_VERSION, None)
    cand = q(ready, "SELECT review_status, accepted_publication_id, review_note IS NOT NULL, reviewed_at IS NOT NULL "
                    "FROM public.energy_publication_candidates")[0]
    assert tuple(cand) == ("accepted", r.publication_id, True, True)
    assert q(ready, "SELECT count(*) FROM public.energy_publication_current_imports")[0][0] == 1
    assert q(ready, "SELECT count(*) FROM public.energy_generation_canonical")[0][0] == 390


def test_stored_station_identities_are_the_exact_parser_identities(ready):
    run(ready)
    stored = {tuple(r) for r in q(ready, "SELECT source_series, station_group, station_name FROM public.energy_source_stations")}
    assert stored == {(store.SOURCE_SERIES, g, n) for g, n, _ in store.expected_observation_keys()}
    assert len(stored) == 30


def test_observations_round_trip_value_state_precision_and_provenance(ready):
    result = parse()
    run(ready, result)
    rows = q(ready, "SELECT s.station_group, s.station_name, o.observation_year, o.value_state, o.generation_gwh, o.published_precision, "
                    "o.source_table, o.source_pdf_page, o.source_printed_page, o.imported_at FROM public.energy_generation_observations o "
                    "JOIN public.energy_source_stations s ON s.id = o.source_station_id")
    stored = {(r[0], r[1], r[2]): tuple(r[3:9]) for r in rows}
    assert stored == store.observation_rows(result)
    assert all(r[9] is not None for r in rows)
    dashes = [v for v in stored.values() if v[0] == "not_reported"]
    assert len(dashes) == 39 and all(v[1] is None and v[2] is None for v in dashes)  # never zero
    zeros = [v for v in stored.values() if v[0] == "reported" and v[1] == 0]
    assert zeros and all(isinstance(v[2], int) for v in zeros)
    assert {v[2] for v in stored.values() if v[0] == "reported"} == {0, 2}  # printed scale, from the source token
    assert q(ready, "SELECT count(*) FROM public.energy_generation_observations WHERE value_state='reported' "
                    "AND scale(generation_gwh) <> published_precision")[0][0] == 0


def test_printed_precision_variants_persist_exactly(ready):
    row = table_row(22, "Table 13:", 2013)
    result = parse(edit(pages(), 22, row, replace_cell(row, 1, str(next(o for o in parse().observations if o.station_name == "Kafue Gorge"
                                                                        and o.observation_year == 2013).generation_gwh.quantize(D("0.1"))))))
    run(ready, result)
    got = q(ready, "SELECT o.generation_gwh, o.published_precision FROM public.energy_generation_observations o "
                   "JOIN public.energy_source_stations s ON s.id = o.source_station_id "
                   "WHERE s.station_name = 'Kafue Gorge' AND o.observation_year = 2013")[0]
    assert got[1] == 1 and str(got[0]).count(".") == 1 and len(str(got[0]).split(".")[1]) == 1


def test_group_totals_are_the_published_totals_not_station_sums(ready):
    result = parse()
    run(ready, result)
    totals = {(r[0], r[1]): (r[2], r[3], r[4], r[5], r[6]) for r in q(
        ready, "SELECT station_group, observation_year, total_gwh, published_precision, source_table, source_pdf_page, source_printed_page "
               "FROM public.energy_generation_group_totals")}
    assert totals == store.total_rows(result)
    # the two pinned source discrepancies (Table 16 2024 and 2025) prove the stored value is the printed total, not a recomputed sum
    sums = {(r[0], r[1]): r[2] for r in q(ready, "SELECT s.station_group, o.observation_year, sum(o.generation_gwh) "
                                                 "FROM public.energy_generation_observations o JOIN public.energy_source_stations s "
                                                 "ON s.id = o.source_station_id GROUP BY 1, 2")}
    ipp = "independent_power_producer"
    assert totals[(ipp, 2024)][0] - sums[(ipp, 2024)] == D("0.05") and totals[(ipp, 2025)][0] - sums[(ipp, 2025)] == D("0.01")
    assert len(totals) == 52


def test_identical_second_run_is_a_complete_noop(ready):
    first = run(ready)
    before = counts(ready)
    snapshot = q(ready, "SELECT id, status_changed_at, import_status FROM public.energy_generation_imports")
    second = store.import_erb_2025(ready, parse(), retrieved_at=datetime(2030, 1, 1, tzinfo=timezone.utc), importer_version="other/9")
    assert (second.status, second.publication_id, second.import_id, second.import_sequence) == (
        store.ALREADY_PRESENT, first.publication_id, first.import_id, 1)
    assert (second.observations_inserted, second.totals_inserted, second.stations_inserted, second.stations_reused) == (0, 0, 0, 0)
    assert counts(ready) == before
    assert q(ready, "SELECT id, status_changed_at, import_status FROM public.energy_generation_imports") == snapshot
    assert q(ready, "SELECT publication_status FROM public.energy_publications") == [("approved",)]
    assert q(ready, "SELECT importer_version FROM public.energy_generation_imports") == [(store.IMPORTER_VERSION,)]


def with_obs(result, index, **fields):
    obs = list(result.observations)
    obs[index] = dataclasses.replace(obs[index], **fields)
    return dataclasses.replace(result, observations=tuple(obs))


def changed(kind):
    """Internally consistent variants of the parse (diagnostics follow the data), so only the stored-data comparison can reject them."""
    result = parse()
    o, t, f = result.observations[0], result.table_totals[0], result.footing[0]  # Table 13, 2013: Kafue Gorge
    if kind == "value":  # a station value and its published total move together, so footing still holds
        r = with_obs(result, 0, generation_gwh=o.generation_gwh + D("1.00"))
        t2 = dataclasses.replace(t, published_total_gwh=t.published_total_gwh + D("1.00"))
        f2 = dataclasses.replace(f, reported_sum_gwh=f.reported_sum_gwh + D("1.00"), published_total_gwh=f.published_total_gwh + D("1.00"))
        return dataclasses.replace(r, table_totals=(t2,) + r.table_totals[1:], footing=(f2,) + r.footing[1:])
    if kind == "precision":
        return with_obs(result, 0, generation_gwh=o.generation_gwh.quantize(D("0.1")))
    if kind == "state":  # a printed dash becomes a printed zero; the footing diagnostic follows
        i = next(i for i, x in enumerate(result.observations) if x.value_state == NOT_REPORTED)
        x = result.observations[i]
        r = with_obs(result, i, generation_gwh=D("0"), value_state=REPORTED)
        j = next(j for j, g in enumerate(r.footing) if (g.source_table, g.observation_year) == (x.source_table, x.observation_year))
        g = r.footing[j]
        left = g.stations_not_reported - 1
        g2 = dataclasses.replace(g, stations_not_reported=left, status="reported_values_only" if left else "computed")
        return dataclasses.replace(r, footing=r.footing[:j] + (g2,) + r.footing[j + 1:])
    if kind == "page":  # a Table 14 row legitimately printed on its other page
        i = next(i for i, x in enumerate(result.observations) if x.source_table == "Table 14" and x.source_pdf_page == 22)
        return with_obs(result, i, source_pdf_page=23, source_printed_page=16)
    return dataclasses.replace(result, table_totals=(dataclasses.replace(t, published_total_gwh=t.published_total_gwh.quantize(D("0.1"))),)
                               + result.table_totals[1:], footing=(dataclasses.replace(f, published_total_gwh=f.published_total_gwh.quantize(D("0.1"))),) + result.footing[1:])


@pytest.mark.parametrize("kind", ["value", "precision", "state", "page", "total_precision"])
def test_stable_field_difference_for_the_same_sha_is_a_conflict_that_writes_nothing(ready, kind):
    run(ready)
    before = counts(ready)
    stored = q(ready, "SELECT o.id, o.generation_gwh, o.published_precision FROM public.energy_generation_observations o ORDER BY o.id")
    bad = changed(kind)
    store.validate_parse_result(bad)  # passes pre-transaction validation, so the stored-data comparison is what rejects it
    with pytest.raises(store.EnergyImportConflictError):
        run(ready, bad)
    assert counts(ready) == before
    assert q(ready, "SELECT o.id, o.generation_gwh, o.published_precision FROM public.energy_generation_observations o ORDER BY o.id") == stored
    assert q(ready, "SELECT import_status FROM public.energy_generation_imports") == [("current",)]


def test_importer_refuses_a_changed_publication_row_for_the_vetted_identity(ready):
    run(ready)
    other = dataclasses.replace(M, title="2025 ANNUAL STATISTICAL BULLETIN (edited)")
    with pytest.raises(ErbGenerationError):  # only the vetted manifest is accepted at all
        store.import_erb_2025(ready, parse(), retrieved_at=NOW, manifest=other)
    assert count(ready, "energy_publications") == 1


def test_source_sha_mismatch_writes_nothing(ready):
    before = counts(ready)
    with pytest.raises(ErbGenerationError):
        run(ready, dataclasses.replace(parse(), source_sha256="2" * 64))
    assert counts(ready) == before and q(ready, "SELECT review_status FROM public.energy_publication_candidates") == [("pending",)]


def test_late_failure_rolls_the_entire_first_import_back(ready, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("late failure after every insert")
    monkeypatch.setattr(store, "_accept_candidate", boom)
    with pytest.raises(RuntimeError):
        run(ready)
    assert {t: n for t, n in counts(ready).items() if t != "energy_publication_candidates"} == {
        "energy_generation_group_totals": 0, "energy_generation_observations": 0, "energy_generation_imports": 0,
        "energy_publications": 0, "energy_source_stations": 0}
    assert q(ready, "SELECT review_status, accepted_publication_id FROM public.energy_publication_candidates") == [("pending", None)]
    monkeypatch.undo()
    assert run(ready).status == store.IMPORTED  # a clean retry succeeds from the untouched state


def test_deferred_database_count_constraint_failure_at_commit_rolls_everything_back(ready, monkeypatch):
    real = store._accept_candidate

    def add_a_stray_observation_then_accept(c, candidate_id, publication_id, m):
        real(c, candidate_id, publication_id, m)
        c.execute(text("INSERT INTO public.energy_generation_observations (import_id, source_station_id, observation_year, value_state, "
                       "generation_gwh, published_precision, source_table, source_pdf_page, source_printed_page) "
                       "SELECT i.id, s.id, 2099, 'reported', 1.00, 2, 'Table 13', 22, 15 FROM public.energy_generation_imports i, "
                       "public.energy_source_stations s WHERE s.station_name = 'Kafue Gorge' LIMIT 1"))  # 391 stored vs 390 expected
    monkeypatch.setattr(store, "_accept_candidate", add_a_stray_observation_then_accept)
    with pytest.raises(IntegrityError, match="expected 390 / 52"):
        run(ready)
    assert {t: n for t, n in counts(ready).items() if t != "energy_publication_candidates"} == dict.fromkeys(
        [t for t in ENERGY_TABLES if t != "energy_publication_candidates"], 0)
    assert q(ready, "SELECT review_status FROM public.energy_publication_candidates") == [("pending",)]


def test_station_reuse_does_not_create_duplicates(ready):
    with ready.begin() as c:
        c.execute(text("INSERT INTO public.energy_source_stations (source_series, station_group, station_name) VALUES "
                       "('erb_statistical_bulletin', 'zesco_large_hydro', 'Kafue Gorge'), "
                       "('erb_statistical_bulletin', 'independent_power_producer', 'Chisamba')"))
        c.execute(text("INSERT INTO public.energy_source_stations (source_series, station_group, station_name) VALUES "
                       "('other_series', 'zesco_large_hydro', 'Kafue Gorge')"))
    r = run(ready)
    assert (r.stations_inserted, r.stations_reused) == (28, 2)
    assert count(ready, "energy_source_stations") == 31  # 30 + the unrelated series' own station
    assert q(ready, "SELECT count(*) FROM public.energy_source_stations WHERE source_series = 'erb_statistical_bulletin'")[0][0] == 30


def test_a_near_identical_station_name_is_a_different_identity_not_reused(ready):
    with ready.begin() as c:  # stored identities are exact: a case/whitespace-different name must not be merged into the pinned one
        c.execute(text("INSERT INTO public.energy_source_stations (source_series, station_group, station_name) VALUES "
                       "('erb_statistical_bulletin', 'zesco_large_hydro', 'kafue gorge')"))
    r = run(ready)
    assert (r.stations_inserted, r.stations_reused) == (30, 0)
    assert count(ready, "energy_source_stations") == 31


def test_importer_sql_ignores_temp_tables_that_shadow_energy_names(ready):
    pinned = create_engine(ready.url, poolclass=StaticPool)  # one connection, so TEMP tables stay visible to every importer statement
    try:
        with pinned.begin() as c:
            for table in ENERGY_TABLES:
                c.execute(text(f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING DEFAULTS)"))
            c.execute(text("INSERT INTO pg_temp.energy_publications (publisher, source_series, publication_year, revision_sequence, "
                           "publication_status, title, source_url, source_sha256, source_size_bytes, pdf_pages, rights_status, "
                           "first_retrieved_at) VALUES ('ERB','erb_statistical_bulletin',2025,1,'approved','spoof','u',"
                           f"'{M.sha256}',1,1,'review_required', now())"))
        r = store.import_erb_2025(pinned, parse(), retrieved_at=NOW)  # the spoofed temp publication must not look like an existing one
        assert r.status == store.IMPORTED
        again = store.import_erb_2025(pinned, parse(), retrieved_at=NOW)
        assert again.status == store.ALREADY_PRESENT
        with pinned.connect() as c:
            assert c.execute(text("SELECT count(*) FROM pg_temp.energy_generation_observations")).scalar() == 0
            assert c.execute(text("SELECT count(*) FROM pg_temp.energy_publications")).scalar() == 1  # only the spoof row we planted
    finally:
        pinned.dispose()
    assert (count(ready, "energy_publications"), count(ready, "energy_generation_observations"),
            count(ready, "energy_generation_group_totals")) == (1, 390, 52)


def test_database_constraints_pass_normally_after_import(ready):
    run(ready)
    assert q(ready, "SELECT count(*) FROM public.energy_generation_imports WHERE import_status = 'current'")[0][0] == 1
    assert q(ready, "SELECT count(*) FROM public.energy_publication_candidates c JOIN public.energy_publications p "
                    "ON p.id = c.accepted_publication_id AND p.source_sha256 = c.source_sha256 AND p.source_url = c.source_url "
                    "AND p.source_size_bytes = c.source_size_bytes WHERE c.review_status = 'accepted'")[0][0] == 1
    assert q(ready, "SELECT i.expected_observation_count = (SELECT count(*) FROM public.energy_generation_observations o "
                    "WHERE o.import_id = i.id), i.expected_group_total_count = (SELECT count(*) FROM "
                    "public.energy_generation_group_totals t WHERE t.import_id = i.id) FROM public.energy_generation_imports i")[0] == (True, True)
    with pytest.raises(IntegrityError):  # the database still refuses a second current import for the publication
        with ready.begin() as c:
            c.execute(text("INSERT INTO public.energy_generation_imports (publication_id, import_sequence, import_status, "
                           "expected_observation_count, expected_group_total_count, importer_version, correction_reason) "
                           "SELECT id, 2, 'current', 0, 0, 'x', 'because' FROM public.energy_publications"))


# ------------------------------------------------------------------------------------- trigger bindings (schema precondition)
TRIGGERS = [(t, n) for t, n, *_rest in store._REQUIRED_TRIGGERS]


def refused_without_writes(engine):
    before = counts(engine)
    with pytest.raises(store.EnergySchemaNotMigratedError, match="Energy schema not migrated"):
        run(engine)
    with pytest.raises(store.EnergySchemaNotMigratedError):
        store.register_vetted_candidate(engine, discovered_at=NOW)
    assert counts(engine) == before
    assert q(engine, "SELECT review_status, accepted_publication_id FROM public.energy_publication_candidates") == [("pending", None)]


def test_the_complete_migration_satisfies_every_required_trigger_binding(ready):
    assert len(TRIGGERS) == 22
    with ready.connect() as c:
        store._require_schema(c)  # passes on the real migration 014


@pytest.mark.parametrize("table,trigger", TRIGGERS)
def test_a_missing_required_trigger_is_refused_before_any_write(ready, table, trigger):
    with ready.begin() as c:
        c.execute(text(f"DROP TRIGGER {trigger} ON public.{table}"))
    refused_without_writes(ready)


@pytest.mark.parametrize("table,trigger", [("energy_generation_imports", "energy_generation_imports_counts"),
                                           ("energy_generation_observations", "energy_generation_observations_counts"),
                                           ("energy_publications", "energy_publications_chain"),
                                           ("energy_generation_group_totals", "energy_generation_group_totals_immutable")])
@pytest.mark.parametrize("mode", ["DISABLE", "ENABLE REPLICA", "ENABLE ALWAYS"])
def test_a_disabled_or_replica_only_trigger_is_refused(ready, table, trigger, mode):
    with ready.begin() as c:
        c.execute(text(f"ALTER TABLE public.{table} {mode} TRIGGER {trigger}"))
    if mode == "ENABLE ALWAYS":  # fires in every session role, which is stricter than required: still a correctly working trigger
        with ready.connect() as c:
            store._require_schema(c)
        return
    refused_without_writes(ready)


def test_a_trigger_rebound_to_the_wrong_function_is_refused(ready):
    with ready.begin() as c:
        c.execute(text("DROP TRIGGER energy_generation_imports_counts ON public.energy_generation_imports"))
        c.execute(text("CREATE CONSTRAINT TRIGGER energy_generation_imports_counts AFTER INSERT OR UPDATE ON public.energy_generation_imports "
                       "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.energy_check_one_current_import()"))
    refused_without_writes(ready)


def test_a_count_trigger_that_is_not_deferred_is_refused(ready):
    with ready.begin() as c:
        c.execute(text("DROP TRIGGER energy_generation_observations_counts ON public.energy_generation_observations"))
        c.execute(text("CREATE CONSTRAINT TRIGGER energy_generation_observations_counts AFTER INSERT ON public.energy_generation_observations "
                       "FOR EACH ROW EXECUTE FUNCTION public.energy_check_import_counts()"))  # immediate, not DEFERRABLE INITIALLY DEFERRED
    refused_without_writes(ready)


def test_a_look_alike_trigger_on_a_temp_table_does_not_satisfy_the_check(ready):
    pinned = create_engine(ready.url, poolclass=StaticPool)
    try:
        with pinned.begin() as c:
            c.execute(text("DROP TRIGGER energy_generation_imports_counts ON public.energy_generation_imports"))
            c.execute(text("CREATE TEMP TABLE energy_generation_imports (LIKE public.energy_generation_imports INCLUDING DEFAULTS)"))
            c.execute(text("CREATE CONSTRAINT TRIGGER energy_generation_imports_counts AFTER INSERT ON pg_temp.energy_generation_imports "
                           "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.energy_check_import_counts()"))
        with pytest.raises(store.EnergySchemaNotMigratedError):
            store.import_erb_2025(pinned, parse(), retrieved_at=NOW)
    finally:
        pinned.dispose()
    assert count(ready, "energy_publications") == 0


def test_the_391_observation_scenario_cannot_commit_without_its_count_trigger(ready):
    """Codex repro: with the count bindings removed the old precondition passed and an import with a stray row committed."""
    with ready.begin() as c:
        for table, trigger in (("energy_generation_imports", "energy_generation_imports_counts"),
                               ("energy_generation_observations", "energy_generation_observations_counts"),
                               ("energy_generation_group_totals", "energy_generation_group_totals_counts")):
            c.execute(text(f"DROP TRIGGER {trigger} ON public.{table}"))
    refused_without_writes(ready)


# --------------------------------------------------------------------------- trigger firing contract (timing / events / level)
def rebuild(engine, table, trigger, function, definition):
    """Replace a required trigger by one with the same name, table and function but a different firing definition."""
    with engine.begin() as c:
        c.execute(text(f"DROP TRIGGER {trigger} ON public.{table}"))
        c.execute(text(f"CREATE {definition.replace('<name>', trigger).replace('<table>', 'public.' + table)} "
                       f"EXECUTE FUNCTION public.{function}()"))


COUNTS = ("energy_generation_imports", "energy_generation_imports_counts", "energy_check_import_counts")
OBS_COUNTS = ("energy_generation_observations", "energy_generation_observations_counts", "energy_check_import_counts")
DEFERRED = "DEFERRABLE INITIALLY DEFERRED"


def test_tgtype_decoding_distinguishes_timing_events_and_level():
    assert store._firing(1 | 4) == ("AFTER INSERT", True)
    assert store._firing(1 | 4 | 16) == ("AFTER INSERT OR UPDATE", True)
    assert store._firing(1 | 2 | 8) == ("BEFORE DELETE", True)
    assert store._firing(16) == ("AFTER UPDATE", False)  # statement-level
    assert store._firing(1 | 64 | 4) == ("INSTEAD OF INSERT", True)
    assert store._firing(1 | 4 | 32)[0] == "AFTER INSERT OR TRUNCATE"


def test_every_required_firing_contract_matches_the_real_migration(ready):
    with ready.connect() as c:
        found = {(r.tbl, r.name): store._firing(r.tgtype) for r in c.execute(store._TRIGGER_CATALOG, {"tables": list(store._REQUIRED_RELATIONS)})}
    assert {(t, n): (firing, True) for t, n, _f, firing, _d in store._REQUIRED_TRIGGERS} == {k: found[k] for k in
                                                                                              {(t, n) for t, n, *_ in store._REQUIRED_TRIGGERS}}


def test_codex_repro_count_trigger_recreated_as_after_update_is_refused(ready):
    rebuild(ready, *COUNTS, f"CONSTRAINT TRIGGER <name> AFTER UPDATE ON <table> {DEFERRED} FOR EACH ROW")
    with ready.connect() as c:  # everything else about the trigger is still right
        row = next(r for r in c.execute(store._TRIGGER_CATALOG, {"tables": ["energy_generation_imports"]}) if r.name == COUNTS[1])
        assert (row.fn, row.fn_schema, row.enabled, row.deferred) == ("energy_check_import_counts", "public", "O", True)
    refused_without_writes(ready)


def test_observation_count_trigger_recreated_as_after_update_is_refused(ready):
    rebuild(ready, *OBS_COUNTS, f"CONSTRAINT TRIGGER <name> AFTER UPDATE ON <table> {DEFERRED} FOR EACH ROW")
    refused_without_writes(ready)


def test_wrong_timing_is_refused(ready):
    rebuild(ready, "energy_publications", "energy_publications_no_delete", "energy_reject_delete",
            "TRIGGER <name> AFTER DELETE ON <table> FOR EACH ROW")  # AFTER instead of BEFORE
    refused_without_writes(ready)


def test_before_instead_of_after_on_a_count_trigger_is_refused(ready):
    rebuild(ready, "energy_generation_group_totals", "energy_generation_group_totals_counts", "energy_check_import_counts",
            "TRIGGER <name> BEFORE INSERT ON <table> FOR EACH ROW")  # (also no longer a deferred constraint trigger)
    refused_without_writes(ready)


def test_statement_level_instead_of_row_level_is_refused(ready):
    rebuild(ready, "energy_source_stations", "energy_source_stations_immutable", "energy_reject_change",
            "TRIGGER <name> BEFORE UPDATE ON <table> FOR EACH STATEMENT")
    refused_without_writes(ready)


def test_a_missing_event_from_a_multi_event_trigger_is_refused(ready):
    rebuild(ready, *COUNTS, f"CONSTRAINT TRIGGER <name> AFTER INSERT ON <table> {DEFERRED} FOR EACH ROW")  # needs INSERT OR UPDATE
    refused_without_writes(ready)


def test_an_extra_event_is_not_accepted_either(ready):
    rebuild(ready, "energy_generation_observations", "energy_generation_observations_counts", "energy_check_import_counts",
            f"CONSTRAINT TRIGGER <name> AFTER INSERT OR UPDATE ON <table> {DEFERRED} FOR EACH ROW")  # migration: INSERT only
    refused_without_writes(ready)


def test_intact_schema_still_fails_at_commit_on_a_391st_observation(ready, monkeypatch):
    real = store._accept_candidate

    def stray(c, candidate_id, publication_id, m):
        real(c, candidate_id, publication_id, m)
        c.execute(text("INSERT INTO public.energy_generation_observations (import_id, source_station_id, observation_year, value_state, "
                       "generation_gwh, published_precision, source_table, source_pdf_page, source_printed_page) "
                       "SELECT i.id, s.id, 2099, 'reported', 1.00, 2, 'Table 13', 22, 15 FROM public.energy_generation_imports i, "
                       "public.energy_source_stations s WHERE s.station_name = 'Kafue Gorge' LIMIT 1"))
    monkeypatch.setattr(store, "_accept_candidate", stray)
    with pytest.raises(IntegrityError, match="expected 390 / 52"):
        run(ready)
    assert count(ready, "energy_publications") == 0 and count(ready, "energy_generation_observations") == 0
