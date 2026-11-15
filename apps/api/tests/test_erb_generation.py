"""ERB 2025 Annual Statistical Bulletin parser (generation by station, Tables 13-16, plus the Table 12 diagnostic).

Two layers, deliberately separate:
- OFFLINE unit tests run on tests/fixtures/erb_bulletin_2025_pdf{22,23,24}.txt: SYNTHETIC pages that keep only what parser identity
  needs (exact captions, station headers, the 2013-2025 row structure, the Table 14 continuation, Table 16's stacked header, dash
  and zero cells, totals) with invented values (1.00, 2.00, ...). They are not the published data. The pinned residual shapes
  (Table 16 +0.05 in 2024 and +0.01 in 2025, Table 12 / Table 16 +2.50) are reproduced with invented numbers.
- ONE optional real-source test parses the pinned bulletin from a local cached copy (ZM_ERB_BULLETIN_PDF) and checks fidelity to
  the actual source. The PDF is never committed or downloaded by these tests (rights status review_required).
"""

import asyncio
import dataclasses
import hashlib
import os
import re
import shutil
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.services import erb_generation_source as src
from app.services import erb_generation_table as tab
from app.services.erb_generation_models import ErbGenerationError, NOT_REPORTED, REPORTED

FIXTURES = Path(__file__).parent / "fixtures"
D = Decimal


def pages() -> dict[int, str]:
    return {p: (FIXTURES / f"erb_bulletin_2025_pdf{p}.txt").read_text() for p in (22, 23, 24)}


def edit(pg: dict[int, str], page: int, old: str, new: str) -> dict[int, str]:
    assert pg[page].count(old) == 1, f"fixture edit anchor must be unique: {old!r}"
    out = dict(pg)
    out[page] = pg[page].replace(old, new)
    return out


def parse(pg=None):
    return src.parse_pages(pg or pages())


def lines_of(page: int) -> list[str]:
    return pages()[page].split("\n")


def line_with(page: int, prefix: str) -> str:
    hits = [l for l in lines_of(page) if l.strip().startswith(prefix)]
    assert len(hits) == 1, prefix
    return hits[0]


def replace_cell(row: str, n: int, new: str) -> str:
    """Replace the n-th whitespace-separated token of a row (0 is the year), keeping the rest of the line untouched."""
    spans = [m.span() for m in re.finditer(r"\S+", row)]
    a, b = spans[n]
    return row[:a] + new + row[b:]


def table_row(page: int, table_caption: str, year: int) -> str:
    L = lines_of(page)
    start = next(i for i, l in enumerate(L) if l.strip().startswith(table_caption))
    return next(l for l in L[start:] if re.match(rf"^\s*{year}\s", l))


RESULT = parse()


# ------------------------------------------------------------------ source identity
PDF = b"%PDF-1.7 synthetic bytes for identity checks"
MANIFEST = dataclasses.replace(src.BULLETIN_2025, sha256=hashlib.sha256(PDF).hexdigest(), size_bytes=len(PDF))


def test_manifest_pins_the_approved_bulletin():
    m = src.BULLETIN_2025
    assert (m.url, m.size_bytes, m.pdf_pages, m.publication_year, m.rights_status) == (
        "https://www.erb.org.zm/wp-content/uploads/files/statBullet2025.pdf", 1_193_636, 42, 2025, "review_required")
    assert m.sha256 == "03fa6051ef49ed02aef527d1264c6f389479747dd4b022508f5ae54f3f5e4203"
    assert m.title == "2025 ANNUAL STATISTICAL BULLETIN" and m.cover_date == "February 2026"
    assert m.pdf_to_printed_page == {22: 15, 23: 16, 24: 17} and m.tables == (12, 13, 14, 15, 16)


def test_correct_hash_and_size_are_accepted():
    src.verify_bulletin(PDF, MANIFEST)


def test_changed_hash_is_rejected():
    with pytest.raises(ErbGenerationError, match="SHA-256"):
        src.verify_bulletin(PDF, dataclasses.replace(MANIFEST, sha256="0" * 64))


def test_changed_size_is_rejected():
    with pytest.raises(ErbGenerationError, match="size"):
        src.verify_bulletin(PDF + b"x", MANIFEST)


def test_non_pdf_bytes_are_rejected():
    body = b"not a pdf at all, same length....."
    m = dataclasses.replace(MANIFEST, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
    with pytest.raises(ErbGenerationError, match="not a PDF"):
        src.verify_bulletin(body, m)


def test_parse_bulletin_verifies_then_extracts_only_the_pinned_pages():
    asked = []
    texts = pages()

    def extractor(data, page):
        asked.append(page)
        return texts[page]

    result = src.parse_bulletin(PDF, extractor, MANIFEST)
    assert asked == [22, 23, 24] and len(result.observations) == 390
    with pytest.raises(ErbGenerationError, match="SHA-256"):
        src.parse_bulletin(PDF, extractor, dataclasses.replace(MANIFEST, sha256="1" * 64))


def test_extraction_failure_is_reported_as_a_parser_error():
    from app.services.pdf_layout_text import LayoutExtractionError

    def boom(data, page):
        raise LayoutExtractionError("pdftotext failed")

    with pytest.raises(ErbGenerationError, match="pdftotext failed"):
        src.parse_bulletin(PDF, boom, MANIFEST)


# ------------------------------------------------------------------ fetch (mock transport, no network)
def fetch(handler, manifest=MANIFEST):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await src.fetch_bulletin(client, manifest)
    return asyncio.run(go())


def ok(request):
    assert str(request.url) == src.BULLETIN_2025.url and request.method == "GET"
    return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})


def test_fetch_returns_verified_bytes():
    assert fetch(ok) == PDF


def test_fetch_rejects_redirect_wrong_status_content_type_and_oversize():
    redirect = lambda r: httpx.Response(302, headers={"location": "https://evil.example/x.pdf"})
    with pytest.raises(ErbGenerationError, match="HTTP status 302"):
        fetch(redirect)
    with pytest.raises(ErbGenerationError, match="HTTP status 404"):
        fetch(lambda r: httpx.Response(404))
    with pytest.raises(ErbGenerationError, match="content type"):
        fetch(lambda r: httpx.Response(200, content=PDF, headers={"content-type": "text/html"}))
    big = dataclasses.replace(MANIFEST, max_bytes=10)
    with pytest.raises(ErbGenerationError, match="larger"):
        fetch(ok, big)


def test_fetch_refuses_a_non_vetted_url_and_a_changed_file():
    with pytest.raises(ErbGenerationError, match="vetted"):
        fetch(ok, dataclasses.replace(MANIFEST, url="https://www.erb.org.zm/other.pdf"))
    with pytest.raises(ErbGenerationError, match="SHA-256"):
        fetch(lambda r: httpx.Response(200, content=PDF[:-1] + b"X", headers={"content-type": "application/pdf"}))


# ------------------------------------------------------------------ table identity
@pytest.mark.parametrize("number,page", [(13, 22), (14, 22), (15, 24), (16, 24)])
def test_modified_caption_is_rejected(number, page):
    caption = tab.TABLE_BY_NUMBER[number].caption
    changed = caption.replace("Electricity generation", "Electricity Generation")
    anchor = re.search(re.escape(caption.split()[0] + " " + caption.split()[1]) + r".*", pages()[page]).group(0)
    with pytest.raises(ErbGenerationError, match=f"Table {number}.*caption"):
        parse(edit(pages(), page, anchor, anchor.replace(caption, changed)))


@pytest.mark.parametrize("number,page", [(13, 22), (14, 22), (15, 24), (16, 24)])
def test_missing_table_is_rejected(number, page):
    caption = tab.TABLE_BY_NUMBER[number].caption
    anchor = re.search(re.escape(f"Table {number}:") + r".*", pages()[page]).group(0)
    with pytest.raises(ErbGenerationError, match=f"Table {number}"):
        parse(edit(pages(), page, anchor, ""))


def test_duplicate_table_identity_is_rejected():
    pg = pages()
    pg[24] = pg[24].replace("2.1.4 ", "Table 15: duplicate\n2.1.4 ", 1)
    with pytest.raises(ErbGenerationError, match="exactly one caption"):
        parse(pg)


def test_pages_outside_the_pinned_set_are_refused():
    extra = {**pages(), 25: "x"}
    with pytest.raises(ErbGenerationError, match="pinned pages"):
        parse(extra)
    missing = pages()
    del missing[23]
    with pytest.raises(ErbGenerationError, match="pinned pages"):
        parse(missing)


def test_wrong_printed_page_number_is_rejected():
    pg = pages()
    pg[24] = pg[24].rstrip("\f\n ").rsplit("17", 1)[0] + "18\n\f"
    with pytest.raises(ErbGenerationError, match="printed page number 17"):
        parse(pg)


def test_wrong_header_columns_are_rejected_for_regular_tables():
    row = next(l for l in lines_of(22) if "Kariba North Bank" in l and "Victoria" in l)
    with pytest.raises(ErbGenerationError, match="Table 13.*header"):
        parse(edit(pages(), 22, row, row.replace("Kariba North Bank", "Kariba North")))
    with pytest.raises(ErbGenerationError, match="Table 13.*header"):  # reordered station columns
        parse(edit(pages(), 22, row, row.replace("Kariba North Bank", "@@").replace("Kafue Gorge", "Kariba North Bank").replace("@@", "Kafue Gorge")))


# ------------------------------------------------------------------ Table 14 continuation
def test_table14_two_page_structure_is_accepted():
    rows = [o for o in RESULT.observations if o.source_table == "Table 14"]
    assert {o.source_pdf_page for o in rows if o.observation_year <= 2020} == {22}
    assert {o.source_pdf_page for o in rows if o.observation_year >= 2021} == {23}
    assert {o.source_printed_page for o in rows if o.observation_year >= 2021} == {16}


def test_missing_table14_continuation_page_is_rejected():
    pg = pages()
    del pg[23]
    with pytest.raises(ErbGenerationError):
        parse(pg)
    blank = {**pages(), 23: "\n16\n"}
    with pytest.raises(ErbGenerationError):
        parse(blank)


def test_malformed_repeated_header_on_continuation_is_rejected():
    header = next(l for l in lines_of(23) if l.strip().startswith("Year"))
    with pytest.raises(ErbGenerationError, match="Table 14.*header"):
        parse(edit(pages(), 23, header, header.replace("Lunzua", "Lunzua2")))


def test_caption_repeated_on_continuation_page_is_rejected():
    pg = pages()
    pg[23] = tab.TABLE_BY_NUMBER[14].caption + "\n" + pg[23]
    with pytest.raises(ErbGenerationError, match="repeated"):
        parse(pg)


def test_stray_content_after_continuation_rows_is_rejected():
    pg = pages()
    assert "\n\n\n" in pg[23]
    pg[23] = pg[23].replace("\n\n\n", "\n\nsome stray text\n\n", 1)
    with pytest.raises(ErbGenerationError, match="unexpected content"):
        parse(pg)


def test_continuation_is_not_confused_with_the_next_table():
    # Table 15 starts on page 24, not 23: moving its caption onto page 23 fails.
    cap = next(l for l in lines_of(24) if l.strip().startswith("Table 15:"))
    pg = edit(pages(), 24, cap, "")
    pg[23] = pg[23].rstrip("\f") + cap + "\n\f"
    with pytest.raises(ErbGenerationError):
        parse(pg)


# ------------------------------------------------------------------ Table 16 header
EXPECTED_T16 = (
    "Bangweulu Power Limited", "Itezhi tezhi Power Corporation", "Kafue Gorge Lower", "Kariba North Bank Extension",
    "Lunsemfwa Hydro Power Limited", "Maamba Energy Limited", "Ndola Energy Corporation Limited", "Kafue Gorge Mini Hydro",
    "Chisamba", "Solar century", "United Capital Fertiliser", "Ngonye Power Limited", "Dangote", "CEC Emergency Generators",
)


def t16_header_and_first_row():
    L = lines_of(24)
    cap = next(i for i, l in enumerate(L) if l.strip().startswith("Table 16:"))
    first = next(i for i in range(cap, len(L)) if re.match(r"^\s*2013\s", L[i]))
    return L[cap + 1:first], L[first]


def test_table16_exact_ordered_header_is_accepted_and_solar_century_is_rebuilt_from_wrapped_lines():
    header, first = t16_header_and_first_row()
    cols = tab._stacked_header_columns(header, first, "Table 16")
    assert cols == ["Year", *EXPECTED_T16, "Total GWh"]
    assert cols[10] == "Solar century" and "Solarcentury" not in cols
    assert tab.TABLE_BY_NUMBER[16].stations == EXPECTED_T16
    assert [o.station_name for o in RESULT.observations if o.source_table == "Table 16"][:14] == list(EXPECTED_T16)


def mutate_t16_header(fn):
    header, first = t16_header_and_first_row()
    old = "\n".join(header)
    new = "\n".join(fn(list(header)))
    pg = pages()
    assert pg[24].count(old) == 1
    return {**pg, 24: pg[24].replace(old, new)}


def test_united_capital_fertiliser_limited_is_rejected():
    def add_limited(h):
        h.append(" " * 136 + "Limited")  # a printed "Limited" under the Fertiliser column
        return h
    with pytest.raises(ErbGenerationError, match="Table 16.*header"):
        parse(mutate_t16_header(add_limited))


def test_reordered_station_is_rejected():
    swap = lambda h: [l.replace("Chisamba", "@@").replace("Dangote", "Chisamba").replace("@@", "Dangote") for l in h]
    with pytest.raises(ErbGenerationError, match="Table 16.*header"):
        parse(mutate_t16_header(swap))


def test_missing_station_is_rejected():
    with pytest.raises(ErbGenerationError, match="Table 16.*header"):
        parse(mutate_t16_header(lambda h: [l.replace("Dangote", " " * 7) for l in h]))


def test_extra_station_is_rejected():
    def extra(h):
        h.append(" " * 195 + "Extra")  # beyond the total column
        return h
    with pytest.raises(ErbGenerationError, match="Table 16.*header"):
        parse(mutate_t16_header(extra))


def test_altered_solar_century_spelling_is_rejected():
    with pytest.raises(ErbGenerationError, match="Table 16.*header"):
        parse(mutate_t16_header(lambda h: [l.replace("century", "Century") for l in h]))


# ------------------------------------------------------------------ years
def test_duplicate_year_is_rejected():
    r = table_row(22, "Table 13:", 2014)
    with pytest.raises(ErbGenerationError, match="Table 13.*years"):
        parse(edit(pages(), 22, r, r.replace("2014", "2013", 1)))


def test_missing_year_is_rejected():
    r = table_row(22, "Table 13:", 2020)
    with pytest.raises(ErbGenerationError, match="Table 13.*years"):
        parse(edit(pages(), 22, r + "\n", ""))


def test_extra_year_is_rejected():
    r = table_row(22, "Table 13:", 2025)
    with pytest.raises(ErbGenerationError, match="Table 13.*years"):
        parse(edit(pages(), 22, r, r + "\n" + r.replace("2025", "2026", 1)))


def _swap_rows(a, b):
    pg = pages()
    pg[22] = pg[22].replace(a, "@@A").replace(b, a).replace("@@A", b)
    return pg


def test_reordered_years_are_rejected():
    a, b = table_row(22, "Table 13:", 2015), table_row(22, "Table 13:", 2016)
    with pytest.raises(ErbGenerationError, match="Table 13.*years"):
        parse(_swap_rows(a, b))


# ------------------------------------------------------------------ trailing rows / complete table region
def with_after_last_row(page: int, caption: str, text: str) -> dict[int, str]:
    """Insert `text` immediately after the last (2025) row of the table, i.e. after the accepted block and before the boundary."""
    last = table_row(page, caption, 2025)
    return edit(pages(), page, last, last + text)


@pytest.mark.parametrize("page,caption,table", [(24, "Table 16:", 16), (22, "Table 13:", 13), (24, "Table 15:", 15)])
def test_blank_line_then_2026_row_after_the_table_is_rejected(page, caption, table):
    r = table_row(page, caption, 2025)
    with pytest.raises(ErbGenerationError, match=f"Table {table}.*unexpected content after the table"):
        parse(with_after_last_row(page, caption, "\n\n" + r.replace("2025", "2026", 1)))


@pytest.mark.parametrize("page,caption,table", [(24, "Table 16:", 16), (22, "Table 13:", 13), (24, "Table 15:", 15)])
def test_blank_line_then_duplicate_2025_row_after_the_table_is_rejected(page, caption, table):
    r = table_row(page, caption, 2025)
    with pytest.raises(ErbGenerationError, match=f"Table {table}.*unexpected content after the table"):
        parse(with_after_last_row(page, caption, "\n\n" + r))


def test_valid_looking_extra_row_with_commentary_between_is_rejected():
    r = table_row(24, "Table 16:", 2025)
    with pytest.raises(ErbGenerationError, match="Table 16.*unexpected content"):
        parse(with_after_last_row(24, "Table 16:", "\n\nNote: revised\n" + r))
    with pytest.raises(ErbGenerationError, match="Table 16.*unexpected content"):
        parse(with_after_last_row(24, "Table 16:", "\n\n" + r.replace("2025", "2012", 1)))  # an earlier year too


def test_trailing_rows_after_both_pages_of_table14_are_rejected():
    first = table_row(22, "Table 14:", 2020)
    with pytest.raises(ErbGenerationError, match="Table 14.*unexpected content"):
        parse(edit(pages(), 22, first, first + "\n\n" + first))
    last = next(l for l in lines_of(23) if re.match(r"^\s*2025\s", l))
    with pytest.raises(ErbGenerationError, match="Table 14.*unexpected content"):
        parse(edit(pages(), 23, last, last + "\n\n" + last.replace("2025", "2026", 1)))


def test_year_shaped_row_outside_any_table_region_is_rejected():
    stray = " 2025   1.00   2.00   3.00   6.00"
    with pytest.raises(ErbGenerationError, match="year-shaped row is outside"):
        parse(edit(pages(), 22, "Table 12: National Electricity Generation 2025", "Table 12: National Electricity Generation 2025\n" + stray))


def test_extra_blank_lines_and_the_printed_page_footer_are_accepted():
    r = table_row(24, "Table 16:", 2025)
    assert len(parse(with_after_last_row(24, "Table 16:", "\n\n\n\n")).observations) == 390
    pg = pages()
    assert pg[24].rstrip("\f\n ").endswith("17") and pg[22].rstrip("\f\n ").endswith("15") and pg[23].rstrip("\f\n ").endswith("16")
    assert len(parse(pg).observations) == 390


# ------------------------------------------------------------------ missing values and cells
def obs(table, station, year):
    return next(o for o in RESULT.observations if (o.source_table, o.station_name, o.observation_year) == (table, station, year))


def test_dash_becomes_not_reported_null_and_is_never_zero_or_omitted():
    dash = obs("Table 14", "Lusiwasi Upper", 2013)
    assert (dash.generation_gwh, dash.value_state) == (None, NOT_REPORTED)
    assert sum(1 for o in RESULT.observations if o.value_state == NOT_REPORTED and o.generation_gwh is None) == 39
    assert all(o.generation_gwh is not None for o in RESULT.observations if o.value_state == REPORTED)


def test_printed_zero_is_decimal_zero_and_reported():
    zero = obs("Table 16", "Itezhi tezhi Power Corporation", 2013)
    assert zero.generation_gwh == D("0") and zero.value_state == REPORTED and isinstance(zero.generation_gwh, Decimal)


def test_numbers_are_exact_decimals_with_printed_precision():
    o = obs("Table 13", "Kafue Gorge", 2013)
    assert o.generation_gwh == D("1.00") and str(o.generation_gwh) == "1.00"
    assert str(obs("Table 13", "Kafue Gorge", 2025).generation_gwh) == "1013.00"  # thousands separator removed, exact
    assert str(obs("Table 16", "Maamba Energy Limited", 2018).generation_gwh) == "6.05"


@pytest.mark.parametrize("bad", ["8l0.00", "NaN", "Infinity", "1.2.3", "7.463,00", "--", "+5", "1e3"])
def test_unexpected_cell_tokens_are_rejected(bad):
    r = table_row(22, "Table 13:", 2013)
    with pytest.raises(ErbGenerationError):
        parse(edit(pages(), 22, r, replace_cell(r, 3, bad)))


def test_dash_is_rejected_where_the_source_has_none():
    r13 = table_row(22, "Table 13:", 2013)
    with pytest.raises(ErbGenerationError, match="dash is not allowed"):
        parse(edit(pages(), 22, r13, replace_cell(r13, 3, "-")))
    r16 = table_row(24, "Table 16:", 2013)
    with pytest.raises(ErbGenerationError, match="dash is not allowed"):
        parse(edit(pages(), 24, r16, replace_cell(r16, 1, "-")))


# ------------------------------------------------------------------ row widths
def test_row_width_constants_are_4_7_8_15():
    assert [t.row_width for t in tab.TABLES] == [4, 7, 8, 15]


def test_one_cell_short_or_extra_is_rejected():
    r = table_row(22, "Table 13:", 2013)
    with pytest.raises(ErbGenerationError, match="3 source value cells, expected 4"):
        parse(edit(pages(), 22, r, r.rsplit(None, 1)[0]))
    with pytest.raises(ErbGenerationError, match="5 source value cells, expected 4"):
        parse(edit(pages(), 22, r, r + "   1.00"))
    r16 = table_row(24, "Table 16:", 2013)
    with pytest.raises(ErbGenerationError, match="14 source value cells, expected 15"):
        parse(edit(pages(), 24, r16, r16.rsplit(None, 1)[0]))


def test_dash_still_counts_as_a_cell_and_does_not_shrink_the_row():
    r = table_row(22, "Table 14:", 2013)
    assert r.split().count("-") == 2 and len(r.split()) - 1 == 7
    short = r.replace("-", "", 1)
    with pytest.raises(ErbGenerationError, match="6 source value cells, expected 7"):
        parse(edit(pages(), 22, r, short))


# ------------------------------------------------------------------ grid
def test_grid_is_exactly_390_unique_observations_with_the_expected_per_table_counts():
    assert len(RESULT.observations) == 390
    counts = {t: sum(1 for o in RESULT.observations if o.source_table == t) for t in ("Table 13", "Table 14", "Table 15", "Table 16")}
    assert counts == {"Table 13": 39, "Table 14": 78, "Table 15": 91, "Table 16": 182}
    assert len({(o.source_table, o.station_name, o.observation_year) for o in RESULT.observations}) == 390
    assert {o.observation_year for o in RESULT.observations} == set(range(2013, 2026))


def test_totals_are_never_observations_and_stations_are_not_merged_across_tables():
    names = {o.station_name for o in RESULT.observations}
    assert not any("Total" in n for n in names)
    assert {o.source_table for o in RESULT.observations if o.station_name == "Itezhi tezhi"} == {"Table 15"}
    assert {o.source_table for o in RESULT.observations if o.station_name == "Itezhi tezhi Power Corporation"} == {"Table 16"}
    assert len(RESULT.table_totals) == 52 and {t.source_table for t in RESULT.table_totals} == {"Table 13", "Table 14", "Table 15", "Table 16"}


def test_check_grid_rejects_a_wrong_count_or_duplicate():
    obs_list = list(RESULT.observations)
    with pytest.raises(ErbGenerationError, match="390"):
        tab.check_grid(obs_list[:-1])
    with pytest.raises(ErbGenerationError, match="390|duplicate"):
        tab.check_grid(obs_list[:-1] + [obs_list[0]])


# ------------------------------------------------------------------ diagnostics
def test_tables_13_to_15_foot_exactly_and_partial_rows_never_use_zero():
    for f in RESULT.footing:
        if f.source_table != "Table 16":
            assert f.residual_gwh == 0
    partial = next(f for f in RESULT.footing if (f.source_table, f.observation_year) == ("Table 14", 2013))
    assert partial.status == "reported_values_only" and partial.stations_not_reported == 2
    assert partial.reported_sum_gwh == D("1.00") + D("2.00") + D("3.00") + D("5.00")  # the two dashes are not counted as zero
    assert partial.published_total_gwh == D("11.00") and partial.residual_gwh == 0
    assert sum(1 for f in RESULT.footing if f.status == "reported_values_only") == 20


def test_table16_pinned_residuals_are_preserved_and_published_totals_not_repaired():
    res = {(f.source_table, f.observation_year): f.residual_gwh for f in RESULT.footing if f.source_table == "Table 16"}
    assert res[("Table 16", 2024)] == D("0.05") and res[("Table 16", 2025)] == D("0.01")
    assert all(v == 0 for (t, y), v in res.items() if y not in (2024, 2025))
    f25 = next(f for f in RESULT.footing if (f.source_table, f.observation_year) == ("Table 16", 2025))
    assert f25.published_total_gwh == D("92.57") and f25.reported_sum_gwh == D("92.56")  # synthetic values, pinned shape
    f24 = next(f for f in RESULT.footing if (f.source_table, f.observation_year) == ("Table 16", 2024))
    assert f24.published_total_gwh == D("79.37") and f24.reported_sum_gwh == D("79.32")


def test_a_changed_station_value_changes_the_residual_and_fails_closed():
    r = table_row(22, "Table 13:", 2013)
    with pytest.raises(ErbGenerationError, match="footing residual"):
        parse(edit(pages(), 22, r, replace_cell(r, 1, "2.00")))


def test_a_changed_pinned_table16_residual_fails_closed():
    r = table_row(24, "Table 16:", 2025)
    with pytest.raises(ErbGenerationError, match="footing residual"):
        parse(edit(pages(), 24, r, replace_cell(r, 15, "92.56")))


def test_table12_table16_2025_diagnostic_is_preserved_without_repair():
    t = RESULT.table12
    assert (t.table16_total_gwh, t.table12_ipp_gwh, t.table16_minus_table12_ipp_gwh) == (D("92.57"), D("90.07"), D("2.50"))
    assert (t.table12_printed_total_gwh, t.table12_rows_sum_gwh, t.table12_footing_residual_gwh) == (D("98.57"), D("96.07"), D("2.50"))
    assert next(x for x in RESULT.table_totals if (x.source_table, x.observation_year) == ("Table 16", 2025)).published_total_gwh == D("92.57")


def test_table12_changes_fail_closed():
    line = next(l for l in lines_of(22) if l.strip().startswith("IPPs"))
    with pytest.raises(ErbGenerationError, match="Table 12"):
        parse(edit(pages(), 22, line, replace_cell(line, 1, "92.57")))
    cap = next(l for l in lines_of(22) if l.strip().startswith("Table 12:"))
    with pytest.raises(ErbGenerationError, match="Table 12"):
        parse(edit(pages(), 22, cap, cap.replace("National", "Nationwide")))
    lab = next(l for l in lines_of(22) if l.strip().startswith("ZESCO’s Diesel"))
    with pytest.raises(ErbGenerationError, match="Table 12"):
        parse(edit(pages(), 22, lab, lab.replace("Diesel", "Diesels")))


# ------------------------------------------------------------------ provenance
def test_observations_carry_provenance_and_groups():
    m = src.BULLETIN_2025
    cases = [("Table 13", "Kariba North Bank", 2021, 22, 15, "zesco_large_hydro"),
             ("Table 14", "Musonda Falls", 2025, 23, 16, "zesco_mini_hydro"),
             ("Table 15", "Shang'ombo", 2013, 24, 17, "zesco_diesel"),
             ("Table 16", "Dangote", 2025, 24, 17, "independent_power_producer")]
    for table, station, year, pdf, printed, group in cases:
        o = obs(table, station, year)
        assert (o.source_pdf_page, o.source_printed_page, o.station_group) == (pdf, printed, group)
        assert (o.source_url, o.source_sha256, o.source_publication, o.publication_year) == (
            m.url, m.sha256, "2025 ANNUAL STATISTICAL BULLETIN", 2025)
    assert obs("Table 16", "Dangote", 2025).generation_gwh == D("13.12")
    assert obs("Table 14", "Musonda Falls", 2025).generation_gwh == D("6.20")


def test_no_storage_or_network_symbols_leak_into_the_parser_modules():
    for mod in (tab, src):
        text = Path(mod.__file__).read_text().lower()
        assert "sqlalchemy" not in text and "psycopg" not in text and "insert into" not in text


# ------------------------------------------------------------------ real pinned PDF (optional source-fidelity test)
REAL = os.environ.get("ZM_ERB_BULLETIN_PDF")


@pytest.mark.skipif(not REAL or shutil.which("pdftotext") is None,
                    reason="set ZM_ERB_BULLETIN_PDF to a cached copy of the pinned bulletin (and install pdftotext)")
def test_real_pinned_bulletin_matches_the_specification():
    data = Path(REAL).read_bytes()
    m = src.BULLETIN_2025
    assert len(data) == m.size_bytes == 1_193_636 and hashlib.sha256(data).hexdigest() == m.sha256
    result = src.parse_bulletin(data)  # verifies identity, extracts pages 22-24 via the shared adapter, parses
    counts = {t: sum(1 for o in result.observations if o.source_table == t) for t in ("Table 13", "Table 14", "Table 15", "Table 16")}
    assert counts == {"Table 13": 39, "Table 14": 78, "Table 15": 91, "Table 16": 182} and len(result.observations) == 390
    assert {o.observation_year for o in result.observations} == set(range(2013, 2026))
    assert len({(o.source_table, o.station_name, o.observation_year) for o in result.observations}) == 390
    assert [o.station_name for o in result.observations if o.source_table == "Table 16"][:14] == list(EXPECTED_T16)
    dash = next(o for o in result.observations if (o.source_table, o.station_name, o.observation_year) == ("Table 14", "Lusiwasi Upper", 2013))
    zero = next(o for o in result.observations if (o.source_table, o.station_name, o.observation_year) == ("Table 16", "Bangweulu Power Limited", 2013))
    assert (dash.generation_gwh, dash.value_state) == (None, NOT_REPORTED) and (zero.generation_gwh, zero.value_state) == (D("0"), REPORTED)
    assert sum(1 for o in result.observations if o.value_state == NOT_REPORTED) == 61
    assert [(f.source_table, f.observation_year, f.residual_gwh) for f in result.footing if f.residual_gwh] == [
        ("Table 16", 2024, D("0.05")), ("Table 16", 2025, D("0.01"))]
    f25 = next(f for f in result.footing if (f.source_table, f.observation_year) == ("Table 16", 2025))
    assert (f25.published_total_gwh, f25.reported_sum_gwh) == (D("5875.67"), D("5875.66"))
    t12 = result.table12
    assert (t12.table16_total_gwh, t12.table12_ipp_gwh, t12.table16_minus_table12_ipp_gwh) == (D("5875.67"), D("5873.17"), D("2.50"))
    assert (t12.table12_printed_total_gwh, t12.table12_footing_residual_gwh) == (D("14560.23"), D("2.50"))
    assert obs_value(result, "Table 13", "Kafue Gorge", 2013) == D("7463.00")
    assert obs_value(result, "Table 16", "Solar century", 2025) == D("30.00")


def obs_value(result, table, station, year):
    return next(o.generation_gwh for o in result.observations if (o.source_table, o.station_name, o.observation_year) == (table, station, year))
