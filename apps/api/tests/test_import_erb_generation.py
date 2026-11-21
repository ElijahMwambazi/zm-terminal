"""ERB generation import job (CLI around the merged parser and storage importer). SYNTHETIC only: the committed fixture pages stand in for the
parsed bulletin (job.parse_bulletin is replaced where a valid parse is needed); changed bytes are rejected by the REAL pinned verification.
No network, no real PDF. The PostgreSQL test runs in the guarded disposable database."""

import dataclasses
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.jobs import import_erb_generation as job
from app.services import energy_generation_store as store
from app.services import erb_generation_source as src
from disposable_db import reset_public
from test_erb_generation import pages, parse

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
M014 = Path(__file__).parents[3] / "db/init/014_energy_generation_vintages.sql"
SECRET = "postgresql+psycopg://zm_terminal_app:SECRETPW@db.invalid:5432/zm_terminal"


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "statBullet2025.pdf"
    path.write_bytes(b"%PDF-1.7 synthetic stand-in")  # never matches the pinned SHA; only used where parsing is replaced
    return path


@pytest.fixture
def valid_parse(monkeypatch):
    monkeypatch.setattr(job, "parse_bulletin", lambda data: src.parse_pages(pages()))


@pytest.fixture
def no_engine(monkeypatch):
    def boom():
        raise AssertionError("the database must not be opened")
    monkeypatch.setattr(job, "get_engine", boom)


def cli(args, capsys):
    code = job.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


# ------------------------------------------------------------------------------------------------------- CLI shape and files
def test_exactly_one_action_is_required(pdf, capsys):
    for args in ([], ["--pdf", str(pdf)], ["--pdf", str(pdf), "--dry-run", "--import"], ["--pdf", str(pdf), "--register-candidate", "--import"],
                 ["--dry-run"]):
        with pytest.raises(SystemExit) as exit_:
            job.main(args)
        assert exit_.value.code == 2
    capsys.readouterr()


def test_missing_or_non_regular_pdf_fails_without_a_database(tmp_path, capsys, no_engine):
    for path in (tmp_path / "absent.pdf", tmp_path):
        code, out, err = cli(["--pdf", str(path), "--dry-run"], capsys)
        assert code == 1 and out == "" and "existing regular file" in err


def test_changed_bytes_fail_the_real_pinned_verification_before_any_database_access(pdf, capsys, no_engine):
    for action in ("--dry-run", "--register-candidate", "--import"):
        code, out, err = cli(["--pdf", str(pdf), action], capsys)  # real parse_bulletin: wrong size / SHA-256
        assert code == 1 and out == "" and "bulletin:" in err
    pdf.write_bytes(b"%PDF-" + b"x" * (src.BULLETIN_2025.size_bytes - 5))  # right size, wrong SHA-256
    code, _out, err = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 1 and "SHA-256" in err
    pdf.write_bytes(b"x" * (src.BULLETIN_2025.max_bytes + 1))
    code, _out, err = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 1 and "larger than expected" in err


def test_the_job_has_no_network_code():
    source = Path(job.__file__).read_text()
    assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|socket|aiohttp)", source, re.M)
    assert "fetch_bulletin" not in source


# --------------------------------------------------------------------------------------------------------------------- dry run
def test_dry_run_reports_counts_and_never_opens_the_database(pdf, valid_parse, no_engine, capsys):
    code, out, err = cli(["--pdf", str(pdf), "--dry-run"], capsys)
    assert (code, err) == (0, "")
    assert out == "dry-run publication_year=2025 observations=390 totals=52 stations=30 status=valid\n"


def test_dry_run_still_runs_the_importers_pre_persistence_validation(pdf, monkeypatch, no_engine, capsys):
    broken = dataclasses.replace(parse(), observations=parse().observations[:-1])
    monkeypatch.setattr(job, "parse_bulletin", lambda data: broken)
    code, out, err = cli(["--pdf", str(pdf), "--dry-run"], capsys)
    assert code == 1 and out == "" and "key set differs" in err


# ------------------------------------------------------------------------------------------------- register / import delegation
def test_register_validates_first_then_delegates_to_register_vetted_candidate(pdf, valid_parse, monkeypatch, capsys):
    calls = []
    engine = object()
    monkeypatch.setattr(job, "get_engine", lambda: calls.append("engine") or engine)
    monkeypatch.setattr(job, "register_vetted_candidate", lambda e, *, discovered_at: calls.append(("register", e, discovered_at))
                        or store.CandidateResult(7, True))
    monkeypatch.setattr(job, "import_erb_2025", lambda *a, **k: pytest.fail("registration must not import"))
    code, out, _ = cli(["--pdf", str(pdf), "--register-candidate"], capsys)
    assert code == 0 and out == "register-candidate status=pending created=true\n"
    assert calls[0] == "engine" and calls[1][0] == "register" and calls[1][1] is engine and calls[1][2].tzinfo is timezone.utc
    assert "7" not in out  # no internal ids


def test_register_is_idempotent_in_its_report(pdf, valid_parse, monkeypatch, capsys):
    monkeypatch.setattr(job, "get_engine", lambda: object())
    monkeypatch.setattr(job, "register_vetted_candidate", lambda e, *, discovered_at: store.CandidateResult(7, False))
    code, out, _ = cli(["--pdf", str(pdf), "--register-candidate"], capsys)
    assert code == 0 and out == "register-candidate status=already_registered created=false\n"


def test_register_validates_before_database_access(pdf, monkeypatch, no_engine, capsys):
    broken = dataclasses.replace(parse(), source_sha256="0" * 64)
    monkeypatch.setattr(job, "parse_bulletin", lambda data: broken)
    code, _out, err = cli(["--pdf", str(pdf), "--register-candidate"], capsys)
    assert code == 1 and "vetted bulletin identity" in err


def imported(**over):
    base = dict(status=store.IMPORTED, publication_id=3, revision_sequence=1, import_id=9, import_sequence=1, observations_inserted=390,
                totals_inserted=52, stations_inserted=30, stations_reused=0)
    return store.ImportResult(**{**base, **over})


def test_import_delegates_to_import_erb_2025_and_never_registers_a_candidate(pdf, valid_parse, monkeypatch, capsys):
    seen = {}
    engine = object()
    monkeypatch.setattr(job, "get_engine", lambda: engine)
    monkeypatch.setattr(job, "register_vetted_candidate", lambda *a, **k: pytest.fail("import must not register a candidate"))

    def fake(e, result, *, retrieved_at):
        seen.update(engine=e, result=result, at=retrieved_at)
        return imported()
    monkeypatch.setattr(job, "import_erb_2025", fake)
    code, out, _ = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 0 and seen["engine"] is engine and seen["result"].publication_year == 2025 and seen["at"].tzinfo is timezone.utc
    assert out == ("import status=imported publication_revision=1 import_sequence=1 observations_inserted=390 totals_inserted=52 "
                   "stations_inserted=30 stations_reused=0\n")


def test_already_present_result_is_reported_as_a_noop(pdf, valid_parse, monkeypatch, capsys):
    monkeypatch.setattr(job, "get_engine", lambda: object())
    monkeypatch.setattr(job, "import_erb_2025", lambda e, r, *, retrieved_at: imported(
        status=store.ALREADY_PRESENT, observations_inserted=0, totals_inserted=0, stations_inserted=0))
    code, out, _ = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 0 and out.startswith("import status=already_present ") and "observations_inserted=0 totals_inserted=0" in out


def test_normal_output_never_contains_values_or_pdf_text(pdf, valid_parse, monkeypatch, capsys):
    monkeypatch.setattr(job, "get_engine", lambda: object())
    monkeypatch.setattr(job, "import_erb_2025", lambda e, r, *, retrieved_at: imported())
    page_text = "".join(pages().values())
    values = {str(o.generation_gwh) for o in parse().observations if o.generation_gwh} | {str(t.published_total_gwh) for t in parse().table_totals}
    for args in (["--dry-run"], ["--import"]):
        _code, out, err = cli(["--pdf", str(pdf), *args], capsys)
        text_out = out + err
        assert "Kafue Gorge" not in text_out and "Table 13" not in text_out and str(pdf) not in text_out
        assert not any(re.search(rf"(?<![\w.]){re.escape(v)}(?![\w.])", text_out) for v in values if "." in v)
        assert page_text[:40] not in text_out


# ------------------------------------------------------------------------------------------------------------- error handling
@pytest.mark.parametrize("error,needle", [
    (store.EnergySchemaNotMigratedError("Energy schema not migrated: migration 014 is incomplete (x); apply it first. Nothing was written"),
     "Energy schema not migrated"),
    (store.EnergyCandidateError("no source candidate is registered for the vetted bulletin; register_vetted_candidate first"), "candidate"),
    (store.EnergyImportConflictError("the same publication and SHA-256 produce different stable output; review required"), "review required"),
])
def test_service_errors_return_nonzero_with_their_safe_message(pdf, valid_parse, monkeypatch, capsys, error, needle):
    monkeypatch.setattr(job, "get_engine", lambda: object())

    def fail(*a, **k):
        raise error
    monkeypatch.setattr(job, "import_erb_2025", fail)
    monkeypatch.setattr(job, "register_vetted_candidate", fail)
    for action in ("--import", "--register-candidate"):
        code, out, err = cli(["--pdf", str(pdf), action], capsys)
        assert code == 1 and out == "" and needle in err


def test_generic_database_errors_print_only_the_exception_type(pdf, valid_parse, monkeypatch, capsys):
    monkeypatch.setattr(job, "get_engine", lambda: object())

    def fail(*a, **k):
        raise RuntimeError(f"could not connect to {SECRET} password=SECRETPW")
    monkeypatch.setattr(job, "import_erb_2025", fail)
    monkeypatch.setattr(job, "register_vetted_candidate", fail)
    for action in ("--import", "--register-candidate"):
        code, out, err = cli(["--pdf", str(pdf), action], capsys)
        assert code == 1 and out == "" and err.strip() == f"{action.lstrip('-')} failed: RuntimeError"
        assert "SECRET" not in err and "db.invalid" not in err
    def engine_fails():
        raise ValueError(SECRET)
    monkeypatch.setattr(job, "get_engine", engine_fails)
    code, _out, err = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 1 and "SECRET" not in err and "ValueError" in err


def test_module_runs_as_a_script_and_requires_an_action():
    done = subprocess.run([sys.executable, "-m", "app.jobs.import_erb_generation", "--pdf", "x.pdf"], capture_output=True, text=True,
                          cwd=Path(__file__).parents[1], timeout=60)
    assert done.returncode == 2 and "one of the arguments --dry-run --register-candidate --import is required" in done.stderr


# ---------------------------------------------------------------------------------------- PostgreSQL: the real workflow end to end
@pytest.fixture
def engine(disposable_database):
    _url, eng = disposable_database
    reset_public(eng)
    with eng.begin() as c:
        c.execute(text(M014.read_text()))
    return eng


def test_workflow_through_the_application_engine_path(pdf, valid_parse, engine, monkeypatch, capsys):
    monkeypatch.setattr(job, "get_engine", lambda: engine)  # the job's only route to PostgreSQL
    code, out, err = cli(["--pdf", str(pdf), "--import"], capsys)  # no candidate yet: refused, nothing written
    assert code == 1 and "candidate" in err
    assert engine.connect().execute(text("SELECT count(*) FROM public.energy_publications")).scalar() == 0
    assert cli(["--pdf", str(pdf), "--register-candidate"], capsys)[:2] == (0, "register-candidate status=pending created=true\n")
    assert cli(["--pdf", str(pdf), "--register-candidate"], capsys)[:2] == (0, "register-candidate status=already_registered created=false\n")
    assert engine.connect().execute(text("SELECT count(*) FROM public.energy_publications")).scalar() == 0  # registering imports nothing
    code, out, _ = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 0 and "status=imported" in out and "observations_inserted=390 totals_inserted=52 stations_inserted=30" in out
    code, out, _ = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 0 and "status=already_present" in out and "observations_inserted=0 totals_inserted=0" in out
    with engine.connect() as c:
        assert c.execute(text("SELECT (SELECT count(*) FROM public.energy_generation_imports), "
                              "(SELECT count(*) FROM public.energy_generation_observations)")).one() == (1, 390)


def test_dry_run_works_without_migration_014(pdf, valid_parse, disposable_database, monkeypatch, capsys):
    _url, eng = disposable_database
    reset_public(eng)  # no Energy schema at all
    monkeypatch.setattr(job, "get_engine", lambda: pytest.fail("dry-run must not open PostgreSQL"))
    assert cli(["--pdf", str(pdf), "--dry-run"], capsys)[0] == 0
    monkeypatch.setattr(job, "get_engine", lambda: eng)
    code, out, err = cli(["--pdf", str(pdf), "--import"], capsys)
    assert code == 1 and out == "" and "Energy schema not migrated" in err
