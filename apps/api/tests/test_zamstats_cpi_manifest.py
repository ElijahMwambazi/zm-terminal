"""ZamStats CPI vetted publication manifest: static pins, canonical-window coverage proof and its failure modes (offline, pure)."""

import dataclasses
import re
from datetime import date
from urllib.parse import urlsplit

import pytest

from app.services import zamstats_cpi_source as src
from app.services.zamstats_cpi_models import BOUNDARY_FIRST_MONTH, PHASE1_SERIES, SERIES_NAMES, ZamstatsCpiError
from app.services.zamstats_cpi_table import FAMILIES

D = date
P = src.PUBLICATIONS


def replaced(index: int, **changes) -> tuple:
    return P[:index] + (dataclasses.replace(P[index], **changes),) + P[index + 1:]


def table_replaced(pub_index: int, table_index: int, **changes) -> tuple:
    p = P[pub_index]
    tables = p.tables[:table_index] + (dataclasses.replace(p.tables[table_index], **changes),) + p.tables[table_index + 1:]
    return replaced(pub_index, tables=tables)


def test_committed_manifest_is_a_complete_unambiguous_phase1_coverage_proof():
    assert src.manifest_problems() == []
    src.validate_manifest()
    assert len(P) == 14 and src.FIRST_REFERENCE_MONTH == D(2012, 12, 1) and src.LAST_REFERENCE_MONTH == D(2026, 10, 1)


def test_every_series_has_exactly_one_canonical_owner_for_every_month_of_its_window():
    owners = src.canonical_owners()
    for metric, category in PHASE1_SERIES:
        name = f"{metric}_{category}"
        months = [m for (m, n) in owners if n == name]
        assert min(months) == BOUNDARY_FIRST_MONTH[metric] and max(months) == src.PHASE1_LAST_MONTH
        assert len(months) == len(set(months))
        assert all(len(owners[(m, name)]) == 1 for m in months)
    expected = {"index": 202, "monthly_inflation": 190, "annual_inflation": 190}
    for metric, category in PHASE1_SERIES:
        assert sum(1 for (_m, n) in owners if n == f"{metric}_{category}") == expected[metric]
    assert {n for (_m, n) in owners} == set(SERIES_NAMES)


def test_boundaries_are_respected_and_no_annual_component_or_other_series_is_claimed():
    owners = src.canonical_owners()
    assert min(m for (m, n) in owners if n.startswith("index_")) == D(2010, 1, 1)
    assert min(m for (m, n) in owners if n.startswith("monthly_inflation_")) == D(2011, 1, 1)
    assert min(m for (m, n) in owners if n.startswith("annual_inflation_")) == D(2011, 1, 1)
    assert not any(n in ("annual_inflation_food", "annual_inflation_non_food") for (_m, n) in owners)
    for p in P:
        for t in p.tables:
            for metric, category, _c in t.columns:
                assert (metric, category) in PHASE1_SERIES


def test_urls_keys_hashes_and_pins_are_well_formed_and_unique():
    assert len({p.key for p in P}) == len(P) and len({p.url for p in P}) == len(P) and len({p.sha256 for p in P}) == len(P)
    for p in P:
        parts = urlsplit(p.url)
        assert parts.scheme == "https" and parts.netloc == "www.zamstats.gov.zm" and not parts.query and not parts.fragment
        assert parts.path.startswith("/wp-content/uploads/") and parts.path.endswith(".pdf")
        assert len(p.sha256) == 64 and set(p.sha256) <= set("0123456789abcdef")
        assert 0 < p.size_bytes <= src.MAX_PDF_BYTES and p.pdf_pages >= 1
        assert p.reference_month.day == 1 and p.role in src.ROLES
        assert p.regime == "2009=100" and p.rights_status == "review_required"
        assert set(p.layout_families) == {t.family for t in p.tables} and set(p.layout_families) <= set(FAMILIES)
        assert p.pages and max(p.pages) <= p.pdf_pages
        assert p.archive_label and p.archive_label in p.url


def test_exactly_one_latest_publication_with_the_newest_reference_month():
    latest = [p for p in P if p.role == "latest"]
    assert [p.key for p in latest] == ["zam-cpi-2026-10"] and latest[0].reference_month == src.LAST_REFERENCE_MONTH


def test_duplicate_displayed_volume_labels_are_allowed_safely_and_the_archive_label_is_not_an_identity():
    # the real archive labels two months "Vol 121"; the PDF cover of "Vol 117" says Volume 116. Identity is URL + hash + key.
    first = P[0]
    clone = dataclasses.replace(first, key="zam-cpi-clone", url=first.url.replace(".pdf", "-b.pdf"), sha256="0" * 63 + "1")
    assert src.manifest_problems(P + (clone,)) != []  # the clone duplicates canonical coverage, not its volume label
    assert not any("label" in problem for problem in src.manifest_problems(P + (clone,)))
    assert any(p.cover_volume is not None and p.cover_volume not in p.archive_label for p in P)  # the recorded 117 vs 116 anomaly


# ------------------------------------------------------------------ failure modes (each names months/keys, never values)
def test_missing_months_are_named():
    problems = src.manifest_problems(P[:-1])
    assert any("index_all_items: no canonical publication for 2026-01..2026-10" in p for p in problems)
    assert any("annual_inflation_all_items" in p for p in problems) and any("latest" in p for p in problems)


def test_duplicate_canonical_coverage_is_named():
    # the 2023 bulletin extends its index window back over 2020, which the 2020 bulletin already owns
    problems = src.manifest_problems(table_replaced(10, 0, canonical_first=D(2020, 1, 1)))
    assert any("index_all_items: more than one canonical publication" in p for p in problems)


def test_overlapping_canonical_publications_that_disagree_on_ownership_are_reported_not_chosen():
    problems = src.manifest_problems(table_replaced(3, 0, canonical_first=D(2013, 1, 1), canonical_last=D(2016, 12, 1)))
    assert any("more than one canonical publication" in p for p in problems)  # z152 also owns 2013-2015; neither is silently preferred
    owners = src.canonical_owners(table_replaced(3, 0, canonical_first=D(2013, 1, 1), canonical_last=D(2016, 12, 1)))
    assert len(owners[(D(2013, 6, 1), "index_food")]) == 2


def test_wrong_regime_is_rejected():
    assert any("wrong regime" in p for p in src.manifest_problems(replaced(0, regime="1994=100")))


def test_pre_boundary_canonical_rows_are_rejected():
    # a canonical window starting before 2010-01 / 2011-01 is refused even if the table prints those months
    t0 = P[0].tables[0]
    problems = src.manifest_problems(table_replaced(0, 0, first_month=D(2009, 12, 1), canonical_first=D(2009, 12, 1)))
    assert any("before the Phase 1 boundary" in p for p in problems)
    assert t0.canonical_first == D(2010, 1, 1)


def test_unsupported_metric_or_category_coverage_is_rejected():
    bad_columns = (("index", "all_items", True), ("annual_inflation", "food", True))
    problems = src.manifest_problems(table_replaced(3, 1, columns=bad_columns))
    assert any("unsupported series annual_inflation_food" in p or "All Items only" in p for p in problems)


def test_bad_url_hash_role_and_windows_are_rejected():
    assert any("exact https" in p for p in src.manifest_problems(replaced(0, url="http://www.zamstats.gov.zm/x.pdf")))
    assert any("exact https" in p for p in src.manifest_problems(replaced(0, url="https://example.org/a.pdf")))
    assert any("exact https" in p for p in src.manifest_problems(replaced(0, url=P[0].url + "?download=1")))
    assert any("64 lowercase hex" in p for p in src.manifest_problems(replaced(0, sha256="ABC")))
    assert any("unknown role" in p for p in src.manifest_problems(replaced(0, role="newest")))
    assert any("rights status" in p for p in src.manifest_problems(replaced(0, rights_status="cleared")))
    assert any("ends after" in p for p in src.manifest_problems(table_replaced(13, 0, canonical_last=D(2026, 11, 1), last_month=D(2026, 11, 1))))
    assert any("layout families" in p for p in src.manifest_problems(replaced(0, layout_families=("free_form",))))
    assert any("beyond the PDF" in p for p in src.manifest_problems(replaced(1, pdf_pages=2)))


def test_base_evidence_must_be_pinned_when_a_table_does_not_print_it():
    problems = src.manifest_problems(replaced(8, base_pages=()))
    assert any("no base-period evidence page" in p for p in problems)


def test_duplicate_keys_urls_and_hashes_are_rejected():
    dup = P + (P[0],)
    problems = src.manifest_problems(dup)
    assert any("duplicate publication key" in p for p in problems) and any("duplicate URL" in p for p in problems)
    assert any("duplicate SHA-256" in p for p in problems)


def test_validate_manifest_raises_with_months_and_keys_only():
    with pytest.raises(ZamstatsCpiError, match="not a valid Phase 1 coverage proof") as info:
        src.validate_manifest(P[:-1])
    assert "2026-01" in str(info.value) and not re.search(r"\d+\.\d", str(info.value))  # months and keys, never numeric values


def test_only_the_zamstats_host_is_vetted_and_no_other_url_is():
    assert all(src.is_vetted_url(p.url) for p in P)
    assert not src.is_vetted_url("https://www.zamstats.gov.zm/wp-content/uploads/2026/10/other.pdf")
    assert not src.is_vetted_url(P[0].url + "?x=1")
    assert not src.is_vetted_url(P[0].url.replace("https://", "http://"))
    assert not src.is_vetted_url("https://zamstats.gov.zm/" + P[0].url.split("zm/", 1)[1])
    assert not src.is_vetted_url(None)


def test_manifest_records_the_archive_anomalies_as_provenance():
    notes = " ".join(n for p in P for n in p.notes)
    for needle in ("Volume 116", "printed twice", "Volume 247", "October 2018 - October 2019", "May-2024", "disagree"):
        assert needle in notes


def test_manifest_contains_no_database_bo_z_or_discovery_dependency():
    import inspect
    source = inspect.getsource(src)
    for forbidden in ("boz.zm", "sqlalchemy", "psycopg", "crawl(", "find_newest", "datetime.now", "date.today"):
        assert forbidden not in source
