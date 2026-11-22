"""Pure reconciliation of ZamStats CPI source evidence into logical Phase 1 observations (no I/O, no storage).

Three gates are deliberately distinct and none implies another:
  1. manifest coverage      (zamstats_cpi_source.manifest_problems): the committed manifest names exactly one canonical owner for
                            every month of every series; it says nothing about what has been parsed;
  2. parsed-evidence completeness (validate_evidence_completeness): CELL-level. Every source cell the manifest requires for the
                            requested scope (owner AND validation overlaps: manifest key, table, metric, category, month) exists
                            exactly once in the supplied evidence with the right provenance. Presence of a table is not enough:
                            dropping only the validation cell that would reveal a disagreement must fail, not look resolved;
  3. canonical resolution   (require_resolved / require_canonical_ready): no conflicting source cells remain.
`reconcile` alone is a diagnostic over whatever evidence it is given (a partial reconciliation): reconcile != canonical readiness. A
result that is numerically unconflicted in a partial batch is NOT evidence that it is canonical-ready; only `require_canonical_ready`
proves completeness AND resolution for a scope, in this fixed order: validate the scope, validate cell-level completeness,
reconcile, require resolution.

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


EvidenceCell = tuple  # (manifest key, table id, metric, category, first-of-month date): the stable identity of one source cell


def _check_scope(scope: Scope, publications: tuple[Publication, ...]) -> None:
    if not scope:
        raise ZamstatsCpiError("the requested scope is empty")
    outside = sorted(set(scope) - manifest_scope(publications))
    if outside:
        raise ZamstatsCpiError("the requested scope contains observations the manifest does not own: "
                               + "; ".join(f"{name} {month:%Y-%m}" for name, month in outside[:10]))


def required_evidence_cells(scope: Scope, publications: tuple[Publication, ...] = PUBLICATIONS) -> frozenset:
    """Every source cell that must be present to establish readiness for `scope`: for each manifest table whose printed extent
    contains a requested month and which prints that series, the cell (key, table, metric, category, month). That includes the
    canonical owner cells AND every validation overlap, so any disagreement the manifest could expose can be seen. Derived purely
    from the manifest's tables, extents and columns (never from what happened to parse, and never from hard-coded months)."""
    by_series: dict[str, set[date]] = {}
    for name, month in scope:
        by_series.setdefault(name, set()).add(month)
    cells = set()
    for publication in publications:
        for table in publication.tables:
            for metric, category, _canonical in table.columns:
                for month in by_series.get(series_name(metric, category), ()):
                    if table.first_month <= month <= table.last_month:
                        cells.add((publication.key, table.table_id, metric, category, month))
    return frozenset(cells)


def required_evidence(scope: Scope, publications: tuple[Publication, ...] = PUBLICATIONS) -> frozenset:
    """The (publication key, table id) pairs that hold at least one required cell. Informational; completeness itself is
    cell-level (`required_evidence_cells`)."""
    return frozenset((key, table) for key, table, _metric, _category, _month in required_evidence_cells(scope, publications))


def _describe(cells) -> str:
    """Compact, value-free description of cells: 'key / table series months...'."""
    groups: dict[tuple[str, str, str], list[date]] = {}
    for key, table, metric, category, month in cells:
        groups.setdefault((key, table, series_name(metric, category)), []).append(month)
    parts = []
    for (key, table, name), months in sorted(groups.items()):
        shown = ", ".join(f"{m:%Y-%m}" for m in sorted(months)[:6]) + (f" (+{len(months) - 6} more)" if len(months) > 6 else "")
        parts.append(f"{key} / {table} {name} {shown}")
    return "; ".join(parts)


def validate_evidence_completeness(
    parsed: Iterable[ParsedPublication], scope: Scope | None = None, publications: tuple[Publication, ...] = PUBLICATIONS,
) -> frozenset:
    """Cell-level proof that the supplied evidence contains every manifest-required source cell for `scope` (default: the whole
    manifest scope). Each required cell must exist exactly once and with the right provenance (manifest key, table, page, layout
    family, canonical role and the pinned metric/category/month). Missing, duplicated or mis-attributed cells fail; a table being
    present is not enough. Completeness does NOT imply resolution. Returns the required cells. Messages name keys, tables, series
    and months only."""
    requested = manifest_scope(publications) if scope is None else frozenset(scope)
    _check_scope(requested, publications)
    known = {(p.key, t.table_id): t for p in publications for t in p.tables}
    counts: dict[tuple, int] = {}
    for publication in parsed:
        for table in publication.tables:
            spec = known.get((publication.manifest_key, table.source_table))
            if spec is None:
                raise ZamstatsCpiError(f"{publication.manifest_key}: supplied table '{table.source_table}' is not on the manifest")
            if (table.pdf_page, table.layout_family, table.first_month, table.last_month) != (
                    spec.pdf_page, spec.family, spec.first_month, spec.last_month):
                raise ZamstatsCpiError(f"{publication.manifest_key}: supplied table '{table.source_table}' does not match the manifest")
            roles = {(m, c): canonical for m, c, canonical in spec.columns}
            for o in table.observations:
                if (o.manifest_key, o.source_table, o.pdf_page, o.layout_family) != (
                        publication.manifest_key, table.source_table, spec.pdf_page, spec.family):
                    raise ZamstatsCpiError(
                        f"{publication.manifest_key}: an observation of '{table.source_table}' carries other provenance")
                if (o.metric, o.category) not in roles or o.canonical_for_table != roles[(o.metric, o.category)]:
                    raise ZamstatsCpiError(
                        f"{publication.manifest_key}: an observation of '{table.source_table}' does not match the table's columns")
                identity = (o.manifest_key, o.source_table, o.metric, o.category, o.month)
                counts[identity] = counts.get(identity, 0) + 1
    required = required_evidence_cells(requested, publications)
    missing = sorted(c for c in required if counts.get(c, 0) == 0)
    if missing:
        raise ZamstatsCpiError("parsed evidence is incomplete for the requested scope; missing: " + _describe(missing))
    duplicated = sorted(c for c in required if counts.get(c, 0) > 1)
    if duplicated:
        raise ZamstatsCpiError("parsed evidence repeats required source cells: " + _describe(duplicated))
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
    _check_scope(requested, publications)  # 1. the requested scope is valid
    validate_evidence_completeness(parsed, requested, publications)  # 2. every required source cell was supplied
    result = reconcile(parsed, publications)  # 3. reconcile (only reachable with complete evidence)
    # 4. require resolution
    conflicted = [c for c in result.conflicts if (c.series, c.month) in requested]
    if conflicted:
        require_resolved(Reconciliation((), tuple(conflicted)))
    resolved = {(r.series, r.month): r for r in result.resolved}
    unresolved = sorted(key for key in requested if key not in resolved)
    if unresolved:
        raise ZamstatsCpiError("observations are unresolved: " + "; ".join(f"{s} {m:%Y-%m}" for s, m in unresolved[:20]))
    return tuple(resolved[key] for key in sorted(requested, key=lambda k: (k[0], k[1])))
