"""Development seed: one illustrative user per role plus one demo institution.

    python -m app.seed

Requires SEED_PASSWORD (min. 12 characters) in the environment or .env, and refuses to run
in production. All records are fictional and marked as demo data. This is the only place
demo identities are created.
"""

import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Institution, User
from app.rbac import ROLE_LABELS, Role
from app.security import PASSWORD_MIN_LENGTH, hash_password

DEMO_DOMAIN = "demo.example.org"
DEMO_INSTITUTION = "Demo Institute of Technology (illustrative)"


def main() -> int:
    settings = get_settings()
    if settings.is_production:
        print("Refusing to seed demo users in production.", file=sys.stderr)
        return 1
    if len(settings.seed_password) < PASSWORD_MIN_LENGTH:
        print(f"Set SEED_PASSWORD to at least {PASSWORD_MIN_LENGTH} characters (see .env.example).", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        inst = db.scalar(select(Institution).where(Institution.name == DEMO_INSTITUTION))
        if inst is None:
            inst = Institution(name=DEMO_INSTITUTION)
            db.add(inst)
            db.flush()

        created = []
        for role in Role:
            email = f"{role.value.replace('_', '.')}@{DEMO_DOMAIN}"
            if db.scalar(select(User).where(User.email == email)):
                continue
            db.add(
                User(
                    email=email,
                    full_name=f"Demo {ROLE_LABELS[role]}",
                    password_hash=hash_password(settings.seed_password),
                    role=role.value,
                    institution_id=inst.id if role is Role.INSTITUTE_USER else None,
                )
            )
            created.append(email)
        db.commit()

    print(f"Created {len(created)} demo user(s)." if created else "Demo users already exist; nothing changed.")
    for email in created:
        print(f"  {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
