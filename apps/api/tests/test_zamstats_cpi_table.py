"""ZamStats CPI table parsers on SYNTHETIC fixtures (tests/fixtures/zamstats_cpi_*.txt): invented numbers that keep only the
layout shapes the real tables have (title anchors, year labels, wrapped rows, notations). Offline, pure."""

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.zamstats_cpi_models import PARENTHESES, ZamstatsCpiError
from app.services.zamstats_cpi_table import (
    ANNUAL_ROWS, HORIZONTAL_13, QUOTED_MONTHS, VERTICAL_LABELLED, TableSpec, parse_table,
)

FIXTURES = Path(__file__).parent / "fixtures"
D = date
IDX = (("index", "all_items", True), ("index", "food", True), ("index", "non_food", True))
MON = (("monthly_inflation", "all_items", True), ("monthly_inflation", "food", True), ("monthly_inflation", "non_food", True))
ANN = (("index", "all_items", False), ("annual_inflation", "all_items", True))


def text(name: str) -> str:
    return (FIXTURES / f"zamstats_cpi_{name}.txt").read_text()


def edit(page: str, old: str, new: str) -> str:
    assert page.count(old) == 1, f"fixture edit anchor must be unique: {old!r}"
    return page.replace(old, new)


LABELLED = TableSpec("Table 1.1", VERTICAL_LABELLED, 23, r"Table 1\.1: Consumer Price Index - Food and Non-Food \(National\)",
                     D(2022, 10, 1), D(2023, 3, 1), IDX)
PREFIXED = TableSpec("Index", VERTICAL_LABELLED, 23, r"Consumer Index Numbers of Consumer Prices - Food and Non-Food \(National\)",
                     D(2010, 10, 1), D(2011, 2, 1), IDX, authorized_duplicate_months=frozenset({D(2010, 12, 1)}))
ANNUAL = TableSpec("Annual", ANNUAL_ROWS, 31, r"CONSUMER PRICE INDICES \(2009=100\)", D(2022, 11, 1), D(2023, 2, 1), ANN)
ANNUAL_LAB = TableSpec("Table 1.3", VERTICAL_LABELLED, 25, r"Table 1\.3: Consumer Price Indices and Annual Inflation",
                       D(2022, 10, 1), D(2023, 3, 1), ANN)
QUOTED = TableSpec("Month on month", QUOTED_MONTHS, 5, r"Month on month Inflation Rates: Food and Non Food Items",
                   D(2011, 1, 1), D(2012, 1, 1), MON)
# note: the quoted fixture lists Jan-Mar 2011 and Jan 2012, so the contiguous run is deliberately broken (see test below)
HORIZ = TableSpec("Table 7", HORIZONTAL_13, 12, r"Table 7: Overall Monthly Inflation Rate for Food and Non-Food Items",
                  D(2098, 12, 1), D(2099, 12, 1), MON, base_on_page=False)
WRAPPED = TableSpec("Table 5", HORIZONTAL_13, 9, r"Table 5: Overall Monthly Inflation Rate for Food and Non-Food Items",
                    D(2098, 10, 1), D(2099, 10, 1), MON, base_on_page=False, wrapped_non_food_tail=4)


def parse(page: str, spec: TableSpec):
    return parse_table(page, spec, "k")


def by_series(table):
    out: dict[str, list] = {}
    for o in table.observations:
        out.setdefault(o.series, []).append(o)
    return out


# ------------------------------------------------------------------ identification
def test_labelled_index_table_is_accepted_with_exact_months_and_three_series():
    t = parse(text("index_labelled"), LABELLED)
    assert (t.first_month, t.last_month) == (D(2022, 10, 1), D(2023, 3, 1)) and len(t.observations) == 18
    s = by_series(t)
    assert set(s) == {"index_all_items", "index_food", "index_non_food"}
    assert [o.month for o in s["index_all_items"]] == [D(2022, 10, 1), D(2022, 11, 1), D(2022, 12, 1), D(2023, 1, 1), D(2023, 2, 1), D(2023, 3, 1)]
    assert s["index_food"][1].number.source_value_text == "201.25" and s["index_non_food"][5].number.value == Decimal("305.88")
    assert all(o.regime == "2009=100" and o.manifest_key == "k" and o.pdf_page == 23 for o in t.observations)
    assert [o.source_row for o in s["index_all_items"]] == [1, 2, 3, 4, 5, 6]


def test_missing_title_anchor_is_rejected():
    with pytest.raises(ZamstatsCpiError, match="matched 0 times"):
        parse(edit(text("index_labelled"), "Table 1.1: Consumer Price Index", "Table 1.9: Consumer Price Index"), LABELLED)


def test_duplicate_title_anchor_is_rejected_unless_pinned():
    page = text("index_labelled")
    twice = page + "\n Table 1.1: Consumer Price Index - Food and Non-Food (National), again\n"
    with pytest.raises(ZamstatsCpiError, match="matched 2 times"):
        parse(twice, LABELLED)
    assert parse(twice, dataclasses.replace(LABELLED, title_matches=2, title_pick=1)).first_month == D(2022, 10, 1)


def test_wrong_table_number_or_title_is_rejected():
    with pytest.raises(ZamstatsCpiError):
        parse(text("index_labelled"), dataclasses.replace(LABELLED, title_regex=r"Table 1\.2: Consumer Price Index"))


def test_wrong_base_1994_is_rejected():
    page = edit(text("index_labelled"), "Note: 2009 = 100", "Note: 1994 = 100")
    with pytest.raises(ZamstatsCpiError, match="base period other than"):
        parse(page, LABELLED)
    with pytest.raises(ZamstatsCpiError, match="base label is missing"):
        parse(edit(text("index_labelled"), "Note: 2009 = 100", "Note: see table"), LABELLED)
    # a table that pins its base on another page is allowed to omit it, but never to print a different one
    page_without = edit(text("index_labelled"), "Note: 2009 = 100", "Note: see table")
    assert parse(page_without, dataclasses.replace(LABELLED, base_on_page=False)).last_month == D(2023, 3, 1)
    with pytest.raises(ZamstatsCpiError, match="base period other than"):
        parse(edit(text("index_labelled"), "Note: 2009 = 100", "Note: 1994 = 100"), dataclasses.replace(LABELLED, base_on_page=False))


def test_empty_page_is_rejected():
    with pytest.raises(ZamstatsCpiError, match="page text is empty"):
        parse("  \n", LABELLED)


# ------------------------------------------------------------------ month sequence
def test_pinned_first_or_last_month_mismatch_is_rejected():
    with pytest.raises(ZamstatsCpiError, match="rows cover"):
        parse(text("index_labelled"), dataclasses.replace(LABELLED, first_month=D(2022, 9, 1)))
    with pytest.raises(ZamstatsCpiError, match="rows cover"):
        parse(text("index_labelled"), dataclasses.replace(LABELLED, last_month=D(2023, 4, 1)))


def test_missing_month_is_rejected():
    page = edit(text("index_labelled"), "                                     November                 101.22         201.25          301.44\n", "")
    with pytest.raises(ZamstatsCpiError):
        parse(page, LABELLED)


def test_duplicate_month_is_rejected_when_not_authorised():
    page = edit(text("index_labelled"), "                                     December                 102.33         202.36          302.55\n",
                "                                     December                 102.33         202.36          302.55\n"
                "                                     December                 102.33         202.36          302.55\n")
    with pytest.raises(ZamstatsCpiError, match="duplicated printed rows"):
        parse(page, LABELLED)


def test_unexpected_extra_row_is_rejected():
    page = edit(text("index_labelled"), " Source: ZamStats", "                                     April                    106.77         206.7           306.99\n Source: ZamStats")
    with pytest.raises(ZamstatsCpiError, match="rows cover"):
        parse(page, LABELLED)


def test_year_transition_is_assigned_from_blocks():
    t = parse(text("index_labelled"), LABELLED)
    assert [o.month for o in by_series(t)["index_food"]][2:4] == [D(2022, 12, 1), D(2023, 1, 1)]


def test_missing_or_surplus_year_label_is_rejected():
    page = edit(text("index_labelled"), "  2023\n", "")
    with pytest.raises(ZamstatsCpiError, match="year block 2 has 0 year labels"):
        parse(page, LABELLED)
    page = edit(text("index_labelled"), "  2023\n", "  2023\n  2024\n")
    with pytest.raises(ZamstatsCpiError, match="year labels"):
        parse(page, LABELLED)


def test_non_consecutive_year_labels_are_rejected():
    with pytest.raises(ZamstatsCpiError, match="not consecutive"):
        parse(edit(text("index_labelled"), "  2023\n", "  2025\n"), LABELLED)


def test_label_outside_its_block_is_not_accepted():
    # the 2022 label printed before any row of block 1 does not belong to the block
    page = edit(text("index_labelled"), "  2022\n", "")
    page = edit(page, "                                     October ", "  2022\n                                     October ")
    with pytest.raises(ZamstatsCpiError, match="year block 1 has 0 year labels"):
        parse(page, LABELLED)


def test_year_prefixed_layout_with_authorised_duplicate_row():
    t = parse(text("index_prefixed_duplicate"), PREFIXED)
    assert (t.first_month, t.last_month) == (D(2010, 10, 1), D(2011, 2, 1)) and len(by_series(t)["index_all_items"]) == 5
    assert [d.month for d in t.duplicate_rows] == [D(2010, 12, 1)]  # reported, never silently dropped
    s = by_series(t)
    assert s["index_all_items"][2].month == D(2010, 12, 1) and s["index_all_items"][3].month == D(2011, 1, 1)  # the next month is not shifted
    assert s["index_non_food"][3].number.source_value_text == "107.9"  # a dropped trailing zero is accepted by the index grammar


def test_duplicate_row_requires_an_explicit_authorisation_for_that_month():
    with pytest.raises(ZamstatsCpiError, match="duplicated printed rows"):
        parse(text("index_prefixed_duplicate"), dataclasses.replace(PREFIXED, authorized_duplicate_months=frozenset()))
    with pytest.raises(ZamstatsCpiError, match="not an authorised month"):
        parse(text("index_prefixed_duplicate"), dataclasses.replace(PREFIXED, authorized_duplicate_months=frozenset({D(2010, 11, 1)})))


def test_naive_row_counter_shift_is_prevented_by_explicit_handling():
    page = text("index_prefixed_duplicate")
    t = parse(page, PREFIXED)
    months = [o.month for o in t.observations if o.series == "index_food"]
    assert months == [D(2010, 10, 1), D(2010, 11, 1), D(2010, 12, 1), D(2011, 1, 1), D(2011, 2, 1)]
    # a non-identical "duplicate" is a different row, and a repeated month is never accepted as data
    other = edit(page, "                     December                   106.55         106.13               107.03\n             2011",
                 "                     December                   106.99         106.13               107.03\n             2011")
    with pytest.raises(ZamstatsCpiError):
        parse(other, PREFIXED)


def test_annual_rows_layout_year_on_every_row():
    t = parse(text("annual_rows"), ANNUAL)
    s = by_series(t)
    assert set(s) == {"index_all_items", "annual_inflation_all_items"}
    assert [o.month for o in s["annual_inflation_all_items"]] == [D(2022, 11, 1), D(2022, 12, 1), D(2023, 1, 1), D(2023, 2, 1)]
    assert [o.canonical_for_table for o in s["index_all_items"]] == [False] * 4  # validation column
    assert all(o.canonical_for_table for o in s["annual_inflation_all_items"])


def test_annual_labelled_layout_year_label_per_block():
    t = parse(text("annual_labelled"), ANNUAL_LAB)
    assert (t.first_month, t.last_month) == (D(2022, 10, 1), D(2023, 3, 1)) and len(t.observations) == 12


def test_annual_rows_missing_month_or_wrong_window_rejected():
    page = edit(text("annual_rows"), "                  2023                                     January                  152.33             -0.3\n", "")
    with pytest.raises(ZamstatsCpiError):
        parse(page, ANNUAL)
    with pytest.raises(ZamstatsCpiError, match="rows cover"):
        parse(text("annual_rows"), dataclasses.replace(ANNUAL, last_month=D(2023, 3, 1)))


def test_quoted_months_requires_a_contiguous_run_and_a_comparable_preceding_month():
    with pytest.raises(ZamstatsCpiError, match="month sequence breaks"):
        parse(text("monthly_quoted"), QUOTED)  # the fixture jumps from Mar 2011 to Jan 2012
    contiguous = edit(text("monthly_quoted"), "     2012                Jan '12 - Dec '11                     1.2                  1.8              0.3\n", "")
    t = parse(contiguous, dataclasses.replace(QUOTED, last_month=D(2011, 3, 1)))
    assert [o.month for o in t.observations][::3] == [D(2011, 1, 1), D(2011, 2, 1), D(2011, 3, 1)]
    wrong = edit(contiguous, "Mar '11 - Feb '11", "Mar '11 - Jan '11")
    with pytest.raises(ZamstatsCpiError, match="does not compare"):
        parse(wrong, dataclasses.replace(QUOTED, last_month=D(2011, 3, 1)))


def test_truncated_table_is_rejected():
    page = text("index_labelled")
    cut = page[: page.index("                                     February")] + " Source: ZamStats\n Note: 2009 = 100\n"
    with pytest.raises(ZamstatsCpiError):
        parse(cut, LABELLED)


# ------------------------------------------------------------------ categories
def test_all_items_food_non_food_only_and_no_unknown_category():
    for spec in (LABELLED, ANN and ANNUAL_LAB):
        t = parse(text("index_labelled") if spec is LABELLED else text("annual_labelled"), spec)
        assert {o.category for o in t.observations} <= {"all_items", "food", "non_food"}
    with pytest.raises(ZamstatsCpiError):
        parse(text("index_labelled"), dataclasses.replace(LABELLED, columns=(("index", "all_items", True), ("index", "division_1", True), ("index", "non_food", True))))


def test_annual_food_and_non_food_cannot_be_requested_from_a_table():
    for category in ("food", "non_food"):
        bad = dataclasses.replace(ANNUAL, columns=(("index", "all_items", False), ("annual_inflation", category, True)))
        with pytest.raises(ZamstatsCpiError, match="All Items only"):
            parse(text("annual_rows"), bad)


def test_duplicate_category_rows_are_rejected_in_horizontal_tables():
    page = text("monthly_horizontal")
    food_row = [line for line in page.splitlines() if line.startswith("Food ")][0]
    with pytest.raises(ZamstatsCpiError, match="food row matched 2 times"):
        parse(page.replace(food_row, food_row + "\n" + food_row), HORIZ)


# ------------------------------------------------------------------ numeric grammar and sign metadata
def test_horizontal_table_with_wrapped_non_food_label_and_all_notations():
    t = parse(text("monthly_horizontal"), HORIZ)
    s = by_series(t)
    assert len(t.observations) == 39 and [o.month for o in s["monthly_inflation_food"]][0] == D(2098, 12, 1)
    nf = s["monthly_inflation_non_food"]
    assert nf[4].number.source_value_text == "(0.3)" and nf[4].number.value == Decimal("-0.3") and nf[4].number.source_notation == PARENTHESES
    assert nf[9].number.source_value_text == "-0.8" and nf[9].number.source_negative
    zero = nf[10].number
    assert zero.source_value_text == "(0.0)" and zero.value == 0 and zero.source_negative and zero.source_notation == PARENTHESES
    assert all(isinstance(o.number.value, Decimal) for o in t.observations)  # no float anywhere


def test_header_years_are_not_used_a_typo_in_the_printed_year_is_harmless():
    # the fixture header prints 2024 in a 2099 window (as the real Dec 2025 table prints May-2024); the pinned window rules
    assert parse(text("monthly_horizontal"), HORIZ).observations[5 * 3].month == D(2099, 5, 1)  # the May column carries the typo year


def test_header_month_names_must_match_the_pinned_window():
    with pytest.raises(ZamstatsCpiError, match="header months"):
        parse(text("monthly_horizontal"), dataclasses.replace(HORIZ, first_month=D(2098, 11, 1), last_month=D(2099, 11, 1)))


def test_horizontal_weight_row_identity_is_enforced():
    with pytest.raises(ZamstatsCpiError, match="food row matched 0 times"):
        parse(edit(text("monthly_horizontal"), "534.85", "534.86"), HORIZ)


def test_wrapped_tail_variant_reads_the_tail_from_the_line_above():
    t = parse(text("monthly_wrapped_tail"), WRAPPED)
    nf = [o for o in t.observations if o.series == "monthly_inflation_non_food"]
    assert [o.number.source_value_text for o in nf] == ["0.5", "0.6", "3.0", "-0.3", "0.0", "0.4", "1.3", "0.1", "-0.2", "0.3", "0.2", "0.5", "1.2"]
    assert nf[3].number.source_negative and nf[4].number.source_negative is False  # -0.3 vs a plain 0.0
    with pytest.raises(ZamstatsCpiError, match="wrapped non_food row"):
        parse(text("monthly_wrapped_tail"), dataclasses.replace(WRAPPED, wrapped_non_food_tail=3))
    with pytest.raises(ZamstatsCpiError):
        parse(text("monthly_wrapped_tail"), dataclasses.replace(WRAPPED, wrapped_non_food_tail=0))  # not wrapped for this layout


@pytest.mark.parametrize("bad", ["+0.3", ".3", "1e2", "−0.3", "0.35", "N/A", "-"])
def test_unvetted_rate_notation_in_a_cell_is_rejected(bad):
    page = edit(text("monthly_horizontal"), "2.1        2.2", f"{bad}        2.2")
    with pytest.raises(ZamstatsCpiError):
        parse(page, HORIZ)


@pytest.mark.parametrize("bad", ["+105.66", ".66", "1e2", "105.667", "N/A"])
def test_unvetted_index_notation_in_a_cell_is_rejected(bad):
    with pytest.raises(ZamstatsCpiError):
        parse(edit(text("index_labelled"), "105.66", bad), LABELLED)


def test_index_precision_is_a_layout_observation_not_a_global_invariant():
    t = parse(text("index_prefixed_duplicate"), PREFIXED)
    texts = {o.number.source_value_text for o in t.observations}
    assert {"107.9", "108.34"} <= texts  # one and two decimals in the same vetted layout; exact strings preserved


# ------------------------------------------------------------------ spec validation
def test_invalid_specs_are_rejected():
    for spec in (dataclasses.replace(LABELLED, family="free_form"), dataclasses.replace(LABELLED, first_month=D(2022, 10, 2)),
                 dataclasses.replace(LABELLED, canonical_first=D(2022, 10, 1)),
                 dataclasses.replace(LABELLED, canonical_first=D(2022, 9, 1), canonical_last=D(2022, 10, 1))):
        with pytest.raises(ZamstatsCpiError):
            parse(text("index_labelled"), spec)
