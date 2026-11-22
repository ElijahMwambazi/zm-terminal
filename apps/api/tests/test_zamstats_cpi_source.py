"""ZamStats CPI source adapter: bounded fetch with mocked HTTP only, byte verification and pinned-page extraction (offline)."""

import asyncio
import dataclasses
import hashlib
from datetime import date

import httpx
import pytest

from app.services import zamstats_cpi_source as src
from app.services.pdf_layout_text import LayoutExtractionError
from app.services.zamstats_cpi_models import ZamstatsCpiError
from app.services.zamstats_cpi_table import HORIZONTAL_13, TableSpec

PUB = src.PUBLICATIONS[-1]
BODY = b"%PDF-1.7\n" + b"synthetic bytes, not a ZamStats file\n" * 20
FAKE = dataclasses.replace(PUB, sha256=hashlib.sha256(BODY).hexdigest(), size_bytes=len(BODY))


def run_fetch(handler, publication=FAKE, patch_manifest=True):
    calls: list[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as client:
            return await src.fetch_publication(client, publication)

    return asyncio.run(go()), calls


@pytest.fixture
def vetted_fake(monkeypatch):
    """Make the synthetic FAKE entry (real vetted URL, fake pins) the only manifest entry so the fetch seam can be exercised."""
    monkeypatch.setattr(src, "PUBLICATIONS", (FAKE,))
    return FAKE


def ok(body=BODY, content_type="application/pdf", status=200, headers=None):
    return lambda request: httpx.Response(status, content=body, headers={"content-type": content_type, **(headers or {})})


def test_fetch_requests_exactly_the_vetted_url_once_with_an_identifying_user_agent(vetted_fake):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        seen["accept"] = request.headers["accept"]
        return httpx.Response(200, content=BODY, headers={"content-type": "application/pdf"})

    data, calls = run_fetch(handler)
    assert data == BODY and calls == [FAKE.url] and len(calls) == 1
    assert seen["ua"].startswith("zm-terminal/") and "research" in seen["ua"] and seen["accept"] == "application/pdf"


def test_non_manifest_url_is_rejected_before_any_network_call(vetted_fake):
    alien = dataclasses.replace(FAKE, url=FAKE.url.replace("Vol-283", "Vol-999"))
    with pytest.raises(ZamstatsCpiError, match="not on the vetted manifest"):
        run_fetch(ok(), publication=alien)
    # a vetted URL carried by a different (unlisted) Publication object is also refused
    imposter = dataclasses.replace(FAKE, key="other")
    called = []
    with pytest.raises(ZamstatsCpiError, match="not on the vetted manifest"):
        run_fetch(lambda r: called.append(1) or ok()(r), publication=imposter)
    assert called == []


def test_the_real_manifest_url_with_changed_scheme_or_query_is_not_vetted():
    for url in (PUB.url.replace("https://", "http://"), PUB.url + "?x=1", PUB.url + "#frag"):
        assert not src.is_vetted_url(url)


def test_redirect_is_rejected_not_followed(vetted_fake):
    def handler(request):
        if str(request.url) == FAKE.url:
            return httpx.Response(302, headers={"location": "https://evil.example/x.pdf"})
        return httpx.Response(200, content=BODY, headers={"content-type": "application/pdf"})

    with pytest.raises(ZamstatsCpiError, match="unexpected HTTP status 302"):
        run_fetch(handler)


def test_redirect_makes_one_request_only(vetted_fake):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(301, headers={"location": "https://www.zamstats.gov.zm/other.pdf"})

    with pytest.raises(ZamstatsCpiError):
        run_fetch(handler)
    assert calls == [FAKE.url]


@pytest.mark.parametrize("status", [403, 404, 500, 503])
def test_bad_status_is_rejected_with_no_retry(vetted_fake, status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, content=b"Cloudflare challenge secret body")

    with pytest.raises(ZamstatsCpiError, match=f"unexpected HTTP status {status}") as info:
        run_fetch(handler)
    assert len(calls) == 1 and "secret" not in str(info.value)


def test_wrong_content_type_is_rejected(vetted_fake):
    with pytest.raises(ZamstatsCpiError, match="content type"):
        run_fetch(ok(content_type="text/html"))


def test_oversized_declared_or_streamed_body_is_rejected(vetted_fake):
    with pytest.raises(ZamstatsCpiError, match="larger than expected"):
        run_fetch(ok(headers={"content-length": str(src.MAX_PDF_BYTES + 1)}))
    big = b"%PDF-" + b"x" * (src.MAX_PDF_BYTES + 10)
    with pytest.raises(ZamstatsCpiError, match="larger than expected"):
        run_fetch(ok(body=big))


def test_bad_pdf_magic_is_rejected(vetted_fake):
    body = b"<html>" + BODY
    pub = dataclasses.replace(FAKE, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
    with pytest.raises(ZamstatsCpiError, match="not a PDF"):
        src.verify_publication(body, pub)


def test_hash_mismatch_and_size_mismatch_are_rejected_without_leaking_bytes(vetted_fake):
    tampered = BODY[:-1] + b"!"
    with pytest.raises(ZamstatsCpiError, match="vetted SHA-256") as info:
        run_fetch(ok(body=tampered))
    assert "synthetic" not in str(info.value)
    with pytest.raises(ZamstatsCpiError, match="unexpected file size"):
        run_fetch(ok(body=BODY + b"x"))


def test_exceptions_never_carry_response_text_or_values(vetted_fake):
    secret = b"%PDF- SECRET-SOURCE-VALUE 564.62"
    with pytest.raises(ZamstatsCpiError) as info:
        run_fetch(ok(body=secret))
    assert "SECRET" not in str(info.value) and "564.62" not in str(info.value)


# ------------------------------------------------------------------ extraction of pinned pages only
def test_parse_publication_extracts_only_the_pinned_pages_and_never_scans_the_document():
    requested: list[int] = []
    from pathlib import Path
    horizontal = (Path(__file__).parent / "fixtures" / "zamstats_cpi_monthly_wrapped_tail.txt").read_text()
    table = TableSpec("Table 5", HORIZONTAL_13, 9, r"Table 5: Overall Monthly Inflation Rate for Food and Non-Food Items",
                      date(2098, 10, 1), date(2099, 10, 1),
                      (("monthly_inflation", "all_items", True), ("monthly_inflation", "food", True), ("monthly_inflation", "non_food", True)),
                      base_on_page=False, wrapped_non_food_tail=4, canonical_first=date(2099, 1, 1), canonical_last=date(2099, 10, 1))
    pub = dataclasses.replace(FAKE, tables=(table,), layout_families=(HORIZONTAL_13,), base_pages=(25,))
    base_page = "Table 1.3 ...  Note: 2009 = 100\n"

    def extractor(pdf: bytes, page: int) -> str:
        requested.append(page)
        return {9: horizontal, 25: base_page}[page]

    result = src.parse_publication(BODY, pub, extractor)
    assert sorted(requested) == [9, 25] and result.manifest_key == pub.key and len(result.observations) == 39


def test_a_publication_without_a_text_layer_fails_and_the_error_is_value_free():
    def extractor(pdf: bytes, page: int) -> str:
        raise LayoutExtractionError("pdftotext produced no text for the pinned page")

    with pytest.raises(ZamstatsCpiError, match="no text for the pinned page"):
        src.parse_publication(BODY, FAKE, extractor)


def test_unverified_bytes_are_never_extracted():
    called = []
    with pytest.raises(ZamstatsCpiError, match="vetted SHA-256"):
        src.parse_publication(BODY + b"x", dataclasses.replace(FAKE, size_bytes=len(BODY) + 1), lambda b, p: called.append(p) or "")
    assert called == []


def test_parse_pages_requires_every_pinned_page_and_the_base_evidence():
    with pytest.raises(ZamstatsCpiError, match="pinned pages"):
        src.parse_pages({}, FAKE)
    pages = {p: "x" for p in FAKE.pages}
    with pytest.raises(ZamstatsCpiError):
        src.parse_pages(pages, FAKE)  # the base-evidence page does not print the 2009=100 base
