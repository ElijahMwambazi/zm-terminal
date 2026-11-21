"""Runtime database configuration: one authoritative source, never the admin role, no secrets in errors. Offline."""

import pytest

from app.config import Settings


def settings(**kw):
    return Settings(_env_file=None, **kw)


def test_discrete_settings_build_the_url_and_escape_the_password():
    url = settings(app_db_password="p@ss/w:rd#?%", app_db_host="db", app_db_port=5433, app_db_name="zm").runtime_database_url
    assert (url.drivername, url.username, url.password, url.host, url.port, url.database) == \
        ("postgresql+psycopg", "zm_terminal_app", "p@ss/w:rd#?%", "db", 5433, "zm")
    assert "p@ss" not in repr(url) and "***" in repr(url)  # SQLAlchemy masks the password in reprs/logs


def test_no_password_default_and_no_silent_admin_fallback():
    with pytest.raises(ValueError, match="APP_DB_PASSWORD is not set"):
        settings().runtime_database_url


def test_database_url_overrides_discrete_settings_but_never_the_admin_role():
    override = "postgresql+psycopg://custom_app:pw@h:5432/d"
    assert settings(app_db_password="x", database_url=override).runtime_database_url.username == "custom_app"
    for bad in ("postgresql+psycopg://zm_terminal:secretvalue@h/d",):
        with pytest.raises(ValueError, match="not the admin role") as err:
            settings(database_url=bad).runtime_database_url
        assert "secretvalue" not in str(err.value)
    with pytest.raises(ValueError, match="not the admin role"):  # also when APP_DB_USER itself is set to the admin name
        settings(app_db_user="zm_terminal", app_db_password="x").runtime_database_url
    with pytest.raises(ValueError, match="not the admin role"):  # and when the configured admin name is customised
        settings(postgres_user="boss", app_db_user="boss", app_db_password="x").runtime_database_url


def test_malformed_database_url_error_does_not_leak_it():
    with pytest.raises(ValueError, match="not a valid database URL") as err:
        settings(database_url="not a url but secretvalue").runtime_database_url
    assert "secretvalue" not in str(err.value)


def test_blank_database_url_falls_back_to_discrete_settings():
    assert settings(database_url="  ", app_db_password="x").runtime_database_url.username == "zm_terminal_app"
