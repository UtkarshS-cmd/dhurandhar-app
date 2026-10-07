"""Phase 8 admin schemas: pagination envelope + per-resource payloads.

Secrets policy: no schema here carries ``password_hash``, ``token_version``,
JWTs, Razorpay secrets or webhook secrets. Admin user payloads expose only
operational identity fields.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    items: list[T]
    page: int
    page_size: int
    total: int


class AdminUserOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    full_name: str
    email: str
    phone: str
    role: str
    is_active: bool
    created_at: datetime
    booking_count: int = 0
    review_count: int = 0


class AdminUserRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=4, max_length=20)


class AdminMovieCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    metadata_json: str = Field(default="{}", max_length=20000)
    is_active: bool = True


class AdminMovieUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    metadata_json: str | None = Field(default=None, max_length=20000)
    is_active: bool | None = None


class AdminTheaterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    address: str = Field(min_length=1, max_length=300)


class AdminTheaterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    address: str | None = Field(default=None, min_length=1, max_length=300)


class AdminShowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movie_id: int = Field(ge=1)
    screen_id: int = Field(ge=1)
    show_date: date
    show_time: time
    status: str = Field(default="ACTIVE", min_length=1, max_length=20)


class AdminShowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movie_id: int | None = Field(default=None, ge=1)
    screen_id: int | None = Field(default=None, ge=1)
    show_date: date | None = None
    show_time: time | None = None
    status: str | None = Field(default=None, min_length=1, max_length=20)


class AdminBookingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    booking_reference: str
    user_id: int | None
    user_name: str | None = None
    user_email: str | None = None
    show_id: int
    movie_title: str | None = None
    theater_name: str | None = None
    screen_name: str | None = None
    show_date: date | None = None
    show_time: time | None = None
    seats: list[str] = []
    status: str
    payment_method: str
    payment_status: str
    total_amount: Decimal
    created_at: datetime


class AdminPaymentAttemptOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    booking_id: int
    booking_reference: str | None = None
    provider: str
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    amount: Decimal
    currency: str
    status: str
    attempt_no: int
    failure_code: str | None = None
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminReviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    user_id: int
    user_name: str | None = None
    movie_id: int
    movie_title: str | None = None
    rating: int
    title: str
    body: str
    spoiler: bool
    likes: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class AdminAuditLogOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    admin_user_id: int | None
    admin_email: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    metadata_json: str = "{}"
    created_at: datetime
