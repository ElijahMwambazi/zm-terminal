"""Disposable-database helpers for the Bank FX tests (guarded harness only; SYNTHETIC data). Migration 017 is installed in a reset
`public` schema with a throwaway application role that the test creates and drops, so the real zm_terminal_app role is never touched."""

import secrets
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from disposable_db import reset_public
from test_application_role_privileges import drop_role, one

M017 = Path(__file__).parents[3] / "db/init/017_bank_fx.sql"


@contextmanager
def bank_fx_engine(database):
    url, admin = database
    role = "zm_t_bfx_" + secrets.token_hex(4)
    reset_public(admin)
    try:
        with admin.begin() as c:
            c.execute(text(f"CREATE ROLE {role} LOGIN"))
            c.execute(text(f"SET LOCAL zm.app_role = '{role}'"))
            c.execute(text(M017.read_text()))
        yield admin, role
    finally:
        reset_public(admin)
        drop_role(admin, role)


def scalar(connection, sql, **params):
    return one(connection, sql, **params)
