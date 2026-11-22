"""Vetted publication manifest, bounded fetching and verified text extraction for the ZamStats CPI Phase 1 foundation.

Only the explicitly vetted Monthly Bulletin PDFs below are ever requested: no URL is constructed from a date, discovered or
crawled, and "newest PDF" is never searched for. Each entry pins the exact URL, byte size, SHA-256, archive label, the PDF cover
as far as it is legible, the layout family and printed extent of every table, the PDF pages to extract, and the months each
table may *canonically* supply. A changed, substituted or misidentified file fails verification instead of being ingested.
Numbering anomalies in ZamStats' own archive (a displayed volume label is not an identity) are kept as provenance in `notes`.

Pipeline (each step separate): verify bytes (size, magic, SHA-256) -> extract only the pinned pages (`pdftotext -layout`, no
OCR, shared bounded adapter) -> pure layout parsers (zamstats_cpi_table) -> reconcile (zamstats_cpi_combine). Raw bytes and
extracted text are never stored or logged. Rights status: review_required (local/internal use only). Published rates are
authoritative: nothing here calculates, repairs or fills a value. There is no BoZ call and no storage.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import urlsplit

import httpx

from app.services.pdf_layout_text import LayoutExtractionError, extract_page_layout
from app.services.zamstats_cpi_models import (
    BOUNDARY_FIRST_MONTH, PHASE1_SERIES, REGIME, RIGHTS_STATUS, ParsedPublication, ZamstatsCpiError, series_name, validate_series,
)
from app.services.zamstats_cpi_table import (
    ANNUAL_ROWS, FAMILIES, HORIZONTAL_13, QUOTED_MONTHS, VERTICAL_LABELLED, TableSpec, parse_table,
)

ZAMSTATS_HOST = "www.zamstats.gov.zm"
USER_AGENT = "zm-terminal/0.1 (ZamStats CPI research import; low-volume)"
TIMEOUT_SECONDS = 60.0
MAX_PDF_BYTES = 4_000_000  # vetted files are 0.6 to 2.7 MB
ROLES = ("canonical", "validation", "latest")  # "latest": a canonical publication that also carries the newest reference month
PHASE1_LAST_MONTH = date(2026, 10, 1)  # the newest reference month published when this manifest was vetted (Vol 283)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _d(year: int, month: int) -> date:
    return date(year, month, 1)


@dataclass(frozen=True)
class Publication:
    key: str
    reference_month: date  # the bulletin's reference month (its own title month)
    archive_label: str  # the archive's file label, e.g. "Vol-117-2012-The-Monthly-December"; NOT an identity
    cover_volume: str | None  # the volume printed on the PDF cover when legible (may differ from the archive label)
    cover_text: str | None
    url: str
    sha256: str
    size_bytes: int
    pdf_pages: int
    layout_families: tuple[str, ...]
    tables: tuple[TableSpec, ...]
    base_pages: tuple[int, ...]  # pinned pages that must print the `2009 = 100` base label
    role: str
    regime: str = REGIME
    rights_status: str = RIGHTS_STATUS
    notes: tuple[str, ...] = field(default=())

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({t.pdf_page for t in self.tables} | set(self.base_pages)))


# Column layouts. Annual inflation is All Items only; the annual table's "Annual CPI" column is validation evidence, not a
# canonical supplier (the Food/Non-Food index table owns the All Items index).
_IDX = (("index", "all_items", True), ("index", "food", True), ("index", "non_food", True))
_MON = (("monthly_inflation", "all_items", True), ("monthly_inflation", "food", True), ("monthly_inflation", "non_food", True))
_ANN = (("index", "all_items", False), ("annual_inflation", "all_items", True))

_T_IDX_OLD = r"CONSUMER INDEX NUMBERS OF CONSUMER PRICES - FOOD AND NON-FOOD \(NATIONAL\)"
_T_ANNEX_11 = r"Table 1\.1: Consumer Price Index - Food and Non-Food \(National\)"
_T_ANNEX_13 = r"Table 1\.3: Consumer Price Indices and Annual Inflation"


def _idx(table_id, page, title, first, last, c_first=None, c_last=None, **kw) -> TableSpec:
    return TableSpec(table_id, VERTICAL_LABELLED, page, title, first, last, _IDX, canonical_first=c_first, canonical_last=c_last, **kw)


def _mon(table_id, family, page, title, first, last, c_first, c_last, **kw) -> TableSpec:
    return TableSpec(table_id, family, page, title, first, last, _MON, canonical_first=c_first, canonical_last=c_last, **kw)


def _ann(table_id, family, page, title, first, last, c_first, c_last, **kw) -> TableSpec:
    return TableSpec(table_id, family, page, title, first, last, _ANN, canonical_first=c_first, canonical_last=c_last, **kw)


_U = f"https://{ZAMSTATS_HOST}/wp-content/uploads"
_OLD = f"{_U}/2023/12"

PUBLICATIONS: tuple[Publication, ...] = (
    Publication(
        "zam-cpi-2012-12", _d(2012, 12), "Vol-117-2012-The-Monthly-December", '116', 'Volume 116 / December, 2012',
        f"{_OLD}/Vol-117-2012-The-Monthly-December.pdf", "c8cf82963f93a96e468f77649621ac93795777a197b7d2a2275d1c81b16a92da",
        1_330_012, 31, (VERTICAL_LABELLED, QUOTED_MONTHS),
        (_idx("Consumer Index Numbers of Consumer Prices - Food and Non-Food (National)", 23,
              r"Consumer Index Numbers of Consumer Prices - Food and Non-Food \(National\)", _d(2010, 1), _d(2012, 11),
              _d(2010, 1), _d(2012, 11), authorized_duplicate_months=frozenset({_d(2010, 12)})),
         _mon("Month on month Inflation Rates: Food and Non Food Items", QUOTED_MONTHS, 5,
              r"Month on month Inflation Rates: Food and Non Food Items", _d(2011, 1), _d(2012, 12), _d(2011, 1), _d(2012, 12), base_on_page=True),),
        (23,), "canonical",
        notes=('archive label Vol 117; the PDF cover reads Volume 116, December 2012', 'index table prints December 2010 twice in a row (collapsed under an explicit per-table authorisation and reported)', 'index table ends at November 2012; December 2012 is owned by the December 2015 bulletin'),
    ),
    Publication(
        "zam-cpi-2014-12", _d(2014, 12), "Vol-140-2014-The-Monthly-December", '140', 'Volume 140',
        f"{_OLD}/Vol-140-2014-The-Monthly-December.pdf", "7f4d06e23370d367e3d45d402db3b1ae7892dcb675a791bbbbbd22a9b7cbe52b",
        2_208_507, 36, (VERTICAL_LABELLED,),
        (_mon("Monthly Inflation Rates: Food and Non Food Items", VERTICAL_LABELLED, 5,
              r"Monthly Inflation Rates: Food and Non Food Items", _d(2013, 1), _d(2014, 12), _d(2013, 1), _d(2014, 12)),),
        (5,), "canonical",
    ),
    Publication(
        "zam-cpi-2015-12", _d(2015, 12), "Vol-152-2015-The-Monthly-December", '152', 'Volume 152',
        f"{_OLD}/Vol-152-2015-The-Monthly-December.pdf", "9aa044e4cca01482e3099bd9ae15da5300e50cf415ec70f33fbe4b619f40a31c",
        607_485, 43, (VERTICAL_LABELLED, HORIZONTAL_13),
        (_idx("Consumer Index Numbers of Consumer Prices - Food and Non-Food (National)", 24, _T_IDX_OLD, _d(2012, 1), _d(2015, 12),
              _d(2012, 12), _d(2015, 12)),
         _mon("Monthly Inflation Rate: Food and Non Food Items", HORIZONTAL_13, 5,
              r"Monthly Inflation Rate: Food and Non Food Items, \(2009=100\)", _d(2014, 12), _d(2015, 12), _d(2015, 1), _d(2015, 12),
              horizontal_weights=False),),
        (24,), "canonical",
        notes=('the monthly table has no weight column',),
    ),
    Publication(
        "zam-cpi-2016-12", _d(2016, 12), "Vol-164-2016-The-Monthly-December", '164', 'Volume 164',
        f"{_OLD}/Vol-164-2016-The-Monthly-December.pdf", "cee0daa96df9143251f5e825269bd246aa41d1c85d828a2e0c75e7ddc01e15ea",
        674_444, 44, (VERTICAL_LABELLED, ANNUAL_ROWS, HORIZONTAL_13),
        (_idx("Consumer Index Numbers of Consumer Prices - Food and Non-Food (National)", 29, _T_IDX_OLD, _d(2013, 1), _d(2016, 12),
              _d(2016, 1), _d(2016, 12)),
         _ann("Consumer Price Indices (2009=100)", ANNUAL_ROWS, 31, r"CONSUMER PRICE INDICES \(2009=100\)", _d(2011, 1), _d(2016, 12),
              _d(2011, 1), _d(2016, 12)),
         _mon("Monthly Inflation Rate: Food and Non-Food Items (2009=100)", HORIZONTAL_13, 9,
              r"Monthly Inflation Rate: Food and Non-Food Items \(2009=100\)", _d(2015, 12), _d(2016, 12), _d(2016, 1), _d(2016, 12)),),
        (29,), "canonical",
        notes=('the Food/Non-Food index table and the annual table disagree on All Items for February 2013, February 2014 and August 2014 (source inconsistency, preserved as a conflict)',),
    ),
    Publication(
        "zam-cpi-2018-01", _d(2018, 1), "Vol-177-2018-The-Monthly-January", None, None,
        f"{_OLD}/Vol-177-2018-The-Monthly-January.pdf", "e3ebbcf7a87020f2df0b79b60fe96262580187054388a3dabdbcbe5a23f0407e",
        2_231_554, 37, (HORIZONTAL_13,),
        (_mon("Month on Month Inflation Rate: Food and Non-Food Items (2009=100)", HORIZONTAL_13, 6,
              r"Month on Month Inflation Rate: Food and Non-Food Items \(2009=100\)", _d(2017, 1), _d(2018, 1), _d(2017, 1), _d(2017, 12),
              wrapped_non_food_tail=5),),
        (6,), "canonical",
        notes=('the PDF cover has no text layer', 'the Non-Food row prints its last five values on the line above its weight (explicit wrapped variant)'),
    ),
    Publication(
        "zam-cpi-2018-12", _d(2018, 12), "Vol-189-2018-The-Monthly-December", None, None,
        f"{_OLD}/Vol-189-2018-The-Monthly-December.pdf", "cb003b36c6a5670f838ddcdf0ec8b1a0c088c776380d401f22c3d609f84428eb",
        2_333_399, 42, (HORIZONTAL_13,),
        (_mon("Month on Month Inflation Rate: Food and Non-Food Items", HORIZONTAL_13, 7,
              r"Month on Month Inflation Rate: Food and Non-Food Items", _d(2017, 12), _d(2018, 12), _d(2018, 1), _d(2018, 12),
              title_matches=2, title_pick=2),),
        (7,), "canonical",
        notes=('the PDF cover has no text layer', 'the heading is printed twice on the page; the second is the table title'),
    ),
    Publication(
        "zam-cpi-2019-12", _d(2019, 12), "Vol-201-2019-The-Monthly-December", None, None,
        f"{_OLD}/Vol-201-2019-The-Monthly-December.pdf", "c4b6179c58aaafe2e29b51569b367396f68900da41f10352e048ba2d4fca0f4d",
        1_961_186, 42, (ANNUAL_ROWS, HORIZONTAL_13),
        (_ann("Consumer Price Indices and Annual Inflation", ANNUAL_ROWS, 32, r"CONSUMER PRICE INDICES AND ANNUAL INFLATION", _d(2015, 1), _d(2019, 12),
              _d(2017, 1), _d(2019, 12)),
         _mon("Table 6: Month on Month Inflation Rate: Food and Non-Food Items", HORIZONTAL_13, 10,
              r"Table 6: Month on Month Inflation Rate: Food and Non-Food Items", _d(2018, 12), _d(2019, 12), _d(2019, 1), _d(2019, 12)),),
        (32, 10), "canonical",
        notes=('the printed title of Table 6 says October 2018 - October 2019 but the column headers and values are December 2018 - December 2019 (the headers are used)',),
    ),
    Publication(
        "zam-cpi-2020-12", _d(2020, 12), "Vol-213-2020-The-Monthly-December", None, None,
        f"{_OLD}/Vol-213-2020-The-Monthly-December.pdf", "ab679b9361df9f7ffd719919f3811f30baeb2da50cc0c164cf3311bdb0b91256",
        2_581_000, 55, (VERTICAL_LABELLED, HORIZONTAL_13),
        (_idx("Table 1.1: Consumer Price Index - Food and Non-Food (National)", 42, r"Table 1\.1: Consumer Price Index - Food and Non-Food \(National\)",
              _d(2017, 1), _d(2020, 12), _d(2017, 1), _d(2020, 12)),
         _mon("Table 5: Month - on - Month Inflation Rate", HORIZONTAL_13, 8, r"Table 5: Month - on - Month Inflation Rate",
              _d(2019, 12), _d(2020, 12), _d(2020, 1), _d(2020, 12), base_on_page=False),),
        (42,), "canonical",
        notes=('the PDF cover has no text layer',),
    ),
    Publication(
        "zam-cpi-2021-12", _d(2021, 12), "Vol-225-of-2021-The-Monthly-December", '225', 'Volume 225 / December, 2021',
        f"{_OLD}/Vol-225-of-2021-The-Monthly-December.pdf", "9684bb319e74980b148f805ff2d7e9af307f2e738454182737a246178b800c75",
        2_125_833, 46, (HORIZONTAL_13,),
        (_mon("Table 7: Overall Monthly Inflation Rate", HORIZONTAL_13, 12, r"Table 7: Overall Monthly Inflation Rate for Food and Non-Food Items",
              _d(2020, 12), _d(2021, 12), _d(2021, 1), _d(2021, 12), base_on_page=False),),
        (26,), "canonical",
    ),
    Publication(
        "zam-cpi-2022-12", _d(2022, 12), "Vol-237-of-2022-The-Monthly-December", '237', 'Volume 237 / December, 2022',
        f"{_OLD}/Vol-237-of-2022-The-Monthly-December.pdf", "cdc0978b7b5d744c1e7bf47df8ccfc084d4397534b937e811f2b7f39a4c47ea8",
        2_682_655, 55, (HORIZONTAL_13,),
        (_mon("Table 8: Overall Monthly Inflation Rate", HORIZONTAL_13, 13, r"Table 8: Overall Monthly Inflation Rate for Food and Non-Food Items",
              _d(2021, 12), _d(2022, 12), _d(2022, 1), _d(2022, 12), base_on_page=False),),
        (31,), "canonical",
    ),
    Publication(
        "zam-cpi-2023-12", _d(2023, 12), "Vol-249-of-2023-The-Monthly-December", '249', 'Volume 249 / December, 2023',
        f"{_OLD}/Vol-249-of-2023-The-Monthly-December.pdf", "6478f3e2ed398445d61ee6b955edc49ec78ed0c56a49509fb137685320d2138c",
        1_896_016, 74, (VERTICAL_LABELLED, ANNUAL_ROWS, HORIZONTAL_13),
        (_idx("Table 1.1: Consumer Price Index - Food and Non-Food (National)", 50, _T_ANNEX_11, _d(2020, 1), _d(2023, 12), _d(2021, 1), _d(2023, 12),
              base_on_page=False),
         _ann("Table 1.3: Consumer Price Indices and Annual Inflation", ANNUAL_ROWS, 52, _T_ANNEX_13, _d(2020, 1), _d(2023, 12),
              _d(2020, 1), _d(2023, 12)),
         _mon("Table 8: Overall Monthly Inflation Rate", HORIZONTAL_13, 12, r"Table 8: Overall Monthly Inflation Rate for Food and Non-Food Items",
              _d(2022, 12), _d(2023, 12), _d(2023, 1), _d(2023, 12), base_on_page=False),),
        (52,), "canonical",
        notes=('the Table 1.1 caption says 2018-2023 but the rows start in January 2020', 'page 52 carries a stale running header (Volume 247, October 2023)'),
    ),
    Publication(
        "zam-cpi-2024-12", _d(2024, 12), "Vol-261-of-2024-The-Monthly-December", '261', 'Volume 261 / December',
        f"{_U}/2024/12/Vol-261-of-2024-The-Monthly-December.pdf", "20f74bd310aecb3ae11d01d9cb5508b671c72dae31dabbf3e9b9982683344693",
        2_191_438, 44, (HORIZONTAL_13,),
        (_mon("Table 7: Overall Monthly Inflation Rate", HORIZONTAL_13, 13, r"Table 7: Overall Monthly Inflation Rate for Food and Non-Food Items",
              _d(2023, 12), _d(2024, 12), _d(2024, 1), _d(2024, 12), base_on_page=False),),
        (29,), "canonical",
    ),
    Publication(
        "zam-cpi-2025-12", _d(2025, 12), "Vol-273-of-2025-The-Monthly-December-241225", '273', 'Volume 273 / December',
        f"{_U}/2025/12/Vol-273-of-2025-The-Monthly-December-241225.pdf", "ec774ac50dd7630bb23ecf23dd791ee9068cd1c98145b1d47e3185fe29ee97a3",
        2_290_180, 51, (VERTICAL_LABELLED, ANNUAL_ROWS, HORIZONTAL_13),
        (_idx("Table 1.1: Consumer Price Index - Food and Non-Food (National)", 25, r"Table 1\.1", _d(2020, 9), _d(2025, 12), _d(2024, 1), _d(2025, 12),
              base_on_page=False),
         _ann("Table 1.3: Consumer Price Indices and Annual Inflation", ANNUAL_ROWS, 27, r"Table 1\.3", _d(2021, 3), _d(2025, 12),
              _d(2024, 1), _d(2025, 12)),
         _mon("Table 7: Overall Monthly Inflation Rate", HORIZONTAL_13, 12, r"Table 7: Overall Monthly Inflation Rate for Food and Non-Food Items",
              _d(2024, 12), _d(2025, 12), _d(2025, 1), _d(2025, 12), base_on_page=False),),
        (27,), "canonical",
        notes=('the monthly table header prints May-2024 inside a 2025 window (header years are not used)',),
    ),
    Publication(
        "zam-cpi-2026-10", _d(2026, 10), "Vol-283-of-2026-The-Monthly-October", '283', 'Volume 283 / October 2026',
        f"{_U}/2026/10/Vol-283-of-2026-The-Monthly-October.pdf", "06d867a8ed2060e2a2d4d609447e9494e0c46d11d68790ba7b4de00290b7eb70",
        2_402_456, 59, (VERTICAL_LABELLED, HORIZONTAL_13),
        (_idx("Table 1.1: Consumer Price Index - Food and Non-Food (National)", 23, _T_ANNEX_11, _d(2023, 1), _d(2026, 10), _d(2026, 1), _d(2026, 10),
              base_on_page=False),
         _ann("Table 1.3: Consumer Price Indices and Annual Inflation", VERTICAL_LABELLED, 25, _T_ANNEX_13, _d(2022, 1), _d(2026, 10),
              _d(2026, 1), _d(2026, 10)),
         _mon("Table 5: Overall Monthly Inflation Rate", HORIZONTAL_13, 9, r"Table 5: Overall Monthly Inflation Rate for Food and Non-Food Items",
              _d(2025, 10), _d(2026, 10), _d(2026, 1), _d(2026, 10), base_on_page=False, wrapped_non_food_tail=4),),
        (25,), "latest",
        notes=('the Non-Food monthly row prints its last four values on the line above its weight (explicit wrapped variant)', 'the annual table prints one year label per twelve-month block'),
    ),
)


FIRST_REFERENCE_MONTH = min(p.reference_month for p in PUBLICATIONS)
LAST_REFERENCE_MONTH = max(p.reference_month for p in PUBLICATIONS)
_BASE_LABEL = re.compile(r"\b((?:19|20)\d\d)\s*=\s*100\b")


def is_vetted_url(url: object) -> bool:
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and parts.scheme == "https" and parts.netloc == ZAMSTATS_HOST and not parts.query and not parts.fragment
                and url in {p.url for p in PUBLICATIONS})


# ---------------------------------------------------------------- manifest validation (pure)
def _months(first: date, last: date):
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        yield date(year, month, 1)
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)


def _ranges(months: list[date]) -> str:
    """Compress sorted months into 'YYYY-MM..YYYY-MM' runs for error messages (months only, never values)."""
    runs: list[list[date]] = []
    for m in sorted(months):
        if runs and (m.year * 12 + m.month) - (runs[-1][-1].year * 12 + runs[-1][-1].month) == 1:
            runs[-1][-1] = m
        else:
            runs.append([m, m])
    return ", ".join(f"{a:%Y-%m}" if a == b else f"{a:%Y-%m}..{b:%Y-%m}" for a, b in runs)


def canonical_owners(publications: tuple[Publication, ...] = PUBLICATIONS) -> dict[tuple[date, str], list[tuple[str, str]]]:
    """For every (month, series) the (publication key, table id) pairs that the manifest allows to supply it canonically."""
    owners: dict[tuple[date, str], list[tuple[str, str]]] = {}
    for publication in publications:
        for table in publication.tables:
            if table.canonical_first is None or table.canonical_last is None:
                continue
            for metric, category, canonical in table.columns:
                if not canonical:
                    continue
                for month in _months(table.canonical_first, table.canonical_last):
                    owners.setdefault((month, series_name(metric, category)), []).append((publication.key, table.table_id))
    return owners


def manifest_problems(publications: tuple[Publication, ...] = PUBLICATIONS, last_month: date = PHASE1_LAST_MONTH) -> list[str]:
    """Every reason the manifest is not an unambiguous proof of full Phase 1 coverage (empty list: it is). Pure. Messages name
    publication keys, tables, series and months; never source numeric values."""
    problems: list[str] = []
    keys = [p.key for p in publications]
    for key in sorted({k for k in keys if keys.count(k) > 1}):
        problems.append(f"duplicate publication key {key}")
    for attr, label in (("url", "URL"), ("sha256", "SHA-256")):
        values = [getattr(p, attr) for p in publications]
        for value in {v for v in values if values.count(v) > 1}:
            problems.append(f"duplicate {label} on publications {[p.key for p in publications if getattr(p, attr) == value]}")
    for p in publications:
        where = p.key
        parts = urlsplit(p.url)
        if parts.scheme != "https" or parts.netloc != ZAMSTATS_HOST or parts.query or parts.fragment or not parts.path.endswith(".pdf"):
            problems.append(f"{where}: URL is not an exact https {ZAMSTATS_HOST} PDF URL")
        if not _SHA256.fullmatch(p.sha256):
            problems.append(f"{where}: SHA-256 is not 64 lowercase hex digits")
        if not (0 < p.size_bytes <= MAX_PDF_BYTES) or p.pdf_pages < 1:
            problems.append(f"{where}: size or page count is not a plausible pin")
        if p.reference_month.day != 1:
            problems.append(f"{where}: reference month is not first-of-month")
        if p.role not in ROLES:
            problems.append(f"{where}: unknown role")
        if p.regime != REGIME:
            problems.append(f"{where}: wrong regime (only {REGIME} is supported)")
        if p.rights_status != RIGHTS_STATUS:
            problems.append(f"{where}: rights status must be {RIGHTS_STATUS}")
        if not p.tables:
            problems.append(f"{where}: no tables")
        if set(p.layout_families) != {t.family for t in p.tables} or not set(p.layout_families) <= set(FAMILIES):
            problems.append(f"{where}: layout families do not match the tables or are unknown")
        for table in p.tables:
            try:
                from app.services.zamstats_cpi_table import validate_spec
                validate_spec(table)
            except ZamstatsCpiError as exc:
                problems.append(f"{where}: {exc}")
                continue
            if table.pdf_page > p.pdf_pages:
                problems.append(f"{where} {table.table_id}: pinned page is beyond the PDF")
            if not table.base_on_page and not p.base_pages:
                problems.append(f"{where} {table.table_id}: no base-period evidence page is pinned")
            for metric, category, canonical in table.columns:
                try:
                    validate_series(metric, category)
                except ZamstatsCpiError:
                    problems.append(f"{where} {table.table_id}: unsupported series {metric}_{category}")
                    continue
                if canonical and table.canonical_first is not None and table.canonical_first < BOUNDARY_FIRST_MONTH[metric]:
                    problems.append(f"{where} {table.table_id}: canonical {metric} starts before the Phase 1 boundary")
            if table.canonical_last is not None and table.canonical_last > last_month:
                problems.append(f"{where} {table.table_id}: canonical window ends after {last_month:%Y-%m}")
        for page in p.base_pages:
            if not 1 <= page <= p.pdf_pages:
                problems.append(f"{where}: base-evidence page {page} is beyond the PDF")
    latest = [p for p in publications if p.role == "latest"]
    if len(latest) != 1 or latest[0].reference_month != max(p.reference_month for p in publications):
        problems.append("exactly one 'latest' publication must carry the newest reference month")
    owners = canonical_owners(publications)
    series_names = [series_name(m, c) for m, c in PHASE1_SERIES]
    for name, (metric, _category) in zip(series_names, PHASE1_SERIES):
        required = list(_months(BOUNDARY_FIRST_MONTH[metric], last_month))
        missing = [m for m in required if (m, name) not in owners]
        duplicated = [m for m in required if len(owners.get((m, name), [])) > 1]
        if missing:
            problems.append(f"{name}: no canonical publication for {_ranges(missing)}")
        if duplicated:
            problems.append(f"{name}: more than one canonical publication for {_ranges(duplicated)}")
    for month, name in sorted(owners):
        if name not in series_names:
            problems.append(f"{name}: not a Phase 1 series ({month:%Y-%m})")
            break
    return problems


def validate_manifest(publications: tuple[Publication, ...] = PUBLICATIONS, last_month: date = PHASE1_LAST_MONTH) -> None:
    problems = manifest_problems(publications, last_month)
    if problems:
        raise ZamstatsCpiError("manifest is not a valid Phase 1 coverage proof: " + "; ".join(problems))


# ---------------------------------------------------------------- bytes, fetch, extract, parse
def verify_publication(pdf_bytes: bytes, publication: Publication) -> None:
    if len(pdf_bytes) > MAX_PDF_BYTES or len(pdf_bytes) != publication.size_bytes:
        raise ZamstatsCpiError(f"{publication.key}: unexpected file size")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ZamstatsCpiError(f"{publication.key}: not a PDF")
    if hashlib.sha256(pdf_bytes).hexdigest() != publication.sha256:
        raise ZamstatsCpiError(f"{publication.key}: file does not match the vetted SHA-256")


async def fetch_publication(client: httpx.AsyncClient, publication: Publication) -> bytes:
    """One GET of a vetted URL: HTTPS ZamStats host only, no redirects, size-capped, no retries, nothing stored. The vetted-URL
    check happens before any network call; the body is verified against the pinned size and SHA-256."""
    if not is_vetted_url(publication.url) or publication not in PUBLICATIONS:
        raise ZamstatsCpiError("publication URL is not on the vetted manifest")
    data = bytearray()
    async with client.stream(
        "GET", publication.url, timeout=TIMEOUT_SECONDS, follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"},
    ) as response:
        if response.status_code != 200:
            raise ZamstatsCpiError(f"{publication.key}: unexpected HTTP status {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" not in content_type and "octet-stream" not in content_type:
            raise ZamstatsCpiError(f"{publication.key}: unexpected content type")
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_PDF_BYTES:
            raise ZamstatsCpiError(f"{publication.key}: file is larger than expected")
        async for chunk in response.aiter_bytes():
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                raise ZamstatsCpiError(f"{publication.key}: file is larger than expected")
    verify_publication(bytes(data), publication)
    return bytes(data)


def parse_pages(pages: dict[int, str], publication: Publication) -> ParsedPublication:
    """Parse already-extracted pinned pages (also the entry for fixture tests, which skip byte verification)."""
    missing = [page for page in publication.pages if page not in pages]
    if missing:
        raise ZamstatsCpiError(f"{publication.key}: pinned pages {missing} were not supplied")
    for page in publication.base_pages:
        bases = {m.group(1) for m in _BASE_LABEL.finditer(pages[page])}
        if bases != {"2009"}:
            raise ZamstatsCpiError(f"{publication.key}: base-evidence page {page} does not print exactly the {REGIME} base")
    tables = tuple(parse_table(pages[table.pdf_page], table, publication.key) for table in publication.tables)
    return ParsedPublication(publication.key, publication.reference_month, tables)


def parse_publication(
    pdf_bytes: bytes, publication: Publication, extractor: Callable[[bytes, int], str] = extract_page_layout,
) -> ParsedPublication:
    """Verify identity, extract ONLY the pinned pages (no OCR, no whole-document scan) and parse every pinned table."""
    verify_publication(pdf_bytes, publication)
    try:
        pages = {page: extractor(pdf_bytes, page) for page in publication.pages}
    except LayoutExtractionError as exc:
        raise ZamstatsCpiError(f"{publication.key}: {exc}") from exc
    return parse_pages(pages, publication)
