"""Web Push endpoints: the website sends us the device's subscription; we use it
to deliver "your movie starts soon" notifications (app/services/push_service.py)."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import PushSubscription, User
from app.services import push_service

router = APIRouter(prefix="/push", tags=["push"])


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=4, max_length=100)


class SubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000, pattern=r"^https://")
    keys: Keys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)


@router.get("/public-key")
def public_key():
    """The server's VAPID public key, which the browser needs to subscribe."""
    return {"public_key": push_service.get_keys()["public"]}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(body: SubscribeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint, user_id=user.id, p256dh=body.keys.p256dh, auth=body.keys.auth)
        db.add(sub)
    else:  # same browser, maybe a different account now: it belongs to whoever is signed in
        sub.user_id, sub.p256dh, sub.auth = user.id, body.keys.p256dh, body.keys.auth
    db.commit()
    return {"subscribed": True}


@router.delete("/subscribe")
def unsubscribe(body: UnsubscribeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sub = db.scalar(select(PushSubscription).where(
        PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id))
    if sub is not None:
        db.delete(sub)
        db.commit()
    return {"subscribed": False}


@router.post("/test")
def send_test(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Send a test notification to all of my devices."""
    sent = push_service.send_to_user(db, user.id, {
        "title": "Reminders are on", "body": "You'll get a notification 15 minutes before your movie.",
        "tag": "test", "url": "/bookings"})
    if sent == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "No device could be reached. Turn on reminders in this browser first.")
    return {"sent": sent}
