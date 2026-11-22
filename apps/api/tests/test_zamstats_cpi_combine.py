"""ZamStats CPI reconciliation: overlap, formatting-only differences, conflicts and regime rejection (synthetic, offline)."""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from app.services import zamstats_cpi_source as src
from app.services.zamstats_cpi_combine import reconcile, require_resolved
from app.services.zamstats_cpi_models import ParsedPublication, ParsedTable, SourceObservation, ZamstatsCpiError, parse_index_number

D = date
P = src.PUBLICATIONS
A, B = P[3], P[4]  # z164 (owns index 2016-01..2016-12 in table "Consumer Index Numbers...") and an unrelated publication


def obs(key: str, table: str, month: date, text: str, *, series=("index", "all_items"), canonical=True, regime="2009=100") -> SourceObservation:
    return SourceObservation(month, series[0], series[1], parse_index_number(text), regime, key, table, 29, 1, "vertical_labelled", canonical)


def pub(key: str, *observations: SourceObservation) -> ParsedPublication:
    table = ParsedTable("t", 1, "vertical_labelled", observations[0].month, observations[-1].month, tuple(observations))
    return ParsedPublication(key, D(2016, 12, 1), (table,))


OWN_TABLE = P[3].tables[0].table_id  # the pinned canonical table of zam-cpi-2016-12 for index 2016
M = D(2016, 6, 1)


def test_single_canonical_cell_resolves_with_its_provenance():
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "186.50"))])
    assert r.conflicts == () and len(r.resolved) == 1
    x = r.resolved[0]
    assert x.value == Decimal("186.50") and x.owner.manifest_key == A.key and x.text_variants == ()


def test_identical_value_and_identical_text_in_two_publications_is_an_accepted_overlap():
    other = P[2]  # z152 also prints 2016? no: use a validation cell with the same text
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "186.50")),
                   pub(other.key, obs(other.key, "validation-table", M, "186.50", canonical=False))])
    assert r.conflicts == () and len(r.resolved[0].evidence) == 2 and r.resolved[0].text_variants == ()


def test_same_value_different_text_is_formatting_only_evidence_not_a_conflict():
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "274.20")),
                   pub("other", obs("other", "validation-table", M, "274.2", canonical=False))])
    assert r.conflicts == () and r.resolved[0].text_variants == ("274.2", "274.20")  # both source strings preserved, text differs
    assert Decimal("274.20") == Decimal("274.2")
    assert r.resolved[0].owner.number.source_value_text == "274.20"  # the owner's own text stays the canonical source text


def test_different_decimal_across_publications_is_a_conflict_and_fails_closed():
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "186.50")), pub("other", obs("other", "validation-table", M, "186.60", canonical=False))])
    assert r.resolved == () and len(r.conflicts) == 1
    c = r.conflicts[0]
    assert (c.month, c.series) == (M, "index_all_items") and not c.same_publication and len(c.evidence) == 2
    with pytest.raises(ZamstatsCpiError, match="conflicting source cells") as info:
        require_resolved(r)
    assert "index_all_items 2016-06" in str(info.value) and "186.5" not in str(info.value) and "186.6" not in str(info.value)


def test_same_publication_conflicting_tables_are_preserved_and_fail_closed():
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "127.32"), obs(A.key, "Consumer Price Indices (2009=100)", M, "127.30", canonical=False))])
    assert r.resolved == () and r.conflicts[0].same_publication
    assert sorted(o.number.source_value_text for o in r.conflicts[0].evidence) == ["127.30", "127.32"]  # neither is preferred or averaged
    with pytest.raises(ZamstatsCpiError, match="within publications"):
        require_resolved(r)


def test_conflicting_cells_are_never_averaged_rounded_or_chosen():
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "100.00")), pub("o", obs("o", "x", M, "100.02", canonical=False))])
    assert r.resolved == () and not any(x.value in (Decimal("100.01"), Decimal("100.00"), Decimal("100.02")) for x in r.resolved)


def test_old_regime_evidence_is_rejected_outright():
    with pytest.raises(ZamstatsCpiError, match="2009=100"):
        reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "186.50", regime="1994=100"))])


def test_rows_before_the_phase_1_boundary_are_not_logical_observations():
    early = D(2009, 12, 1)
    r = reconcile([pub(A.key, obs(A.key, OWN_TABLE, early, "95.00"))])
    assert r.resolved == () and r.conflicts == ()


def test_a_logical_observation_without_exactly_one_pinned_owner_is_an_error_not_a_guess():
    with pytest.raises(ZamstatsCpiError, match="0 canonical source cells"):
        reconcile([pub("o", obs("o", "validation-table", M, "186.50", canonical=False))])
    with pytest.raises(ZamstatsCpiError, match="2 canonical source cells"):
        reconcile([pub(A.key, obs(A.key, OWN_TABLE, M, "186.50"), obs(A.key, OWN_TABLE, M, "186.50"))])


def test_rate_series_reconcile_with_their_own_grammar_and_never_mix_with_index_series():
    from app.services.zamstats_cpi_models import parse_rate_number
    cell = SourceObservation(M, "monthly_inflation", "all_items", parse_rate_number("(0.0)"), "2009=100", A.key,
                             A.tables[2].table_id, 9, 1, "horizontal_13", True)
    r = reconcile([pub(A.key, cell)])
    assert r.resolved[0].series == "monthly_inflation_all_items" and r.resolved[0].owner.number.source_negative
    # an index cell and a rate cell for the same month are different series, never compared with each other
    both = reconcile([pub(A.key, cell, obs(A.key, OWN_TABLE, M, "186.50"))])
    assert {x.series for x in both.resolved} == {"monthly_inflation_all_items", "index_all_items"} and both.conflicts == ()
