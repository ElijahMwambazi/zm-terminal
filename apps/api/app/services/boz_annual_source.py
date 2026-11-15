"""Vetted BoZ Annual Report manifest (Table 13 monthly averages, 2010-2013) and fail-closed loading.

Source family: BoZ Annual Reports, Table 13 `KWACHA/US DOLLAR EXCHANGE RATES`, Monthly Average, series id
`annual_report_table_13_monthly_average` (distinct from the Fortnightly `fortnightly_table_02_monthly_average`). Every pin below
comes from docs/research/boz-2010-2013-monthly-fx-source-chain.md (sections 3 and 8): file SHA-256 and size, the PDF page and
printed page of Table 13, the exact row count and the first and last row. A changed file, page or layout fails the report
instead of being ingested.

CANONICAL_REPORTS are the earliest report per year and the only persistence sources (their `persist_*` window). The 2014
report is VALIDATION ONLY: it bridges to the stored phase-1 months (validate_2014_bridge) and must never supply or change
stored rows or provenance. The 2015 report, which also repeats the 2013 months, is deliberately not part of the manifest.

This module only verifies and parses bytes the caller already holds. Downloading (HTTPS www.boz.zm, one GET per file,
sequential, no redirects) belongs to the later backfill task; `is_vetted_url` is the guard it will use.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence
from urllib.parse import urlsplit

from app.services.boz_annual_table import (
    AnnualTableError, ParsedAnnualTable, PublishedRate, parse_table13,
)
from app.services.pdf_layout_text import LayoutExtractionError, extract_page_layout

BOZ_HOST = "www.boz.zm"
MAX_ANNUAL_PDF_BYTES = 20_000_000  # vetted files are 6.4 to 13.3 MB


class AnnualSourceError(Exception):
    """A vetted report failed hash, page, layout or coverage verification. Never carries rate values."""


class BridgeMismatchError(AnnualSourceError):
    pass


@dataclass(frozen=True)
class AnnualReport:
    key: str
    title: str
    path: str  # absolute path on https://www.boz.zm
    sha256: str
    size_bytes: int
    pdf_page: int  # 1-based PDF page holding Table 13
    printed_page: int  # number printed on that page
    row_count: int  # rows of the table, December-only rows of earlier years included
    first_row: date
    last_row: date
    role: str  # "canonical" or "validation"
    persist_first: date | None  # months this report may supply (canonical only)
    persist_last: date | None
    ordering_from: date  # buy <= mid <= sell is enforced from this month onward (earlier rows: structure only)

    @property
    def url(self) -> str:
        return f"https://{BOZ_HOST}{self.path}"

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


_BASE = "/sites/default/files/migrated/annual-reports"


def _d(year: int, month: int) -> date:
    return date(year, month, 1)


CANONICAL_REPORTS: tuple[AnnualReport, ...] = (
    AnnualReport("ar2010", "Bank of Zambia 2010 Annual Reports", f"{_BASE}/2010/BOZAnnualReport2010.pdf",
                 "752e35fe36704a4b293a2bf6717b5f32db1f63436e34d80f99de425554df2610", 9_320_554, 148, 140, 49,
                 _d(1995, 12), _d(2010, 12), "canonical", _d(2010, 1), _d(2010, 12), _d(2010, 1)),
    AnnualReport("ar2011", "Bank of Zambia 2011 Annual Report", f"{_BASE}/2011/BOZANNUALREPORT2011.pdf",
                 "9dfea388de98718201e345534b830688dec554c00273f484e7e218c911135ac5", 9_040_649, 141, 133, 61,
                 _d(1995, 12), _d(2011, 12), "canonical", _d(2011, 1), _d(2011, 12), _d(2011, 1)),
    AnnualReport("ar2012", "Bank of Zambia 2012 Annual Report", f"{_BASE}/2012/BOZANNUALREPORT2012.pdf",
                 "fe64384f43cdb7a58df8fc2df4977886ff5366d0d49ab5d548152f000221f424", 7_670_856, 145, 137, 51,
                 _d(1995, 12), _d(2012, 12), "canonical", _d(2012, 1), _d(2012, 12), _d(2012, 1)),
    AnnualReport("ar2013", "Bank of Zambia 2013 Annual Report", f"{_BASE}/2013/BOZANNUALREPORT2013.pdf",
                 "9c85f2cfb3a8bd141dc70362e087ff4c1975cc4cbb63e1740104d1780bddc645", 6_415_553, 146, 138, 47,
                 _d(2000, 12), _d(2013, 12), "canonical", _d(2013, 1), _d(2013, 12), _d(2013, 1)),
)
VALIDATION_REPORTS: tuple[AnnualReport, ...] = (
    AnnualReport("ar2014", "Bank of Zambia 2014 Annual Report", f"{_BASE}/2014/BOZANNUALREPORT2014.pdf",
                 "e97747fb6f960b509355d6b69461069bb4ae7a4ffd5a91a9bca985e5bdd78209", 13_303_623, 139, 131, 43,
                 _d(2005, 12), _d(2014, 12), "validation", None, None, _d(2014, 1)),
)
ALL_REPORTS = CANONICAL_REPORTS + VALIDATION_REPORTS
FIRST_COVERED_MONTH = min(r.persist_first for r in CANONICAL_REPORTS)  # type: ignore[type-var]
LAST_COVERED_MONTH = max(r.persist_last for r in CANONICAL_REPORTS)  # type: ignore[type-var]


def is_vetted_url(url: object) -> bool:
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and parts.scheme == "https" and parts.netloc == BOZ_HOST and not parts.query and not parts.fragment
                and url in {r.url for r in ALL_REPORTS})


def verify_pdf(report: AnnualReport, pdf_bytes: bytes) -> None:
    if len(pdf_bytes) > MAX_ANNUAL_PDF_BYTES or len(pdf_bytes) != report.size_bytes:
        raise AnnualSourceError(f"{report.filename}: unexpected file size")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise AnnualSourceError(f"{report.filename}: not a PDF")
    if hashlib.sha256(pdf_bytes).hexdigest() != report.sha256:
        raise AnnualSourceError(f"{report.filename}: file does not match the vetted SHA-256")


def check_pins(report: AnnualReport, table: ParsedAnnualTable) -> None:
    """Row count, first/last row and (canonical) full contiguous coverage of the persistence window."""
    rows = table.rows
    if len(rows) != report.row_count:
        raise AnnualSourceError(f"{report.filename}: Table 13 has {len(rows)} rows, expected {report.row_count}")
    if (rows[0].month, rows[-1].month) != (report.first_row, report.last_row):
        raise AnnualSourceError(
            f"{report.filename}: Table 13 spans {rows[0].month:%Y-%m} to {rows[-1].month:%Y-%m}, "
            f"expected {report.first_row:%Y-%m} to {report.last_row:%Y-%m}")
    if report.persist_first is not None and report.persist_last is not None:
        have = {r.month for r in rows}
        month, missing = report.persist_first, []
        while month <= report.persist_last:
            if month not in have:
                missing.append(month)
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        if missing:
            raise AnnualSourceError(f"{report.filename}: {len(missing)} month(s) missing from the persistence window")


def load_annual_report(
    report: AnnualReport, pdf_bytes: bytes,
    extractor: Callable[[bytes, int], str] = extract_page_layout,
) -> ParsedAnnualTable:
    """Hash-check, extract the pinned page with pdftotext -layout, parse Table 13 and check every pin."""
    verify_pdf(report, pdf_bytes)
    try:
        text = extractor(pdf_bytes, report.pdf_page)
        table = parse_table13(text, printed_page=report.printed_page, ordering_from=report.ordering_from)
    except (LayoutExtractionError, AnnualTableError) as exc:
        raise AnnualSourceError(f"{report.filename}: {exc}") from exc
    check_pins(report, table)
    return table


def window_rows(report: AnnualReport, table: ParsedAnnualTable) -> list[PublishedRate]:
    """The rows a canonical report may supply (its persistence window); a validation report supplies none."""
    if report.role != "canonical" or report.persist_first is None or report.persist_last is None:
        raise AnnualSourceError(f"{report.filename}: not a canonical persistence source")
    return [r for r in table.rows if report.persist_first <= r.month <= report.persist_last]


def validate_2014_bridge(table_2014: ParsedAnnualTable, phase1: Sequence[object]) -> int:
    """Validation only: the 2014 report's 2014-01..2014-12 rows must equal the stored phase-1 canonical rows exactly.

    `phase1` items need month, buy, mid, sell (MonthlyObservation or a stored row). Nothing is returned except the number of
    months compared and nothing is written; provenance of the phase-1 rows is never touched. Any mismatch names months only.
    """
    expected = [_d(2014, m) for m in range(1, 13)]
    annual = {r.month: r for r in table_2014.rows}
    stored = {o.month: o for o in phase1}  # type: ignore[attr-defined]
    bad = [m for m in expected if m not in annual or m not in stored
           or (annual[m].buy, annual[m].mid, annual[m].sell) != (stored[m].buy, stored[m].mid, stored[m].sell)]  # type: ignore[attr-defined]
    if bad:
        raise BridgeMismatchError(f"2014 Annual Report bridge mismatch in {len(bad)} month(s): "
                                  + ", ".join(f"{m:%Y-%m}" for m in bad[:5]))
    return len(expected)
