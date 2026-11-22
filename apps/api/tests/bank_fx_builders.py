"""Synthetic BoZ commercial-bank-rates JSON:API documents (all values invented; never real rates). Shape follows
docs/research/boz-commercial-bank-fx.md: a Drupal tablefield of numbered rows with 13 string cells each plus a `weight`."""

import copy
from uuid import UUID

NODE_UUID = "00000000-0000-4000-8000-0000000000a1"
SLOT_FIRST_CELL = (1, 5, 9)
BLANK = ""

V2_HEADER = [
    ["Bank Name", "9:30", "", "", "", "12:30", "", "", "", "15:30", "", "", ""],
    ["", "INTERBANK", "", "BOARD RATES", "", "INTERBANK", "", "BOARD RATES", "", "INTERBANK", "", "BOARD RATES", ""],
    [""] + ["Buy", "Sell"] * 6,
]
V1_HEADER = [
    ["﻿ 02-01-2030", "9:30", "", "", "", "12:30", "", "", "", "15:30", "", "", ""],
    ["Bank Name", "Interbank Rates", "", "Retail Rates", "", "Interbank Rates", "", "Retail Rates", "", "Interbank Rates", "", "Retail Rates", ""],
    [""] + ["Buy", "Sell"] * 6,
]


def quartet(base: int, scale: int = 4) -> list[str]:
    """Four synthetic rate strings for one slot, e.g. base 10 -> 10.0001, 10.0002, ..."""
    return [f"{base + n / 10 ** scale:.{scale}f}" for n in range(1, 5)]


def bank_row(name: str, slots: tuple[list[str] | None, ...]) -> list[str]:
    cells = [name]
    for slot in slots:
        cells += slot if slot is not None else [BLANK] * 4
    return cells


def default_banks(layout: str = "v2", published: int = 3) -> list[list[str]]:
    names = ("ALPHA TEST BANK PLC    ", "BETA   TEST BANK LIMITED", "MARKET AVERAGE") if layout == "v2" else (
        "ALPHA TEST BANK    ", "BETA TEST BANK", "Market Average")
    rows = []
    for i, name in enumerate(names):
        slots = tuple(quartet(10 + i + s) if s < published else None for s in range(3))
        rows.append(bank_row(name, slots))
    return rows


def table(layout: str = "v2", banks: list[list[str]] | None = None) -> dict:
    header = V2_HEADER if layout == "v2" else V1_HEADER
    rows = [list(r) for r in header] + [list(r) for r in (banks if banks is not None else default_banks(layout))]
    value = {str(i): {**{str(c): cell for c, cell in enumerate(row)}, "weight": str(i if i < 17 else 99)} for i, row in enumerate(rows)}
    value["caption"] = "COMMERCIAL BANK RATES"
    return {"value": value, "format": None, "caption": "COMMERCIAL BANK RATES"}


def node(*, layout: str = "v2", uuid: str = NODE_UUID, nid: int = 11, vid: int = 22, rates_date: str = "2030-01-02T15:36:15+02:00",
         created: str = "2030-01-02T13:36:38+00:00", changed: str = "2030-01-02T13:36:46+00:00", revision: str | None = None,
         title: str = "SYNTHETIC RATES 02 01 30", banks: list[list[str]] | None = None) -> dict:
    return {
        "type": "node--commercial_bank_daily_rates", "id": uuid,
        "attributes": {
            "drupal_internal__nid": nid, "drupal_internal__vid": vid, "title": title, "created": created, "changed": changed,
            "revision_timestamp": revision or changed, "commercial_bank_rates_date": rates_date,
            "field_commercial_bank_rates": table(layout, banks),
        },
    }


def document(*nodes: dict, next_link: str | None = None) -> dict:
    links = {"self": {"href": "https://example.test/self"}}
    if next_link:
        links["next"] = {"href": next_link}
    return {"jsonapi": {"version": "1.1"}, "data": list(nodes), "links": links}


def cell(doc: dict, row: int, column: int, value, *, index: int = 0) -> dict:
    """A deep copy of `doc` with one table cell replaced."""
    out = copy.deepcopy(doc)
    out["data"][index]["attributes"]["field_commercial_bank_rates"]["value"][str(row)][str(column)] = value
    return out


def uuid_of(text: str = NODE_UUID) -> UUID:
    return UUID(text)
