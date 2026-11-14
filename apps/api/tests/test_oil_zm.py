import importlib
import os
import random
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import get_args
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.oil import OilPoint
from app.schemas.oil_zm import OIL_ZM_METHODOLOGY, OIL_ZM_NOTICE, FxCoverage, OilZmMonth, OilZmPoint
from app.services import oil_store, oil_zm
from app.services.oil_zm import BrentMonth, FxMid, derive_oil_zm, lusaka_date

router_module = importlib.import_module("app.api.router")
TODAY = date(2026, 11, 14)
RELEASE_URL = "https://thedocs.worldbank.org/en/doc/abc/related/CMO-Historical-Data-Monthly.xlsx"


def utc(text_: str) -> datetime:
    return datetime.fromisoformat(text_.replace("Z", "+00:00"))


def fx(moment: str, mid: str) -> FxMid:
    return FxMid(observed_at=utc(moment), mid=Decimal(mid))


def brent(year: int, month: int, value: str, publication=date(2026, 11, 3)) -> BrentMonth:
    first = date(year, month, 1)
    last = date.fromordinal(date(year + (month == 12), month % 12 + 1, 1).toordinal() - 1)
    return BrentMonth(first, last, Decimal(value), publication, RELEASE_URL)


def derive(brent_months, fx_obs, today=TODAY):
    return derive_oil_zm(brent_months, fx_obs, today=today)


def dec40(expression):
    """Reference arithmetic at the same 40-digit context, without trailing zeros."""
    with localcontext() as ctx:
        ctx.prec = 40
        return Decimal(format(expression().normalize(), "f"))


# ---------------- derivation: aggregation and exact arithmetic ----------------
def test_one_day_one_observation():
    months, omitted = derive([brent(2026, 3, "100")], [fx("2026-03-01T07:30:00Z", "20")])
    (m,) = months
    assert omitted == 0
    assert (m.zmw_per_bbl, m.usd_zmw_observed_days_avg, m.brent_usd_per_bbl) == (Decimal("2000"), Decimal("20"), Decimal("100"))
    assert (m.fx_day_count, m.fx_observation_count) == (1, 1)
    assert m.fx_first_observation == m.fx_last_observation == utc("2026-03-01T07:30:00Z")
    assert (m.period_start, m.period_end) == (date(2026, 3, 1), date(2026, 3, 31))


def test_one_day_three_observations_are_averaged_within_the_day():
    obs = [fx("2026-03-02T07:30:00Z", "20.0"), fx("2026-03-02T10:30:00Z", "20.3"), fx("2026-03-02T13:30:00Z", "20.7")]
    (m,), _ = derive([brent(2026, 3, "100")], obs)
    expected_fx = dec40(lambda: (Decimal("20.0") + Decimal("20.3") + Decimal("20.7")) / 3)
    assert m.usd_zmw_observed_days_avg == expected_fx and m.fx_day_count == 1 and m.fx_observation_count == 3
    assert m.zmw_per_bbl == dec40(lambda: Decimal("100") * ((Decimal("20.0") + Decimal("20.3") + Decimal("20.7")) / 3))


def test_multiple_days_are_averaged_across_days():
    obs = [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-03T07:30:00Z", "21"), fx("2026-03-04T07:30:00Z", "22")]
    (m,), _ = derive([brent(2026, 3, "100")], obs)
    assert (m.usd_zmw_observed_days_avg, m.zmw_per_bbl, m.fx_day_count, m.fx_observation_count) == (Decimal("21"), Decimal("2100"), 3, 3)


def test_days_are_weighted_equally_not_by_observation_count():
    """Day 1 has three observations, day 2 has one: mean of daily means (21.5), NOT the mean of all four (20.75)."""
    obs = [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-02T10:30:00Z", "20"), fx("2026-03-02T13:30:00Z", "20"),
           fx("2026-03-03T07:30:00Z", "23")]
    (m,), _ = derive([brent(2026, 3, "100")], obs)
    flat_mean = (Decimal(20) * 3 + Decimal(23)) / 4
    assert flat_mean == Decimal("20.75") and m.usd_zmw_observed_days_avg != flat_mean
    assert m.usd_zmw_observed_days_avg == Decimal("21.5") and m.zmw_per_bbl == Decimal("2150")
    assert (m.fx_day_count, m.fx_observation_count) == (2, 4)


def test_decimal_arithmetic_is_exact_not_float():
    (m,), _ = derive([brent(2026, 3, "125")], [fx("2026-03-02T07:30:00Z", "22.45")])
    assert m.zmw_per_bbl == Decimal("2806.25") and str(m.zmw_per_bbl) == "2806.25" and isinstance(m.zmw_per_bbl, Decimal)
    # 0.1 + 0.2 is 0.30000000000000004 in binary floats; Decimal gives exactly 0.15 as the day mean, 0.45 for Brent 3
    (m,), _ = derive([brent(2026, 3, "3")], [fx("2026-03-02T07:30:00Z", "0.1"), fx("2026-03-02T10:30:00Z", "0.2")])
    assert (m.usd_zmw_observed_days_avg, m.zmw_per_bbl) == (Decimal("0.15"), Decimal("0.45")) and str(m.zmw_per_bbl) == "0.45"


def test_repeating_decimals_keep_forty_significant_digits_without_early_rounding():
    obs = [fx("2026-03-02T07:30:00Z", "1"), fx("2026-03-03T07:30:00Z", "1"), fx("2026-03-04T07:30:00Z", "2")]
    (m,), _ = derive([brent(2026, 3, "100")], obs)
    assert m.usd_zmw_observed_days_avg == dec40(lambda: Decimal(4) / Decimal(3))
    assert str(m.usd_zmw_observed_days_avg) == "1." + "3" * 39  # 40 significant digits, nothing rounded to 2 or 4 places
    assert m.zmw_per_bbl == dec40(lambda: Decimal(100) * (Decimal(4) / Decimal(3)))


def test_negative_brent_still_multiplies_exactly():
    (m,), _ = derive([brent(2026, 3, "-3.5")], [fx("2026-03-02T07:30:00Z", "20")])
    assert (m.brent_usd_per_bbl, m.zmw_per_bbl) == (Decimal("-3.5"), Decimal("-70"))


# ---------------- derivation: availability ----------------
def test_month_without_brent_is_not_derived_even_with_fx():
    months, omitted = derive([brent(2026, 3, "100")], [fx("2026-03-02T07:30:00Z", "20"), fx("2026-04-02T07:30:00Z", "21")])
    assert [m.period_start for m in months] == [date(2026, 3, 1)] and omitted == 0


def test_brent_month_without_fx_is_omitted_and_counted_never_filled():
    months, omitted = derive([brent(2026, 3, "100"), brent(2026, 4, "110")], [fx("2026-03-02T07:30:00Z", "20")])
    assert [m.period_start for m in months] == [date(2026, 3, 1)] and omitted == 1


def test_no_fx_at_all_derives_nothing():
    months, omitted = derive([brent(2026, 3, "100"), brent(2026, 4, "110")], [])
    assert months == [] and omitted == 2


def test_overlap_only_pre_boz_brent_months_are_omitted_not_extended():
    months, omitted = derive([brent(2025, 12, "62.7"), brent(2026, 1, "66.8")], [fx("2026-01-22T07:30:00Z", "19.6")])
    assert [m.period_start for m in months] == [date(2026, 1, 1)] and omitted == 1


# ---------------- derivation: calendar handling (Africa/Lusaka) ----------------
def test_late_utc_observation_belongs_to_the_next_lusaka_day_and_month():
    """2026-01-31 23:30Z is 2026-02-01 01:30 in Lusaka (UTC+2): February, not January."""
    obs = [fx("2026-01-31T21:59:00Z", "19"), fx("2026-01-31T23:30:00Z", "20"), fx("2026-02-28T22:30:00Z", "21")]
    assert lusaka_date(obs[0].observed_at) == date(2026, 1, 31) and lusaka_date(obs[1].observed_at) == date(2026, 2, 1)
    months, _ = derive([brent(2026, 1, "100"), brent(2026, 2, "100"), brent(2026, 3, "100")], obs)
    by_start = {m.period_start: m for m in months}
    assert by_start[date(2026, 1, 1)].usd_zmw_observed_days_avg == Decimal("19") and by_start[date(2026, 1, 1)].fx_observation_count == 1
    assert by_start[date(2026, 2, 1)].usd_zmw_observed_days_avg == Decimal("20") and by_start[date(2026, 2, 1)].fx_first_date == date(2026, 2, 1)
    assert by_start[date(2026, 3, 1)].usd_zmw_observed_days_avg == Decimal("21") and by_start[date(2026, 3, 1)].fx_first_date == date(2026, 3, 1)  # Feb 28 22:30Z = Mar 1 Lusaka


def test_observations_group_by_lusaka_date_not_utc_date():
    obs = [fx("2026-02-01T22:30:00Z", "20"), fx("2026-02-02T08:00:00Z", "22")]  # both Lusaka 2 Feb, different UTC days
    (m,), _ = derive([brent(2026, 2, "100")], obs)
    assert (m.fx_day_count, m.fx_observation_count, m.usd_zmw_observed_days_avg) == (1, 2, Decimal("21"))
    assert (m.fx_first_date, m.fx_last_date) == (date(2026, 2, 2), date(2026, 2, 2))


def test_january_and_february_are_independent_months():
    obs = [fx("2026-01-22T07:30:00Z", "19.6"), fx("2026-01-23T07:30:00Z", "19.8"), fx("2026-02-16T07:30:00Z", "18.3")]
    months, _ = derive([brent(2026, 1, "66.8"), brent(2026, 2, "71.1")], obs)
    assert [(m.period_start, m.usd_zmw_observed_days_avg, m.fx_day_count) for m in months] == [
        (date(2026, 1, 1), Decimal("19.7"), 2), (date(2026, 2, 1), Decimal("18.3"), 1)]


def test_leap_february_period_and_boundary_observations():
    obs = [fx("2024-02-01T07:30:00Z", "20"), fx("2024-02-29T07:30:00Z", "22"), fx("2024-03-01T07:30:00Z", "30")]
    months, _ = derive([brent(2024, 2, "100"), brent(2024, 3, "100")], obs)
    feb, mar = months
    assert (feb.period_start, feb.period_end) == (date(2024, 2, 1), date(2024, 2, 29))
    assert (feb.fx_day_count, feb.usd_zmw_observed_days_avg, feb.fx_last_date) == (2, Decimal("21"), date(2024, 2, 29))
    assert (mar.fx_day_count, mar.usd_zmw_observed_days_avg) == (1, Decimal("30"))


def test_output_is_chronological_and_independent_of_input_order():
    brent_months = [brent(2026, m, "100") for m in (1, 2, 3, 4)]
    obs = [fx(f"2026-{m:02d}-{d:02d}T{h:02d}:30:00Z", f"{19 + m / 10}") for m in (1, 2, 3, 4) for d in (3, 4) for h in (7, 10)]
    expected, omitted = derive(brent_months, obs)
    shuffled_b, shuffled_f = brent_months[:], obs[:]
    random.Random(7).shuffle(shuffled_b); random.Random(8).shuffle(shuffled_f)
    got, got_omitted = derive(shuffled_b, shuffled_f)
    assert got == expected and got_omitted == omitted
    assert [m.period_start for m in got] == sorted(m.period_start for m in got)


# ---------------- derivation: coverage transparency ----------------
def test_month_containing_the_start_of_stored_boz_history_is_partial():
    obs = [fx("2026-01-22T07:30:00Z", "19.6"), fx("2026-02-16T07:30:00Z", "18.3")]
    january, february = derive([brent(2026, 1, "66.8"), brent(2026, 2, "71.1")], obs)[0]
    assert january.fx_coverage == "partial_source_window" and (january.fx_first_date, january.fx_last_date) == (date(2026, 1, 22), date(2026, 1, 22))
    assert february.fx_coverage == "observed_days"  # not claimed complete either: only that the stored window begins earlier


@pytest.mark.parametrize("first_day,expected", [("2026-03-01T07:30:00Z", "observed_days"), ("2026-03-02T07:30:00Z", "partial_source_window"),
                                                ("2026-03-31T07:30:00Z", "partial_source_window")])
def test_partial_flag_depends_on_when_the_stored_window_begins(first_day, expected):
    (m,), _ = derive([brent(2026, 3, "100")], [fx(first_day, "20")])
    assert m.fx_coverage == expected


def test_window_start_in_a_month_without_brent_does_not_flag_later_months():
    obs = [fx("2026-01-22T07:30:00Z", "19.6"), fx("2026-03-02T07:30:00Z", "19")]
    (march,), omitted = derive([brent(2026, 3, "100")], obs)
    assert march.fx_coverage == "observed_days" and omitted == 0


def test_components_and_provenance_are_exposed_and_the_result_is_marked_derived():
    (m,), _ = derive([brent(2026, 3, "100", publication=date(2026, 4, 2))], [fx("2026-03-02T07:30:00Z", "20"), fx("2026-03-03T12:00:00Z", "22")])
    assert (m.classification, m.derived, m.currency, m.unit) == ("derived", True, "ZMW", "barrel")
    assert (m.methodology, m.notice) == (OIL_ZM_METHODOLOGY, OIL_ZM_NOTICE) and "Not a Zambia retail fuel price" in m.notice
    assert (m.brent_source, m.brent_source_url, m.brent_publication_date) == ("world_bank", RELEASE_URL, date(2026, 4, 2))
    assert m.brent_source_page_url == "https://www.worldbank.org/en/research/commodity-markets"
    assert (m.fx_source, m.fx_pair, m.fx_price) == ("boz", "USDZMW", "mid") and m.fx_source_url.startswith("https://www.boz.zm/")
    assert (m.fx_first_observation, m.fx_last_observation) == (utc("2026-03-02T07:30:00Z"), utc("2026-03-03T12:00:00Z"))


def test_staleness_reuses_the_monthly_oil_threshold():
    obs = [fx("2026-10-01T07:30:00Z", "20"), fx("2026-08-03T07:30:00Z", "20")]
    months, _ = derive([brent(2026, 8, "100"), brent(2026, 10, "100")], obs)
    assert [(m.period_start.month, m.stale) for m in months] == [(8, True), (10, False)] and months[0].stale_after_months == 2


# ---------------- sparse months, vocabulary and the UTC year boundary ----------------
def test_february_style_month_with_one_day_and_two_observations_is_valid_and_not_partial():
    """BoZ's own source has only two rows in February 2026: still derivable, flagged by counts, not as a source-window start."""
    obs = [fx("2026-01-22T07:30:00Z", "19.6"), fx("2026-02-16T07:30:00Z", "18.2481"), fx("2026-02-16T10:30:00Z", "18.3325")]
    january, february = derive([brent(2026, 1, "66.8"), brent(2026, 2, "71.1")], obs)[0]
    assert january.fx_coverage == "partial_source_window"  # stored BoZ history begins on 22 Jan
    assert (february.fx_day_count, february.fx_observation_count) == (1, 2)
    assert february.fx_coverage == "observed_days"  # sparse, but NOT partial_source_window and NOT a completeness claim
    expected_fx = dec40(lambda: (Decimal("18.2481") + Decimal("18.3325")) / 2)
    assert february.usd_zmw_observed_days_avg == expected_fx
    assert february.zmw_per_bbl == dec40(lambda: Decimal("71.1") * ((Decimal("18.2481") + Decimal("18.3325")) / 2))
    assert (february.fx_first_date, february.fx_last_date) == (date(2026, 2, 16), date(2026, 2, 16))


def test_coverage_vocabulary_makes_no_completeness_claim():
    assert set(get_args(FxCoverage)) == {"partial_source_window", "observed_days"}
    point = dict(period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), zmw_per_bbl=Decimal(1), brent_usd_per_bbl=Decimal(1),
                 usd_zmw_observed_days_avg=Decimal(1), fx_day_count=1, fx_observation_count=1)
    assert OilZmPoint(**point, fx_coverage="observed_days").fx_coverage == "observed_days"
    for retired_or_overclaiming in ("observed_month", "complete", "full_month"):
        with pytest.raises(ValidationError):
            OilZmPoint(**point, fx_coverage=retired_or_overclaiming)
    assert "usd_zmw_monthly_avg" not in OilZmPoint.model_fields  # the old name implied a full-month average


def test_methodology_names_stored_observation_days_and_equal_weighting():
    assert "for stored observation days" in OIL_ZM_METHODOLOGY and "weighted equally" in OIL_ZM_METHODOLOGY
    assert "monthly average BoZ" not in OIL_ZM_METHODOLOGY and OIL_ZM_METHODOLOGY.startswith("World Bank Brent monthly average ×")


def test_utc_year_boundary_groups_into_the_next_lusaka_day_month_and_year():
    """2025-12-31 23:30 UTC is 2026-01-01 01:30 in Lusaka: it belongs to January 2026, not December 2025."""
    obs = [fx("2025-12-31T21:59:00Z", "19"), fx("2025-12-31T23:30:00Z", "20"), fx("2026-01-02T07:30:00Z", "22")]
    assert lusaka_date(obs[0].observed_at) == date(2025, 12, 31) and lusaka_date(obs[1].observed_at) == date(2026, 1, 1)
    (december, january), omitted = derive([brent(2025, 12, "60"), brent(2026, 1, "70")], obs, today=date(2026, 2, 10))
    assert omitted == 0
    assert (december.fx_day_count, december.fx_observation_count, december.usd_zmw_observed_days_avg) == (1, 1, Decimal("19"))
    assert (january.fx_day_count, january.fx_observation_count) == (2, 2)
    assert (january.fx_first_date, january.fx_last_date) == (date(2026, 1, 1), date(2026, 1, 2))
    assert january.usd_zmw_observed_days_avg == Decimal("21") and january.zmw_per_bbl == Decimal("1470")  # mean(20, 22) x 70
    assert january.period_start.year == 2026 and december.period_start.year == 2025


# ---------------- API (offline) ----------------
client = TestClient(app)


def month(year=2026, mon=10, **kw) -> OilZmMonth:
    brent_months = [brent(year, mon, kw.pop("brent", "125"))]
    (built,), _ = derive(brent_months, [fx(f"{year}-{mon:02d}-{kw.pop('day', 1):02d}T07:30:00Z", kw.pop("mid", "22.45"))])
    return built


def test_latest_returns_the_newest_derived_month_with_components_and_provenance(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: seen.append((r, today)) or ([month(2026, 9), month(2026, 10)], 0))
    monkeypatch.setattr(router_module, "_lusaka_today", lambda: date(2026, 12, 1))
    body = client.get("/api/v1/oil/zm/latest").json()
    assert seen == [("ALL", date(2026, 12, 1))]
    assert (body["period_start"], body["classification"], body["derived"]) == ("2026-10-01", "derived", True)
    assert (body["zmw_per_bbl"], body["brent_usd_per_bbl"], body["usd_zmw_observed_days_avg"]) == ("2806.25", "125", "22.45")
    assert (body["fx_day_count"], body["fx_observation_count"], body["fx_coverage"]) == (1, 1, "observed_days")
    assert body["methodology"] == OIL_ZM_METHODOLOGY and body["notice"] == OIL_ZM_NOTICE
    assert (body["brent_source"], body["fx_source"], body["fx_pair"], body["fx_price"]) == ("world_bank", "boz", "USDZMW", "mid")
    assert body["brent_source_url"] == RELEASE_URL and body["brent_publication_date"] == "2026-11-03"
    assert body["fx_first_observation"].endswith("Z") and body["fx_first_date"] == "2026-10-01"
    assert (body["currency"], body["unit"], body["stale"], body["stale_after_months"]) == ("ZMW", "barrel", False, 2)


def test_api_exposes_the_observed_days_field_and_never_the_old_monthly_average_name(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: ([month(2026, 10)], 0))
    latest = client.get("/api/v1/oil/zm/latest").json()
    point = client.get("/api/v1/oil/zm/history?range=ALL").json()["observations"][0]
    for body in (latest, point):
        assert body["usd_zmw_observed_days_avg"] == "22.45" and "usd_zmw_monthly_avg" not in body
        assert body["fx_coverage"] == "observed_days"
    assert "for stored observation days" in latest["methodology"]
    assert client.get("/api/v1/oil/zm/history?range=ALL").json()["methodology"] == latest["methodology"]


def test_a_partial_source_window_month_is_visible_through_the_api(monkeypatch):
    partial = month(2026, 1, brent="66.8", mid="19.6", day=22)
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: ([partial], 0))
    latest = client.get("/api/v1/oil/zm/latest").json()
    history = client.get("/api/v1/oil/zm/history?range=ALL").json()["observations"][0]
    assert latest["fx_coverage"] == history["fx_coverage"] == "partial_source_window"
    assert (latest["fx_first_date"], latest["fx_last_date"], latest["fx_day_count"], latest["fx_observation_count"]) == ("2026-01-22", "2026-01-22", 1, 1)


def test_latest_without_an_overlapping_month_is_a_normal_404(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: ([], 3))
    response = client.get("/api/v1/oil/zm/latest")
    assert response.status_code == 404 and "BoZ" in response.json()["detail"]


def test_history_passes_the_range_returns_slim_points_and_counts_omissions(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: seen.append(r) or ([month(2026, 9), month(2026, 10)], 12))
    body = client.get("/api/v1/oil/zm/history?range=3Y").json()
    assert seen == ["3Y"] and (body["range"], body["count"], body["brent_months_without_fx"]) == ("3Y", 2, 12)
    assert [o["period_start"] for o in body["observations"]] == ["2026-09-01", "2026-10-01"]
    point = body["observations"][1]
    assert {"zmw_per_bbl", "brent_usd_per_bbl", "usd_zmw_observed_days_avg", "fx_day_count", "fx_observation_count", "fx_coverage", "classification", "derived"} <= set(point)
    assert "brent_source_url" not in point and "methodology" not in point  # provenance lives on /latest
    assert body["methodology"] == OIL_ZM_METHODOLOGY
    assert client.get("/api/v1/oil/zm/history").json()["range"] == "1Y" and seen[-1] == "1Y"
    for r in ("1M", "2Y", "all", ""):
        assert client.get(f"/api/v1/oil/zm/history?range={r}").status_code == 422


def test_history_with_no_overlap_is_a_normal_empty_response(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: ([], 12))
    assert client.get("/api/v1/oil/zm/history?range=ALL").json() == {
        "range": "ALL", "count": 0, "brent_months_without_fx": 12, "methodology_id": "oil_zm_v1",
        "methodology": OIL_ZM_METHODOLOGY, "observations": []}


@pytest.mark.parametrize("path", ["/api/v1/oil/zm/latest", "/api/v1/oil/zm/history"])
def test_database_failure_is_a_safe_503(monkeypatch, path):
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 2806.25")
    monkeypatch.setattr(router_module, "fetch_oil_zm", boom)
    response = client.get(path)
    assert response.status_code == 503 and "SECRET" not in response.text and "2806.25" not in response.text


def test_zm_routes_never_reach_an_upstream_source(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("OIL ZM must read PostgreSQL only")
    from app.services import worldbank_oil
    monkeypatch.setattr(worldbank_oil, "download_workbook", no_network)
    monkeypatch.setattr(worldbank_oil, "discover_workbook_url", no_network)
    monkeypatch.setattr(router_module, "get_boz_fx_quote", no_network)
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: ([month()], 0))
    assert client.get("/api/v1/oil/zm/latest").status_code == 200 and client.get("/api/v1/oil/zm/history").status_code == 200


def test_zm_routes_do_not_disturb_the_brent_and_wti_routes(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_oil_zm", lambda r, today: ([month()], 0))
    monkeypatch.setattr(router_module, "fetch_latest_oil", lambda today: [])
    monkeypatch.setattr(router_module, "fetch_oil_history", lambda b, r, today: [])
    assert client.get("/api/v1/oil/BRENT").status_code == 404 and client.get("/api/v1/oil/WTI").status_code == 404
    assert client.get("/api/v1/oil/latest").json()["missing_benchmarks"] == ["BRENT", "WTI"]
    assert client.get("/api/v1/oil/history").json()["benchmark"] == "BRENT"
    assert client.get("/api/v1/oil/zm").status_code == 422  # "zm" is not a benchmark
    assert client.get("/api/v1/oil/zm/latest").json()["classification"] == "derived"


# ---------------- real PostgreSQL (opt-in) ----------------
@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    init = Path(__file__).parents[3] / "db/init"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS fx_rates"))
        c.execute(text("DROP TABLE IF EXISTS global_oil_prices"))
        for name in ("001_schema.sql", "002_fx_rates_provenance.sql", "009_global_oil_prices.sql"):
            c.execute(text((init / name).read_text()))
        c.execute(text("DELETE FROM fx_rates"))
    yield eng
    eng.dispose()


def put_fx(engine, rows, *, source="boz", pair=("USD", "ZMW"), buy_sell=True):
    with engine.begin() as c:
        for moment, mid in rows:
            c.execute(text("INSERT INTO fx_rates (base_currency, quote_currency, observed_at, buy, mid, sell, source) "
                           "VALUES (:b, :q, :t, :buy, :mid, :sell, :s)"),
                      {"b": pair[0], "q": pair[1], "t": utc(moment), "mid": Decimal(mid), "s": source,
                       "buy": Decimal(mid) - Decimal("0.01") if buy_sell else None, "sell": Decimal(mid) + Decimal("0.01") if buy_sell else None})


def put_brent(engine, rows):
    points = []
    for (year, mon), value in rows:
        first = date(year, mon, 1)
        last = date.fromordinal(date(year + (mon == 12), mon % 12 + 1, 1).toordinal() - 1)
        points.append(OilPoint(benchmark="BRENT", period_start=first, period_end=last, value=Decimal(value)))
    oil_store.save_oil_observations(points, publication_date=date(2026, 11, 3), source_url=RELEASE_URL,
                                    retrieved_at=datetime(2026, 11, 14, tzinfo=timezone.utc), engine=engine)


def test_pg_stored_brent_and_fx_derive_a_month_with_exact_decimals(engine):
    put_brent(engine, [((2026, 10), "125")])
    put_fx(engine, [("2026-10-01T07:30:00Z", "22.4000"), ("2026-10-01T10:30:00Z", "22.4000"), ("2026-10-01T13:30:00Z", "22.4000"),
                    ("2026-10-02T07:30:00Z", "22.6000")])
    (m,), omitted = oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine)
    assert omitted == 0 and (m.usd_zmw_observed_days_avg, m.zmw_per_bbl) == (Decimal("22.5"), Decimal("2812.5"))  # days equal-weighted: (22.4+22.6)/2
    assert (m.fx_day_count, m.fx_observation_count, m.brent_publication_date, m.brent_source_url) == (2, 4, date(2026, 11, 3), RELEASE_URL)
    assert isinstance(m.zmw_per_bbl, Decimal) and m.fx_coverage == "observed_days"  # stored FX history begins on day 1 of the month


def test_pg_brent_without_fx_and_fx_without_brent_derive_nothing(engine):
    put_brent(engine, [((2026, 4), "110"), ((2026, 5), "100")])
    put_fx(engine, [("2026-05-04T07:30:00Z", "19"), ("2026-06-02T07:30:00Z", "20")])
    months, omitted = oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine)
    assert [m.period_start for m in months] == [date(2026, 5, 1)] and omitted == 1  # April has no FX; June has no Brent


def test_pg_multiple_months_ascending_with_january_marked_partial(engine):
    put_brent(engine, [((2025, 12), "62.7"), ((2026, 1), "66.8"), ((2026, 2), "71.1"), ((2026, 3), "103.7")])
    put_fx(engine, [("2026-01-22T07:30:00Z", "19.6"), ("2026-01-23T07:30:00Z", "19.8"), ("2026-02-16T07:30:00Z", "18.3"),
                    ("2026-03-18T07:30:00Z", "19.5")])
    months, omitted = oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine)
    assert [(m.period_start, m.fx_coverage, m.fx_day_count) for m in months] == [
        (date(2026, 1, 1), "partial_source_window", 2), (date(2026, 2, 1), "observed_days", 1), (date(2026, 3, 1), "observed_days", 1)]
    assert omitted == 1  # December 2025 predates stored BoZ history: omitted, never filled or backfilled from another source
    assert months[0].usd_zmw_observed_days_avg == Decimal("19.7") and months[0].zmw_per_bbl == Decimal("66.8") * Decimal("19.7")


def test_pg_ranges_limit_brent_months_but_keep_the_full_fx_window(engine):
    put_brent(engine, [((2025, 11), "60"), ((2025, 12), "62"), ((2026, 1), "66.8"), ((2026, 10), "125")])
    put_fx(engine, [("2026-01-22T07:30:00Z", "19.6"), ("2026-10-01T07:30:00Z", "22")])
    one_year, omitted = oil_zm.fetch_oil_zm("1Y", today=TODAY, engine=engine)  # from 2025-11-01
    assert [m.period_start for m in one_year] == [date(2026, 1, 1), date(2026, 10, 1)] and omitted == 2
    assert one_year[0].fx_coverage == "partial_source_window"  # still known to be partial although it is the range's oldest month
    five, _ = oil_zm.fetch_oil_zm("5Y", today=TODAY, engine=engine)
    assert len(five) == 2 and oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine)[0] == five


def test_pg_only_the_boz_usdzmw_mid_population_is_used(engine):
    put_brent(engine, [((2026, 3), "100")])
    put_fx(engine, [("2026-03-02T07:30:00Z", "20")])
    put_fx(engine, [("2026-03-03T07:30:00Z", "99")], source="other")                  # not BoZ
    put_fx(engine, [("2026-03-04T07:30:00Z", "88")], pair=("EUR", "ZMW"))             # not USD/ZMW
    put_fx(engine, [("2026-03-05T07:30:00Z", "77")], buy_sell=False)                  # incomplete row, excluded like the history
    (m,), _ = oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine)
    assert (m.usd_zmw_observed_days_avg, m.fx_day_count, m.fx_observation_count) == (Decimal("20"), 1, 1)


def test_pg_derivation_is_read_only_and_persists_nothing(engine):
    put_brent(engine, [((2026, 3), "100")])
    put_fx(engine, [("2026-03-02T07:30:00Z", "20")])
    with engine.connect() as c:
        before = (c.execute(text("SELECT count(*) FROM fx_rates")).scalar(), c.execute(text("SELECT count(*) FROM global_oil_prices")).scalar())
        tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine); oil_zm.fetch_oil_zm("ALL", today=TODAY, engine=engine)
    with engine.connect() as c:
        assert before == (c.execute(text("SELECT count(*) FROM fx_rates")).scalar(), c.execute(text("SELECT count(*) FROM global_oil_prices")).scalar())
        assert {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))} == tables
    assert not [t for t in tables if "zm" in t and "oil" in t]  # no derived table exists
    assert not Path(__file__).parents[3].joinpath("db/init/010_oil_zm.sql").exists()
