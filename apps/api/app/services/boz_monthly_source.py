"""Vetted publication manifest, bounded fetching and text extraction for BoZ Fortnightly Statistics PDFs.

Only the explicitly vetted publications below are ever requested (no URL is constructed or discovered, the archive is not
crawled). Each entry comes from the research report's section 6.1 manifest (docs/research/boz-deeper-fx-history.md), and
`first_month` / `last_month` pin the months its Table 02 monthly block was observed to contain, so a layout or content change
fails that publication instead of being ingested. Add a publication only after validating its layout.

Pipeline (each step separate): fetch bytes -> extract page text -> interpret Table 02 (boz_monthly_table) -> combine.
Text-bearing PDFs only: there is no OCR fallback. Raw bytes and extracted text are never stored or logged.
"""

import asyncio
import io
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx
import pdfplumber

from app.services.boz_monthly_table import ParsedTable, Table02Error, parse_table02

BOZ_HOST = "www.boz.zm"
USER_AGENT = "ZM-Terminal/0.1 (manual research import; low-volume)"
TIMEOUT_SECONDS = 60.0
MAX_PDF_BYTES = 5_000_000  # vetted files are 1.2 to 1.6 MB
MAX_PDF_PAGES = 60  # vetted files have 24 to 27 pages
REQUEST_PAUSE_SECONDS = 2.0


class PublicationError(Exception):
    """A vetted publication could not be fetched, read or validated. Never carries source values."""


@dataclass(frozen=True)
class Publication:
    title: str  # the title BoZ's own listing gives the issue
    path: str  # absolute path on https://www.boz.zm, exactly as listed by the BoZ statistics JSON:API
    first_month: date
    last_month: date

    @property
    def url(self) -> str:
        return f"https://{BOZ_HOST}{self.path}"

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def _p(title: str, path: str, first: tuple[int, int], last: tuple[int, int]) -> Publication:
    return Publication(title, path, date(first[0], first[1], 1), date(last[0], last[1], 1))


_OLD = "/sites/default/files/migrated/fortnight-statistics"

# Chronological order: the first publication containing a month is that month's canonical provenance.
# 2026 issue 22 (a one-page issue without Table 02) is deliberately not part of the manifest.
PUBLICATIONS: tuple[Publication, ...] = (
    _p("Volume 24 Issue No. 01 - 2016", f"{_OLD}/2016/StatisticsFortnightly2016Vol24No01.pdf", (2014, 1), (2016, 12)),
    _p("Volume 25 Issue No. 06 - 2017", f"{_OLD}/2017/StatisticsFortnightly2017Vol25No6.pdf", (2016, 1), (2018, 2)),
    _p("Volume 26 Issue No. 26 - 2019", f"{_OLD}/2019/StatisticsFortnightly2019Vol26No26.pdf", (2017, 1), (2019, 11)),
    _p("Volume 28 Issue No. 02 - 2020", f"{_OLD}/2020/StatisticsFortnightly2020Vol28No02.pdf", (2018, 1), (2020, 12)),
    _p("Volume 28 Issue No. 27 - 2021", f"{_OLD}/2021/StatisticsFortnightly2021Vol28No27.pdf", (2019, 1), (2021, 12)),
    _p("Volume 29 Issue No. 26 - 2022", f"{_OLD}/2022/StatisticsFortnightly2022Vol29IssueNo26.pdf", (2020, 7), (2022, 12)),
    _p("Volume 30 Issue No. 26 - 2023", f"{_OLD}/2023/StatisticsFortnightly2023Vol30IssueNo26.pdf", (2021, 7), (2023, 12)),
    _p("Volume 31 Issue No. 26 - 2024", f"{_OLD}/2024/StatisticsFortnightly2024Vol31IssueNo26.pdf", (2022, 1), (2024, 11)),
    _p("Volume 32 Issue No. 14 - 2025", f"{_OLD}/2025/StatisticsFortnightly2025Vol32IssueNo_14.pdf", (2024, 1), (2025, 6)),
    _p("Volume 32 Issue No. 26 - 2025", f"{_OLD}/2025/StatisticsFortnightly2025Vol32IssueNo_26.pdf", (2024, 1), (2025, 11)),
    _p("Fortnightly Statistics Volume 33 Issue No. 08", "/sites/default/files/2026-04/StatisticsFortnightly2026Vol33IssueNo_8.pdf",
       (2024, 1), (2026, 3)),
    _p("FORTNIGHTLY STATISTICS (2026-07-21)", "/sites/default/files/2026-07/StatisticsFortnightly2026Vol33IssueNo_14.pdf",
       (2024, 1), (2026, 6)),
    _p("FORTNIGHTLY STATISTICS (2026-10-27)", "/sites/default/files/2026-11/StatisticsFortnightly2026Vol33IssueNo_21.pdf",
       (2024, 1), (2026, 9)),
)
FIRST_COVERED_MONTH = min(p.first_month for p in PUBLICATIONS)
LAST_COVERED_MONTH = max(p.last_month for p in PUBLICATIONS)

def is_vetted_url(url: object) -> bool:
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and parts.scheme == "https" and parts.netloc == BOZ_HOST and not parts.query and not parts.fragment
                and url in {p.url for p in PUBLICATIONS})


def select_publications(first: date, last: date) -> list[Publication]:
    """Vetted publications whose pinned month range intersects first..last (months as first-of-month dates), in manifest order."""
    return [p for p in PUBLICATIONS if p.first_month <= last and p.last_month >= first]


async def fetch_pdf(publication: Publication, client: httpx.AsyncClient) -> bytes:
    """One GET of a vetted URL: HTTPS BoZ host only, no redirects, size-capped, content checked. No retries."""
    if not is_vetted_url(publication.url):
        raise PublicationError("Publication URL is not on the vetted manifest")
    data = bytearray()
    async with client.stream(
        "GET", publication.url, timeout=TIMEOUT_SECONDS, follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"},
    ) as response:
        if response.status_code != 200:
            raise PublicationError(f"{publication.filename}: unexpected HTTP status {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" not in content_type and "octet-stream" not in content_type:
            raise PublicationError(f"{publication.filename}: unexpected content type")
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_PDF_BYTES:
            raise PublicationError(f"{publication.filename}: file is larger than expected")
        async for chunk in response.aiter_bytes():
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                raise PublicationError(f"{publication.filename}: file is larger than expected")
    if not data.startswith(b"%PDF-"):
        raise PublicationError(f"{publication.filename}: not a PDF")
    return bytes(data)


def extract_pages(pdf_bytes: bytes) -> list[str]:
    """Extracted text of every page (text layer only; no OCR). A PDF without any text fails closed."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise PublicationError("PDF has more pages than expected")
            pages = [page.extract_text() or "" for page in pdf.pages]
    except PublicationError:
        raise
    except Exception as exc:  # malformed PDF: report the class only
        raise PublicationError(f"PDF could not be read ({type(exc).__name__})") from exc
    if not any(page.strip() for page in pages):
        raise PublicationError("PDF has no usable text layer")
    return pages


def load_publication(publication: Publication, pdf_bytes: bytes) -> ParsedTable:
    """Extract, interpret and pin-check one publication; raises PublicationError on any failure."""
    try:
        table = parse_table02(extract_pages(pdf_bytes))
    except Table02Error as exc:
        raise PublicationError(f"{publication.filename}: {exc}") from exc
    first, last = table.rows[0].month, table.rows[-1].month
    if (first, last) != (publication.first_month, publication.last_month):
        raise PublicationError(
            f"{publication.filename}: monthly block covers {first:%Y-%m} to {last:%Y-%m}, "
            f"expected {publication.first_month:%Y-%m} to {publication.last_month:%Y-%m}"
        )
    return table


async def load_publication_async(publication: Publication, pdf_bytes: bytes) -> ParsedTable:
    return await asyncio.to_thread(load_publication, publication, pdf_bytes)
