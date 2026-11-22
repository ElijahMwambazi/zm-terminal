"""Bank FX conflict-aware persistence on a guarded disposable PostgreSQL database; SYNTHETIC data, no network."""

import copy
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

import bank_fx_builders as b
from app.services.bank_fx_store import BankFxConflictError, save_bank_fx_snapshots
from app.services.boz_bank_fx import parse_bank_fx_document
from bank_fx_db import bank_fx_engine, scalar

T1 = datetime(2030, 1, 3, 8, 0, tzinfo=timezone.utc)
T2 = datetime(2030, 1, 4, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def engine(disposable_database):
    with bank_fx_engine(disposable_database) as (admin, _role):
        yield admin


def parsed(*nodes, at=T1, url="https://example.test/page"):
    return parse_bank_fx_document(b.document(*nodes), retrieved_at=at, source_url=url)


def counts(engine):
    with engine.connect() as c:
        return scalar(c, "SELECT COUNT(*) FROM bank_fx_snapshots"), scalar(c, "SELECT COUNT(*) FROM bank_fx_rates")


def rows(engine, sql):
    with engine.connect() as c:
        return c.execute(text(sql)).all()


def test_first_save_inserts_the_snapshot_and_every_published_rate_row_exactly(engine):
    result = save_bank_fx_snapshots(parsed(b.node()), engine=engine)
    assert (result.inserted, result.already_present) == (1, 0)
    assert counts(engine) == (1, 9)  # 3 institutions x 3 slots
    (row,) = rows(engine, "SELECT observation_date::text, source_rates_datetime_raw, table_layout, rights_status, source_revision_id FROM bank_fx_snapshots")
    assert row == ("2030-01-02", "2030-01-02T15:36:15+02:00", "v2", "review_required", 22)
    sample = rows(engine, "SELECT institution_name_raw, institution_name, source_row_index, interbank_buy_text, interbank_buy::text, retail_source_label "
                          "FROM bank_fx_rates WHERE slot='09:30' ORDER BY source_row_index")
    assert [r[2] for r in sample] == [3, 4, 5] and sample[0][:2] == ("ALPHA TEST BANK PLC    ", "ALPHA TEST BANK PLC")
    assert sample[0][3] == sample[0][4] and sample[0][5] == "BOARD RATES"  # text and numeric agree; the label is the source's own


def test_exact_repeat_is_a_noop_and_a_later_retrieval_time_or_url_alone_changes_nothing(engine):
    save_bank_fx_snapshots(parsed(b.node()), engine=engine)
    before = rows(engine, "SELECT retrieved_at, source_url FROM bank_fx_snapshots")
    result = save_bank_fx_snapshots(parsed(b.node(), at=T2, url="https://example.test/other"), engine=engine)
    assert (result.inserted, result.already_present) == (0, 1)
    assert counts(engine) == (1, 9) and rows(engine, "SELECT retrieved_at, source_url FROM bank_fx_snapshots") == before  # never updated


def test_same_revision_numeric_change_is_a_conflict_and_identical_content_is_not(engine):
    save_bank_fx_snapshots(parsed(b.node(banks=b.default_banks())), engine=engine)
    assert save_bank_fx_snapshots(parsed(b.node(banks=b.default_banks())), engine=engine).already_present == 1
    banks = b.default_banks()
    banks[0][1:5] = ["10.001", "10.0002", "10.0003", "10.0004"]  # 10.001 differs from the stored 10.0001
    with pytest.raises(BankFxConflictError, match="revision 22"):
        save_bank_fx_snapshots(parsed(b.node(banks=banks)), engine=engine)


def test_9_9_to_9_9000_on_the_same_revision_conflicts_even_though_the_numbers_are_equal(engine):
    def banks(value):
        rows_ = b.default_banks()
        rows_[0][1] = value
        return rows_

    save_bank_fx_snapshots(parsed(b.node(banks=banks("9.9"))), engine=engine)
    with pytest.raises(BankFxConflictError):
        save_bank_fx_snapshots(parsed(b.node(banks=banks("9.9000"))), engine=engine)
    assert rows(engine, "SELECT interbank_buy_text FROM bank_fx_rates WHERE slot='09:30' ORDER BY source_row_index LIMIT 1") == [("9.9",)]


def mutate(fn):
    def go(banks):
        fn(banks)
        return banks
    return go


@pytest.mark.parametrize("name,node_kwargs,bank_change", [
    ("title", {"title": "CHANGED TITLE"}, None),
    ("rates datetime raw string", {"rates_date": "2030-01-02T15:36:15.000+02:00"}, None),
    ("observation date", {"rates_date": "2030-01-03T15:36:15+02:00"}, None),
    ("created", {"created": "2030-01-02T13:36:39+00:00"}, None),
    ("changed", {"changed": "2030-01-02T13:36:47+00:00"}, None),
    ("revision timestamp", {"revision": "2030-01-02T13:36:48+00:00"}, None),
    ("nid", {"nid": 12}, None),
    ("layout", {"layout": "v1"}, None),
    ("raw institution text", {}, lambda banks: banks[0].__setitem__(0, "ALPHA TEST BANK PLC")),
    ("normalised identity", {}, lambda banks: banks[0].__setitem__(0, "ALPHA RENAMED BANK PLC")),
    ("row order", {}, lambda banks: banks.insert(0, banks.pop(1))),
    ("rate value", {}, lambda banks: banks[1].__setitem__(7, "99.9999")),
    ("slot disappears", {}, lambda banks: banks[0].__setitem__(slice(9, 13), [""] * 4)),
    ("slot appears", {}, lambda banks: None),
    ("market average flag", {}, lambda banks: banks[2].__setitem__(0, "NOT AN AVERAGE")),
])
def test_any_same_revision_source_difference_is_a_conflict_and_rolls_back(engine, name, node_kwargs, bank_change):
    base_banks = b.default_banks("v2", published=2 if name == "slot appears" else 3)
    save_bank_fx_snapshots(parsed(b.node(banks=base_banks)), engine=engine)
    before = (counts(engine), rows(engine, "SELECT * FROM bank_fx_rates ORDER BY id"))
    banks = b.default_banks("v2", published=3 if name == "slot appears" else 3)
    if bank_change:
        bank_change(banks)
    kwargs = dict(node_kwargs)
    if kwargs.get("layout") == "v1":
        banks = b.default_banks("v1")  # a v1 node must carry v1 headers; names differ too: still the same node + revision
    other = b.node(banks=banks, **kwargs)
    if name == "market average flag":
        banks.append(b.bank_row("MARKET AVERAGE", (b.quartet(10),) * 3))
        other = b.node(banks=banks)
    with pytest.raises(BankFxConflictError):
        save_bank_fx_snapshots(parsed(other), engine=engine)
    assert (counts(engine), rows(engine, "SELECT * FROM bank_fx_rates ORDER BY id")) == before


def test_conflict_message_names_only_date_node_and_revision_never_rates(engine):
    save_bank_fx_snapshots(parsed(b.node()), engine=engine)
    banks = b.default_banks()
    banks[0][1] = "77.7777"
    with pytest.raises(BankFxConflictError) as raised:
        save_bank_fx_snapshots(parsed(b.node(banks=banks)), engine=engine)
    message = str(raised.value)
    assert b.NODE_UUID in message and "2030-01-02" in message and "22" in message and "77.7777" not in message


def test_batch_conflict_rolls_back_every_write_including_earlier_new_snapshots(engine):
    save_bank_fx_snapshots(parsed(b.node()), engine=engine)
    new = b.node(uuid="00000000-0000-4000-8000-0000000000a2", nid=12, vid=30)
    banks = b.default_banks()
    banks[0][1] = "55.5555"
    changed = b.node(banks=banks)  # conflicts with the stored node 22
    with pytest.raises(BankFxConflictError):
        save_bank_fx_snapshots(parsed(new, changed), engine=engine)
    assert counts(engine) == (1, 9)  # the new snapshot that preceded the conflict was rolled back


def test_new_revision_with_identical_values_is_a_separate_immutable_snapshot(engine):
    save_bank_fx_snapshots(parsed(b.node(vid=22)), engine=engine)
    result = save_bank_fx_snapshots(parsed(b.node(vid=23, revision="2030-01-02T14:00:00+00:00")), engine=engine)
    assert (result.inserted, result.already_present) == (1, 0) and counts(engine) == (2, 18)
    assert {r[0] for r in rows(engine, "SELECT source_revision_id FROM bank_fx_snapshots")} == {22, 23}


def test_multiple_nodes_for_one_date_are_stored_separately_and_batches_are_idempotent(engine):
    nodes = [b.node(uuid=f"00000000-0000-4000-8000-0000000000{i}0", nid=i, vid=i * 10, created=f"2030-01-02T0{i}:00:00+00:00",
                    banks=b.default_banks("v2", published=i)) for i in (1, 2, 3)]
    assert save_bank_fx_snapshots(parsed(*nodes), engine=engine).inserted == 3
    assert save_bank_fx_snapshots(parsed(*nodes), engine=engine).already_present == 3
    assert counts(engine) == (3, 3 + 6 + 9)  # 1, 2 and 3 published slots, 3 institutions each


def test_duplicate_identity_inside_one_batch_is_handled_by_the_same_rule(engine):
    node = b.node()
    snapshots = parsed(node) + parsed(node)  # the parser forbids this inside one document; the store still compares
    assert save_bank_fx_snapshots(snapshots, engine=engine).already_present == 1
    banks = b.default_banks()
    banks[0][1] = "88.8888"
    with pytest.raises(BankFxConflictError):
        save_bank_fx_snapshots(parsed(node) + parsed(b.node(banks=banks)), engine=engine)


def test_canonical_view_picks_the_newest_node_and_never_merges_rows_across_snapshots(engine):
    full = b.node(uuid="00000000-0000-4000-8000-0000000000a1", nid=1, vid=10, created="2030-01-02T07:00:00+00:00")
    later_partial = b.node(uuid="00000000-0000-4000-8000-0000000000a2", nid=2, vid=20, created="2030-01-02T12:00:00+00:00",
                           banks=b.default_banks("v2", published=2))
    save_bank_fx_snapshots(parsed(full, later_partial), engine=engine)
    (canonical,) = rows(engine, "SELECT snapshot_id, source_internal_nid FROM bank_fx_canonical_snapshots")
    assert canonical[1] == 2
    slots = {r[0] for r in rows(engine, f"SELECT slot FROM bank_fx_rates WHERE snapshot_id={canonical[0]}")}
    assert slots == {"09:30", "12:30"}  # 15:30 exists in the OLDER node but is not inherited by the canonical snapshot
    assert len(rows(engine, "SELECT 1 FROM bank_fx_rates WHERE slot='15:30'")) == 3  # it is still stored, as provenance of the older snapshot


def test_canonical_view_picks_a_nodes_newest_revision_and_does_not_use_cross_date_created_order(engine):
    r1 = b.node(vid=10, revision="2030-01-02T12:00:00+00:00")
    r2 = b.node(vid=11, revision="2030-01-02T13:00:00+00:00", banks=b.default_banks("v2", published=2))
    old_created_later_date = b.node(uuid="00000000-0000-4000-8000-0000000000a9", nid=9, vid=90, rates_date="2030-01-03T10:00:00+02:00",
                                    created="2029-12-31T00:00:00+00:00")  # an EARLIER source_created for the NEWER observation date
    save_bank_fx_snapshots(parsed(r1, r2, old_created_later_date), engine=engine)
    got = {str(d): v for d, v in rows(engine, "SELECT observation_date, source_revision_id FROM bank_fx_canonical_snapshots")}
    assert got == {"2030-01-02": 11, "2030-01-03": 90}
    (latest,) = rows(engine, "SELECT observation_date::text FROM bank_fx_canonical_snapshots ORDER BY observation_date DESC LIMIT 1")
    assert latest == ("2030-01-03",)  # future /latest = newest observation date, not the greatest source_created


def test_rows_keep_source_order_and_only_published_slots_have_rows(engine):
    save_bank_fx_snapshots(parsed(b.node(banks=b.default_banks("v2", published=1))), engine=engine)
    got = rows(engine, "SELECT source_row_index, slot FROM bank_fx_rates ORDER BY source_row_index")
    assert got == [(3, "09:30"), (4, "09:30"), (5, "09:30")]


def test_v1_snapshot_stores_its_own_source_label(engine):
    save_bank_fx_snapshots(parsed(b.node(layout="v1")), engine=engine)
    assert {r[0] for r in rows(engine, "SELECT retail_source_label FROM bank_fx_rates")} == {"Retail Rates"}
    assert rows(engine, "SELECT table_layout FROM bank_fx_snapshots") == [("v1",)]


def blank_row(name, weight=None):
    return b.bank_row(name, (None, None, None))


def test_same_revision_change_in_a_wholly_blank_institution_is_a_conflict_and_rolls_back(engine):
    banks = b.default_banks()
    save_bank_fx_snapshots(parsed(b.node(banks=banks[:2] + [blank_row("EXAMPLE BANK A")] + banks[2:])), engine=engine)
    before = counts(engine)
    other = b.node(uuid="00000000-0000-4000-8000-0000000000b2", vid=23)
    with pytest.raises(BankFxConflictError):  # same identity, only the blank institution's raw name differs; a new revision in the same batch
        save_bank_fx_snapshots(parsed(other, b.node(banks=banks[:2] + [blank_row("EXAMPLE BANK B")] + banks[2:])), engine=engine)
    assert counts(engine) == before  # whole batch rolled back, including the new node
    same = save_bank_fx_snapshots(parsed(b.node(banks=banks[:2] + [blank_row("EXAMPLE BANK A")] + banks[2:]), at=T2), engine=engine)
    assert (same.inserted, same.already_present) == (0, 1)
