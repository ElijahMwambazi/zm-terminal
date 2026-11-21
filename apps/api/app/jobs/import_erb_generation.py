"""Manual, explicit import of the vetted ERB 2025 Annual Statistical Bulletin (Energy generation by station) from a LOCAL PDF file.

The job never touches the network: it reads only the file given by --pdf, verifies it against the pinned size and SHA-256, parses it
and runs every pre-persistence validation before any database access. The path is operational input only (never stored as provenance),
raw PDF bytes are never stored and only counts and statuses are printed. Exactly one action is required:

  --dry-run             verify, parse and validate; never opens PostgreSQL (works without migration 014)
  --register-candidate  record the vetted bulletin as a PENDING source candidate (idempotent); does not import
  --import              atomically import it (requires that pending candidate); an identical re-run reports already_present

The intended workflow is dry-run -> register-candidate -> import. Persistence runs through the normal application engine
(app.db.get_engine, the limited application role) after the admin has applied migration 014/015; the job never migrates or escalates.
Rights status stays review_required (local/internal use only).
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.db import get_engine
from app.services.energy_generation_store import (
    EnergyCandidateError, EnergyImportConflictError, EnergySchemaNotMigratedError, import_erb_2025, register_vetted_candidate,
    validate_parse_result,
)
from app.services.erb_generation_models import ErbGenerationError, ParseResult
from app.services.erb_generation_source import BULLETIN_2025, parse_bulletin

DRY_RUN, REGISTER, IMPORT = "dry-run", "register-candidate", "import"
SAFE_ERRORS = (ErbGenerationError, EnergySchemaNotMigratedError, EnergyCandidateError, EnergyImportConflictError)  # messages carry no values


class JobError(Exception):
    pass


def read_pdf(path: Path) -> bytes:
    """Bytes of an existing regular file no larger than the manifest allows (the exact pinned size is verified by the source layer)."""
    try:
        if not path.is_file():
            raise JobError("--pdf must be an existing regular file")
        if path.stat().st_size > BULLETIN_2025.max_bytes:
            raise JobError("bulletin: file is larger than expected")
        return path.read_bytes()
    except OSError as exc:
        raise JobError(f"cannot read the PDF ({type(exc).__name__})") from exc


def verify_and_parse(pdf_bytes: bytes) -> ParseResult:
    """Existing source verification (size, %PDF, SHA-256), the pinned parser and the importer's pre-persistence validation."""
    result = parse_bulletin(pdf_bytes)
    validate_parse_result(result)
    return result


def run_import_job(action: str, pdf_path: Path, *, engine=None, now: datetime | None = None) -> dict[str, object]:
    """Everything before the database (file, verification, parsing, validation) runs first; the engine is only obtained for the
    persistence actions, so --dry-run can never open PostgreSQL."""
    now = now or datetime.now(timezone.utc)
    result = verify_and_parse(read_pdf(pdf_path))
    if action == DRY_RUN:
        stations = {(o.station_group, o.station_name) for o in result.observations}
        return {"publication_year": result.publication_year, "observations": len(result.observations),
                "totals": len(result.table_totals), "stations": len(stations), "status": "valid"}
    engine = engine or get_engine()
    if action == REGISTER:
        candidate = register_vetted_candidate(engine, discovered_at=now)
        return {"status": "pending" if candidate.created else "already_registered", "created": str(candidate.created).lower()}
    if action == IMPORT:
        r = import_erb_2025(engine, result, retrieved_at=now)
        return {"status": r.status, "publication_revision": r.revision_sequence, "import_sequence": r.import_sequence,
                "observations_inserted": r.observations_inserted, "totals_inserted": r.totals_inserted,
                "stations_inserted": r.stations_inserted, "stations_reused": r.stations_reused}
    raise JobError(f"unknown action {action!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.import_erb_generation", description=__doc__.split("\n")[0])
    parser.add_argument("--pdf", required=True, type=Path, help="local copy of the vetted bulletin (never downloaded by this job)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", dest="action", action="store_const", const=DRY_RUN, help="verify and parse only; never opens PostgreSQL")
    group.add_argument("--register-candidate", dest="action", action="store_const", const=REGISTER,
                       help="record the vetted bulletin as a pending source candidate")
    group.add_argument("--import", dest="action", action="store_const", const=IMPORT, help="atomically import (needs the pending candidate)")
    args = parser.parse_args(argv)
    try:
        result = run_import_job(args.action, args.pdf)
    except (JobError, *SAFE_ERRORS) as exc:
        print(f"{args.action} failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # database etc.; never print connection strings
        print(f"{args.action} failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(args.action + " " + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
