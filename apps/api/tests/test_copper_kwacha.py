import importlib
import os
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.copper import COPPER_SOURCE_DEFINITION
from app.schemas.copper_kwacha import CORR_METHODOLOGY_ID, SENS_METHODOLOGY_ID, SMALL_SAMPLE_WARNING
from app.schemas.copper_zm import CopperZmMonth
from app.services import copper_kwacha
from app.services.copper_kwacha import (
    Change, analyse, analyse_sensitivity, build_changes, consecutive_months, pearson, rolling_correlation, sensitivity_beta,
)
from app.services.copper_store import save_copper_months
from app.services.imf_pcps import IMF_COPPER_URL, CopperMonth

router_module = importlib.import_module("app.api.router")
client = TestClient(app)
TODAY = date(2026, 11, 14)
RETRIEVED = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
D = Decimal


def mon(year, month, copper="100", fx="20", partial=False):
    first = date(year, month, 1)
    last = date.fromordinal(date(year + (month == 12), month % 12 + 1, 1).toordinal() - 1)
    return CopperZmMonth(
        period_start=first, period_end=last, zmw_per_mt="1", copper_usd_per_mt=copper, usd_zmw_observed_days_avg=fx,
        fx_day_count=3, fx_observation_count=9, fx_coverage="partial_source_window" if partial else "observed_days",
        fx_first_date=first, fx_last_date=last, fx_first_observation=RETRIEVED, fx_last_observation=RETRIEVED,
        copper_source_definition=COPPER_SOURCE_DEFINITION, copper_source_url=IMF_COPPER_URL, copper_retrieved_at=RETRIEVED, stale=False)


def series(start, levels):
    """Consecutive calendar months from (year, month): levels = [(copper, fx), ...]."""
    y, m = start
    out = []
    for c, f in levels:
        out.append(mon(y, m, c, f))
        y, m = y + (m == 12), m % 12 + 1
    return out


# ---------------- calendar adjacency ----------------
def test_consecutive_calendar_months_including_december_to_january():
    assert consecutive_months(date(2026, 1, 1), date(2026, 2, 1))
    assert consecutive_months(date(2025, 12, 1), date(2026, 1, 1))
    assert not consecutive_months(date(2026, 1, 1), date(2026, 3, 1))
    assert not consecutive_months(date(2026, 2, 1), date(2026, 1, 1)) and not consecutive_months(date(2026, 1, 1), date(2026, 1, 1))
    assert not consecutive_months(date(2025, 1, 1), date(2026, 1, 1))  # same month a year apart is not adjacent


# ---------------- change generation ----------------
def test_exact_decimal_fractional_changes_and_dec_to_jan():
    entries, n = build_changes([mon(2025, 12, "100", "20"), mon(2026, 1, "110", "21")])
    (change,) = entries
    assert n == 1 and change == Change(date(2026, 1, 1), D("0.1"), D("0.05")) and isinstance(change.copper, Decimal)
    (c,), _ = build_changes([mon(2026, 1, "3", "7"), mon(2026, 2, "4", "8")])
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = 40
        assert c.copper == D(4) / D(3) - 1 and c.fx == D(8) / D(7) - 1  # 40-digit Decimal from the strings, no float
    assert len(str(c.copper)) > 30


def test_input_order_does_not_matter():
    entries, _ = build_changes([mon(2026, 3, "120"), mon(2026, 1, "100"), mon(2026, 2, "110")])
    assert [e.end_month.month for e in entries] == [2, 3]


def test_calendar_gap_is_excluded_not_treated_as_a_one_month_change():
    entries, n = build_changes([mon(2026, 1), mon(2026, 3)])
    assert entries == ["gap"] and n == 1


@pytest.mark.parametrize("a,b", [(True, False), (False, True)])
def test_partial_source_window_on_either_endpoint_is_excluded(a, b):
    entries, _ = build_changes([mon(2026, 1, partial=a), mon(2026, 2, partial=b)])
    assert entries == ["partial"]


@pytest.mark.parametrize("kw", [
    {"copper": "0"}, {"copper": "-5"}, {"fx": "0"}, {"fx": "-1"}])
def test_nonpositive_inputs_are_excluded_at_either_endpoint(kw):
    for first, second in ((mon(2026, 1, **kw), mon(2026, 2)), (mon(2026, 1), mon(2026, 2, **kw))):
        entries, _ = build_changes([first, second])
        assert entries == ["nonpositive"]


def test_exclusion_precedence_gap_then_partial_then_nonpositive():
    assert build_changes([mon(2026, 1, partial=True, copper="0"), mon(2026, 3)])[0] == ["gap"]
    assert build_changes([mon(2026, 1, partial=True, copper="0"), mon(2026, 2)])[0] == ["partial"]
    assert build_changes([mon(2026, 1, copper="0"), mon(2026, 2)])[0] == ["nonpositive"]


def test_every_candidate_is_accounted_for():
    months = [mon(2026, 1, partial=True), mon(2026, 2, "100"), mon(2026, 3, "101", "20.1"), mon(2026, 5, "103", "20.2"),
              mon(2026, 6, "0", "20.3"), mon(2026, 7, "105", "20.4"), mon(2026, 8, "106", "20.6")]
    r = analyse(months, "ALL", "6M")
    assert r.matched_months == 7 and r.candidate_changes == 6
    assert (r.changes_used, r.changes_excluded_partial, r.changes_excluded_gap, r.changes_excluded_nonpositive) == (2, 1, 1, 2)
    assert r.candidate_changes == r.changes_used + r.changes_excluded_gap + r.changes_excluded_partial + r.changes_excluded_nonpositive


def test_zero_one_and_no_matched_months_have_no_candidates():
    for months in ([], [mon(2026, 1)]):
        r = analyse(months, "ALL", "6M")
        assert (r.candidate_changes, r.changes_used, r.full_sample.status, r.rolling) == (0, 0, "insufficient_sample", [])


# ---------------- Pearson ----------------
def test_perfect_positive_and_inverse_correlation():
    xs = [D(s) for s in ("0.01", "0.02", "0.03", "0.04", "0.05", "0.06")]
    assert pearson(xs, [x * 2 for x in xs]) == D(1)
    assert pearson(xs, [-x for x in xs]) == D(-1)
    assert pearson(xs, [D("0.5") - x for x in xs]) == D(-1)


def test_nontrivial_known_coefficient():
    # means 3 and 3: Sxx = 10, Syy = 10, Sxy = 8  ->  r = 0.8 exactly
    assert pearson([D(1), D(2), D(3), D(4), D(5)], [D(2), D(1), D(4), D(3), D(5)]) == D("0.8")
    # scale/shift invariance: percentages instead of fractions, and shifted levels, give the same r
    xs, ys = [D("0.01"), D("-0.02"), D("0.015"), D("0.03"), D("-0.005"), D("0.0")], [D("0.002"), D("0.001"), D("-0.004"), D("0.003"), D("0.0"), D("-0.001")]
    base = pearson(xs, ys)
    assert abs(pearson([x * 100 for x in xs], ys) - base) < D("1e-30") and abs(pearson(xs, [y + 5 for y in ys]) - base) < D("1e-30")
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    expected = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)).sqrt()
    assert abs(base - expected) < D("1e-25") and -1 <= base <= 1


def test_zero_variance_and_too_few_pairs_are_undefined_not_a_division_error():
    assert pearson([D(1)] * 6, [D(1), D(2), D(3), D(4), D(5), D(6)]) is None
    assert pearson([D(1), D(2), D(3)], [D(2)] * 3) is None
    assert pearson([D(1)], [D(1)]) is None and pearson([], []) is None


def levels_with_known_changes(n=8):
    """n changes; FX changes track Copper changes exactly (r = +1)."""
    copper, fx, out = D(100), D(20), [("100", "20")]
    for i in range(n):
        step = D("0.01") * (i + 1)
        copper, fx = copper * (1 + step), fx * (1 + step * 2)
        out.append((str(copper), str(fx)))
    return out


def test_full_sample_threshold_and_small_sample_flag():
    below = analyse(series((2026, 2), levels_with_known_changes(5)), "ALL", "6M")  # 5 changes
    assert below.full_sample.status == "insufficient_sample" and below.full_sample.correlation is None and below.full_sample.sample_size == 5
    assert "6 eligible" in below.full_sample.reason
    at = analyse(series((2026, 2), levels_with_known_changes(6)), "ALL", "6M")
    assert at.full_sample.status == "available" and at.full_sample.sample_size == 6 and at.full_sample.small_sample is True
    assert abs(at.full_sample.correlation - 1) < D("1e-25")
    big = analyse(series((2025, 1), levels_with_known_changes(12)), "ALL", "12M")
    assert big.full_sample.small_sample is False
    assert "fewer than 12" in SMALL_SAMPLE_WARNING


def test_zero_variance_full_sample_is_reported_with_a_reason():
    flat = [("100", "20")] + [(str(D(100) * D("1.01") ** i), str(D(20) * D("1.01") ** i)) for i in range(1, 8)]
    constant_fx = [(c, "20") for c, _ in flat]
    r = analyse(series((2026, 2), constant_fx), "ALL", "6M")
    assert r.full_sample.status == "zero_variance" and r.full_sample.correlation is None and "constant" in r.full_sample.reason


def test_only_the_two_underlying_inputs_feed_the_statistic_not_the_derived_zmw_value():
    a = series((2026, 2), levels_with_known_changes(7))
    b = [m.model_copy(update={"zmw_per_mt": D("12345.678")}) for m in a]  # change the derived product only
    assert analyse(a, "ALL", "6M").model_dump() == analyse(b, "ALL", "6M").model_dump()
    assert analyse(a, "ALL", "6M").variables == ["copper_usd_per_mt", "usd_zmw_observed_days_avg"]
    import inspect
    assert "zmw_per_mt" not in inspect.getsource(copper_kwacha).split('"""', 2)[2].replace("`zmw_per_mt`", "")


# ---------------- rolling continuity ----------------
def eligible(n):
    return [Change(date(2026 + i // 12, i % 12 + 1, 1), D(i + 1) / 100, D((i * 7) % 5 + 1) / 100) for i in range(n)]


def test_six_consecutive_changes_make_one_rolling_point_and_seven_make_two():
    one = rolling_correlation(eligible(6), 6)
    assert len(one) == 1 and one[0].window_end_month == date(2026, 6, 1) and one[0].sample_size == 6
    two = rolling_correlation(eligible(7), 6)
    assert [p.window_end_month.month for p in two] == [6, 7] and all(p.sample_size == 6 for p in two)
    assert rolling_correlation(eligible(5), 6) == []


@pytest.mark.parametrize("reason", ["gap", "partial", "nonpositive"])
def test_a_break_resets_the_rolling_window(reason):
    entries = eligible(4) + [reason] + eligible(4)
    assert rolling_correlation(entries, 6) == []  # 8 eligible changes, but never 6 in a row
    entries = eligible(6) + [reason] + eligible(5)
    assert len(rolling_correlation(entries, 6)) == 1  # only the window before the break
    entries = eligible(3) + [reason] + eligible(6)
    assert len(rolling_correlation(entries, 6)) == 1  # only the window after the break
    assert len(rolling_correlation(eligible(6) + [reason] + eligible(6), 6)) == 2


def test_rolling_window_with_zero_variance_stays_defined_with_a_null_coefficient():
    # 12 consecutive eligible changes: Copper varies, FX is constant (every FX change is exactly 0)
    levels = [(str(D(100) * D("1.01") ** i * (1 + D(i % 3) / 100)), "20") for i in range(13)]
    r = analyse(series((2026, 2), levels), "ALL", "6M")
    assert r.changes_used == 12 and r.full_sample.status == "zero_variance" and r.rolling_reason is None
    assert len(r.rolling) == 7 and all(p.correlation is None and p.sample_size == 6 for p in r.rolling)
    assert [p.window_end_month for p in r.rolling][0] == date(2026, 8, 1) and r.rolling[-1].window_end_month == date(2027, 2, 1)
    dumped = r.model_dump(mode="json")["rolling"][0]
    assert dumped == {"window_end_month": "2026-08-01", "correlation": None, "sample_size": 6}  # present, not dropped


def test_mixed_defined_and_undefined_rolling_windows_are_all_returned():
    steps = [D(0)] * 6 + [D("0.03"), D("-0.01"), D("0.02"), D("0.015")]  # FX flat for 6 changes, then moving
    copper, fx, levels = D(100), D(20), [("100", "20")]
    for i, st in enumerate(steps):
        copper, fx = copper * (1 + D(i + 1) / 100 * (-1) ** i), fx * (1 + st)
        levels.append((str(copper), str(fx)))
    r = analyse(series((2026, 2), levels), "ALL", "6M")
    assert len(r.rolling) == 5 and r.rolling[0].correlation is None and r.rolling[-1].correlation is not None
    assert [p.correlation is None for p in r.rolling][0] and any(p.correlation is not None for p in r.rolling)


THIRDS = ["729", "243", "81", "27", "9", "3", "1"]  # six identical changes of exactly -2/3, a repeating Decimal


def repeating_constant_levels(varying):
    """varying: 'none' = both change series are the repeating -2/3; 'fx' = Copper constant, FX varies; 'copper' = FX constant, Copper varies."""
    moving_copper = ["100", "110", "99", "120", "118", "130", "126"]
    moving_fx = ["20", "21", "19", "23", "22", "26", "24"]
    copper = moving_copper if varying == "copper" else THIRDS
    fx = moving_fx if varying == "fx" else THIRDS
    return list(zip(copper, fx))


def test_repeating_decimal_constant_changes_are_exactly_equal_and_pearson_is_undefined():
    entries, _ = build_changes(series((2026, 2), repeating_constant_levels("none")))
    assert len(entries) == 6 and all(isinstance(e, Change) for e in entries)
    assert len({e.copper for e in entries}) == 1 and len({e.fx for e in entries}) == 1  # exactly equal stored Decimals
    with localcontext() as ctx:
        ctx.prec = 40
        mean = sum((e.copper for e in entries), D(0)) / 6
    assert mean != entries[0].copper  # the rounding hazard this regression guards against: the mean is not the value
    assert pearson([e.copper for e in entries], [e.fx for e in entries]) is None


@pytest.mark.parametrize("varying", ["none", "fx", "copper"])
def test_full_sample_with_a_repeating_constant_series_is_zero_variance_never_one(varying):
    r = analyse(series((2026, 2), repeating_constant_levels(varying)), "ALL", "6M")
    assert r.changes_used == 6 and r.full_sample.sample_size == 6
    assert r.full_sample.status == "zero_variance" and r.full_sample.correlation is None
    assert r.full_sample.reason and "constant" in r.full_sample.reason


def test_rolling_window_over_repeating_constant_changes_exists_with_a_null_coefficient():
    r = analyse(series((2026, 2), repeating_constant_levels("fx")), "ALL", "6M")
    (point,) = r.rolling
    assert point.window_end_month == date(2026, 8, 1) and point.sample_size == 6 and point.correlation is None and r.rolling_reason is None


def test_a_later_varying_window_still_gets_a_numeric_coefficient_after_a_repeating_constant_one():
    # Copper changes are the repeating -2/3 for six months, then move; FX always moves: window 1 is undefined, a later one is numeric
    copper = THIRDS + ["2", "5", "3"]
    fx = ["20", "21", "19", "23", "22", "26", "24", "27", "25", "29"]
    r = analyse(series((2026, 2), list(zip(copper, fx))), "ALL", "6M")
    assert r.rolling[0].correlation is None and r.rolling[-1].correlation is not None and -1 <= r.rolling[-1].correlation <= 1


def test_twelve_month_window_needs_twelve_changes_and_current_style_data_gives_none():
    assert rolling_correlation(eligible(11), 12) == []
    assert len(rolling_correlation(eligible(13), 12)) == 2
    r = analyse(series((2026, 2), levels_with_known_changes(8)), "ALL", "12M")
    assert r.rolling == [] and r.rolling_reason and "12 uninterrupted" in r.rolling_reason and r.window_changes == 12


def test_end_to_end_current_shape_partial_january_gives_three_six_change_windows():
    months = [mon(2026, 1, "100", "20", partial=True)] + series((2026, 2), levels_with_known_changes(8))[:9]
    r = analyse(months, "ALL", "6M")
    assert (r.matched_months, r.candidate_changes, r.changes_excluded_partial, r.changes_used) == (10, 9, 1, 8)
    assert r.changes_excluded_partial == 1 and r.changes_used == r.candidate_changes - 1
    assert len(r.rolling) == r.changes_used - 6 + 1  # derived from the data
    assert r.full_sample.first_change_month == date(2026, 3, 1)  # Jan -> Feb was excluded, so the first change ends in March


# ---------------- API ----------------
def test_endpoint_defaults_params_and_methodology(monkeypatch):
    seen = []
    def fake(range_, window, today):
        seen.append((range_, window))
        return analyse(series((2026, 2), levels_with_known_changes(8)), range_, window)
    monkeypatch.setattr(router_module, "fetch_copper_kwacha_correlation", fake)
    body = client.get("/api/v1/copper/kwacha/correlation").json()
    assert seen == [("ALL", "6M")] and body["methodology_id"] == CORR_METHODOLOGY_ID == "copper_kwacha_corr_v1"
    assert body["full_sample"]["status"] == "available" and isinstance(body["full_sample"]["correlation"], str)
    assert "causation" in body["notice"] and body["variables"] == ["copper_usd_per_mt", "usd_zmw_observed_days_avg"]
    assert "p_value" not in body and "r_squared" not in body and "strength" not in str(body).lower()
    assert client.get("/api/v1/copper/kwacha/correlation?range=3Y&window=12M").status_code == 200 and seen[-1] == ("3Y", "12M")


@pytest.mark.parametrize("query", ["range=1M", "range=", "window=3M", "window=6", "window=1Y", "range=1y"])
def test_invalid_range_or_window_is_422(query):
    assert client.get(f"/api/v1/copper/kwacha/correlation?{query}").status_code == 422


def test_insufficient_sample_is_a_200_not_a_404(monkeypatch):
    monkeypatch.setattr(router_module, "fetch_copper_kwacha_correlation", lambda r, w, today: analyse([], r, w))
    response = client.get("/api/v1/copper/kwacha/correlation")
    assert response.status_code == 200 and response.json()["full_sample"]["correlation"] is None and response.json()["rolling"] == []


def test_database_failure_is_a_safe_503(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db 0.1234")
    monkeypatch.setattr(router_module, "fetch_copper_kwacha_correlation", boom)
    response = client.get("/api/v1/copper/kwacha/correlation")
    assert response.status_code == 503 and "SECRET" not in response.text and "0.1234" not in response.text


def test_endpoint_never_reaches_an_upstream_source(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("must read PostgreSQL only")
    import httpx
    from app.services import imf_pcps
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(imf_pcps, "fetch_copper_payload", no_network)
    monkeypatch.setattr(router_module, "get_boz_fx_quote", no_network)
    monkeypatch.setattr(copper_kwacha, "fetch_copper_zm", lambda r, today, engine=None: ([], 0))
    assert client.get("/api/v1/copper/kwacha/correlation").status_code == 200


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
        c.execute(text("DROP TABLE IF EXISTS global_copper_prices"))
        for name in ("001_schema.sql", "002_fx_rates_provenance.sql", "011_global_copper_prices.sql"):
            c.execute(text((init / name).read_text()))
        c.execute(text("DELETE FROM fx_rates"))
    yield eng
    eng.dispose()


def put(engine, rows):
    """rows: (year, month, copper, fx) -> a stored Copper month and one BoZ USD/ZMW observation on the 5th."""
    save_copper_months([CopperMonth(date(y, m, 1), D(c)) for y, m, c, _ in rows], source_url=IMF_COPPER_URL, retrieved_at=RETRIEVED, engine=engine)
    with engine.begin() as c:
        for y, m, _, f in rows:
            c.execute(text("INSERT INTO fx_rates (base_currency, quote_currency, observed_at, buy, mid, sell, source) VALUES "
                           "('USD','ZMW',:t,:b,:m,:s,'boz')"), {"t": datetime(y, m, 5, 7, 30, tzinfo=timezone.utc), "m": D(f), "b": D(f) - 1, "s": D(f) + 1})


def test_pg_real_storage_end_to_end_with_range_local_counts(engine):
    rows = []
    for i in range(40):  # Aug 2023 .. Nov 2026 would pass today; keep to Jan 2023 .. Apr 2026
        y, m = 2023 + i // 12, i % 12 + 1
        rows.append((y, m, str(D(8000) + D(i * 37 % 91)), str(D("17") + D(i * 13 % 17) / 10)))
    put(engine, rows)
    allr = copper_kwacha.fetch_copper_kwacha_correlation("ALL", "6M", today=TODAY, engine=engine)
    assert allr.matched_months == 40 and allr.candidate_changes == 39 and allr.changes_used == 38  # Jan 2023 is partial (stored FX begins on the 5th), so 38 of 39
    assert allr.candidate_changes == allr.changes_used + allr.changes_excluded_partial + allr.changes_excluded_gap + allr.changes_excluded_nonpositive
    one = copper_kwacha.fetch_copper_kwacha_correlation("1Y", "6M", today=TODAY, engine=engine)  # Copper months from 2025-11-01
    assert one.matched_months == 6 and one.candidate_changes == 5 and one.changes_used == 5  # range-local: older months not counted
    assert one.full_sample.status == "insufficient_sample" and one.rolling == []
    assert allr.full_sample.status == "available" and -1 <= allr.full_sample.correlation <= 1 and len(allr.rolling) == 38 - 6 + 1


def test_pg_nonpositive_stored_values_are_excluded_analytically_but_remain_stored(engine):
    put(engine, [(2026, 1, "100", "20"), (2026, 2, "0", "20"), (2026, 3, "-4", "20"), (2026, 4, "110", "20")])
    r = copper_kwacha.fetch_copper_kwacha_correlation("ALL", "6M", today=TODAY, engine=engine)
    assert r.changes_excluded_partial == 1 and r.changes_excluded_nonpositive == 2 and r.changes_used == 0  # Jan is partial (stored FX begins on the 5th)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM global_copper_prices")).scalar() == 4  # source rows untouched


def test_pg_nothing_is_persisted(engine):
    put(engine, [(2026, 1, "100", "20"), (2026, 2, "101", "20.1")])
    def snap():
        with engine.connect() as c:
            names = sorted(r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
            return names, [c.execute(text(f'SELECT count(*) FROM "{n}"')).scalar() for n in names]
    before = snap()
    copper_kwacha.fetch_copper_kwacha_correlation("ALL", "6M", today=TODAY, engine=engine)
    assert snap() == before and not list(Path(__file__).parents[3].joinpath("db/init").glob("012*"))


# ======================= historical sensitivity (beta) =======================
CX = [D(v) for v in ("0.01", "0.02", "0.03", "0.04", "0.05", "0.06")]


def test_beta_perfect_proportional_negative_and_offset_are_exact():
    assert sensitivity_beta(CX, [x * 2 for x in CX]) == D(2)
    assert sensitivity_beta(CX, [x * D("-0.5") for x in CX]) == D("-0.5")
    assert sensitivity_beta(CX, [x * 2 + D("0.007") for x in CX]) == D(2)  # a constant offset does not matter: mean-centred


def test_beta_is_not_symmetric_and_matches_an_independent_calculation():
    xs, ys = [D("0.01"), D("-0.02"), D("0.015"), D("0.03"), D("-0.005"), D("0.0")], [D("0.002"), D("0.001"), D("-0.004"), D("0.003"), D("0.0"), D("-0.001")]
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    expected = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    assert abs(sensitivity_beta(xs, ys) - expected) < D("1e-25")
    assert sensitivity_beta(xs, ys) != sensitivity_beta(ys, xs)  # dependent = FX, explanatory = Copper
    # scaling Copper changes by 100 (percent instead of fraction) divides beta by 100; scaling FX multiplies it
    assert abs(sensitivity_beta([x * 100 for x in xs], ys) - sensitivity_beta(xs, ys) / 100) < D("1e-25")


def test_constant_copper_is_undefined_but_constant_fx_with_varying_copper_is_exactly_zero():
    assert sensitivity_beta([D("0.01")] * 6, CX) is None  # undefined, never 0 and never the FX variance as denominator
    assert sensitivity_beta(CX, [D("0.02")] * 6) == D(0)  # valid: covariance 0, Copper variance nonzero
    assert sensitivity_beta([D(1)], [D(1)]) is None and sensitivity_beta([], []) is None


def test_repeating_decimal_constant_copper_changes_are_undefined_for_beta_too():
    entries, _ = build_changes(series((2026, 2), [(c, f) for c, f in zip(THIRDS, ["20", "21", "19", "23", "22", "26", "24"])]))
    assert len({e.copper for e in entries}) == 1
    assert sensitivity_beta([e.copper for e in entries], [e.fx for e in entries]) is None
    assert sensitivity_beta(CX, [D(1) / D(3)] * 6) == D(0)  # repeating-decimal constant FX: exactly zero, no spurious residue
    r = analyse_sensitivity(series((2026, 2), [(c, f) for c, f in zip(THIRDS, ["20", "21", "19", "23", "22", "26", "24"])]), "ALL")
    assert r.status == "zero_copper_variance" and r.beta is None and r.sample_size == 6 and "constant" in r.reason


def test_near_constant_copper_is_not_suppressed_by_exact_detection():
    near = [D("-0.6666666666666666666666666666666666666667")] * 5 + [D("-0.6666666666666666666666666666666666666666")]
    b = sensitivity_beta(near, [D(i) / 100 for i in range(6)])
    assert b is not None and b != 0 and b.is_finite()


def test_sample_size_boundary_for_beta():
    five = analyse_sensitivity(series((2026, 2), levels_with_known_changes(5)), "ALL")
    six = analyse_sensitivity(series((2026, 2), levels_with_known_changes(6)), "ALL")
    assert (five.status, five.beta, five.sample_size) == ("insufficient_sample", None, 5) and "6 eligible" in five.reason
    assert six.status == "available" and six.sample_size == 6 and six.beta is not None and six.small_sample is True
    assert analyse_sensitivity(series((2025, 1), levels_with_known_changes(12)), "ALL").small_sample is False
    assert analyse_sensitivity([], "ALL").status == "insufficient_sample"


def test_known_proportional_changes_give_beta_two_through_the_full_pipeline():
    # FX changes are exactly twice the Copper changes in every month
    copper, fx, levels = D(100), D(20), [("100", "20")]
    for st in ("0.01", "0.02", "-0.015", "0.03", "0.005", "-0.01", "0.02"):
        copper, fx = copper * (1 + D(st)), fx * (1 + D(st) * 2)
        levels.append((str(copper), str(fx)))
    r = analyse_sensitivity(series((2026, 2), levels), "ALL")
    assert r.status == "available" and abs(r.beta - 2) < D("1e-30")  # Copper/FX levels are Decimal strings, no float anywhere


def mixed_fixture():
    return [mon(2026, 1, "100", "20", partial=True), mon(2026, 2, "101", "20.1"), mon(2026, 3, "103", "20.3"), mon(2026, 5, "104", "20.2"),
            mon(2026, 6, "0", "20.4"), mon(2026, 7, "106", "20.5"), mon(2026, 8, "108", "20.4"), mon(2026, 9, "107", "20.6"), mon(2026, 10, "110", "20.5")]


def test_sample_metadata_matches_the_correlation_exactly_on_gap_partial_and_nonpositive_inputs():
    months = mixed_fixture()
    c, b = analyse(months, "ALL", "6M"), analyse_sensitivity(months, "ALL")
    assert (c.matched_months, c.candidate_changes, c.changes_used, c.changes_excluded_gap, c.changes_excluded_partial, c.changes_excluded_nonpositive) == \
           (b.matched_months, b.candidate_changes, b.changes_used, b.changes_excluded_gap, b.changes_excluded_partial, b.changes_excluded_nonpositive)
    assert (c.full_sample.first_change_month, c.full_sample.last_change_month, c.full_sample.sample_size) == (b.first_change_month, b.last_change_month, b.sample_size)
    assert b.changes_excluded_gap == 1 and b.changes_excluded_partial == 1 and b.changes_excluded_nonpositive == 2  # the fixture really exercises all three
    assert b.candidate_changes == b.changes_used + b.changes_excluded_gap + b.changes_excluded_partial + b.changes_excluded_nonpositive


def test_beta_uses_only_the_two_inputs_not_the_derived_zmw_value():
    a = series((2026, 2), levels_with_known_changes(7))
    b = [m.model_copy(update={"zmw_per_mt": D("999.5")}) for m in a]
    assert analyse_sensitivity(a, "ALL").model_dump() == analyse_sensitivity(b, "ALL").model_dump()
    r = analyse_sensitivity(a, "ALL")
    assert (r.dependent, r.explanatory, r.methodology_id) == ("usd_zmw_observed_days_avg", "copper_usd_per_mt", "copper_kwacha_beta_v1")
    assert SENS_METHODOLOGY_ID == "copper_kwacha_beta_v1"


def test_sensitivity_endpoint_defaults_422_503_and_no_significance_fields(monkeypatch):
    seen = []
    monkeypatch.setattr(router_module, "fetch_copper_kwacha_sensitivity", lambda r, today: seen.append(r) or analyse_sensitivity(series((2026, 2), levels_with_known_changes(8)), r))
    body = client.get("/api/v1/copper/kwacha/sensitivity").json()
    assert seen == ["ALL"] and body["status"] == "available" and isinstance(body["beta"], str) and body["small_sample"] is True
    assert "kwacha per US dollar" in body["sign_note"] and "causal" in body["notice"] and "Fractional USD/ZMW" in body["units"]
    assert not {"r_squared", "p_value", "t_stat", "intercept", "standard_error", "confidence_interval", "rolling"} & set(body)
    assert client.get("/api/v1/copper/kwacha/sensitivity?range=3Y").status_code == 200 and seen[-1] == "3Y"
    for bad in ("range=1M", "range=", "range=1y"):
        assert client.get(f"/api/v1/copper/kwacha/sensitivity?{bad}").status_code == 422
    monkeypatch.setattr(router_module, "fetch_copper_kwacha_sensitivity", lambda r, today: analyse_sensitivity([], r))
    empty = client.get("/api/v1/copper/kwacha/sensitivity")
    assert empty.status_code == 200 and empty.json()["beta"] is None and empty.json()["status"] == "insufficient_sample"
    def boom(*a, **k):
        raise RuntimeError("postgresql://u:SECRET@h/db")
    monkeypatch.setattr(router_module, "fetch_copper_kwacha_sensitivity", boom)
    response = client.get("/api/v1/copper/kwacha/sensitivity")
    assert response.status_code == 503 and "SECRET" not in response.text


def test_pg_sensitivity_matches_correlation_sample_and_persists_nothing(engine):
    rows = []
    for i in range(14):
        y, m = 2025 + i // 12, i % 12 + 1
        rows.append((y, m, str(D(8000) + D(i * 37 % 91)), str(D("17") + D(i * 13 % 17) / 10)))
    put(engine, rows)
    def snap():
        with engine.connect() as c:
            names = sorted(r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
            return names, [c.execute(text(f'SELECT count(*) FROM "{n}"')).scalar() for n in names]
    before = snap()
    for rng in ("ALL", "1Y"):
        s_ = copper_kwacha.fetch_copper_kwacha_sensitivity(rng, today=TODAY, engine=engine)
        c_ = copper_kwacha.fetch_copper_kwacha_correlation(rng, "6M", today=TODAY, engine=engine)
        assert (s_.matched_months, s_.candidate_changes, s_.changes_used, s_.first_change_month, s_.last_change_month) == \
               (c_.matched_months, c_.candidate_changes, c_.changes_used, c_.full_sample.first_change_month, c_.full_sample.last_change_month)
    assert copper_kwacha.fetch_copper_kwacha_sensitivity("ALL", today=TODAY, engine=engine).status == "available"
    assert snap() == before
