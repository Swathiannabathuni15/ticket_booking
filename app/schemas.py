"""Request and response shapes (validated by Pydantic)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    is_admin: bool


class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    venue: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    rows: int = Field(ge=1, le=26)
    seats_per_row: int = Field(ge=1, le=100)
    price: float = Field(gt=0)


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    venue: str
    starts_at: datetime


class SeatOut(BaseModel):
    id: int
    label: str
    price: float
    available: bool


class HoldCreate(BaseModel):
    seat_id: int


class HoldOut(BaseModel):
    seat_id: int
    ttl_seconds: int


class BookingCreate(BaseModel):
    seat_id: int


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    seat_id: int
    status: str
    created_at: datetime
    payment_due_at: datetime | None = None
    paid_at: datetime | None = None


class PaymentCreate(BaseModel):
    booking_id: int
