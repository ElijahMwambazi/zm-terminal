"""Migration 017 (Bank FX storage): guarded disposable PostgreSQL only; SYNTHETIC rows. Never the normal database."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from bank_fx_db import M017, bank_fx_engine, scalar

NOW = "2030-01-03T08:00:00+00:00"


@pytest.fixture
def db(disposable_database):
    with bank_fx_engine(disposable_database) as (admin, role):
        yield admin, role


def snap(c, **over):
    row = {"observation_date": "2030-01-02", "source_record_id": str(uuid4()), "source_revision_id": 1, "source_internal_nid": 1,
           "source_created": NOW, "source_changed": NOW, "source_revision_timestamp": NOW, "source_title": "T",
           "source_rates_datetime_raw": "2030-01-02T15:00:00+02:00", "source_rates_datetime": NOW, "table_layout": "v2",
           "retrieved_at": NOW, "source_url": "https://example.test/", "rights_status": "review_required"}
    row.update(over)
    cols = ", ".join(row)
    return c.execute(text(f"INSERT INTO bank_fx_snapshots ({cols}) VALUES ({', '.join(':' + k for k in row)}) RETURNING id"), row).scalar()


def rate(c, snapshot_id, **over):
    row = {"snapshot_id": snapshot_id, "source_row_index": 3, "slot": "09:30", "institution_name_raw": "A BANK ", "institution_name": "A BANK",
           "is_market_average": False, "interbank_buy": Decimal("9.90"), "interbank_buy_text": "9.90", "interbank_sell": Decimal("10.1"),
           "interbank_sell_text": "10.1", "retail_buy": Decimal("9.9000"), "retail_buy_text": "9.9000", "retail_sell": Decimal("10.0001"),
           "retail_sell_text": "10.0001", "retail_source_label": "BOARD RATES"}
    row.update(over)
    cols = ", ".join(row)
    return c.execute(text(f"INSERT INTO bank_fx_rates ({cols}) VALUES ({', '.join(':' + k for k in row)}) RETURNING id"), row).scalar()


def fails(admin, fn, error=IntegrityError):
    with pytest.raises(error) as raised:
        with admin.begin() as c:
            fn(c)
    return raised.value


def test_tables_view_columns_and_types(db):
    admin, _ = db
    with admin.connect() as c:
        cols = {(t, n): (d, null) for t, n, d, null in c.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name IN ('bank_fx_snapshots','bank_fx_rates')"))}
        assert all(null == "NO" for _, null in cols.values())
        assert cols[("bank_fx_snapshots", "observation_date")][0] == "date" and cols[("bank_fx_snapshots", "source_record_id")][0] == "uuid"
        assert cols[("bank_fx_snapshots", "source_revision_id")][0] == "bigint" and cols[("bank_fx_snapshots", "source_internal_nid")][0] == "bigint"
        for name in ("source_created", "source_changed", "source_revision_timestamp", "source_rates_datetime", "retrieved_at"):
            assert cols[("bank_fx_snapshots", name)][0] == "timestamp with time zone", name
        assert cols[("bank_fx_snapshots", "source_rates_datetime_raw")][0] == "text"
        for name in ("interbank_buy", "interbank_sell", "retail_buy", "retail_sell"):
            assert cols[("bank_fx_rates", name)][0] == "numeric" and cols[("bank_fx_rates", name + "_text")][0] == "text"
        assert cols[("bank_fx_rates", "source_row_index")][0] == "integer" and cols[("bank_fx_rates", "is_market_average")][0] == "boolean"
        assert scalar(c, "SELECT COUNT(*) FROM information_schema.views WHERE table_name='bank_fx_canonical_snapshots'") == 1
        assert scalar(c, "SELECT COUNT(*) FROM bank_fx_snapshots") == 0  # nothing inserted by the migration


def test_unique_node_revision_identity_and_foreign_key(db):
    admin, _ = db
    node = str(uuid4())
    with admin.begin() as c:
        snap(c, source_record_id=node, source_revision_id=1)
        snap(c, source_record_id=node, source_revision_id=2)  # another revision of the node, even for the same date
    fails(admin, lambda c: snap(c, source_record_id=node, source_revision_id=1, observation_date="2030-02-02"))  # date is not identity
    fails(admin, lambda c: rate(c, 987654))  # no such snapshot
    with admin.begin() as c:
        sid = snap(c)
        rate(c, sid)
    fails(admin, lambda c: rate(c, sid))  # same (snapshot, slot, institution)
    fails(admin, lambda c: rate(c, sid, institution_name="OTHER", institution_name_raw="OTHER"))  # same (snapshot, slot, source row)
    with admin.begin() as c:
        rate(c, sid, slot="12:30")  # the same institution in another slot is a different row
        rate(c, sid, source_row_index=4, institution_name="B", institution_name_raw="B")


@pytest.mark.parametrize("override", [
    {"source_revision_id": 0}, {"source_internal_nid": 0}, {"table_layout": "v3"}, {"rights_status": "cleared"}, {"rights_status": "REVIEW_REQUIRED"},
    {"source_title": ""}, {"source_url": ""}, {"source_rates_datetime_raw": ""}])
def test_snapshot_constraints(db, override):
    fails(db[0], lambda c: snap(c, **override))


def test_snapshot_rights_status_is_exactly_review_required(db):
    admin, _ = db
    with admin.begin() as c:
        sid = snap(c)
        assert scalar(c, "SELECT rights_status FROM bank_fx_snapshots WHERE id=:i", i=sid) == "review_required"


@pytest.mark.parametrize("override", [
    {"slot": "9:30"}, {"slot": "10:00"}, {"source_row_index": 2}, {"source_row_index": -1}, {"retail_source_label": "Board Rates"},
    {"institution_name": ""}, {"institution_name_raw": ""}, {"institution_name": " A BANK"}, {"institution_name": "A BANK "}])
def test_rate_row_constraints(db, override):
    admin, _ = db

    def go(c):
        rate(c, snap(c), **override)
    fails(admin, go)


@pytest.mark.parametrize("column,value,value_text", [
    ("interbank_buy", Decimal("0"), "0.0000"), ("interbank_buy", Decimal("9.91"), "9.90"), ("interbank_sell", Decimal("-1"), "-1.0000"),
    ("retail_buy", Decimal("9.9"), "9.9 "), ("retail_buy", Decimal("9.9"), " 9.9"), ("retail_sell", Decimal("1"), "1"), ("retail_sell", Decimal("1"), "1."),
    ("retail_sell", Decimal("0.5"), ".5"), ("retail_sell", Decimal("1.00001"), "1.00001"), ("interbank_sell", Decimal("10"), "1e1"),
    ("interbank_sell", Decimal("1000"), "1,000.0000"), ("interbank_buy", Decimal("9.9"), "+9.9"), ("retail_buy", Decimal("9.9"), "9.9\n"),
    ("retail_buy", Decimal("9.9"), ""), ("retail_buy", Decimal("9.9"), "NaN")])
def test_text_and_numeric_must_agree_and_follow_the_grammar(db, column, value, value_text):
    admin, _ = db

    def go(c):
        rate(c, snap(c), **{column: value, column + "_text": value_text})
    fails(admin, go)


def test_nan_and_infinity_numerics_are_rejected(db):
    admin, _ = db
    for special in ("'NaN'", "'Infinity'", "'-Infinity'"):
        try:
            with admin.begin() as c:
                sid = snap(c)
                c.execute(text(
                    "INSERT INTO bank_fx_rates (snapshot_id, source_row_index, slot, institution_name_raw, institution_name, is_market_average, "
                    "interbank_buy, interbank_buy_text, interbank_sell, interbank_sell_text, retail_buy, retail_buy_text, retail_sell, retail_sell_text, "
                    f"retail_source_label) VALUES (:s, 3, '09:30', 'A', 'A', false, {special}::numeric, '9.9', 1, '1.0', 1, '1.0', 1, '1.0', 'BOARD RATES')"), {"s": sid})
        except (IntegrityError, DBAPIError):
            continue
        raise AssertionError(f"{special} was accepted")


def test_valid_scales_are_stored_exactly_and_distinguishably(db):
    admin, _ = db
    with admin.begin() as c:
        sid = snap(c)
        rate(c, sid, interbank_buy=Decimal("9.9"), interbank_buy_text="9.9", retail_buy=Decimal("9.9000"), retail_buy_text="9.9000")
        rate(c, sid, slot="12:30", interbank_buy=Decimal("0.0001"), interbank_buy_text="0.0001", retail_sell=Decimal("123456.1234"), retail_sell_text="123456.1234")
        rows = c.execute(text("SELECT interbank_buy_text, retail_buy_text, interbank_buy::text, retail_buy::text FROM bank_fx_rates ORDER BY slot")).all()
    assert rows[0] == ("9.9", "9.9000", "9.9", "9.9000") and rows[1][0] == "0.0001"


def test_market_average_invariant_and_one_per_snapshot_and_slot(db):
    admin, _ = db
    fails(admin, lambda c: rate(c, snap(c), is_market_average=True))  # an ordinary name flagged as the average
    fails(admin, lambda c: rate(c, snap(c), institution_name="MARKET AVERAGE", institution_name_raw="MARKET AVERAGE", is_market_average=False))
    with admin.begin() as c:
        sid = snap(c)
        rate(c, sid, institution_name="Market Average", institution_name_raw="Market Average", is_market_average=True)
        rate(c, sid, slot="12:30", institution_name="MARKET AVERAGE", institution_name_raw="MARKET AVERAGE", is_market_average=True, source_row_index=9)
    # a second, differently spelled average in the same snapshot and slot: blocked by the partial unique index (the names differ in case)
    fails(admin, lambda c: rate(c, sid, institution_name="MARKET AVERAGE", institution_name_raw="MARKET AVERAGE", is_market_average=True, source_row_index=10))
    with admin.connect() as c:
        assert scalar(c, "SELECT COUNT(*) FROM pg_indexes WHERE indexname='bank_fx_rates_one_market_average' AND indexdef LIKE '%WHERE is_market_average%'") == 1


@pytest.mark.parametrize("layout,label,ok", [("v1", "Retail Rates", True), ("v2", "BOARD RATES", True), ("v1", "BOARD RATES", False), ("v2", "Retail Rates", False)])
def test_source_label_must_match_the_snapshot_layout(db, layout, label, ok):
    admin, _ = db

    def go(c):
        rate(c, snap(c, table_layout=layout), retail_source_label=label)
    if ok:
        with admin.begin() as c:
            go(c)
    else:
        fails(admin, go)


def test_source_data_is_insert_only_even_for_the_admin_role(db):
    admin, _ = db
    with admin.begin() as c:
        sid = snap(c)
        rid = rate(c, sid)
    for sql in ("UPDATE bank_fx_snapshots SET source_title='x'", "DELETE FROM bank_fx_snapshots", "UPDATE bank_fx_rates SET interbank_buy_text='9.90'",
                "UPDATE bank_fx_rates SET institution_name_raw='A BANK'", "DELETE FROM bank_fx_rates", "TRUNCATE bank_fx_rates", "TRUNCATE bank_fx_snapshots, bank_fx_rates",
                "TRUNCATE bank_fx_snapshots CASCADE"):
        err = fails(admin, lambda c, s=sql: c.execute(text(s)))  # no-op updates are rejected too: the trigger is row-level BEFORE UPDATE
        assert "insert-only" in str(err.orig), sql
    with admin.connect() as c:
        assert scalar(c, "SELECT COUNT(*) FROM bank_fx_rates") == 1 and scalar(c, "SELECT COUNT(*) FROM bank_fx_snapshots") == 1


def test_canonical_view_is_one_row_per_observation_date_and_selects_node_then_revision(db):
    admin, _ = db
    a, b, c_ = str(uuid4()), str(uuid4()), str(uuid4())
    with admin.begin() as c:
        # 2030-01-02: node A (older) and node B (newer by source_created); B has two revisions
        snap(c, source_record_id=a, source_created="2030-01-02T07:00:00+00:00", source_revision_timestamp="2030-01-02T23:00:00+00:00", source_title="A")
        snap(c, source_record_id=b, source_revision_id=1, source_created="2030-01-02T12:00:00+00:00", source_revision_timestamp="2030-01-02T12:00:08+00:00", source_title="B1")
        snap(c, source_record_id=b, source_revision_id=2, source_created="2030-01-02T12:00:00+00:00", source_revision_timestamp="2030-01-02T14:00:00+00:00", source_title="B2")
        # 2030-01-03: node with a much EARLIER source_created than the 2030-01-02 nodes' ... and a different date
        snap(c, source_record_id=c_, observation_date="2030-01-03", source_created="2030-01-01T00:00:00+00:00", source_title="C")
    with admin.connect() as c:
        rows = c.execute(text("SELECT observation_date, source_title FROM bank_fx_canonical_snapshots ORDER BY observation_date")).all()
    # node B wins on source_created even though A's revision timestamp is newer (node first, then revision), and B2 is B's newest revision
    assert [(str(d), t) for d, t in rows] == [("2030-01-02", "B2"), ("2030-01-03", "C")]
    with admin.connect() as c:  # the newest observation date is chosen from the view, independent of source_created across dates
        assert scalar(c, "SELECT source_title FROM bank_fx_canonical_snapshots ORDER BY observation_date DESC LIMIT 1") == "C"


def test_canonical_tie_breakers(db):
    admin, _ = db
    low, high = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
    with admin.begin() as c:
        # same source_created: larger internal nid wins
        snap(c, source_record_id=low, source_internal_nid=5, source_title="nid5")
        snap(c, source_record_id=high, source_internal_nid=9, source_title="nid9")
        # same created and nid: larger record id wins
        snap(c, observation_date="2030-02-01", source_record_id=low, source_revision_id=11, source_internal_nid=5, source_title="uuid-low")
        snap(c, observation_date="2030-02-01", source_record_id=high, source_revision_id=11, source_internal_nid=5, source_title="uuid-high")
        # same node and revision timestamp: larger revision id wins
        snap(c, observation_date="2030-03-01", source_record_id=low, source_revision_id=3, source_title="r3")
        snap(c, observation_date="2030-03-01", source_record_id=low, source_revision_id=7, source_title="r7")
    with admin.connect() as c:
        got = dict((str(d), t) for d, t in c.execute(text("SELECT observation_date, source_title FROM bank_fx_canonical_snapshots")).all())
    assert got == {"2030-01-02": "nid9", "2030-02-01": "uuid-high", "2030-03-01": "r7"}


def test_indexes_exist(db):
    admin, _ = db
    with admin.connect() as c:
        names = {r[0] for r in c.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename LIKE 'bank_fx_%'"))}
    assert {"bank_fx_snapshots_date_idx", "bank_fx_snapshots_node_revision_key", "bank_fx_rates_identity_key", "bank_fx_rates_row_key",
            "bank_fx_rates_one_market_average", "bank_fx_rates_snapshot_idx"} <= names


def test_migration_is_idempotent_and_changes_no_data(db):
    admin, role = db
    with admin.begin() as c:
        sid = snap(c)
        rate(c, sid)
    with admin.begin() as c:
        c.execute(text(f"SET LOCAL zm.app_role = '{role}'"))
        c.execute(text(M017.read_text()))
    with admin.connect() as c:
        assert scalar(c, "SELECT COUNT(*) FROM bank_fx_snapshots") == 1 and scalar(c, "SELECT COUNT(*) FROM bank_fx_rates") == 1


def test_migration_017_fails_closed_without_a_usable_application_role(db):
    admin, role = db
    with pytest.raises(DBAPIError, match="does not exist"):
        with admin.begin() as c:
            c.execute(text("SET LOCAL zm.app_role = 'zm_t_no_such_role_x'"))
            c.execute(text(M017.read_text()))
    with pytest.raises(DBAPIError, match="must differ from the admin role"):
        with admin.begin() as c:
            c.execute(text(f"SET LOCAL zm.app_role = '{scalar(c, 'SELECT current_user')}'"))
            c.execute(text(M017.read_text()))


def test_functions_pin_the_search_path_and_are_security_invoker(db):
    admin, _ = db
    with admin.connect() as c:
        rows = c.execute(text("SELECT proname, prosecdef, proconfig FROM pg_proc WHERE proname LIKE 'bank\\_fx\\_%'")).all()
    assert {r[0] for r in rows} == {"bank_fx_rate_matches", "bank_fx_reject_change", "bank_fx_check_label"}
    assert all(not r[1] and r[2] == ["search_path=pg_catalog, pg_temp"] for r in rows)
