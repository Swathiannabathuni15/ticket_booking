"""Admin-only read endpoints for the small admin UI (B5)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import Booking, PricingRule, Screen, User
from app.schemas import AdminBookingOut, DashboardOut, PricePreviewOut, PricingRuleIn, PricingRuleOut
from app.services import pricing_service, stats_service
from app.services.schedule_service import prices_for

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/bookings", response_model=list[AdminBookingOut])
def all_bookings(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    limit = max(1, min(limit, 200))
    stmt = select(Booking).order_by(Booking.id.desc()).limit(limit).offset(max(offset, 0))
    if status:
        stmt = stmt.where(Booking.status == status)
    return db.scalars(stmt).all()


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(days: int = 14, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Revenue and bookings per day, occupancy per show, top movies (see stats_service)."""
    return stats_service.dashboard(db, days)


# ---------- pricing rules ----------


def _out(rule: PricingRule) -> PricingRuleOut:
    out = PricingRuleOut.model_validate(rule)
    out.summary = pricing_service.describe(rule)
    return out


@router.get("/pricing-rules", response_model=list[PricingRuleOut])
def list_rules(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return [_out(r) for r in db.scalars(select(PricingRule).order_by(PricingRule.id))]


@router.post("/pricing-rules", response_model=PricingRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: PricingRuleIn, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    rule = PricingRule(**body.model_dump())
    db.add(rule)
    db.commit()
    return _out(rule)


@router.put("/pricing-rules/{rule_id}", response_model=PricingRuleOut)
def update_rule(
    rule_id: int, body: PricingRuleIn, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    rule = db.get(PricingRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    db.commit()
    return _out(rule)


@router.delete("/pricing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    rule = db.get(PricingRule, rule_id)
    if rule is not None:
        db.delete(rule)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/pricing-rules/preview", response_model=PricePreviewOut)
def preview(screen_id: int, starts_at: datetime, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """What a show on this screen at this time would cost with today's rules."""
    screen = db.get(Screen, screen_id)
    if screen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screen not found")
    base = prices_for(db, screen)
    priced = pricing_service.price_for(db, starts_at, base)
    return PricePreviewOut(base=base, final=priced.prices, note=priced.note)
