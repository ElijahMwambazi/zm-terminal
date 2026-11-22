"""ZamStats CPI reconciliation: overlap, formatting-only differences, conflicts and regime rejection (synthetic, offline)."""

import dataclasses
import re
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
    r = reconcile([pub("o", obs("o", "validation-table", M, "186.50", canonical=False))])
    assert r.resolved == () and r.unowned == ((M, "index_all_items"),)  # never resolved from validation evidence alone
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


# ================================================================== gates: manifest coverage / evidence completeness / resolution
from app.services import zamstats_cpi_combine as cmb  # noqa: E402
from app.services.zamstats_cpi_table import VERTICAL_LABELLED  # noqa: E402

_IDX_ALL = (("index", "all_items", True),)
_IDX_ALL_VALIDATION = (("index", "all_items", False),)


def spec(table_id: str, first: date, last: date, *, canonical: bool, cf=None, cl=None):
    return TableSpecAlias(table_id, VERTICAL_LABELLED, 1, "t", first, last,
                          _IDX_ALL if canonical else _IDX_ALL_VALIDATION, canonical_first=cf, canonical_last=cl, header_regex="h")


from app.services.zamstats_cpi_table import TableSpec as TableSpecAlias  # noqa: E402

M1, M2, M3, M4 = D(2020, 1, 1), D(2020, 2, 1), D(2020, 3, 1), D(2020, 4, 1)


def syn_pub(key: str, *tables) -> src.Publication:
    return dataclasses.replace(P[0], key=key, url=f"https://www.zamstats.gov.zm/wp-content/uploads/{key}.pdf",
                               sha256=hashlib_sha(key), tables=tuple(tables), layout_families=(VERTICAL_LABELLED,), base_pages=(),
                               role="canonical", pdf_pages=10)


def hashlib_sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


# S1 owns Jan-Mar 2020; S2 owns only Apr 2020 but prints Feb-Apr as validation evidence; S3 is unrelated (2022).
S1 = syn_pub("syn-1", spec("T1", M1, M3, canonical=True, cf=M1, cl=M3))
S2 = syn_pub("syn-2", spec("V2", M2, M4, canonical=False),
             dataclasses.replace(spec("T2", M4, M4, canonical=True, cf=M4, cl=M4)))
S3 = syn_pub("syn-3", spec("T3", D(2022, 1, 1), D(2022, 2, 1), canonical=True, cf=D(2022, 1, 1), cl=D(2022, 2, 1)))
SYN = (S1, S2, S3)


def parsed(manifest_pub: src.Publication, values: dict) -> ParsedPublication:
    """A ParsedPublication for every table of `manifest_pub`; values: {table_id: {month: text}}."""
    tables = []
    for t in manifest_pub.tables:
        cells = tuple(obs(manifest_pub.key, t.table_id, m, text, canonical=t.columns[0][2]) for m, text in values[t.table_id].items())
        tables.append(ParsedTable(t.table_id, t.pdf_page, t.family, t.first_month, t.last_month, cells))
    return ParsedPublication(manifest_pub.key, manifest_pub.reference_month, tuple(tables))


AGREE = {S1: {"T1": {M1: "10.00", M2: "11.00", M3: "12.00"}},
         S2: {"V2": {M2: "11.00", M3: "12.00", M4: "13.00"}, "T2": {M4: "13.00"}}}


def evidence(overrides=None):
    out = []
    for pub, values in AGREE.items():
        values = {k: dict(v) for k, v in values.items()}
        for (key, table), cells in (overrides or {}).items():
            if key == pub.key:
                values[table].update(cells)
        out.append(parsed(pub, values))
    return out


def test_manifest_scope_and_required_evidence_are_driven_by_the_manifest():
    scope = cmb.manifest_scope(SYN)
    assert scope == {("index_all_items", m) for m in (M1, M2, M3, M4, D(2022, 1, 1), D(2022, 2, 1))}
    assert cmb.required_evidence(scope, SYN) == {("syn-1", "T1"), ("syn-2", "V2"), ("syn-2", "T2"), ("syn-3", "T3")}
    only_march = cmb.scope_for(["index_all_items"], M3, M3, SYN)
    assert cmb.required_evidence(only_march, SYN) == {("syn-1", "T1"), ("syn-2", "V2")}  # S3 and S2's T2 are not required


def test_empty_evidence_fails_completeness_for_a_non_empty_scope_and_names_the_missing_tables():
    with pytest.raises(ZamstatsCpiError, match="parsed evidence is incomplete") as info:
        cmb.validate_evidence_completeness([], publications=SYN)
    assert "syn-1 / T1" in str(info.value) and "syn-3 / T3" in str(info.value) and not re.search(r"\d+\.\d", str(info.value))
    with pytest.raises(ZamstatsCpiError, match="incomplete"):
        cmb.require_canonical_ready([], publications=SYN)


def test_an_empty_requested_scope_is_an_error():
    with pytest.raises(ZamstatsCpiError, match="scope is empty"):
        cmb.validate_evidence_completeness([], frozenset(), SYN)


def test_empty_reconciliation_is_not_resolved():
    with pytest.raises(ZamstatsCpiError, match="nothing was resolved"):
        require_resolved(reconcile([]))


def test_one_publication_only_fails_when_required_validation_evidence_is_missing():
    s1_only = [evidence()[0]]
    scope = cmb.scope_for(["index_all_items"], M2, M3, SYN)
    with pytest.raises(ZamstatsCpiError, match="syn-2 / V2"):
        cmb.validate_evidence_completeness(s1_only, scope, SYN)
    # a diagnostic reconciliation of the same partial evidence "looks" resolved: exactly the trap the gate closes
    assert cmb.reconcile(s1_only, SYN).conflicts == () and len(cmb.reconcile(s1_only, SYN).resolved) == 3


def test_missing_table_inside_a_supplied_publication_is_detected():
    half = parsed(S2, {"V2": {M2: "11.00", M3: "12.00", M4: "13.00"}, "T2": {M4: "13.00"}})
    half = ParsedPublication(half.manifest_key, half.reference_month, half.tables[:1])  # V2 only
    with pytest.raises(ZamstatsCpiError, match="syn-2 / T2"):
        cmb.validate_evidence_completeness([evidence()[0], half], cmb.scope_for(["index_all_items"], M2, M4, SYN), SYN)


def test_all_required_publications_pass_completeness_and_unrelated_ones_are_not_required():
    scope = cmb.scope_for(["index_all_items"], M1, M4, SYN)
    required = cmb.validate_evidence_completeness(evidence(), scope, SYN)
    assert required == {("syn-1", "T1"), ("syn-2", "V2"), ("syn-2", "T2")}  # syn-3 was never supplied and never needed


def test_a_supplied_table_must_be_the_manifest_table():
    wrong = parsed(S1, {"T1": {M1: "10.00", M2: "11.00", M3: "12.00"}})
    t = wrong.tables[0]
    wrong = ParsedPublication(wrong.manifest_key, wrong.reference_month, (dataclasses.replace(t, last_month=M4),))
    with pytest.raises(ZamstatsCpiError, match="does not match the manifest"):
        cmb.validate_evidence_completeness([wrong], cmb.scope_for(["index_all_items"], M1, M3, SYN), SYN)
    unknown = ParsedPublication("nope", M1, (dataclasses.replace(t, source_table="X"),))
    with pytest.raises(ZamstatsCpiError, match="not on the manifest"):
        cmb.validate_evidence_completeness([unknown], cmb.scope_for(None, M1, M3, SYN), SYN)


def test_completeness_does_not_imply_resolution():
    conflicted = evidence({("syn-2", "V2"): {M3: "12.50"}})
    scope = cmb.scope_for(["index_all_items"], M1, M4, SYN)
    cmb.validate_evidence_completeness(conflicted, scope, SYN)  # complete...
    result = cmb.reconcile(conflicted, SYN)
    assert [(c.month) for c in result.conflicts] == [M3]  # ...but not resolved
    with pytest.raises(ZamstatsCpiError, match="conflicting source cells"):
        require_resolved(result)
    with pytest.raises(ZamstatsCpiError, match="index_all_items 2020-03"):
        cmb.require_canonical_ready(conflicted, scope, SYN)


def test_a_scope_of_fully_resolved_observations_becomes_canonical_ready_despite_conflicts_elsewhere():
    conflicted = evidence({("syn-2", "V2"): {M3: "12.50"}})
    ok_scope = cmb.scope_for(["index_all_items"], M1, M2, SYN)
    ready = cmb.require_canonical_ready(conflicted, ok_scope, SYN)
    assert [(r.month, r.value) for r in ready] == [(M1, Decimal("10.00")), (M2, Decimal("11.00"))]
    full = cmb.require_canonical_ready(evidence(), cmb.scope_for(["index_all_items"], M1, M4, SYN), SYN)
    assert [r.month for r in full] == [M1, M2, M3, M4] and all(r.owner.canonical_for_table for r in full)


def test_ready_requires_every_scope_observation_to_be_resolved():
    # evidence for M4 is supplied by the validation table only; the owner table T2 is supplied but empty of that month
    partial = evidence()
    p2 = partial[1]
    t2 = dataclasses.replace(p2.tables[1], observations=())
    partial[1] = ParsedPublication(p2.manifest_key, p2.reference_month, (p2.tables[0], t2))
    with pytest.raises(ZamstatsCpiError, match="unresolved"):
        cmb.require_canonical_ready(partial, cmb.scope_for(["index_all_items"], M4, M4, SYN), SYN)


def test_partial_reconcile_reports_unowned_evidence_instead_of_resolving_it():
    only_validation = ParsedPublication("syn-2", M4, parsed(S2, AGREE[S2]).tables[:1])
    result = cmb.reconcile([only_validation], SYN)
    assert result.resolved == () and (M2, "index_all_items") in result.unowned


def test_the_real_manifest_scope_and_requirements():
    scope = cmb.manifest_scope()
    assert len(scope) == 1366
    assert cmb.required_evidence(scope) == {(p.key, t.table_id) for p in P for t in p.tables}
    index_food_2021 = cmb.scope_for(["index_food"], D(2021, 1, 1), D(2021, 12, 1))
    assert cmb.required_evidence(index_food_2021) == {("zam-cpi-2023-12", P[10].tables[0].table_id), ("zam-cpi-2025-12", P[12].tables[0].table_id)}
