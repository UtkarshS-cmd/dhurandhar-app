"""Shared authentication dependencies (extracted from main.py in Phase 8).

``main.py`` re-exports ``current_user``/``optional_current_user`` so existing
imports (tests, routers) keep working. Admin routers import from here to
avoid a circular import (``admin/* -> main`` while ``main -> admin``).
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User, UserRole
from .security import AuthenticationError, decode_token_payload


def current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    """Resolve the authenticated user or raise a uniform 401.

    Every failure (missing/malformed/expired/tampered token, unknown user,
    disabled account, or stale token version) returns a bare 401 with a
    Bearer challenge so session problems are indistinguishable to callers.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token_payload(authorization.split(" ", 1)[1].strip())
        uid = int(payload["sub"])
    except (AuthenticationError, ValueError, KeyError, IndexError):
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"}) from None
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    try:
        token_version = payload.get("ver")
        if token_version is None:
            if user.token_version != 1:
                raise ValueError("missing token version")
        else:
            if int(token_version) != user.token_version:
                raise ValueError("token version mismatch")
    except (TypeError, ValueError):
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"}) from None
    return user


def optional_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User | None:
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    try:
        payload = decode_token_payload(authorization.split(" ", 1)[1].strip())
        uid = int(payload["sub"])
    except (AuthenticationError, ValueError, KeyError, IndexError):
        return None
    user = db.get(User, uid)
    if not user or not user.is_active:
        return None
    try:
        token_version = payload.get("ver")
        if token_version is None:
            if user.token_version != 1:
                return None
        else:
            if int(token_version) != user.token_version:
                return None
    except (TypeError, ValueError):
        return None
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """Admin authorization boundary (Phase 8 RBAC).

    Chain: JWT validation -> user lookup -> active check -> token_version
    check (all inside ``current_user``) -> role check here.
    Authenticated non-admins get 403; unauthenticated callers never reach
    this (``current_user`` raises 401 first). The role is read from the
    database on every request, so demotion takes effect immediately and no
    stale JWT can retain admin access.
    """
    if (user.role or UserRole.USER.value) != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def is_admin(user: User | None) -> bool:
    return bool(user is not None and (user.role or UserRole.USER.value) == UserRole.ADMIN.value)
