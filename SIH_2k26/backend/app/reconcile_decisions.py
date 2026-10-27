"""Recover decision-stage work after a restart or an outage.

    python -m app.reconcile_decisions

  * retries every external sync that is SYNC_PENDING (or was NOT_CONFIGURED before a provider existed) and settles RECONCILIATION_REQUIRED ones by asking the receiver what it
    holds: a row becomes SYNCED only when the receiver confirms receipt. A FAILED row is never touched here: a person decides (the Bureau's "Try again" on /app/integration);
  * opens a decision case for any completed route that has a hand-off but no case (idempotent);
  * closes issued cases whose remaining condition was only the passage of time (an appeal window that has run out, with no appeal and nothing else open).

Safe to run repeatedly. It never decides anything and never skips a required step.
"""

from app.config import get_settings
from app.db import SessionLocal
from app.services import decisions, portal_sync, stages


def main() -> int:
    with SessionLocal() as db:
        stages.reconcile(db)
    with SessionLocal() as db:
        synced = portal_sync.reconcile(db)  # every unconfirmed record, now (the scheduler does the same but respects each record's back-off)
    with SessionLocal() as db:
        closed = decisions.finalise_expired(db, get_settings())
    print(f"External syncs confirmed: {synced}. Decision cases closed after their appeal window: {closed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
