"""Narrow OCR fallback for *scanned* official ERB press statements (manual ingestion job only).

Used only when a press-statement PDF has no text layer at all (erb_pdf.ScannedPdfError); a text PDF that
fails structural validation is never OCR'd. Never imported by the API routes.

The statement is rendered in memory (300 and 400 DPI) and read twice with Tesseract. Each pass must
independently satisfy a narrow grammar (title month, "at midnight on" wording equal to the day before the
pricing month, a table of explicitly labelled PETROL/DIESEL/KEROSENE/JET A-1 rows with strict NN.NN prices whose
variance/percentage columns must add up). The two passes must agree on the month, the effective wording and every
product price; otherwise the whole statement is rejected. OCR output is never repaired (33.O6 stays invalid),
never averaged, never stored and never returned by the API: only the validated prices leave this module.
"""

import io
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import erb_pdf
from app.services.erb_pdf import MONTH_NAMES, MONTH_RE, FuelPdfError, ParsedPress

MAX_PAGES = 3
REQUIRED_PRODUCTS = ("PETROL", "DIESEL", "KEROSENE")


@dataclass(frozen=True)
class OcrPass:
    name: str
    dpi: int
    config: str


# Two independent, deterministic reads of the same page: Tesseract's uniform-block mode at two different
# render resolutions (~300 and 400 DPI). Chosen after comparing PSM 3/4/6/11 and several resolutions on the
# real scanned statements: the table is only read completely by PSM 6, and the second resolution gives the
# same engine a genuinely different input.
PASSES = (OcrPass("A", 300, "--oem 1 --psm 6"), OcrPass("B", 400, "--oem 1 --psm 6"))
OCR_DPI = PASSES[0].dpi


class OcrUnavailableError(FuelPdfError):
    """Tesseract (or its Python wrapper) is not installed; the job cannot read scanned statements."""


def render_pages(data: bytes, dpi: int = OCR_DPI):
    """In-memory greyscale page images of the PDF (nothing is written to disk)."""
    import pdfplumber  # lazy: ingestion-only dependency

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not 1 <= len(pdf.pages) <= MAX_PAGES:
                raise FuelPdfError("Unexpected page count for a scanned press statement")
            return [page.to_image(resolution=dpi).original.convert("L") for page in pdf.pages]
    except FuelPdfError:
        raise
    except Exception as exc:
        raise FuelPdfError("Scanned PDF could not be rendered") from exc


def tesseract_recognize(images, ocr_pass: OcrPass) -> str:
    try:
        import pytesseract
    except ImportError as exc:
        raise OcrUnavailableError("pytesseract is not installed") from exc
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # single-threaded Tesseract: reproducible output
    try:
        return "\n".join(pytesseract.image_to_string(image, lang="eng", config=ocr_pass.config) for image in images)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError("The tesseract binary is not installed") from exc
    except Exception as exc:  # never leak engine internals or image content
        raise FuelPdfError("OCR failed") from exc


# ---------------------------------------------------------------- the (much narrower) scanned-statement grammar
# Label variants: the printed "JET A-1" is commonly read with its final glyph as I, l or ! (observed on the real
# scans); only those spellings are recognised, and only in a *label*. Prices never get any such tolerance.
_LABELS = {"PETROL": "PETROL", "DIESEL": "DIESEL", "DIESEL (LSG)": "DIESEL", "KEROSENE": "KEROSENE",
           "JET A-1": "JET_A1", "JET A1": "JET_A1", "JET A-I": "JET_A1", "JET A-L": "JET_A1", "JET A-!": "JET_A1"}
_NUM = r"\(?\d+\.\d{2}\)?"
_ROW = re.compile(rf"^(?P<label>[A-Za-z][A-Za-z0-9 ()!-]*?)\s+(?P<nums>{_NUM}(?:\s+{_NUM}){{1,3}})$")
_HEADING = re.compile(r"^(?:(?:PRODUCT|CURRENT|NEW|ABSOLUTE|VARIANCE|CHANGE|PRICE|PRICES|%)\s*)+$", re.I)
_TABLE_START = re.compile(r"NATIONAL UNIFORM PUMP PRICES", re.I)
_TABLE_END = re.compile(r"(?:revised|current|new) (?:fuel )?prices (?:will|shall)|take effect|will remain in force", re.I)
_EFFECT = re.compile(rf"effect at mid-?night on (\d{{1,2}})[a-z*]{{0,2}}\s+{MONTH_RE},?\s+(\d{{4}})", re.I)
_TITLE = re.compile(rf"PUMP PRICES FOR {MONTH_RE}\s+(\d{{4}})", re.I)


def _money(token: str) -> Decimal:
    value = Decimal(token.strip("()"))
    return -value if token.startswith("(") else value


def _check_row(numbers: list[Decimal]) -> Decimal:
    """Columns are CURRENT, NEW[, VARIANCE[, % CHANGE]]; the arithmetic must hold, which also guards misread digits."""
    current, new = numbers[0], numbers[1]
    if current <= 0 or new <= 0:
        raise FuelPdfError("Scanned price is not positive")
    if len(numbers) == 2:
        if new != current:  # an unchanged price prints no variance
            raise FuelPdfError("Scanned row has no variance but the price changed")
        return new
    if numbers[2] != new - current:
        raise FuelPdfError("Scanned row variance does not match its prices")
    if len(numbers) == 4 and abs(numbers[3] - (numbers[2] / current * 100).quantize(Decimal("0.01"))) > Decimal("0.01"):
        raise FuelPdfError("Scanned row percentage does not match its prices")
    return new


def parse_ocr_pass(text: str) -> ParsedPress:
    """One OCR pass -> strict press statement. Any irregularity (stray characters, bad digits) raises."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise FuelPdfError("OCR produced no text")
    joined = " ".join(lines)
    titles = {(int(y), MONTH_NAMES.index(m.upper()) + 1) for m, y in _TITLE.findall(joined)}
    effect = _EFFECT.findall(joined)
    if len(titles) != 1 or len(effect) != 1:
        raise FuelPdfError("Scanned statement month or effective wording not identified")
    year, month = titles.pop()
    day, mon, yr = effect[0]
    try:
        midnight = date(int(yr), MONTH_NAMES.index(mon.upper()) + 1, int(day))
    except ValueError as exc:
        raise FuelPdfError("Scanned effective date is not a date") from exc
    start = date(year, month, 1)
    if (start - midnight).days != 1:
        raise FuelPdfError("Effective date does not match the pricing month")
    begin = next((i for i, line in enumerate(lines) if _TABLE_START.search(line)), None)
    if begin is None:
        raise FuelPdfError("Scanned price table not found")
    prices: dict[str, Decimal] = {}
    for line in lines[begin + 1:]:
        if _TABLE_END.search(line):
            break
        if _HEADING.fullmatch(line):
            continue
        row = _ROW.match(line)
        product = _LABELS.get(re.sub(r"\s+", " ", row.group("label")).upper()) if row else None
        if product is None:
            raise FuelPdfError("Unrecognised line inside the scanned price table")
        if product in prices:
            raise FuelPdfError("Duplicate product in the scanned statement")
        prices[product] = _check_row([_money(t) for t in row.group("nums").split()])
    else:
        raise FuelPdfError("Scanned price table has no end")
    if not prices:
        raise FuelPdfError("No prices found in the scanned statement")
    return ParsedPress(start, prices, midnight, "ocr")


def combine_passes(first: ParsedPress, second: ParsedPress) -> ParsedPress:
    """Both passes must agree exactly on month, effective wording and every product price."""
    if (first.month, first.effective_midnight) != (second.month, second.effective_midnight):
        raise FuelPdfError("OCR passes disagree on the pricing month")
    if first.prices != second.prices:
        raise FuelPdfError("OCR passes disagree on the prices")
    if any(product not in first.prices for product in REQUIRED_PRODUCTS):
        raise FuelPdfError("A pump product is missing from the scanned press statement")
    return first


def ocr_press_statement(data: bytes, recognizer: Callable[[list, OcrPass], str] | None = None) -> ParsedPress:
    """Scanned official press-statement PDF bytes -> validated prices, or FuelPdfError (fail closed)."""
    recognize = recognizer or tesseract_recognize
    parsed = []
    for ocr_pass in PASSES:
        parsed.append(parse_ocr_pass(recognize(render_pages(data, ocr_pass.dpi), ocr_pass)))
    return combine_passes(*parsed)
