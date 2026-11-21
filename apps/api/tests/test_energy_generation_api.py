"""Stored-only Energy generation API. All data is SYNTHETIC (invented values, invented station names for vintages); no ERB value is used.
PostgreSQL tests run in the guarded disposable session database with `public` reset to migration 014 before each test."""

import json
import re
import socket
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.main import app
from app.schemas import energy as schemas
from app.services import energy_generation_read as read
from disposable_db import reset_public

M014 = Path(__file__).parents[3] / "db/init/014_energy_generation_vintages.sql"
SERIES = "erb_statistical_bulletin"
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
D = Decimal
LARGE, MINI, DIESEL, IPP = schemas.ENERGY_STATION_GROUPS
ENERGY_TABLES = ("energy_publication_candidates", "energy_publications", "energy_generation_imports", "energy_source_stations",
                 "energy_generation_observations", "energy_generation_group_totals")
LATEST, HISTORY, GROUPS = "/api/v1/energy/generation/latest", "/api/v1/energy/generation/history", "/api/v1/energy/generation/group-history"


# ------------------------------------------------------------------------------------------------------------------ schemas
def obs(**over):
    base = dict(station_group=LARGE, station_name="Alpha", observation_year=2020, value_state="reported", generation_gwh=D("393.40"),
                published_precision=2, source_table="Table 13", source_pdf_page=22, source_printed_page=15)
    return schemas.EnergyGenerationObservation(**{**base, **over})


def test_exact_decimal_strings_keep_the_printed_scale():
    body = json.loads(obs().model_dump_json())
    assert body["generation_gwh"] == "393.40" and body["published_precision"] == 2
    for value, expected in ((D("393.4"), "393.4"), (D("0"), "0"), (D("0.00"), "0.00"), (D("1234567.5"), "1234567.5"), (D("1E+2"), "100")):
        assert json.loads(obs(generation_gwh=value).model_dump_json())["generation_gwh"] == expected
    assert '"393.40"' in obs().model_dump_json() and "E+" not in obs(generation_gwh=D("3.934E+2")).model_dump_json()
    total = schemas.EnergyGenerationGroupTotal(station_group=LARGE, observation_year=2020, total_gwh=D("500.10"), published_precision=2,
                                               source_table="Table 13", source_pdf_page=22, source_printed_page=15)
    assert json.loads(total.model_dump_json())["total_gwh"] == "500.10"


def test_not_reported_serializes_null_value_and_precision():
    body = json.loads(obs(value_state="not_reported", generation_gwh=None, published_precision=None).model_dump_json())
    assert body["generation_gwh"] is None and body["published_precision"] is None and body["value_state"] == "not_reported"


def test_invalid_groups_and_value_state_combinations_are_rejected():
    with pytest.raises(ValidationError):
        obs(station_group="hydro")
    with pytest.raises(ValidationError):
        schemas.EnergyGenerationGroupHistory(station_group="other", count=0, rights_status=None, observations=[])
    for bad in (dict(value_state="not_reported"), dict(value_state="not_reported", generation_gwh=None),
                dict(value_state="not_reported", published_precision=None),
                dict(generation_gwh=None, published_precision=None), dict(published_precision=None), dict(generation_gwh=None),
                dict(value_state="unknown"), dict(generation_gwh=D("-1")), dict(generation_gwh=D("NaN")), dict(published_precision=-1)):
        with pytest.raises(ValidationError):
            obs(**bad)
    schemas.EnergyGenerationHistoryPoint(observation_year=2020, value_state="not_reported", publication_year=2025, revision_sequence=1,
                                         source_table="t", source_pdf_page=1, source_printed_page=1)
    with pytest.raises(ValidationError):
        schemas.EnergyGenerationHistoryPoint(observation_year=2020, value_state="reported", publication_year=2025, revision_sequence=1,
                                             source_table="t", source_pdf_page=1, source_printed_page=1)


# ---------------------------------------------------------------------------------------------------------------- database
@pytest.fixture
def engine(disposable_database, monkeypatch):
    _url, eng = disposable_database
    reset_public(eng)
    with eng.begin() as c:
        c.execute(text(M014.read_text()))
    monkeypatch.setattr(read, "get_engine", lambda: eng)
    return eng


@pytest.fixture
def client(engine):
    return TestClient(app)


def sha(n):
    return f"{n:064x}"


def precision(d):
    return -d.as_tuple().exponent


class Seed:
    """Writes synthetic publications through plain SQL in the shape the (reviewed) importer produces."""

    def __init__(self, engine):
        self.engine = engine

    def publication(self, n, year, rev, observations, totals, prev=None):
        """observations: [(group, name, obs_year, value|None, table, pdf_page)], totals: [(group, obs_year, value, table, pdf_page)]."""
        with self.engine.begin() as c:
            cand = c.execute(text("INSERT INTO public.energy_publication_candidates (source_series, nominal_publication_year, source_url, "
                                  "source_sha256, source_size_bytes, discovered_at) VALUES (:s,:y,:u,:h,1000,:t) RETURNING id"),
                             {"s": SERIES, "y": year, "u": f"https://example.invalid/{n}.pdf", "h": sha(n), "t": NOW}).scalar()
            pub = c.execute(text("INSERT INTO public.energy_publications (publisher, source_series, publication_year, revision_sequence, "
                                 "publication_status, supersedes_publication_id, title, cover_date, source_url, source_sha256, source_size_bytes, "
                                 "pdf_pages, rights_status, first_retrieved_at) VALUES ('ERB',:s,:y,:r,'approved',:prev,:title,'Synthetic',:u,:h,"
                                 "1000,10,'review_required',:t) RETURNING id"),
                            {"s": SERIES, "y": year, "r": rev, "prev": prev, "title": f"Synthetic bulletin {n}",
                             "u": f"https://example.invalid/{n}.pdf", "h": sha(n), "t": NOW}).scalar()
            imp = self._import(c, pub, 1, observations, totals, None)
            c.execute(text("UPDATE public.energy_publication_candidates SET review_status='accepted', review_note='ok', reviewed_at=:t, "
                           "accepted_publication_id=:p WHERE id=:c"), {"t": NOW, "p": pub, "c": cand})
            if prev:
                c.execute(text("UPDATE public.energy_publications SET publication_status='superseded', status_changed_at=:t, status_note='r' "
                               "WHERE id=:p"), {"t": NOW, "p": prev})
        return pub, imp

    def correction(self, pub, observations, totals):
        """Import 2 of the same publication; import 1 becomes superseded."""
        with self.engine.begin() as c:
            imp = self._import(c, pub, 2, observations, totals, "synthetic correction")
            c.execute(text("UPDATE public.energy_generation_imports SET import_status='superseded', status_changed_at=:t, status_note='c' "
                           "WHERE publication_id=:p AND import_sequence=1"), {"t": NOW, "p": pub})
        return imp

    def _import(self, c, pub, seq, observations, totals, reason):
        imp = c.execute(text("INSERT INTO public.energy_generation_imports (publication_id, import_sequence, import_status, "
                             "expected_observation_count, expected_group_total_count, importer_version, correction_reason) "
                             "VALUES (:p,:q,:st,:o,:t,'test',:r) RETURNING id"),
                        {"p": pub, "q": seq, "st": "current", "o": len(observations), "t": len(totals), "r": reason}).scalar()
        for group, name, year, value, table, pdf in observations:
            c.execute(text("INSERT INTO public.energy_source_stations (source_series, station_group, station_name) VALUES (:s,:g,:n) "
                           "ON CONFLICT DO NOTHING"), {"s": SERIES, "g": group, "n": name})
            sid = c.execute(text("SELECT id FROM public.energy_source_stations WHERE source_series=:s AND station_group=:g AND station_name=:n"),
                            {"s": SERIES, "g": group, "n": name}).scalar()
            state, v, pr = ("not_reported", None, None) if value is None else ("reported", D(value), precision(D(value)))
            c.execute(text("INSERT INTO public.energy_generation_observations (import_id, source_station_id, observation_year, value_state, "
                           "generation_gwh, published_precision, source_table, source_pdf_page, source_printed_page) "
                           "VALUES (:i,:s,:y,:st,:v,:p,:tbl,:pdf,:pr)"),
                      {"i": imp, "s": sid, "y": year, "st": state, "v": v, "p": pr, "tbl": table, "pdf": pdf, "pr": pdf - 7})
        for group, year, value, table, pdf in totals:
            c.execute(text("INSERT INTO public.energy_generation_group_totals (import_id, station_group, observation_year, total_gwh, "
                           "published_precision, source_table, source_pdf_page, source_printed_page) VALUES (:i,:g,:y,:v,:p,:tbl,:pdf,:pr)"),
                      {"i": imp, "g": group, "y": year, "v": D(value), "p": precision(D(value)), "tbl": table, "pdf": pdf, "pr": pdf - 7})
        return imp


def O(group, name, year, value, table="Table 13", pdf=22):
    return (group, name, year, value, table, pdf)


def T(group, year, value, table="Table 13", pdf=22):
    return (group, year, value, table, pdf)


@pytest.fixture
def seed(engine):
    return Seed(engine)


def counts(engine):
    with engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM public.{t}")).scalar() for t in ENERGY_TABLES}


def lifecycle(engine):
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT 'p', id, publication_status, status_changed_at FROM public.energy_publications UNION ALL "
            "SELECT 'i', id, import_status, status_changed_at FROM public.energy_generation_imports UNION ALL "
            "SELECT 'c', id, review_status, reviewed_at FROM public.energy_publication_candidates ORDER BY 1, 2"))]


def base_data(seed):
    """r1 2024 publication: two groups, two years; Alpha has a dash in 2024; totals deliberately differ from station sums."""
    return seed.publication(1, 2024, 1, [
        O(LARGE, "Alpha", 2023, "100.40"), O(LARGE, "Alpha", 2024, None), O(LARGE, "Beta", 2023, "50.00"), O(LARGE, "Beta", 2024, "60.10"),
        O(IPP, "Zed", 2023, "7.5", "Table 16", 24), O(IPP, "Zed", 2024, "0.00", "Table 16", 24),
    ], [T(LARGE, 2023, "999.99"), T(LARGE, 2024, "888.80"), T(IPP, 2023, "7.5", "Table 16", 24), T(IPP, 2024, "0.00", "Table 16", 24)])


# ------------------------------------------------------------------------------------------------------------------- latest
def test_latest_empty_database_is_a_structured_200(client):
    r = client.get(LATEST)
    assert r.status_code == 200 and r.json() == {"snapshot": None}


def test_latest_returns_one_coherent_snapshot_with_published_totals(client, seed):
    base_data(seed)
    r = client.get(LATEST)
    assert r.status_code == 200
    s = r.json()["snapshot"]
    assert (s["publisher"], s["source_series"], s["publication_year"], s["revision_sequence"], s["observation_year"], s["rights_status"]) == (
        "ERB", SERIES, 2024, 1, 2024, "review_required")
    assert s["title"] == "Synthetic bulletin 1" and s["cover_date"] == "Synthetic" and s["source_url"].endswith("/1.pdf")
    assert [(x["station_group"], x["station_name"]) for x in s["stations"]] == [(LARGE, "Alpha"), (LARGE, "Beta"), (IPP, "Zed")]
    assert all(x["observation_year"] == 2024 for x in s["stations"])
    alpha = s["stations"][0]
    assert (alpha["value_state"], alpha["generation_gwh"], alpha["published_precision"]) == ("not_reported", None, None)  # never zero
    assert (s["stations"][1]["generation_gwh"], s["stations"][1]["published_precision"]) == ("60.10", 2)  # printed scale kept
    assert (s["stations"][2]["generation_gwh"], s["stations"][2]["published_precision"]) == ("0.00", 2)  # a printed zero stays reported
    assert [(t["station_group"], t["observation_year"], t["total_gwh"]) for t in s["group_totals"]] == [
        (LARGE, 2024, "888.80"), (IPP, 2024, "0.00")]  # the published totals, not the 60.10 station sum
    assert "id" not in s and "publication_id" not in s and all("id" not in x for x in s["stations"])


def test_latest_uses_the_newest_approved_revision_and_its_own_import_only(client, seed):
    r1, _ = base_data(seed)
    r2, _ = seed.publication(2, 2024, 2, [O(LARGE, "Alpha", 2024, "11.00"), O(LARGE, "Alpha", 2025, "12.00"), O(LARGE, "Beta", 2025, "13.0")],
                             [T(LARGE, 2025, "25.0"), T(LARGE, 2024, "11.00")], prev=r1)
    s = client.get(LATEST).json()["snapshot"]
    assert (s["publication_year"], s["revision_sequence"], s["observation_year"]) == (2024, 2, 2025)
    assert [x["station_name"] for x in s["stations"]] == ["Alpha", "Beta"] and {x["generation_gwh"] for x in s["stations"]} == {"12.00", "13.0"}
    assert [t["total_gwh"] for t in s["group_totals"]] == ["25.0"]  # from the same import as the stations, never from r1


def test_latest_ranks_publication_year_before_revision_and_ignores_superseded_imports(client, seed):
    base_data(seed)
    pub, _ = seed.publication(3, 2025, 1, [O(LARGE, "Alpha", 2025, "1.00")], [T(LARGE, 2025, "1.00")])
    seed.correction(pub, [O(LARGE, "Alpha", 2025, "2.00")], [T(LARGE, 2025, "2.00")])
    s = client.get(LATEST).json()["snapshot"]
    assert (s["publication_year"], s["revision_sequence"]) == (2025, 1)
    assert s["stations"][0]["generation_gwh"] == "2.00" and s["group_totals"][0]["total_gwh"] == "2.00"  # the current import 2, not import 1


def test_latest_order_is_deterministic_by_group_then_name(client, seed):
    seed.publication(1, 2024, 1, [O(IPP, "B", 2024, "1.00", "Table 16", 24), O(DIESEL, "Z", 2024, "1.00", "Table 15", 24),
                                  O(MINI, "M", 2024, "1.00", "Table 14", 22), O(LARGE, "B", 2024, "1.00"), O(LARGE, "A", 2024, "1.00"),
                                  O(IPP, "A", 2024, "1.00", "Table 16", 24)],
                     [T(IPP, 2024, "2.00", "Table 16", 24), T(LARGE, 2024, "2.00"), T(DIESEL, 2024, "1.00", "Table 15", 24), T(MINI, 2024, "1.00", "Table 14", 22)])
    s = client.get(LATEST).json()["snapshot"]
    assert [(x["station_group"], x["station_name"]) for x in s["stations"]] == [
        (LARGE, "A"), (LARGE, "B"), (MINI, "M"), (DIESEL, "Z"), (IPP, "A"), (IPP, "B")]
    assert [t["station_group"] for t in s["group_totals"]] == [LARGE, MINI, DIESEL, IPP]


# ------------------------------------------------------------------------------------------------------- station history
def hist(client, group=LARGE, name="Alpha"):
    return client.get(HISTORY, params={"station_group": group, "station_name": name})


def test_station_history_is_ascending_exact_and_keeps_not_reported_null(client, seed):
    base_data(seed)
    r = hist(client)
    assert r.status_code == 200
    body = r.json()
    assert (body["station_group"], body["station_name"], body["count"], body["rights_status"]) == (LARGE, "Alpha", 2, "review_required")
    assert [p["observation_year"] for p in body["observations"]] == [2023, 2024]
    p23, p24 = body["observations"]
    assert (p23["generation_gwh"], p23["published_precision"], p23["value_state"]) == ("100.40", 2, "reported")
    assert (p24["generation_gwh"], p24["published_precision"], p24["value_state"]) == (None, None, "not_reported")
    assert (p23["publication_year"], p23["revision_sequence"], p23["source_table"], p23["source_pdf_page"], p23["source_printed_page"]) == (
        2024, 1, "Table 13", 22, 15)
    assert "id" not in p23


def test_station_identity_is_exact_and_missing_is_404(client, seed):
    base_data(seed)
    for group, name in ((LARGE, "alpha"), (LARGE, "Alpha "), (LARGE, " Alpha"), (MINI, "Alpha"), (IPP, "Alpha"), (LARGE, "Gamma")):
        r = hist(client, group, name)
        assert r.status_code == 404 and r.json() == {"detail": "No stored ERB generation history for that station"}
    assert hist(client, "bogus_group", "Alpha").status_code == 422
    assert client.get(HISTORY, params={"station_group": LARGE}).status_code == 422
    assert client.get(HISTORY, params={"station_group": LARGE, "station_name": ""}).status_code == 422


def test_newest_approved_revision_wins_for_a_restated_year(client, seed):
    r1, _ = base_data(seed)
    seed.publication(2, 2024, 2, [O(LARGE, "Alpha", 2024, "77.70")], [T(LARGE, 2024, "77.70")], prev=r1)  # restates 2024 only
    pts = {p["observation_year"]: p for p in hist(client).json()["observations"]}
    assert (pts[2024]["generation_gwh"], pts[2024]["revision_sequence"], pts[2024]["value_state"]) == ("77.70", 2, "reported")
    assert list(pts) == [2024]  # r1 is superseded, so its 2023 value is no longer canonical (omission fallback is among APPROVED revisions)


def test_a_superseded_publication_is_not_canonical_even_for_years_only_it_has(client, seed):
    r1, _ = base_data(seed)
    seed.publication(2, 2024, 2, [O(LARGE, "Alpha", 2024, "77.70")], [T(LARGE, 2024, "77.70")], prev=r1)
    body = hist(client).json()
    assert body["count"] == 1 and body["observations"][0]["revision_sequence"] == 2  # 2023 exists only in the superseded r1
    assert hist(client, LARGE, "Beta").status_code == 404


def test_omission_fallback_to_an_older_approved_publication_of_another_year(client, seed):
    base_data(seed)  # 2024 publication: Alpha 2023/2024
    seed.publication(2, 2025, 1, [O(LARGE, "Alpha", 2025, "5.50")], [T(LARGE, 2025, "5.50")])  # newer year, different observation years
    body = hist(client).json()
    assert [(p["observation_year"], p["publication_year"]) for p in body["observations"]] == [(2023, 2024), (2024, 2024), (2025, 2025)]
    assert body["rights_status"] == "review_required"


def test_a_newer_publication_restating_a_year_overrides_the_older_approved_one(client, seed):
    base_data(seed)
    seed.publication(2, 2025, 1, [O(LARGE, "Alpha", 2024, "42.00")], [T(LARGE, 2024, "42.00")])
    pts = {p["observation_year"]: p for p in hist(client).json()["observations"]}
    assert (pts[2024]["generation_gwh"], pts[2024]["publication_year"]) == ("42.00", 2025)
    assert (pts[2023]["generation_gwh"], pts[2023]["publication_year"]) == ("100.40", 2024)


def test_a_superseded_local_import_is_not_used(client, seed):
    pub, _ = base_data(seed)
    seed.correction(pub, [O(LARGE, "Alpha", 2023, "1.11"), O(LARGE, "Alpha", 2024, "2.22")], [T(LARGE, 2023, "1.11"), T(LARGE, 2024, "2.22")])
    assert [p["generation_gwh"] for p in hist(client).json()["observations"]] == ["1.11", "2.22"]
    assert hist(client, LARGE, "Beta").status_code == 404  # Beta exists only in the superseded import 1


def test_station_history_reads_the_canonical_view(client, seed):
    base_data(seed)
    sql = str(read._STATION_HISTORY)
    assert "public.energy_generation_canonical" in sql and "energy_generation_observations" not in sql  # no Python/SQL re-derivation
    assert hist(client).status_code == 200


# ----------------------------------------------------------------------------------------------------------- group history
def ghist(client, group=LARGE):
    return client.get(GROUPS, params={"station_group": group})


def test_group_history_returns_published_totals_ascending_unchanged(client, seed):
    base_data(seed)
    body = ghist(client).json()
    assert (body["station_group"], body["count"], body["rights_status"]) == (LARGE, 2, "review_required")
    assert [(p["observation_year"], p["total_gwh"], p["published_precision"]) for p in body["observations"]] == [(2023, "999.99", 2), (2024, "888.80", 2)]
    p = body["observations"][0]
    assert (p["station_group"], p["publication_year"], p["revision_sequence"], p["source_table"], p["source_pdf_page"], p["source_printed_page"]) == (
        LARGE, 2024, 1, "Table 13", 22, 15)
    assert ghist(client, IPP).json()["observations"][0]["total_gwh"] == "7.5"


def test_group_history_validates_the_four_groups(client):
    for group in schemas.ENERGY_STATION_GROUPS:
        assert ghist(client, group).status_code == 200
    assert ghist(client, "hydro").status_code == 422
    assert client.get(GROUPS).status_code == 422


def test_empty_valid_group_is_a_200_with_count_zero(client, seed):
    assert ghist(client, DIESEL).json() == {"station_group": DIESEL, "count": 0, "rights_status": None, "observations": []}
    base_data(seed)
    assert ghist(client, DIESEL).json()["count"] == 0


def test_latest_revision_wins_per_group_year_with_omission_fallback(client, seed):
    r1, _ = base_data(seed)
    seed.publication(2, 2024, 2, [O(LARGE, "Alpha", 2024, "1.00")], [T(LARGE, 2024, "123.45")], prev=r1)  # restates only 2024; 2023 is gone from r2
    body = ghist(client).json()
    assert [(p["observation_year"], p["total_gwh"], p["revision_sequence"]) for p in body["observations"]] == [(2024, "123.45", 2)]
    # r1 is superseded, so its 2023 total is no longer canonical; an APPROVED older publication of another year does fall back:
    seed.publication(3, 2025, 1, [O(LARGE, "Alpha", 2025, "1.00")], [T(LARGE, 2025, "9.00")])
    body = ghist(client).json()
    assert [(p["observation_year"], p["publication_year"]) for p in body["observations"]] == [(2024, 2024), (2025, 2025)]


def test_publication_revision_not_import_sequence_ranks_totals(client, seed):
    old_year, _ = seed.publication(1, 2023, 1, [O(LARGE, "Alpha", 2022, "1.00")], [T(LARGE, 2022, "10.00")])
    seed.correction(old_year, [O(LARGE, "Alpha", 2022, "1.00")], [T(LARGE, 2022, "11.00")])  # import_sequence 2, older publication
    seed.publication(2, 2024, 1, [O(LARGE, "Alpha", 2022, "1.00")], [T(LARGE, 2022, "20.00")])  # import_sequence 1, newer publication
    body = ghist(client).json()["observations"]
    assert [(p["total_gwh"], p["publication_year"]) for p in body] == [("20.00", 2024)]  # newer publication wins despite import_sequence 1 < 2


def test_group_history_ignores_a_superseded_local_import(client, seed):
    pub, _ = base_data(seed)
    seed.correction(pub, [O(LARGE, "Alpha", 2023, "1.00")], [T(LARGE, 2023, "5.55")])
    assert [(p["observation_year"], p["total_gwh"]) for p in ghist(client).json()["observations"]] == [(2023, "5.55")]


# ------------------------------------------------------------------------------------------------- failures / isolation
def test_missing_migration_is_a_safe_503_on_every_route(disposable_database, monkeypatch):
    _url, eng = disposable_database
    reset_public(eng)
    monkeypatch.setattr(read, "get_engine", lambda: eng)
    client = TestClient(app)
    for response in (client.get(LATEST), hist(client), ghist(client)):
        assert response.status_code == 503 and response.json() == {"detail": "Stored ERB generation data unavailable"}
        assert "energy_" not in response.text and "relation" not in response.text


def test_database_errors_do_not_leak_details(monkeypatch):
    secret = "postgresql+psycopg://zm_terminal_app:SECRETPW@db.invalid/zm_terminal"
    router = __import__("app.api.router", fromlist=["x"])

    def boom(*a, **k):
        raise RuntimeError(f"could not connect to {secret}")
    for name in ("fetch_latest_generation", "fetch_station_history", "fetch_group_history"):
        monkeypatch.setattr(router, name, boom)
    client = TestClient(app)
    for response in (client.get(LATEST), hist(client), ghist(client)):
        assert response.status_code == 503 and "SECRET" not in response.text and "db.invalid" not in response.text


def test_routes_make_no_network_request_and_write_nothing(client, seed, engine, monkeypatch):
    base_data(seed)
    before_counts, before_life = counts(engine), lifecycle(engine)

    def no_network(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)  # the real network transports (TestClient uses an ASGI one)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    for _ in range(2):
        assert client.get(LATEST).status_code == 200 and hist(client).status_code == 200 and ghist(client).status_code == 200
        assert hist(client, LARGE, "nobody").status_code == 404
    assert counts(engine) == before_counts and lifecycle(engine) == before_life


def test_the_read_connection_is_read_only_at_the_database_level(engine):
    with read._read_only(engine) as c:
        with pytest.raises(Exception, match="read-only"):
            c.execute(text("INSERT INTO public.energy_source_stations (source_series, station_group, station_name) VALUES ('s','zesco_diesel','x')"))


def test_read_service_source_is_select_only_and_network_free():
    source = Path(read.__file__).read_text()
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|socket|aiohttp)", code, re.M)
    for forbidden in ("erb_generation", "energy_generation_store", "import_erb_generation", "fetch_bulletin", "parse_bulletin"):
        assert forbidden not in code
    sql = " ".join(re.findall(r'"([^"]*(?:SELECT|FROM)[^"]*)"', code))
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|DROP|ALTER|GRANT)\b", sql, re.I)
    assert ".begin(" not in code and "commit" not in code
    for relation in re.findall(r"\b(?:FROM|JOIN)\s+([\w.]+)", sql):
        assert relation.startswith("public.energy_"), relation


def test_openapi_lists_the_three_routes():
    paths = app.openapi()["paths"]
    for path in ("/api/v1/energy/generation/latest", "/api/v1/energy/generation/history", "/api/v1/energy/generation/group-history"):
        assert list(paths[path]) == ["get"]
