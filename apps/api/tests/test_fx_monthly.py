"""Official BoZ monthly USD/ZMW averages (Fortnightly Statistics Table 02, phase 1). All offline; PostgreSQL tests are opt-in.

Fixtures in tests/fixtures/boz_table02_*.txt are SANITIZED representations of the extracted-text structure of the real
publications (headings, line breaks, month/year structure and column order); every number in them is synthetic, except that
two rows are shaped like the published 2019-03 (12.01 / 12.04 / 12.04) and 2022-11 (16.16 / 16.57 / 16.55) midpoint cases.
Pages are separated by form feeds.
"""

import asyncio
import importlib
import inspect
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.jobs import backfill_fx_monthly as job
from app.main import app
from app.schemas.fx_monthly import FxMonthlyHistory, FxMonthlyPoint
from app.services import boz_monthly_source as source
from app.services import boz_monthly_table as table
from app.services.boz_monthly_table import MonthlyRate, Table02Error, Table02NotFound, midpoint_deviation, parse_table02
from app.services.fx_monthly_combine import MonthlyConflictError, MonthlyObservation, combine, find_gaps, month_range
from app.services.fx_monthly_store import fetch_fx_monthly, save_monthly_averages
from tests.pdfgen import make_pdf
from tests.storage_contract import assert_no_derived_storage

router_module = importlib.import_module("app.api.router")
client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
D = Decimal


def pages(name: str) -> list[str]:
    return (FIXTURES / name).read_text().split("\f")


def months_of(parsed) -> list[str]:
    return [f"{r.month:%Y-%m}" for r in parsed.rows]


# ---------------- parser: supported layout eras ----------------
@pytest.mark.parametrize("name,first,last,count,page", [
    ("boz_table02_early.txt", "2014-01", "2015-02", 14, 2),
    ("boz_table02_middle.txt", "2018-11", "2019-04", 6, 3),
    ("boz_table02_middle_2022.txt", "2022-09", "2023-01", 5, 2),
    ("boz_table02_recent.txt", "2025-11", "2026-03", 5, 2),
])
def test_each_publication_era_layout_parses_the_exact_monthly_block(name, first, last, count, page):
    parsed = parse_table02(pages(name))
    assert parsed.page == page and len(parsed.rows) == count
    assert months_of(parsed)[0] == first and months_of(parsed)[-1] == last
    assert all(r.month.day == 1 for r in parsed.rows)
    assert not any(r.month.year == 2015 and r.month.month == 3 for r in parsed.rows if name.endswith("early.txt"))  # weekly/daily rows ignored


def test_values_are_exact_decimals_with_the_published_digits_and_bureau_columns_are_discarded():
    first = parse_table02(pages("boz_table02_early.txt")).rows[0]
    assert (first.buy, first.mid, first.sell) == (D("5.50"), D("5.53"), D("5.55"))  # BoZ columns only
    assert str(first.buy) == "5.50" and str(first.mid) == "5.53"  # trailing zero kept, nothing extended
    assert all(isinstance(v, Decimal) for v in (first.buy, first.mid, first.sell))
    assert not hasattr(first, "bureau_buy") and set(vars(first)) == {"month", "buy", "mid", "sell"}


def test_months_are_consecutive_across_year_labels():
    rows = parse_table02(pages("boz_table02_recent.txt")).rows
    assert [f"{r.month:%Y-%m}" for r in rows] == ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]


# ---------------- parser: midpoint diagnostics are not validity rules ----------------
def test_2019_03_shaped_row_parses_and_keeps_the_published_mid():
    rows = {f"{r.month:%Y-%m}": r for r in parse_table02(pages("boz_table02_middle.txt")).rows}
    row = rows["2019-03"]
    assert (row.buy, row.sell, row.mid) == (D("12.01"), D("12.04"), D("12.04")) and str(row.mid) == "12.04"
    assert midpoint_deviation(row) == D("0.015")  # diagnostic only


def test_2022_11_shaped_row_parses_and_keeps_the_published_mid():
    rows = {f"{r.month:%Y-%m}": r for r in parse_table02(pages("boz_table02_middle_2022.txt")).rows}
    row = rows["2022-11"]
    assert (row.buy, row.sell, row.mid) == (D("16.16"), D("16.57"), D("16.55")) and str(row.mid) == "16.55"
    assert midpoint_deviation(row) == D("0.185")


def test_phase_1_has_no_ordering_exceptions_and_the_importer_never_uses_the_midpoint_rule():
    assert table.ORDERING_EXCEPTIONS == frozenset()
    code = inspect.getsource(table.parse_page) + inspect.getsource(table._check_ordering)
    assert "midpoint_deviation" not in code and "/ 2" not in code  # no derived mid, no midpoint validity rule


# ---------------- parser: negative fixtures fail closed ----------------
def recent() -> list[str]:
    return pages("boz_table02_recent.txt")


def mutate(old: str, new: str, *, count: int = 1) -> list[str]:
    joined = "\f".join(recent())
    assert old in joined, old
    return joined.replace(old, new, count).split("\f")


def test_missing_table_02_is_reported_as_not_found():
    with pytest.raises(Table02NotFound):
        parse_table02(["Fortnightly Statistics\n", "Table 03 OTHER\nKWACHA/US DOLLAR EXCHANGE RATES\n"])
    with pytest.raises(Table02NotFound):
        parse_table02([])


def test_heading_without_table_02_or_table_02_without_heading_is_not_a_table():
    with pytest.raises(Table02NotFound):
        parse_table02([x.replace("Table 02", "Table 04") for x in recent()])
    with pytest.raises(Table02NotFound):
        parse_table02([x.replace("KWACHA/US DOLLAR EXCHANGE RATES", "SOME OTHER HEADING") for x in recent()])


def test_duplicate_table_02_blocks_are_rejected():
    with pytest.raises(Table02Error, match="More than one"):
        parse_table02(recent() + [recent()[1]])
    with pytest.raises(Table02Error, match="More than one"):
        parse_table02(["\n".join(recent()[1].splitlines() * 2)])


@pytest.mark.parametrize("heading", [
    "KWACHA/US DOLLAR EXCHANGE RATES - RETAIL ONLY Table 02",
    "RETAIL KWACHA/US DOLLAR EXCHANGE RATES Table 02",
    "KWACHA/US DOLLAR EXCHANGE RATES Table 03\nTable 02",
    "Table 02\nKWACHA/US DOLLAR EXCHANGE RATES Table 03",
    "Table 03\nKWACHA/US DOLLAR EXCHANGE RATES Table 02 EXTRA",
    "KWACHA/US DOLLAR EXCHANGE RATES\nTable 02\nKWACHA/US DOLLAR EXCHANGE RATES",
    "Table 02\nKWACHA/US DOLLAR EXCHANGE RATES\nTable 02",
    "Table 03\nKWACHA/US DOLLAR EXCHANGE RATES\nTable 02",
    "Table 02\nKWACHA/US DOLLAR EXCHANGE RATES\nTable 03",
])
def test_malformed_and_ambiguous_table_headings_fail_closed(heading):
    with pytest.raises(Table02Error):
        parse_table02(mutate("KWACHA/US DOLLAR EXCHANGE RATES Table 02", heading))


@pytest.mark.parametrize("heading", [
    "KWACHA/US DOLLAR EXCHANGE RATES Table 02",
    "Table 02\nKWACHA/US DOLLAR EXCHANGE RATES",
    "KWACHA/US DOLLAR EXCHANGE RATES\nTable 02",
])
def test_exact_supported_heading_forms_remain_supported(heading):
    assert len(parse_table02(mutate("KWACHA/US DOLLAR EXCHANGE RATES Table 02", heading)).rows) == 5


def test_missing_monthly_averages_marker_is_rejected():
    with pytest.raises(Table02Error, match="Monthly Averages"):
        parse_table02(mutate("Monthly Averages\n", "Weekly Averages\n"))


@pytest.mark.parametrize("old,new", [
    ("November 22.00", "Novembre 22.00"),        # malformed month name
    ("22.00 22.05", "22.0 22.05"),               # one decimal
    ("22.00 22.05", "22.000 22.05"),             # three decimals
    ("22.00 22.05", "22,00 22.05"),              # comma decimal
    ("22.00 22.05", "x2.00 22.05"),              # malformed decimal
    ("22.00 22.05", "-22.00 22.05"),             # sign
    ("22.00 22.05", "1e1.00 22.05"),             # exponent
    ("22.00 22.05", "NaN 22.05"),
])
def test_malformed_months_and_decimals_are_rejected(old, new):
    with pytest.raises(Table02Error):
        parse_table02(mutate(old, new))


def test_nonpositive_values_are_rejected():
    for old, new in (("22.00 22.05", "0.00 22.05"),):
        with pytest.raises(Table02Error):
            parse_table02(mutate(old, new))


def test_duplicated_out_of_order_or_gapped_months_are_rejected():
    with pytest.raises(Table02Error, match="consecutive"):
        parse_table02(mutate("December 22.07", "November 22.07"))   # duplicate month
    with pytest.raises(Table02Error, match="consecutive"):
        parse_table02(mutate("January 22.14", "February 22.14"))    # gap (Dec -> Feb)
    with pytest.raises(Table02Error, match="consecutive"):
        parse_table02(mutate("2026\nJanuary", "January"))           # year label missing: the year is never inferred


def test_ambiguous_or_missing_year_context_is_rejected():
    with pytest.raises(Table02Error, match="Ambiguous year"):
        parse_table02(mutate("2026\nJanuary", "2026\n2026\nJanuary"))
    with pytest.raises(Table02Error, match="without a year"):
        parse_table02(mutate("2025\nNovember", "November"))
    with pytest.raises(Table02Error, match="Year label without"):
        parse_table02(mutate("Weekly Average\n", "2027\nWeekly Average\n"))


def test_ordering_violations_are_rejected_unless_named():
    with pytest.raises(Table02Error, match="Ordering"):
        parse_table02(mutate("22.07 22.12 22.10", "22.12 22.07 22.10"))  # buy > sell
    with pytest.raises(Table02Error, match="Ordering"):
        parse_table02(mutate("22.07 22.12 22.10", "22.07 22.12 22.20"))  # mid > sell
    with pytest.raises(Table02Error, match="Ordering"):
        parse_table02(mutate("22.07 22.12 22.10", "22.07 22.12 22.00"))  # mid < buy


def test_a_named_ordering_exception_is_the_only_way_to_accept_a_violation(monkeypatch):
    broken = mutate("22.07 22.12 22.10", "22.07 22.12 22.20")
    monkeypatch.setattr(table, "ORDERING_EXCEPTIONS", frozenset({date(2025, 12, 1)}))
    rows = {f"{r.month:%Y-%m}": r for r in parse_table02(broken).rows}
    assert rows["2025-12"].mid == D("22.20")  # accepted as published, not repaired
    assert rows["2025-12"].sell == D("22.12")


def test_shifted_or_reordered_columns_are_rejected():
    with pytest.raises(Table02Error, match="column header"):
        parse_table02(mutate("Bank of Zambia Rates Bureau Rates", "Bureau Rates Bank of Zambia Rates"))
    with pytest.raises(Table02Error, match="column labels"):
        parse_table02(mutate("Buying Selling Mid-rate Buying Selling Mid-rate", "Selling Buying Mid-rate Buying Selling Mid-rate"))


def test_row_shape_changes_are_rejected():
    with pytest.raises(Table02Error, match="Unexpected line"):
        parse_table02(mutate("December 22.07 22.12 22.10 22.04 22.14 22.10", "December 22.07 22.12 22.10"))  # Bureau columns missing
    with pytest.raises(Table02Error, match="Unexpected line"):
        parse_table02(mutate("22.04 22.14 22.10\n", "22.04 22.14 22.10 1.00\n"))  # extra numeric column
    with pytest.raises(Table02Error, match="Unexpected line"):
        parse_table02(mutate("2025\nNovember", "2025\nNote: revised\nNovember"))


def test_truncated_or_empty_monthly_blocks_are_rejected():
    joined = "\f".join(recent())
    cut = joined[: joined.index("Weekly Average")]
    with pytest.raises(Table02Error, match="truncated"):
        parse_table02(cut.split("\f"))
    start, end = joined.index("Monthly Averages\n") + len("Monthly Averages\n"), joined.index("Weekly Average")
    with pytest.raises(Table02Error, match="Empty"):
        parse_table02((joined[:start] + joined[end:]).split("\f"))


def test_a_weekly_only_split_marker_and_unrelated_numbers_do_not_make_a_table():
    with pytest.raises(Table02NotFound):
        parse_table02(["November 22.00 22.05 22.03 21.97 22.07 22.03\nDecember 22.07 22.12 22.10 22.04 22.14 22.10"])


# ---------------- combining publications ----------------
def pub(title: str, first=(2024, 1), last=(2024, 3)) -> source.Publication:
    return source.Publication(title, f"/sites/default/files/x/{title}.pdf", date(*first, 1), date(*last, 1))


def parsed(rows: dict[tuple[int, int], tuple[str, str, str]], page: int = 3) -> table.ParsedTable:
    return table.ParsedTable(page=page, rows=tuple(
        MonthlyRate(date(y, m, 1), D(b), D(mid), D(s)) for (y, m), (b, mid, s) in rows.items()))


def test_identical_repeated_months_are_accepted_and_counted_and_the_earliest_publication_is_canonical():
    a, b = pub("a"), pub("b")
    one = parsed({(2024, 1): ("1.00", "1.02", "1.05"), (2024, 2): ("2.00", "2.02", "2.05")}, page=3)
    two = parsed({(2024, 2): ("2.00", "2.02", "2.05"), (2024, 3): ("3.00", "3.02", "3.05")}, page=4)
    out = combine([(a, one), (b, two)])
    assert [f"{o.month:%Y-%m}" for o in out.observations] == ["2024-01", "2024-02", "2024-03"] and out.overlaps_checked == 1
    by_month = {o.month.month: o for o in out.observations}
    assert (by_month[2].publication_title, by_month[2].source_page) == ("a", 3)  # earliest publication wins provenance
    assert (by_month[3].publication_title, by_month[3].source_page) == ("b", 4)


def test_numerically_identical_precision_is_not_a_conflict():
    a, b = pub("a"), pub("b")
    out = combine([(a, parsed({(2024, 1): ("20.1", "20.2", "20.3")})), (b, parsed({(2024, 1): ("20.10", "20.20", "20.30")}))])
    assert out.overlaps_checked == 1 and len(out.observations) == 1
    assert str(out.observations[0].buy) == "20.1"  # the canonical (first) published text is kept


@pytest.mark.parametrize("changed", [("1.01", "1.02", "1.05"), ("1.00", "1.03", "1.05"), ("1.00", "1.02", "1.06")])
def test_any_differing_triple_fails_the_whole_combination_without_choosing(changed):
    a, b = pub("a"), pub("b")
    with pytest.raises(MonthlyConflictError) as info:
        combine([(a, parsed({(2024, 1): ("1.00", "1.02", "1.05"), (2024, 2): ("2.00", "2.02", "2.05")})),
                 (b, parsed({(2024, 1): changed}))])
    assert info.value.count == 1 and "2024-01" in str(info.value) and "1.06" not in str(info.value) and "1.03" not in str(info.value)


def test_gaps_are_reported_never_filled():
    obs = [MonthlyObservation(date(2024, m, 1), D("1"), D("1"), D("1"), "t", "u", 3) for m in (1, 2, 4)]
    assert find_gaps(obs, date(2024, 1, 1), date(2024, 5, 1)) == [date(2024, 3, 1), date(2024, 5, 1)]
    assert find_gaps(obs, date(2024, 1, 1), date(2024, 2, 1)) == []


# ---------------- vetted manifest and source retrieval ----------------
def test_manifest_is_explicit_chronological_https_boz_only_and_excludes_the_no_table_issue():
    urls = [p.url for p in source.PUBLICATIONS]
    assert len(urls) == 13 and len(set(urls)) == 13
    assert all(u.startswith("https://www.boz.zm/sites/default/files/") and u.endswith(".pdf") and "?" not in u for u in urls)
    assert not any("IssueNo_22" in u for u in urls)  # one-page issue without Table 02
    assert all(a.last_month <= b.last_month for a, b in zip(source.PUBLICATIONS, source.PUBLICATIONS[1:]))
    assert source.FIRST_COVERED_MONTH == date(2014, 1, 1) and source.LAST_COVERED_MONTH == date(2026, 9, 1)
    assert all(source.is_vetted_url(u) for u in urls)
    for bad in ("http://www.boz.zm" + source.PUBLICATIONS[0].path, "https://evil.example" + source.PUBLICATIONS[0].path,
                source.PUBLICATIONS[0].url + "?x=1", "https://www.boz.zm/sites/default/files/other.pdf", None, 5):
        assert not source.is_vetted_url(bad)


def test_manifest_months_cover_2014_01_to_2026_09_without_gaps():
    covered = set()
    for p in source.PUBLICATIONS:
        covered |= set(month_range(p.first_month, p.last_month))
    assert covered == set(month_range(date(2014, 1, 1), date(2026, 9, 1))) and len(covered) == 153


def test_select_publications_returns_only_intersecting_vetted_issues_in_order():
    chosen = source.select_publications(date(2026, 1, 1), date(2026, 9, 1))
    assert [p.filename for p in chosen][-1].endswith("IssueNo_21.pdf") and all(p.last_month >= date(2026, 1, 1) for p in chosen)
    assert len(source.select_publications(date(2014, 1, 1), date(2026, 9, 1))) == 13
    assert source.select_publications(date(2014, 1, 1), date(2014, 1, 1))[0].filename == "StatisticsFortnightly2016Vol24No01.pdf"


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


PDF_BODY = make_pdf(["Table 02"])


def test_fetch_pdf_is_one_bounded_get_with_no_redirects_and_content_checks():
    seen = []

    def ok(request):
        seen.append((request.method, str(request.url), request.headers["user-agent"]))
        return httpx.Response(200, content=PDF_BODY, headers={"content-type": "application/pdf"})

    async def run():
        async with make_client(ok) as c:
            return await source.fetch_pdf(source.PUBLICATIONS[0], c)

    assert asyncio.run(run()) == PDF_BODY
    assert seen == [("GET", source.PUBLICATIONS[0].url, source.USER_AGENT)] and "ZM-Terminal" in seen[0][2]


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://evil.example/x.pdf"}),
    httpx.Response(404),
    httpx.Response(200, content=PDF_BODY, headers={"content-type": "text/html"}),
    httpx.Response(200, content=b"<html>not a pdf</html>", headers={"content-type": "application/pdf"}),
    httpx.Response(200, content=PDF_BODY, headers={"content-type": "application/pdf", "content-length": str(source.MAX_PDF_BYTES + 1)}),
    httpx.Response(200, content=b"%PDF-" + b"0" * (source.MAX_PDF_BYTES + 10), headers={"content-type": "application/pdf"}),
])
def test_fetch_pdf_fails_closed(response):
    async def run():
        async with make_client(lambda request: response) as c:
            await source.fetch_pdf(source.PUBLICATIONS[0], c)

    with pytest.raises(source.PublicationError):
        asyncio.run(run())


def test_fetch_refuses_an_unvetted_url_before_any_request():
    called = []
    rogue = source.Publication("x", "/sites/default/files/rogue.pdf", date(2024, 1, 1), date(2024, 2, 1))

    async def run():
        async with make_client(lambda r: called.append(r) or httpx.Response(200)) as c:
            await source.fetch_pdf(rogue, c)

    with pytest.raises(source.PublicationError):
        asyncio.run(run())
    assert not called


def test_extraction_uses_the_text_layer_only_and_fails_closed_without_one():
    lines = ["Table 02", "KWACHA/US DOLLAR EXCHANGE RATES", "Bank of Zambia Rates Bureau Rates",
             "Buying Selling Mid-rate Buying Selling Mid-rate", "Monthly Averages", "2026",
             "January 20.10 20.15 20.12 20.05 20.20 20.12", "Weekly Average"]
    pdf = make_pdf(lines)
    extracted = source.extract_pages(pdf)
    assert len(extracted) == 1 and "KWACHA/US DOLLAR EXCHANGE RATES" in extracted[0]
    pub_ = source.Publication("t", "/sites/default/files/t.pdf", date(2026, 1, 1), date(2026, 1, 1))
    assert source.load_publication(pub_, pdf).rows[0].mid == D("20.12")
    with pytest.raises(source.PublicationError, match="text layer"):
        source.extract_pages(make_pdf(lines, text=False))  # image-only/no text: no OCR fallback
    with pytest.raises(source.PublicationError):
        source.extract_pages(b"%PDF-1.4 garbage")
    with pytest.raises(source.PublicationError, match="Table 02"):
        source.load_publication(pub_, make_pdf(["Table 04", "nothing here"]))


def test_load_publication_fails_when_the_block_differs_from_the_pinned_months():
    lines = ["Table 02", "KWACHA/US DOLLAR EXCHANGE RATES", "Bank of Zambia Rates Bureau Rates",
             "Buying Selling Mid-rate Buying Selling Mid-rate", "Monthly Averages", "2026",
             "January 20.10 20.15 20.12 20.05 20.20 20.12", "Weekly Average"]
    wrong = source.Publication("t", "/sites/default/files/t.pdf", date(2026, 1, 1), date(2026, 2, 1))
    with pytest.raises(source.PublicationError, match="expected 2026-01 to 2026-02"):
        source.load_publication(wrong, make_pdf(lines))


# ---------------- importer (offline) ----------------
def fake_pipeline(monkeypatch, tables: dict[str, table.ParsedTable], *, fetched=None):
    fetched = fetched if fetched is not None else []

    async def fake_fetch(publication, client):
        fetched.append(publication.filename)
        return publication.filename.encode()

    async def fake_load(publication, data):
        return tables[publication.filename]

    monkeypatch.setattr(source, "fetch_pdf", fake_fetch)
    monkeypatch.setattr(source, "load_publication_async", fake_load)
    return fetched


def synthetic_tables(*, conflict: bool = False, gap: bool = False) -> dict[str, table.ParsedTable]:
    out = {}
    for p in source.PUBLICATIONS:
        rows = {}
        cursor = p.first_month
        while cursor <= p.last_month:
            if not (gap and cursor == date(2020, 5, 1)):
                value = D(str(cursor.year - 2000 + cursor.month / 100)).quantize(D("0.01"))
                rows[(cursor.year, cursor.month)] = (str(value), str(value + D("0.02")), str(value + D("0.05")))
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        if conflict and p.filename.endswith("IssueNo_21.pdf"):
            rows[(2024, 1)] = ("9.99", "9.99", "9.99")
        out[p.filename] = parsed(rows)
    return out


def run_job(monkeypatch, start, end, *, dry_run=True, tables=None, engine=None, saved=None, sleeps=None):
    fetched = fake_pipeline(monkeypatch, tables or synthetic_tables())
    if saved is not None:
        monkeypatch.setattr(job, "save_monthly_averages", lambda obs, imported_at, engine=None: saved.append(list(obs)) or (len(obs), 0))

    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    async def go():
        async with httpx.AsyncClient() as c:
            return await job.run_import(start, end, dry_run=dry_run, client=c, now=NOW, sleep=sleep, engine=engine)

    return asyncio.run(go()), fetched


def test_full_dry_run_reconstructs_153_months_checks_overlaps_and_never_touches_the_database(monkeypatch):
    saved, sleeps = [], []

    def no_db(*a, **k):
        raise AssertionError("dry-run must not touch PostgreSQL")

    monkeypatch.setattr(job, "save_monthly_averages", no_db)
    result, fetched = run_job(monkeypatch, date(2014, 1, 1), date(2026, 9, 1), dry_run=True, sleeps=sleeps)
    assert result["months_selected"] == 153 and result["first_month"] == "2014-01" and result["last_month"] == "2026-09"
    assert result["publications_fetched"] == 13 == result["publications_parsed"] == len(fetched) and result["gaps"] == 0
    assert result["overlaps_checked"] > 0 and result["inserted"] == 0 and result["already_present"] == 0
    assert sleeps == [source.REQUEST_PAUSE_SECONDS] * 12  # sequential, paused between requests only
    assert fetched == [p.filename for p in source.PUBLICATIONS]


def test_a_bounded_range_fetches_only_the_needed_publications(monkeypatch):
    saved = []
    result, fetched = run_job(monkeypatch, date(2026, 1, 1), date(2026, 9, 1), dry_run=False, saved=saved)
    assert fetched == [p.filename for p in source.select_publications(date(2026, 1, 1), date(2026, 9, 1))] and len(fetched) < 13
    assert result["months_selected"] == 9 and result["inserted"] == 9 and len(saved) == 1  # one atomic write call
    assert [f"{o.month:%Y-%m}" for o in saved[0]][0] == "2026-01"
    assert saved[0][0].publication_title == next(p.title for p in source.PUBLICATIONS if p.first_month <= date(2026, 1, 1) <= p.last_month and p.filename in fetched)


def test_overlap_conflict_fails_before_any_write(monkeypatch):
    saved = []
    with pytest.raises(MonthlyConflictError):
        run_job(monkeypatch, date(2014, 1, 1), date(2026, 9, 1), dry_run=False, tables=synthetic_tables(conflict=True), saved=saved)
    assert saved == []


def test_a_calendar_gap_fails_before_any_write_and_nothing_is_filled(monkeypatch):
    saved = []
    with pytest.raises(job.ImportRequestError, match="missing, first 2020-05"):
        run_job(monkeypatch, date(2018, 1, 1), date(2021, 12, 1), dry_run=False, tables=synthetic_tables(gap=True), saved=saved)
    assert saved == []


def test_range_validation():
    today = date(2026, 11, 20)
    for start, end in ((date(2026, 5, 1), date(2026, 4, 1)), (date(2013, 12, 1), date(2014, 3, 1)),
                       (date(2014, 1, 1), date(2026, 10, 1)), (date(2026, 1, 1), date(2027, 1, 1))):
        with pytest.raises(job.ImportRequestError):
            job.validate_range(start, end, today=today)
    job.validate_range(date(2014, 1, 1), date(2026, 9, 1), today=today)
    with pytest.raises(job.ImportRequestError, match="future"):
        job.validate_range(date(2014, 1, 1), date(2026, 12, 1), today=date(2026, 11, 20))


def test_cli_argument_parsing_and_safe_output(monkeypatch, capsys):
    for bad in ("2026-13", "2026-1", "26-01", "2026-01-01", ""):
        with pytest.raises(SystemExit):
            job.main(["--from", bad])

    async def fake_run(start, end, **kwargs):
        return {"months_selected": 1, "first_month": f"{start:%Y-%m}", "last_month": f"{end:%Y-%m}", "inserted": 0}

    monkeypatch.setattr(job, "run_import", fake_run)
    assert job.main(["--from", "2026-01", "--to", "2026-02", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("dry-run ") and "first_month=2026-01" in out and "last_month=2026-02" in out

    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")

    monkeypatch.setattr(job, "run_import", boom)
    assert job.main([]) == 1
    err = capsys.readouterr().err
    assert "SECRET" not in err and "RuntimeError" in err

    async def conflict(*a, **k):
        raise MonthlyConflictError(1, ["2024-01 (a vs b)"])

    monkeypatch.setattr(job, "run_import", conflict)
    assert job.main([]) == 1 and "conflict" in capsys.readouterr().err


# ---------------- stored-only API ----------------
def stored(months: list[str]) -> FxMonthlyHistory:
    points = [FxMonthlyPoint(month=m, buy=D("20.10"), mid=D("20.15"), sell=D("20.20"), source_series=table.SOURCE_SERIES,
                             source_publication="Pub", source_url="https://www.boz.zm/x.pdf", source_page=3, published_precision=2)
              for m in months]
    return FxMonthlyHistory(range="ALL", count=len(points), first_month=months[0] if months else None,
                            last_month=months[-1] if months else None, observations=points)


def test_api_is_stored_only_defaults_to_all_and_validates_the_range(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_fx_monthly", lambda r: seen.append(r) or stored(["2026-08", "2026-09"]))

    async def no_network(*a, **k):
        raise AssertionError("the monthly FX route must read PostgreSQL only")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    body = client.get("/api/v1/fx/USDZMW/monthly").json()
    assert seen == ["ALL"] and body["symbol"] == "USDZMW" and body["count"] == 2
    assert (body["first_month"], body["last_month"]) == ("2026-08", "2026-09")
    point = body["observations"][0]
    assert point["month"] == "2026-08" and point["buy"] == "20.10" and point["mid"] == "20.15" and point["sell"] == "20.20"
    assert {"source", "source_series", "source_publication", "source_url", "source_page", "published_precision"} <= set(point)
    assert not {"observed_at", "stale", "retrieved_at"} & set(point) and not {"stale", "observed_at"} & set(body)
    for r in ("1Y", "3Y", "5Y", "ALL"):
        assert client.get("/api/v1/fx/USDZMW/monthly", params={"range": r}).status_code == 200 and seen[-1] == r
    for bad in ("1M", "10Y", "all", "", "1y"):
        assert client.get("/api/v1/fx/USDZMW/monthly", params={"range": bad}).status_code == 422
    src = inspect.getsource(importlib.import_module("app.services.fx_monthly_store"))
    assert "httpx" not in src and "pdfplumber" not in src and "boz_monthly_source" not in src


def test_database_failure_is_a_safe_503(monkeypatch):
    def boom(r):
        raise RuntimeError("postgresql://u:SECRET@h/db 20.15")

    monkeypatch.setattr(router_module, "fetch_fx_monthly", boom)
    r = client.get("/api/v1/fx/USDZMW/monthly")
    assert r.status_code == 503 and "SECRET" not in r.text and "20.15" not in r.text


def test_existing_intraday_fx_routes_are_untouched():
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/v1/fx/USDZMW", "/api/v1/fx/USDZMW/history", "/api/v1/fx/USDZMW/monthly"} <= set(paths)
    history = paths["/api/v1/fx/USDZMW/history"]["get"]["parameters"]
    assert [p["name"] for p in history] == ["range"] and set(history[0]["schema"]["enum"]) == {"1D", "1W", "1M", "3M", "1Y", "ALL"}


# ---------------- PostgreSQL (opt-in) ----------------
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    init = Path(__file__).parents[3] / "db/init"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS fx_monthly_averages"))
        c.execute(text((init / "012_fx_monthly_averages.sql").read_text()))
        c.execute(text((init / "013_fx_monthly_published_values.sql").read_text()))
    yield eng
    eng.dispose()


def obs(month: tuple[int, int], buy="20.10", mid="20.15", sell="20.20", title="Pub A", page=3) -> MonthlyObservation:
    return MonthlyObservation(date(*month, 1), D(buy), D(mid), D(sell), title, "https://www.boz.zm/sites/default/files/a.pdf", page)


def test_pg_first_insert_exact_decimals_provenance_and_idempotency(engine):
    batch = [obs((2026, 7), "18.14", "18.16", "18.19"), obs((2026, 8), "18.9", "18.95", "19.00")]
    assert save_monthly_averages(batch, imported_at=NOW, engine=engine) == (2, 0)
    assert save_monthly_averages(batch, imported_at=NOW, engine=engine) == (0, 2)  # idempotent no-op
    assert save_monthly_averages([obs((2026, 8), "18.90", "18.950", "19.0")], imported_at=NOW, engine=engine) == (0, 1)  # numerically equal
    with engine.connect() as c:
        row = c.execute(text("SELECT * FROM fx_monthly_averages WHERE observation_month = '2026-08-01'")).mappings().one()
        count = c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar()
        cols = [r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'fx_monthly_averages'"))]
    assert count == 2 and (row["buy"], row["mid"], row["sell"]) == (D("18.9"), D("18.95"), D("19.00")) and str(row["mid"]) == "18.95"
    assert (row["base_currency"], row["quote_currency"], row["source"], row["source_series"]) == ("USD", "ZMW", "boz", table.SOURCE_SERIES)
    assert (row["source_publication"], row["source_page"], row["source_table"]) == ("Pub A", 3, "Table 02")
    assert (row["published_precision"], row["currency_regime"], row["imported_at"]) == (2, "ZMW", NOW)
    assert row["source_url"].startswith("https://www.boz.zm/") and not [c for c in cols if "raw" in c or "payload" in c or "pdf" in c]


def test_pg_mid_is_stored_as_published_even_when_far_from_the_midpoint(engine):
    save_monthly_averages([obs((2022, 11), "16.16", "16.55", "16.57")], imported_at=NOW, engine=engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT mid FROM fx_monthly_averages")).scalar() == D("16.55")


def test_pg_conflict_rolls_back_the_whole_import_and_never_overwrites(engine):
    save_monthly_averages([obs((2026, 1)), obs((2026, 2))], imported_at=NOW, engine=engine)
    with pytest.raises(MonthlyConflictError) as info:
        save_monthly_averages([obs((2026, 2), mid="99.99"), obs((2026, 3)), obs((2025, 12))], imported_at=NOW, engine=engine)
    assert "2026-02" in str(info.value) and "99.99" not in str(info.value) and info.value.count == 1
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar() == 2  # 2025-12 and 2026-03 rolled back
        assert c.execute(text("SELECT mid FROM fx_monthly_averages WHERE observation_month = '2026-02-01'")).scalar() == D("20.15")


@pytest.mark.parametrize("field,value", [
    ("source_publication", "Different publication"),
    ("source_url", "https://www.boz.zm/sites/default/files/different.pdf"),
    ("source_page", 9),
    ("source_table", "Table 03"),
    ("published_precision", 1),
])
def test_pg_stable_provenance_conflict_rolls_back_every_insert(engine, field, value):
    save_monthly_averages([obs((2026, 1))], imported_at=NOW, engine=engine)
    with engine.begin() as c:
        c.execute(text(f"UPDATE fx_monthly_averages SET {field} = :value"), {"value": value})
    with pytest.raises(MonthlyConflictError) as error:
        save_monthly_averages([obs((2025, 12)), obs((2026, 1)), obs((2026, 2))], imported_at=NOW, engine=engine)
    assert error.value.count == 1 and "2026-01" in str(error.value)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar() == 1
        assert c.execute(text(f"SELECT {field} FROM fx_monthly_averages")).scalar() == value


def test_pg_import_time_alone_is_not_a_conflict(engine):
    save_monthly_averages([obs((2026, 1))], imported_at=NOW, engine=engine)
    assert save_monthly_averages([obs((2026, 1))], imported_at=NOW + timedelta(days=1), engine=engine) == (0, 1)
    with engine.connect() as c:
        assert c.execute(text("SELECT imported_at FROM fx_monthly_averages")).scalar() == NOW


@pytest.mark.parametrize("field", ["buy", "mid", "sell"])
@pytest.mark.parametrize("special", ["NaN", "Infinity", "-Infinity"])
def test_pg_all_rate_columns_reject_nonfinite_numeric(engine, field, special):
    from sqlalchemy.exc import IntegrityError
    save_monthly_averages([obs((2026, 1))], imported_at=NOW, engine=engine)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text(f"UPDATE fx_monthly_averages SET {field} = CAST(:value AS NUMERIC)"), {"value": special})
    assert fetch_fx_monthly("ALL", engine=engine).observations[0].mid == D("20.15")


def test_pg_finite_exact_numeric_rows_serialize_through_the_monthly_api(engine, monkeypatch):
    save_monthly_averages([obs((2026, 1), "20.10", "20.15", "20.20")], imported_at=NOW, engine=engine)
    monkeypatch.setattr(router_module, "fetch_fx_monthly", lambda r: fetch_fx_monthly(r, engine=engine))
    response = client.get("/api/v1/fx/USDZMW/monthly")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1 and body["first_month"] == body["last_month"] == "2026-01"
    point = body["observations"][0]
    assert (point["buy"], point["mid"], point["sell"]) == ("20.10", "20.15", "20.20")
    assert not {"stale", "observed_at", "retrieved_at"} & set(point)


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE kwacha_sensitivity (month DATE, beta NUMERIC)",
    "CREATE MATERIALIZED VIEW kwacha_sensitivity AS SELECT 1 AS beta",
    "CREATE TABLE public.kwacha_sensitivity (month DATE, beta NUMERIC)",
    "CREATE VIEW public.kwacha_sensitivity AS SELECT 1 AS beta",
])
def test_pg_storage_guard_rejects_unregistered_derived_relations(engine, ddl):
    with pytest.raises(AssertionError, match="kwacha_sensitivity"):
        assert_no_derived_storage(engine, Path(__file__).parents[3] / "db/init", extra_sql=[ddl])
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'storage_contract_%'")).scalar() == 0
        assert c.execute(text("SELECT to_regclass('public.kwacha_sensitivity')")).scalar() is None


def test_pg_storage_guard_allows_future_changes_to_approved_source_tables(engine):
    assert_no_derived_storage(engine, Path(__file__).parents[3] / "db/init", extra_sql=[
        "ALTER TABLE fx_rates ADD COLUMN future_source_metadata TEXT",
        "CREATE INDEX future_source_lookup ON fx_rates (observed_at)",
    ])


def test_pg_month_identity_is_unique_and_constraints_hold(engine):
    from sqlalchemy.exc import IntegrityError
    save_monthly_averages([obs((2026, 1))], imported_at=NOW, engine=engine)
    base = dict(base="USD", quote="ZMW", month="2026-01-01", buy=1, mid=1, sell=1, source="boz", series=table.SOURCE_SERIES, pub="p",
                url="u", page=3, tbl="Table 02", prec=2, regime="ZMW", at=NOW)
    insert = text("INSERT INTO fx_monthly_averages (base_currency, quote_currency, observation_month, buy, mid, sell, source, source_series, "
                  "source_publication, source_url, source_page, source_table, published_precision, currency_regime, imported_at, "
                  "published_buy, published_mid, published_sell, published_currency, normalization_divisor) VALUES "
                  "(:base, :quote, :month, :buy, :mid, :sell, :source, :series, :pub, :url, :page, :tbl, :prec, :regime, :at, "
                  ":buy, :mid, :sell, 'ZMW', 1)")
    for change in ({}, {"month": "2026-02-02"}, {"buy": 0}, {"mid": -1}, {"base": "EUR"}, {"quote": "USD"}, {"source": "imf"},
                   {"regime": "ZMK"}, {"page": 0}, {"month": None}):
        with pytest.raises(IntegrityError):
            with engine.begin() as c:
                c.execute(insert, {**base, **change})
    with engine.begin() as c:  # a different source series is a different identity
        c.execute(insert, {**base, "series": "another_series"})


def test_pg_range_windows_are_counts_of_stored_months(engine):
    months = [(2020 + (m // 12), m % 12 + 1) for m in range(0, 70)]
    save_monthly_averages([obs(m) for m in months], imported_at=NOW, engine=engine)
    for name, size in (("1Y", 12), ("3Y", 36), ("5Y", 60), ("ALL", 70)):
        history = fetch_fx_monthly(name, engine=engine)
        assert history.count == size and len(history.observations) == size
        assert history.last_month == "2025-10" and history.first_month == history.observations[0].month
        assert [p.month for p in history.observations] == sorted(p.month for p in history.observations)
    assert fetch_fx_monthly("1Y", engine=engine).first_month == "2024-11"


def test_pg_empty_table_gives_an_empty_history_not_an_error(engine):
    history = fetch_fx_monthly("ALL", engine=engine)
    assert history.count == 0 and history.first_month is None and history.last_month is None and history.observations == []


def test_pg_the_intraday_table_is_not_touched(engine):
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    assert "fx_rates" not in inspect.getsource(importlib.import_module("app.services.fx_monthly_store"))
