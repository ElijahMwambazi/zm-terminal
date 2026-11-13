"""Offline tests for the scanned-press-statement OCR fallback (no live ERB, no network)."""

import asyncio
import os
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

import httpx
import pytest
from sqlalchemy import text

from app.jobs import backfill_fuel_buildups as job
from app.services import erb_ocr, erb_pdf, fuel_buildup_store, fuel_store
from app.services.erb_fuel import FuelRow
from app.services.erb_pdf import FuelPdfError
from tests.pdfgen import make_pdf
from tests.test_fuel_pdf import (B, NOW, engine, job_world, jet_lines, media_client, parsed_set, press_lines,  # noqa: F401
                                 pump_lines, run_job, wholesale_lines, listing_for, total_items)

SCANNED = make_pdf([], text=False)  # a PDF page with no text layer
REAL_RENDER = erb_ocr.render_pages


@pytest.fixture(autouse=True)
def fast_render(monkeypatch):
    """Rendering a page at 300/400 DPI is slow and irrelevant to the grammar tests: stub it (tested separately)."""
    monkeypatch.setattr(erb_ocr, "render_pages", lambda data, dpi=erb_ocr.OCR_DPI: ["page image"])


def small_scanned_pdf(lines=None) -> bytes:
    """An image-only A4 PDF (no text layer); optionally with printed text, as a scan would have."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (1240, 1754), 255)  # A4 at 150 DPI
    if lines:
        draw, font, y = ImageDraw.Draw(image), ImageFont.load_default(size=26), 80
        for line in lines:
            draw.text((60, y), " ".join(line.split()), font=font, fill=0)
            y += 52
    out = BytesIO()
    image.save(out, "PDF", resolution=150.0)
    return out.getvalue()
OCTOBER = date(2026, 10, 1)


CURRENT = {"PETROL": "25.29", "DIESEL": "26.86", "KEROSENE": "27.02", "JET A-1": "28.71"}


def ocr_text(prices=("31.05", "32.45", "30.08", "31.85"), month="OCTOBER 2026", midnight="30* September 2026", jet="JET A-1",
             variance=True, extra=()) -> str:
    """What Tesseract (--psm 6) returns for a scanned press statement: prose, a one-line-per-product table, prose."""
    rows = []
    for (label, current), new in zip(CURRENT.items(), prices):
        label = jet if label == "JET A-1" else label
        try:
            gap = str(Decimal(new) - Decimal(current)) if variance else ""
        except Exception:
            gap = "0.00"
        rows.append(f"{label} {current} {new} {gap}".strip())
    return "\n".join([
        "Press Release For Immediate Release", f"REVISION OF PETROLEUM PUMP PRICES FOR {month}",
        "The Energy Regulation Board (ERB) hereby announces the revised prices.",
        "NATIONAL UNIFORM PUMP PRICES K/LITRE", "ABSOLUTE", *rows, *extra,
        f"The revised prices will take effect at midnight on {midnight} and will remain in force until",
        "the next price review.", "Issued on Wednesday, 30 September 2026"])


def recognizer(*texts):
    """Fake Tesseract: pass A gets texts[0], pass B gets texts[-1]; records the passes it was asked to run."""
    calls = []

    def recognize(images, ocr_pass):
        calls.append(ocr_pass)
        return texts[0] if ocr_pass.name == "A" else texts[-1]
    recognize.calls = calls
    return recognize


def read(*texts):
    return erb_ocr.ocr_press_statement(SCANNED, recognizer(*texts))


# ---------------------------------------------------------------- extraction
def test_four_valid_products_are_read_with_exact_decimals_and_marked_as_ocr():
    parsed = read(ocr_text())
    assert parsed.prices == {"PETROL": Decimal("31.05"), "DIESEL": Decimal("32.45"), "KEROSENE": Decimal("30.08"),
                             "JET_A1": Decimal("31.85")}
    assert parsed.month == OCTOBER and parsed.extraction_method == "ocr"
    assert all(isinstance(v, Decimal) for v in parsed.prices.values())


def test_both_passes_are_run_at_different_resolutions_with_a_fixed_config():
    rec = recognizer(ocr_text())
    erb_ocr.ocr_press_statement(SCANNED, rec)
    assert rec.calls == list(erb_ocr.PASSES) and len({p.dpi for p in erb_ocr.PASSES}) == 2
    assert all("--psm 6" in p.config and "--oem 1" in p.config for p in erb_ocr.PASSES)


@pytest.mark.parametrize("jet_label", ["JET A-1", "JET A1", "JET A-I", "JET A-l", "JET A-!"])
def test_known_jet_label_variants(jet_label):
    assert read(ocr_text(jet=jet_label)).prices["JET_A1"] == Decimal("31.85")


@pytest.mark.parametrize("jet_label", ["JET A-7", "JET", "AIR A-1", "KERO A-1"])
def test_unknown_labels_are_rejected(jet_label):
    with pytest.raises(FuelPdfError):
        read(ocr_text(jet=jet_label))


def test_passes_that_disagree_reject_the_document_and_nothing_is_averaged_or_chosen():
    with pytest.raises(FuelPdfError, match="disagree"):
        read(ocr_text(), ocr_text(prices=("31.05", "32.45", "30.08", "31.86")))
    with pytest.raises(FuelPdfError, match="disagree"):
        read(ocr_text(), ocr_text(month="NOVEMBER 2026", midnight="31* October 2026"))


def test_a_pass_that_cannot_parse_rejects_even_if_the_other_is_fine():
    with pytest.raises(FuelPdfError):
        read(ocr_text(), "garbled | text")


@pytest.mark.parametrize("bad", ["33.O6", "3З.06", "33,06", "33.6", "33.060", "33", "-33.06", "33..06", "O.00", "3l.05"])
def test_ocr_misreads_and_malformed_prices_are_rejected_not_repaired(bad):
    text = ocr_text().replace("PETROL 25.29 31.05 5.76", f"PETROL 25.29 {bad} 5.76")
    with pytest.raises(FuelPdfError):
        read(text)


def test_zero_prices_are_rejected():
    with pytest.raises(FuelPdfError):
        read(ocr_text().replace("PETROL 25.29 31.05 5.76", "PETROL 0.00 0.00 0.00"))


def test_duplicate_product_is_rejected():
    with pytest.raises(FuelPdfError, match="Duplicate"):
        read(ocr_text(extra=("KEROSENE 27.02 30.09 3.07",)))


def test_missing_products_are_handled_explicitly():
    no_jet = "\n".join(l for l in ocr_text().splitlines() if not l.startswith("JET"))
    assert "JET_A1" not in read(no_jet).prices  # Jet A-1 is simply not stored when the table does not carry it
    no_petrol = "\n".join(l for l in ocr_text().splitlines() if not l.startswith("PETROL"))
    with pytest.raises(FuelPdfError, match="missing"):
        read(no_petrol)


def test_products_are_never_inferred_from_row_order():
    unlabelled = ocr_text().replace("PETROL ", "").replace("DIESEL ", "")
    with pytest.raises(FuelPdfError):
        read(unlabelled)


def test_prices_must_add_up_with_their_variance_and_percentage_columns():
    with pytest.raises(FuelPdfError, match="variance"):
        read(ocr_text().replace("PETROL 25.29 31.05 5.76", "PETROL 25.29 31.05 5.75"))
    with pytest.raises(FuelPdfError, match="no variance"):
        read(ocr_text(variance=False))  # a changed price must show its variance
    unchanged = ocr_text(prices=("25.29", "26.86", "27.02", "28.71"), variance=False)
    assert read(unchanged).prices["PETROL"] == Decimal("25.29")  # unchanged prices print only CURRENT NEW
    with_pct = ocr_text().replace("PETROL 25.29 31.05 5.76", "PETROL 25.29 31.05 5.76 22.77")
    assert read(with_pct).prices["PETROL"] == Decimal("31.05")
    with pytest.raises(FuelPdfError, match="percentage"):
        read(ocr_text().replace("PETROL 25.29 31.05 5.76", "PETROL 25.29 31.05 5.76 22.97"))


def test_stray_text_inside_the_table_rejects():
    with pytest.raises(FuelPdfError, match="Unrecognised"):
        read(ocr_text(extra=("/ KEROSENE | 35.05 33.9 | (1.14)",)))


def test_wrong_effective_wording_or_month_is_rejected():
    with pytest.raises(FuelPdfError):
        read(ocr_text(midnight="31* October 2026"))  # not the day before the pricing month
    with pytest.raises(FuelPdfError):
        read(ocr_text().replace("at midnight on", "on"))
    assert read(ocr_text(midnight="30th September 2026")).month == OCTOBER  # a clean ordinal is fine too


def test_empty_ocr_output_is_rejected():
    with pytest.raises(FuelPdfError):
        read("   \n ")


def test_pages_are_rendered_in_memory_at_300_dpi_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf = small_scanned_pdf()
    images = REAL_RENDER(pdf, 300)
    assert erb_ocr.OCR_DPI == 300 and images and images[0].mode == "L"
    assert images[0].size[0] > 2000  # 300 DPI of an A4 page, not a thumbnail
    assert REAL_RENDER(pdf, 400)[0].size[0] > images[0].size[0]
    assert list(tmp_path.iterdir()) == []


def test_missing_tesseract_fails_loudly_without_reading(monkeypatch):
    import pytesseract

    def not_found(*a, **k):
        raise pytesseract.TesseractNotFoundError()
    monkeypatch.setattr(pytesseract, "image_to_string", not_found)
    with pytest.raises(erb_ocr.OcrUnavailableError):
        erb_ocr.ocr_press_statement(SCANNED)


def test_ocr_text_is_not_part_of_the_parsed_result():
    parsed = read(ocr_text())
    assert set(vars(parsed)) == {"month", "prices", "effective_midnight", "extraction_method"}


# ---------------------------------------------------------------- pump reconciliation
def october_pump():
    return erb_pdf.parse_pump_buildup(pump_lines(month="OCTOBER 2026"))  # 31,052.35 / 32,446.72 / 30,081.51


def test_reconciliation_exact_two_decimal_match():
    pump = erb_pdf.parse_pump_buildup(pump_lines(month="OCTOBER 2026", uniform=("31,050.00", "32,450.00", "30,080.00")))
    assert erb_pdf.reconcile(read(ocr_text()), pump) == 3


def test_underlying_buildup_with_more_than_two_decimals_rounds_to_the_official_price():
    # 31,052.35 -> 31.05235 -> 31.05 ; 32,446.72 -> 32.44672 -> 32.45 ; 30,081.51 -> 30.08151 -> 30.08
    assert erb_pdf.reconcile(read(ocr_text()), october_pump()) == 3
    pump = erb_pdf.parse_pump_buildup(pump_lines(month="OCTOBER 2026", uniform=("31,055.00", "32,445.00", "30,075.00")))
    assert erb_pdf.reconcile(read(ocr_text(prices=("31.06", "32.45", "30.08", "31.85"))), pump) == 3  # half-up: 31.055 -> 31.06


@pytest.mark.parametrize("prices", [("31.04", "32.45", "30.08", "31.85"), ("31.05", "32.44", "30.08", "31.85"),
                                    ("31.05", "32.45", "30.09", "31.85")])
def test_any_one_cent_mismatch_rejects(prices):
    press = read(ocr_text(prices=prices))
    with pytest.raises(FuelPdfError, match="disagrees"):
        erb_pdf.reconcile(press, october_pump())


def test_the_buildup_never_becomes_the_retail_value():
    press = read(ocr_text())
    before = dict(press.prices)
    erb_pdf.reconcile(press, october_pump())
    assert press.prices == before and press.prices["DIESEL"] == Decimal("32.45")


def test_jet_a1_does_not_use_pump_reconciliation():
    pump = october_pump()
    press = read(ocr_text(prices=("31.05", "32.45", "30.08", "99.99")))  # Jet is not compared to anything
    assert erb_pdf.reconcile(press, pump) == 3 and press.prices["JET_A1"] == Decimal("99.99")
    assert not [i for i in pump.items if i.product == "JET_A1"]


def test_a_scanned_statement_needs_a_buildup_price_for_every_pump_product():
    pump = october_pump()
    pump = erb_pdf.ParsedBuildUp(pump.kind, pump.month, [i for i in pump.items if i.product != "DIESEL"])
    with pytest.raises(FuelPdfError):
        erb_pdf.reconcile(read(ocr_text()), pump)


# ---------------------------------------------------------------- job: trigger and acceptance rules
class Counter:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = 0, result, error

    def __call__(self, data):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def scanned_ok():
    return Counter(read(ocr_text()))


def test_a_text_press_statement_never_invokes_ocr():
    ocr = Counter()
    result = run_job(job_world(), dry_run=True, ocr=ocr)
    assert ocr.calls == 0 and result["text_press_parsed"] == 1 and result["retail_rows"] == 4
    assert result["ocr_parsed"] == 0 and result["reconciliation_passes"] == 3


def test_a_scanned_press_statement_invokes_ocr_and_reconciles():
    ocr = scanned_ok()
    result = run_job(job_world(scanned_press=True), dry_run=True, ocr=ocr)
    assert ocr.calls == 1 and result["scanned_documents"] == 1 and result["ocr_parsed"] == 1
    assert result["text_press_parsed"] == 0 and result["retail_rows"] == 4 and result["reconciliation_passes"] == 3


def test_a_malformed_text_press_statement_is_not_rescued_by_ocr():
    world_urls = {"press": f"{B}/PressStatements/CurrentFuelPumpPricesOctober2026.pdf"}
    malformed = make_pdf(["REVISION OF PETROLEUM PUMP PRICES FOR OCTOBER 2026", "nothing useful here"])
    transport = media_client(listing_for(list(world_urls.values())), {world_urls["press"]: (malformed, "application/pdf")})
    ocr = Counter()
    result = run_job(transport, dry_run=True, ocr=ocr)
    assert ocr.calls == 0 and result["unsupported_documents"] == 1 and result["scanned_documents"] == 0


def test_only_press_statements_are_ocrd_not_scanned_buildups():
    urls = {"pump": f"{B}/files/FuelPumpPrices/OCTOBER-2026-PUMP-PRICE-BUILD-UP.pdf"}
    transport = media_client(listing_for(list(urls.values())), {urls["pump"]: (SCANNED, "application/pdf")})
    ocr = Counter()
    result = run_job(transport, dry_run=True, ocr=ocr)
    assert ocr.calls == 0 and result["scanned_documents"] == 1 and result["ocr_rejected"] == 0


def test_ocr_rejection_is_counted_and_stores_no_retail_but_keeps_buildups():
    result = run_job(job_world(scanned_press=True), dry_run=True, ocr=Counter(error=FuelPdfError("x")))
    assert result["ocr_rejected"] == 1 and result["ocr_parsed"] == 0 and result["retail_rows"] == 0
    assert result["build_up_items"] == total_items()


def test_ocr_price_that_disagrees_with_the_buildup_is_not_stored():
    wrong = Counter(read(ocr_text(prices=("31.05", "32.44", "30.08", "31.85"))))
    result = run_job(job_world(scanned_press=True), dry_run=True, ocr=wrong)
    assert result["reconciliation_failures"] == 1 and result["ocr_parsed"] == 0 and result["ocr_rejected"] == 1
    assert result["retail_rows"] == 0 and result["build_up_items"] == total_items()  # build-ups are independent


def test_ocr_without_the_months_pump_buildup_is_not_stored():
    result = run_job(job_world(scanned_press=True, with_pump=False), dry_run=True, ocr=scanned_ok())
    assert result["ocr_parsed"] == 0 and result["ocr_rejected"] == 1 and result["retail_rows"] == 0


def test_missing_tesseract_aborts_the_job_with_a_clear_error():
    with pytest.raises(erb_ocr.OcrUnavailableError):
        run_job(job_world(scanned_press=True), dry_run=True, ocr=Counter(error=erb_ocr.OcrUnavailableError("no tesseract")))


# ---------------------------------------------------------------- persistence
def rows_for(prices):
    return [FuelRow(OCTOBER, p, "retail", Decimal(v), "litre") for p, v in prices.items()]


def test_pg_ocr_and_text_observations_share_one_identity(engine):
    from_text = erb_pdf.parse_press_statement(press_lines())
    from_ocr = read(ocr_text())
    assert from_text.prices == from_ocr.prices
    first = fuel_buildup_store.save_fuel_publications([(r, "text-url") for r in rows_for(from_text.prices)], [], retrieved_at=NOW, engine=engine)
    second = fuel_buildup_store.save_fuel_publications([(r, "ocr-url") for r in rows_for(from_ocr.prices)], [], retrieved_at=NOW, engine=engine)
    assert first["retail_inserted"] == 4 and second["retail_inserted"] == 0 and second["retail_already_present"] == 4
    different = rows_for({**from_ocr.prices, "PETROL": Decimal("31.06")})
    with pytest.raises(fuel_store.FuelConflictError):
        fuel_buildup_store.save_fuel_publications([(r, "ocr-url") for r in different], [], retrieved_at=NOW, engine=engine)


def test_pg_no_ocr_text_image_or_pdf_is_stored(engine):
    run_job(job_world(scanned_press=True), engine=engine, ocr=scanned_ok())
    with engine.connect() as c:
        for table in ("zambia_fuel_prices", "zambia_fuel_build_up_items"):
            columns = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table})}
            assert not columns & {"raw", "payload", "ocr_text", "image", "pdf", "content", "body"}
        stored = " ".join(str(v) for row in c.execute(text("SELECT * FROM zambia_fuel_prices")) for v in row)
        assert "REVISION" not in stored and "midnight" not in stored


# ---------------------------------------------------------------- real Tesseract on a generated scan
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
def test_real_tesseract_reads_a_generated_scanned_statement(monkeypatch):
    monkeypatch.setattr(erb_ocr, "render_pages", REAL_RENDER)
    rows = ["NATIONAL UNIFORM PUMP PRICES K/LITRE", "PRODUCT CURRENT NEW VARIANCE", "PETROL 31.46 33.06 1.60",
            "DIESEL 33.27 34.45 1.18", "KEROSENE 29.76 32.08 2.32", "JET A-1 31.85 34.51 2.66"]
    lines = ["Press Release For Immediate Release", "REVISION OF PETROLEUM PUMP PRICES FOR NOVEMBER 2026", *rows,
             "The revised prices will take effect at midnight on 31st October 2026 and will remain in force"]
    pdf = small_scanned_pdf(lines)
    with pytest.raises(erb_pdf.ScannedPdfError):
        erb_pdf.pdf_lines(pdf)
    parsed = erb_ocr.ocr_press_statement(pdf)
    assert parsed.prices == {"PETROL": Decimal("33.06"), "DIESEL": Decimal("34.45"), "KEROSENE": Decimal("32.08"),
                             "JET_A1": Decimal("34.51")} and parsed.extraction_method == "ocr"
