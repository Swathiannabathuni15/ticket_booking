"""Pricing rules: weekend, evening or morning prices.

A rule says "for shows starting on these days, in this time window, (for this seat type), change the
price by N%". Every matching rule counts and their percentages ADD UP (Weekend +20% and Evening +10% on a
Saturday night = +30%), capped between -90% and +200% so a typo can't make seats free or absurd. Prices
are rounded to whole rupees.

Applied ONCE, when the showtime is created. The base prices are kept on the showtime and the final ones
are stamped on its seats, so:
  - a price the customer saw never changes under them (holds, checkout and refunds all agree);
  - the automatic schedule copies the BASE prices from the last show, so rules never compound.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import PricingRule

MIN_PERCENT, MAX_PERCENT = -90, 200
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Priced:
    prices: dict[str, float]  # final price per seat type
    note: str | None  # e.g. "Weekend +20%, Evening +10%"; None if nothing applied


def _local(starts_at: datetime) -> datetime:
    return starts_at.astimezone(ZoneInfo(settings.display_timezone))


def matches(rule: PricingRule, starts_at: datetime, seat_type: str | None = None) -> bool:
    local = _local(starts_at)
    if rule.days and local.weekday() not in rule.days:
        return False
    minute = local.hour * 60 + local.minute
    start, end = rule.start_minute, rule.end_minute
    if start is not None and end is not None:
        # A window may wrap past midnight (22:00-02:00).
        inside = start <= minute < end if start <= end else (minute >= start or minute < end)
        if not inside:
            return False
    return not (seat_type is not None and rule.seat_type and rule.seat_type != seat_type)


def _fmt(p: int) -> str:
    return f"+{p}%" if p > 0 else f"{p}%"


def apply_rules(rules: list[PricingRule], starts_at: datetime, base: dict[str, float]) -> Priced:
    final: dict[str, float] = {}
    used: dict[int, PricingRule] = {}
    for seat_type, price in base.items():
        hits = [r for r in rules if r.active and matches(r, starts_at, seat_type)]
        pct = max(MIN_PERCENT, min(MAX_PERCENT, sum(r.percent for r in hits)))
        final[seat_type] = float(round(float(price) * (100 + pct) / 100))
        used.update({r.id: r for r in hits})
    note = ", ".join(
        f"{r.name} {_fmt(r.percent)}" + (f" ({r.seat_type})" if r.seat_type else "") for r in used.values()
    )
    return Priced(final, note or None)


def active_rules(db: Session) -> list[PricingRule]:
    return list(db.scalars(select(PricingRule).where(PricingRule.active.is_(True)).order_by(PricingRule.id)))


def price_for(db: Session, starts_at: datetime, base: dict[str, float]) -> Priced:
    return apply_rules(active_rules(db), starts_at, base)


def describe(rule: PricingRule) -> str:
    """'Sat, Sun · 18:00-23:00 · gold · +20%' - for the admin list."""
    parts = [", ".join(DAY_NAMES[d] for d in sorted(rule.days)) if rule.days else "Every day"]
    if rule.start_minute is not None and rule.end_minute is not None:
        parts.append(
            f"{rule.start_minute // 60:02d}:{rule.start_minute % 60:02d}-{rule.end_minute // 60:02d}:{rule.end_minute % 60:02d}"
        )
    else:
        parts.append("any time")
    parts.append(rule.seat_type or "all seats")
    parts.append(_fmt(rule.percent))
    return " · ".join(parts)
