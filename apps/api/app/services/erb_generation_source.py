"""Vetted source identity and loader for the ERB 2025 Annual Statistical Bulletin (generation by station, Tables 12-16).

One pinned file: exact HTTPS URL, byte size and SHA-256 are verified before any text is extracted, so an updated or substituted
upstream PDF is never accepted silently. Only PDF pages 22-24 are extracted (`pdftotext -layout`, no OCR, via the shared bounded
adapter). `fetch_bulletin` is the only network entry (one GET, no redirects, size-capped, no retries, nothing persisted); the
parser itself works on bytes the caller already holds. Rights status: review_required (local/internal use only).
"""

import hashlib
from dataclasses import dataclass
from typing import Callable

import httpx

from app.services.erb_generation_models import ErbGenerationError, ParseResult
from app.services.erb_generation_table import (
    PINNED_PAGES, check_grid, parse_generation_tables, parse_table12_diagnostic,
)
from app.services.pdf_layout_text import LayoutExtractionError, extract_page_layout

ERB_HOST = "www.erb.org.zm"
TIMEOUT_SECONDS = 60.0
USER_AGENT = "zm-terminal/0.1 (ERB statistical bulletin research import)"


@dataclass(frozen=True)
class BulletinManifest:
    url: str
    sha256: str
    size_bytes: int
    pdf_pages: int  # pinned; the SHA-256 covers it, the adapter does not count pages
    title: str
    cover_date: str
    publication_year: int
    rights_status: str
    role: str
    pdf_to_printed_page: dict[int, int]  # the only pages ever extracted
    tables: tuple[int, ...]  # 12 is a diagnostic input, never a station source
    max_bytes: int


BULLETIN_2025 = BulletinManifest(
    url=f"https://{ERB_HOST}/wp-content/uploads/files/statBullet2025.pdf",
    sha256="03fa6051ef49ed02aef527d1264c6f389479747dd4b022508f5ae54f3f5e4203",
    size_bytes=1_193_636,
    pdf_pages=42,
    title="2025 ANNUAL STATISTICAL BULLETIN",
    cover_date="February 2026",
    publication_year=2025,
    rights_status="review_required",
    role="canonical parser source (first slice, candidate)",
    pdf_to_printed_page=dict(PINNED_PAGES),
    tables=(12, 13, 14, 15, 16),
    max_bytes=2_000_000,
)


def verify_bulletin(pdf_bytes: bytes, manifest: BulletinManifest = BULLETIN_2025) -> None:
    if len(pdf_bytes) > manifest.max_bytes or len(pdf_bytes) != manifest.size_bytes:
        raise ErbGenerationError("bulletin: unexpected file size")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ErbGenerationError("bulletin: not a PDF")
    if hashlib.sha256(pdf_bytes).hexdigest() != manifest.sha256:
        raise ErbGenerationError("bulletin: file does not match the vetted SHA-256")


async def fetch_bulletin(client: httpx.AsyncClient, manifest: BulletinManifest = BULLETIN_2025) -> bytes:
    """One GET of the vetted URL: HTTPS ERB host, no redirects, size-capped, verified. No retries, nothing stored."""
    if manifest.url != BULLETIN_2025.url:
        raise ErbGenerationError("bulletin: URL is not the vetted source")
    data = bytearray()
    async with client.stream(
        "GET", manifest.url, timeout=TIMEOUT_SECONDS, follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"},
    ) as response:
        if response.status_code != 200:
            raise ErbGenerationError(f"bulletin: unexpected HTTP status {response.status_code}")
        if "pdf" not in response.headers.get("content-type", "").lower():
            raise ErbGenerationError("bulletin: unexpected content type")
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > manifest.max_bytes:
            raise ErbGenerationError("bulletin: file is larger than expected")
        async for chunk in response.aiter_bytes():
            data += chunk
            if len(data) > manifest.max_bytes:
                raise ErbGenerationError("bulletin: file is larger than expected")
    verify_bulletin(bytes(data), manifest)
    return bytes(data)


def parse_bulletin(
    pdf_bytes: bytes, extractor: Callable[[bytes, int], str] = extract_page_layout,
    manifest: BulletinManifest = BULLETIN_2025,
) -> ParseResult:
    """Verify identity, extract only the pinned pages, parse Tables 13-16 and the Table 12 diagnostic."""
    verify_bulletin(pdf_bytes, manifest)
    try:
        pages = {page: extractor(pdf_bytes, page) for page in manifest.pdf_to_printed_page}
    except LayoutExtractionError as exc:
        raise ErbGenerationError(f"bulletin: {exc}") from exc
    return parse_pages(pages, manifest)


def parse_pages(pages: dict[int, str], manifest: BulletinManifest = BULLETIN_2025) -> ParseResult:
    """Parse already-extracted pinned pages (also the entry for fixture tests, which skip byte verification)."""
    observations, totals, footing = parse_generation_tables(
        pages, source_url=manifest.url, source_publication=manifest.title, publication_year=manifest.publication_year,
        source_sha256=manifest.sha256,
    )
    check_grid(observations)
    total_2025 = next(t.published_total_gwh for t in totals if t.source_table == "Table 16" and t.observation_year == 2025)
    table12 = parse_table12_diagnostic(pages[22], total_2025)
    return ParseResult(manifest.url, manifest.sha256, manifest.title, manifest.publication_year, tuple(observations),
                       tuple(totals), tuple(footing), table12)
