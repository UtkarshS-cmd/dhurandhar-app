"""Promote an existing user to ADMIN (idempotent development/deployment tool).

Usage:
    ADMIN_EMAIL=admin@example.com python backend/admin_bootstrap.py
    python backend/admin_bootstrap.py --email admin@example.com

The target account must already exist (register normally first). The script
only flips ``users.role`` to ADMIN; it never creates accounts, never resets
passwords, and never runs automatically at startup. Re-running with the same
email is a no-op (idempotent). ``ADMIN_EMAIL`` env var takes precedence over
``--email`` when both are given.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import User, UserRole  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote an existing user to ADMIN.")
    parser.add_argument("--email", default=None, help="Email of the user to promote")
    args = parser.parse_args()
    email = (os.getenv("ADMIN_EMAIL") or args.email or "").strip().lower()
    if not email:
        print("Set ADMIN_EMAIL or pass --email <address> of an EXISTING user.", file=sys.stderr)
        return 2
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            print(f"No user found with email {email!r}; register that account first.", file=sys.stderr)
            return 1
        if not user.is_active:
            print(f"User {email!r} is deactivated; activate it before promoting.", file=sys.stderr)
            return 1
        if (user.role or UserRole.USER.value) == UserRole.ADMIN.value:
            print(f"{email} is already an administrator (id={user.id}).")
            return 0
        user.role = UserRole.ADMIN.value
        db.add(user)
        db.commit()
        print(f"Promoted {email} to ADMIN (id={user.id}).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
