"""Phase 2 foundation for official BoZ monthly USD/ZMW: Annual Report Table 13 extraction, parsing, normalization, narrow
ordering exceptions, the 2014 validation bridge and migration 013 (published-vs-canonical storage). All offline.

Fixtures tests/fixtures/boz_table13_*.txt are SANITIZED representations of `pdftotext -layout` output of the vetted pages
(headings, whitespace, year-label forms, column order). All numbers are synthetic except the rows the research pins
(2012-04 and 2013-12 ordering exceptions; 2012-12 / 2013-01 boundary rows). PostgreSQL tests are opt-in (TEST_DATABASE_URL).
"""

import dataclasses
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.services import boz_annual_source as src
from app.services import boz_annual_table as tab
from app.services import pdf_layout_text as layout
from app.services.fx_monthly_combine import MonthlyObservation
from app.services.fx_monthly_normalize import NormalizationError, normalize
from app.services.fx_monthly_store import fetch_fx_monthly, save_monthly_averages

D = Decimal
FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)


def fixture(name: str) -> str:
    return (FIX / name).read_text()


def parse(name: str, page: int, ordering_from=date(2010, 1, 1)):
    return tab.parse_table13(fixture(name), printed_page=page, ordering_from=ordering_from)


def mutate(name: str, old: str, new: str, count: int = 1) -> str:
    text_ = fixture(name)
    assert old in text_, old
    return text_.replace(old, new, count)


def fails(page_text: str, page: int, match: str, ordering_from=date(2010, 1, 1)):
    with pytest.raises(tab.AnnualTableError, match=match):
        tab.parse_table13(page_text, printed_page=page, ordering_from=ordering_from)


# ---------------------------------------------------------------- pdftotext adapter
def test_command_is_a_fixed_argument_list():
    assert layout.pdftotext_command("/x/input.pdf", 145) == [
        "pdftotext", "-layout", "-f", "145", "-l", "145", "-enc", "UTF-8", "/x/input.pdf", "-"]
    for bad in (0, -1, True, "3", 1.5):
        with pytest.raises(layout.LayoutExtractionError):
            layout.pdftotext_command("/x/input.pdf", bad)  # type: ignore[arg-type]


def fake_tool(tmp_path, body: str) -> str:
    """A real executable (python script) standing in for pdftotext; argv[-2] is the input file, argv[-1] is '-'."""
    script = tmp_path / "fake_pdftotext"
    script.write_text(f"#!{sys.executable}\nimport sys, os, time\n{body}\n")
    script.chmod(0o755)
    return str(script)


def temp_dirs_left(tmp_path_factory=None) -> list:
    import glob, tempfile
    return glob.glob(os.path.join(tempfile.gettempdir(), "zmt-pdf-*"))


def test_extractor_uses_popen_without_a_shell_with_file_sinks(monkeypatch):
    seen = {}
    real = subprocess.Popen

    def spy(command, **kwargs):
        seen.update(command=command, kwargs=kwargs, dir=Path(command[-2]).parent, input=Path(command[-2]).read_bytes())
        return real(["/bin/sh", "-c", "printf 'page text\\n'"], **kwargs)

    monkeypatch.setattr(layout.subprocess, "Popen", spy)
    assert layout.extract_page_layout(b"%PDF-1.4 data", 7, timeout=12.5) == "page text\n"
    assert seen["command"][:7] == ["pdftotext", "-layout", "-f", "7", "-l", "7", "-enc"] and seen["command"][-1] == "-"
    kw = seen["kwargs"]
    assert kw["shell"] is False and kw["stdin"] == subprocess.DEVNULL
    assert kw["stdout"] is not subprocess.PIPE and kw["stderr"] is not subprocess.PIPE  # file sinks, never in-memory pipes
    assert seen["input"] == b"%PDF-1.4 data" and not seen["dir"].exists()


def test_normal_extraction_succeeds_with_a_real_child(tmp_path):
    tool = fake_tool(tmp_path, "sys.stdout.write('hello page\\n'); sys.stderr.write('warn')")
    assert layout.extract_page_layout(b"%PDF-", 3, executable=tool) == "hello page\n"


def test_nonzero_exit_fails_cleanly_without_echoing_stderr(tmp_path):
    tool = fake_tool(tmp_path, "sys.stderr.write('secret detail'); sys.exit(3)")
    with pytest.raises(layout.LayoutExtractionError, match="exit status 3") as info:
        layout.extract_page_layout(b"%PDF-", 1, executable=tool)
    assert "secret" not in str(info.value)


def test_empty_output_is_rejected(tmp_path):
    with pytest.raises(layout.LayoutExtractionError, match="no text"):
        layout.extract_page_layout(b"%PDF-", 1, executable=fake_tool(tmp_path, "sys.stdout.write('  \\n')"))


def test_missing_executable_is_a_clear_error(tmp_path):
    with pytest.raises(layout.LayoutExtractionError, match="not found.*poppler"):
        layout.extract_page_layout(b"%PDF-", 1, executable=str(tmp_path / "nope"))


def test_unrunnable_executable_is_reported_by_class_only(tmp_path):
    not_exec = tmp_path / "plain"
    not_exec.write_text("x")
    with pytest.raises(layout.LayoutExtractionError, match="could not be run"):
        layout.extract_page_layout(b"%PDF-", 1, executable=str(not_exec))


FLOOD = "chunk = b'x' * 65536\nwhile True:\n    os.write({fd}, chunk)\n"


def test_oversized_stdout_kills_the_child_before_unbounded_buffering(tmp_path, monkeypatch):
    sizes, pids = [], []
    real_stat = layout._size

    def watching(path):
        n = real_stat(path)
        sizes.append(n)
        return n

    real_popen = subprocess.Popen

    def spy(*a, **k):
        p = real_popen(*a, **k)
        pids.append(p)
        return p

    monkeypatch.setattr(layout, "_size", watching)
    monkeypatch.setattr(layout.subprocess, "Popen", spy)
    with pytest.raises(layout.LayoutExtractionError, match="larger than expected"):
        layout.extract_page_layout(b"%PDF-", 1, executable=fake_tool(tmp_path, FLOOD.format(fd=1)), timeout=20)
    assert pids[0].returncode is not None  # reaped (killed by the monitor or by the RLIMIT_FSIZE hard cap)
    assert max(sizes) <= layout.MAX_OUTPUT_BYTES + 1  # on-disk output never grew past the cap (+1 detection byte)


def test_oversized_stderr_is_bounded_too(tmp_path, monkeypatch):
    pids = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(layout.subprocess, "Popen", lambda *a, **k: pids.append(real_popen(*a, **k)) or pids[-1])
    body = "chunk = b'e' * 4096\nwhile True:\n    os.write(2, chunk)\n    time.sleep(0.001)\n"
    with pytest.raises(layout.LayoutExtractionError, match="larger than expected"):
        layout.extract_page_layout(b"%PDF-", 1, executable=fake_tool(tmp_path, body), timeout=20)
    assert pids[0].returncode is not None


def test_timeout_kills_and_reaps_the_child(tmp_path, monkeypatch):
    pids = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(layout.subprocess, "Popen", lambda *a, **k: pids.append(real_popen(*a, **k)) or pids[-1])
    with pytest.raises(layout.LayoutExtractionError, match="timed out"):
        layout.extract_page_layout(b"%PDF-", 1, executable=fake_tool(tmp_path, "time.sleep(60)"), timeout=0.3)
    assert pids[0].returncode is not None and pids[0].poll() is not None


@pytest.mark.parametrize("body,exc", [
    ("sys.stdout.write('ok\\n')", None), (FLOOD.format(fd=1), "larger"), ("time.sleep(60)", "timed out"),
    ("sys.exit(2)", "exit status"),
])
def test_temporary_files_are_removed_after_success_overflow_timeout_and_failure(tmp_path, body, exc):
    before = set(temp_dirs_left())
    if exc is None:
        layout.extract_page_layout(b"%PDF-", 1, executable=fake_tool(tmp_path, body), timeout=0.5)
    else:
        with pytest.raises(layout.LayoutExtractionError, match=exc):
            layout.extract_page_layout(b"%PDF-", 1, executable=fake_tool(tmp_path, body), timeout=0.5)
    assert set(temp_dirs_left()) == before


def two_page_pdf() -> bytes:
    def page_stream(label: str) -> bytes:
        return f"BT /F1 12 Tf 50 700 Td ({label}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
    ]
    for label in ("PAGE ONE MARKER", "PAGE TWO MARKER"):
        stream = page_stream(label)
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler pdftotext not installed")
def test_real_pdftotext_extracts_only_the_pinned_page():
    out = layout.extract_page_layout(two_page_pdf(), 2)
    assert "PAGE TWO MARKER" in out and "PAGE ONE MARKER" not in out
    assert "PAGE ONE MARKER" in layout.extract_page_layout(two_page_pdf(), 1)


# ---------------------------------------------------------------- parser: vetted layouts
def test_2010_layout_standalone_year_lines_and_header_identity():
    t = parse("boz_table13_2010_style.txt", 140)
    months = [r.month for r in t.rows]
    assert months[0] == date(1995, 12, 1) and months[1] == date(2007, 12, 1) and months[-1] == date(2010, 12, 1)
    assert len(t.rows) == 2 + 36 and months == sorted(set(months))
    assert [r.month for r in t.rows if r.month.year == 2010] == [date(2010, m, 1) for m in range(1, 13)]


def test_2012_layout_inline_years_and_old_kwacha_thousands_separators():
    t = parse("boz_table13_2012_style.txt", 137)
    assert t.rows[0].month == date(1995, 12, 1) and t.rows[-1].month == date(2012, 12, 1)
    dec = t.rows[-1]
    assert (dec.buy, dec.sell, dec.mid) == (D("5198.47"), D("5228.97"), D("5208.46"))  # printed Buying, Selling, Mid


def test_2013_layout_page_number_inside_the_header_and_label_line_variants():
    t = parse("boz_table13_2013_style.txt", 138)
    assert [r.month for r in t.rows[-12:]] == [date(2013, m, 1) for m in range(1, 13)]
    jan = t.rows[-12]
    assert (jan.buy, jan.mid, jan.sell) == (D("5.27"), D("5.28"), D("5.29"))


def test_2014_layout_mixed_case_heading_and_runtogether_column_groups():
    t = parse("boz_table13_2014_style.txt", 131, ordering_from=date(2014, 1, 1))
    assert [r.month for r in t.rows[-12:]] == [date(2014, m, 1) for m in range(1, 13)]


def test_published_columns_map_to_buy_mid_sell_not_the_phase1_order():
    # Printed order is Buying, SELLING, MID. A naive buy/mid/sell reading would put the selling rate in `mid`.
    row = next(r for r in parse("boz_table13_2012_style.txt", 137).rows if r.month == date(2012, 12, 1))
    assert row.buy == D("5198.47") and row.mid == D("5208.46") and row.sell == D("5228.97")
    assert row.buy <= row.mid <= row.sell
    only_bureau_differs = mutate("boz_table13_2012_style.txt", "5,198.47        5,228.97        5,208.46", "5,198.47        5,228.97        5,208.46")
    assert tab.parse_table13(only_bureau_differs, printed_page=137, ordering_from=date(2010, 1, 1)).rows[-1].sell == D("5228.97")


def test_known_exception_months_use_the_exact_source_layout():
    t12 = {r.month: r for r in parse("boz_table13_2012_style.txt", 137).rows}
    april = t12[date(2012, 4, 1)]  # printed: Buying 5,283.60  Selling 5,250.97  Mid 5,240.97
    assert (april.buy, april.sell, april.mid) == (D("5283.60"), D("5250.97"), D("5240.97"))
    assert april.sell < april.buy and april.mid < april.sell  # the published violation, preserved unrepaired
    t13 = {r.month: r for r in parse("boz_table13_2013_style.txt", 138).rows}
    december = t13[date(2013, 12, 1)]  # printed: Buying 5.52  Selling 5.53  Mid 5.54
    assert (december.buy, december.sell, december.mid) == (D("5.52"), D("5.53"), D("5.54")) and december.mid > december.sell


# ---------------------------------------------------------------- parser: year context (fail closed)
def test_month_row_without_year_context_is_rejected():
    page = fixture("boz_table13_2012_style.txt")
    first_inline = next(l for l in page.splitlines() if l.strip().startswith("1995"))
    fails(page.replace(first_inline, first_inline.replace("1995", "    ", 1)), 137, "without a year context")
    after_december = mutate("boz_table13_2012_style.txt", "2011", "    ")  # January 2012 would follow December unlabelled
    fails(after_december, 137, "year|consecutive")


def test_standalone_year_ambiguities_are_rejected():
    fails(mutate("boz_table13_2010_style.txt", "      2009\n", "      2009\n      2009\n"), 140, "Consecutive standalone")
    fails(mutate("boz_table13_2010_style.txt", "      2009\n", "      2009\n      2010\n"), 140, "Consecutive standalone")  # two different years
    both = mutate("boz_table13_2010_style.txt", "                    January", "2009                January")  # inline + standalone
    fails(both, 140, "both inline and as a standalone")
    dangling = fixture("boz_table13_2010_style.txt").replace("      Source:", "      2011\n      Source:")
    fails(dangling, 140, "without a month row")
    fails(mutate("boz_table13_2010_style.txt", "      2009\n", "      2007\n"), 140, "not increasing")


def test_inline_year_conflicts_and_bad_sequences_are_rejected():
    page = fixture("boz_table13_2012_style.txt")
    fails(page.replace("2011  ", "2009  ", 1), 137, "not increasing")
    fails(mutate("boz_table13_2012_style.txt", "February", "March"), 137, "not consecutive")
    fails(mutate("boz_table13_2012_style.txt", "February  ", "January   "), 137, "not consecutive")  # duplicate month
    june = next(l for l in page.splitlines() if "June" in l)
    fails(page.replace(june + "\n", ""), 137, "not consecutive")  # a gap inside a year
    fails(mutate("boz_table13_2012_style.txt", "2012                      January", "2012                      March"), 137, "start at January or December")


# ---------------------------------------------------------------- parser: identity and shape (fail closed)
@pytest.mark.parametrize("old,new,match", [
    ("TABLE 13", "TABLE 14", "table identity"),
    ("KWACHA/US DOLLAR EXCHANGE RATES", "KWACHA/POUND EXCHANGE RATES", "table identity"),
    ("Bureau Rates", "Retail Rates", "column groups"),
    ("Buying          Selling             Mid          Buying", "Selling          Buying             Mid          Buying", "column labels"),
    ("Monthly Average", "Quarterly Average", "column labels"),
    ("      Source: Bank of Zambia", "      Reference: Bank of Zambia", "truncated|Unexpected line"),
])
def test_unexpected_table_identity_or_layout_is_rejected(old, new, match):
    fails(mutate("boz_table13_2012_style.txt", old, new), 137, match)


@pytest.mark.parametrize("name,page", [
    ("boz_table13_2010_style.txt", 140), ("boz_table13_2012_style.txt", 137),
    ("boz_table13_2013_style.txt", 138), ("boz_table13_2014_style.txt", 131),
])
def test_each_vetted_real_layout_heading_is_still_accepted(name, page):
    assert parse(name, page).rows


def test_2011_heading_form_is_accepted():  # the 2011 report prints the same heading line as 2012
    assert parse("boz_table13_2012_style.txt", 137).rows


def test_probe_retail_only_suffix_before_table_13_is_rejected():
    page = fixture("boz_table13_2012_style.txt")
    assert "EXCHANGE RATES      " in page
    fails(page.replace("EXCHANGE RATES      ", "EXCHANGE RATES - RETAIL ONLY", 1), 137, "table identity")


def test_probe_table_14_heading_with_nearby_table_13_is_rejected():
    page = fixture("boz_table13_2012_style.txt")
    page = page.replace("TABLE 13", "TABLE 14 ... TABLE 13", 1)
    fails(page, 137, "table identity")
    # a Table 14 heading plus a separate unrelated "TABLE 13" line
    page = fixture("boz_table13_2012_style.txt").replace("TABLE 13", "TABLE 14", 1)
    fails("      SOMETHING ELSE                    TABLE 13\n" + page, 137, "table identity")


def test_probe_duplicate_candidate_headings_are_rejected():
    page = fixture("boz_table13_2012_style.txt")
    first = page.splitlines()[0]
    fails(first + "\n" + page, 137, "table identity")


@pytest.mark.parametrize("heading", [
    "KWACHA/US DOLLAR EXCHANGE RATES TABLE 130", "KWACHA/US DOLLAR EXCHANGE RATES TABLE 13 (REVISED)",
    "NEW KWACHA/US DOLLAR EXCHANGE RATES TABLE 13", "KWACHA/US DOLLAR EXCHANGE RATES TABLE 3",
    "KWACHA/US DOLLAR EXCHANGE RATES", "TABLE 13",
])
def test_other_inserted_prefix_suffix_or_missing_parts_are_rejected(heading):
    page = fixture("boz_table13_2012_style.txt").splitlines()
    page[0] = "      " + heading
    fails("\n".join(page) + "\n", 137, "table identity")


def test_wrong_printed_page_is_rejected():
    fails(fixture("boz_table13_2012_style.txt"), 140, "Printed page number")
    fails(fixture("boz_table13_2010_style.txt"), 141, "Printed page number")


def test_decimals_columns_and_rows_are_validated():
    base = "boz_table13_2012_style.txt"
    fails(mutate(base, "5,228.97", "5,228.9"), 137, "Unexpected line")  # one decimal
    fails(mutate(base, "5,228.97", "5,228,97"), 137, "Unexpected line")  # malformed decimal
    fails(mutate(base, "5,228.97", "5,22a.97"), 137, "Unexpected line")
    fails(mutate(base, "5,228.97", "0.00"), 137, "Non-positive")
    row = next(l for l in fixture(base).splitlines() if l.strip().startswith("2011"))
    parts = row.split()
    fails(fixture(base).replace(row, " ".join(parts[:-1])), 137, "Unexpected line")  # a missing rate
    fails(fixture(base).replace(row, row + "        1.00"), 137, "Unexpected line")  # an extra column


def test_empty_and_truncated_tables_are_rejected():
    fails("KWACHA/US DOLLAR EXCHANGE RATES TABLE 13\nnothing", 137, "No Table 13 rows")
    page = fixture("boz_table13_2012_style.txt")
    fails(page[: page.index("      Source:")], 137, "truncated")


def test_ordering_is_enforced_from_the_window_start_only():
    row = next(l for l in fixture("boz_table13_2013_style.txt").splitlines() if "February" in l)
    nums = row.split()[1:]
    bad = fixture("boz_table13_2013_style.txt").replace(row, row.replace(nums[0], "9.99", 1))
    fails(bad, 138, "Ordering violated", ordering_from=date(2013, 1, 1))
    # the same violation before the window is a structure-only row: parsed, never usable
    assert tab.parse_table13(bad, printed_page=138, ordering_from=date(2014, 1, 1)).rows


# ---------------------------------------------------------------- ordering exceptions
def test_exceptions_are_exactly_the_two_documented_months():
    assert [(e.series, e.month) for e in tab.ORDERING_EXCEPTIONS] == [
        (tab.ANNUAL_SOURCE_SERIES, date(2012, 4, 1)), (tab.ANNUAL_SOURCE_SERIES, date(2013, 12, 1))]


def test_normal_ordering_is_still_enforced_and_exceptions_are_not_generic():
    ok = tab.PublishedRate(date(2013, 5, 1), D("5.31"), D("5.32"), D("5.33"))
    tab.check_ordering(ok)
    for bad in (tab.PublishedRate(date(2013, 5, 1), D("5.33"), D("5.32"), D("5.31")),  # buy > sell
                tab.PublishedRate(date(2013, 5, 1), D("5.31"), D("5.34"), D("5.33"))):  # mid > sell
        with pytest.raises(tab.AnnualTableError, match="Ordering violated"):
            tab.check_ordering(bad)
    # The 2012-04 violation shape on any other month fails.
    with pytest.raises(tab.AnnualTableError, match="Ordering violated"):
        tab.check_ordering(tab.PublishedRate(date(2012, 5, 1), D("5283.60"), D("5240.97"), D("5250.97")))
    # 2013-12's shape on another month fails; the exception also never applies to another series.
    with pytest.raises(tab.AnnualTableError, match="Ordering violated"):
        tab.check_ordering(tab.PublishedRate(date(2013, 11, 1), D("5.52"), D("5.54"), D("5.53")))
    with pytest.raises(tab.AnnualTableError, match="Ordering violated"):
        tab.check_ordering(tab.PublishedRate(date(2013, 12, 1), D("5.52"), D("5.54"), D("5.53")), series="fortnightly_table_02_monthly_average")


def test_each_exception_month_is_accepted_only_with_its_exact_values():
    tab.check_ordering(tab.PublishedRate(date(2012, 4, 1), D("5283.60"), D("5240.97"), D("5250.97")))
    tab.check_ordering(tab.PublishedRate(date(2012, 4, 1), D("5283.6"), D("5240.970"), D("5250.97")))  # numerically equal
    tab.check_ordering(tab.PublishedRate(date(2013, 12, 1), D("5.52"), D("5.54"), D("5.53")))
    for altered in (tab.PublishedRate(date(2012, 4, 1), D("5283.61"), D("5240.97"), D("5250.97")),
                    tab.PublishedRate(date(2012, 4, 1), D("5283.60"), D("5240.97"), D("5250.98")),
                    tab.PublishedRate(date(2013, 12, 1), D("5.52"), D("5.55"), D("5.53")),
                    tab.PublishedRate(date(2013, 12, 1), D("5.52"), D("5.52"), D("5.53"))):  # ordered, but no longer the exception
        with pytest.raises(tab.AnnualTableError, match="approved published values"):
            tab.check_ordering(altered)


def test_a_fixture_row_altered_on_an_exception_month_fails_the_parse():
    fails(mutate("boz_table13_2012_style.txt", "5,250.97", "5,250.99"), 137, "approved published values")
    fails(mutate("boz_table13_2013_style.txt", "5.52            5.53            5.54", "5.52            5.53            5.55"),
          138, "approved published values", ordering_from=date(2013, 1, 1))


# ---------------------------------------------------------------- normalization
def rate(y, m, buy, mid, sell):
    return tab.PublishedRate(date(y, m, 1), D(buy), D(mid), D(sell))


def test_old_kwacha_months_divide_exactly_by_1000_without_rounding():
    n = normalize(rate(2010, 1, "4504.49", "4513.99", "4524.49"))
    assert (n.published_currency, n.normalization_divisor) == ("ZMK", 1000)
    assert (n.buy, n.mid, n.sell) == (D("4.50449"), D("4.51399"), D("4.52449"))  # more decimals than the 2 printed
    assert (n.published_buy, n.published_mid, n.published_sell) == (D("4504.49"), D("4513.99"), D("4524.49"))
    assert str(normalize(rate(2012, 4, "5283.60", "5240.97", "5250.97")).buy) == "5.28360"  # no trailing-zero loss, no rounding
    assert str(normalize(rate(2012, 12, "5198.47", "5208.46", "5228.97")).mid) == "5.20846"


def test_rebased_months_use_divisor_one_and_the_boundary_is_by_month():
    dec12, jan13 = normalize(rate(2012, 12, "5198.47", "5208.46", "5228.97")), normalize(rate(2013, 1, "5.27", "5.28", "5.29"))
    assert (dec12.published_currency, dec12.normalization_divisor, dec12.mid) == ("ZMK", 1000, D("5.20846"))
    assert (jan13.published_currency, jan13.normalization_divisor, jan13.mid) == ("ZMW", 1, D("5.28"))
    assert tab.published_currency_for(date(2012, 12, 1)) == "ZMK" and tab.published_currency_for(date(2013, 1, 1)) == "ZMW"
    for y in (2010, 2011, 2012):
        assert normalize(rate(y, 6, "5000.00", "5000.01", "5000.02")).normalization_divisor == 1000


def test_regime_is_never_inferred_from_magnitude():
    small_old = normalize(rate(2011, 3, "5.28", "5.29", "5.30"))  # rebased-looking value in an old-kwacha month
    big_new = normalize(rate(2013, 3, "5283.60", "5284.60", "5285.60"))  # old-looking value in a rebased month
    assert small_old.normalization_divisor == 1000 and small_old.mid == D("0.00529")
    assert big_new.normalization_divisor == 1 and big_new.mid == D("5284.60")
    with pytest.raises(NormalizationError, match="contradicts"):
        normalize(rate(2013, 3, "5.28", "5.29", "5.30"), currency="ZMK")
    with pytest.raises(NormalizationError, match="contradicts"):
        normalize(rate(2011, 3, "5283.60", "5284.60", "5285.60"), currency="ZMW")


def test_normalization_is_exact_decimal_and_never_float():
    n = normalize(rate(2010, 7, "5007.00", "5017.02", "5027.02"))
    assert isinstance(n.mid, D) and n.mid * 1000 == n.published_mid
    assert all(isinstance(v, D) for v in (n.buy, n.mid, n.sell))


# ---------------------------------------------------------------- manifest and loading
def test_manifest_pins_canonical_windows_and_marks_2014_as_validation_only():
    assert [r.key for r in src.CANONICAL_REPORTS] == ["ar2010", "ar2011", "ar2012", "ar2013"]
    assert [(r.persist_first, r.persist_last) for r in src.CANONICAL_REPORTS] == [
        (date(y, 1, 1), date(y, 12, 1)) for y in (2010, 2011, 2012, 2013)]
    assert [r.key for r in src.VALIDATION_REPORTS] == ["ar2014"]
    assert src.VALIDATION_REPORTS[0].role == "validation" and src.VALIDATION_REPORTS[0].persist_first is None
    assert (src.FIRST_COVERED_MONTH, src.LAST_COVERED_MONTH) == (date(2010, 1, 1), date(2013, 12, 1))
    assert {r.key for r in src.ALL_REPORTS} == {"ar2010", "ar2011", "ar2012", "ar2013", "ar2014"}  # no 2015 report
    assert [(r.pdf_page, r.printed_page) for r in src.ALL_REPORTS] == [(148, 140), (141, 133), (145, 137), (146, 138), (139, 131)]
    assert all(len(r.sha256) == 64 and r.url.startswith("https://www.boz.zm/sites/default/files/migrated/annual-reports/")
               for r in src.ALL_REPORTS)
    assert tab.ANNUAL_SOURCE_SERIES != "fortnightly_table_02_monthly_average"


def test_vetted_url_guard():
    assert src.is_vetted_url(src.ALL_REPORTS[0].url)
    for bad in ("http://www.boz.zm" + src.ALL_REPORTS[0].path, "https://evil.example" + src.ALL_REPORTS[0].path,
                src.ALL_REPORTS[0].url + "?x=1", "https://www.boz.zm/sites/default/files/other.pdf", None, 5):
        assert not src.is_vetted_url(bad)


def fake_report(name: str, page_number: int, *, first, last, rows, role="canonical", window=(date(2013, 1, 1), date(2013, 12, 1)), ordering=date(2013, 1, 1)):
    pdf = b"%PDF-1.4 fake " + name.encode()
    base = src.CANONICAL_REPORTS[3]
    return pdf, dataclasses.replace(
        base, sha256=hashlib.sha256(pdf).hexdigest(), size_bytes=len(pdf), pdf_page=5, printed_page=page_number, row_count=rows,
        first_row=first, last_row=last, role=role, persist_first=window[0] if role == "canonical" else None,
        persist_last=window[1] if role == "canonical" else None, ordering_from=ordering)


def test_load_annual_report_verifies_hash_extracts_the_pinned_page_and_checks_pins():
    pdf, report = fake_report("a", 138, first=date(2000, 12, 1), last=date(2013, 12, 1), rows=15)
    calls = []
    extractor = lambda data, page: (calls.append((data, page)), fixture("boz_table13_2013_style.txt"))[1]
    table = src.load_annual_report(report, pdf, extractor)
    assert calls == [(pdf, 5)] and len(table.rows) == 15
    assert len(src.window_rows(report, table)) == 12
    with pytest.raises(src.AnnualSourceError, match="SHA-256"):
        src.load_annual_report(report, pdf[:-1] + b"X", extractor)  # same size, different bytes
    with pytest.raises(src.AnnualSourceError, match="size"):
        src.load_annual_report(report, b"%PDF-1.4 short", extractor)
    for changed, match in ((dataclasses.replace(report, row_count=14), "expected 14"),
                           (dataclasses.replace(report, last_row=date(2013, 11, 1)), "spans")):
        with pytest.raises(src.AnnualSourceError, match=match):
            src.load_annual_report(changed, pdf, extractor)
    with pytest.raises(src.AnnualSourceError, match="Printed page"):  # extractor returned another page
        src.load_annual_report(report, pdf, lambda d, p: fixture("boz_table13_2012_style.txt"))
    def broken(d, p): raise layout.LayoutExtractionError("pdftotext was not found")
    with pytest.raises(src.AnnualSourceError, match="pdftotext was not found"):
        src.load_annual_report(report, pdf, broken)


def test_window_with_a_missing_month_fails_and_a_validation_report_supplies_no_rows():
    pdf, report = fake_report("b", 138, first=date(2000, 12, 1), last=date(2013, 12, 1), rows=14, window=(date(2013, 1, 1), date(2013, 12, 1)))
    page = fixture("boz_table13_2013_style.txt")
    june = next(l for l in page.splitlines() if "June" in l)
    with pytest.raises(src.AnnualSourceError, match="not consecutive"):
        src.load_annual_report(report, pdf, lambda d, p: page.replace(june + "\n", ""))
    pdf2, validation = fake_report("c", 131, first=date(2005, 12, 1), last=date(2014, 12, 1), rows=15, role="validation", ordering=date(2014, 1, 1))
    table = src.load_annual_report(validation, pdf2, lambda d, p: fixture("boz_table13_2014_style.txt"))
    with pytest.raises(src.AnnualSourceError, match="not a canonical persistence source"):
        src.window_rows(validation, table)


# ---------------------------------------------------------------- 2014 validation bridge
def phase1_2014_from(table):
    return [MonthlyObservation(r.month, r.buy, r.mid, r.sell, "Fortnightly 2016", "https://www.boz.zm/f.pdf", 3)
            for r in table.rows if r.month.year == 2014]


def test_2014_bridge_requires_all_12_months_to_match_exactly_and_changes_nothing():
    table = parse("boz_table13_2014_style.txt", 131, ordering_from=date(2014, 1, 1))
    stored = phase1_2014_from(table)
    before = list(stored)
    assert src.validate_2014_bridge(table, stored) == 12
    assert stored == before and all(o.source_url == "https://www.boz.zm/f.pdf" and o.source_page == 3 for o in stored)
    assert all(o.source_series is None for o in stored)  # provenance untouched; nothing Annual-Report is attached
    equal_but_scaled = [dataclasses.replace(o, buy=o.buy * D("1.000")) for o in stored]
    assert src.validate_2014_bridge(table, equal_but_scaled) == 12  # numeric equality, not text
    for index, field in ((0, "buy"), (5, "mid"), (11, "sell")):
        altered = list(stored)
        altered[index] = dataclasses.replace(altered[index], **{field: altered[index].__dict__[field] + D("0.01")})
        with pytest.raises(src.BridgeMismatchError, match=altered[index].month.strftime("%Y-%m")):
            src.validate_2014_bridge(table, altered)
    with pytest.raises(src.BridgeMismatchError):
        src.validate_2014_bridge(table, stored[:11])  # a missing stored month


# ---------------------------------------------------------------- real vetted reports (optional, never committed)
@pytest.mark.skipif(not os.environ.get("ZM_BOZ_ANNUAL_PDF_DIR") or shutil.which("pdftotext") is None,
                    reason="set ZM_BOZ_ANNUAL_PDF_DIR to a folder with the five vetted Annual Report PDFs (and install pdftotext)")
def test_real_vetted_reports_parse_and_pin():
    folder = Path(os.environ["ZM_BOZ_ANNUAL_PDF_DIR"])
    tables = {}
    for report in src.ALL_REPORTS:
        tables[report.key] = src.load_annual_report(report, (folder / report.filename).read_bytes())
    union = {}
    for report in src.CANONICAL_REPORTS:
        for row in src.window_rows(report, tables[report.key]):
            union[row.month] = row
    assert len(union) == 48 and min(union) == date(2010, 1, 1) and max(union) == date(2013, 12, 1)
    assert normalize(union[date(2012, 12, 1)]).mid == D("5.20846") and normalize(union[date(2013, 1, 1)]).mid == D("5.28")
    assert [m for m in union if not union[m].buy <= union[m].mid <= union[m].sell] == [date(2012, 4, 1), date(2013, 12, 1)]


# ---------------------------------------------------------------- PostgreSQL (opt-in): migration 013 and storage
INIT = Path(__file__).parents[3] / "db/init"
M013 = INIT / "013_fx_monthly_published_values.sql"


def make_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    return create_engine(url)


@pytest.fixture
def pre013():
    """The table exactly as migration 012 left it (what the existing local database has)."""
    eng = make_engine()
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS fx_monthly_averages"))
        c.execute(text((INIT / "012_fx_monthly_averages.sql").read_text()))
    yield eng
    eng.dispose()


@pytest.fixture
def engine(pre013):
    with pre013.begin() as c:
        c.execute(text(M013.read_text()))
    return pre013


PHASE1_INSERT = text(
    "INSERT INTO fx_monthly_averages (base_currency, quote_currency, observation_month, buy, mid, sell, source, source_series, "
    "source_publication, source_url, source_page, source_table, published_precision, currency_regime, imported_at) VALUES "
    "('USD','ZMW',:m,:b,:mid,:s,'boz','fortnightly_table_02_monthly_average','Vol 24','https://www.boz.zm/x.pdf',3,'Table 02',2,'ZMW',:at)")
T0 = datetime(2026, 11, 15, 8, 30, 15, tzinfo=timezone.utc)


def test_pg_migration_013_upgrades_existing_phase1_rows_in_place(pre013):
    with pre013.begin() as c:
        for month, (b, m, s) in {date(2014, 1, 1): ("5.52", "5.53", "5.53"), date(2019, 3, 1): ("12.01", "12.04", "12.04"),
                                 date(2026, 9, 1): ("19.39", "19.42", "19.44")}.items():
            c.execute(PHASE1_INSERT, {"m": month, "b": D(b), "mid": D(m), "s": D(s), "at": T0})
        before = c.execute(text("SELECT * FROM fx_monthly_averages ORDER BY observation_month")).mappings().all()
    with pre013.begin() as c:
        c.execute(text(M013.read_text()))
    with pre013.connect() as c:
        after = c.execute(text("SELECT * FROM fx_monthly_averages ORDER BY observation_month")).mappings().all()
    assert len(after) == 3
    for old, new in zip(before, after):
        for column in old.keys():  # every pre-existing column, incl. id, imported_at and canonical rates, is unchanged
            assert new[column] == old[column], column
        assert (new["published_buy"], new["published_mid"], new["published_sell"]) == (old["buy"], old["mid"], old["sell"])
        assert (new["published_currency"], new["normalization_divisor"]) == ("ZMW", 1)
    assert str(after[1]["published_mid"]) == "12.04" and after[1]["imported_at"] == T0


def test_pg_migration_013_is_additive_idempotent_and_fails_loudly_on_unknown_pre_2013_rows(pre013):
    with pre013.connect() as c:
        cols_before = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='fx_monthly_averages'"))}
    with pre013.begin() as c:
        c.execute(text(M013.read_text()))
        c.execute(text(M013.read_text()))  # second application is a no-op
    with pre013.connect() as c:
        cols_after = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='fx_monthly_averages'"))}
    assert cols_before < cols_after and cols_after - cols_before == {
        "published_buy", "published_mid", "published_sell", "published_currency", "normalization_divisor"}
    # An old-kwacha month with no recorded published values cannot be guessed: the migration refuses instead of inventing them.
    with pre013.begin() as c:
        c.execute(text("DROP TABLE fx_monthly_averages"))
        c.execute(text((INIT / "012_fx_monthly_averages.sql").read_text()))
        c.execute(PHASE1_INSERT, {"m": date(2012, 4, 1), "b": D("5283.60"), "mid": D("5240.97"), "s": D("5250.97"), "at": T0})
    with pytest.raises(IntegrityError):  # SET NOT NULL fails on the row it was not allowed to guess
        with pre013.begin() as c:
            c.execute(text(M013.read_text()))
    with pre013.connect() as c:  # the failed single-transaction migration changed nothing
        cols = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='fx_monthly_averages'"))}
        stored = c.execute(text("SELECT buy FROM fx_monthly_averages")).scalar()
    assert cols == cols_before and stored == D("5283.60")


def annual_obs(month: date, published: tuple[str, str, str]):
    n = normalize(tab.PublishedRate(month, D(published[0]), D(published[1]), D(published[2])))
    return MonthlyObservation(
        month, n.buy, n.mid, n.sell, "Bank of Zambia 2012 Annual Report", "https://www.boz.zm/sites/default/files/migrated/annual-reports/2012/BOZANNUALREPORT2012.pdf",
        145, source_series=tab.ANNUAL_SOURCE_SERIES, source_table=tab.ANNUAL_SOURCE_TABLE, published_precision=2,
        published_buy=n.published_buy, published_mid=n.published_mid, published_sell=n.published_sell,
        published_currency=n.published_currency, normalization_divisor=n.normalization_divisor)


def test_pg_store_persists_printed_and_canonical_values_exactly_and_is_idempotent(engine):
    batch = [annual_obs(date(2012, 4, 1), ("5283.60", "5240.97", "5250.97")),  # published ordering exception, stored as printed
             annual_obs(date(2012, 12, 1), ("5198.47", "5208.46", "5228.97")),
             annual_obs(date(2013, 1, 1), ("5.27", "5.28", "5.29"))]
    assert save_monthly_averages(batch, imported_at=NOW, engine=engine) == (3, 0)
    assert save_monthly_averages(batch, imported_at=NOW, engine=engine) == (0, 3)
    with engine.connect() as c:
        rows = {r["observation_month"]: r for r in c.execute(text("SELECT * FROM fx_monthly_averages")).mappings()}
    apr = rows[date(2012, 4, 1)]
    assert (apr["published_buy"], apr["published_mid"], apr["published_sell"]) == (D("5283.60"), D("5240.97"), D("5250.97"))
    assert (apr["buy"], apr["mid"], apr["sell"]) == (D("5.28360"), D("5.24097"), D("5.25097")) and str(apr["buy"]) == "5.28360"
    assert (apr["published_currency"], apr["normalization_divisor"], apr["source_series"], apr["source_table"]) == (
        "ZMK", 1000, tab.ANNUAL_SOURCE_SERIES, "Table 13")
    assert (rows[date(2013, 1, 1)]["published_currency"], rows[date(2013, 1, 1)]["normalization_divisor"]) == ("ZMW", 1)
    assert apr["currency_regime"] == "ZMW"  # canonical regime stays ZMW
    # A phase-1 style observation still saves (printed = canonical, ZMW, divisor 1) next to them.
    phase1 = MonthlyObservation(date(2014, 1, 1), D("5.52"), D("5.53"), D("5.53"), "Vol 24", "https://www.boz.zm/x.pdf", 3)
    assert save_monthly_averages([phase1], imported_at=NOW, engine=engine) == (1, 0)
    with engine.connect() as c:
        row = c.execute(text("SELECT * FROM fx_monthly_averages WHERE observation_month='2014-01-01'")).mappings().one()
    assert (row["published_buy"], row["published_currency"], row["normalization_divisor"]) == (D("5.52"), "ZMW", 1)
    assert fetch_fx_monthly("ALL", engine=engine).count == 1  # the API still serves only the Fortnightly series (unchanged contract)


def insert_row(engine, **override):
    row = dict(m=date(2012, 4, 1), b=D("5.28360"), mid=D("5.24097"), s=D("5.25097"), pb=D("5283.60"), pm=D("5240.97"), ps=D("5250.97"),
               cur="ZMK", div=1000, series="annual_report_table_13_monthly_average")
    row.update(override)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO fx_monthly_averages (base_currency, quote_currency, observation_month, buy, mid, sell, source, source_series, "
            "source_publication, source_url, source_page, source_table, published_precision, currency_regime, imported_at, "
            "published_buy, published_mid, published_sell, published_currency, normalization_divisor) VALUES "
            "('USD','ZMW',:m,:b,:mid,:s,'boz',:series,'p','u',1,'Table 13',2,'ZMW',:at,:pb,:pm,:ps,:cur,:div)"),
            {**row, "at": T0})


def test_pg_constraints_reject_inconsistent_or_unsafe_published_values(engine):
    insert_row(engine)  # the consistent row is accepted
    cases = {
        "nonfinite published": dict(pb=D("NaN")), "infinite published": dict(pm=D("Infinity")), "negative infinity": dict(ps=D("-Infinity")),
        "zero published": dict(pb=D("0")), "negative published": dict(pm=D("-1")),
        "invalid divisor": dict(div=10, m=date(2012, 5, 1)), "zero divisor": dict(div=0, m=date(2012, 5, 1)),
        "canonical disagrees (buy)": dict(b=D("5.28361"), m=date(2012, 6, 1)),
        "canonical disagrees (mid)": dict(mid=D("5.2"), m=date(2012, 6, 1)),
        "canonical disagrees (sell)": dict(s=D("5.25098"), m=date(2012, 6, 1)),
        "canonical equals printed under 1000": dict(b=D("5283.60"), mid=D("5240.97"), s=D("5250.97"), m=date(2012, 6, 1)),
        "old kwacha month marked ZMW": dict(cur="ZMW", div=1, b=D("5283.60"), mid=D("5240.97"), s=D("5250.97"), m=date(2012, 6, 1)),
        "rebased month marked ZMK": dict(m=date(2013, 1, 1)),
        "bad currency": dict(cur="USD", m=date(2012, 6, 1)),
    }
    for name, override in cases.items():
        with pytest.raises(IntegrityError):
            insert_row(engine, **override)
        assert name
    insert_row(engine, m=date(2013, 1, 1), b=D("5.27"), mid=D("5.28"), s=D("5.29"), pb=D("5.27"), pm=D("5.28"), ps=D("5.29"), cur="ZMW", div=1)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar() == 2


def test_pg_numeric_equality_ignores_scale_and_no_ordering_constraint_exists(engine):
    insert_row(engine, m=date(2012, 7, 1), b=D("5.2836000"), pb=D("5283.6"))  # scale differs, value equal
    insert_row(engine, m=date(2012, 8, 1), b=D("9"), mid=D("5"), s=D("1"), pb=D("9000"), pm=D("5000"), ps=D("1000"))  # buy > mid > sell
    with engine.connect() as c:
        names = [r[0] for r in c.execute(text("SELECT conname FROM pg_constraint WHERE conrelid='fx_monthly_averages'::regclass"))]
    assert not any("order" in n for n in names)
    assert {"fx_monthly_published_values_finite_positive", "fx_monthly_normalization_regime",
            "fx_monthly_canonical_equals_published_over_divisor"} <= set(names)


# ---------------------------------------------------------------- all-or-none publication metadata
from app.services.fx_monthly_store import PublicationMetadataError, has_publication_metadata, row_params  # noqa: E402

PUB = dict(published_buy=D("5283.60"), published_mid=D("5240.97"), published_sell=D("5250.97"),
           published_currency="ZMK", normalization_divisor=1000)


def obs(**extra):
    return MonthlyObservation(date(2012, 4, 1), D("5.28360"), D("5.24097"), D("5.25097"), "t", "u", 1,
                              source_series=tab.ANNUAL_SOURCE_SERIES, source_table=tab.ANNUAL_SOURCE_TABLE, **extra)


@pytest.mark.parametrize("keep", [
    ["published_mid"], ["published_sell"], ["published_currency"], ["normalization_divisor"], ["published_buy"],
    ["published_buy", "published_mid", "published_sell"],  # missing currency and divisor
    ["published_buy", "published_mid", "published_sell", "published_currency"],  # missing divisor
    ["published_currency", "normalization_divisor"],  # both present, no rates
    ["published_currency", "normalization_divisor", "published_buy", "published_mid"],  # one rate missing
    ["published_currency", "normalization_divisor", "published_buy", "published_sell"],
    ["published_currency", "normalization_divisor", "published_mid", "published_sell"],
])
def test_partial_publication_metadata_is_rejected_before_params_or_writes(keep):
    o = obs(**{k: PUB[k] for k in keep})
    with pytest.raises(PublicationMetadataError, match="Partial publication metadata"):
        has_publication_metadata(o)
    with pytest.raises(PublicationMetadataError):
        row_params(o, T0)
    boom = type("E", (), {"begin": lambda self: (_ for _ in ()).throw(AssertionError("database touched"))})()
    with pytest.raises(PublicationMetadataError):  # rejected before any connection is opened
        save_monthly_averages([obs(**PUB), o], imported_at=T0, engine=boom)


def test_absent_metadata_defaults_to_phase1_and_complete_metadata_is_preserved_exactly():
    legacy = MonthlyObservation(date(2014, 1, 1), D("6.10"), D("6.15"), D("6.20"), "t", "u", 1)
    assert has_publication_metadata(legacy) is False
    p = row_params(legacy, T0)
    assert (p["published_buy"], p["published_mid"], p["published_sell"]) == (D("6.10"), D("6.15"), D("6.20"))
    assert (p["published_currency"], p["normalization_divisor"]) == ("ZMW", 1)
    full = obs(**PUB)
    assert has_publication_metadata(full) is True
    p = row_params(full, T0)
    assert {k: p[k] for k in PUB} == PUB


def test_imported_at_is_irrelevant_to_metadata_equality():
    a, b = row_params(obs(**PUB), T0), row_params(obs(**PUB), datetime(2031, 1, 1, tzinfo=timezone.utc))
    assert {k: v for k, v in a.items() if k != "imported_at"} == {k: v for k, v in b.items() if k != "imported_at"}
