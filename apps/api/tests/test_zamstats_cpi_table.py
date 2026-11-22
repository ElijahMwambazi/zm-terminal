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


H_IDX = r"Period Total Food CPI Non-Food CPI"
H_ANN = r"Year Month Annual CPI Annual Inflation Rate"
DUP_TABLE = "Consumer Index Numbers of Consumer Prices - Food and Non-Food (National)"  # the registry-bound table of the real Dec 2012 bulletin
DUP_KEY = "zam-cpi-2012-12"
LABELLED = TableSpec("Table 1.1", VERTICAL_LABELLED, 23, r"Table 1\.1: Consumer Price Index - Food and Non-Food \(National\)",
                     D(2022, 10, 1), D(2023, 3, 1), IDX, header_regex=H_IDX)
PREFIXED = TableSpec(DUP_TABLE, VERTICAL_LABELLED, 23, r"Consumer Index Numbers of Consumer Prices - Food and Non-Food \(National\)",
                     D(2010, 10, 1), D(2011, 2, 1), IDX, authorized_duplicate_months=frozenset({D(2010, 12, 1)}), header_regex=H_IDX)
ANNUAL = TableSpec("Annual", ANNUAL_ROWS, 31, r"CONSUMER PRICE INDICES \(2009=100\)", D(2022, 11, 1), D(2023, 2, 1), ANN,
                   header_regex=H_ANN, extra_allowed=(r"Note: Inflation rates are computed using unrounded consumer price indices",))
ANNUAL_LAB = TableSpec("Table 1.3", VERTICAL_LABELLED, 25, r"Table 1\.3: Consumer Price Indices and Annual Inflation",
                       D(2022, 10, 1), D(2023, 3, 1), ANN, header_regex=H_ANN)
QUOTED = TableSpec("Month on month", QUOTED_MONTHS, 5, r"Month on month Inflation Rates: Food and Non Food Items",
                   D(2011, 1, 1), D(2012, 1, 1), MON, header_regex=r"Total Food Non-Food",
                   extra_allowed=(r"1000\.0 534\.9 465\.2", r"Weight Period"))
# note: the quoted fixture lists Jan-Mar 2011 and Jan 2012, so the contiguous run is deliberately broken (see test below)
HORIZ = TableSpec("Table 7", HORIZONTAL_13, 12, r"Table 7: Overall Monthly Inflation Rate for Food and Non-Food Items",
                  D(2098, 12, 1), D(2099, 12, 1), MON, base_on_page=False)
WRAPPED = TableSpec("Table 5", HORIZONTAL_13, 9, r"Table 5: Overall Monthly Inflation Rate for Food and Non-Food Items",
                    D(2098, 10, 1), D(2099, 10, 1), MON, base_on_page=False, wrapped_non_food_tail=4)


def parse(page: str, spec: TableSpec, key: str = "k"):
    return parse_table(page, spec, key)


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
    page = edit(text("index_labelled"), " Source: SYNTHETIC fixture (invented values)", "                                     April                    106.77         206.7           306.99\n Source: SYNTHETIC fixture (invented values)")
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
    t = parse(text("index_prefixed_duplicate"), PREFIXED, DUP_KEY)
    assert (t.first_month, t.last_month) == (D(2010, 10, 1), D(2011, 2, 1)) and len(by_series(t)["index_all_items"]) == 5
    assert [d.month for d in t.duplicate_rows] == [D(2010, 12, 1)]  # reported, never silently dropped
    s = by_series(t)
    assert s["index_all_items"][2].month == D(2010, 12, 1) and s["index_all_items"][3].month == D(2011, 1, 1)  # the next month is not shifted
    assert s["index_non_food"][3].number.source_value_text == "214.3"  # a dropped trailing zero is accepted by the index grammar


def test_duplicate_row_requires_an_explicit_authorisation_for_that_month():
    with pytest.raises(ZamstatsCpiError, match="duplicated printed rows"):
        parse(text("index_prefixed_duplicate"), dataclasses.replace(PREFIXED, authorized_duplicate_months=frozenset()), DUP_KEY)
    with pytest.raises(ZamstatsCpiError, match="duplicated printed rows"):
        parse(text("index_prefixed_duplicate"), dataclasses.replace(PREFIXED, authorized_duplicate_months=frozenset({D(2010, 11, 1)})), DUP_KEY)


def test_naive_row_counter_shift_is_prevented_by_explicit_handling():
    page = text("index_prefixed_duplicate")
    t = parse(page, PREFIXED, DUP_KEY)
    months = [o.month for o in t.observations if o.series == "index_food"]
    assert months == [D(2010, 10, 1), D(2010, 11, 1), D(2010, 12, 1), D(2011, 1, 1), D(2011, 2, 1)]
    # a non-identical "duplicate" is a different row, and a repeated month is never accepted as data
    other = edit(page, "                     December                   211.77         212.88               213.99\n             2011",
                 "                     December                   211.99         212.88               213.99\n             2011")
    with pytest.raises(ZamstatsCpiError):
        parse(other, PREFIXED, DUP_KEY)


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
    contiguous = edit(text("monthly_quoted"), "     2012                Jan '12 - Dec '11                     5.1                  5.2              5.3\n", "")
    t = parse(contiguous, dataclasses.replace(QUOTED, last_month=D(2011, 3, 1)))
    assert [o.month for o in t.observations][::3] == [D(2011, 1, 1), D(2011, 2, 1), D(2011, 3, 1)]
    wrong = edit(contiguous, "Mar '11 - Feb '11", "Mar '11 - Jan '11")
    with pytest.raises(ZamstatsCpiError, match="does not compare"):
        parse(wrong, dataclasses.replace(QUOTED, last_month=D(2011, 3, 1)))


def test_truncated_table_is_rejected():
    page = text("index_labelled")
    cut = page[: page.index("                                     February")] + " Source: SYNTHETIC fixture (invented values)\n Note: 2009 = 100\n"
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
    with pytest.raises(ZamstatsCpiError, match="unclassified line"):
        parse(edit(text("monthly_horizontal"), "534.85", "534.86"), HORIZ)


def test_wrapped_tail_variant_reads_the_tail_from_the_line_above():
    t = parse(text("monthly_wrapped_tail"), WRAPPED)
    nf = [o for o in t.observations if o.series == "monthly_inflation_non_food"]
    assert [o.number.source_value_text for o in nf] == ["8.5", "8.6", "8.7", "-3.3", "0.0", "4.4", "1.9", "1.8", "-1.2", "4.3", "4.2", "4.5", "4.6"]
    assert nf[3].number.source_negative and nf[4].number.source_negative is False  # -0.3 vs a plain 0.0
    with pytest.raises(ZamstatsCpiError, match="unclassified line|wrapped non_food row"):
        parse(text("monthly_wrapped_tail"), dataclasses.replace(WRAPPED, wrapped_non_food_tail=3))
    with pytest.raises(ZamstatsCpiError):
        parse(text("monthly_wrapped_tail"), dataclasses.replace(WRAPPED, wrapped_non_food_tail=0))  # not wrapped for this layout


@pytest.mark.parametrize("bad", ["+0.3", ".3", "1e2", "−0.3", "0.35", "N/A", "-"])
def test_unvetted_rate_notation_in_a_cell_is_rejected(bad):
    page = edit(text("monthly_horizontal"), "5.2        5.3", f"{bad}        5.3")
    with pytest.raises(ZamstatsCpiError):
        parse(page, HORIZ)


@pytest.mark.parametrize("bad", ["+105.66", ".66", "1e2", "105.667", "N/A"])
def test_unvetted_index_notation_in_a_cell_is_rejected(bad):
    with pytest.raises(ZamstatsCpiError):
        parse(edit(text("index_labelled"), "105.66", bad), LABELLED)


def test_index_precision_is_a_layout_observation_not_a_global_invariant():
    t = parse(text("index_prefixed_duplicate"), PREFIXED, DUP_KEY)
    texts = {o.number.source_value_text for o in t.observations}
    assert {"214.3", "213.99"} <= texts  # one and two decimals in the same vetted layout; exact strings preserved


# ------------------------------------------------------------------ spec validation
def test_invalid_specs_are_rejected():
    for spec in (dataclasses.replace(LABELLED, family="free_form"), dataclasses.replace(LABELLED, first_month=D(2022, 10, 2)),
                 dataclasses.replace(LABELLED, canonical_first=D(2022, 10, 1)),
                 dataclasses.replace(LABELLED, canonical_first=D(2022, 9, 1), canonical_last=D(2022, 10, 1))):
        with pytest.raises(ZamstatsCpiError):
            parse(text("index_labelled"), spec)


# ------------------------------------------------------------------ ordered column headings are verified, never inferred
IDX_HEADER_LINE = "Period                                                        Total        Food CPI       Non-Food CPI"


def with_header(page: str, new_header: str) -> str:
    return edit(page, IDX_HEADER_LINE, new_header)


@pytest.mark.parametrize("header", [
    "Period   Total   Non-Food CPI   Food CPI",  # Food and Non-Food swapped
    "Period   Total   Non-Food CPI",  # Food heading missing
    "Period   Total   Food CPI   Food CPI   Non-Food CPI",  # duplicated heading
    "Period   Total   Food CPI   Division CPI",  # unknown category heading
    "Period   Total   Food CPI   Non-Food CPI   Other CPI",  # unexpected extra column heading
    "Period   Food CPI   Total   Non-Food CPI",  # heading order changed
    "Period   Total   Food   Non-Food",  # different wording from the pinned real layout
])
def test_changed_column_headings_fail_closed_even_though_the_numeric_columns_would_parse(header):
    with pytest.raises(ZamstatsCpiError, match="unclassified line|ordered column headings"):
        parse(with_header(text("index_labelled"), header), LABELLED)


def test_duplicated_heading_line_is_rejected():
    page = text("index_labelled")
    both = edit(page, IDX_HEADER_LINE, IDX_HEADER_LINE + "\n " + IDX_HEADER_LINE)
    with pytest.raises(ZamstatsCpiError, match="matched 2 lines"):
        parse(both, LABELLED)


def test_heading_whitespace_is_normalised_but_wording_is_not():
    assert parse(with_header(text("index_labelled"), "Period Total Food CPI Non-Food CPI"), LABELLED).last_month == D(2023, 3, 1)
    with pytest.raises(ZamstatsCpiError):
        parse(with_header(text("index_labelled"), "period total food cpi non-food cpi"), LABELLED)


def test_annual_and_quoted_headings_are_verified_too():
    ann = text("annual_rows")
    with pytest.raises(ZamstatsCpiError, match="unclassified line|ordered column headings"):
        parse(edit(ann, "Annual CPI   Annual Inflation Rate", "Annual Inflation Rate   Annual CPI"), ANNUAL)
    quoted = edit(text("monthly_quoted"), "Total               Food            Non-Food", "Total               Non-Food            Food")
    with pytest.raises(ZamstatsCpiError, match="unclassified line|ordered column headings"):
        parse(quoted, dataclasses.replace(QUOTED, last_month=D(2012, 1, 1)))


def test_a_vertical_family_spec_must_pin_its_headings():
    with pytest.raises(ZamstatsCpiError, match="headings are not pinned"):
        parse(text("index_labelled"), dataclasses.replace(LABELLED, header_regex=None))


# ------------------------------------------------------------------ every line of the bounded block is classified
APRIL = "                                     April                    106.77         206.7           306.99\n"
MARCH = "                                     March                    105.66         205.69          305.88\n"


@pytest.mark.parametrize("line", [
    "                                     April                    106.77         206.7           306.99          406.1\n",  # four values
    "                                     April                    106.77         206.7\n",  # partial numeric row
    "                                     April (provisional)\n",  # month-like line that is not a row
    "                                     Smarch                   106.77         206.7           306.99\n",  # unknown month name: text
])
def test_malformed_or_extra_month_like_rows_inside_the_block_are_rejected_not_skipped(line):
    page = edit(text("index_labelled"), " Source: SYNTHETIC fixture (invented values)", line + " Source: SYNTHETIC fixture (invented values)")
    with pytest.raises(ZamstatsCpiError, match="malformed month row|unclassified line"):
        parse(page, LABELLED)


def test_a_duplicate_month_with_the_wrong_width_is_rejected():
    page = edit(text("index_labelled"), MARCH, MARCH + "                                     March                    105.66         205.69\n")
    with pytest.raises(ZamstatsCpiError, match="malformed month row"):
        parse(page, LABELLED)


def test_malformed_rows_in_the_other_families_are_rejected():
    with pytest.raises(ZamstatsCpiError, match="malformed month row"):
        parse(edit(text("annual_rows"), "2023                                     February                 153.44             (0.0)",
                   "2023                                     February                 153.44"), ANNUAL)
    with pytest.raises(ZamstatsCpiError, match="malformed month row"):
        parse(edit(text("monthly_quoted"), "Mar '11 - Feb '11                     4.7                  4.8              4.9",
                   "Mar '11 - Feb '11                     4.7                  4.8"), dataclasses.replace(QUOTED, last_month=D(2012, 1, 1)))


def test_unexpected_text_inside_the_table_body_is_rejected():
    page = edit(text("index_labelled"), MARCH, MARCH + " Footnote: values were revised\n")
    with pytest.raises(ZamstatsCpiError, match="unclassified line"):
        parse(page, LABELLED)
    horizontal = edit(text("monthly_horizontal"), "Food\nSource:", "Food\n see the narrative\nSource:")
    with pytest.raises(ZamstatsCpiError, match="unclassified line"):
        parse(horizontal, HORIZ)
    with pytest.raises(ZamstatsCpiError, match="unclassified line"):  # a numeric stray line
        parse(edit(text("monthly_horizontal"), "Food\nSource:", "Food\n 1.1 2.2\nSource:"), HORIZ)


def test_a_pinned_footer_line_is_accepted_and_an_unpinned_one_is_not():
    page = edit(text("index_labelled"), MARCH, MARCH + " Provisional figures\n")
    assert parse(page, dataclasses.replace(LABELLED, extra_allowed=(r"Provisional figures",))).last_month == D(2023, 3, 1)
    with pytest.raises(ZamstatsCpiError, match="unclassified line"):
        parse(page, LABELLED)


def test_missing_closing_boundary_is_rejected():
    page = edit(text("index_labelled"), " Source: SYNTHETIC fixture (invented values)\n", "")
    with pytest.raises(ZamstatsCpiError, match="closing boundary is missing"):
        parse(page, LABELLED)
    horizontal = edit(text("monthly_horizontal"), "Source: SYNTHETIC fixture (invented values)\n", "")
    with pytest.raises(ZamstatsCpiError, match="closing boundary is missing"):
        parse(horizontal, HORIZ)


def test_duplicated_closing_boundary_is_rejected_unless_pinned():
    page = edit(text("index_labelled"), " Source: SYNTHETIC fixture (invented values)\n",
                " Source: SYNTHETIC fixture (invented values)\n Source: SYNTHETIC fixture (invented values)\n")
    with pytest.raises(ZamstatsCpiError, match="printed 2 times in a row, expected 1"):
        parse(page, LABELLED)
    assert parse(page, dataclasses.replace(LABELLED, boundary_count=2)).last_month == D(2023, 3, 1)


def test_a_different_real_boundary_can_be_pinned_per_table():
    page = edit(text("index_labelled"), " Source: SYNTHETIC fixture (invented values)\n", " End of table\n")
    with pytest.raises(ZamstatsCpiError, match="closing boundary is missing"):
        parse(page, LABELLED)
    assert parse(page, dataclasses.replace(LABELLED, boundary_regex=r"^\s*End of table")).last_month == D(2023, 3, 1)


# ------------------------------------------------------------------ the duplicate-row exception is bound to its context
def test_the_authorised_duplicate_is_bound_to_the_vetted_publication_and_table():
    page = text("index_prefixed_duplicate")
    assert [d.month for d in parse(page, PREFIXED, DUP_KEY).duplicate_rows] == [D(2010, 12, 1)]
    with pytest.raises(ZamstatsCpiError, match="0 authorised for this publication and table"):
        parse(page, PREFIXED, "zam-cpi-2015-12")  # the same spec in another manifest context
    with pytest.raises(ZamstatsCpiError, match="0 authorised for this publication and table"):
        parse(page, dataclasses.replace(PREFIXED, table_id="Another table"), DUP_KEY)  # a similar spec for another table
    with pytest.raises(ZamstatsCpiError, match="duplicated printed rows"):
        parse(page, dataclasses.replace(PREFIXED, authorized_duplicate_months=frozenset()), DUP_KEY)  # no spec permission


def test_the_registry_holds_exactly_the_one_real_exception():
    from app.services.zamstats_cpi_table import AUTHORIZED_DUPLICATE_ROWS
    assert AUTHORIZED_DUPLICATE_ROWS == {(DUP_KEY, DUP_TABLE, D(2010, 12, 1))}


# ------------------------------------------------------------------ header years are ignored only because the month is anchored elsewhere
def test_month_identity_is_anchored_by_the_pinned_window_not_by_printed_header_years():
    page = text("monthly_horizontal")
    t = parse(page, HORIZ)
    assert t.observations[0].month == D(2098, 12, 1) and t.observations[-1].month == D(2099, 12, 1)
    # an independent anchor must fail when it disagrees: the wrong pinned window cannot be rescued by the printed years
    with pytest.raises(ZamstatsCpiError, match="header months"):
        parse(page, dataclasses.replace(HORIZ, first_month=D(2099, 1, 1), last_month=D(2100, 1, 1)))
    with pytest.raises(ZamstatsCpiError, match="twelve or thirteen"):
        parse(page, dataclasses.replace(HORIZ, last_month=D(2099, 6, 1)))
