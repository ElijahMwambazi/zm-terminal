"""Combine the monthly rows of several vetted publications into one deterministic set (pure functions, no I/O).

Overlapping publications repeat historical months. Every repeat is compared with Decimal numeric equality (so 20.1 equals
20.10) across buy, mid and sell; any disagreement fails the whole import and nothing is chosen silently. The single stored
row's provenance is the EARLIEST publication in the vetted manifest order that contains the month, so later issues repeating
unchanged values never move a row's provenance. Missing months are reported, never interpolated or forward-filled.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from app.services.boz_monthly_source import Publication
from app.services.boz_monthly_table import ParsedTable

MAX_CONFLICTS_SHOWN = 5


class MonthlyConflictError(Exception):
    """Overlapping publications (or a stored month) disagree. Names months and publications only, never a rate."""

    def __init__(self, count: int, identities: list[str]):
        shown = identities[:MAX_CONFLICTS_SHOWN]
        detail = ": " + ", ".join(shown)
        if len(identities) > len(shown):
            detail += f" ... and {len(identities) - len(shown)} more"
        super().__init__(f"{count} monthly average(s) conflict{detail}; nothing was written")
        self.count = count
        self.identities = list(identities)


@dataclass(frozen=True)
class MonthlyObservation:
    month: date
    buy: Decimal
    mid: Decimal
    sell: Decimal
    publication_title: str
    source_url: str
    source_page: int


@dataclass(frozen=True)
class Combined:
    observations: tuple[MonthlyObservation, ...]  # ascending by month, one per month
    overlaps_checked: int  # repeat occurrences compared against the canonical row


def combine(parsed: Sequence[tuple[Publication, ParsedTable]]) -> Combined:
    """`parsed` must be in vetted manifest (chronological) order; the first occurrence of a month is canonical."""
    chosen: dict[date, MonthlyObservation] = {}
    overlaps = 0
    conflicts: list[str] = []
    for publication, table in parsed:
        for rate in table.rows:
            first = chosen.get(rate.month)
            if first is None:
                chosen[rate.month] = MonthlyObservation(
                    month=rate.month, buy=rate.buy, mid=rate.mid, sell=rate.sell,
                    publication_title=publication.title, source_url=publication.url, source_page=table.page,
                )
                continue
            overlaps += 1
            if (first.buy, first.mid, first.sell) != (rate.buy, rate.mid, rate.sell):
                conflicts.append(f"{rate.month:%Y-%m} ({first.publication_title} vs {publication.title})")
    if conflicts:
        raise MonthlyConflictError(len(conflicts), sorted(conflicts))
    return Combined(tuple(chosen[m] for m in sorted(chosen)), overlaps)


def month_range(first: date, last: date) -> list[date]:
    months, cursor = [], first
    while cursor <= last:
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return months


def find_gaps(observations: Sequence[MonthlyObservation], first: date, last: date) -> list[date]:
    """Calendar months in first..last that no publication supplied."""
    have = {o.month for o in observations}
    return [m for m in month_range(first, last) if m not in have]
