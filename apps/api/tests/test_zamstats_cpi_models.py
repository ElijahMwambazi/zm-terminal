"""ZamStats CPI Phase 1 foundation: numeric grammar, sign metadata and series model (pure, offline)."""

from decimal import Decimal

import pytest

from app.services.zamstats_cpi_models import (
    CATEGORIES, METRICS, PARENTHESES, PHASE1_SERIES, PLAIN, MINUS, SERIES_NAMES, ZamstatsCpiError, parse_index_number,
    parse_number, parse_rate_number, validate_series,
)


def test_exactly_seven_phase1_series():
    assert SERIES_NAMES == (
        "index_all_items", "index_food", "index_non_food", "monthly_inflation_all_items", "monthly_inflation_food",
        "monthly_inflation_non_food", "annual_inflation_all_items")
    assert len(PHASE1_SERIES) == 7


@pytest.mark.parametrize("category", ["food", "non_food"])
def test_annual_component_series_are_rejected(category):
    with pytest.raises(ZamstatsCpiError):
        validate_series("annual_inflation", category)
    validate_series("annual_inflation", "all_items")


@pytest.mark.parametrize("metric,category", [("division", "food"), ("index", "province"), ("index", "")])
def test_unknown_series_rejected(metric, category):
    with pytest.raises(ZamstatsCpiError):
        validate_series(metric, category)
    assert metric not in METRICS or category not in CATEGORIES


@pytest.mark.parametrize("text,value", [("564.62", "564.62"), ("274.20", "274.20"), ("274.2", "274.2"), ("0.5", "0.5")])
def test_index_grammar_keeps_exact_text_and_is_decimal(text, value):
    n = parse_index_number(text)
    assert n.source_value_text == text and n.value == Decimal(value) and isinstance(n.value, Decimal)
    assert n.source_negative is False and n.source_notation == PLAIN


def test_formatting_only_difference_is_numerically_equal_but_text_different():
    a, b = parse_index_number("274.20"), parse_index_number("274.2")
    assert a.value == b.value and a.source_value_text != b.source_value_text


@pytest.mark.parametrize("text,value,negative,notation", [
    ("0.7", "0.7", False, PLAIN), ("6.3", "6.3", False, PLAIN), ("0.0", "0.0", False, PLAIN),
    ("-0.3", "-0.3", True, MINUS), ("(0.6)", "-0.6", True, PARENTHESES), ("(0.0)", "0.0", True, PARENTHESES),
])
def test_rate_grammar_and_sign_semantics(text, value, negative, notation):
    n = parse_rate_number(text)
    assert n.source_value_text == text and n.value == Decimal(value) and isinstance(n.value, Decimal)
    assert n.source_negative is negative and n.source_notation == notation


def test_parenthesised_zero_keeps_negative_evidence_independent_of_decimal():
    n = parse_rate_number("(0.0)")
    assert n.value == 0 and not n.value.is_signed()  # a plain zero Decimal: SQL NUMERIC has no signed zero either
    assert n.source_negative is True and n.source_notation == PARENTHESES and n.source_value_text == "(0.0)"
    assert parse_rate_number("0.0").source_negative is False  # an ordinary zero stays distinguishable


@pytest.mark.parametrize("text", ["+0.3", ".3", "1e2", "−0.3", "0.35", "7", "1,0.5", " 0.7", "0.7 ", "(−0.3)", "((0.3))", "(0.3", "-(0.3)", "--0.3", "", "N/A", "-", "(.3)"])
def test_rate_grammar_rejects_unvetted_forms(text):
    with pytest.raises(ZamstatsCpiError):
        parse_rate_number(text)


@pytest.mark.parametrize("text", ["+564.62", ".62", "5.6e2", "564.623", "564", "1,564.62", "(564.62)", "-564.62", "−564.62", ""])
def test_index_grammar_rejects_unvetted_forms(text):
    with pytest.raises(ZamstatsCpiError):
        parse_index_number(text)


def test_non_string_input_is_rejected_not_coerced():
    for bad in (None, 6.3, Decimal("6.3"), 6):
        with pytest.raises(ZamstatsCpiError):
            parse_rate_number(bad)  # type: ignore[arg-type]
    with pytest.raises(ZamstatsCpiError):
        parse_number("percent", "6.3")
    assert parse_number("rate", "6.3").value == Decimal("6.3")
    assert parse_number("index", "564.62").value == Decimal("564.62")
