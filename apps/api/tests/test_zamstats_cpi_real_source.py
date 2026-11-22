"""OPTIONAL real-source fidelity gate for the ZamStats CPI manifest.

- ZM_ZAMSTATS_CPI_PDF_DIR unset: the whole suite is skipped (not enabled).
- ZM_ZAMSTATS_CPI_PDF_DIR set: the suite is explicitly enabled and REQUIRES the complete set of manifest PDFs under their manifest
  file names; a missing file or directory FAILS (it is never skipped), in the per-publication tests and the aggregate ones alike.
No network request is made, no PDF is committed and the normal offline suite never needs the files (rights status
review_required: the files stay local).
"""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import zamstats_cpi_combine as cmb
from app.services import zamstats_cpi_source as src
from app.services.zamstats_cpi_models import ZamstatsCpiError
from zamstats_cpi_real_gate import ENV_VAR, gate_state

DIRECTORY = os.environ.get(ENV_VAR)
STATE, MISSING = gate_state(DIRECTORY, [p.filename for p in src.PUBLICATIONS])
pytestmark = pytest.mark.skipif(STATE == "skip", reason=f"set {ENV_VAR} to a folder of the vetted ZamStats PDFs to enable")
D = date

# The ten logical observations whose source cells disagree in the vetted publications. They are preserved as conflicts and are NOT
# resolved by this foundation (no preferred value is chosen). Any change to this set means the source or the manifest changed.
KNOWN_CONFLICTS = {
    ("index_all_items", D(2013, 2, 1)), ("index_all_items", D(2014, 2, 1)), ("index_all_items", D(2014, 8, 1)),
    ("monthly_inflation_all_items", D(2014, 12, 1)), ("index_all_items", D(2017, 3, 1)), ("index_all_items", D(2018, 5, 1)),
    ("index_all_items", D(2019, 2, 1)), ("index_all_items", D(2022, 10, 1)), ("monthly_inflation_all_items", D(2022, 12, 1)),
    ("index_food", D(2023, 8, 1)),
}
SAME_PUBLICATION = {("index_all_items", D(2013, 2, 1)), ("index_all_items", D(2014, 2, 1)), ("index_all_items", D(2014, 8, 1)),
                    ("index_all_items", D(2022, 10, 1))}
_CACHE: dict[str, object] = {}


def load(publication: src.Publication):
    """Parse one manifest PDF (verified against its pinned size and SHA-256). A missing file FAILS the test."""
    if publication.key in _CACHE:
        return _CACHE[publication.key]
    path = Path(DIRECTORY) / publication.filename
    if not path.is_file():
        pytest.fail(f"required manifest PDF is missing: {publication.filename}")
    try:
        _CACHE[publication.key] = src.parse_publication(path.read_bytes(), publication)
    except ZamstatsCpiError as exc:
        pytest.fail(str(exc))
    return _CACHE[publication.key]


def load_all():
    return [load(p) for p in src.PUBLICATIONS]


def test_the_complete_manifest_pdf_set_is_present():
    assert STATE == "run", f"missing manifest PDFs: {MISSING}"


@pytest.mark.parametrize("publication", src.PUBLICATIONS, ids=lambda p: p.key)
def test_each_vetted_pdf_matches_its_hash_size_pages_and_pinned_table_windows(publication):
    result = load(publication)
    assert [(t.source_table, t.pdf_page, t.first_month, t.last_month) for t in result.tables] == [
        (t.table_id, t.pdf_page, t.first_month, t.last_month) for t in publication.tables]
    assert all(isinstance(o.number.value, Decimal) for o in result.observations)


def test_known_source_edge_values():
    by_key = {p.key: p for p in src.PUBLICATIONS}
    oct_2026 = {(o.series, o.month): o for o in load(by_key["zam-cpi-2026-10"]).observations}
    assert oct_2026[("monthly_inflation_all_items", D(2026, 10, 1))].number.source_value_text == "0.7"
    assert oct_2026[("monthly_inflation_non_food", D(2026, 10, 1))].number.source_value_text == "1.2"  # the wrapped Non-Food row
    assert oct_2026[("annual_inflation_all_items", D(2026, 10, 1))].number.source_value_text == "6.3"
    assert oct_2026[("index_all_items", D(2026, 10, 1))].number.source_value_text == "564.62"
    z140 = {(o.series, o.month): o for o in load(by_key["zam-cpi-2014-12"]).observations}
    zero = z140[("monthly_inflation_all_items", D(2013, 10, 1))].number  # printed "(0.0)"
    assert zero.source_value_text == "(0.0)" and zero.value == 0 and zero.source_negative


def test_three_gates_manifest_coverage_evidence_completeness_and_resolution():
    assert src.manifest_problems() == []  # 1. manifest coverage: complete
    parsed = load_all()
    required = cmb.validate_evidence_completeness(parsed)  # 2. parsed evidence completeness: complete
    assert {(key, table) for key, table, *_ in required} == {(p.key, t.table_id) for p in src.PUBLICATIONS for t in p.tables}
    assert len(required) > 1366  # cell-level: every owner cell plus every validation-overlap cell
    result = cmb.reconcile(parsed)  # 3. canonical resolution: INCOMPLETE (ten source conflicts)
    assert len(result.conflicts) == 10
    with pytest.raises(ZamstatsCpiError, match="conflicting source cells"):
        cmb.require_resolved(result)
    with pytest.raises(ZamstatsCpiError, match="conflicting source cells"):
        cmb.require_canonical_ready(parsed)


def _without_cells(parsed, doomed):
    """The same parsed batch without the given observations (tables stay present)."""
    out = []
    for p in parsed:
        tables = [t.__class__(t.source_table, t.pdf_page, t.layout_family, t.first_month, t.last_month,
                              tuple(o for o in t.observations if id(o) not in doomed), t.duplicate_rows) for t in p.tables]
        out.append(p.__class__(p.manifest_key, p.reference_month, tuple(tables)))
    return out


def test_cell_level_completeness_on_the_real_evidence_mutation_and_restore():
    parsed = load_all()
    cmb.validate_evidence_completeness(parsed)  # complete
    # December 2014 monthly inflation: remove only the later publication's validation observation
    scope = cmb.scope_for(["monthly_inflation_all_items"], D(2014, 12, 1), D(2014, 12, 1))
    doomed = {id(o) for p in parsed if p.manifest_key == "zam-cpi-2015-12" for o in p.observations
              if o.series == "monthly_inflation_all_items" and o.month == D(2014, 12, 1)}
    assert len(doomed) == 1
    mutated = _without_cells(parsed, doomed)
    for gate in (lambda: cmb.validate_evidence_completeness(mutated, scope), lambda: cmb.require_canonical_ready(mutated, scope)):
        with pytest.raises(ZamstatsCpiError, match="incomplete"):
            gate()
    with pytest.raises(ZamstatsCpiError, match="conflicting source cells"):  # restored: refused only because of the real conflict
        cmb.require_canonical_ready(parsed, scope)
    cmb.validate_evidence_completeness(parsed, scope)


def test_suppressing_the_non_owner_cells_of_all_ten_real_conflicts_fails_completeness():
    parsed = load_all()
    owners = cmb.canonical_owners()
    result = cmb.reconcile(parsed)
    doomed = set()
    for conflict in result.conflicts:
        pinned = set(owners[(conflict.month, conflict.series)])
        doomed |= {id(o) for o in conflict.evidence if not (o.canonical_for_table and (o.manifest_key, o.source_table) in pinned)}
    assert doomed
    suppressed = _without_cells(parsed, doomed)
    assert cmb.reconcile(suppressed).conflicts == ()  # a diagnostic reconcile alone would show no conflict at all
    scope = frozenset((c.series, c.month) for c in result.conflicts)
    for gate in (lambda: cmb.validate_evidence_completeness(suppressed, scope), lambda: cmb.require_canonical_ready(suppressed, scope),
                 lambda: cmb.require_canonical_ready(suppressed)):
        with pytest.raises(ZamstatsCpiError, match="incomplete"):
            gate()


def test_full_reconciliation_resolves_every_observation_except_the_pinned_source_conflicts():
    result = cmb.reconcile(load_all())
    assert len(result.resolved) == 1356 and len(result.conflicts) == 10
    assert len(result.resolved) + len(result.conflicts) == 1366  # 3 x 202 index + 3 x 190 monthly + 190 annual
    assert result.unowned == ()
    assert {(c.series, c.month) for c in result.conflicts} == KNOWN_CONFLICTS
    assert {(c.series, c.month) for c in result.conflicts if c.same_publication} == SAME_PUBLICATION
    assert not any(r.series in ("annual_inflation_food", "annual_inflation_non_food") for r in result.resolved)
    assert min(r.month for r in result.resolved if r.series == "index_food") == D(2010, 1, 1)
    formatting = {(r.series, r.month) for r in result.resolved if r.text_variants}
    assert len(formatting) == 8 and ("index_all_items", D(2020, 10, 1)) in formatting  # 274.2 vs 274.20: numerically equal


def test_a_conflict_free_scope_is_canonical_ready_with_only_the_tables_it_needs():
    scope = cmb.scope_for(["index_food"], D(2021, 1, 1), D(2021, 12, 1))
    needed = cmb.required_evidence(scope)
    assert len(needed) == 2
    parsed = [load(p) for p in src.PUBLICATIONS if p.key in {key for key, _table in needed}]
    ready = cmb.require_canonical_ready(parsed, scope)
    assert len(ready) == 12 and all(r.series == "index_food" for r in ready)
    with pytest.raises(ZamstatsCpiError, match="incomplete"):
        cmb.require_canonical_ready(parsed[:1], scope)  # one of the two required tables missing
