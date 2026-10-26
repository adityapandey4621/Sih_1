"""Recover committee work after a restart or an outage.

    python -m app.reconcile_committee

  * activates any EVC / SHC stage that a recorded route resolution names but whose case was never created (idempotent);
  * polls every pending selection request of a connected provider and applies results that arrived (a provider that is not connected leaves
    its requests pending and visible).

Safe to run repeatedly. It never decides anything and never skips a stage: it only completes steps the approved route already requires.
"""

from app.config import get_settings
from app.db import SessionLocal
from app.services import selection, stages


def main() -> int:
    with SessionLocal() as db:
        activated = stages.reconcile(db)
    with SessionLocal() as db:
        applied = selection.reconcile(db, get_settings())
    print(f"Stage activations recovered: {activated}. Selection results applied: {applied}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
