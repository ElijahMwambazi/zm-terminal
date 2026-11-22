"""OPTIONAL real-source fidelity gate for the ZamStats CPI manifest. Skipped unless ZM_ZAMSTATS_CPI_PDF_DIR points at a local
directory holding the vetted Monthly Bulletin PDFs under their manifest file names. Makes no network request, commits no PDF, and is
never required by the normal offline suite (rights status review_required: the files stay local)."""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import zamstats_cpi_source as src
from app.services.zamstats_cpi_combine import reconcile
from app.services.zamstats_cpi_models import ZamstatsCpiError

DIRECTORY = os.environ.get("ZM_ZAMSTATS_CPI_PDF_DIR")
pytestmark = pytest.mark.skipif(not DIRECTORY, reason="set ZM_ZAMSTATS_CPI_PDF_DIR to a folder of the vetted ZamStats PDFs")
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


def load(publication: src.Publication):
    path = Path(DIRECTORY) / publication.filename
    if not path.exists():
        pytest.skip(f"{publication.filename} is not in ZM_ZAMSTATS_CPI_PDF_DIR")
    try:
        return src.parse_publication(path.read_bytes(), publication)
    except ZamstatsCpiError as exc:
        pytest.fail(str(exc))


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


def test_full_reconciliation_resolves_every_observation_except_the_pinned_source_conflicts():
    parsed = [load(p) for p in src.PUBLICATIONS]
    result = reconcile(parsed)
    assert len(result.resolved) + len(result.conflicts) == 1366  # 3 x 202 index + 3 x 190 monthly + 190 annual
    assert {(c.series, c.month) for c in result.conflicts} == KNOWN_CONFLICTS
    assert {(c.series, c.month) for c in result.conflicts if c.same_publication} == SAME_PUBLICATION
    assert not any(r.series in ("annual_inflation_food", "annual_inflation_non_food") for r in result.resolved)
    assert min(r.month for r in result.resolved if r.series == "index_food") == D(2010, 1, 1)
    formatting = {(r.series, r.month) for r in result.resolved if r.text_variants}
    assert ("index_all_items", D(2020, 10, 1)) in formatting  # 274.2 vs 274.20: numerically equal, text different
