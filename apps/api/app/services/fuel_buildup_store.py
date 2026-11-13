"""PostgreSQL persistence for ERB build-up rows and press-statement retail prices (no raw PDFs)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_engine
from app.schemas.fuel_buildup import BuildUpComponent, FuelBuildUp
from app.services.erb_fuel import FuelRow
from app.services.erb_pdf import ParsedBuildUp
from app.services.fuel_store import _INSERT as _INSERT_PRICE, _SELECT_ONE as _SELECT_PRICE, FuelConflictError

_INSERT_ITEM = text(
    """
    INSERT INTO zambia_fuel_build_up_items
        (effective_date, publication_month, build_up_type, product, component_code, component_label, position,
         amount, currency, unit, note, source, source_url, retrieved_at)
    VALUES (:effective_date, :publication_month, :build_up_type, :product, :component_code, :component_label,
            :position, :amount, :currency, :unit, :note, 'erb', :source_url, :retrieved_at)
    ON CONFLICT (effective_date, build_up_type, product, component_code, unit) DO NOTHING
    """
)
_SELECT_ITEM = text(
    """
    SELECT amount FROM zambia_fuel_build_up_items
    WHERE effective_date = :effective_date AND build_up_type = :build_up_type AND product = :product
      AND component_code = :component_code AND unit = :unit
    """
)


def save_fuel_publications(
    retail: list[tuple[FuelRow, str]], builds: list[tuple[ParsedBuildUp, str]], *,
    retrieved_at: datetime, engine: Engine | None = None,
) -> dict[str, int]:
    """One transaction for retail prices (press statements) and build-up rows.

    new = insert, numerically identical = already present, different value = conflict (nothing
    from the batch is written, nothing is overwritten). A NULL (not published) equals a NULL.
    """
    counts = {"retail_inserted": 0, "retail_already_present": 0, "items_inserted": 0, "items_already_present": 0}
    conflicts = 0
    with (engine or get_engine()).begin() as connection:
        for row, url in retail:
            params = {"effective_date": row.effective_date, "product": row.product, "price_type": row.price_type,
                      "price_zmw": row.price_zmw, "unit": row.unit, "source_url": url, "retrieved_at": retrieved_at}
            if connection.execute(_INSERT_PRICE, params).rowcount == 1:
                counts["retail_inserted"] += 1
            elif connection.execute(_SELECT_PRICE, params).scalar_one() == row.price_zmw:
                counts["retail_already_present"] += 1
            else:
                conflicts += 1
        for build, url in builds:
            kind = "pump" if build.kind == "pump" else "wholesale"
            for item in build.items:
                params = {"effective_date": build.month, "publication_month": build.month.strftime("%Y-%m"),
                          "build_up_type": kind, "product": item.product, "component_code": item.component_code,
                          "component_label": item.component_label, "position": item.position, "amount": item.amount,
                          "currency": item.currency, "unit": item.unit or "", "note": item.note,
                          "source_url": url, "retrieved_at": retrieved_at}
                if connection.execute(_INSERT_ITEM, params).rowcount == 1:
                    counts["items_inserted"] += 1
                else:
                    existing = connection.execute(_SELECT_ITEM, params).scalar_one()
                    if existing == item.amount:  # None == None for "not published"; Decimals compare numerically
                        counts["items_already_present"] += 1
                    else:
                        conflicts += 1
        if conflicts:
            raise FuelConflictError(conflicts)
    return counts


_MONTH = text(
    """
    SELECT max(publication_month) FROM zambia_fuel_build_up_items
    WHERE product = :product AND build_up_type = :build_up_type
    """
)
_ITEMS = text(
    """
    SELECT effective_date, publication_month, component_code, component_label, amount, currency, unit, note,
           source_url, retrieved_at
    FROM zambia_fuel_build_up_items
    WHERE product = :product AND build_up_type = :build_up_type AND publication_month = :month
    ORDER BY position ASC, unit ASC
    """
)


def fetch_build_up(product: str, build_up_type: str, month: str | None, engine: Engine | None = None) -> FuelBuildUp | None:
    """Stored rows only. `month` is YYYY-MM; None means the newest stored month for that product/type."""
    with (engine or get_engine()).connect() as connection:
        key = {"product": product, "build_up_type": build_up_type}
        chosen = month or connection.execute(_MONTH, key).scalar()
        if chosen is None:
            return None
        rows = connection.execute(_ITEMS, {**key, "month": chosen}).mappings().all()
    if not rows:
        return None
    first = rows[0]
    return FuelBuildUp(
        product=product, effective_date=first["effective_date"], publication_month=first["publication_month"],
        build_up_type=build_up_type, source_url=first["source_url"], retrieved_at=first["retrieved_at"],
        components=[BuildUpComponent(code=r["component_code"], label=r["component_label"], amount=r["amount"],
                                     currency=r["currency"], unit=r["unit"] or None, note=r["note"]) for r in rows],
    )
