"""ERB monthly publications (press statement + build-up PDFs): discovery and strict parsing.

Discovery uses ERB's public WordPress media API (not hard-coded filenames): PDFs under
`/wp-content/uploads/files/FuelPumpPrices/` (build-ups) and `/wp-content/uploads/PressStatements/`
are classified by filename into press statement / pump build-up / wholesale build-up / Jet A-1
wholesale build-up, per pricing month. Only https://www.erb.org.zm URLs are fetched.

Parsing is two-layer: `pdf_lines` turns a text-layer PDF into lines (fields separated by two
spaces) with pdfplumber; the `parse_*` functions are pure functions over those lines. Scanned
(image-only) PDFs have no text layer and are reported, never OCR'd. Every label, unit, product
heading and month is validated; an unknown row, unit or heading fails the document. Amounts are
parsed straight from printed text to Decimal ("24,721.03" -> 24721.03, "-" -> None,
"(2,117.40)" -> -2117.40), never through float, and published totals are never recomputed.
"""

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.config import get_settings

ERB_HOST = "www.erb.org.zm"
ERB_USER_AGENT = "ZM-Terminal/0.1 (ERB fuel publications adapter)"
ERB_TIMEOUT_SECONDS = 20.0
MAX_PDF_BYTES = 3_000_000
MAX_MEDIA_PAGES = 5
BUILD_UP_PATH = "/wp-content/uploads/files/FuelPumpPrices/"
PRESS_PATH = "/wp-content/uploads/PressStatements/"

MONTH_NAMES = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
               "OCTOBER", "NOVEMBER", "DECEMBER"]
MONTH_RE = "(" + "|".join(MONTH_NAMES) + ")"
PRODUCTS3 = ["PETROL", "DIESEL", "KEROSENE"]


class FuelPdfError(ValueError):
    """A publication is not in the verified layout or contains an unusable value."""


class ScannedPdfError(FuelPdfError):
    """The PDF has no text layer (a scanned image). Never OCR'd."""


# ---------------------------------------------------------------- amounts
_AMOUNT = re.compile(r"\(?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?")
_VALUE_TOKEN = r"(?:-|\(?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?)"


def parse_amount(token: str) -> Decimal | None:
    """Printed amount -> Decimal. "-" is not published (None). No float is ever involved."""
    token = token.strip()
    if token == "-":
        return None
    if not _AMOUNT.fullmatch(token) or token.count("(") != token.count(")"):
        raise FuelPdfError("Invalid printed amount")
    negative = token.startswith("(")
    try:
        value = Decimal(token.strip("()").replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - the regex already guarantees syntax
        raise FuelPdfError("Invalid printed amount") from exc
    return -value if negative else value


# ---------------------------------------------------------------- PDF -> lines
def pdf_lines(data: bytes) -> list[str]:
    """Text lines of a text-layer PDF; fields separated by 2+ spaces. Raises ScannedPdfError if none."""
    import pdfplumber  # lazy: only ingestion jobs need it, not the API runtime

    if not data.startswith(b"%PDF"):
        raise FuelPdfError("Not a PDF")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not 1 <= len(pdf.pages) <= 5:
                raise FuelPdfError("Unexpected page count")
            lines: list[str] = []
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
                rows: dict[int, list[dict]] = {}
                for word in words:
                    rows.setdefault(round(word["top"] / 3), []).append(word)
                for key in sorted(rows):
                    ordered = sorted(rows[key], key=lambda w: w["x0"])
                    text = ordered[0]["text"]
                    for prev, word in zip(ordered, ordered[1:]):
                        gap = word["x0"] - prev["x1"]
                        text += ("" if gap < 1 else " " if gap < 8 else "  ") + word["text"]
                    lines.append(text)
    except FuelPdfError:
        raise
    except Exception as exc:  # corrupt PDF; do not leak library internals
        raise FuelPdfError("PDF could not be read") from exc
    if not any(line.strip() for line in lines):
        raise ScannedPdfError("PDF has no text layer")
    return lines


# ---------------------------------------------------------------- shared row parsing
def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", label.lower())


def _split_row(line: str, n: int) -> tuple[list[str], list[Decimal | None]] | None:
    """(head segments, n trailing values) if the line ends with exactly n value tokens, else None."""
    match = re.match(rf"^(?P<head>.*?)(?P<vals>(?:\s+{_VALUE_TOKEN}){{{n}}})\s*$", line)
    if not match or not match.group("head").strip():
        return None
    values = [parse_amount(t) for t in match.group("vals").split()]
    head = re.split(r"\s{2,}", match.group("head").strip())
    return [re.sub(r"\s+", " ", h) for h in head], values


def _title_month(lines: list[str], keyword: re.Pattern, before: int) -> date:
    found = set()
    for line in lines[:before]:
        if keyword.search(line):
            for month, year in re.findall(rf"{MONTH_RE}\s+(\d{{4}})", line.upper()):
                found.add((int(year), MONTH_NAMES.index(month) + 1))
    if len(found) != 1:
        raise FuelPdfError("Publication month not identified")
    year, month = found.pop()
    return date(year, month, 1)


@dataclass(frozen=True)
class BuildUpItem:
    product: str
    component_code: str
    component_label: str
    amount: Decimal | None
    currency: str | None
    unit: str | None
    note: str | None
    position: int


@dataclass(frozen=True)
class ParsedBuildUp:
    kind: str  # "pump" | "wholesale" | "jet"
    month: date
    items: list[BuildUpItem]


def _currency(unit: str | None) -> str | None:
    if not unit:
        return None
    if unit.startswith(("US$", "$")):
        return "USD"
    if unit.startswith(("ZMW", "K ", "K/")):
        return "ZMW"
    return None


def _header_index(lines: list[str], expected: list[str]) -> int:
    for i, line in enumerate(lines):
        if [t for t in re.split(r"\s{2,}", line.strip())] == expected:
            return i
    raise FuelPdfError("Expected product heading not found")


# ---------------------------------------------------------------- pump build-up
_PUMP_LABELS = {
    "wholesalepricetoomc": "WHOLESALE_PRICE_TO_OMC", "ndolafuelterminalfee": "NDOLA_FUEL_TERMINAL_FEE",
    "markingfee": "MARKING_FEE", "exciseduty": "EXCISE_DUTY", "exfueldepot": "EX_FUEL_DEPOT",
    "transportationcost": "TRANSPORTATION_COST", "omcmargin": "OMC_MARGIN", "dealermargin": "DEALER_MARGIN",
    "strategicreservesfund": "STRATEGIC_RESERVES_FUND", "pricetodealerpricebeforevat": "PRICE_TO_DEALER_BEFORE_VAT",
    "uniformpumpprice": "UNIFORM_PUMP_PRICE",
}
_ERB_FEE = re.compile(r"ERB Fees? @ ?\d+(?:\.\d+)?%")
_VAT = re.compile(r"VAT(?: @ ?\d+(?:\.\d+)?%)?")
_TOTAL = re.compile(r"TOTAL \(Excl VAT\)")


def parse_pump_buildup(lines: list[str]) -> ParsedBuildUp:
    head = _header_index(lines, PRODUCTS3)
    month = _title_month(lines, re.compile(r"PUMP PRICE BUILD|PRICE ADJUSTMENT", re.I), head)
    first_row = next((i for i in range(head + 1, len(lines)) if lines[i].strip() and _split_row(lines[i], 3)), None)
    if first_row is None:
        raise FuelPdfError("No rows in the pump build-up")
    # The unit is "ZMW/ M3" per column; the PDF text layer may emit it as one line or as "ZMW/" and "M3" lines.
    unit_tokens = [re.sub(r"\s+", "", t) for line in lines[head + 1:first_row] for t in re.split(r"\s{2,}", line.strip()) if t]
    if sorted(unit_tokens) not in (["ZMW/M3"] * 3, sorted(["ZMW/"] * 3 + ["M3"] * 3), sorted(["ZMW/M"] * 3 + ["3"] * 3)):
        raise FuelPdfError("Unexpected pump build-up units")
    items: list[BuildUpItem] = []
    seen: set[str] = set()
    previous = ""
    for line in lines[first_row:]:
        if not line.strip():
            continue
        row = _split_row(line, 3)
        if row is None:
            raise FuelPdfError("Unexpected line in pump build-up")
        segments, values = row
        label, note = segments[0], None
        if len(segments) == 2 and re.fullmatch(r"\d+(?:\.\d+)?", segments[1]):
            note = f"rate {segments[1]}"  # the printed rate column, e.g. ERB fee 0.007
        elif len(segments) != 1:
            raise FuelPdfError("Unexpected pump build-up columns")
        if _ERB_FEE.fullmatch(label):
            code = "ERB_FEE"
        elif _VAT.fullmatch(label):
            code = "VAT"
        elif _TOTAL.fullmatch(label):
            code = {"TRANSPORTATION_COST": "TOTAL_EXCL_VAT_BEFORE_OMC_MARGIN",
                    "OMC_MARGIN": "TOTAL_EXCL_VAT_AFTER_OMC_MARGIN"}.get(previous, "")
            if not code:
                raise FuelPdfError("Unexpected position of a TOTAL row")
        elif _norm(label) in _PUMP_LABELS:
            code = _PUMP_LABELS[_norm(label)]
        else:
            raise FuelPdfError("Unknown pump build-up component")
        if code in seen:
            raise FuelPdfError("Duplicate pump build-up component")
        seen.add(code)
        previous = code
        for product, amount in zip(PRODUCTS3, values):
            items.append(BuildUpItem(product, code, label, amount, "ZMW", "ZMW/M3", note, len(seen)))
    if not {"WHOLESALE_PRICE_TO_OMC", "UNIFORM_PUMP_PRICE"} <= seen:
        raise FuelPdfError("Pump build-up is missing required components")
    return ParsedBuildUp("pump", month, items)


# ---------------------------------------------------------------- wholesale / Jet A-1 build-ups
_WHOLESALE_LABELS = {
    "plattsarabgulf": "PLATTS_ARAB_GULF", "conversionfactor": "CONVERSION_FACTOR", "fobprice": "FOB_PRICE",
    "oceanfreight": "OCEAN_FREIGHT", "cfdarbeira": "C_AND_F_DAR_BEIRA", "storage": "STORAGE",
    "insurance": "INSURANCE", "financingcharges": "FINANCING_CHARGES", "tippercharges": "TIPPER_CHARGES",
    "localcharges": "LOCAL_CHARGES", "wharfage": "WHARFAGE", "bpspremium": "BPS_PREMIUM",
    "cifdarbeira": "CIF_DAR_BEIRA", "transportationfeeroadpipeline": "TRANSPORTATION_FEE",
    "transportationlosses": "TRANSPORTATION_LOSSES", "importerscostplusmargin": "IMPORTERS_COST_PLUS_MARGIN",
    "ciflusakandola": "CIF_LUSAKA_NDOLA", "storagelosses": "STORAGE_LOSSES",
    "pricepermetrictonnes": "PRICE_PER_METRIC_TONNE", "priceinmetrictonnes": "PRICE_PER_METRIC_TONNE",
    "conversionfactors": "CONVERSION_FACTORS", "pricepercubicmetres": "PRICE_PER_CUBIC_METRE",
    "priceincubicmeters": "PRICE_PER_CUBIC_METRE", "exchangerate": "EXCHANGE_RATE",
    "pricebeforepricesupport": "PRICE_BEFORE_SUPPORT", "pricebeforepricestabilisation": "PRICE_BEFORE_SUPPORT",
    "pricebeforestabilisation": "PRICE_BEFORE_SUPPORT", "pricestabilisationsupport": "PRICE_STABILISATION_SUPPORT",
    "pricestabilisation": "PRICE_STABILISATION_SUPPORT", "pricesupport": "PRICE_STABILISATION_SUPPORT",
    "wholesalepricetotheomcs": "WHOLESALE_PRICE_TO_OMC", "wholesalepricetoomc": "WHOLESALE_PRICE_TO_OMC",
    "wholesalepricetoomcs": "WHOLESALE_PRICE_TO_OMC", "newwholesalepricetoomc": "WHOLESALE_PRICE_TO_OMC",
}
_JET_ONLY_LABELS = {  # rows that exist only in the Jet A-1 table; kept distinct from the shared rows
    "jeta1fobprice": "JET_A1_FOB_PRICE", "fob": "FOB", "freight": "FREIGHT", "tradersmargin": "TRADERS_MARGIN",
    "oceanlosses": "OCEAN_LOSSES", "cfdar": "C_AND_F_DAR", "cifdar": "CIF_DAR", "ciflusaka": "CIF_LUSAKA",
    "transportationfee": "TRANSPORTATION_FEE",
}
UNITS = {"US$/barrel", "US$/MT", "$/MT", "US$/M3", "$/M3", "K/M3", "K to $", "K to US$", "US$/tonne", "$/tonne"}
_NOTE = re.compile(r"^(?:[\d$&.%]|0)")


def _unit_and_note(segments: list[str], pending_note: str | None) -> tuple[str | None, str | None]:
    unit, note = None, pending_note
    for segment in segments[1:]:
        if segment in UNITS:
            if unit:
                raise FuelPdfError("Several units in one row")
            unit = segment
        elif re.fullmatch(r"K/M3\s*\(.*\)?", segment):  # e.g. "K/M3 (Inclusive of Price Support)"
            unit, note = "K/M3", segment
        elif _NOTE.match(segment) or "%" in segment or "$" in segment:
            note = f"{note} {segment}" if note else segment
        else:
            raise FuelPdfError("Unknown unit text in a build-up row")
    return unit, note


# The only continuation text ERB prints inside the wholesale table: the wharfage rate wording, wrapped over
# several lines (verified against the 2024-2026 documents). Anything else that is not a component row is rejected.
_USD = r"\$?\d+(?:\.\d{1,2})?"
_LOSS_NEIGHBOURS = frozenset({"IMPORTERS_COST_PLUS_MARGIN", "STORAGE_LOSSES", "PRICE_PER_METRIC_TONNE", "WHOLESALE_PRICE_TO_OMC"})
_NOTE_FAMILIES: list[tuple[re.Pattern, frozenset[str]]] = [  # (wording, rows the wrapped text may sit beside)
    (re.compile(rf"{_USD}/M3\s*which is equivalent to(?:\s+{_USD}/MT)?(?:\s+for)?(?:\s+petrol)?(?:\s*&)?", re.I),
     frozenset({"WHARFAGE", "BPS_PREMIUM"})),
    (re.compile(rf"&?\s*{_USD}/MT for Diesel & {_USD}/MT for Kerosene(?:/Jet A-1)?", re.I),
     frozenset({"WHARFAGE", "BPS_PREMIUM"})),
    (re.compile(r"\d+(?:\.\d+)?% petrol & \d+(?:\.\d+)?% diesel, \d+(?:\.\d+)?%(?: kerosene)?", re.I),
     _LOSS_NEIGHBOURS),
    (re.compile(r"\d+\.\d{2}%"), _LOSS_NEIGHBOURS),
]


def _note_rows(line: str) -> frozenset[str] | None:
    """Rows a recognised continuation line may precede, or None if the line is not a recognised note."""
    text = re.sub(r"\s+", " ", line.strip())
    return next((rows for pattern, rows in _NOTE_FAMILIES if pattern.fullmatch(text)), None)


def _parse_wholesale(lines: list[str], kind: str) -> ParsedBuildUp:
    products = PRODUCTS3 if kind == "wholesale" else ["JET A-1"]
    head = _header_index(lines, products)
    keyword = re.compile(r"WHOLESALE PRICE BUILD" if kind == "wholesale" else r"WHOLESALE.*BUILD.*JET A-? ?1", re.I)
    month = _title_month(lines, keyword, head)
    labels = dict(_WHOLESALE_LABELS) | (_JET_ONLY_LABELS if kind == "jet" else {})
    product_codes = ["JET_A1"] if kind == "jet" else PRODUCTS3
    items: list[BuildUpItem] = []
    seen: set[tuple[str, str | None]] = set()
    pending: str | None = None
    allowed: frozenset[str] | None = None
    position = 0
    for line in lines[head + 1:]:
        if not line.strip():
            continue
        row = _split_row(line, len(products))
        if row is None:
            rows = _note_rows(line)  # only the printed wharfage/loss-rate wording; anything else fails closed
            if rows is not None:
                allowed = rows if allowed is None else allowed & rows
                pending = re.sub(r"\s+", " ", f"{pending} {line.strip()}" if pending else line.strip())
                continue
            raise FuelPdfError("Unexpected line in wholesale build-up")
        segments, values = row
        label = segments[0]
        key = _norm(label)
        if kind == "jet" and key == "newwholesalepricetoomckm3":  # the unit is part of the printed label
            label, key, segments = "New Wholesale Price to OMC", "newwholesalepricetoomc", [segments[0], "K/M3"]
        if key not in labels:
            raise FuelPdfError("Unknown wholesale build-up component")
        code = labels[key]
        if pending and (allowed is None or code not in allowed):
            raise FuelPdfError("A continuation note sits beside an unexpected row")
        unit, note = _unit_and_note(segments, pending)
        pending, allowed = None, None
        if code == "WHOLESALE_PRICE_TO_OMC" and unit and unit != "K/M3":
            code = "WHOLESALE_PRICE_TO_OMC_USD"  # a US$ row is a different quantity from the K/M3 price
        if (code, unit) in seen:
            raise FuelPdfError("Duplicate wholesale build-up component")
        seen.add((code, unit))
        position += 1
        for product, amount in zip(product_codes, values):
            items.append(BuildUpItem(product, code, label, amount, _currency(unit), unit, note, position))
    if pending:
        raise FuelPdfError("Dangling note in wholesale build-up")
    if ("WHOLESALE_PRICE_TO_OMC", "K/M3") not in seen:
        raise FuelPdfError("Wholesale build-up is missing the wholesale price to OMC")
    return ParsedBuildUp(kind, month, items)


def parse_wholesale_buildup(lines: list[str]) -> ParsedBuildUp:
    return _parse_wholesale(lines, "wholesale")


def parse_jet_buildup(lines: list[str]) -> ParsedBuildUp:
    return _parse_wholesale(lines, "jet")


# ---------------------------------------------------------------- press statement (retail K/litre)
_PRESS_PRODUCTS = {"PETROL": "PETROL", "DIESEL": "DIESEL", "DIESEL(LSG)": "DIESEL", "DIESEL/LSG": "DIESEL",
                   "KEROSENE": "KEROSENE", "JETA-1": "JET_A1", "JETA1": "JET_A1"}
_PRESS_ROW = r"(PETROL|DIESEL(?: \(LSG\)|/LSG)?|KEROSENE|JET A-?1)((?: [0-9.()%-]+)+)"


@dataclass(frozen=True)
class ParsedPress:
    month: date
    prices: dict[str, Decimal]  # product -> K/litre, exactly as printed (two decimals)
    effective_midnight: date    # the date the statement says the prices take effect "at midnight on"
    extraction_method: str = "text"  # "text" (text layer) or "ocr" (scanned statement); operational metadata only


def parse_press_statement(lines: list[str]) -> ParsedPress:
    text = re.sub(r"\s+", " ", " ".join(lines))
    titles = {(int(y), MONTH_NAMES.index(m.upper()) + 1)
              for m, y in re.findall(rf"PUMP PRICES FOR {MONTH_RE} (\d{{4}})", text, re.I)}
    if len(titles) != 1:
        raise FuelPdfError("Press statement month not identified")
    year, month = titles.pop()
    effect = re.findall(rf"effect at mid-?night on (\d{{1,2}})(?:st|nd|rd|th)? {MONTH_RE},? (\d{{4}})", text, re.I)
    if len(effect) != 1:
        raise FuelPdfError("Press statement effective date not identified")
    day, mon, yr = effect[0]
    midnight = date(int(yr), MONTH_NAMES.index(mon.upper()) + 1, int(day))
    start = date(year, month, 1)
    if (start - midnight).days != 1:  # prices apply from midnight at the end of the previous month
        raise FuelPdfError("Effective date does not match the pricing month")
    # The table sits between the "PRODUCT" heading and the next prose sentence. Cells may wrap across
    # text lines, so it is read as a token stream rather than line by line.
    table = re.search(r"\bPRODUCT\b(.*?)(?=\b(?:The|These|Posted|Recommended|AIRPORT|Issued|By Order)\b|$)", text)
    if table is None:
        raise FuelPdfError("Price table not found")
    body = table.group(1).strip()
    first = re.search(r"\b(PETROL|DIESEL|KEROSENE|JET A-?1)\b", body)
    if first is None:
        raise FuelPdfError("No prices found in the press statement")
    heading = body[:first.start()].upper()
    if "CURRENT" in heading and "NEW" in heading:
        column = 1  # CURRENT, NEW, ABSOLUTE VARIANCE: the new price is the second column
    elif "PUMP PRICE" in heading:
        column = 0
    else:
        raise FuelPdfError("Unexpected price table heading")
    rows = re.findall(_PRESS_ROW, body[first.start():])
    leftover = re.sub(_PRESS_ROW, "", body[first.start():]).strip()
    if leftover:
        raise FuelPdfError("Unexpected text inside the price table")
    prices: dict[str, Decimal] = {}
    for label, values in rows:
        tokens = values.split()
        product = _PRESS_PRODUCTS[re.sub(r"\s+", "", label)]
        if len(tokens) <= column or not re.fullmatch(r"\d+\.\d{2}", tokens[column]):
            raise FuelPdfError("Malformed press statement price row")
        if product in prices:
            raise FuelPdfError("Duplicate product in press statement")
        prices[product] = Decimal(tokens[column])
        if prices[product] <= 0:
            raise FuelPdfError("Press statement price is not positive")
    if not prices:
        raise FuelPdfError("No prices found in the press statement")
    return ParsedPress(start, prices, midnight)


# ---------------------------------------------------------------- reconciliation (checks only)


def reconcile(press: ParsedPress, pump: ParsedBuildUp) -> int:
    """Check each press price against the pump build-up's uniform pump price; returns products compared.

    ERB prints the pump price to the cubic metre (33,061.35 ZMW/M3 = 33.06135 ZMW/litre) and the press
    statement to two decimals (33.06), so the press price must equal the build-up price converted to
    ZMW/litre and rounded half-up to two decimals. Jet A-1 has no pump build-up and is not compared.
    The press price is never replaced by the build-up value. A statement that was OCR'd must have a
    build-up price for every pump product it contains.
    """
    if press.month != pump.month:
        raise FuelPdfError("Press statement and build-up are for different months")
    uniform = {i.product: i.amount for i in pump.items if i.component_code == "UNIFORM_PUMP_PRICE"}
    checked = 0
    for product, price in press.prices.items():
        if product == "JET_A1":
            continue
        amount = uniform.get(product)
        if amount is None:
            if press.extraction_method == "ocr":
                raise FuelPdfError("No build-up price to confirm a scanned press price")
            continue
        if price != (amount / Decimal(1000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
            raise FuelPdfError("Press statement price disagrees with the build-up")
        checked += 1
    return checked


# ---------------------------------------------------------------- discovery
@dataclass(frozen=True)
class PublicationDoc:
    kind: str  # press | pump | wholesale | jet
    month: date
    url: str


def _is_erb_pdf_url(url: object) -> bool:
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and parts.scheme == "https" and parts.netloc == ERB_HOST
                and parts.path.lower().endswith(".pdf")
                and (parts.path.startswith(BUILD_UP_PATH) or parts.path.startswith(PRESS_PATH)))


def classify(url: str) -> PublicationDoc | None:
    """Classify an official ERB PDF URL by filename; None if it is not a monthly price publication."""
    if not _is_erb_pdf_url(url):
        return None
    path = unquote(urlsplit(url).path)
    name = path.rsplit("/", 1)[-1].upper()
    found = re.findall(rf"{MONTH_RE}\D{{0,3}}(\d{{4}})", name)
    if len(found) != 1:
        return None
    month = date(int(found[0][1]), MONTH_NAMES.index(found[0][0]) + 1, 1)
    if path.startswith(PRESS_PATH):
        return PublicationDoc("press", month, url) if name.startswith("CURRENTFUELPUMPPRICES") else None
    if "JET" in name:
        return PublicationDoc("jet", month, url)
    if "PUMP-PRICE-BUILD" in name:
        return PublicationDoc("pump", month, url)
    if "WHOLESALE" in name:
        return PublicationDoc("wholesale", month, url)
    return None


@dataclass
class Discovery:
    docs: dict[tuple[date, str], str]  # (month, kind) -> url
    ambiguous: frozenset = frozenset()  # (month, kind) pairs with two different files
    unclassified: int = 0


async def discover_publications(client: httpx.AsyncClient) -> Discovery:
    """Monthly publications from ERB's media API. Two different files for one (month, kind) are
    ambiguous and excluded rather than guessed."""
    found: dict[tuple[date, str], set[str]] = {}
    unclassified = 0
    for page in range(1, MAX_MEDIA_PAGES + 1):
        response = await client.get(
            get_settings().erb_media_url,
            params={"search": "pump", "mime_type": "application/pdf", "per_page": "100", "page": str(page),
                    "orderby": "date", "order": "desc", "_fields": "date,source_url"},
            timeout=ERB_TIMEOUT_SECONDS, headers={"User-Agent": ERB_USER_AGENT, "Accept": "application/json"},
        )
        if response.status_code == 400:  # past the last page
            break
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            raise FuelPdfError("Unexpected media listing")
        for item in items:
            url = item.get("source_url") if isinstance(item, dict) else None
            doc = classify(url) if isinstance(url, str) else None
            if doc is None:
                unclassified += 1
            else:
                found.setdefault((doc.month, doc.kind), set()).add(url)
        if len(items) < 100:
            break
    docs = {key: next(iter(urls)) for key, urls in found.items() if len(urls) == 1}
    return Discovery(docs, ambiguous=frozenset(k for k, urls in found.items() if len(urls) > 1), unclassified=unclassified)


async def download_pdf(client: httpx.AsyncClient, url: str) -> bytes:
    if not _is_erb_pdf_url(url):
        raise FuelPdfError("Refusing to download from a non-ERB location")
    data = bytearray()
    async with client.stream("GET", quote(url, safe=":/%"), timeout=ERB_TIMEOUT_SECONDS,
                             headers={"User-Agent": ERB_USER_AGENT}) as response:
        response.raise_for_status()
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
            raise FuelPdfError("Unexpected content type")
        async for chunk in response.aiter_bytes():
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                raise FuelPdfError("PDF is larger than expected")
    return bytes(data)
