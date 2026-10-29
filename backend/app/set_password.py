"""Operator tool: set a user's password and end their sessions.

    python -m app.set_password user@example.org

Prompts for the new password (not echoed). Interim recovery path until an email
provider and self-service reset exist; the action is written to the audit log.
"""

import getpass
import sys

from sqlalchemy import select, update

from app import audit
from app.db import SessionLocal
from app.models import AuthSession, User, utcnow
from app.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, hash_password, normalise_email


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m app.set_password <email>", file=sys.stderr)
        return 2
    email = normalise_email(argv[1])
    password = getpass.getpass("New password: ")
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        print(f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.", file=sys.stderr)
        return 1
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
        audit.record(db, "user.password_set_by_operator", actor_label="operator-cli", target_type="user", target_id=user.id)
        db.commit()
    print("Password updated; existing sessions revoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
