
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# Payment methods accepted by the API. "PENDING" is the placeholder the
# checkout sends while holding seats; the remaining values mirror the payment
# <select> options in index.html. The backend — not the frontend — is the
# authority on which values are valid.
PaymentMethod = Literal[
    "PENDING",
    "UPI / Google Pay / PhonePe",
    "Credit / Debit Card",
    "Net Banking",
    "Pay at Counter",
]

# Seats are positive database identifiers; 0/negative values are rejected by
# schema validation before any query runs.
SeatId = Annotated[int, Field(ge=1)]


def _strip(value: str) -> str:
    return value.strip() if isinstance(value, str) else value


class _NormalizedEmail(BaseModel):
    """Base for schemas with an ``email`` field: trim + lowercase once, at
    the API edge, so uniqueness/lookup can never be bypassed with casing."""

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _strip(value).lower() if isinstance(value, str) else value


class UserCreate(_NormalizedEmail):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=3, max_length=120)
    email: EmailStr
    phone: str = Field(min_length=10, max_length=30)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        # Leading/trailing whitespace removed; internal spaces preserved.
        return _strip(value)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit())
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        if len(digits) != 10 or digits[0] not in "6789":
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return digits

class LoginRequest(_NormalizedEmail):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

class UserOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    full_name: str
    email: EmailStr
    phone: str

class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, min_length=3, max_length=120)
    phone: str | None = Field(default=None, min_length=10, max_length=30)

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        return _strip(value)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit())
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        if len(digits) != 10 or digits[0] not in "6789":
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return digits

    @model_validator(mode="after")
    def require_one_field(self):
        if self.full_name is None and self.phone is None:
            raise ValueError("Provide at least one profile field to update")
        return self

class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

class AuthOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    token_type: str = "bearer"
    user: UserOut

class ShowOut(BaseModel):
    id: int
    date: date
    time: time
    theater_id: int
    theater_name: str
    screen_id: int

class SeatOut(BaseModel):
    id: int
    row: str
    number: int
    category: str
    price: Decimal
    status: str

class HoldRequest(BaseModel):
    user: UserCreate | None = None
    show_id: int = Field(ge=1)
    seat_ids: list[SeatId] = Field(min_length=1, max_length=6)

class BookingConfirmRequest(BaseModel):
    payment_method: PaymentMethod


class PaymentOrderRequest(BaseModel):
    # No amount field by design: the amount always comes from the
    # server-side booking record; client-submitted amounts are ignored.
    booking_reference: str = Field(min_length=4, max_length=64)

class BookingCreateRequest(BaseModel):
    user: UserCreate  # nested schema normalizes/validates the email
    show_id: int = Field(ge=1)
    # 1..6 seats; duplicate rejection happens in the booking engine (422).
    seat_ids: list[SeatId] = Field(min_length=1, max_length=6)
    payment_method: PaymentMethod

class BookingOut(BaseModel):
    booking_reference: str
    status: str
    payment_status: str
    total_amount: Decimal
    show_id: int
    seats: list[str]
    created_at: datetime
    hold_expires_at: datetime | None = None

class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movie_id: int | None = Field(default=None, ge=1)
    rating: int = Field(ge=1, le=10)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=3000)
    spoiler: bool = False

    @field_validator("title", "body", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        text = _strip(value)
        if not text:
            raise ValueError("Value cannot be empty")
        return text


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int | None = Field(default=None, ge=1, le=10)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1, max_length=3000)
    spoiler: bool | None = None

    @field_validator("title", "body", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        text = _strip(value)
        if not text:
            raise ValueError("Value cannot be empty")
        return text

    @model_validator(mode="after")
    def require_content(self):
        if all(getattr(self, field) is None for field in ("rating", "title", "body", "spoiler")):
            raise ValueError("Provide at least one field to update")
        return self


class NewsletterCreate(_NormalizedEmail):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip(value)

class ContactCreate(_NormalizedEmail):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    subject: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=5, max_length=5000)

    @field_validator("name", "subject", "message", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)
