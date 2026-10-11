import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import Settings
from app.schemas.fx import BOZ_SOURCE_URL
from app.services import boz


NOW = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 10, 9, 14, 0, 51, tzinfo=timezone.utc)


@pytest.fixture
def payload():
    # Sanitized, deliberately altered values; shape matches the discovered API.
    return json.loads((Path(__file__).parent / "fixtures/boz_usd_rates.json").read_text())


def test_normalization_and_serialization(payload):
    quote = boz.parse_boz_quote(payload, retrieved_at=NOW)
    assert (quote.symbol, quote.base_currency, quote.quote_currency) == ("USDZMW", "USD", "ZMW")
    assert (quote.buy, quote.mid, quote.sell) == tuple(map(Decimal, ("19.8736", "19.8987", "19.9236")))
    assert quote.mid != (quote.buy + quote.sell) / 2
    assert quote.observed_at == OBSERVED
    assert quote.observed_at.second == 51
    assert quote.observed_at.tzinfo == timezone.utc
    assert quote.observed_at.astimezone(ZoneInfo("Africa/Lusaka")).hour == 16
    assert quote.retrieved_at == NOW
    assert quote.retrieved_at.tzinfo == timezone.utc
    assert quote.source == "boz" and quote.source_url == BOZ_SOURCE_URL
    assert quote.stale is False
    serialized = json.loads(quote.model_dump_json())
    assert (serialized["buy"], serialized["mid"], serialized["sell"]) == ("19.8736", "19.8987", "19.9236")
    assert serialized["observed_at"] == "2026-10-09T14:00:51Z"


def test_single_row_precision_and_display_text_ignored(payload):
    row = payload[0]
    row["buying"] = "19.123456789012345678901234567890"
    row["time"] = '<time datetime="2026-10-09T07:30:00Z">untrusted display text</time>'
    quote = boz.parse_boz_quote([row], retrieved_at=NOW)
    assert quote.buy == Decimal(row["buying"])
    assert quote.model_dump(mode="json")["buy"] == row["buying"]


def test_order_and_identical_duplicates_do_not_change_result(payload):
    expected = boz.parse_boz_quote(payload[:3], retrieved_at=NOW)
    assert boz.parse_boz_quote(list(reversed(payload)), retrieved_at=NOW) == expected


def test_equal_duplicate_precision_is_deterministic(payload):
    payload.append(dict(payload[1], buying="19.873600"))
    first = boz.parse_boz_quote(payload, retrieved_at=NOW).model_dump_json()
    reverse = boz.parse_boz_quote(list(reversed(payload)), retrieved_at=NOW).model_dump_json()
    assert first == reverse


@pytest.mark.parametrize("index", [0, 1])
def test_conflicting_duplicate_at_any_timestamp_fails(payload, index):
    duplicate = dict(payload[index], buying="18.0000")
    with pytest.raises(ValueError, match="Conflicting"):
        boz.parse_boz_quote(payload + [duplicate], retrieved_at=NOW)


@pytest.mark.parametrize("fragment", [
    None, "", "not HTML", "<time>09:30</time>",
    '<time datetime="2026-10-09T07:30:00Z">',
    '<div datetime="2026-10-09T07:30:00Z"></div>',
    '<time datetime="invalid">x</time>',
    '<time datetime="2026-02-30T07:30:00Z">x</time>',
    '<time datetime="2026-10-09T07:30:00">x</time>',
    '<time datetime="2026-10-09T07:30:00+02:00">x</time>',
    '<time datetime="2026-10-09T07:30:00Z" datetime="2026-10-09T10:30:00Z">x</time>',
    '<time datetime="2026-10-09T07:30:00Z">x</time><time datetime="2026-10-09T10:30:00Z">x</time>',
])
def test_bad_timestamp_fails_even_with_other_valid_rows(payload, fragment):
    payload[0]["time"] = fragment
    with pytest.raises(ValueError):
        boz.parse_boz_quote(payload, retrieved_at=NOW)


@pytest.mark.parametrize("field", ["buying", "mid_rate", "selling"])
@pytest.mark.parametrize("value", [None, "bad", "NaN", "Infinity", "0", "-1", 19.5])
def test_malformed_decimal_fails(payload, field, value):
    payload[0][field] = value
    with pytest.raises(ValueError):
        boz.parse_boz_quote(payload, retrieved_at=NOW)


@pytest.mark.parametrize("field", ["buying", "mid_rate", "selling", "time"])
def test_missing_field_fails(payload, field):
    del payload[0][field]
    with pytest.raises(ValueError):
        boz.parse_boz_quote(payload, retrieved_at=NOW)


@pytest.mark.parametrize("payload", [[], {}, {"value": []}, None, "html", [None]])
def test_empty_or_changed_shape_fails(payload):
    with pytest.raises(ValueError):
        boz.parse_boz_quote(payload, retrieved_at=NOW)


@pytest.mark.parametrize("age,stale", [
    (timedelta(hours=1), False),
    (timedelta(hours=72), False),
    (boz.FX_STALE_AFTER, False),
    (boz.FX_STALE_AFTER + timedelta(seconds=1), True),
])
def test_freshness_boundary(age, stale):
    assert boz.is_stale(NOW - age, NOW) is stale
    assert boz.is_stale((NOW - age).astimezone(ZoneInfo("Africa/Lusaka")), NOW) is stale


@pytest.mark.parametrize("observed,now", [
    (None, NOW), (NOW.replace(tzinfo=None), NOW),
    (NOW, NOW.replace(tzinfo=None)), (NOW + timedelta(seconds=1), NOW),
])
def test_unknown_or_future_time_never_fresh(observed, now):
    with pytest.raises(ValueError):
        boz.is_stale(observed, now)


def test_stale_quote_returned_and_retrieval_normalized(payload):
    later = (NOW + timedelta(days=5)).astimezone(ZoneInfo("Africa/Lusaka"))
    quote = boz.parse_boz_quote(payload, retrieved_at=later)
    assert quote.stale is True
    assert quote.observed_at == OBSERVED
    assert quote.retrieved_at.tzinfo == timezone.utc


def run_request(handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await boz.get_boz_fx_quote(client=client)
    return asyncio.run(run())


def test_http_success_and_configuration(payload, monkeypatch):
    url = "https://boz.example.test/rates"
    monkeypatch.setattr(boz, "get_settings", lambda: Settings(boz_usd_rates_url=url))
    def handler(request):
        assert str(request.url) == url
        assert request.method == "GET"
        assert request.headers["User-Agent"] == boz.BOZ_USER_AGENT
        assert request.headers["Accept"] == "application/json"
        assert request.extensions["timeout"]["read"] == boz.BOZ_TIMEOUT_SECONDS
        return httpx.Response(200, json=payload)
    quote = run_request(handler)
    assert quote.observed_at == OBSERVED
    assert quote.retrieved_at.tzinfo == timezone.utc


def test_http_timeout():
    def handler(request):
        raise httpx.ReadTimeout("private upstream detail", request=request)
    with pytest.raises(httpx.TimeoutException):
        run_request(handler)


@pytest.mark.parametrize("status", [302, 404, 429, 500])
def test_http_status_failure(status):
    with pytest.raises(httpx.HTTPStatusError):
        run_request(lambda request: httpx.Response(status, text="upstream details"))


@pytest.mark.parametrize("response", [
    httpx.Response(200, content="not JSON", headers={"Content-Type": "application/json"}),
    httpx.Response(200, json={"changed": []}),
    httpx.Response(200, content="<html>error</html>", headers={"Content-Type": "text/html"}),
])
def test_http_bad_payload(response):
    with pytest.raises(ValueError):
        run_request(lambda request: response)
