"""Bounded, sequential traversal of a BoZ Drupal JSON:API collection for manual jobs."""

import asyncio
from datetime import date
from urllib.parse import urlsplit

import httpx

JSONAPI_TYPES = ("application/vnd.api+json", "application/json")


class TruncatedSearchError(Exception):
    """The page cap was reached while the API still advertised another page."""


def next_link(payload: object, base_url: str) -> str | None:
    """The page's own `links.next`, accepted only if it targets the configured endpoint."""
    links = payload.get("links") if isinstance(payload, dict) else None
    nxt = links.get("next") if isinstance(links, dict) else None
    if nxt is None:
        return None
    href = nxt.get("href") if isinstance(nxt, dict) else None
    if not isinstance(href, str):
        raise ValueError("Malformed next link")
    got, want = urlsplit(href), urlsplit(base_url)
    if (got.scheme, got.netloc, got.path) != (want.scheme, want.netloc, want.path):
        raise ValueError("Next link leaves the BoZ endpoint")
    return href


def date_range_params(field: str, low: str, high: str) -> dict[str, str]:
    """Standard JSON:API/Drupal filter: field >= low AND field <= high."""
    return {
        "filter[lo][condition][path]": field, "filter[lo][condition][operator]": ">=",
        "filter[lo][condition][value]": low,
        "filter[hi][condition][path]": field, "filter[hi][condition][operator]": "<=",
        "filter[hi][condition][value]": high,
    }


async def fetch_pages(
    client: httpx.AsyncClient, base_url: str, params: dict[str, str], *, user_agent: str,
    max_pages: int, timeout: float = 10.0, pause: float = 1.0, sleep=asyncio.sleep,
) -> list[object]:
    """Sequential GETs following `links.next`; no retries. Fails closed if the cap truncates."""
    pages: list[object] = []
    url: str = base_url
    query: dict[str, str] | None = params
    for number in range(1, max_pages + 1):
        if number > 1:
            await sleep(pause)
        response = await client.get(
            url, params=query, timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/vnd.api+json"},
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in JSONAPI_TYPES:
            raise ValueError("Expected a JSON response")
        payload = response.json()
        pages.append(payload)
        following = next_link(payload, base_url)
        if following is None:
            return pages
        if number == max_pages:
            raise TruncatedSearchError("Page cap reached before the end of the archive")
        url, query = following, None
    return pages


def validate_range(start: date, end: date, *, today: date) -> None:
    if start > end:
        raise ValueError("--from must not be after --to")
    if end > today:
        raise ValueError("--to must not be in the future")
