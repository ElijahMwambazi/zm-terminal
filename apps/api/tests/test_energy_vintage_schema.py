"""Migration 014 (Energy generation vintages): PostgreSQL lifecycle tests. All data is SYNTHETIC; no ERB value is used.

Opt-in like the other PostgreSQL tests: set TEST_DATABASE_URL to a DISPOSABLE database. The URL is parsed and checked by
disposable_db.parse_disposable_database_url (actual database name, never the normal `zm_terminal`) before any engine exists.
Tests run in a database created for the session (dropped at the end) and each starts from a reset `public` schema holding only migration 014,
as deployed (the lifecycle functions are bound to `public`); the reset removes tables, views and trigger/helper functions together.
Deferred (commit-time) invariants surface when the `with engine.begin()` block exits, so the failing statement is the commit.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from disposable_db import reset_public

M014 = Path(__file__).parents[3] / "db/init/014_energy_generation_vintages.sql"
SERIES = "erb_statistical_bulletin"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

ENERGY_TABLES = ("energy_generation_group_totals", "energy_generation_observations", "energy_generation_imports",
                 "energy_publication_candidates", "energy_publications", "energy_source_stations")


@pytest.fixture
def engine(disposable_database):
    _url, eng = disposable_database
    reset_public(eng)
    with eng.begin() as c:
        c.execute(text(M014.read_text()))
    yield eng


# ------------------------------------------------------------------------------------------------------------------ helpers
def sha(n: int) -> str:
    return f"{n:064x}"


def precision(d: Decimal) -> int:
    return -d.as_tuple().exponent


def one(c, sql, **p):
    return c.execute(text(sql), p).scalar()


def station(c, name, group="independent_power_producer", series=SERIES):
    c.execute(text("INSERT INTO energy_source_stations (source_series, station_group, station_name) VALUES (:s,:g,:n) "
                   "ON CONFLICT DO NOTHING"), {"s": series, "g": group, "n": name})
    return one(c, "SELECT id FROM energy_source_stations WHERE source_series=:s AND station_group=:g AND station_name=:n",
               s=series, g=group, n=name)


def candidate(c, n, year=2025, series=SERIES):
    return one(c, "INSERT INTO energy_publication_candidates (source_series, nominal_publication_year, source_url, source_sha256, "
                  "source_size_bytes, discovered_at) VALUES (:s,:y,:u,:h,1000,:t) RETURNING id",
               s=series, y=year, u=f"https://example.invalid/{n}.pdf", h=sha(n), t=NOW)


def add_publication(c, n, year, rev, series=SERIES, prev=None):
    return one(c, "INSERT INTO energy_publications (publisher, source_series, publication_year, revision_sequence, publication_status, "
                  "supersedes_publication_id, title, source_url, source_sha256, source_size_bytes, pdf_pages, rights_status, "
                  "first_retrieved_at) VALUES ('ERB',:s,:y,:r,'approved',:prev,'Synthetic bulletin',:u,:h,1000,10,'review_required',:t) "
                  "RETURNING id", s=series, y=year, r=rev, prev=prev, u=f"https://example.invalid/{n}.pdf", h=sha(n), t=NOW)


def add_import(c, pub_id, seq, n_obs, n_tot, status="current", reason=None):
    return one(c, "INSERT INTO energy_generation_imports (publication_id, import_sequence, import_status, expected_observation_count, "
                  "expected_group_total_count, importer_version, correction_reason) VALUES (:p,:q,:st,:o,:t,'test-1',:r) RETURNING id",
               p=pub_id, q=seq, st=status, o=n_obs, t=n_tot, r=reason)


def add_obs(c, import_id, station_id, year, value):
    if value is None:
        state, v, pr = "not_reported", None, None
    else:
        d = Decimal(value)
        state, v, pr = "reported", d, precision(d)
    c.execute(text("INSERT INTO energy_generation_observations (import_id, source_station_id, observation_year, value_state, "
                   "generation_gwh, published_precision, source_table, source_pdf_page, source_printed_page) "
                   "VALUES (:i,:s,:y,:st,:v,:p,'Table 16',23,16)"), {"i": import_id, "s": station_id, "y": year, "st": state, "v": v, "p": pr})


def add_total(c, import_id, year, value, group="independent_power_producer"):
    d = Decimal(value)
    c.execute(text("INSERT INTO energy_generation_group_totals (import_id, station_group, observation_year, total_gwh, published_precision, "
                   "source_table, source_pdf_page, source_printed_page) VALUES (:i,:g,:y,:v,:p,'Table 16',23,16)"),
              {"i": import_id, "g": group, "y": year, "v": d, "p": precision(d)})


def link_candidate(c, cand_id, pub_id):
    c.execute(text("UPDATE energy_publication_candidates SET review_status='accepted', review_note='accepted', reviewed_at=:t, "
                   "accepted_publication_id=:p WHERE id=:id"), {"t": NOW, "p": pub_id, "id": cand_id})


def publish(c, n, year, rev, obs, total="100.00", series=SERIES, prev=None, cand=None):
    """Whole first-import transaction body. obs = [(station_id, observation_year, value-or-None)]; total=None stores no group total.
    Returns (pub, import)."""
    cand = cand or candidate(c, n, year, series)
    pub = add_publication(c, n, year, rev, series, prev)
    imp = add_import(c, pub, 1, len(obs), 0 if total is None else 1)
    for station_id, y, value in obs:
        add_obs(c, imp, station_id, y, value)
    if total is not None:
        add_total(c, imp, 2020, total)
    link_candidate(c, cand, pub)
    if prev:
        c.execute(text("UPDATE energy_publications SET publication_status='superseded', status_changed_at=:t, status_note='replaced' "
                       "WHERE id=:p"), {"t": NOW, "p": prev})
    return pub, imp


def fails(engine, body, match=None):
    with pytest.raises(IntegrityError) as err:
        with engine.begin() as c:
            body(c)
    if match:
        assert match in str(err.value)


def canonical(engine, st, year):
    with engine.connect() as c:
        return c.execute(text("SELECT publication_year, revision_sequence, import_sequence, value_state, generation_gwh, "
                              "published_precision, is_latest_publication FROM energy_generation_canonical "
                              "WHERE source_station_id=:s AND observation_year=:y"), {"s": st, "y": year}).one_or_none()


# ----------------------------------------------------------------------------------------------------- migration and emptiness
def test_migration_is_idempotent_creates_the_approved_objects_and_inserts_no_rows(engine):
    with engine.begin() as c:
        c.execute(text(M014.read_text()))  # second application is a no-op
    with engine.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))}
        views = {r[0] for r in c.execute(text("SELECT viewname FROM pg_views WHERE schemaname = current_schema()"))}
        counts = [one(c, f"SELECT COUNT(*) FROM {t}") for t in ENERGY_TABLES]
    assert tables == set(ENERGY_TABLES)
    assert views == {"energy_publication_current_imports", "energy_generation_canonical"}
    assert counts == [0] * 6


# ------------------------------------------------------------------------------------------------------------------ candidates
def test_candidate_sha_is_unique_and_a_rejected_candidate_consumes_no_revision(engine):
    with engine.begin() as c:
        cid = candidate(c, 1)
        c.execute(text("UPDATE energy_publication_candidates SET review_status='rejected', review_note='corrupt', reviewed_at=:t "
                       "WHERE id=:i"), {"t": NOW, "i": cid})
    fails(engine, lambda c: candidate(c, 1))  # same bytes are reviewed once
    with engine.connect() as c:
        assert one(c, "SELECT COUNT(*) FROM energy_publications") == 0  # no publication row, hence no revision number


def test_candidate_legal_and_illegal_transitions(engine):
    with engine.begin() as c:
        a, b, d = candidate(c, 1), candidate(c, 2), candidate(c, 3)
        c.execute(text("UPDATE energy_publication_candidates SET review_status='rejected', review_note='x', reviewed_at=:t "
                       "WHERE id=:i"), {"t": NOW, "i": a})
        pub, _ = publish(c, 2, 2025, 1, [(station(c, "S"), 2020, "1.00")], cand=b)
    # pending -> pending with changed note is not a transition
    fails(engine, lambda c: c.execute(text("UPDATE energy_publication_candidates SET review_note='n' WHERE id=:i"), {"i": d}))
    # accepted is terminal
    fails(engine, lambda c: c.execute(text("UPDATE energy_publication_candidates SET review_status='rejected', review_note='x', "
                                           "reviewed_at=:t, accepted_publication_id=NULL WHERE id=:i"), {"t": NOW, "i": b}))
    fails(engine, lambda c: c.execute(text("UPDATE energy_publication_candidates SET review_status='pending', review_note=NULL, "
                                           "reviewed_at=NULL WHERE id=:i"), {"i": a}))  # rejected -> pending
    fails(engine, lambda c: c.execute(text("UPDATE energy_publication_candidates SET source_sha256=:h WHERE id=:i"), {"h": sha(99), "i": d}))
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_publication_candidates WHERE id=:i"), {"i": d}))


def test_rejected_candidate_can_later_be_accepted_with_updated_review_metadata(engine):
    with engine.begin() as c:
        cid = candidate(c, 1)
        c.execute(text("UPDATE energy_publication_candidates SET review_status='rejected', review_note='looked corrupt', reviewed_at=:t "
                       "WHERE id=:i"), {"t": NOW, "i": cid})
    with engine.begin() as c:
        publish(c, 1, 2025, 1, [(station(c, "S"), 2020, "1.00")], cand=cid)
    with engine.connect() as c:
        row = c.execute(text("SELECT review_status, review_note FROM energy_publication_candidates WHERE id=:i"), {"i": cid}).one()
        assert row == ("accepted", "accepted")
        assert one(c, "SELECT COUNT(*) FROM energy_publication_candidates") == 1  # no second candidate for the same SHA


def test_pending_candidate_must_not_carry_review_fields_and_a_non_accepted_one_cannot_link_a_publication(engine):
    with engine.begin() as c:
        pub, _ = publish(c, 1, 2025, 1, [(station(c, "S"), 2020, "1.00")])
        other = candidate(c, 2)
    fails(engine, lambda c: c.execute(text("UPDATE energy_publication_candidates SET accepted_publication_id=:p, review_status='rejected', "
                                           "review_note='x', reviewed_at=:t WHERE id=:i"), {"p": pub, "t": NOW, "i": other}))


# ----------------------------------------------------------------------------------------------------------------- publications
def test_first_import_commits_atomically_and_a_failure_leaves_nothing(engine):
    with engine.begin() as c:
        st = station(c, "S")
        cid = candidate(c, 1)
    # candidate pending; publication + import + data + candidate link in one transaction
    with engine.begin() as c:
        publish(c, 1, 2025, 1, [(st, 2020, "5.00")], cand=cid)
    with engine.connect() as c:
        assert one(c, "SELECT review_status FROM energy_publication_candidates") == "accepted"
        assert one(c, "SELECT COUNT(*) FROM energy_publications") == 1
    # failed first import (wrong child count at commit): nothing survives, candidate stays pending
    with engine.begin() as c:
        cid2 = candidate(c, 2, 2026)

    def bad(c):
        pub = add_publication(c, 2, 2026, 1)
        imp = add_import(c, pub, 1, 2, 1)  # expects two observations but only one inserted
        add_obs(c, imp, st, 2020, "1.00")
        add_total(c, imp, 2020, "1.00")
        link_candidate(c, cid2, pub)
    fails(engine, bad)
    with engine.connect() as c:
        assert one(c, "SELECT COUNT(*) FROM energy_publications") == 1
        assert one(c, "SELECT COUNT(*) FROM energy_generation_imports") == 1
        assert one(c, "SELECT review_status FROM energy_publication_candidates WHERE id=:i", i=cid2) == "pending"


def test_publication_without_a_matching_accepted_candidate_or_without_an_import_cannot_commit(engine):
    def no_candidate(c):
        pub = add_publication(c, 1, 2025, 1)
        imp = add_import(c, pub, 1, 0, 0)
    fails(engine, no_candidate)

    def no_import(c):
        cid = candidate(c, 2)
        link_candidate(c, cid, add_publication(c, 2, 2025, 1))
    fails(engine, no_import)

    def mismatched(c):  # candidate bytes differ from the publication's
        cid = candidate(c, 3)
        pub = add_publication(c, 4, 2025, 1)
        add_import(c, pub, 1, 0, 0)
        link_candidate(c, cid, pub)
    fails(engine, mismatched)


def test_revision_predecessor_rules(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, _ = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        p24, _ = publish(c, 2, 2024, 1, [(st, 2020, "1.00")])
    # r2 without a predecessor, r3 directly after r1, backwards, self reference, cross-year
    fails(engine, lambda c: add_publication(c, 10, 2025, 2, prev=None), "must supersede revision 1")
    fails(engine, lambda c: add_publication(c, 11, 2025, 3, prev=p1))  # gap: r3 replacing r1
    fails(engine, lambda c: add_publication(c, 12, 2025, 2, prev=p24))  # cross-year predecessor (FK)
    fails(engine, lambda c: add_publication(c, 13, 2025, 1, prev=p1))  # r1 must have no predecessor
    fails(engine, lambda c: c.execute(text("UPDATE energy_publications SET supersedes_publication_id=id WHERE id=:i"), {"i": p1}))
    # cross-series predecessor (FK includes the series)
    with engine.begin() as c:
        p_other, _ = publish(c, 20, 2025, 1, [(station(c, "D", "zesco_diesel", "erb_other"), 2020, "1.00")], series="erb_other")
    fails(engine, lambda c: add_publication(c, 14, 2025, 2, prev=p_other))


def test_multiple_successors_are_rejected(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, _ = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        p2, _ = publish(c, 2, 2025, 2, [(st, 2020, "2.00")], prev=p1)
    fails(engine, lambda c: add_publication(c, 3, 2025, 2, prev=p1))  # second r2 (and second successor of r1)


def test_all_annual_vintages_stay_approved_together(engine):
    with engine.begin() as c:
        st = station(c, "S")
        for n, year in ((1, 2023), (2, 2024), (3, 2025)):
            publish(c, n, year, 1, [(st, 2020, "1.00")])
    with engine.connect() as c:
        rows = c.execute(text("SELECT publication_year, publication_status FROM energy_publications ORDER BY 1")).all()
    assert rows == [(2023, "approved"), (2024, "approved"), (2025, "approved")]


def test_chain_must_be_consistent_at_commit(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, _ = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])

    def r2_without_superseding_r1(c):
        cid = candidate(c, 2)
        pub = add_publication(c, 2, 2025, 2, prev=p1)
        imp = add_import(c, pub, 1, 1, 1)
        add_obs(c, imp, st, 2020, "2.00")
        add_total(c, imp, 2020, "1.00")
        link_candidate(c, cid, pub)  # r1 left approved: two approved revisions in one nominal year
    fails(engine, r2_without_superseding_r1)

    def supersede_alone(c):  # r1 superseded with no approved replacement
        c.execute(text("UPDATE energy_publications SET publication_status='superseded', status_changed_at=:t, status_note='x' WHERE id=:i"),
                  {"t": NOW, "i": p1})
    fails(engine, supersede_alone)


def test_publication_immutability_transitions_and_delete(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, _ = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
    for column, value in (("title", "'x'"), ("source_sha256", f"'{sha(77)}'"), ("revision_sequence", "5"), ("rights_status", "'open'"),
                          ("source_url", "'https://x'"), ("publication_year", "2024")):
        fails(engine, lambda c, col=column, v=value: c.execute(text(f"UPDATE energy_publications SET {col}={v} WHERE id=:i"), {"i": p1}))
    fails(engine, lambda c: c.execute(text("UPDATE energy_publications SET status_note='n' WHERE id=:i"), {"i": p1}))  # no transition
    fails(engine, lambda c: c.execute(text("UPDATE energy_publications SET publication_status='rejected' WHERE id=:i"), {"i": p1}))
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_publications WHERE id=:i"), {"i": p1}))
    with engine.begin() as c:  # a legal replacement supersedes r1; superseded is then terminal
        publish(c, 2, 2025, 2, [(st, 2020, "2.00")], prev=p1)
    fails(engine, lambda c: c.execute(text("UPDATE energy_publications SET publication_status='approved' WHERE id=:i"), {"i": p1}))


def test_rejected_candidate_does_not_block_a_later_r2(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, _ = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        a = candidate(c, 2)  # replacement candidate A ...
        c.execute(text("UPDATE energy_publication_candidates SET review_status='rejected', review_note='unexpected file', reviewed_at=:t "
                       "WHERE id=:i"), {"t": NOW, "i": a})
        b = candidate(c, 3)  # ... then candidate B with different bytes
    with engine.begin() as c:
        p2, _ = publish(c, 3, 2025, 2, [(st, 2020, "2.00")], prev=p1, cand=b)
    with engine.connect() as c:
        rows = c.execute(text("SELECT revision_sequence, publication_status FROM energy_publications ORDER BY 1")).all()
        assert rows == [(1, "superseded"), (2, "approved")]  # no rejected r2 occupies the chain, and no gap
        assert one(c, "SELECT review_status FROM energy_publication_candidates WHERE id=:i", i=a) == "rejected"
        assert one(c, "SELECT accepted_publication_id FROM energy_publication_candidates WHERE id=:i", i=b) == p2


def test_same_year_replacement_is_atomic_and_a_failure_keeps_r1_canonical(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
    assert canonical(engine, st, 2020)[:2] == (2025, 1)

    def failing_replacement(c):
        cid = candidate(c, 2)
        pub = add_publication(c, 2, 2025, 2, prev=p1)
        imp = add_import(c, pub, 1, 3, 1)  # wrong expected count
        add_obs(c, imp, st, 2020, "2.00")
        add_total(c, imp, 2020, "1.00")
        link_candidate(c, cid, pub)
        c.execute(text("UPDATE energy_publications SET publication_status='superseded', status_changed_at=:t, status_note='x' WHERE id=:i"),
                  {"t": NOW, "i": p1})
    fails(engine, failing_replacement)
    with engine.connect() as c:
        assert one(c, "SELECT publication_status FROM energy_publications WHERE id=:i", i=p1) == "approved"
        assert one(c, "SELECT COUNT(*) FROM energy_publications") == 1
    assert canonical(engine, st, 2020)[:2] == (2025, 1)

    with engine.begin() as c:
        publish(c, 2, 2025, 2, [(st, 2020, "2.00")], prev=p1)
    with engine.connect() as c:
        assert c.execute(text("SELECT revision_sequence, publication_status FROM energy_publications ORDER BY 1")).all() == [(1, "superseded"), (2, "approved")]
        assert one(c, "SELECT COUNT(*) FROM energy_generation_imports") == 2  # both import histories stored
    assert canonical(engine, st, 2020)[:2] == (2025, 2)


# ---------------------------------------------------------------------------------------------------------------------- imports
def test_exactly_one_current_import_at_commit(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])

    def zero_current(c):  # demote the only current import
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i1})
    fails(engine, zero_current, "exactly one current import")

    def two_current(c):
        i2 = add_import(c, p1, 2, 1, 0, reason="fix")
        add_obs(c, i2, st, 2020, "1.50")
    fails(engine, two_current, "exactly one current import")


def test_correction_swap_is_atomic_and_leaves_source_publication_and_old_data_unchanged(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        before_pub = c.execute(text("SELECT * FROM energy_publications WHERE id=:i"), {"i": p1}).one()
        before_obs = c.execute(text("SELECT * FROM energy_generation_observations WHERE import_id=:i"), {"i": i1}).all()

    def correct(c):
        i2 = add_import(c, p1, 2, 1, 1, reason="parser fix")
        add_obs(c, i2, st, 2020, "1.25")
        add_total(c, i2, 2020, "100.00")
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t, status_note='replaced' "
                       "WHERE id=:i"), {"t": NOW, "i": i1})
        return i2
    with engine.begin() as c:
        i2 = correct(c)
    with engine.connect() as c:
        assert c.execute(text("SELECT * FROM energy_publications WHERE id=:i"), {"i": p1}).one() == before_pub
        assert c.execute(text("SELECT * FROM energy_generation_observations WHERE import_id=:i"), {"i": i1}).all() == before_obs
        assert one(c, "SELECT generation_gwh FROM energy_generation_observations WHERE import_id=:i", i=i2) == Decimal("1.25")
        assert c.execute(text("SELECT import_sequence, import_status FROM energy_generation_imports ORDER BY 1")).all() == [(1, "superseded"), (2, "current")]
        assert one(c, "SELECT COUNT(*) FROM energy_publications") == 1  # a local correction is never a fake r2
        assert one(c, "SELECT COUNT(*) FROM energy_publications WHERE revision_sequence > 1") == 0
    assert canonical(engine, st, 2020)[:3] == (2025, 1, 2) and canonical(engine, st, 2020)[4] == Decimal("1.25")


def test_invalid_correction_rolls_back_and_the_old_import_stays_current(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])

    def bad_correction(c):
        i2 = add_import(c, p1, 2, 2, 1, reason="parser fix")  # expects 2 observations, inserts 1
        add_obs(c, i2, st, 2020, "9.99")
        add_total(c, i2, 2020, "1.00")
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i1})
    fails(engine, bad_correction, "at commit")
    with engine.connect() as c:
        assert c.execute(text("SELECT import_sequence, import_status FROM energy_generation_imports")).all() == [(1, "current")]
        assert one(c, "SELECT generation_gwh FROM energy_generation_observations") == Decimal("1.00")


def test_import_sequence_and_stable_field_rules(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
    fails(engine, lambda c: add_import(c, p1, 3, 0, 0, status="superseded", reason="skipped 2"))  # no gaps
    fails(engine, lambda c: add_import(c, p1, 2, 0, 0, status="superseded", reason=None))  # correction needs a reason
    fails(engine, lambda c: add_import(c, p1, 1, 0, 0))  # duplicate identity
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_imports SET expected_observation_count=9 WHERE id=:i"), {"i": i1}))
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_imports SET import_sequence=2 WHERE id=:i"), {"i": i1}))
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_imports SET status_note='n' WHERE id=:i"), {"i": i1}))  # no transition
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_generation_imports WHERE id=:i"), {"i": i1}))


def test_superseded_import_cannot_return_to_current(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        i2 = add_import(c, p1, 2, 1, 1, reason="fix")
        add_obs(c, i2, st, 2020, "1.10")
        add_total(c, i2, 2020, "1.00")
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i1})
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_imports SET import_status='current' WHERE id=:i"), {"i": i1}))


def test_child_counts_are_enforced_at_commit_not_per_row(engine):
    with engine.begin() as c:  # rows arrive one by one inside the transaction without tripping anything
        st1, st2 = station(c, "S1"), station(c, "S2")
        publish(c, 1, 2025, 1, [(st1, 2020, "1.00"), (st2, 2020, "2.00")])
    with engine.begin() as c:
        st = station(c, "S1")
        pub, imp = one(c, "SELECT id FROM energy_publications"), one(c, "SELECT id FROM energy_generation_imports")
    fails(engine, lambda c: add_obs(c, imp, station(c, "S3"), 2020, "3.00"))  # a late extra row breaks the stored expectation
    fails(engine, lambda c: add_total(c, imp, 2021, "1.00"))  # likewise for totals
    with engine.connect() as c:
        assert one(c, "SELECT COUNT(*) FROM energy_generation_observations") == 2


# ------------------------------------------------------------------------------------------------------------------- stations
def test_station_identity_is_series_group_and_exact_name(engine):
    with engine.begin() as c:
        a = station(c, "Itezhi tezhi", "zesco_large_hydro")
        b = station(c, "Itezhi tezhi Power Corporation", "independent_power_producer")
        reclassified = station(c, "Itezhi tezhi", "independent_power_producer")
        case = station(c, "itezhi tezhi", "zesco_large_hydro")
    assert len({a, b, reclassified, case}) == 4  # no normalization, merging or case folding
    fails(engine, lambda c: c.execute(text("INSERT INTO energy_source_stations (source_series, station_group, station_name) "
                                           "VALUES (:s,'zesco_large_hydro','Itezhi tezhi')"), {"s": SERIES}))
    fails(engine, lambda c: c.execute(text("INSERT INTO energy_source_stations (source_series, station_group, station_name) VALUES (:s,'zesco_large_hydro',' padded')"), {"s": SERIES}))
    fails(engine, lambda c: c.execute(text("INSERT INTO energy_source_stations (source_series, station_group, station_name) VALUES (:s,'wind','W')"), {"s": SERIES}))
    fails(engine, lambda c: c.execute(text("UPDATE energy_source_stations SET station_name='X' WHERE id=:i"), {"i": a}))
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_source_stations WHERE id=:i"), {"i": a}))


def test_a_station_of_another_series_cannot_be_used_by_an_import(engine):
    with engine.begin() as c:
        foreign = station(c, "D", "zesco_diesel", "erb_other")
    fails(engine, lambda c: publish(c, 1, 2025, 1, [(foreign, 2020, "1.00")]))


# ---------------------------------------------------------------------------------------------------------------- observations
def violation(engine, body, sqlstate, constraint):
    """The body must fail with exactly this SQLSTATE and this named constraint (not merely 'some IntegrityError')."""
    with pytest.raises(IntegrityError) as err:
        with engine.begin() as c:
            body(c)
    assert (err.value.orig.sqlstate, err.value.orig.diag.constraint_name) == (sqlstate, constraint)


def side_import(c, pub_id, n_obs, n_tot):
    """A superseded correction import whose stored expectations match what the test inserts. Its deferred count and current-import
    checks therefore pass, so ONLY the row-level constraint under test can fail, and it fails at the INSERT statement itself."""
    seq = one(c, "SELECT MAX(import_sequence) + 1 FROM energy_generation_imports WHERE publication_id=:p", p=pub_id)
    return add_import(c, pub_id, seq, n_obs, n_tot, status="superseded", reason="constraint probe")


def raw_obs(c, imp, st, state, value, prec, year=2021):
    c.execute(text("INSERT INTO energy_generation_observations (import_id, source_station_id, observation_year, value_state, generation_gwh, "
                   "published_precision, source_table, source_pdf_page, source_printed_page) "
                   "VALUES (:i,:s,:y,:st,:v,:p,'T',1,1)"), {"i": imp, "s": st, "y": year, "st": state, "v": value, "p": prec})


STATE_VALUE = "energy_generation_observations_state_value"


@pytest.fixture
def probe(engine):
    """An accepted publication (its own import is complete) plus helpers for isolated single-row probes."""
    with engine.begin() as c:
        st, st2 = station(c, "S"), station(c, "S2")
        pub, imp = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
    return engine, pub, imp, st, st2


def test_probe_harness_accepts_a_valid_row_so_the_negative_cases_isolate_their_constraint(probe):
    engine, pub, imp, st, st2 = probe
    with engine.begin() as c:  # the same harness with a VALID row commits: deferred count checks do not mask anything
        raw_obs(c, side_import(c, pub, 1, 0), st2, "reported", Decimal("1.5"), 1)
    with engine.connect() as c:
        assert one(c, "SELECT COUNT(*) FROM energy_generation_observations") == 2


@pytest.mark.parametrize("state,value,prec,label", [
    ("reported", None, None, "reported without a value"),
    ("reported", Decimal("1.5"), None, "reported without precision"),
    ("reported", None, 1, "reported with NULL value"),
    ("not_reported", Decimal("0"), 0, "not_reported with numeric zero"),
    ("not_reported", Decimal("1.0"), None, "not_reported with a numeric value"),
    ("not_reported", None, 1, "not_reported with a precision"),
    ("reported", Decimal("-1.0"), 1, "negative value"),
    ("reported", Decimal("1.50"), 1, "precision different from the stored scale"),
    ("reported", Decimal("1.5"), 2, "invalid scale (printed precision larger than the scale)"),
    ("reported", Decimal("1.5"), -1, "negative precision"),
    ("reported", Decimal("NaN"), 0, "NaN"),
    ("reported", Decimal("Infinity"), 0, "Infinity"),
    ("reported", Decimal("-Infinity"), 0, "-Infinity"),
    ("reported", Decimal("1000000"), 0, "value at or above the upper bound"),
])
def test_observation_value_rules_each_fail_on_the_state_value_constraint(probe, state, value, prec, label):
    engine, pub, imp, st, st2 = probe
    violation(engine, lambda c: raw_obs(c, side_import(c, pub, 1, 0), st2, state, value, prec), "23514", STATE_VALUE)


def test_observation_state_year_and_uniqueness_each_fail_on_their_own_constraint(probe):
    engine, pub, imp, st, st2 = probe
    violation(engine, lambda c: raw_obs(c, side_import(c, pub, 1, 0), st2, "unknown", None, None), "23514",
              STATE_VALUE)  # an unknown state also violates the vocabulary CHECK; PostgreSQL reports the first failing one by name
    violation(engine, lambda c: raw_obs(c, side_import(c, pub, 1, 0), st2, "reported", Decimal("1.00"), 2, year=1989), "23514",
              "energy_generation_observations_observation_year_check")

    def duplicate(c):  # the second identical (import, station, year) row; expected count 2 so only uniqueness can fail
        side = side_import(c, pub, 2, 0)
        raw_obs(c, side, st2, "reported", Decimal("1.00"), 2)
        raw_obs(c, side, st2, "reported", Decimal("2.00"), 2)
    violation(engine, duplicate, "23505", "energy_generation_observations_identity")


def test_observation_and_total_immutability_uniqueness_and_zero_is_distinct_from_not_reported(engine):
    with engine.begin() as c:
        st, st2 = station(c, "S"), station(c, "S2")
        pub, imp = publish(c, 1, 2025, 1, [(st, 2020, "0"), (st2, 2020, None)])
    with engine.connect() as c:
        rows = c.execute(text("SELECT value_state, generation_gwh, published_precision FROM energy_generation_observations "
                              "ORDER BY source_station_id")).all()
    assert rows == [("reported", Decimal("0"), 0), ("not_reported", None, None)]  # a printed zero is not a dash
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_observations SET generation_gwh=1")))
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_generation_observations")))
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_group_totals SET total_gwh=1")))
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_generation_group_totals")))
    fails(engine, lambda c: add_total(c, imp, 2020, "1.00"))  # duplicate (import, group, year)


def test_numeric_precision_round_trips_through_the_driver(engine):
    with engine.begin() as c:
        s1, s2 = station(c, "A"), station(c, "B")
        pub, imp = publish(c, 1, 2025, 1, [(s1, 2020, "393.40"), (s2, 2020, "393.4")])
    with engine.connect() as c:
        rows = {r[0]: r[1:] for r in c.execute(text(
            "SELECT source_station_id, generation_gwh, published_precision, scale(generation_gwh), generation_gwh::text "
            "FROM energy_generation_observations"))}
    a, b = rows[s1], rows[s2]
    assert isinstance(a[0], Decimal) and isinstance(b[0], Decimal)  # never float
    assert a[0] == b[0] == Decimal("393.4")  # numerically equal ...
    assert (a[1], a[2], a[3]) == (2, 2, "393.40")  # ... but the printed scale survives the round trip
    assert (b[1], b[2], b[3]) == (1, 1, "393.4")
    assert a[0].as_tuple().exponent == -2 and b[0].as_tuple().exponent == -1


def test_group_total_precision_provenance_and_scale(engine):
    with engine.begin() as c:
        st = station(c, "S")
        pub, imp = publish(c, 1, 2025, 1, [(st, 2020, "1.00")], total="5848.01")
    with engine.connect() as c:
        row = c.execute(text("SELECT total_gwh, published_precision, source_table, source_pdf_page, source_printed_page "
                             "FROM energy_generation_group_totals")).one()
    assert row == (Decimal("5848.01"), 2, "Table 16", 23, 16)


def raw_total(c, imp, value, prec, group="zesco_diesel", year=2020):
    c.execute(text("INSERT INTO energy_generation_group_totals (import_id, station_group, observation_year, total_gwh, published_precision, "
                   "source_table, source_pdf_page, source_printed_page) VALUES (:i,:g,:y,:v,:p,'T',1,1)"),
              {"i": imp, "g": group, "y": year, "v": value, "p": prec})


@pytest.mark.parametrize("value,prec,constraint,label", [
    (Decimal("1.50"), 1, "energy_generation_group_totals_scale", "precision different from the stored scale"),
    (Decimal("1.5"), 2, "energy_generation_group_totals_scale", "invalid scale"),
    (Decimal("-1.5"), 1, "energy_generation_group_totals_total_gwh_check", "negative total"),
    (Decimal("NaN"), 0, "energy_generation_group_totals_total_gwh_check", "NaN total"),
    (Decimal("Infinity"), 0, "energy_generation_group_totals_total_gwh_check", "Infinity total"),
    (Decimal("1000000"), 0, "energy_generation_group_totals_total_gwh_check", "total at or above the upper bound"),
    (Decimal("1.5"), -1, "energy_generation_group_totals_published_precision_check", "negative precision"),
])
def test_group_total_value_and_precision_rules_each_fail_on_their_own_constraint(probe, value, prec, constraint, label):
    engine, pub, imp, st, st2 = probe
    violation(engine, lambda c: raw_total(c, side_import(c, pub, 0, 1), value, prec), "23514", constraint)


def test_group_total_probe_harness_accepts_a_valid_row(probe):
    engine, pub, imp, st, st2 = probe
    with engine.begin() as c:
        raw_total(c, side_import(c, pub, 0, 1), Decimal("1.5"), 1)
    with engine.connect() as c:
        assert one(c, "SELECT COUNT(*) FROM energy_generation_group_totals") == 2


# ------------------------------------------------------------------------------------------------------------------------ views
def test_current_import_view_maps_each_accepted_publication_to_its_one_current_import(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        i2 = add_import(c, p1, 2, 1, 1, reason="fix")
        add_obs(c, i2, st, 2020, "1.10")
        add_total(c, i2, 2020, "1.00")
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i1})
        publish(c, 2, 2025, 2, [(st, 2020, "2.00")], prev=p1)
    with engine.connect() as c:
        rows = c.execute(text("SELECT revision_sequence, publication_status, import_sequence, import_status FROM energy_publication_current_imports "
                              "ORDER BY revision_sequence")).all()
    assert rows == [(1, "superseded", 2, "current"), (2, "approved", 1, "current")]  # superseded publications are included; the view picks no vintage


def test_canonical_omission_fallback_across_all_annual_vintages_and_latest_flag(engine):
    with engine.begin() as c:
        x, y, z = station(c, "X"), station(c, "Y"), station(c, "Z")
        publish(c, 1, 2023, 1, [(x, 2020, "1.00"), (y, 2020, "3.00"), (z, 2020, "7.00")])
        publish(c, 2, 2024, 1, [(x, 2020, "2.00"), (y, 2020, "4.00")])
        publish(c, 3, 2025, 1, [(x, 2020, "5.00")])
    assert canonical(engine, x, 2020) == (2025, 1, 1, "reported", Decimal("5.00"), 2, True)
    assert canonical(engine, y, 2020) == (2024, 1, 1, "reported", Decimal("4.00"), 2, False)  # omitted in 2025: fall back to 2024
    assert canonical(engine, z, 2020) == (2023, 1, 1, "reported", Decimal("7.00"), 2, False)  # omitted in 2025 and 2024: 2023
    assert canonical(engine, x, 2021) is None


def test_canonical_newer_explicit_dash_wins_without_fallback(engine):
    with engine.begin() as c:
        x = station(c, "X")
        publish(c, 1, 2024, 1, [(x, 2020, "5.00")])
        publish(c, 2, 2025, 1, [(x, 2020, None)])
    assert canonical(engine, x, 2020) == (2025, 1, 1, "not_reported", None, None, True)


def test_canonical_same_year_r2_wins_and_r1_stays_queryable(engine):
    with engine.begin() as c:
        x = station(c, "X")
        p1, i1 = publish(c, 1, 2025, 1, [(x, 2020, "1.00")])
        publish(c, 2, 2025, 2, [(x, 2020, "2.00")], prev=p1)
    assert canonical(engine, x, 2020)[:2] == (2025, 2) and canonical(engine, x, 2020)[4] == Decimal("2.00")
    with engine.connect() as c:
        assert one(c, "SELECT generation_gwh FROM energy_generation_observations WHERE import_id=:i", i=i1) == Decimal("1.00")
        assert one(c, "SELECT publication_status FROM energy_publications WHERE id=:i", i=p1) == "superseded"


def test_canonical_uses_only_the_current_import_and_import_sequence_never_orders_vintages(engine):
    with engine.begin() as c:
        x = station(c, "X")
        p1, i1 = publish(c, 1, 2025, 1, [(x, 2020, "1.00")])
        p2, i2 = publish(c, 2, 2025, 2, [(x, 2020, "2.00")], prev=p1)
        i3 = add_import(c, p2, 2, 1, 1, reason="parser fix")  # r2 import 1 superseded, import 2 current
        add_obs(c, i3, x, 2020, "2.50")
        add_total(c, i3, 2020, "1.00")
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i2})
        # r1 has the same number of imports (1) as r2 had; a high import_sequence on an OLDER vintage must not outrank a newer one
        i1b = add_import(c, p1, 2, 1, 1, reason="fix r1 too")
        add_obs(c, i1b, x, 2020, "1.90")
        add_total(c, i1b, 2020, "1.00")
        c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i1})
    row = canonical(engine, x, 2020)
    assert row[:3] == (2025, 2, 2) and row[4] == Decimal("2.50")
    with engine.connect() as c:  # import 1 of r2 stays queryable from the base tables, and there is still only r1 and r2
        assert one(c, "SELECT generation_gwh FROM energy_generation_observations WHERE import_id=:i", i=i2) == Decimal("2.00")
        assert one(c, "SELECT COUNT(*) FROM energy_publications") == 2


def test_precision_only_difference_between_vintages_is_kept_exactly(engine):
    with engine.begin() as c:
        x = station(c, "X")
        publish(c, 1, 2024, 1, [(x, 2020, "393.40")])
        publish(c, 2, 2025, 1, [(x, 2020, "393.4")])
    with engine.connect() as c:
        rows = c.execute(text("SELECT p.publication_year, o.generation_gwh, o.published_precision, o.generation_gwh::text "
                              "FROM energy_generation_observations o JOIN energy_generation_imports i ON i.id=o.import_id "
                              "JOIN energy_publications p ON p.id=i.publication_id ORDER BY 1")).all()
    assert rows == [(2024, Decimal("393.4"), 2, "393.40"), (2025, Decimal("393.4"), 1, "393.4")]
    assert rows[0][1] == rows[1][1] and rows[0][2] != rows[1][2]
    assert canonical(engine, x, 2020)[:2] == (2025, 1)  # the newer official row is selected normally
    with engine.connect() as c:  # no restatement classification is stored anywhere
        cols = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema()"))}
    assert not {c for c in cols if "restatement" in c or c == "change"}


def test_canonical_views_exclude_nothing_but_superseded_publications_and_noncurrent_imports(engine):
    with engine.begin() as c:
        x, y = station(c, "X"), station(c, "Y")
        p1, _ = publish(c, 1, 2025, 1, [(y, 2020, "9.00")])
        publish(c, 2, 2025, 2, [(x, 2020, "2.00")], prev=p1)  # r2 omits Y; r1 is superseded, so Y is NOT canonical
    assert canonical(engine, y, 2020) is None  # superseded r1 is not canonical-eligible, so omission does not resurrect it
    assert canonical(engine, x, 2020)[:2] == (2025, 2)


# --------------------------------------------------------------------------------------------------------- added lifecycle coverage
def supersede_import(c, import_id):
    c.execute(text("UPDATE energy_generation_imports SET import_status='superseded', status_changed_at=:t, status_note='replaced' "
                   "WHERE id=:i"), {"t": NOW, "i": import_id})


def test_successful_third_revision_supersedes_r2_and_becomes_canonical(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, _ = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        p2, _ = publish(c, 2, 2025, 2, [(st, 2020, "2.00")], prev=p1)
    with engine.begin() as c:
        publish(c, 3, 2025, 3, [(st, 2020, "3.00")], prev=p2)
    with engine.connect() as c:
        rows = c.execute(text("SELECT revision_sequence, publication_status, supersedes_publication_id IS NOT NULL "
                              "FROM energy_publications ORDER BY 1")).all()
        assert one(c, "SELECT COUNT(*) FROM energy_generation_imports WHERE import_status='current'") == 3
    assert rows == [(1, "superseded", False), (2, "superseded", True), (3, "approved", True)]
    row = canonical(engine, st, 2020)
    assert row[:2] == (2025, 3) and row[4] == Decimal("3.00") and row[6] is True


def test_correction_swap_works_in_the_opposite_statement_order(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
    with engine.begin() as c:  # demote first, then insert the replacement: still consistent at COMMIT
        supersede_import(c, i1)
        i2 = add_import(c, p1, 2, 1, 1, reason="parser fix")
        add_obs(c, i2, st, 2020, "1.25")
        add_total(c, i2, 2020, "100.00")
    with engine.connect() as c:
        assert c.execute(text("SELECT import_sequence, import_status FROM energy_generation_imports ORDER BY 1")).all() == [(1, "superseded"), (2, "current")]
    assert canonical(engine, st, 2020)[2] == 2 and canonical(engine, st, 2020)[4] == Decimal("1.25")


def test_import_with_zero_expected_observations_and_totals_commits(engine):
    with engine.begin() as c:
        st = station(c, "S")
        pub, imp = publish(c, 1, 2025, 1, [], total=None)
    with engine.connect() as c:
        assert one(c, "SELECT expected_observation_count + expected_group_total_count FROM energy_generation_imports WHERE id=:i", i=imp) == 0
        assert one(c, "SELECT COUNT(*) FROM energy_generation_observations") == 0
        assert one(c, "SELECT COUNT(*) FROM energy_generation_group_totals") == 0
        assert one(c, "SELECT COUNT(*) FROM energy_publication_current_imports") == 1
    assert canonical(engine, st, 2020) is None
    fails(engine, lambda c: add_obs(c, imp, st, 2020, "1.00"), "at commit")  # a late row contradicts the stored zero expectation


def test_correction_may_carry_different_reviewed_expected_counts(engine):
    with engine.begin() as c:
        s1, s2 = station(c, "S1"), station(c, "S2")
        p1, i1 = publish(c, 1, 2025, 1, [(s1, 2020, "1.00")])  # import 1: 1 observation, 1 group total
    with engine.begin() as c:
        i2 = add_import(c, p1, 2, 2, 0, reason="reviewed: parser now also reads S2, no group totals")
        add_obs(c, i2, s1, 2020, "1.00")
        add_obs(c, i2, s2, 2020, "2.00")
        supersede_import(c, i1)
    with engine.connect() as c:
        counts = c.execute(text("SELECT import_sequence, expected_observation_count, expected_group_total_count, import_status "
                                "FROM energy_generation_imports ORDER BY 1")).all()
    assert counts == [(1, 1, 1, "superseded"), (2, 2, 0, "current")]
    assert canonical(engine, s2, 2020)[2] == 2 and canonical(engine, s1, 2020)[2] == 2


def test_superseded_import_remains_complete_valid_and_immutable(engine):
    with engine.begin() as c:
        st = station(c, "S")
        p1, i1 = publish(c, 1, 2025, 1, [(st, 2020, "1.00")])
        i2 = add_import(c, p1, 2, 1, 1, reason="fix")
        add_obs(c, i2, st, 2020, "1.10")
        add_total(c, i2, 2020, "1.00")
        supersede_import(c, i1)
    with engine.connect() as c:
        row = c.execute(text("SELECT i.import_status, i.expected_observation_count, i.expected_group_total_count, "
                             "(SELECT COUNT(*) FROM energy_generation_observations WHERE import_id=i.id), "
                             "(SELECT COUNT(*) FROM energy_generation_group_totals WHERE import_id=i.id) "
                             "FROM energy_generation_imports i WHERE i.id=:i"), {"i": i1}).one()
    assert row == ("superseded", 1, 1, 1, 1)  # still complete: stored expectations equal the actual children
    fails(engine, lambda c: add_obs(c, i1, station(c, "S9"), 2020, "9.00"), "at commit")  # and still guarded by the count invariant
    fails(engine, lambda c: c.execute(text("UPDATE energy_generation_observations SET generation_gwh=9 WHERE import_id=:i"), {"i": i1}))
    fails(engine, lambda c: c.execute(text("DELETE FROM energy_generation_group_totals WHERE import_id=:i"), {"i": i1}))


def test_reapplying_014_converges_an_earlier_unhardened_draft_without_duplicating_objects(engine):
    import re
    draft = re.sub(r"\bpublic\.(energy_)", r"\1", M014.read_text()).replace(" SET search_path = pg_catalog, pg_temp;", ";")
    assert draft != M014.read_text()

    def state(c):
        funcs = c.execute(text("SELECT proname, prosrc, proconfig FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'energy\\_%' "
                               "ORDER BY 1")).all()
        trig = one(c, "SELECT COUNT(*) FROM pg_trigger WHERE NOT tgisinternal")
        objs = one(c, "SELECT COUNT(*) FROM pg_class WHERE relnamespace='public'::regnamespace")
        return funcs, trig, objs
    with engine.begin() as c:  # reproduce a database that applied the earlier, shadowable definitions
        c.execute(text(draft))
        before_funcs, before_trig, before_objs = state(c)
        assert all(cfg is None for _, _, cfg in before_funcs)
        c.execute(text(M014.read_text()))  # the hardened migration, applied over it
        funcs, trig, objs = state(c)
        c.execute(text(M014.read_text()))  # and once more: stable
        assert state(c) == (funcs, trig, objs)
    assert (trig, objs) == (before_trig, before_objs)  # no duplicated triggers, tables, views or indexes
    assert len(funcs) == 12
    for name, src, cfg in funcs:
        assert cfg == ["search_path=pg_catalog, pg_temp"], name
        assert not re.search(r"\b(FROM|JOIN)\s+energy_", src), name
