"""Create the first platform administrator of a new deployment (the demo seed is refused in production, so this is how the first account exists).

    python -m app.create_admin --email admin@your-organisation.example --name "Full Name"                    # prompts for the password
    python -m app.create_admin --email ... --name ... --password-file /run/secrets/first_admin_password    # non-interactive

The password is never taken from the command line (it would end up in shell history and process listings). Refuses to run if a user with that email already exists.
From then on, accounts are provisioned through the administrator's own screens (invitations). A platform administrator holds no decision, scrutiny or verification powers.
"""

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

from app import audit
from app.db import SessionLocal
from app.models import User
from app.rbac import Role
from app.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, hash_password, normalise_email


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first platform administrator")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--password-file", help="read the password from this file instead of prompting")
    args = parser.parse_args()
    email = normalise_email(args.email)
    if "@" not in email or len(email) > 320 or not args.name.strip():
        print("Give a valid email address and a name.", file=sys.stderr)
        return 2
    if args.password_file:
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat it: ") != password:
            print("The two entries differ.", file=sys.stderr)
            return 2
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        print(f"The password must be {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters.", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            print("A user with that email already exists. Nothing was changed.", file=sys.stderr)
            return 1
        user = User(email=email, full_name=args.name.strip()[:200], password_hash=hash_password(password), role=Role.PLATFORM_ADMIN.value)
        db.add(user)
        db.flush()
        audit.record(db, "admin.bootstrap_created", actor_label="operator (command line)", target_type="user", target_id=user.id, detail={"role": Role.PLATFORM_ADMIN.value})
        db.commit()
    print(f"Created platform administrator {email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
