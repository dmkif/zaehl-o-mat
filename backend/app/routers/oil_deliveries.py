import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Meter, MeterType, OilDelivery, OilDeliveryDismissal, Property, User
from app.permissions import AccessLevel, has_property_access
from app.schemas.oil_deliveries import (
    DismissRequest, OilDeliveryCreate, OilDeliveryResponse, OilDeliveryUpdate,
    ProbableDeliveryResponse, derive_prices,
)
from app.services.oil_loader import load_oil_results

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/properties/{property_id}/oil-deliveries", tags=["oil-deliveries"])


def _authorize(db: Session, property_id: uuid.UUID, user: User, level: AccessLevel) -> None:
    """404 for unknown properties and for users without any access (same body, so
    existence is not revealed); 403 only when the user can see the property but lacks the level."""
    exists = db.query(Property.id).filter(Property.id == property_id).first() is not None
    if not exists or not has_property_access(db, property_id, user, "read"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    if level != "read" and not has_property_access(db, property_id, user, level):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


def _get_delivery(db: Session, property_id: uuid.UUID, delivery_id: int) -> OilDelivery:
    delivery = (
        db.query(OilDelivery)
        .filter(OilDelivery.id == delivery_id, OilDelivery.property_id == property_id)
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")
    return delivery


def _audit(action: str, user: User, property_id: uuid.UUID, delivery_id: int | None) -> None:
    # ids only, never free text (supplier/note)
    logger.info("oil_delivery action=%s user=%s property=%s delivery=%s", action, user.id, property_id, delivery_id)


def open_probable_deliveries(db: Session, property_ids: list[uuid.UUID], now: datetime) -> list[dict]:
    """Open probable deliveries of the active oil meters of the given properties."""
    meters = (
        db.query(Meter)
        .filter(
            Meter.property_id.in_(property_ids),
            Meter.meter_type == MeterType.oil,
            Meter.replaced_at.is_(None),
        )
        .all()
    )
    results = load_oil_results(db, meters, now)
    out = []
    for m in meters:
        for p in results[m.id].probable:
            out.append(
                {
                    "property_id": m.property_id,
                    "meter_id": m.id,
                    "from_reading_id": p.from_reading_id,
                    "to_reading_id": p.to_reading_id,
                    "from_at": p.from_at,
                    "to_at": p.to_at,
                    "estimated_liters": p.estimated_liters,
                }
            )
    return out


@router.get("/", response_model=List[OilDeliveryResponse])
def list_deliveries(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize(db, property_id, current_user, "read")
    return (
        db.query(OilDelivery)
        .filter(OilDelivery.property_id == property_id)
        .order_by(OilDelivery.delivered_at.desc(), OilDelivery.id.desc())
        .all()
    )


@router.get("/probable", response_model=List[ProbableDeliveryResponse])
def list_probable(
    property_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize(db, property_id, current_user, "read")
    return open_probable_deliveries(db, [property_id], datetime.now(timezone.utc))


def _dismissal_exists(db: Session, from_id: int, to_id: int) -> bool:
    return (
        db.query(OilDeliveryDismissal.id)
        .filter(OilDeliveryDismissal.from_reading_id == from_id, OilDeliveryDismissal.to_reading_id == to_id)
        .first()
        is not None
    )


@router.post("/probable/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_probable(
    property_id: uuid.UUID,
    body: DismissRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize(db, property_id, current_user, "write")
    # Idempotent: an already dismissed pair is no longer "open", so look at open ones
    # and at existing dismissals.
    pair = (body.from_reading_id, body.to_reading_id)
    already = _dismissal_exists(db, *pair)
    if not already:
        match = next(
            (
                p
                for p in open_probable_deliveries(db, [property_id], datetime.now(timezone.utc))
                if (p["from_reading_id"], p["to_reading_id"]) == pair
            ),
            None,
        )
        if match is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Probable delivery not found")
        db.add(
            OilDeliveryDismissal(
                meter_id=match["meter_id"],
                from_reading_id=pair[0],
                to_reading_id=pair[1],
                dismissed_by_id=current_user.id,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # a concurrent request dismissed the same pair first: same end state
            db.rollback()
    else:
        # only the property's own pairs may be confirmed as dismissed (no existence leak)
        if not _pair_belongs_to_property(db, property_id, pair):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Probable delivery not found")
    _audit("dismiss", current_user, property_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _pair_belongs_to_property(db: Session, property_id: uuid.UUID, pair: tuple[int, int]) -> bool:
    return (
        db.query(OilDeliveryDismissal.id)
        .join(Meter, Meter.id == OilDeliveryDismissal.meter_id)
        .filter(
            OilDeliveryDismissal.from_reading_id == pair[0],
            OilDeliveryDismissal.to_reading_id == pair[1],
            Meter.property_id == property_id,
        )
        .first()
        is not None
    )


@router.post("/", response_model=OilDeliveryResponse, status_code=status.HTTP_201_CREATED)
def create_delivery(
    property_id: uuid.UUID,
    body: OilDeliveryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize(db, property_id, current_user, "write")
    delivery = OilDelivery(property_id=property_id, created_by_id=current_user.id, **body.model_dump())
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    _audit("create", current_user, property_id, delivery.id)
    return delivery


@router.patch("/{delivery_id}", response_model=OilDeliveryResponse)
def update_delivery(
    property_id: uuid.UUID,
    delivery_id: int,
    body: OilDeliveryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize(db, property_id, current_user, "write")
    delivery = _get_delivery(db, property_id, delivery_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(delivery, field, value)

    # Prices: one given price derives the other from the (possibly new) litres; with
    # neither sent but new litres, the invoice total stays and the per-litre price follows.
    sent = changes.keys() & {"total_price", "price_per_liter"}
    liters = delivery.liters
    try:
        if sent == {"total_price", "price_per_liter"}:
            derive_prices(liters, delivery.total_price, delivery.price_per_liter)
        elif sent == {"total_price"}:
            delivery.total_price, delivery.price_per_liter = derive_prices(liters, delivery.total_price, None)
        elif sent == {"price_per_liter"}:
            delivery.total_price, delivery.price_per_liter = derive_prices(liters, None, delivery.price_per_liter)
        elif "liters" in changes and delivery.total_price is not None:
            delivery.total_price, delivery.price_per_liter = derive_prices(liters, delivery.total_price, None)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    db.commit()
    db.refresh(delivery)
    _audit("update", current_user, property_id, delivery.id)
    return delivery


@router.delete("/{delivery_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_delivery(
    property_id: uuid.UUID,
    delivery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize(db, property_id, current_user, "delete")
    delivery = _get_delivery(db, property_id, delivery_id)
    db.delete(delivery)
    db.commit()
    _audit("delete", current_user, property_id, delivery_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
