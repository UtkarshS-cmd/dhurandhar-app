
from datetime import date, datetime, time
from decimal import Decimal
from pydantic import BaseModel, EmailStr, Field, field_validator

class UserCreate(BaseModel):
    full_name: str = Field(min_length=3, max_length=120)
    email: EmailStr
    phone: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit())
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        if len(digits) != 10 or digits[0] not in "6789":
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return digits

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

class UserOut(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    phone: str

class AuthOut(BaseModel):
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
    user_id: int | None = None
    show_id: int
    seat_ids: list[int] = Field(min_length=1, max_length=6)

class BookingConfirmRequest(BaseModel):
    payment_method: str = Field(min_length=2, max_length=60)

class BookingCreateRequest(BaseModel):
    user: UserCreate
    show_id: int
    seat_ids: list[int] = Field(min_length=1, max_length=6)
    payment_method: str = Field(min_length=2, max_length=60)

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
    rating: int = Field(ge=1, le=10)
    title: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=2, max_length=3000)
    spoiler: bool = False

class NewsletterCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr

class ContactCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    subject: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=5, max_length=5000)
