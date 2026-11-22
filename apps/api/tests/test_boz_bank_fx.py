"""BoZ commercial bank FX parser and network seam: offline, SYNTHETIC fixtures only, all HTTP mocked (no live request)."""

import asyncio
import copy
import gzip
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

import bank_fx_builders as b
from app.services import boz_bank_fx as fx
from app.services.bank_fx_models import BankFxError

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2030, 1, 3, 8, 0, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def parse(doc, **kw):
    return fx.parse_bank_fx_document(doc, retrieved_at=NOW, **kw)


def one(doc, **kw):
    (snapshot,) = parse(doc, **kw)
    return snapshot


def rejects(doc, match=None, **kw):
    with pytest.raises(BankFxError, match=match):
        parse(doc, **kw)


# ------------------------------------------------------------------------------------------------------------ layouts and rows
def test_v2_layout_rows_and_exact_source_label():
    s = one(load("boz_bank_fx_v2"))
    assert s.table_layout == "v2" and s.rights_status == "review_required"
    assert {r.retail_source_label for r in s.rates} == {"BOARD RATES"}
    assert {r.slot for r in s.rates} == {"09:30", "12:30", "15:30"}
    assert [r.source_row_index for r in s.rates][:3] == [3, 3, 3]  # CMS row positions, in source order, header rows excluded
    assert sorted({r.source_row_index for r in s.rates}) == [3, 4, 5]


def test_v1_layout_with_bom_date_cell_and_variable_decimal_scale():
    s = one(load("boz_bank_fx_v1"))
    assert s.table_layout == "v1"
    assert {r.retail_source_label for r in s.rates} == {"Retail Rates"}  # never normalised into BOARD RATES
    first = [r for r in s.rates if r.slot == "09:30" and not r.is_market_average][0]
    assert (first.interbank_buy_text, first.interbank_sell_text, first.retail_buy_text, first.retail_sell_text) == ("10.1", "10.25", "10.123", "10.1234")
    assert first.interbank_buy == Decimal("10.1") and isinstance(first.interbank_buy, Decimal)


def test_progressive_node_publishes_only_the_first_slot_and_creates_no_rows_for_blank_slots():
    s = one(load("boz_bank_fx_progressive"))
    assert {r.slot for r in s.rates} == {"09:30"} and len(s.rates) == 3


def test_a_blank_slot_for_one_institution_only_skips_that_institution_and_slot():
    banks = b.default_banks()
    banks[0] = b.bank_row("ALPHA TEST BANK PLC", (b.quartet(10), None, b.quartet(12)))
    s = one(b.document(b.node(banks=banks)))
    assert [r.slot for r in s.rates if r.institution_name == "ALPHA TEST BANK PLC"] == ["09:30", "15:30"]


def test_bank_count_is_dynamic_and_no_name_list_is_assumed():
    for count in (1, 7):
        banks = [b.bank_row(f"SYNTHETIC BANK {i}", (b.quartet(10 + i),) * 3) for i in range(count)] + [b.bank_row("MARKET AVERAGE", (b.quartet(10),) * 3)]
        s = one(b.document(b.node(banks=banks)))
        assert len({r.institution_name for r in s.rates}) == count + 1


def test_names_are_normalised_for_identity_and_kept_raw():
    s = one(load("boz_bank_fx_v2"))
    by_row = {r.source_row_index: r for r in s.rates}
    assert by_row[3].institution_name_raw == "ALPHA TEST BANK PLC    " and by_row[3].institution_name == "ALPHA TEST BANK PLC"
    assert by_row[4].institution_name_raw == "BETA   TEST BANK LIMITED" and by_row[4].institution_name == "BETA TEST BANK LIMITED"
    banks = b.default_banks()
    banks[0][0] = "﻿  GAMMA \t TEST BANK \n"
    r = one(b.document(b.node(banks=banks))).rates[0]
    assert r.institution_name == "GAMMA TEST BANK" and r.institution_name_raw == banks[0][0]
    banks = b.default_banks()
    banks[0][0] = "alpha test bank plc"  # ordinary names are never case-folded
    assert one(b.document(b.node(banks=banks))).rates[0].institution_name == "alpha test bank plc"


def test_market_average_is_detected_case_insensitively_and_kept_separate():
    for layout, label in (("v2", "MARKET AVERAGE"), ("v1", "Market Average")):
        s = one(b.document(b.node(layout=layout)))
        average = [r for r in s.rates if r.is_market_average]
        assert {r.institution_name_raw for r in average} == {label}
        assert all(not r.is_market_average for r in s.rates if r.institution_name != label)
    banks = b.default_banks()
    banks[2][0] = "  market   AVERAGE "
    assert [r.is_market_average for r in one(b.document(b.node(banks=banks))).rates][-1] is True


@pytest.mark.parametrize("average_rows", [0, 2])
def test_exactly_one_market_average_row(average_rows):
    banks = [r for r in b.default_banks() if r[0] != "MARKET AVERAGE"]
    banks += [b.bank_row(f"MARKET AVERAGE{' ' * i}" if i else "MARKET AVERAGE", (b.quartet(10),) * 3) for i in range(average_rows)]
    banks = banks if average_rows != 2 else banks[:-1] + [b.bank_row("Market Average", (b.quartet(10),) * 3)]
    rejects(b.document(b.node(banks=banks)), "market average")


def test_duplicate_normalised_institution_and_blank_name_are_rejected():
    banks = b.default_banks()
    banks[1][0] = "ALPHA  TEST   BANK PLC"  # same identity as row 0 after normalisation
    rejects(b.document(b.node(banks=banks)), "duplicate institution")
    banks = b.default_banks()
    banks[1][0] = "   "
    rejects(b.document(b.node(banks=banks)), "blank institution")


def test_no_buy_le_sell_rule_is_introduced():
    banks = b.default_banks()
    banks[0] = b.bank_row("ALPHA TEST BANK PLC", (["20.0000", "10.0000", "30.0000", "5.0000"],) * 3)  # buy above sell everywhere
    s = one(b.document(b.node(banks=banks)))
    first = s.rates[0]
    assert first.interbank_buy > first.interbank_sell and first.retail_buy > first.retail_sell


# ------------------------------------------------------------------------------------------------------------------- decimals
@pytest.mark.parametrize("value", ["5.1234", "25.1234", "105.1234", "0.0001", "9.9", "9.90", "9.900", "9.9000", "1000000.5"])
def test_valid_rate_strings_are_kept_exactly(value):
    doc = b.cell(b.document(b.node()), 3, 1, value)
    r = one(doc).rates[0]
    assert r.interbank_buy_text == value and r.interbank_buy == Decimal(value) and str(r.interbank_buy) == value


@pytest.mark.parametrize("value", ["0.0000", "0.0", "-1.0000", "+1.0000", "1", "1.", ".5", "1.00000", "1e1", "1E+1", "NaN", "Infinity", "-Infinity",
                                   " 1.0000", "1.0000 ", "1.0000\n", "1,000.0000", "1.0000 USD", "٣.١٢٣٤", None, 1.5, 1, True, [], {}])
def test_invalid_rate_cells_reject_the_whole_node(value):
    rejects(b.cell(b.document(b.node()), 3, 2, value))


@pytest.mark.parametrize("quartet", [("10.0001", "10.0002", "10.0003", ""), ("10.0001", "", "", ""), ("", "10.0002", "10.0003", "10.0004"),
                                     ("10.0001", "10.0002", "x", "10.0004"), ("10.0001", "10.0002", "10.0003", None), ("10.0001", " ", "10.0003", "10.0004")])
def test_partial_or_malformed_quartets_reject(quartet):
    banks = b.default_banks(published=1)
    banks[0][1:5] = list(quartet)
    rejects(b.document(b.node(banks=banks)))


def test_no_float_is_involved():
    assert "float(" not in Path(fx.__file__).read_text()
    r = one(b.cell(b.document(b.node()), 3, 1, "0.1000")).rates[0]
    assert r.interbank_buy == Decimal("0.1000") and r.interbank_buy.as_tuple().exponent == -4


# ---------------------------------------------------------------------------------------------------------------- table structure
def test_unknown_or_malformed_headers_fail_closed():
    base = b.document(b.node())
    for row, column, value in [(0, 1, "09:30"), (0, 5, "13:30"), (0, 9, ""), (1, 1, "BOARD RATES"), (1, 3, "Retail Rates"), (1, 1, "INTERBANK RATES"),
                               (2, 2, "Buy"), (2, 1, "Bid"), (0, 0, "Bank"), (1, 0, "x"), (0, 2, "extra")]:
        rejects(b.cell(base, row, column, value))
    mixed = copy.deepcopy(b.document(b.node(layout="v1")))
    mixed["data"][0]["attributes"]["field_commercial_bank_rates"]["value"]["1"]["3"] = "BOARD RATES"  # v1 and v2 labels mixed
    rejects(mixed)


def test_v1_requires_its_source_date_cell():
    rejects(b.cell(b.document(b.node(layout="v1")), 0, 0, "﻿  "))


def test_table_keys_rows_and_cells_are_validated():
    def with_value(mutate):
        doc = copy.deepcopy(b.document(b.node()))
        mutate(doc["data"][0]["attributes"]["field_commercial_bank_rates"]["value"])
        return doc

    rejects(with_value(lambda v: v.update({"extra": {"0": "x"}})), "unexpected key")
    rejects(with_value(lambda v: v.pop("4")), "contiguous")  # a gap in the numeric row keys
    rejects(with_value(lambda v: v.update({"06": v["5"]})), "unexpected key|contiguous")
    rejects(with_value(lambda v: v["3"].pop("7")), "cells 0..12")
    rejects(with_value(lambda v: v["3"].update({"13": "1.0000"})), "cells 0..12")
    rejects(with_value(lambda v: v.update({k: v[k] for k in ("0", "1", "2")}) or [v.pop(k) for k in ("3", "4", "5")]), "no institution rows")
    doc = with_value(lambda v: v["3"].pop("weight"))  # weight is neither identity nor required
    assert parse(doc)
    doc = with_value(lambda v: v["3"].update({"weight": "999"}))  # nor ordering: row order follows the numeric key
    assert [r.source_row_index for r in parse(doc)[0].rates][0] == 3


def test_caption_and_weight_never_become_institution_rows():
    s = one(b.document(b.node()))
    assert all(r.institution_name not in {"COMMERCIAL BANK RATES", ""} for r in s.rates)
    assert max(r.source_row_index for r in s.rates) == 5


def test_a_node_without_any_published_slot_is_rejected():
    banks = [b.bank_row("ALPHA", (None, None, None)), b.bank_row("MARKET AVERAGE", (None, None, None))]
    rejects(b.document(b.node(banks=banks)), "publishes no slot")


# ---------------------------------------------------------------------------------------------------------- identity and timestamps
def test_json_api_identity_and_provenance_fields():
    s = one(load("boz_bank_fx_v2"))
    assert str(s.source_record_id) == b.NODE_UUID and s.source_revision_id == 22 and s.source_internal_nid == 11
    assert s.source_title == "SYNTHETIC RATES 02 01 30" and s.source_url == fx.BANK_FX_PAGE_URL and s.retrieved_at == NOW
    assert s.source_created.utcoffset().total_seconds() == 0 and s.source_changed > s.source_created


@pytest.mark.parametrize("field,value", [("drupal_internal__nid", 0), ("drupal_internal__nid", "11"), ("drupal_internal__nid", True), ("drupal_internal__vid", -1),
                                         ("drupal_internal__vid", 1.0), ("drupal_internal__vid", None), ("title", ""), ("title", None),
                                         ("created", "2030-01-02T13:36:38"), ("changed", "not a date"), ("revision_timestamp", "2030-01-02"),
                                         ("revision_timestamp", 5), ("commercial_bank_rates_date", "2030-01-02T15:36:15"),
                                         ("commercial_bank_rates_date", "2030-01-02"), ("commercial_bank_rates_date", None)])
def test_identity_and_timestamp_fields_are_validated(field, value):
    doc = copy.deepcopy(b.document(b.node()))
    doc["data"][0]["attributes"][field] = value
    rejects(doc)


def test_type_and_uuid_are_validated_and_the_title_is_not_an_identity():
    doc = copy.deepcopy(b.document(b.node()))
    doc["data"][0]["type"] = "node--other"
    rejects(doc, "resource type")
    doc = copy.deepcopy(b.document(b.node()))
    doc["data"][0]["id"] = "not-a-uuid"
    rejects(doc, "UUID")
    a, c = b.node(title="SAME TITLE"), b.node(uuid="00000000-0000-4000-8000-0000000000a2", vid=23, title="SAME TITLE")
    assert len(parse(b.document(a, c))) == 2  # equal titles never collapse nodes


def test_observation_date_is_the_calendar_date_at_the_supplied_offset():
    s = one(b.document(b.node(rates_date="2030-01-02T00:30:00+02:00")))  # 2030-01-01T22:30Z: the UTC date would be the previous day
    assert s.observation_date == date(2030, 1, 2) and s.source_rates_datetime.astimezone(timezone.utc).date() == date(2030, 1, 1)
    assert s.source_rates_datetime_raw == "2030-01-02T00:30:00+02:00"
    s = one(b.document(b.node(rates_date="2030-01-02T23:59:59-05:00")))
    assert s.observation_date == date(2030, 1, 2) and s.source_rates_datetime.astimezone(timezone.utc).date() == date(2030, 1, 3)


def test_created_changed_and_rates_time_have_no_ordering_requirement():
    s = one(b.document(b.node(created="2030-01-02T20:00:00+00:00", changed="2030-01-02T10:00:00+00:00", revision="2030-01-02T09:00:00+00:00",
                              rates_date="2030-01-02T01:00:00+02:00")))
    assert s.source_changed < s.source_created and s.source_revision_timestamp < s.source_changed


def test_slot_labels_are_labels_not_timestamps():
    s = one(load("boz_bank_fx_v2"))
    assert {type(r.slot) for r in s.rates} == {str} and not hasattr(s.rates[0], "slot_timestamp")


# ----------------------------------------------------------------------------------------------------------- document completeness
def test_duplicate_node_revision_in_one_response_is_rejected():
    n = b.node()
    rejects(b.document(n, copy.deepcopy(n)), "duplicate node revision")
    assert len(parse(b.document(n, b.node(vid=23)))) == 2  # another revision of the same node is a different identity


def test_links_next_makes_the_response_incomplete():
    rejects(b.document(b.node(), next_link="https://example.test/next"), "paginated")
    doc = b.document(b.node())
    doc["links"]["next"] = None
    assert parse(doc)
    doc = {"data": [b.node()]}  # no links at all
    assert parse(doc)


def test_empty_data_is_no_publication_only_when_allowed():
    assert parse(b.document(), allow_empty=True, requested_date=date(2030, 1, 2)) == []
    rejects(b.document(), "no publication")
    rejects({"links": {}}, "data must be a list")
    rejects([], "document must be an object")


def test_requested_date_must_match_every_node():
    d = date(2030, 1, 2)
    assert one(b.document(b.node()), requested_date=d)
    rejects(b.document(b.node(), b.node(uuid="00000000-0000-4000-8000-0000000000a2", rates_date="2030-01-03T10:00:00+02:00")), "different observation date", requested_date=d)
    rejects(b.document(b.node(rates_date="2030-01-02T00:10:00+02:00")), requested_date=date(2030, 1, 1))  # offset-local date, not UTC


def test_naive_retrieved_at_is_rejected():
    with pytest.raises(BankFxError):
        fx.parse_bank_fx_document(b.document(b.node()), retrieved_at=datetime(2030, 1, 3))


# ------------------------------------------------------------------------------------------------- exact group labels
def with_label(layout, value, column=3, row=1):
    return b.cell(b.document(b.node(layout=layout)), row, column, value)


@pytest.mark.parametrize("layout,label", [("v1", "Retail Rates"), ("v2", "BOARD RATES")])
def test_exact_retail_label_is_accepted_and_reaches_the_model_unchanged(layout, label):
    assert {r.retail_source_label for r in one(b.document(b.node(layout=layout))).rates} == {label}


@pytest.mark.parametrize("layout,good", [("v2", "BOARD RATES"), ("v1", "Retail Rates")])
def test_retail_label_variants_are_rejected(layout, good):
    variants = [f" {good}", f"{good} ", f" {good.replace(' ', '   ')} ", good.replace(" ", "  "), good.lower(), good.title() if good != "Retail Rates" else "retail rates",
                good + "\u00a0", "\ufeff" + good, good.replace(" ", "\u00a0")]
    for variant in variants:
        assert variant != good
        for column in (3, 7, 11):
            rejects(with_label(layout, variant, column), "layout")


@pytest.mark.parametrize("layout,good", [("v2", "INTERBANK"), ("v1", "Interbank Rates")])
def test_interbank_group_and_other_header_cells_are_not_normalised(layout, good):
    for variant in (f" {good}", f"{good} ", good.lower()):
        rejects(with_label(layout, variant, 1), "layout")
    rejects(with_label(layout, " Buy", 1, row=2), "layout")
    rejects(with_label(layout, "9:30 ", 1, row=0), "layout")
    rejects(with_label(layout, " ", 2, row=1), "layout")  # a whitespace-only cell is not blank either
    name_cell = 0 if layout == "v1" else 0
    rejects(with_label(layout, "Bank Name ", name_cell, row=1 if layout == "v1" else 0), "layout")


def test_only_the_v1_date_cell_is_read_tolerantly():
    for text_ in ("\ufeff\u00a002-01-2030", "  02-01-2030 ", "anything"):
        assert one(b.cell(b.document(b.node(layout="v1")), 0, 0, text_)).table_layout == "v1"
    rejects(b.cell(b.document(b.node(layout="v1")), 0, 0, " \ufeff "), "layout")


# -------------------------------------------------------------------------------------------------- source-table digest
def digest(doc):
    return one(doc).source_table_sha256


def banks_with_blank(name="EXAMPLE BANK A", at=2):
    banks = b.default_banks()
    return banks[:at] + [b.bank_row(name, (None, None, None))] + banks[at:]


def test_digest_is_lowercase_sha256_hex_and_deterministic():
    d = digest(b.document(b.node()))
    assert re.fullmatch(r"[0-9a-f]{64}", d) and d == digest(b.document(b.node()))
    assert d != digest(b.document(b.node(layout="v1")))


def test_blank_institution_is_parsed_without_rate_rows_but_changes_the_digest():
    a = one(b.document(b.node(banks=banks_with_blank("EXAMPLE BANK A"))))
    c = one(b.document(b.node(banks=banks_with_blank("EXAMPLE BANK B"))))
    assert a.rates == c.rates and "EXAMPLE BANK A" not in {r.institution_name for r in a.rates}  # no fake or nullable rows
    assert a.source_table_sha256 != c.source_table_sha256


def test_blank_row_order_weight_header_caption_and_cell_changes_change_the_digest():
    base = digest(b.document(b.node(banks=banks_with_blank())))
    assert digest(b.document(b.node(banks=banks_with_blank(at=3)))) != base  # blank row in another position
    two = b.default_banks()
    assert digest(b.document(b.node(banks=two[:2] + [b.bank_row("X", (None,) * 3), b.bank_row("Y", (None,) * 3)] + two[2:]))) != \
        digest(b.document(b.node(banks=two[:2] + [b.bank_row("Y", (None,) * 3), b.bank_row("X", (None,) * 3)] + two[2:])))
    doc = b.document(b.node(banks=banks_with_blank()))
    weighted = copy.deepcopy(doc)
    weighted["data"][0]["attributes"]["field_commercial_bank_rates"]["value"]["5"]["weight"] = "123"
    assert digest(weighted) != base
    no_weight = copy.deepcopy(doc)
    del no_weight["data"][0]["attributes"]["field_commercial_bank_rates"]["value"]["5"]["weight"]
    assert digest(no_weight) != base
    captioned = copy.deepcopy(doc)
    captioned["data"][0]["attributes"]["field_commercial_bank_rates"]["value"]["caption"] = "OTHER"
    assert digest(captioned) != base
    assert digest(b.cell(doc, 0, 0, "Bank Name")) == base  # unchanged value, same digest
    assert digest(b.cell(doc, 0, 1, "9:30")) == base
    assert digest(b.cell(b.document(b.node(layout="v1", banks=banks_with_blank())), 0, 0, "\ufeff 03-01-2030")) != \
        digest(b.document(b.node(layout="v1", banks=banks_with_blank())))  # the raw v1 date cell is source content


def test_published_cell_text_changes_the_digest_even_when_numerically_equal():
    base = digest(b.document(b.node()))
    assert digest(b.cell(b.document(b.node()), 3, 1, "10.0000")) != base


def test_json_object_key_order_does_not_change_the_digest_but_numeric_row_order_does():
    doc = b.document(b.node())
    value = doc["data"][0]["attributes"]["field_commercial_bank_rates"]["value"]
    shuffled = copy.deepcopy(doc)
    sv = shuffled["data"][0]["attributes"]["field_commercial_bank_rates"]
    sv["value"] = {k: dict(reversed(list(v.items()))) if isinstance(v, dict) else v for k, v in reversed(list(value.items()))}
    assert digest(shuffled) == digest(doc)
    swapped = copy.deepcopy(doc)
    sw = swapped["data"][0]["attributes"]["field_commercial_bank_rates"]["value"]
    sw["3"], sw["4"] = sw["4"], sw["3"]  # the row contents of keys 3 and 4 exchanged: numeric CMS row order changed
    assert digest(swapped) != digest(doc)


# ------------------------------------------------------------------------------------------------------------------------ adapter
def run(handler, day=date(2030, 1, 2)):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped), headers={"User-Agent": fx.BANK_FX_USER_AGENT},
                                     timeout=fx.BANK_FX_TIMEOUT_SECONDS, follow_redirects=False) as client:
            return await fx.fetch_bank_fx_snapshots(client, day, retrieved_at=NOW)

    return calls, go


class ChunkStream(httpx.AsyncByteStream):
    """A response body that is really streamed (never pre-buffered), so the adapter's own accumulator is what is exercised."""

    def __init__(self, chunks):
        self.chunks, self.yielded = list(chunks), 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


def streamed(chunks, status=200, media="application/vnd.api+json", **headers):
    return httpx.Response(status, headers={"content-type": media, **headers}, stream=ChunkStream(chunks))


def ok(doc, status=200, media="application/vnd.api+json"):
    return lambda request: streamed([json.dumps(doc).encode()], status, media)


def test_one_exact_date_get_with_the_researched_query_and_headers():
    calls, go = run(ok(b.document(b.node())))
    (snapshot,) = asyncio.run(go())
    assert snapshot.observation_date == date(2030, 1, 2) and len(calls) == 1  # one GET, no pagination, no retry
    request = calls[0]
    assert request.method == "GET" and request.url.host == "www.boz.zm" and request.url.path == "/jsonapi/node/commercial_bank_daily_rates"
    assert request.url.query.decode() == (
        "filter[commercial_bank_rates_date][operator]=BETWEEN&filter[commercial_bank_rates_date][value][0]=2030-01-02T00:00:00"
        "&filter[commercial_bank_rates_date][value][1]=2030-01-02T23:59:59&sort=-created&page[limit]=10")
    assert request.headers["accept"] == "application/vnd.api+json" and request.headers["user-agent"] == fx.BANK_FX_USER_AGENT
    assert "ZM-Terminal" in fx.BANK_FX_USER_AGENT and fx.BANK_FX_TIMEOUT_SECONDS == 10.0 and fx.new_client().timeout.read == 10.0
    assert fx.new_client().follow_redirects is False


def test_the_configured_url_is_the_vetted_boz_endpoint():
    from app.config import get_settings

    assert get_settings().boz_commercial_bank_rates_url == "https://www.boz.zm/jsonapi/node/commercial_bank_daily_rates"
    for bad in ("http://www.boz.zm/x", "https://example.test/x", "https://user@www.boz.zm/x", "https://www.boz.zm:8443/x"):
        with pytest.raises(BankFxError):
            fx.exact_date_url(date(2030, 1, 2), base=bad)


def test_an_empty_exact_date_result_is_no_publication_and_no_other_date_is_requested():
    calls, go = run(ok(b.document()))
    assert asyncio.run(go()) == [] and len(calls) == 1


def test_a_node_for_another_date_in_the_response_fails():
    calls, go = run(ok(b.document(b.node(rates_date="2030-01-03T10:00:00+02:00"))))
    with pytest.raises(BankFxError, match="different observation date"):
        asyncio.run(go())


def test_non_jsonapi_content_type_is_rejected():
    for media in ("application/json", "text/html", ""):
        calls, go = run(ok(b.document(b.node()), media=media))
        with pytest.raises(BankFxError, match="JSON:API"):
            asyncio.run(go())
    calls, go = run(ok(b.document(b.node()), media="Application/Vnd.Api+JSON; charset=utf-8"))
    assert asyncio.run(go())


def test_oversized_response_is_rejected():
    big = b"{" + b" " * fx.MAX_RESPONSE_BYTES + b"}"
    calls, go = run(lambda r: streamed([big]))
    with pytest.raises(BankFxError, match="too large"):
        asyncio.run(go())
    assert fx.MAX_RESPONSE_BYTES == 1_048_576


@pytest.mark.parametrize("status", [301, 302, 307, 400, 404, 429, 500, 503])
def test_redirects_and_http_failures_are_errors_without_retry(status):
    calls, go = run(lambda r: httpx.Response(status, headers={"location": "https://example.test/", "content-type": "application/vnd.api+json"}))
    with pytest.raises(BankFxError):
        asyncio.run(go())
    assert len(calls) == 1  # no retry, no redirect followed


def test_timeouts_and_transport_errors_are_wrapped_and_not_retried():
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    calls, go = run(boom)
    with pytest.raises(BankFxError, match="request failed"):
        asyncio.run(go())
    assert len(calls) == 1


def test_invalid_json_and_changed_shapes_fail():
    calls, go = run(lambda r: streamed([b"{not json"]))
    with pytest.raises(BankFxError, match="not valid JSON"):
        asyncio.run(go())
    for doc in ({"data": {"id": 1}}, {"unexpected": []}, [1, 2]):
        calls, go = run(ok(doc))
        with pytest.raises(BankFxError):
            asyncio.run(go())
    calls, go = run(ok(b.document(b.node(), next_link="https://example.test/next")))
    with pytest.raises(BankFxError, match="paginated"):
        asyncio.run(go())
    assert len(calls) == 1  # truncated results fail; the adapter never follows links.next


def test_provider_policy_is_registered_and_local_internal():
    from app.services.provider_policy import BOZ_BANK_FX_POLICY, BOZ_USD_POLICY, PROVIDER_POLICIES

    p = BOZ_BANK_FX_POLICY
    assert PROVIDER_POLICIES[p.dataset_id] is p and p.dataset_id == "boz_commercial_bank_daily_rates" != BOZ_USD_POLICY.dataset_id
    assert (p.provider_id, p.authentication, p.raw_response_storage, p.normalized_storage_allowed, p.attribution_required) == ("boz", "none", "disabled", True, True)
    assert (p.redistribution, p.commercial_use, p.personal_data_expected) == ("review_required", "review_required", False)
    assert {"source_record_id", "source_revision_id", "observation_date"} <= set(p.required_provenance) and "retrieved_at" in p.required_timestamps
    assert "no retries" in p.request_guidance and "ONE GET per requested date" in p.request_guidance


def test_no_live_network_import_side_effects():
    """The adapter module performs no request at import, and the importer/router are untouched by this slice."""
    root = Path(__file__).parents[1] / "app"
    assert not (root / "jobs" / "import_bank_fx.py").exists()
    assert "bank_fx" not in (root / "api" / "router.py").read_text().lower()


# ----------------------------------------------------------------------------------------------- bounded raw streaming
LIMIT = fx.MAX_RESPONSE_BYTES


def valid_padded(total):
    """A valid JSON document of exactly `total` bytes (trailing JSON whitespace)."""
    raw = json.dumps(b.document(b.node())).encode()
    assert total >= len(raw)
    return raw + b" " * (total - len(raw))


def split(raw, size):
    return [raw[i:i + size] for i in range(0, len(raw), size)]


def test_request_asks_for_identity_encoding():
    calls, go = run(ok(b.document(b.node())))
    asyncio.run(go())
    assert calls[0].headers["accept-encoding"] == "identity"
    assert fx.new_client().headers["accept-encoding"] == "identity"


def test_many_small_chunks_below_the_limit_are_accepted():
    calls, go = run(lambda r: streamed(split(valid_padded(300_000), 1000)))
    assert len(asyncio.run(go())) == 1


@pytest.mark.parametrize("total", [LIMIT - 1, LIMIT])
def test_exactly_at_and_just_below_the_limit_are_accepted(total):
    calls, go = run(lambda r: streamed(split(valid_padded(total), 50_000)))
    assert len(asyncio.run(go())) == 1


def test_one_byte_over_the_limit_is_rejected():
    calls, go = run(lambda r: streamed(split(valid_padded(LIMIT + 1), 50_000)))
    with pytest.raises(BankFxError, match="too large"):
        asyncio.run(go())


def test_a_single_huge_chunk_is_rejected_without_being_accumulated():
    calls, go = run(lambda r: streamed([b" " * (8 * LIMIT)]))
    with pytest.raises(BankFxError, match="too large"):
        asyncio.run(go())


def test_the_accumulator_never_exceeds_the_limit():
    seen = []

    async def chunks(sizes):
        for n in sizes:
            yield b"x" * n

    async def feed(sizes, limit):
        return await fx.read_capped(chunks(sizes), limit, observe=seen.append)

    assert len(asyncio.run(feed([4, 4, 4], 12))) == 12 and max(seen) == 12  # exactly at the cap
    seen.clear()
    with pytest.raises(BankFxError):
        asyncio.run(feed([4, 4, 5, 4], 12))  # the chunk that would cross the cap is never appended
    assert max(seen) <= 8
    seen.clear()
    with pytest.raises(BankFxError):
        asyncio.run(feed([100], 12))  # a single chunk beyond the remaining capacity
    assert seen == []
    seen.clear()
    with pytest.raises(BankFxError):
        asyncio.run(feed([12, 1], 12))
    assert max(seen) == 12
    assert asyncio.run(feed([], 12)) == b""


def test_the_real_adapter_stops_reading_after_the_cap():
    stream = ChunkStream([b" " * 50_000] * 40)  # 2 MB offered in raw chunks

    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/vnd.api+json"}, stream=stream)

    calls, go = run(handler)
    with pytest.raises(BankFxError, match="too large"):
        asyncio.run(go())
    assert stream.yielded * 50_000 <= LIMIT + fx.READ_CHUNK_BYTES + 50_000 and stream.yielded < 40  # the rest of the body was never pulled


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "compress", "zstd", "GZIP", "x-unknown", "gzip, identity", "identity, gzip", ""])
def test_compressed_or_unknown_content_encoding_is_rejected_before_any_body_is_read(encoding):
    stream = ChunkStream([b"{}"])
    calls, go = run(lambda r: httpx.Response(200, headers={"content-type": "application/vnd.api+json", "content-encoding": encoding}, stream=stream))
    with pytest.raises(BankFxError, match="Content-Encoding"):
        asyncio.run(go())
    assert stream.yielded == 0 and len(calls) == 1


def test_a_tiny_gzip_bomb_is_rejected_for_its_encoding_and_never_decoded():
    bomb = gzip.compress(b" " * (8 * 1024 * 1024))
    assert len(bomb) < 20_000
    stream = ChunkStream([bomb])
    calls, go = run(lambda r: httpx.Response(200, headers={"content-type": "application/vnd.api+json", "content-encoding": "gzip"}, stream=stream))
    with pytest.raises(BankFxError, match="Content-Encoding"):
        asyncio.run(go())
    assert stream.yielded == 0  # not a single (compressed or decoded) byte was pulled


def test_identity_content_encoding_is_accepted():
    for header in ("identity", "Identity", "identity, identity"):
        calls, go = run(lambda r, h=header: streamed([json.dumps(b.document(b.node())).encode()], **{"content-encoding": h}))
        assert len(asyncio.run(go())) == 1
