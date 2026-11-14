"""IMF Primary Commodity Prices (PCPS): the monthly global Copper benchmark, series G001.PCOPP.USD.M.

Public SDMX 3.0 API, no key: GET https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/PCPS/+/G001.PCOPP.USD.M
returns exactly one series (world, indicator PCOPP, data transformation USD, monthly). The IMF code description is "Copper,
grade A cathode, LME spot price, CIF European ports, US$ per metric ton"; the values are nominal USD monthly averages (IMF
technical documentation: monthly values are averages of weekly or daily prices). Manual import only; one request; the
response is parsed in memory and never stored or logged.

The parser is strict. It resolves dimensions and time periods by id/metadata (never by fragile numeric positions alone),
requires exactly the verified series identity, and fails the whole import on anything unexpected: other dimension values,
other indicators/transformations/frequencies, a SCALE attribute that is not exactly 0 (no generic scaling is implemented),
malformed periods or values, duplicate months, and any observation that does not explicitly carry DERIVATION_TYPE R.

Raw-only governance: ZM Terminal currently ingests only IMF PCPS Copper observations explicitly marked "Raw data (no
derivation)". Missing, null, truncated, unknown or non-R status fails the whole import. If the IMF later publishes a
legitimate Copper observation under another status (for example an estimate or preliminary value) the importer
intentionally fails and requires a source-policy review before ingestion; nothing here claims the IMF will do so.
Values are any finite decimal the IMF publishes (positivity is not assumed as a source invariant; zero and negative values
are preserved exactly). Values are exact Decimals from the published strings (never float, never quantized). A month
the source does not publish is simply absent: nothing is interpolated, filled or invented. The source history is treated
as potentially revisable by the storage layer (a changed stored month is a conflict).
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

IMF_HOST = "api.imf.org"
IMF_COPPER_URL = "https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/PCPS/+/G001.PCOPP.USD.M"
IMF_TIMEOUT_SECONDS = 30.0
IMF_USER_AGENT = "ZM-Terminal/0.1 (IMF PCPS copper adapter)"
MAX_RESPONSE_BYTES = 2_000_000  # the real response is about 25 KB
EXPECTED_DIMENSIONS = {"COUNTRY": "G001", "INDICATOR": "PCOPP", "DATA_TRANSFORMATION": "USD", "FREQUENCY": "M"}
RAW_DERIVATION = "R"  # "Raw data (no derivation)"; any other code (estimate, projection, staff calculation) is refused
_PERIOD = re.compile(r"([0-9]{4})-M(0[1-9]|1[0-2])")
_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


class ImfCopperError(ValueError):
    """The IMF response (or request) is not in the verified shape, or contains an unusable value."""


@dataclass(frozen=True)
class CopperMonth:
    period_start: date  # first day of the published month
    value: Decimal  # nominal USD per metric ton, exactly as published


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ImfCopperError("Unexpected IMF URL") from exc
    if parts.scheme != "https" or parts.hostname != IMF_HOST or parts.username or parts.password or port not in (None, 443):
        raise ImfCopperError("Unexpected IMF URL")


async def fetch_copper_payload(client: httpx.AsyncClient) -> object:
    """One GET of the exact series; HTTPS and api.imf.org only, no redirects, a size cap, a JSON Content-Type check."""
    _check_url(IMF_COPPER_URL)
    async with client.stream("GET", IMF_COPPER_URL, follow_redirects=False, headers={"Accept": "application/json"}) as response:
        if response.is_redirect:
            raise ImfCopperError("Unexpected redirect from the IMF")
        response.raise_for_status()
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media != "application/json":
            raise ImfCopperError("Expected a JSON response from the IMF")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ImfCopperError("IMF response is too large")
    try:
        return json.loads(bytes(body))
    except ValueError as exc:
        raise ImfCopperError("IMF response is not valid JSON") from exc


def new_client() -> httpx.AsyncClient:
    """Never follows redirects; one source, one request."""
    return httpx.AsyncClient(headers={"User-Agent": IMF_USER_AGENT}, timeout=IMF_TIMEOUT_SECONDS, follow_redirects=False)


def _dict(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise ImfCopperError(f"Unexpected IMF structure: {what}")
    return value


def _list(value: object, what: str) -> list:
    if not isinstance(value, list):
        raise ImfCopperError(f"Unexpected IMF structure: {what}")
    return value


def _require_scale_zero(attributes: dict, series: object) -> None:
    """SCALE is a series attribute: resolve it by id through the attribute's value list; anything but exactly 0 fails."""
    error = ImfCopperError("Unexpected Copper SCALE; expected 0")
    if any(isinstance(a, dict) and a.get("id") == "SCALE" for a in _list(attributes.get("observation", []), "observation attributes")):
        raise error  # an observation-level SCALE is not the verified contract
    series_attrs = _list(attributes.get("series"), "series attributes")
    positions = [i for i, a in enumerate(series_attrs) if isinstance(a, dict) and a.get("id") == "SCALE"]
    if len(positions) != 1:
        raise error
    values = series_attrs[positions[0]].get("values")
    chosen = _dict(series, "series").get("attributes")
    if not isinstance(values, list) or not isinstance(chosen, list) or positions[0] >= len(chosen):
        raise error
    index = chosen[positions[0]]
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(values):
        raise error
    value = values[index]
    if not isinstance(value, dict) or value.get("id") != "0":
        raise error


def parse_copper_series(payload: object) -> list[CopperMonth]:
    """Validate the whole series and return it ascending by month; never partially returns."""
    data = _dict(_dict(payload, "root").get("data"), "data")
    structures = _list(data.get("structures"), "structures")
    datasets = _list(data.get("dataSets"), "dataSets")
    if len(structures) != 1 or len(datasets) != 1:
        raise ImfCopperError("Expected exactly one IMF structure and one dataset")
    structure = _dict(structures[0], "structure")
    links = _list(structure.get("links"), "links")
    if not any(isinstance(link, dict) and "Dataflow=IMF.RES:PCPS(" in str(link.get("urn", "")) for link in links):
        raise ImfCopperError("Response is not from the IMF PCPS dataflow")
    dimensions = _dict(structure.get("dimensions"), "dimensions")
    series_dims = _list(dimensions.get("series"), "series dimensions")
    resolved: list[str] = []  # the single value id of each series dimension, in key order
    seen: set[str] = set()
    for dim in series_dims:
        dim = _dict(dim, "series dimension")
        dim_id, values = dim.get("id"), _list(dim.get("values"), "dimension values")
        if dim_id not in EXPECTED_DIMENSIONS or dim_id in seen:
            raise ImfCopperError("Unexpected series dimension")
        seen.add(dim_id)
        if len(values) != 1 or _dict(values[0], "dimension value").get("id") != EXPECTED_DIMENSIONS[dim_id]:
            raise ImfCopperError(f"Unexpected {dim_id} value: this is not the world USD monthly PCOPP series")
        resolved.append(dim_id)
    if seen != set(EXPECTED_DIMENSIONS):
        raise ImfCopperError("A required series dimension is missing")
    obs_dims = _list(dimensions.get("observation"), "observation dimensions")
    time_dims = [d for d in obs_dims if isinstance(d, dict) and d.get("id") == "TIME_PERIOD"]
    if len(time_dims) != 1:
        raise ImfCopperError("Expected exactly one TIME_PERIOD dimension")
    periods = [_dict(v, "time value").get("value") for v in _list(time_dims[0].get("values"), "time values")]

    series_map = _dict(_dict(datasets[0], "dataset").get("series"), "series")
    if len(series_map) != 1:
        raise ImfCopperError("Expected exactly one series")
    (series_key, series), = series_map.items()
    if series_key.split(":") != ["0"] * len(series_dims):
        raise ImfCopperError("Series key does not point at the single resolved value of each dimension")
    observations = _dict(_dict(series, "series").get("observations"), "observations")
    if not observations:
        raise ImfCopperError("The series has no observations")

    attributes = _dict(structure.get("attributes"), "attributes")
    _require_scale_zero(attributes, series)
    obs_attrs = _list(attributes.get("observation"), "observation attributes")
    derivation_position = next((i for i, a in enumerate(obs_attrs) if isinstance(a, dict) and a.get("id") == "DERIVATION_TYPE"), None)
    if derivation_position is None:
        raise ImfCopperError("Missing DERIVATION_TYPE attribute; only raw (R) observations are ingested")
    derivation_values = [_dict(v, "attribute value").get("id") for v in _list(obs_attrs[derivation_position].get("values"), "values")]

    months: dict[date, Decimal] = {}
    for key, row in observations.items():
        if not isinstance(key, str) or not key.isdigit() or int(key) >= len(periods):
            raise ImfCopperError("Observation index is outside the time periods")
        period = periods[int(key)]
        match = _PERIOD.fullmatch(period) if isinstance(period, str) else None
        if not match:
            raise ImfCopperError("Malformed monthly period")
        row = _list(row, "observation")
        if not row or not isinstance(row[0], str) or not _DECIMAL.fullmatch(row[0]):
            raise ImfCopperError("Invalid published decimal value")
        value = Decimal(row[0])
        if not value.is_finite():
            raise ImfCopperError("Copper value must be a finite number")
        slot = derivation_position + 1  # position 0 of an observation row is the value itself
        index = row[slot] if slot < len(row) else None
        valid = isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(derivation_values)
        if not valid or derivation_values[index] != RAW_DERIVATION:
            raise ImfCopperError("Observation is not explicitly raw data (DERIVATION_TYPE R)")
        month = date(int(match.group(1)), int(match.group(2)), 1)
        if month in months:
            raise ImfCopperError("Duplicate month in the series")
        months[month] = value
    return [CopperMonth(month, months[month]) for month in sorted(months)]
