"""Shared admin helpers: pagination guard + append-only audit logging."""

import json

from fastapi import HTTPException, Query
from sqlalchemy.orm import Session

from ..models import AdminAuditLog

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


def pagination_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> tuple[int, int]:
    return page, page_size


def paginate(items: list, total: int, page: int, page_size: int) -> dict:
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def audit(
    db: Session,
    *,
    admin_user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str | int | None = None,
    metadata: dict | None = None,
) -> None:
    """Stage (not commit) an audit row; callers commit atomically with the mutation."""
    safe_meta: dict = {}
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            if any(secret in lowered for secret in ("password", "secret", "token", "authorization", "hash", "key")):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe_meta[key] = value
            else:
                safe_meta[key] = str(value)[:500]
    db.add(
        AdminAuditLog(
            admin_user_id=admin_user_id,
            action=action[:60],
            resource_type=resource_type[:60],
            resource_id=None if resource_id is None else str(resource_id)[:120],
            metadata_json=json.dumps(safe_meta)[:4000],
        )
    )


def require_mutation_success_path():
    """Marker: audit rows are staged only after the mutation validates.

    Call sites add business mutations + ``audit(...)`` to the session and
    commit once, so a rolled-back mutation never leaves a false audit trail.
    """
    raise HTTPException(500, "unreachable")
