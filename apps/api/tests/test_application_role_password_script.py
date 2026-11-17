"""db/init/016_application_role_password.sh: the password must never appear in psql's arguments or in SQL text. Offline: the script is
run with a recording stand-in for `psql`, so no database is needed (the real flow is exercised against PostgreSQL in the scratch test)."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "db/init/016_application_role_password.sh"
PASSWORD = "p'\"w #/:@$\\x`y;--"


@pytest.fixture
def fake_psql(tmp_path):
    shim = tmp_path / "bin" / "psql"
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$REC/argv"\ncat > "$REC/stdin"\nprintf "%s" "${APP_DB_PASSWORD-UNSET}" > "$REC/env"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def run(fake_psql, **env):
    base = {"PATH": f"{fake_psql / 'bin'}:/usr/bin:/bin", "REC": str(fake_psql), "POSTGRES_USER": "admin", "POSTGRES_DB": "db"}
    return subprocess.run(["sh", str(SCRIPT)], env={**base, **env}, capture_output=True, text=True)


def test_password_travels_only_through_the_environment(fake_psql):
    result = run(fake_psql, APP_DB_PASSWORD=PASSWORD, APP_DB_USER="custom_app")
    assert result.returncode == 0, result.stderr
    argv, stdin, seen = ((fake_psql / n).read_text() for n in ("argv", "stdin", "env"))
    assert PASSWORD not in argv and PASSWORD not in stdin and PASSWORD not in result.stdout + result.stderr
    assert "pw=" not in argv and "-v\npw" not in argv  # no `-v pw=...` psql argument
    assert "role=custom_app" in argv  # the role NAME (not a secret) is a variable
    assert seen == PASSWORD  # psql inherits it from the environment ...
    assert "\\getenv pw APP_DB_PASSWORD" in stdin  # ... and reads it itself
    assert "ALTER ROLE :\"role\" PASSWORD :'pw';" in stdin  # identifier and literal quoted by psql, never by string interpolation


def test_script_text_has_no_password_in_any_command_line():
    text = SCRIPT.read_text()
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("-v pw" in line or "pw=" in line for line in code)
    assert "\\getenv pw APP_DB_PASSWORD" in text and "ON_ERROR_STOP=1" in text
    # the only shell expansions of the password are the emptiness check and the export
    assert [l.strip() for l in code if "APP_DB_PASSWORD" in l and not l.strip().startswith("\\getenv")] == [
        ': "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set to initialise the application role}"', "export APP_DB_PASSWORD  # psql inherits it; \\getenv below reads it"]


@pytest.mark.parametrize("value", [None, ""])
def test_empty_or_missing_password_is_rejected_before_psql_runs(fake_psql, value):
    result = run(fake_psql, **({} if value is None else {"APP_DB_PASSWORD": value}))
    assert result.returncode != 0 and "APP_DB_PASSWORD" in result.stderr
    assert not (fake_psql / "argv").exists()
