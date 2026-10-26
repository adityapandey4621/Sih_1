"""Re-create in-app notifications that should exist but do not (for example after a notification store failure).

    python -m app.reconcile_notifications

Idempotent: notifications are keyed by event + subject + recipient, so running it twice creates nothing new.
Only issued-notice notifications are reconciled; other events are re-derived from case history by later phases.
"""

from app.db import SessionLocal
from app.services import notifications


def main() -> int:
    with SessionLocal() as db:
        made = notifications.reconcile(db)
        db.commit()
    print(f"Created {made} missing notification(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
