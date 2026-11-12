import asyncio
import copy
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

from app.config import Settings
from app.jobs import backfill_usdzmw as job
from app.schemas.fx import BOZ_SOURCE_URL
from app.services import boz, fx_store

NOW = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
TODAY = NOW.date()


@pytest.fixture
def window():
    return json.loads((Path(__file__).parent / "fixtures/boz_usd_history_window.json").read_text())


def row(ts, buy="19.0000", mid="19.0250", sell="19.0500"):
    return {"buying": buy, "mid_rate": mid, "selling": sell,
            "time": f'<time datetime="{ts}" class="datetime">x</time>\n'}


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_):
    return None


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_response(payload):
    return httpx.Response(200, json=payload)


# ---- parsing / normalization ----
def test_parse_observations_exact_decimals_utc_and_order(window):
    obs = boz.parse_boz_observations(window)
    assert len(obs) == 5
    stamps = sorted(obs)
    assert all(s.tzinfo == timezone.utc for s in stamps)
    assert stamps[0] == datetime(2026, 3, 3, 7, 30, tzinfo=timezone.utc)
    assert datetime(2026, 3, 4, 7, 30, 51, tzinfo=timezone.utc) in obs  # seconds preserved
    buy, mid, sell = obs[datetime(2026, 3, 3, 7, 30, tzinfo=timezone.utc)]
    assert (str(buy), str(mid), str(sell)) == ("19.3800", "19.4040", "19.4300")
    assert all(isinstance(v, Decimal) for rates in obs.values() for v in rates)


def test_empty_window_allowed_only_when_requested():
    assert boz.parse_boz_observations([], allow_empty=True) == {}
    with pytest.raises(ValueError):
        boz.parse_boz_observations([])


def test_identical_duplicates_collapse_conflicts_fail(window):
    assert len(boz.parse_boz_observations(window + [copy.deepcopy(window[0])])) == 5
    conflicting = copy.deepcopy(window[0]); conflicting["mid_rate"] = "19.9999"
    with pytest.raises(ValueError, match="Conflicting"):
        boz.parse_boz_observations(window + [conflicting])


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(buying="19,61"),
    lambda r: r.update(mid_rate=19.635),            # float, not the published string
    lambda r: r.update(selling="0"),
    lambda r: r.pop("buying"),
    lambda r: r.update(time="<time datetime=\"2026-03-04T15:30:00+02:00\">x</time>"),  # not UTC
    lambda r: r.update(time="4 Mar 2026 - 15:30"),  # display text only
    lambda r: r.update(time=None),
])
def test_malformed_rows_fail_explicitly(window, mutate):
    mutate(window[0])
    with pytest.raises(ValueError):
        boz.parse_boz_observations(window)


def test_non_list_payload_fails():
    with pytest.raises(ValueError):
        boz.parse_boz_observations({"value": []}, allow_empty=True)


# ---- window planning / bounds ----
def test_plan_windows_split_and_contiguous():
    windows = job.plan_windows(date(2026, 1, 1), date(2026, 3, 10), today=TODAY)
    assert windows[0][0] == date(2026, 1, 1) and windows[-1][1] == date(2026, 3, 10)
    assert all((b - a).days + 1 <= job.WINDOW_DAYS for a, b in windows)
    assert all(windows[i][1] + timedelta(days=1) == windows[i + 1][0] for i in range(len(windows) - 1))


def test_plan_single_day():
    assert job.plan_windows(TODAY, TODAY, today=TODAY) == [(TODAY, TODAY)]


@pytest.mark.parametrize("start,end", [
    (date(2026, 5, 2), date(2026, 5, 1)),            # reversed
    (date(2026, 1, 1), TODAY + timedelta(days=1)),   # future
    (TODAY - timedelta(days=job.MAX_SPAN_DAYS), TODAY),  # too wide
])
def test_plan_rejects_bad_ranges(start, end):
    with pytest.raises(job.BackfillError):
        job.plan_windows(start, end, today=TODAY)


# ---- fetching ----
def test_fetch_sends_bounded_filters_sequentially(window, monkeypatch):
    monkeypatch.setattr(boz, "get_settings", lambda: Settings(boz_usd_rates_url="https://boz.example.test/rates"))
    seen, sleeps = [], []
    def handler(request):
        seen.append(dict(request.url.params))
        assert request.headers["User-Agent"] == boz.BOZ_USER_AGENT
        return json_response(window if len(seen) == 1 else [])
    async def record_sleep(seconds):
        sleeps.append(seconds)
    async def go():
        async with mock_client(handler) as client:
            return await job.fetch_observations(
                job.plan_windows(date(2026, 3, 1), date(2026, 4, 15), today=TODAY),
                client=client, now=NOW, pause=2.0, sleep=record_sleep)
    obs, not_selected = run(go())
    assert len(obs) == 5 and not_selected == []
    assert seen[0] == {"time[min]": "2026-03-01 00:00:00", "time[max]": "2026-03-31 23:59:59"}
    assert seen[1] == {"time[min]": "2026-04-01 00:00:00", "time[max]": "2026-04-15 23:59:59"}
    assert sleeps == [2.0]


def test_truncated_window_fails():
    rows = [row(f"2026-03-04T07:30:{i % 60:02d}Z") for i in range(job.ROW_CAP)]
    async def go():
        async with mock_client(lambda r: json_response(rows)) as client:
            return await job.fetch_observations([(date(2026, 3, 1), date(2026, 3, 31))], client=client, now=NOW, sleep=no_sleep)
    with pytest.raises(job.BackfillError, match="truncated"):
        run(go())


def test_future_observation_fails():
    async def go():
        async with mock_client(lambda r: json_response([row("2026-11-13T07:30:00Z")])) as client:
            return await job.fetch_observations([(date(2026, 11, 12), date(2026, 11, 12))], client=client, now=NOW, sleep=no_sleep)
    with pytest.raises(ValueError, match="future"):
        run(go())


def test_conflict_across_windows_fails():
    answers = iter([[row("2026-03-04T07:30:00Z")], [row("2026-03-04T07:30:00Z", mid="19.9999")]])
    async def go():
        async with mock_client(lambda r: json_response(next(answers))) as client:
            return await job.fetch_observations(
                [(date(2026, 3, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 2))],
                client=client, now=NOW, sleep=no_sleep)
    with pytest.raises(ValueError, match="Conflicting"):
        run(go())


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, content="<html/>", headers={"Content-Type": "text/html"}),
])
def test_upstream_failures_propagate(response):
    async def go():
        async with mock_client(lambda r: response) as client:
            return await job.fetch_observations([(date(2026, 3, 1), date(2026, 3, 2))], client=client, now=NOW, sleep=no_sleep)
    with pytest.raises((httpx.HTTPError, ValueError)):
        run(go())


def test_off_slot_rows_are_not_selected_and_reported_not_altered(window, capsys):
    odd = row("2026-03-04T08:00:00Z", buy="2.7448", mid="2.7486", sell="2.7524")
    async def go():
        async with mock_client(lambda r: json_response(window + [odd])) as client:
            return await job.run_backfill(date(2026, 3, 3), date(2026, 3, 4), dry_run=True,
                                          client=client, now=NOW, sleep=no_sleep)
    result = run(go())
    assert result["selected"] == 5 and result["not_selected"] == 1
    err = capsys.readouterr().err
    assert "2026-03-04T08:00:00+00:00" in err and "not selected" in err and "invalid" not in err.replace("never", "")


def test_malformed_off_slot_row_is_invalid_not_merely_unselected(window):
    bad = row("2026-03-04T08:00:00Z", mid="oops")
    async def go():
        async with mock_client(lambda r: json_response(window + [bad])) as client:
            return await job.fetch_observations([(date(2026, 3, 3), date(2026, 3, 4))], client=client, now=NOW, sleep=no_sleep)
    with pytest.raises(ValueError):
        run(go())


# ---- run / CLI without a database ----
def test_dry_run_never_touches_database(window):
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("database used during dry run")
    async def go():
        async with mock_client(lambda r: json_response(window)) as client:
            return await job.run_backfill(date(2026, 3, 3), date(2026, 3, 4), dry_run=True,
                                          client=client, engine=Exploding(), now=NOW, sleep=no_sleep)
    assert run(go()) == {"windows": 1, "selected": 5, "not_selected": 0, "inserted": 0, "already_present": 0}


def test_empty_dataset_inserts_nothing_and_skips_database():
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("database used for empty dataset")
    async def go():
        async with mock_client(lambda r: json_response([])) as client:
            return await job.run_backfill(date(2025, 1, 1), date(2025, 1, 7), client=client,
                                          engine=Exploding(), now=NOW, sleep=no_sleep)
    assert run(go())["selected"] == 0


def test_cli_reports_failure_without_details(monkeypatch, capsys):
    async def boom(*a, **k):
        raise RuntimeError("postgresql://user:secret@host/db")
    monkeypatch.setattr(job, "run_backfill", boom)
    assert job.main(["--from", "2026-03-01", "--to", "2026-03-02"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "secret" not in err


def test_cli_requires_from_and_validates_dates(capsys):
    with pytest.raises(SystemExit):
        job.main([])
    with pytest.raises(SystemExit):
        job.main(["--from", "not-a-date"])


def test_cli_range_error_exit_code(capsys):
    assert job.main(["--from", "2026-05-02", "--to", "2026-05-01"]) == 1
    assert "must not be after" in capsys.readouterr().err


# ---- real PostgreSQL (opt-in, disposable *_test database) ----
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    eng = create_engine(url)
    root = Path(__file__).parents[3] / "db/init"
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS fx_rates"))
        c.execute(text((root / "001_schema.sql").read_text()))
        c.execute(text((root / "002_fx_rates_provenance.sql").read_text()))
    yield eng
    eng.dispose()


def backfill(engine, payload):
    async def go():
        async with mock_client(lambda r: json_response(payload)) as client:
            return await job.run_backfill(date(2026, 3, 3), date(2026, 3, 4), client=client,
                                          engine=engine, now=NOW, sleep=no_sleep)
    return run(go())


def test_pg_backfill_idempotent_with_provenance(engine, window):
    assert backfill(engine, window) == {"windows": 1, "selected": 5, "not_selected": 0, "inserted": 5, "already_present": 0}
    assert backfill(engine, window) == {"windows": 1, "selected": 5, "not_selected": 0, "inserted": 0, "already_present": 5}
    with engine.connect() as c:
        rows = c.execute(text("SELECT * FROM fx_rates ORDER BY observed_at")).mappings().all()
    assert len(rows) == 5
    first = rows[0]
    assert (first["base_currency"], first["quote_currency"], first["source"]) == ("USD", "ZMW", "boz")
    assert first["source_url"] == BOZ_SOURCE_URL and first["retrieved_at"] == NOW
    assert str(first["buy"]) == "19.3800" and isinstance(first["mid"], Decimal)
    assert [r["observed_at"] for r in rows] == sorted(r["observed_at"] for r in rows)
    assert not set(first) & {"payload", "raw", "raw_payload", "response"}


def test_pg_conflict_is_not_overwritten_and_batch_is_atomic(engine, window):
    backfill(engine, window)
    changed = copy.deepcopy(window)
    changed[0]["mid_rate"] = "99.0000"            # conflicts with stored row
    changed.append(row("2026-03-04T08:30:00Z"))    # new row must NOT be inserted
    with pytest.raises(fx_store.FxConflictError):
        backfill(engine, changed)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM fx_rates")).scalar() == 5
        assert c.execute(text("SELECT mid FROM fx_rates WHERE observed_at = '2026-03-04T13:30:00Z'")).scalar() == Decimal("19.6350")


def test_pg_equal_values_with_different_scale_are_not_conflicts(engine, window):
    backfill(engine, window)
    rescaled = copy.deepcopy(window); rescaled[0]["buying"] = "19.61"
    assert backfill(engine, rescaled)["already_present"] == 5


def test_pg_live_quote_row_and_backfill_coexist_and_history_reads_them(engine, window):
    from app.schemas.fx import FxQuote
    live = FxQuote(buy="19.6100", mid="19.6350", sell="19.6600",
                   observed_at=datetime(2026, 3, 4, 13, 30, tzinfo=timezone.utc), retrieved_at=NOW, stale=False)
    assert fx_store.save_fx_quote(live, engine=engine) is True
    assert backfill(engine, window)["already_present"] == 1   # same timestamp, same values
    history = fx_store.fetch_fx_history("ALL", now=NOW, engine=engine)
    assert [p.observed_at for p in history] == sorted(p.observed_at for p in history) and len(history) == 5


# ---- live / historical selection parity (source-selection policy) ----
def test_policy_hours_and_hour_only_comparison():
    assert boz.OFFICIAL_SLOT_UTC_HOURS == {7, 10, 13}
    stamps = [datetime(2026, 3, 4, h, m, tzinfo=timezone.utc) for h, m in [(7, 30), (7, 59), (8, 0), (10, 30), (13, 30), (14, 0)]]
    selected, not_selected = boz.select_official({t: (Decimal(1),) * 3 for t in stamps})
    assert sorted(selected) == stamps[:2] + stamps[3:5]   # minutes are not compared, as on the BoZ site
    assert not_selected == [stamps[2], stamps[5]]


def test_off_slot_row_is_never_live_and_never_stored():
    # An anomalous, newer off-slot row (plausibly mis-published) must not become the live quote
    # nor enter history: both paths apply the same selection.
    payload = [row("2026-03-04T10:30:00Z", mid="19.6150"), row("2026-03-04T11:00:00Z", mid="2.7486")]
    quote = boz.parse_boz_quote(payload, retrieved_at=NOW)
    assert (quote.observed_at.hour, str(quote.mid)) == (10, "19.6150")
    selected, not_selected = boz.select_official(boz.parse_boz_observations(payload))
    assert max(selected) == quote.observed_at and len(not_selected) == 1


def test_only_off_slot_rows_fail_closed_live():
    with pytest.raises(ValueError, match="official publication slot"):
        boz.parse_boz_quote([row("2026-03-04T11:00:00Z")], retrieved_at=NOW)


def test_live_still_validates_unselected_rows_strictly():
    for bad in (row("2026-03-04T11:00:00Z", mid="oops"), row("2026-03-04T11:00:00+02:00")):
        with pytest.raises(ValueError):
            boz.parse_boz_quote([row("2026-03-04T10:30:00Z"), bad], retrieved_at=NOW)
    conflict = [row("2026-03-04T11:00:00Z"), row("2026-03-04T11:00:00Z", mid="9.9999"), row("2026-03-04T10:30:00Z")]
    with pytest.raises(ValueError, match="Conflicting"):
        boz.parse_boz_quote(conflict, retrieved_at=NOW)


def test_live_and_backfill_select_same_latest_for_fixture(window):
    quote = boz.parse_boz_quote(window, retrieved_at=NOW)
    selected, _ = boz.select_official(boz.parse_boz_observations(window))
    assert quote.observed_at == max(selected)
