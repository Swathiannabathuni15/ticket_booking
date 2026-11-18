"""Request and response shapes (validated by Pydantic)."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

MAX_SEATS_PER_BOOKING = 8


def _no_duplicate_seats(seat_ids: list[int]) -> list[int]:
    if len(set(seat_ids)) != len(seat_ids):
        raise ValueError("seat_ids must not contain duplicates")
    return seat_ids


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (the OAuth word, not a secret)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str | None = None
    phone: str | None = None
    is_admin: bool


class _OtpTarget(BaseModel):
    """Who the code is for: a phone number OR an email address (exactly one)."""

    phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{10,15}$")
    email: EmailStr | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.phone is None) == (self.email is None):
            raise ValueError("send either phone or email")
        return self

    @property
    def identity(self) -> str:
        return ("+" + self.phone.lstrip("+")) if self.phone else str(self.email).lower()


class OtpRequest(_OtpTarget):
    pass


class OtpRequestOut(BaseModel):
    sent: bool = True
    channel: str = "screen"  # sms | email | screen (demo: no provider configured)
    destination: str = ""  # masked, e.g. +91*****3210
    expires_in_seconds: int
    # ONLY in demo mode: the code, so the flow can be tried without a provider.
    dev_otp: str | None = None


class OtpVerify(_OtpTarget):
    code: str = Field(pattern=r"^[0-9]{6}$")


class MovieCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    duration_minutes: int = Field(gt=0, le=600)
    poster_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    genre: str = Field(default="", max_length=100)
    language: str = Field(default="English", max_length=50)
    rating: float | None = Field(default=None, ge=0, le=10)
    release_date: date | None = None


class MovieUpdate(BaseModel):
    """Admin edits. Only the fields you send are changed."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    duration_minutes: int | None = Field(default=None, gt=0, le=600)
    poster_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    genre: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=50)
    rating: float | None = Field(default=None, ge=0, le=10)
    release_date: date | None = None


class MovieOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    duration_minutes: int
    poster_url: str | None = None
    genre: str = ""
    language: str = "English"
    rating: float | None = None
    release_date: date | None = None
    status: str = "now_showing"  # now_showing | coming_soon


class TheatreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)


class TheatreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    city: str


class ScreenRow(BaseModel):
    row: str = Field(min_length=1, max_length=5)
    # One entry per column; a seat type (e.g. "regular", "premium") or null for an aisle gap.
    seats: list[str | None] = Field(min_length=1, max_length=100)


class ScreenCreate(BaseModel):
    theatre_id: int
    name: str = Field(min_length=1, max_length=100)
    rows: list[ScreenRow] = Field(min_length=1, max_length=26)


class ScreenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    theatre_id: int
    name: str
    layout: list


class ShowtimeCreate(BaseModel):
    movie_id: int
    screen_id: int
    starts_at: datetime
    # seat type -> price, e.g. {"regular": 10.0, "premium": 15.0}; must cover
    # every seat type used in the screen's layout.
    prices: dict[str, float]


class ShowtimeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    movie_id: int
    screen_id: int
    starts_at: datetime
    movie_title: str
    screen_name: str
    theatre_name: str
    theatre_id: int
    city: str


class SeatOut(BaseModel):
    id: int
    label: str
    seat_type: str
    price: float
    # "available" | "held" | "held_by_me" | "booked"
    status: str
    available: bool  # convenience: status == "available"


class HoldCreate(BaseModel):
    seat_ids: list[int] = Field(min_length=1, max_length=MAX_SEATS_PER_BOOKING)

    _no_duplicates = field_validator("seat_ids")(_no_duplicate_seats)


class HoldOut(BaseModel):
    seat_ids: list[int]
    ttl_seconds: int


class BookingCreate(BaseModel):
    seat_ids: list[int] = Field(min_length=1, max_length=MAX_SEATS_PER_BOOKING)

    _no_duplicates = field_validator("seat_ids")(_no_duplicate_seats)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    showtime_id: int
    seat_ids: list[int]
    seat_labels: list[str]
    movie_title: str
    theatre_name: str
    city: str
    starts_at: datetime
    total_cents: int
    status: str
    # paid | processing (waiting for the bank / webhook) | failed | unpaid
    payment_status: str = "unpaid"
    created_at: datetime
    payment_due_at: datetime | None = None
    paid_at: datetime | None = None


class PaymentCreate(BaseModel):
    booking_id: int
    # Demo "card": tok_ok | tok_decline | tok_timeout (the bank charges but our reply is lost).
    payment_method: str = Field(default="tok_ok", pattern="^tok_(ok|decline|timeout)$")


class SplitCreate(BaseModel):
    friends: list[str] = Field(min_length=1, max_length=20)
    include_owner: bool = True
    mode: str = Field(default="equal", pattern="^(equal|custom)$")
    # mode=custom: one amount in cents per share, owner first when include_owner.
    custom_cents: list[int] | None = None

    @field_validator("friends")
    @classmethod
    def _clean_names(cls, v: list[str]) -> list[str]:
        v = [n.strip() for n in v]
        if any(not n or len(n) > 100 for n in v):
            raise ValueError("friend names must be 1-100 characters")
        return v


class ShareOut(BaseModel):
    id: int
    name: str
    amount_cents: int
    is_owner: bool
    status: str
    paid_at: datetime | None = None
    # Owner-only view: a freshly signed link path. Omitted on the public view.
    link: str | None = None


class SplitOut(BaseModel):
    id: int
    booking_id: int
    total_cents: int
    status: str
    paid_cents: int
    shares: list[ShareOut]


class ShareLinkView(BaseModel):
    """What a friend sees on the public link: no emails, no booking internals."""

    name: str
    amount_cents: int
    status: str
    movie_title: str
    starts_at: datetime
    seats: list[str]


class AdminBookingOut(BookingOut):
    user_id: int


class BestShowOut(BaseModel):
    """One show, and the best group of seats in it, for the seat finder."""

    showtime_id: int
    starts_at: datetime
    movie_title: str
    theatre_name: str
    city: str
    screen_name: str
    seat_ids: list[int]
    seat_labels: list[str]
    seat_type: str
    total_price: float
    rows_from_screen: int
    off_centre: float  # seats left (-) or right (+) of the middle
    score: float  # lower is better


class SeatOptionOut(BaseModel):
    """One suggested group of seats, with the reasons."""

    labels: list[str]  # Best view / Best value / Cheapest / Closest to your budget
    seat_ids: list[int]
    seat_labels: list[str]
    seat_type: str
    price_each: float
    total_price: float
    visibility: float  # 0-100, how well you would see the screen
    screen_angle: int  # degrees of your view the screen fills
    side_angle: int  # degrees off-centre
    neck_angle: int  # degrees you look up to the top of the screen
    rows_from_screen: int
    off_centre: float  # seats left (-) / right (+) of the middle
    across_aisle: bool
    aisle_seat: bool
    reasons: list[str]
    warnings: list[str]
    over_budget_by: float | None = None


class SeatOptionsOut(BaseModel):
    showtime_id: int
    count: int
    message: str | None = None  # explains an empty or compromised answer
    options: list[SeatOptionOut]


class TicketOut(BaseModel):
    """The owner's e-ticket."""

    booking_id: int
    ticket_code: str
    ticket_url: str  # what the QR code contains
    qr_image: str  # a data: URI (SVG) to show the QR code
    state: str  # valid | used | cancelled
    movie_title: str
    poster_url: str | None = None
    theatre_name: str
    city: str
    screen_name: str
    starts_at: datetime
    ends_at: datetime
    seat_labels: list[str]
    total_cents: int
    checked_in_at: datetime | None = None
    directions_url: str


class TicketPublicOut(BaseModel):
    """What anyone holding a ticket's code may see: nothing about who bought it."""

    ticket_code: str
    state: str  # valid | used | cancelled
    movie_title: str
    poster_url: str | None = None
    theatre_name: str
    city: str
    screen_name: str
    starts_at: datetime
    seat_labels: list[str]
    checked_in_at: datetime | None = None


class CheckInOut(BaseModel):
    result: str  # admitted | already_used | cancelled | too_early | expired | invalid
    message: str
    ticket: TicketPublicOut | None = None
