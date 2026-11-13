import importlib
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.schemas.agriculture import AgricultureHistoryPoint, AgriculturePrice
from app.services import agriculture_store, moa_agriculture
from app.services.moa_agriculture import AgricultureSourceError, UnsupportedLayoutError
from app.services.provider_policy import MOA_AGRICULTURE_POLICY, PROVIDER_POLICIES

router_module = importlib.import_module("app.api.router")
NOW = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
URL = "https://www.agriculture.gov.zm/wp-content/uploads/2026/05/April-2025-to-January-2026-final.docx.pdf"

# Mirrors the verified Layout B tables but is a local text fixture: no network or PDF is needed.
LAYOUT_B = """
MINISTRY OF AGRICULTURE
DEPARTMENT OF AGRIBUSINESS AND MARKETING
April 2025-January 2026 Market Information Bulletin
Bulletin No: 020430– 30th April 2025 – 31st January 2026

Table 1: National Average Commodity Prices
COMMODITY DECEMBER JANUARY % CHANGE
ZMW/KG ZMW/KG
Maize 5.99 7.00 16.85
Sorghum 15.16 15.79 4.16
Rice 32.19 33.11 2.86
Millet 15.75 17.52 11.24
Sunflower 7.55 8.07 6.89
Groundnuts 33.33 34.86 4.59
Soya Beans 10.55 10.02 -5.02
Mixed Beans 33.05 35.98 8.87
Graph 1

Table 3: Percentage Change in Commodity Prices
All prices are in ZMW. Percentage change columns show the percentage change associated with the corresponding month.
Commodity April May % change June June % change July July % change August August % change September September % change October October % change November November % change December December % change January January % change Overall percent change
Maize (Kg) 8.81 6.85 -999.99 5.76 -999.99 5.41 -999.99 5.38 -999.99 5.38 -999.99 5.72 -999.99 5.93 -999.99 5.99 -999.99 7.00 -999.99 -20.55
Sorghum(Kg) 14.2 13.36 -5.92 16.02 19.91 15.04 -6.12 16.8 11.70 14.88 -11.43 14.44 -2.96 12.73 -11.84 15.16 19.09 15.79 4.16 11.20
Soya Beans (Kg) 19.58 12.49 -999.99 7.89 -999.99 10.43 -999.99 9.99 -999.99 9.87 -999.99 10.06 -999.99 9.87 -999.99 10.55 -999.99 10.02 -999.99 -48.83
Mixed Beans (Kg) 36.6 36.6 0.00 36.8 0.55 37.01 0.57 35.68 -3.59 35.1 -1.63 36.1 2.85 34.1 -5.54 33.05 -3.08 35.98 8.87 -1.69

Maize Seed ZMW/10KG 684.68 723.57
Soya Bean Seed ZMW/25KG 987.85 1017.86
"""


def parsed():
    return moa_agriculture.parse_layout_b_text(
        LAYOUT_B, publication_date=date(2026, 5, 21), source_url=URL, retrieved_at=NOW,
    )


def by(rows, commodity):
    return [r for r in rows if r.commodity == commodity]


def test_layout_b_extracts_only_grain_monthly_prices_and_ignores_printed_changes():
    rows = parsed()
    maize, soya = by(rows, "MAIZE"), by(rows, "SOYA_BEANS")
    assert len(rows) == 20 and len(maize) == len(soya) == 10
    assert maize[0].period_start == date(2025, 4, 1)
    assert maize[0].period_end == date(2025, 4, 30)
    assert maize[0].source_price == Decimal("8.81")
    assert maize[-1].period_start == date(2026, 1, 1)
    assert maize[-1].period_end == date(2026, 1, 31)
    assert maize[-1].source_price == Decimal("7.00")
    assert [r.source_price for r in soya[:3]] == [Decimal("19.58"), Decimal("12.49"), Decimal("7.89")]
    assert all(r.source_unit == "kg" and r.source_currency == "ZMW" for r in rows)
    assert all(r.market_type == "national_average" and r.price_basis == "indicative_average" for r in rows)
    assert not any("SEED" in r.commodity for r in rows)



def test_ministry_provider_policy_is_review_required_and_registered():
    policy = MOA_AGRICULTURE_POLICY
    assert policy.provider_id == "moa"
    assert policy.dataset_id in PROVIDER_POLICIES
    assert policy.authentication == "none"
    assert policy.raw_response_storage == "disabled"
    assert policy.redistribution == policy.commercial_use == "review_required"
    assert set(policy.required_timestamps) == {"period_start", "period_end", "publication_date", "retrieved_at"}


def test_table1_must_reconcile_december_and_january():
    bad = LAYOUT_B.replace("Maize 5.99 7.00 16.85", "Maize 5.99 7.01 16.85")
    with pytest.raises(AgricultureSourceError, match="Table 1 and Table 3 maize prices disagree"):
        moa_agriculture.parse_layout_b_text(
            bad, publication_date=date(2026, 5, 21), source_url=URL, retrieved_at=NOW,
        )


def test_known_older_zmk_layout_is_rejected_not_converted():
    old = "NATIONAL COMMODITY AVERAGE PRICES ZMK/MT ZMK/Kg Maize 8371.98 8.37"
    with pytest.raises(UnsupportedLayoutError):
        moa_agriculture.parse_layout_b_text(
            old, publication_date=date(2024, 5, 16), source_url=URL, retrieved_at=NOW,
        )


def test_missing_currency_statement_and_wrong_month_count_fail_closed():
    with pytest.raises(AgricultureSourceError, match="currency statement"):
        moa_agriculture.parse_layout_b_text(
            LAYOUT_B.replace("All prices are in ZMW.", ""),
            publication_date=date(2026, 5, 21), source_url=URL, retrieved_at=NOW,
        )
    with pytest.raises(AgricultureSourceError, match="ten calendar months"):
        moa_agriculture.parse_layout_b_text(
            LAYOUT_B.replace("31st January 2026", "31st December 2025"),
            publication_date=date(2026, 5, 21), source_url=URL, retrieved_at=NOW,
        )


def price(commodity="MAIZE", month=date(2026, 1, 1), value=Decimal("7.00")):
    end = date(month.year, month.month, 31 if month.month == 1 else 30)
    return AgriculturePrice(
        commodity=commodity,
        period_start=month,
        period_end=end,
        publication_date=date(2026, 5, 21),
        publication_date_source="media_upload",
        source_price=value,
        normalized_zmw_per_kg=value,
        source="Ministry of Agriculture, Department of Agribusiness and Marketing",
        source_url=URL,
        source_period_label="JANUARY",
        retrieved_at=NOW,
    )


def test_agriculture_api_latest_history_validation_and_503(monkeypatch):
    maize = price()
    soya = price("SOYA_BEANS", value=Decimal("10.02"))
    monkeypatch.setattr(router_module, "fetch_latest_agriculture", lambda: [maize, soya])
    monkeypatch.setattr(
        router_module, "fetch_agriculture_history",
        lambda commodity, range, today: [
            AgricultureHistoryPoint(
                commodity=commodity, period_start=date(2025, 12, 1), period_end=date(2025, 12, 31),
                source_price=Decimal("5.99"), normalized_zmw_per_kg=Decimal("5.99"),
                source="Ministry of Agriculture, Department of Agribusiness and Marketing",
                source_url=URL,
            ),
            AgricultureHistoryPoint(
                commodity=commodity, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                source_price=Decimal("7.00"), normalized_zmw_per_kg=Decimal("7.00"),
                source="Ministry of Agriculture, Department of Agribusiness and Marketing",
                source_url=URL,
            ),
        ],
    )
    client = TestClient(app)
    latest = client.get("/api/v1/agriculture/latest")
    assert latest.status_code == 200
    assert {p["commodity"] for p in latest.json()["prices"]} == {"MAIZE", "SOYA_BEANS"}
    assert client.get("/api/v1/agriculture/MAIZE").status_code == 200
    hist = client.get("/api/v1/agriculture/history?commodity=MAIZE&range=1Y")
    assert hist.status_code == 200 and hist.json()["count"] == 2
    assert client.get("/api/v1/agriculture/history?commodity=WHEAT").status_code == 422
    assert client.get("/api/v1/agriculture/history?range=5Y").status_code == 422

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(router_module, "fetch_latest_agriculture", boom)
    assert client.get("/api/v1/agriculture/latest").status_code == 503


@pytest.fixture
def pg_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    assert url.rsplit("/", 1)[-1].endswith("_test")
    engine = create_engine(url)
    migration = Path(__file__).parents[3] / "db" / "init" / "008_zambia_agricultural_prices.sql"
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS zambia_agricultural_prices"))
        for statement in migration.read_text().split(";"):
            if statement.strip():
                conn.exec_driver_sql(statement)
    yield engine
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS zambia_agricultural_prices"))
    engine.dispose()


def test_store_idempotency_conflict_and_history(pg_engine):
    rows = parsed()
    assert agriculture_store.save_agriculture_prices(rows, engine=pg_engine) == (20, 0)
    assert agriculture_store.save_agriculture_prices(rows, engine=pg_engine) == (0, 20)

    conflict = replace(rows[0], source_price=Decimal("99.99"), normalized_zmw_per_kg=Decimal("99.99"))
    with pytest.raises(agriculture_store.AgricultureConflictError):
        agriculture_store.save_agriculture_prices([conflict], engine=pg_engine)

    latest = agriculture_store.fetch_latest_agriculture(engine=pg_engine)
    assert {p.commodity: p.source_price for p in latest} == {
        "MAIZE": Decimal("7.00"), "SOYA_BEANS": Decimal("10.02"),
    }
    history = agriculture_store.fetch_agriculture_history("MAIZE", "ALL", today=date(2026, 11, 13), engine=pg_engine)
    assert len(history) == 10
    assert history[0].source_price == Decimal("8.81")
    assert history[-1].source_price == Decimal("7.00")
