"""Pure reconciliation of ZamStats CPI source evidence into logical Phase 1 observations (no I/O, no storage).

Source evidence (every parsed cell, from every vetted table of every vetted publication) is kept apart from the canonical logical
observation (month, series). For one logical observation:
- all evidence numerically equal: resolved; the owner is the single manifest-pinned canonical table; differing source TEXT of
  an equal value (for example `274.20` and `274.2`) is formatting-only evidence, never a conflict;
- evidence with different Decimal values (within one publication, across publications, or between a canonical and a validation
  column): a CONFLICT. Nothing is chosen, averaged, rounded or repaired; the observation stays unresolved and `require_resolved`
  fails closed;
- evidence from any regime other than 2009=100: rejected outright (old-regime values are never normalised into the new regime).
Rows before the Phase 1 boundary are not part of a logical observation. Messages name months, series and publication keys only.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.zamstats_cpi_models import BOUNDARY_FIRST_MONTH, REGIME, ParsedPublication, SourceObservation, ZamstatsCpiError
from app.services.zamstats_cpi_source import PUBLICATIONS, Publication, canonical_owners


@dataclass(frozen=True)
class Resolved:
    month: date
    series: str
    value: Decimal
    owner: SourceObservation  # the single manifest-pinned canonical source cell
    evidence: tuple[SourceObservation, ...]  # every agreeing cell (owner included), kept as separate evidence
    text_variants: tuple[str, ...]  # distinct source texts when the equal value is printed differently (formatting only)


@dataclass(frozen=True)
class Conflict:
    month: date
    series: str
    evidence: tuple[SourceObservation, ...]  # every disagreeing cell; never collapsed

    @property
    def same_publication(self) -> bool:
        """True when one publication alone prints two different values for the observation."""
        by_key: dict[str, set[Decimal]] = {}
        for o in self.evidence:
            by_key.setdefault(o.manifest_key, set()).add(o.number.value)
        return any(len(values) > 1 for values in by_key.values())


@dataclass(frozen=True)
class Reconciliation:
    resolved: tuple[Resolved, ...]
    conflicts: tuple[Conflict, ...]


def reconcile(parsed: Iterable[ParsedPublication], publications: tuple[Publication, ...] = PUBLICATIONS) -> Reconciliation:
    owners = canonical_owners(publications)
    groups: dict[tuple[date, str], list[SourceObservation]] = {}
    for publication in parsed:
        for observation in publication.observations:
            if observation.regime != REGIME:
                raise ZamstatsCpiError(f"{observation.manifest_key}: evidence is not on the {REGIME} regime")
            if observation.month < BOUNDARY_FIRST_MONTH[observation.metric]:
                continue  # before the Phase 1 boundary: not part of any logical observation
            groups.setdefault((observation.month, observation.series), []).append(observation)
    resolved: list[Resolved] = []
    conflicts: list[Conflict] = []
    for (month, series), evidence in sorted(groups.items()):
        if len({o.number.value for o in evidence}) > 1:
            conflicts.append(Conflict(month, series, tuple(evidence)))
            continue
        pinned = set(owners.get((month, series), []))
        owner_cells = [o for o in evidence if o.canonical_for_table and (o.manifest_key, o.source_table) in pinned]
        if len(owner_cells) != 1:
            raise ZamstatsCpiError(f"{series} {month:%Y-%m}: {len(owner_cells)} canonical source cells, expected exactly 1")
        texts = sorted({o.number.source_value_text for o in evidence})
        resolved.append(Resolved(month, series, owner_cells[0].number.value, owner_cells[0], tuple(evidence),
                                 tuple(texts) if len(texts) > 1 else ()))
    return Reconciliation(tuple(resolved), tuple(conflicts))


def require_resolved(reconciliation: Reconciliation) -> tuple[Resolved, ...]:
    """The resolved observations, or ZamstatsCpiError naming every conflicted (series, month) and its publication keys."""
    if reconciliation.conflicts:
        detail = "; ".join(
            f"{c.series} {c.month:%Y-%m} ({'within ' if c.same_publication else 'across '}publications "
            f"{sorted({o.manifest_key for o in c.evidence})})" for c in reconciliation.conflicts)
        raise ZamstatsCpiError(f"conflicting source cells, canonical resolution unresolved: {detail}")
    return reconciliation.resolved
