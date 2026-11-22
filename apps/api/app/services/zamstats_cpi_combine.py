"""Pure reconciliation of ZamStats CPI source evidence into logical Phase 1 observations (no I/O, no storage).

Three gates are deliberately distinct and none implies another:
  1. manifest coverage      (zamstats_cpi_source.manifest_problems): the committed manifest names exactly one canonical owner for
                            every month of every series; it says nothing about what has been parsed;
  2. parsed-evidence completeness (validate_evidence_completeness): every manifest table whose printed extent touches the requested
                            scope (owner AND validation overlaps) was actually parsed and supplied. Static ownership is not enough:
                            an observation can look resolved until the later publication that contradicts it is supplied;
  3. canonical resolution   (require_resolved / require_canonical_ready): no conflicting source cells remain.
`reconcile` alone is a diagnostic over whatever evidence it is given (a partial reconciliation) and is never canonical-ready;
only `require_canonical_ready` proves completeness AND resolution for a scope.

Source evidence (every parsed cell, from every vetted table of every vetted publication) is kept apart from the canonical logical
observation (month, series). For one logical observation:
- all evidence numerically equal: resolved; the owner is the single manifest-pinned canonical table; differing source TEXT of
  an equal value (for example `274.20` and `274.2`) is formatting-only evidence, never a conflict;
- evidence with different Decimal values (within one publication, across publications, or between a canonical and a validation
  column): a CONFLICT. Nothing is chosen, averaged, rounded or repaired; the observation stays unresolved and the gates fail closed;
- evidence from any regime other than 2009=100: rejected outright (old-regime values are never normalised into the new regime).
Rows before the Phase 1 boundary are not part of a logical observation. Messages name months, series and publication keys only.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.zamstats_cpi_models import (
    BOUNDARY_FIRST_MONTH, REGIME, ParsedPublication, SourceObservation, ZamstatsCpiError, series_name,
)
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
    """A reconciliation of whatever evidence was supplied. Diagnostic: it does not prove the evidence is complete."""
    resolved: tuple[Resolved, ...]
    conflicts: tuple[Conflict, ...]
    unowned: tuple[tuple[date, str], ...] = ()  # agreeing evidence whose manifest-pinned owner table was not supplied


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
    unowned: list[tuple[date, str]] = []
    for (month, series), evidence in sorted(groups.items()):
        if len({o.number.value for o in evidence}) > 1:
            conflicts.append(Conflict(month, series, tuple(evidence)))
            continue
        pinned = set(owners.get((month, series), []))
        owner_cells = [o for o in evidence if o.canonical_for_table and (o.manifest_key, o.source_table) in pinned]
        if not owner_cells:
            unowned.append((month, series))  # the owner table was not supplied: never resolved, never guessed
            continue
        if len(owner_cells) != 1:
            raise ZamstatsCpiError(f"{series} {month:%Y-%m}: {len(owner_cells)} canonical source cells, expected exactly 1")
        texts = sorted({o.number.source_value_text for o in evidence})
        resolved.append(Resolved(month, series, owner_cells[0].number.value, owner_cells[0], tuple(evidence),
                                 tuple(texts) if len(texts) > 1 else ()))
    return Reconciliation(tuple(resolved), tuple(conflicts), tuple(unowned))


Scope = frozenset  # of (series name, first-of-month date)


def manifest_scope(publications: tuple[Publication, ...] = PUBLICATIONS) -> Scope:
    """Every logical observation the manifest pins an owner for."""
    return frozenset((series, month) for (month, series) in canonical_owners(publications))


def scope_for(series: Iterable[str] | None = None, first: date | None = None, last: date | None = None,
              publications: tuple[Publication, ...] = PUBLICATIONS) -> Scope:
    """A sub-scope of the manifest: optional series names and an optional inclusive month window."""
    wanted = None if series is None else set(series)
    return frozenset(
        (name, month) for (name, month) in manifest_scope(publications)
        if (wanted is None or name in wanted) and (first is None or month >= first) and (last is None or month <= last))


def required_evidence(scope: Scope, publications: tuple[Publication, ...] = PUBLICATIONS) -> frozenset:
    """The (publication key, table id) pairs whose printed extent touches the scope for a series they print: owners and validation
    overlaps alike. Driven by the manifest, not by hard-coded month lists; tables outside the scope are not required."""
    by_series: dict[str, set[date]] = {}
    for name, month in scope:
        by_series.setdefault(name, set()).add(month)
    required = set()
    for publication in publications:
        for table in publication.tables:
            for metric, category, _canonical in table.columns:
                months = by_series.get(series_name(metric, category), ())
                if any(table.first_month <= m <= table.last_month for m in months):
                    required.add((publication.key, table.table_id))
    return frozenset(required)


def validate_evidence_completeness(
    parsed: Iterable[ParsedPublication], scope: Scope | None = None, publications: tuple[Publication, ...] = PUBLICATIONS,
) -> frozenset:
    """Prove that every manifest table required for `scope` (default: the whole manifest scope) was parsed and supplied, and that
    each supplied table is the manifest's table (page, family and printed extent). Returns the required (key, table) pairs.
    Completeness does NOT imply resolution. Messages name publication keys and tables only."""
    requested = manifest_scope(publications) if scope is None else frozenset(scope)
    if not requested:
        raise ZamstatsCpiError("the requested scope is empty")
    known = {(p.key, t.table_id): t for p in publications for t in p.tables}
    supplied: set[tuple[str, str]] = set()
    for publication in parsed:
        for table in publication.tables:
            spec = known.get((publication.manifest_key, table.source_table))
            if spec is None:
                raise ZamstatsCpiError(f"{publication.manifest_key}: supplied table '{table.source_table}' is not on the manifest")
            if (table.pdf_page, table.layout_family, table.first_month, table.last_month) != (
                    spec.pdf_page, spec.family, spec.first_month, spec.last_month):
                raise ZamstatsCpiError(f"{publication.manifest_key}: supplied table '{table.source_table}' does not match the manifest")
            supplied.add((publication.manifest_key, table.source_table))
    required = required_evidence(requested, publications)
    missing = sorted(required - supplied)
    if missing:
        raise ZamstatsCpiError(
            "parsed evidence is incomplete for the requested scope; missing: " + "; ".join(f"{key} / {table}" for key, table in missing))
    return required


def require_resolved(reconciliation: Reconciliation) -> tuple[Resolved, ...]:
    """The resolved observations of a reconciliation, or ZamstatsCpiError naming every conflicted (series, month) and its
    publication keys. An empty reconciliation is not 'resolved'. This gate says nothing about evidence completeness."""
    if reconciliation.conflicts:
        detail = "; ".join(
            f"{c.series} {c.month:%Y-%m} ({'within ' if c.same_publication else 'across '}publications "
            f"{sorted({o.manifest_key for o in c.evidence})})" for c in reconciliation.conflicts)
        raise ZamstatsCpiError(f"conflicting source cells, canonical resolution unresolved: {detail}")
    if not reconciliation.resolved:
        raise ZamstatsCpiError("nothing was resolved: no evidence was supplied")
    return reconciliation.resolved


def require_canonical_ready(
    parsed: Iterable[ParsedPublication], scope: Scope | None = None, publications: tuple[Publication, ...] = PUBLICATIONS,
) -> tuple[Resolved, ...]:
    """Completeness AND resolution for `scope`: every required manifest table was supplied and every logical observation in the
    scope is resolved with no conflicting cell. Conflicts outside the scope do not block it. Returns the scope's resolved
    observations."""
    parsed = list(parsed)
    requested = manifest_scope(publications) if scope is None else frozenset(scope)
    validate_evidence_completeness(parsed, requested, publications)
    result = reconcile(parsed, publications)
    conflicted = [c for c in result.conflicts if (c.series, c.month) in requested]
    if conflicted:
        require_resolved(Reconciliation((), tuple(conflicted)))
    resolved = {(r.series, r.month): r for r in result.resolved}
    unresolved = sorted(key for key in requested if key not in resolved)
    if unresolved:
        raise ZamstatsCpiError("observations are unresolved: " + "; ".join(f"{s} {m:%Y-%m}" for s, m in unresolved[:20]))
    return tuple(resolved[key] for key in sorted(requested, key=lambda k: (k[0], k[1])))
