import asyncio
import importlib
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.jobs import backfill_fuel_buildups as job
from app.main import app
from app.schemas.fuel_buildup import BuildUpComponent, FuelBuildUp
from app.services import erb_pdf, fuel_buildup_store, fuel_store
from app.services.erb_fuel import FuelRow
from app.services.erb_pdf import FuelPdfError, ScannedPdfError
from tests.pdfgen import make_pdf

router_module = importlib.import_module("app.api.router")
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
UNKNOWN = object()


def row(*fields) -> str:
    return "  ".join(fields)


# ---------------- altered text-layer fixtures preserving the verified ERB structures ----------------
def pump_lines(month="NOVEMBER 2026", uniform=("31,052.35", "32,446.72", "30,081.51"), extra=(), vat="VAT @0%  0", units=None):
    lines = [f"{month} PUMP PRICE BUILD UP", "UNIFORM PUMP PRICE BUILD-UP", f"{month} PRICE ADJUSTMENT",
             "PETROL  DIESEL  KEROSENE"] + (units or ["ZMW/ M3  ZMW/ M3  ZMW/ M3"]) + [
        row("WHOLESALE PRICE TO OMC", "22,721.03", "25,686.77", "26,595.40"),
        row("Ndola Fuel Terminal Fee", "62.64", "62.64", "62.64"),
        row("Marking Fee", "204.59", "204.59", "204.59"),
        row("Excise Duty", "2,340.00", "750.00", "-"),
        row("Ex Fuel Depot", "25,328.26", "26,704.00", "26,862.63"),
        row("Transportation cost", "650.00", "650.00", "112.50"),
        row("TOTAL (Excl VAT)", "25,978.26", "27,354.00", "26,975.13"),
        row("OMC Margin", "2,679.28", "2,679.28", "1,505.40"),
        row("TOTAL (Excl VAT)", "28,657.54", "30,033.28", "28,480.53"),
        row("Dealer Margin", "2,025.04", "2,025.04", "1,229.01"),
        row("ERB Fees @0.7%", "0.007", "228.78", "238.41", "221.97"),
        row("Strategic Reserves Fund", "150.00", "150.00", "150.00"),
        row("Price to Dealer/Price before VAT", *uniform),
        row(*vat.split("  "), "-", "-", "-"),
        *extra,
        row("Uniform Pump Price", *uniform),
    ]
    return lines


WHOLESALE_BODY = [
    row("Platts Arab Gulf", "US$/barrel", "115.84", "144.71", "143.22"),
    row("Conversion Factor", "8.42", "7.56", "7.88"),
    row(" FOB Price", "US$/MT", "1,049.57", "1,159.61", "1,197.37"),
    row("Ocean Freight", "US$/MT", "-", "-", "-"),
    row("C & F (Dar/Beira)", "1,049.57", "1,159.61", "1,197.37"),
    row("Storage", "US$/MT", "9.48", "9.23", "9.23"),
    row("Insurance", "US$/MT", "-", "-", "-"),
    row("Financing charges", "US$/MT", "-", "-", "-"),
    row("Tipper charges", "US$/MT", "-", "-", "-"),
    row("Local charges", "US$/MT", "-", "-", "-"),
    row("$3.00/M3 which is equivalent to $4.05/MT for petrol &"),
    row("Wharfage", "$3.57/MT for Diesel & $3.75/MT for Kerosene/Jet A-1", "4.05", "3.57", "3.75"),
    row("BPS Premium", "$/MT", "227.83", "220.00", "199.72"),
    row("CIF (Dar/Beira)", "1,300.93", "1,402.41", "1,420.07"),
    row("Transportation Fee (Road/Pipeline)", "US$/MT", "210.00", "159.74", "205.00"),
    row("Transportation Losses", "0.5% petrol & 0.3% diesel, 0.3% kerosene", "7.60", "4.72", "4.91"),
    row("Importers Cost Plus Margin", "US$/MT", "66.51", "-", "70.06"),
    row("CIF (Lusaka/Ndola)", "$/MT", "1,585.04", "1,566.87", "1,700.04"),
    row("Storage losses", "0.5% petrol & 0.3% diesel, 0.3% kerosene", "7.98", "4.73", "5.13"),
    row("Price per Metric Tonnes", "$/MT", "1,593.02", "1,571.60", "1,705.17"),
    row("Conversion factors", "0.74", "0.84", "0.80"),
    row("Price per Cubic Metres", "$/M3", "1,176.23", "1,318.54", "1,362.14"),
    row("Exchange Rate", "K to $", "19.84", "19.84", "19.84"),
    row("Price Before Price Support", "K/M3", "22,721.03", "25,686.77", "26,595.40"),
    row("Price Stabilisation/ (Support)", "K/M3", "(2,117.40)", "-", "(954.07)"),
    row("Wholesale Price to the OMCs", "K/M3", "20,603.63", "25,686.77", "25,641.33"),
]


def wholesale_lines(month="NOVEMBER 2026", body=None):
    return [f"WHOLESALE PRICE BUILD UP FOR {month}", "PETROL  DIESEL  KEROSENE", *(body or WHOLESALE_BODY)]


def jet_lines(month="NOVEMBER 2026", extra=()):
    return [f"{month} WHOLESALE PRICE BUILD UP FOR JET A-1", "JET A-1",
            row("Platts Arab Gulf", "US$/barrel", "143.22"), row("Conversion Factor", "7.88"),
            row("FOB Price", "US$/MT", "1,197.37"), row("Ocean Freight", "US$/MT", "-"),
            row("C & F (Dar/Beira)", "1,197.37"), row("Storage", "US$/MT", "12.00"),
            row("BPS Premium", "$/MT", "199.72"), row("Transportation Fee (Road/Pipeline)", "US$/MT", "205.00"),
            row("Importers Cost Plus Margin", "US$/MT", "42.99"), row("CIF (Lusaka/Ndola)", "$/MT", "1,675.74"),
            row("Price per Metric Tonnes", "$/MT", "1,680.80"), row("Conversion factors", "0.80"),
            row("Price per Cubic Metres", "$/M3", "1,342.64"), row("Exchange Rate", "K to $", "19.84"),
            row("Price Before Price Support", "K/M3", "26,636.02"), row("Price Stabilisation/ (Support)", "K/M3", "-"),
            row("Wholesale Price to the OMCs", "K/M3", "26,636.02"), *extra]


def press_lines(month="OCTOBER 2026", prices=("31.05", "32.45", "30.08", "31.85"), midnight="30th September", layout="new"):
    head = [f"REVISION OF PETROLEUM PUMP PRICES FOR {month}", "The ERB announces new prices for the month under review."]
    currents = ("25.29", "26.86", "27.02", "28.71")
    if layout in ("new", "pct"):  # CURRENT / NEW / VARIANCE [/ % CHANGE], first row wrapped like the real PDF
        def cells(i):
            delta = Decimal(prices[i]) - Decimal(currents[i])
            out = f"{currents[i]}  {prices[i]}  {delta:.2f}"
            return out + (f"  {delta / Decimal(currents[i]) * 100:.2f}" if layout == "pct" else "")
        head_row = "PRODUCT  CURRENT  NEW" + ("  % CHANGE" if layout == "pct" else "")
        table = ["NATIONAL UNIFORM PUMP PRICES K/LITRE", "ABSOLUTE", head_row, "VARIANCE", "PETROL", cells(0),
                 f"DIESEL  {cells(1)}", f"KEROSENE  {cells(2)}", f"JET A-1  {cells(3)}"]
    else:  # single "NEW PUMP PRICE" column
        table = ["PRODUCT  NEW PUMP PRICE (K/Litre)", f"PETROL  {prices[0]}", f"DIESEL (LSG)  {prices[1]}",
                 f"KEROSENE  {prices[2]}", f"JET A-1  {prices[3]}"]
    return head + table + [f"The prices come into effect at midnight on {midnight} 2026 and remain in force.",
                           "Issued on Wednesday, 30 September 2026"]


# ---------------- amounts ----------------
@pytest.mark.parametrize("token,expected", [("24,721.03", "24721.03"), ("20.84", "20.84"), ("-", None),
                                             ("(2,117.40)", "-2117.40"), ("150.00", "150.00"), ("0", "0")])
def test_parse_amount_exact_decimal(token, expected):
    got = erb_pdf.parse_amount(token)
    assert (None if got is None else str(got)) == expected


@pytest.mark.parametrize("bad", ["", "12,34.5", "1e3", "abc", "(12", "1,23", "--", "12.", ".5"])
def test_parse_amount_rejects_malformed(bad):
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_amount(bad)


# ---------------- pump build-up ----------------
def items_of(parsed, product, code):
    return [i for i in parsed.items if i.product == product and i.component_code == code]


def test_pump_buildup_components_products_and_exact_decimals():
    parsed = erb_pdf.parse_pump_buildup(pump_lines())
    assert parsed.kind == "pump" and parsed.month == date(2026, 11, 1)
    codes = [i.component_code for i in parsed.items if i.product == "PETROL"]
    assert codes == ["WHOLESALE_PRICE_TO_OMC", "NDOLA_FUEL_TERMINAL_FEE", "MARKING_FEE", "EXCISE_DUTY", "EX_FUEL_DEPOT",
                     "TRANSPORTATION_COST", "TOTAL_EXCL_VAT_BEFORE_OMC_MARGIN", "OMC_MARGIN", "TOTAL_EXCL_VAT_AFTER_OMC_MARGIN",
                     "DEALER_MARGIN", "ERB_FEE", "STRATEGIC_RESERVES_FUND", "PRICE_TO_DEALER_BEFORE_VAT", "VAT", "UNIFORM_PUMP_PRICE"]
    assert [i.position for i in parsed.items if i.product == "PETROL"] == list(range(1, 16))  # ERB order preserved
    assert items_of(parsed, "PETROL", "WHOLESALE_PRICE_TO_OMC")[0].amount == Decimal("22721.03")  # thousands separator
    assert str(items_of(parsed, "DIESEL", "UNIFORM_PUMP_PRICE")[0].amount) == "32446.72"
    assert items_of(parsed, "KEROSENE", "EXCISE_DUTY")[0].amount is None  # "-" is not published, never zero
    first, second = (items_of(parsed, "PETROL", c)[0] for c in ("TOTAL_EXCL_VAT_BEFORE_OMC_MARGIN", "TOTAL_EXCL_VAT_AFTER_OMC_MARGIN"))
    assert (first.component_label, first.amount) == ("TOTAL (Excl VAT)", Decimal("25978.26"))
    assert second.component_label == "TOTAL (Excl VAT)" and second.amount == Decimal("28657.54")  # two rows, one label, two codes
    fee = items_of(parsed, "PETROL", "ERB_FEE")[0]
    assert fee.component_label == "ERB Fees @0.7%" and fee.note == "rate 0.007" and fee.amount == Decimal("228.78")
    assert all(i.unit == "ZMW/M3" and i.currency == "ZMW" for i in parsed.items)
    assert items_of(parsed, "PETROL", "VAT")[0].component_label == "VAT @0%"


@pytest.mark.parametrize("units", [["ZMW/  ZMW/  ZMW/", "M3  M3  M3"], ["M3  M3  M3", "ZMW/  ZMW/  ZMW/"], ["3  3  3", "ZMW/ M  ZMW/ M  ZMW/ M"]])
def test_pump_unit_line_variants_from_the_text_layer(units):
    assert erb_pdf.parse_pump_buildup(pump_lines(units=units)).items[0].unit == "ZMW/M3"


@pytest.mark.parametrize("vat", ["VAT  0", "VAT @16%  16", "VAT @ 0%  0"])
def test_pump_vat_label_variants_keep_the_source_label(vat):
    parsed = erb_pdf.parse_pump_buildup(pump_lines(vat=vat))
    assert items_of(parsed, "PETROL", "VAT")[0].component_label == vat.split("  ")[0]


def test_pump_structural_failures():
    with pytest.raises(FuelPdfError):  # duplicate component
        erb_pdf.parse_pump_buildup(pump_lines(extra=[row("Marking Fee", "1.00", "1.00", "1.00")]))
    with pytest.raises(FuelPdfError):  # unknown extra component is deliberately rejected, not stored
        erb_pdf.parse_pump_buildup(pump_lines(extra=[row("Road Levy", "1.00", "1.00", "1.00")]))
    changed = pump_lines()
    changed[3] = "PETROL  KEROSENE  DIESEL"
    with pytest.raises(FuelPdfError):  # changed product heading
        erb_pdf.parse_pump_buildup(changed)
    with pytest.raises(FuelPdfError):  # wrong unit
        erb_pdf.parse_pump_buildup(pump_lines(units=["ZMW/ L  ZMW/ L  ZMW/ L"]))
    with pytest.raises(FuelPdfError):  # a malformed amount
        erb_pdf.parse_pump_buildup(pump_lines(extra=[row("Marking Fee 2", "1,2.0", "1.00", "1.00")]))
    missing = [l for l in pump_lines() if not l.startswith("Uniform Pump Price")]
    with pytest.raises(FuelPdfError):  # required published total absent
        erb_pdf.parse_pump_buildup(missing)
    with pytest.raises(FuelPdfError):  # month not identified
        erb_pdf.parse_pump_buildup([l for l in pump_lines() if "NOVEMBER" not in l] + [])


def test_pump_total_row_in_an_unexpected_position_fails():
    lines = pump_lines()
    index = lines.index(row("Dealer Margin", "2,025.04", "2,025.04", "1,229.01"))
    lines.insert(index, row("TOTAL (Excl VAT)", "1.00", "1.00", "1.00"))  # after the second TOTAL, before Dealer Margin
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_pump_buildup(lines)


# ---------------- wholesale and Jet A-1 ----------------
def test_wholesale_mixed_units_support_and_exact_decimals():
    parsed = erb_pdf.parse_wholesale_buildup(wholesale_lines())
    assert parsed.kind == "wholesale" and parsed.month == date(2026, 11, 1)
    assert {i.product for i in parsed.items} == {"PETROL", "DIESEL", "KEROSENE"}
    platts = items_of(parsed, "DIESEL", "PLATTS_ARAB_GULF")[0]
    assert (platts.unit, platts.currency, platts.amount) == ("US$/barrel", "USD", Decimal("144.71"))
    fx = items_of(parsed, "PETROL", "EXCHANGE_RATE")[0]
    assert (fx.unit, fx.currency, str(fx.amount)) == ("K to $", "ZMW", "19.84")
    assert items_of(parsed, "PETROL", "PRICE_PER_CUBIC_METRE")[0].unit == "$/M3"
    final = items_of(parsed, "KEROSENE", "WHOLESALE_PRICE_TO_OMC")[0]
    assert (final.unit, final.amount) == ("K/M3", Decimal("25641.33"))
    support = items_of(parsed, "DIESEL", "PRICE_STABILISATION_SUPPORT")[0]
    assert support.amount is None  # "-": not published
    assert items_of(parsed, "PETROL", "PRICE_STABILISATION_SUPPORT")[0].amount == Decimal("-2117.40")  # "(2,117.40)"
    assert items_of(parsed, "DIESEL", "IMPORTERS_COST_PLUS_MARGIN")[0].amount is None
    assert {i.unit for i in parsed.items} >= {"US$/barrel", "US$/MT", "$/MT", "$/M3", "K/M3", "K to $", None}  # units are never merged
    assert items_of(parsed, "PETROL", "CONVERSION_FACTOR")[0].unit is None and items_of(parsed, "PETROL", "CONVERSION_FACTORS")[0].amount == Decimal("0.74")


def test_wholesale_notes_are_kept_apart_from_units():
    parsed = erb_pdf.parse_wholesale_buildup(wholesale_lines())
    wharfage = items_of(parsed, "DIESEL", "WHARFAGE")[0]
    assert wharfage.unit is None and "$3.00/M3 which is equivalent" in wharfage.note and "$3.57/MT for Diesel" in wharfage.note
    losses = items_of(parsed, "PETROL", "TRANSPORTATION_LOSSES")[0]
    assert losses.unit is None and losses.note == "0.5% petrol & 0.3% diesel, 0.3% kerosene"


def test_wholesale_label_variants_map_to_the_same_row_and_unknowns_fail():
    body = [l.replace("Wholesale Price to the OMCs", "Wholesale Price to OMC").replace("Price Before Price Support", "Price Before Stabilisation") for l in WHOLESALE_BODY]
    assert items_of(erb_pdf.parse_wholesale_buildup(wholesale_lines(body=body)), "PETROL", "WHOLESALE_PRICE_TO_OMC")
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=WHOLESALE_BODY + [row("Carbon Levy", "US$/MT", "1.00", "1.00", "1.00")]))
    with pytest.raises(FuelPdfError):  # duplicate row (same code and unit)
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=WHOLESALE_BODY + [row("Exchange Rate", "K to $", "19.84", "19.84", "19.84")]))
    with pytest.raises(FuelPdfError):  # unknown unit text
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=[l.replace("US$/barrel", "bushels") for l in WHOLESALE_BODY]))
    with pytest.raises(FuelPdfError):  # the final price is required
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=WHOLESALE_BODY[:-1]))
    bad_heading = wholesale_lines()
    bad_heading[1] = "PETROL  DIESEL"
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_wholesale_buildup(bad_heading)


def _swap(old_prefix, new_line):
    return [new_line if l.startswith(old_prefix) else l for l in WHOLESALE_BODY]


def test_malformed_exchange_rate_row_rejects_the_document():  # the Codex P2 example
    body = _swap("Exchange Rate", row("Exchange Rate", "K to $", "19.84", "19.84", "N/A"))
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=body))


@pytest.mark.parametrize("prefix,bad", [
    ("Exchange Rate", row("Exchange Rate", "K to $", "19.84", "19.84")),  # a missing amount
    ("BPS Premium", row("BPS Premium", "$/MT", "149.70", "185.00")),  # merged cells, as in the real Jan/Feb 2026 files
    ("Storage ", row("Storage", "US$/MT", "9.48", "9.23", "9,2x.00")),
])
def test_component_row_with_a_missing_or_unreadable_amount_rejects_the_document(prefix, bad):
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=_swap(prefix, bad)))


def test_known_continuation_notes_are_still_accepted():
    parsed = erb_pdf.parse_wholesale_buildup(wholesale_lines())
    assert "$3.00/M3 which is equivalent" in items_of(parsed, "PETROL", "WHARFAGE")[0].note
    wrapped = [l for l in WHOLESALE_BODY]
    i = next(n for n, l in enumerate(wrapped) if l.startswith("Transportation Losses"))
    wrapped[i] = row("Transportation Losses", "7.60", "4.72", "4.91")
    wrapped.insert(i + 1, "0.5% petrol & 0.3% diesel, 0.3% kerosene")  # the note printed after its row
    assert erb_pdf.parse_wholesale_buildup(wholesale_lines(body=wrapped))


@pytest.mark.parametrize("line", ["Exchange Rate  K to $  19.84  19.84  N/A", "see note 3 for 2026 pricing",
                                  "$3.00/M3 which is equivalent to 12 apples", "0.5%", "Carbon levy 1.00", "7"])
def test_arbitrary_lines_with_digits_are_not_notes(line):
    body = list(WHOLESALE_BODY)
    body.insert(body.index(next(l for l in body if l.startswith("Wharfage"))), line)
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=body))


def test_a_recognised_note_beside_the_wrong_row_is_rejected():
    body = list(WHOLESALE_BODY)
    body.insert(len(body) - 1, "$3.00/M3 which is equivalent to $4.05/MT for petrol &")
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_wholesale_buildup(wholesale_lines(body=body))


def test_jet_a1_is_a_separate_wholesale_publication():
    parsed = erb_pdf.parse_jet_buildup(jet_lines())
    assert parsed.kind == "jet" and parsed.month == date(2026, 11, 1) and {i.product for i in parsed.items} == {"JET_A1"}
    assert items_of(parsed, "JET_A1", "PLATTS_ARAB_GULF")[0].unit == "US$/barrel"
    final = items_of(parsed, "JET_A1", "WHOLESALE_PRICE_TO_OMC")[0]
    assert (final.unit, final.currency, final.amount) == ("K/M3", "ZMW", Decimal("26636.02"))
    assert items_of(parsed, "JET_A1", "OCEAN_FREIGHT")[0].amount is None
    with pytest.raises(FuelPdfError):  # a three-product heading is not a Jet A-1 table
        erb_pdf.parse_jet_buildup(wholesale_lines())


# ---------------- press statement ----------------
@pytest.mark.parametrize("layout,midnight,month", [("new", "30th September", "OCTOBER 2026"), ("pct", "30th September", "OCTOBER 2026"), ("old", "30th September", "OCTOBER 2026")])
def test_press_statement_prices_and_month(layout, midnight, month):
    parsed = erb_pdf.parse_press_statement(press_lines(layout=layout, midnight=midnight, month=month))
    assert parsed.month == date(2026, 10, 1) and parsed.effective_midnight == date(2026, 9, 30)
    assert parsed.prices == {"PETROL": Decimal("31.05"), "DIESEL": Decimal("32.45"), "KEROSENE": Decimal("30.08"), "JET_A1": Decimal("31.85")}
    assert str(parsed.prices["PETROL"]) == "31.05"  # exactly as printed
    assert "LSGO" not in parsed.prices  # never invented


def test_press_statement_missing_product_and_malformed_table_fail_or_omit():
    lines = press_lines()
    no_jet = [l for l in lines if not l.startswith("JET A-1")]
    assert "JET_A1" not in erb_pdf.parse_press_statement(no_jet).prices
    with pytest.raises(FuelPdfError):  # malformed price
        erb_pdf.parse_press_statement(press_lines(prices=("31.5", "32.45", "30.08", "31.85")))
    with pytest.raises(FuelPdfError):  # effective date does not fit the pricing month
        erb_pdf.parse_press_statement(press_lines(midnight="15th September"))
    with pytest.raises(FuelPdfError):  # no table
        erb_pdf.parse_press_statement([l for l in lines if not l.startswith(("PRODUCT", "PETROL", "DIESEL", "KEROSENE", "JET"))])
    with pytest.raises(FuelPdfError):  # duplicate product
        erb_pdf.parse_press_statement(lines[:-2] + ["PETROL  1.00  2.00  3.00"] + lines[-2:])


def _set_row(lines, start, replacement):
    return [replacement if l.startswith(start) else l for l in lines]


def _never_persisted(lines):
    """A rejected press statement yields no ParsedPress, so there is nothing a job could persist."""
    with pytest.raises(FuelPdfError):
        erb_pdf.parse_press_statement(lines)


def test_press_complete_current_new_variance_row_accepted_and_new_is_the_price():
    parsed = erb_pdf.parse_press_statement(press_lines())
    assert parsed.prices["JET_A1"] == Decimal("31.85")  # NEW, never CURRENT (28.71) or VARIANCE (3.14)


@pytest.mark.parametrize("row", [
    "JET A-1  31.85  3.14",            # Codex's case: CURRENT missing, VARIANCE read as NEW
    "JET A-1  28.71  31.85",           # VARIANCE missing
    "JET A-1  28.71  3.14",            # NEW missing (CURRENT + a variance-looking value)
    "JET A-1  28.71  31.85  3.14  9.99",  # extra unexpected numeric cell
    "JET A-1  28.71  31.85  3.14  -",  # extra cell, even a dash
    "JET A-1  28.71  31.85  4.00",     # variance that is not NEW - CURRENT
    "JET A-1  28.71  31.8  3.14",      # NEW is not a two-decimal price
    "JET A-1  28.71  0.00  -28.71",    # non-positive NEW
])
def test_press_incomplete_or_inconsistent_jet_row_rejects_whole_statement(row):
    _never_persisted(_set_row(press_lines(), "JET A-1", row))


@pytest.mark.parametrize("product,row", [("PETROL", "25.29  6.17"), ("DIESEL", "DIESEL  26.86  6.41")])
def test_press_incomplete_row_for_any_product_rejects(product, row):
    lines = press_lines()
    if product == "PETROL":  # the wrapped row: label on its own line, then the values
        lines = _set_row(lines, "25.29", row)
    else:
        lines = _set_row(lines, "DIESEL", row)
    _never_persisted(lines)


def test_press_pct_layout_requires_four_values_and_a_matching_percentage():
    assert erb_pdf.parse_press_statement(press_lines(layout="pct")).prices["JET_A1"] == Decimal("31.85")
    lines = press_lines(layout="pct")
    _never_persisted(_set_row(lines, "JET A-1", "JET A-1  28.71  31.85  3.14"))            # percentage missing
    _never_persisted(_set_row(lines, "JET A-1", "JET A-1  28.71  31.85  3.14  99.00"))      # percentage inconsistent
    _never_persisted(_set_row(lines, "JET A-1", "JET A-1  28.71  31.85  3.14  10.94  1.00"))  # extra cell


def test_press_unchanged_price_prints_dashes_only_when_it_is_unchanged():
    lines = press_lines(layout="pct")
    ok = _set_row(lines, "JET A-1", "JET A-1  31.85  31.85  -  -")
    assert erb_pdf.parse_press_statement(ok).prices["JET_A1"] == Decimal("31.85")
    _never_persisted(_set_row(lines, "JET A-1", "JET A-1  28.71  31.85  -  -"))   # changed but no variance


def test_press_variance_tolerates_erbs_own_one_cent_rounding_only():
    ok = _set_row(press_lines(), "KEROSENE", "KEROSENE  27.02  30.08  3.07")   # 30.08 - 27.02 = 3.06
    assert erb_pdf.parse_press_statement(ok).prices["KEROSENE"] == Decimal("30.08")
    _never_persisted(_set_row(press_lines(), "KEROSENE", "KEROSENE  27.02  30.08  3.08"))


def test_press_single_column_layout_exact_count():
    assert erb_pdf.parse_press_statement(press_lines(layout="old")).prices["JET_A1"] == Decimal("31.85")
    _never_persisted(_set_row(press_lines(layout="old"), "JET A-1", "JET A-1  31.85  3.14"))  # extra value
    _never_persisted(_set_row(press_lines(layout="old"), "JET A-1", "JET A-1"))             # no value


def test_press_unrecognised_heading_is_rejected():
    lines = [l.replace("PRODUCT  CURRENT  NEW", "PRODUCT  OLD  NEW") for l in press_lines()]
    _never_persisted(lines)
    _never_persisted([l.replace("VARIANCE", "") for l in press_lines()])  # CURRENT/NEW without a VARIANCE column


def test_press_row_order_does_not_change_validation():
    lines = press_lines()
    base = erb_pdf.parse_press_statement(lines).prices
    rest = [l for l in lines if not l.startswith("JET A-1")]
    at = rest.index("PETROL")
    good_first = rest[:at] + ["JET A-1  28.71  31.85  3.14"] + rest[at:]
    assert erb_pdf.parse_press_statement(good_first).prices == base
    _never_persisted(rest[:at] + ["JET A-1  31.85  3.14"] + rest[at:])  # same malformed row, now first
    _never_persisted(rest[:-2] + ["JET A-1  31.85  3.14"] + rest[-2:])  # ... and last


# ---------------- reconciliation (checks only, never replaces a published value) ----------------
def test_reconciliation_rounding_relationship_and_inconsistency():
    pump = erb_pdf.parse_pump_buildup(pump_lines(month="OCTOBER 2026"))
    press = erb_pdf.parse_press_statement(press_lines())  # 31.05 vs 31,052.35 ZMW/M3 = 31.05235 ZMW/L
    before = (dict(press.prices), list(pump.items))
    erb_pdf.reconcile(press, pump)  # within the documented rounding (< 0.01 ZMW/litre)
    assert (press.prices, pump.items) == before  # nothing recomputed or replaced
    bad = erb_pdf.parse_press_statement(press_lines(prices=("31.45", "32.45", "30.08", "31.85")))
    with pytest.raises(FuelPdfError, match="disagrees"):
        erb_pdf.reconcile(bad, pump)
    with pytest.raises(FuelPdfError, match="different months"):
        erb_pdf.reconcile(erb_pdf.parse_press_statement(press_lines()), erb_pdf.parse_pump_buildup(pump_lines()))
    # Jet A-1 has no pump build-up: nothing to compare, nothing derived
    assert "JET_A1" in press.prices and not items_of(pump, "JET_A1", "UNIFORM_PUMP_PRICE")


# ---------------- PDF text layer (real extraction on generated PDFs) ----------------
def test_pdf_lines_round_trip_through_the_real_extractor():
    lines = erb_pdf.pdf_lines(make_pdf(pump_lines()))
    parsed = erb_pdf.parse_pump_buildup(lines)
    assert items_of(parsed, "KEROSENE", "UNIFORM_PUMP_PRICE")[0].amount == Decimal("30081.51")
    parsed = erb_pdf.parse_wholesale_buildup(erb_pdf.pdf_lines(make_pdf(wholesale_lines())))
    assert items_of(parsed, "PETROL", "PRICE_STABILISATION_SUPPORT")[0].amount == Decimal("-2117.40")
    assert erb_pdf.parse_press_statement(erb_pdf.pdf_lines(make_pdf(press_lines()))).prices["PETROL"] == Decimal("31.05")


def test_scanned_and_corrupt_pdfs_are_reported_not_read():
    with pytest.raises(ScannedPdfError):
        erb_pdf.pdf_lines(make_pdf([], text=False))
    with pytest.raises(FuelPdfError):
        erb_pdf.pdf_lines(b"not a pdf")
    with pytest.raises(FuelPdfError):
        erb_pdf.pdf_lines(b"%PDF-1.4 truncated")


# ---------------- discovery ----------------
B = "https://www.erb.org.zm/wp-content/uploads"


@pytest.mark.parametrize("url,kind,month", [
    (f"{B}/PressStatements/CurrentFuelPumpPricesNovember2026.pdf", "press", date(2026, 11, 1)),
    (f"{B}/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf", "pump", date(2026, 11, 1)),
    (f"{B}/files/FuelPumpPrices/WHOLESALE-PRICE-BUILD-UP-FOR-NOVEMBER-2026.pdf", "wholesale", date(2026, 11, 1)),
    (f"{B}/files/FuelPumpPrices/NOVEMBER-2026-WHOLESALE-PRICE-BUILD-UP-FOR-JET-A-1.pdf", "jet", date(2026, 11, 1)),
    (f"{B}/files/FuelPumpPrices/WHOLESALE-PRICE-BUILD-UP-FOR-JULY-2026-_PETROL-DIESEL-KEROSENE.pdf", "wholesale", date(2026, 7, 1)),
    (f"{B}/files/FuelPumpPrices/JANUARY-2026-WHOLESALE-PRICE-BUILD-UP-FOR-JET-A-1.pdf", "jet", date(2026, 1, 1)),
])
def test_classification_by_official_filename(url, kind, month):
    doc = erb_pdf.classify(url)
    assert (doc.kind, doc.month, doc.url) == (kind, month, url)


@pytest.mark.parametrize("url", [
    "https://evil.example/wp-content/uploads/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf",
    "http://www.erb.org.zm/wp-content/uploads/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf",
    f"{B}/other/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf", f"{B}/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.docx",
    f"{B}/PressStatements/CurrentFuelPumpPrices-1-1-1.pdf",  # no month: cannot be classified
    f"{B}/PressStatements/2023-04-30_PressStatement_Petroleum_Pump_Prices.pdf",
])
def test_non_erb_or_unclassifiable_urls_are_ignored(url):
    assert erb_pdf.classify(url) is None


def media_client(listing_pages, pdfs=None):
    def handler(request):
        url = str(request.url)
        if "wp-json" in url:
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, json=listing_pages[page - 1] if page <= len(listing_pages) else [])
        body, ctype = (pdfs or {}).get(url, (b"", "application/pdf"))
        return httpx.Response(200, content=body, headers={"content-type": ctype})
    return httpx.MockTransport(handler)


def run(coro_fn, transport):
    async def go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await coro_fn(c)
    return asyncio.run(go())


def test_discovery_groups_by_month_and_flags_ambiguity():
    listing = [[{"source_url": f"{B}/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf"},
                {"source_url": f"{B}/files/FuelPumpPrices/OCTOBER-2026-PUMP-PRICE-BUILD-UP.pdf"},
                {"source_url": f"{B}/PressStatements/CurrentFuelPumpPricesOctober2026.pdf"},
                {"source_url": f"{B}/PressStatements/CurrentFuelPumpPricesOctober2026-1.pdf"},  # second file, same month and kind
                {"source_url": "https://evil.example/x.pdf"}, {"source_url": f"{B}/PressStatements/CurrentFuelPumpPrices-1-1.pdf"}]]
    found = run(erb_pdf.discover_publications, media_client(listing))
    assert set(found.docs) == {(date(2026, 11, 1), "pump"), (date(2026, 10, 1), "pump")}
    assert found.ambiguous == {(date(2026, 10, 1), "press")} and found.unclassified == 2


@pytest.mark.parametrize("url,body,ctype", [
    ("https://evil.example/a.pdf", b"%PDF", "application/pdf"),
    (f"{B}/files/FuelPumpPrices/X-NOVEMBER-2026.html", b"x", "text/html"),
    (f"{B}/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf", b"<html>", "text/html"),
    (f"{B}/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf", b"x" * (erb_pdf.MAX_PDF_BYTES + 1), "application/pdf"),
])
def test_download_rejects_foreign_hosts_wrong_mime_and_oversize(url, body, ctype):
    with pytest.raises(FuelPdfError):
        run(lambda c: erb_pdf.download_pdf(c, url), media_client([[]], {url: (body, ctype)}))


# ---------------- manual job (offline pipeline over generated PDFs) ----------------
def listing_for(month_names_to_docs):
    return [[{"source_url": url} for url in month_names_to_docs]]


def total_items():
    return sum(len(b.items) for b, _ in parsed_set())


def job_world(*, press_prices=("31.05", "32.45", "30.08", "31.85"), scanned_press=False, with_pump=True, press_override=None):
    urls = {
        "press": f"{B}/PressStatements/CurrentFuelPumpPricesOctober2026.pdf",
        "pump": f"{B}/files/FuelPumpPrices/OCTOBER-2026-PUMP-PRICE-BUILD-UP.pdf",
        "wholesale": f"{B}/files/FuelPumpPrices/WHOLESALE-PRICE-BUILD-UP-FOR-OCTOBER-2026.pdf",
        "jet": f"{B}/files/FuelPumpPrices/OCTOBER-2026-WHOLESALE-PRICE-BUILD-UP-FOR-JET-A-1.pdf",
    }
    press_pdf = make_pdf([], text=False) if scanned_press else make_pdf(press_override or press_lines(prices=press_prices))
    pdfs = {urls["press"]: (press_pdf, "application/pdf"),
            urls["pump"]: (make_pdf(pump_lines(month="OCTOBER 2026")), "application/pdf"),
            urls["wholesale"]: (make_pdf(wholesale_lines(month="OCTOBER 2026")), "application/pdf"),
            urls["jet"]: (make_pdf(jet_lines(month="OCTOBER 2026")), "application/pdf")}
    listed = list(urls.values()) if with_pump else [urls["press"], urls["wholesale"], urls["jet"]]
    return media_client(listing_for(listed), pdfs)


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("database touched")
    monkeypatch.setattr(fuel_store, "get_engine", boom)
    monkeypatch.setattr(fuel_buildup_store, "get_engine", boom)


def run_job(transport, start="2026-10", end="2026-10", **kw):
    async def go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await job.run_backfill(job.parse_month(start), job.parse_month(end), client=c, now=NOW, pause=0, **kw)
    return asyncio.run(go())


def test_job_dry_run_counts_and_never_touches_the_database():
    r = run_job(job_world(), dry_run=True)
    assert r["documents_found"] == 4 and r["documents_missing"] == 0 and r["retail_rows"] == 4
    assert r["build_up_items"] == total_items() and r["retail_inserted"] == 0 and r["reconciliation_failures"] == 0
    assert r["scanned_documents"] == 0 and r["unsupported_documents"] == 0


def test_job_reports_missing_scanned_and_reconciliation_failures():
    assert run_job(job_world(with_pump=False), dry_run=True)["documents_missing"] == 1
    def refuse(data):
        raise FuelPdfError("rejected")
    scanned = run_job(job_world(scanned_press=True), dry_run=True, ocr=refuse)
    assert scanned["scanned_documents"] == 1 and scanned["ocr_rejected"] == 1 and scanned["retail_rows"] == 0 and scanned["build_up_items"] == total_items()  # build-ups still imported
    bad = run_job(job_world(press_prices=("33.45", "32.45", "30.08", "31.85")), dry_run=True)
    assert bad["reconciliation_failures"] == 1 and bad["retail_rows"] == 0 and bad["build_up_items"] == 0  # the whole month is skipped


def test_job_malformed_text_press_row_yields_no_retail_row_and_never_ocr():
    """Codex's Jet A-1 case through the job: the statement is unsupported, no retail row exists, and OCR is not tried."""
    def no_ocr(data):
        raise AssertionError("a text PDF must never be OCR'd")
    bad = _set_row(press_lines(), "JET A-1", "JET A-1  31.85  3.14")
    r = run_job(job_world(press_override=bad), dry_run=True, ocr=no_ocr)
    assert r["retail_rows"] == 0 and r["unsupported_documents"] >= 1 and r["ocr_parsed"] == 0 and r["text_press_parsed"] == 0


def test_job_rejects_future_inverted_and_oversized_ranges():
    for start, end, message in (("2026-10", "2026-12", "future"), ("2026-10", "2026-09", "after"), ("2024-01", "2026-10", "months")):
        with pytest.raises(ValueError, match=message):
            run_job(media_client([[]]), start, end, dry_run=True)
    with pytest.raises(ValueError):
        job.parse_month("2026-1")


def test_job_cli_prints_counts_only(monkeypatch, capsys):
    async def fake(start, end, dry_run=False, **kw):
        return {"retail_rows": 4}
    monkeypatch.setattr(job, "run_backfill", fake)
    assert job.main(["--from", "2026-10", "--to", "2026-10", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "dry-run retail_rows=4"
    async def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 31,052.35")
    monkeypatch.setattr(job, "run_backfill", boom)
    assert job.main(["--from", "2026-10"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err and "31,052" not in err


# ---------------- API (offline) ----------------
def build_up(product="PETROL", kind="pump"):
    return FuelBuildUp(product=product, effective_date=date(2026, 11, 1), publication_month="2026-11", build_up_type=kind,
                       source_url=f"{B}/files/FuelPumpPrices/NOVEMBER-2026-PUMP-PRICE-BUILD-UP.pdf", retrieved_at=NOW,
                       components=[BuildUpComponent(code="MARKING_FEE", label="Marking Fee", amount=Decimal("204.59"), currency="ZMW", unit="ZMW/M3"),
                                   BuildUpComponent(code="EXCISE_DUTY", label="Excise Duty", amount=None, currency="ZMW", unit="ZMW/M3")])


def test_build_up_api_query_validation_404_and_503(monkeypatch):
    c = TestClient(app)
    seen = {}
    def fake(product, kind, month):
        seen.update(product=product, kind=kind, month=month)
        return build_up(product, kind)
    monkeypatch.setattr(router_module, "fetch_build_up", fake)
    body = c.get("/api/v1/fuel/build-up?product=PETROL&type=pump&month=2026-11").json()
    assert seen == {"product": "PETROL", "kind": "pump", "month": "2026-11"}
    assert body["publication_month"] == "2026-11" and body["effective_date"] == "2026-11-01" and body["source_url"].endswith(".pdf")
    assert body["components"][0] == {"code": "MARKING_FEE", "label": "Marking Fee", "amount": "204.59", "currency": "ZMW", "unit": "ZMW/M3", "note": None}
    assert body["components"][1]["amount"] is None
    c.get("/api/v1/fuel/build-up?product=DIESEL&type=wholesale")
    assert seen == {"product": "DIESEL", "kind": "wholesale", "month": None}
    assert c.get("/api/v1/fuel/build-up?product=PETROL").status_code == 200  # type defaults to pump
    for bad in ("product=LPG", "product=PETROL&type=retail", "product=PETROL&month=2026-13", "product=PETROL&month=2026-1", "type=pump", "product=petrol"):
        assert c.get(f"/api/v1/fuel/build-up?{bad}").status_code == 422
    monkeypatch.setattr(router_module, "fetch_build_up", lambda *a: None)
    assert c.get("/api/v1/fuel/build-up?product=LSGO").status_code == 404
    def down(*a):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "fetch_build_up", down)
    r = c.get("/api/v1/fuel/build-up?product=PETROL")
    assert r.status_code == 503 and "SECRET" not in r.text


def test_build_up_route_does_not_shadow_product_route_and_never_calls_erb(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("ERB called from an API request")
    monkeypatch.setattr(erb_pdf, "discover_publications", boom)
    monkeypatch.setattr(erb_pdf, "download_pdf", boom)
    monkeypatch.setattr(router_module, "fetch_build_up", lambda *a: build_up())
    monkeypatch.setattr(router_module, "fetch_latest_fuel", lambda: [])
    c = TestClient(app)
    assert c.get("/api/v1/fuel/build-up?product=PETROL").status_code == 200
    assert c.get("/api/v1/fuel/PETROL").status_code == 404  # still the product route


# ---------------- real PostgreSQL (opt-in) ----------------
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    eng = create_engine(url)
    init = Path(__file__).parents[3] / "db/init"
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS zambia_fuel_prices"))
        c.execute(text("DROP TABLE IF EXISTS zambia_fuel_build_up_items"))
        c.execute(text((init / "006_zambia_fuel_prices.sql").read_text()))
        c.execute(text((init / "007_zambia_fuel_build_up_items.sql").read_text()))
    yield eng
    eng.dispose()


def parsed_set():
    return [(erb_pdf.parse_pump_buildup(pump_lines(month="OCTOBER 2026")), "u1"), (erb_pdf.parse_wholesale_buildup(wholesale_lines(month="OCTOBER 2026")), "u2"),
            (erb_pdf.parse_jet_buildup(jet_lines(month="OCTOBER 2026")), "u3")]


def retail_rows(petrol="31.05"):
    return [(FuelRow(date(2026, 10, 1), "PETROL", "retail", Decimal(petrol), "litre"), "p1")]


def test_pg_insert_idempotent_null_equality_no_raw_and_fetch(engine):
    first = fuel_buildup_store.save_fuel_publications(retail_rows(), parsed_set(), retrieved_at=NOW, engine=engine)
    assert first["retail_inserted"] == 1 and first["items_inserted"] == total_items()
    again = fuel_buildup_store.save_fuel_publications(retail_rows("31.050"), parsed_set(), retrieved_at=NOW, engine=engine)
    assert again == {"retail_inserted": 0, "retail_already_present": 1, "items_inserted": 0, "items_already_present": total_items()}  # numeric equal; NULL == NULL
    with engine.connect() as c:
        cols = set(c.execute(text("SELECT * FROM zambia_fuel_build_up_items LIMIT 1")).mappings().one())
        nulls = c.execute(text("SELECT count(*) FROM zambia_fuel_build_up_items WHERE amount IS NULL")).scalar()
        zeros = c.execute(text("SELECT count(*) FROM zambia_fuel_build_up_items WHERE amount = 0")).scalar()
    assert not cols & {"payload", "raw", "pdf", "raw_payload"} and nulls > 0 and zeros == 0
    got = fuel_buildup_store.fetch_build_up("PETROL", "pump", "2026-10", engine)
    assert [c.code for c in got.components][:3] == ["WHOLESALE_PRICE_TO_OMC", "NDOLA_FUEL_TERMINAL_FEE", "MARKING_FEE"]  # ERB order
    assert got.effective_date == date(2026, 10, 1) and got.components[0].amount == Decimal("22721.03") and got.components[0].unit == "ZMW/M3"
    assert next(c for c in got.components if c.code == "EXCISE_DUTY").amount == Decimal("2340.00")
    assert fuel_buildup_store.fetch_build_up("KEROSENE", "pump", None, engine).publication_month == "2026-10"  # newest month by default
    assert fuel_buildup_store.fetch_build_up("JET_A1", "wholesale", None, engine).components[-1].code == "WHOLESALE_PRICE_TO_OMC"
    assert fuel_buildup_store.fetch_build_up("JET_A1", "pump", None, engine) is None
    assert fuel_buildup_store.fetch_build_up("PETROL", "pump", "2026-09", engine) is None


def test_pg_conflicts_roll_back_everything_and_never_overwrite(engine):
    fuel_buildup_store.save_fuel_publications(retail_rows(), parsed_set(), retrieved_at=NOW, engine=engine)
    with engine.begin() as c:
        c.execute(text("UPDATE zambia_fuel_build_up_items SET amount = 1.00 WHERE product = 'PETROL' AND component_code = 'MARKING_FEE'"))
        c.execute(text("DELETE FROM zambia_fuel_build_up_items WHERE product = 'DIESEL' AND build_up_type = 'pump'"))
    with pytest.raises(fuel_store.FuelConflictError) as exc:
        fuel_buildup_store.save_fuel_publications(retail_rows(), parsed_set(), retrieved_at=NOW, engine=engine)
    assert exc.value.count == 1 and "204.59" not in str(exc.value)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM zambia_fuel_build_up_items WHERE product = 'DIESEL' AND build_up_type = 'pump'")).scalar() == 0
        assert c.execute(text("SELECT amount FROM zambia_fuel_build_up_items WHERE product = 'PETROL' AND component_code = 'MARKING_FEE'")).scalar() == Decimal("1.00")
    # a PDF retail price that disagrees with the XLSX/previously stored price is a conflict too
    with pytest.raises(fuel_store.FuelConflictError):
        fuel_buildup_store.save_fuel_publications(retail_rows("31.99"), [], retrieved_at=NOW, engine=engine)
    # NULL vs a published amount is a conflict as well
    with engine.begin() as c:
        c.execute(text("UPDATE zambia_fuel_build_up_items SET amount = NULL WHERE product = 'PETROL' AND component_code = 'EXCISE_DUTY'"))
    with pytest.raises(fuel_store.FuelConflictError):
        fuel_buildup_store.save_fuel_publications([], parsed_set()[:1], retrieved_at=NOW, engine=engine)


def test_pg_latest_fuel_reflects_a_newer_pdf_month_and_agrees_with_the_archive(engine):
    archive = [FuelRow(date(2026, 9, 1), "PETROL", "retail", Decimal("25.29"), "litre"), FuelRow(date(2026, 10, 1), "PETROL", "retail", Decimal("31.05"), "litre")]
    fuel_store.save_fuel_prices(archive, retrieved_at=NOW, source_url="https://www.erb.org.zm/a.zip", engine=engine)
    assert fuel_store.fetch_latest_fuel(engine)[0].effective_date == date(2026, 10, 1)
    # identical overlapping month from the press statement: no conflict
    assert fuel_buildup_store.save_fuel_publications(retail_rows(), [], retrieved_at=NOW, engine=engine)["retail_already_present"] == 1
    newer = [(FuelRow(date(2026, 11, 1), "PETROL", "retail", Decimal("33.06"), "litre"), "https://www.erb.org.zm/p.pdf")]
    fuel_buildup_store.save_fuel_publications(newer, [], retrieved_at=NOW, engine=engine)
    latest = fuel_store.fetch_latest_fuel(engine)[0]
    assert (latest.effective_date, latest.price_zmw, latest.source_url) == (date(2026, 11, 1), Decimal("33.06"), "https://www.erb.org.zm/p.pdf")


def test_pg_job_end_to_end_is_atomic_and_idempotent(engine):
    first = run_job(job_world(), engine=engine)
    assert first["retail_inserted"] == 4 and first["items_inserted"] == total_items()
    again = run_job(job_world(), engine=engine)
    assert again["retail_inserted"] == 0 and again["retail_already_present"] == 4 and again["items_already_present"] == total_items()
