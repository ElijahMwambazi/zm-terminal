"""Combined 2010-2026 monthly USD/ZMW importer: source-family selection, cross-series uniqueness, continuity, dry-run, the
153 -> 201 upgrade path on disposable PostgreSQL, and the widened stored-only API. Offline: sources are synthetic (the
synthetic numbers are not BoZ data); the fetch/load seams are patched, so these tests never touch the network or Poppler."""

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api import router as router_module
from app.jobs import backfill_fx_monthly as job
from app.main import app
from app.services import boz_annual_source as annual
from app.services import boz_annual_table as tab
from app.services import boz_monthly_source as source
from app.services import boz_monthly_table as ftab
from app.services import fx_monthly_canonical as canon
from app.services.fx_monthly_combine import MonthlyConflictError, MonthlyObservation
from app.services.fx_monthly_store import (
    AmbiguousCanonicalMonthError, SchemaNotMigratedError, fetch_fx_monthly, save_monthly_averages,
)
from tests.test_fx_monthly import parsed, synthetic_tables
from disposable_db import require_test_database_url

D = Decimal
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
client = TestClient(app)
EXC = {e.month: e for e in tab.ORDERING_EXCEPTIONS}


def months(first: date, last: date):
    cursor = first
    while cursor <= last:
        yield cursor
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)


def printed(month: date) -> tuple[Decimal, Decimal, Decimal]:
    """Synthetic printed values (not BoZ data): old kwacha before 2013 (thousands), rebased from 2013; exceptions exact."""
    if month in EXC:
        e = EXC[month]
        return e.buy, e.mid, e.sell
    base = Decimal(4000 + (month.year - 2010) * 300 + month.month * 7) if month.year < 2013 else Decimal(5) + Decimal(month.month) / 100
    return base + D("0.10"), base + D("0.15"), base + D("0.20")


def annual_tables(*, alter: dict | None = None) -> dict[str, tab.ParsedAnnualTable]:
    fort = synthetic_tables()[source.PUBLICATIONS[0].filename]
    out = {}
    for report in annual.ALL_REPORTS:
        rows = []
        start = date(report.persist_first.year - 1, 12, 1) if report.persist_first else date(2013, 12, 1)
        for m in months(max(start, date(2010, 1, 1)) if report.key != "ar2010" else date(2010, 1, 1), report.last_row):
            if report.key == "ar2014" and m.year == 2014:
                r = next(x for x in fort.rows if x.month == m)
                rows.append(tab.PublishedRate(m, r.buy, r.mid, r.sell))
            else:
                rows.append(tab.PublishedRate(m, *printed(m)))
        if alter and report.key in alter:
            rows = [alter[report.key](r) for r in rows if alter[report.key](r) is not None]
        out[report.key] = tab.ParsedAnnualTable(rows=tuple(rows))
    return out


def pipeline(monkeypatch, *, annual_override=None, fort_tables=None):
    order: list[str] = []
    a_tables = annual_override or annual_tables()
    f_tables = fort_tables or synthetic_tables()

    async def a_fetch(report, client):
        order.append(report.key)
        return report.key.encode()

    async def a_load(report, data):
        return a_tables[report.key]

    async def f_fetch(publication, client):
        order.append(publication.filename)
        return publication.filename.encode()

    async def f_load(publication, data):
        return f_tables[publication.filename]

    monkeypatch.setattr(annual, "fetch_report", a_fetch)
    monkeypatch.setattr(annual, "load_annual_report_async", a_load)
    monkeypatch.setattr(source, "fetch_pdf", f_fetch)
    monkeypatch.setattr(source, "load_publication_async", f_load)
    return order


def run(monkeypatch, start, end, *, dry_run=True, saved=None, sleeps=None, engine=None, **kw):
    order = pipeline(monkeypatch, **kw)
    if saved is not None:
        monkeypatch.setattr(job, "save_monthly_averages", lambda obs, imported_at, engine=None: saved.append(list(obs)) or (len(obs), 0))

    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    async def go():
        async with httpx.AsyncClient() as c:
            return await job.run_import(start, end, dry_run=dry_run, client=c, now=NOW, sleep=sleep, engine=engine)

    return asyncio.run(go()), order


FIRST, LAST = date(2010, 1, 1), date(2026, 9, 1)
FORT = [p.filename for p in source.PUBLICATIONS]


# ------------------------------------------------------------ coverage and range validation
def test_coverage_constants_and_the_source_boundary():
    assert (job.FIRST_MONTH, job.LAST_MONTH) == (FIRST, LAST)
    assert annual.LAST_COVERED_MONTH == date(2013, 12, 1) and source.FIRST_COVERED_MONTH == date(2014, 1, 1)


def test_range_validation_boundaries():
    today = date(2026, 11, 20)
    for start, end in ((date(2013, 1, 1), date(2012, 12, 1)), (date(2009, 12, 1), date(2010, 1, 1)),
                       (date(2010, 1, 1), date(2026, 10, 1)), (date(2026, 1, 1), date(2027, 1, 1))):
        with pytest.raises(job.ImportRequestError):
            job.validate_range(start, end, today=today)
    for m in (date(2010, 1, 1), date(2012, 12, 1), date(2013, 1, 1), date(2013, 12, 1), date(2014, 1, 1), date(2026, 9, 1)):
        job.validate_range(m, m, today=today)  # single month
    job.validate_range(FIRST, LAST, today=today)


def test_cli_defaults_cover_the_whole_range_and_one_sided_ranges_work(monkeypatch, capsys):
    seen = []

    async def fake(start, end, **k):
        seen.append((start, end))
        return {"months_selected": 0}

    monkeypatch.setattr(job, "run_import", fake)
    for args, expect in (([], (FIRST, LAST)), (["--from", "2013-01"], (date(2013, 1, 1), LAST)),
                         (["--to", "2013-12"], (FIRST, date(2013, 12, 1))), (["--from", "2012-12", "--to", "2012-12", "--dry-run"],
                                                                              (date(2012, 12, 1), date(2012, 12, 1)))):
        assert job.main(args) == 0 and seen[-1] == expect


# ------------------------------------------------------------ source-family selection
@pytest.mark.parametrize("month,expected_fetches,series", [
    (date(2010, 1, 1), ["ar2010"], tab.ANNUAL_SOURCE_SERIES),
    (date(2012, 12, 1), ["ar2012"], tab.ANNUAL_SOURCE_SERIES),
    (date(2013, 1, 1), ["ar2013"], tab.ANNUAL_SOURCE_SERIES),
    (date(2013, 12, 1), ["ar2013"], tab.ANNUAL_SOURCE_SERIES),
])
def test_single_annual_month_fetches_only_its_report(monkeypatch, month, expected_fetches, series):
    saved = []
    result, order = run(monkeypatch, month, month, dry_run=False, saved=saved)
    assert order == expected_fetches and result["months_selected"] == 1 and result["bridge_2014"] == "not_applicable"
    assert [(o.month, canon.series_of(o)) for o in saved[0]] == [(month, series)]


@pytest.mark.parametrize("month", [date(2014, 1, 1), date(2026, 9, 1)])
def test_single_fortnightly_month_fetches_only_fortnightly_publications(monkeypatch, month):
    saved = []
    result, order = run(monkeypatch, month, month, dry_run=False, saved=saved)
    assert order == [p.filename for p in source.select_publications(month, month)] and not any(k.startswith("ar") for k in order)
    assert [(o.month, canon.series_of(o)) for o in saved[0]] == [(month, ftab.SOURCE_SERIES)]


def test_annual_only_range_fetches_no_fortnightly_and_no_validation_report(monkeypatch):
    result, order = run(monkeypatch, date(2012, 12, 1), date(2013, 1, 1))
    assert order == ["ar2012", "ar2013"] and result["annual_candidates"] == 2 and result["fortnightly_candidates"] == 0


def test_boundary_crossing_range_selects_both_families_and_the_validation_report(monkeypatch):
    saved = []
    result, order = run(monkeypatch, date(2013, 12, 1), date(2014, 1, 1), dry_run=False, saved=saved)
    assert order == ["ar2013", "ar2014", source.select_publications(date(2014, 1, 1), date(2014, 1, 1))[0].filename]
    assert [(f"{o.month:%Y-%m}", canon.series_of(o)) for o in saved[0]] == [
        ("2013-12", tab.ANNUAL_SOURCE_SERIES), ("2014-01", ftab.SOURCE_SERIES)]
    assert result["bridge_2014"] == "pass" and result["months_selected"] == 2


def test_unrelated_reports_are_not_fetched(monkeypatch):
    _, order = run(monkeypatch, date(2011, 3, 1), date(2011, 5, 1))
    assert order == ["ar2011"]


# ------------------------------------------------------------ dry-run
def test_full_dry_run_metrics_and_no_database(monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("dry-run must not touch PostgreSQL")

    monkeypatch.setattr(job, "save_monthly_averages", no_db)
    sleeps = []
    result, order = run(monkeypatch, FIRST, LAST, sleeps=sleeps)
    assert (result["first_month"], result["last_month"], result["months_selected"], result["gaps"]) == ("2010-01", "2026-09", 201, 0)
    assert (result["annual_candidates"], result["fortnightly_candidates"]) == (48, 153)
    assert (result["source_conflicts"], result["cross_series_conflicts"], result["ordering_exceptions_used"]) == (0, 0, 2)
    assert result["bridge_2014"] == "pass" and result["inserted"] == 0 and result["already_present"] == 0
    assert order == ["ar2010", "ar2011", "ar2012", "ar2013", "ar2014", *FORT]
    assert result["publications_fetched"] == 18 and sleeps == [source.REQUEST_PAUSE_SECONDS] * 17


def test_dry_run_needs_no_engine_or_migration(monkeypatch):
    monkeypatch.setattr("app.db.get_engine", lambda: (_ for _ in ()).throw(AssertionError("no database in dry-run")))
    result, _ = run(monkeypatch, FIRST, LAST)
    assert result["months_selected"] == 201


# ------------------------------------------------------------ cross-series uniqueness
def annual_obs(*ms):
    loaded = [(r, annual_tables()[r.key]) for r in annual.CANONICAL_REPORTS]
    return [o for o in canon.build_annual_observations(loaded) if o.month in ms]


def fort_obs(month: date, series=None):
    return MonthlyObservation(month, D("1"), D("1"), D("1"), "p", "u", 3, source_series=series)


def test_normal_boundary_is_disjoint():
    a = annual_obs(*months(FIRST, date(2013, 12, 1)))
    f = [fort_obs(m) for m in months(date(2014, 1, 1), LAST)]
    result = canon.assemble(a, f, FIRST, LAST)
    assert len(result.observations) == 201 and (result.annual_count, result.fortnightly_count) == (48, 153) and result.gaps == ()


def test_an_annual_2014_candidate_cannot_enter_the_set():
    sneaky = MonthlyObservation(date(2014, 1, 1), D("1"), D("1"), D("1"), "p", "u", 3, source_series=tab.ANNUAL_SOURCE_SERIES)
    f = [fort_obs(m) for m in months(date(2014, 1, 1), LAST)]
    with pytest.raises(canon.CanonicalSetError, match="2014-01.*outside its approved window"):
        canon.assemble([*annual_obs(date(2013, 12, 1)), sneaky], f, FIRST, LAST)
    report = annual.VALIDATION_REPORTS[0]
    with pytest.raises(canon.CanonicalSetError, match="validation report"):
        canon.build_annual_observations([(report, annual_tables()["ar2014"])])


def test_a_fortnightly_2013_candidate_cannot_enter_the_set():
    with pytest.raises(canon.CanonicalSetError, match="2013-12.*outside its approved window"):
        canon.assemble(annual_obs(date(2013, 12, 1)), [fort_obs(date(2013, 12, 1))], FIRST, LAST)


def test_same_month_from_both_series_fails_and_names_the_series():
    both = [fort_obs(date(2013, 12, 1), series=tab.ANNUAL_SOURCE_SERIES)]
    with pytest.raises(canon.CanonicalSetError, match="outside its approved window|more than one canonical observation"):
        canon.assemble(annual_obs(date(2013, 12, 1)), both, FIRST, LAST)
    # duplicate within one family also fails, and the message carries ids, not rates
    dup = annual_obs(date(2013, 12, 1)) * 2
    with pytest.raises(canon.CanonicalSetError) as info:
        canon.assemble(dup, [], FIRST, date(2013, 12, 1))
    assert tab.ANNUAL_SOURCE_SERIES in str(info.value) and "5.5" not in str(info.value)


def test_injected_annual_2014_row_fails_the_whole_import_before_any_write(monkeypatch):
    saved = []
    real = canon.build_annual_observations
    sneaky = MonthlyObservation(date(2014, 1, 1), D("1"), D("1"), D("1"), "p", "u", 3, source_series=tab.ANNUAL_SOURCE_SERIES)
    monkeypatch.setattr(job, "build_annual_observations", lambda loaded: [*real(loaded), sneaky])
    with pytest.raises(job.ImportRequestError, match="outside its approved window"):
        run(monkeypatch, FIRST, LAST, dry_run=False, saved=saved)
    assert saved == []


# ------------------------------------------------------------ continuity
def test_full_range_is_contiguous_across_the_2013_12_to_2014_01_boundary(monkeypatch):
    saved = []
    run(monkeypatch, FIRST, LAST, dry_run=False, saved=saved)
    got = [o.month for o in saved[0]]
    assert got == list(months(FIRST, LAST)) and len(got) == 201
    i = got.index(date(2013, 12, 1))
    assert got[i + 1] == date(2014, 1, 1)
    assert canon.series_of(saved[0][i]) == tab.ANNUAL_SOURCE_SERIES and canon.series_of(saved[0][i + 1]) == ftab.SOURCE_SERIES


def test_bounded_range_continuity_ignores_months_outside_the_request(monkeypatch):
    result, _ = run(monkeypatch, date(2012, 6, 1), date(2014, 3, 1))
    assert result["months_selected"] == 22 and result["gaps"] == 0


def test_missing_boundary_month_fails_before_any_write(monkeypatch):
    saved = []
    tables = annual_tables(alter={"ar2013": lambda r: None if r.month == date(2013, 12, 1) else r,
                                  "ar2014": lambda r: None if r.month == date(2013, 12, 1) else r})
    with pytest.raises(job.ImportRequestError, match="missing, first 2013-12"):
        run(monkeypatch, FIRST, LAST, dry_run=False, saved=saved, annual_override=tables)
    assert saved == []


# ------------------------------------------------------------ conflicts and bridge
def test_annual_overlap_with_different_printed_values_is_a_conflict(monkeypatch):
    saved = []
    bump = lambda r: tab.PublishedRate(r.month, r.buy + D("0.01"), r.mid, r.sell) if r.month == date(2011, 12, 1) else r
    with pytest.raises(MonthlyConflictError, match="2011-12"):
        run(monkeypatch, FIRST, LAST, dry_run=False, saved=saved, annual_override=annual_tables(alter={"ar2012": bump}))
    assert saved == []


def test_bridge_mismatch_fails_before_any_write(monkeypatch):
    saved = []
    bump = lambda r: tab.PublishedRate(r.month, r.buy, r.mid + D("0.01"), r.sell) if r.month == date(2014, 5, 1) else r
    with pytest.raises(annual.BridgeMismatchError, match="2014-05"):
        run(monkeypatch, FIRST, LAST, dry_run=False, saved=saved, annual_override=annual_tables(alter={"ar2014": bump}))
    assert saved == []


def test_2014_annual_report_never_contributes_rows(monkeypatch):
    saved = []
    run(monkeypatch, FIRST, LAST, dry_run=False, saved=saved)
    assert not any(o.source_url == annual.VALIDATION_REPORTS[0].url for o in saved[0])
    assert all(canon.series_of(o) == ftab.SOURCE_SERIES for o in saved[0] if o.month.year >= 2014)


# ------------------------------------------------------------ PostgreSQL
INIT = Path(__file__).parents[3] / "db/init"


def make_engine(*, migrate_013: bool):
    url = require_test_database_url()
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS fx_monthly_averages"))
        c.execute(text((INIT / "012_fx_monthly_averages.sql").read_text()))
        if migrate_013:
            c.execute(text((INIT / "013_fx_monthly_published_values.sql").read_text()))
    return eng


@pytest.fixture
def engine():
    eng = make_engine(migrate_013=True)
    yield eng
    eng.dispose()


@pytest.fixture
def engine012():
    eng = make_engine(migrate_013=False)
    yield eng
    eng.dispose()


def phase1_observations(monkeypatch) -> list[MonthlyObservation]:
    saved = []
    run(monkeypatch, date(2014, 1, 1), LAST, dry_run=False, saved=saved)
    return saved[0]


def seed_phase1_via_012(engine, observations):
    """What the real database holds today: migration-012 rows only, written before 013 existed."""
    with engine.begin() as c:
        for o in observations:
            c.execute(text(
                "INSERT INTO fx_monthly_averages (base_currency, quote_currency, observation_month, buy, mid, sell, source, "
                "source_series, source_publication, source_url, source_page, source_table, published_precision, currency_regime, "
                "imported_at) VALUES ('USD','ZMW',:m,:b,:mid,:s,'boz',:series,:pub,:url,:page,'Table 02',2,'ZMW',:at)"),
                {"m": o.month, "b": o.buy, "mid": o.mid, "s": o.sell, "series": ftab.SOURCE_SERIES, "pub": o.publication_title,
                 "url": o.source_url, "page": o.source_page, "at": NOW})


def real_run(monkeypatch, engine, *, start=FIRST, end=LAST):
    pipeline(monkeypatch)
    monkeypatch.setattr(job, "save_monthly_averages", save_monthly_averages)  # undo any earlier capture hook

    async def sleep(_):
        pass

    async def go():
        async with httpx.AsyncClient() as c:
            return await job.run_import(start, end, dry_run=False, client=c, now=NOW, sleep=sleep, engine=engine)

    return asyncio.run(go())


def test_pg_upgrade_path_153_to_201_and_idempotent_rerun(engine012, monkeypatch):
    seed_phase1_via_012(engine012, phase1_observations(monkeypatch))
    with engine012.begin() as c:
        c.execute(text((INIT / "013_fx_monthly_published_values.sql").read_text()))
    first = real_run(monkeypatch, engine012)
    assert (first["inserted"], first["already_present"]) == (48, 153)
    second = real_run(monkeypatch, engine012)
    assert (second["inserted"], second["already_present"]) == (0, 201)
    with engine012.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar() == 201


def test_pg_fresh_full_run_inserts_201_and_satisfies_every_invariant(engine, monkeypatch):
    assert real_run(monkeypatch, engine)["inserted"] == 201
    with engine.connect() as c:
        q = lambda sql: c.execute(text(sql)).scalar()
        rows = {r["observation_month"]: r for r in c.execute(text("SELECT * FROM fx_monthly_averages")).mappings()}
        assert q("SELECT count(*) FROM fx_monthly_averages") == 201
        assert (q("SELECT min(observation_month) FROM fx_monthly_averages"), q("SELECT max(observation_month) FROM fx_monthly_averages")) == (FIRST, LAST)
        assert q("SELECT count(DISTINCT observation_month) FROM fx_monthly_averages") == 201
        assert set(rows) == set(months(FIRST, LAST))  # no gaps
        assert q(f"SELECT count(*) FROM fx_monthly_averages WHERE source_series = '{tab.ANNUAL_SOURCE_SERIES}' "
                 "AND observation_month NOT BETWEEN '2010-01-01' AND '2013-12-01'") == 0
        assert q(f"SELECT count(*) FROM fx_monthly_averages WHERE source_series = '{ftab.SOURCE_SERIES}' "
                 "AND observation_month < '2014-01-01'") == 0
        assert q("SELECT count(*) FROM fx_monthly_averages WHERE observation_month < '2013-01-01' "
                 "AND (published_currency <> 'ZMK' OR normalization_divisor <> 1000)") == 0
        assert q("SELECT count(*) FROM fx_monthly_averages WHERE observation_month >= '2013-01-01' "
                 "AND (published_currency <> 'ZMW' OR normalization_divisor <> 1)") == 0
        assert q("SELECT count(*) FROM fx_monthly_averages WHERE buy * normalization_divisor <> published_buy "
                 "OR mid * normalization_divisor <> published_mid OR sell * normalization_divisor <> published_sell") == 0
        violating = [m for m, r in rows.items() if m < date(2014, 1, 1) and not r["published_buy"] <= r["published_mid"] <= r["published_sell"]]
        assert violating == [date(2012, 4, 1), date(2013, 12, 1)]
    for m, series, cur, div in ((date(2010, 1, 1), tab.ANNUAL_SOURCE_SERIES, "ZMK", 1000), (date(2012, 4, 1), tab.ANNUAL_SOURCE_SERIES, "ZMK", 1000),
                                (date(2012, 12, 1), tab.ANNUAL_SOURCE_SERIES, "ZMK", 1000), (date(2013, 1, 1), tab.ANNUAL_SOURCE_SERIES, "ZMW", 1),
                                (date(2013, 12, 1), tab.ANNUAL_SOURCE_SERIES, "ZMW", 1), (date(2014, 1, 1), ftab.SOURCE_SERIES, "ZMW", 1),
                                (LAST, ftab.SOURCE_SERIES, "ZMW", 1)):
        r = rows[m]
        assert (r["source_series"], r["published_currency"], r["normalization_divisor"]) == (series, cur, div), m
    assert rows[date(2012, 4, 1)]["buy"] == D("5.28360") and rows[date(2012, 4, 1)]["published_mid"] == D("5240.97")
    assert rows[date(2013, 12, 1)]["source_url"] == annual.CANONICAL_REPORTS[3].url and rows[date(2014, 1, 1)]["source_url"] == source.PUBLICATIONS[0].url
    assert not any(r["source_url"] == annual.VALIDATION_REPORTS[0].url for r in rows.values())


def test_pg_conflict_with_stored_phase1_row_rolls_everything_back(engine012, monkeypatch):
    obs = phase1_observations(monkeypatch)
    seed_phase1_via_012(engine012, obs)
    with engine012.begin() as c:
        c.execute(text((INIT / "013_fx_monthly_published_values.sql").read_text()))
        c.execute(text("UPDATE fx_monthly_averages SET buy = buy + 0.01, published_buy = published_buy + 0.01 "
                       "WHERE observation_month = '2020-05-01'"))
    with pytest.raises(MonthlyConflictError, match="2020-05"):
        real_run(monkeypatch, engine012)
    with engine012.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar() == 153  # none of the 48 were kept


def test_pg_unmigrated_database_fails_clearly_but_dry_run_still_works(engine012, monkeypatch):
    with pytest.raises(SchemaNotMigratedError, match="migration 013"):
        real_run(monkeypatch, engine012)
    with engine012.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_monthly_averages")).scalar() == 0
    result, _ = run(monkeypatch, FIRST, LAST)  # dry-run: no engine at all
    assert result["months_selected"] == 201


def test_pg_api_all_is_201_and_short_ranges_are_the_latest_months(engine, monkeypatch):
    real_run(monkeypatch, engine)
    monkeypatch.setattr(router_module, "fetch_fx_monthly", lambda r: fetch_fx_monthly(r, engine=engine))

    async def no_network(*a, **k):
        raise AssertionError("the monthly FX route must read PostgreSQL only")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    body = client.get("/api/v1/fx/USDZMW/monthly", params={"range": "ALL"})
    assert body.status_code == 200
    data = body.json()
    assert (data["count"], data["first_month"], data["last_month"]) == (201, "2010-01", "2026-09")
    all_months = [o["month"] for o in data["observations"]]
    assert all_months == [f"{m:%Y-%m}" for m in months(FIRST, LAST)]
    assert not {"published_buy", "published_currency", "normalization_divisor"} & set(data["observations"][0])
    for name, n in (("1Y", 12), ("3Y", 36), ("5Y", 60)):
        d = client.get("/api/v1/fx/USDZMW/monthly", params={"range": name}).json()
        assert d["count"] == n and [o["month"] for o in d["observations"]] == all_months[-n:] and d["last_month"] == "2026-09"
    assert client.get("/api/v1/fx/USDZMW/monthly", params={"range": "12"}).status_code == 422
    by_month = {o["month"]: o for o in data["observations"]}
    assert by_month["2013-12"]["source_series"] == tab.ANNUAL_SOURCE_SERIES and by_month["2014-01"]["source_series"] == ftab.SOURCE_SERIES
    assert by_month["2012-04"]["buy"] == "5.28360"


def test_pg_read_fails_closed_when_a_month_is_stored_under_two_approved_series(engine, monkeypatch):
    real_run(monkeypatch, engine)
    with engine.begin() as c:  # a second approved-series row for a month the Fortnightly chain already owns
        cols = [r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='fx_monthly_averages' "
                                             "AND column_name <> 'id' ORDER BY ordinal_position"))]
        select = ", ".join(f"'{tab.ANNUAL_SOURCE_SERIES}'" if k == "source_series" else k for k in cols)
        c.execute(text(f"INSERT INTO fx_monthly_averages ({', '.join(cols)}) SELECT {select} FROM fx_monthly_averages "
                       "WHERE observation_month = '2020-01-01'"))
    for name in ("1Y", "ALL"):
        with pytest.raises(AmbiguousCanonicalMonthError, match="2020-01"):
            fetch_fx_monthly(name, engine=engine)
    monkeypatch.setattr(router_module, "fetch_fx_monthly", lambda r: fetch_fx_monthly(r, engine=engine))
    assert client.get("/api/v1/fx/USDZMW/monthly", params={"range": "1Y"}).status_code == 503
