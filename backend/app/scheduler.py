"""The deadline scheduler process.

    python -m app.scheduler            # run forever: derive clocks, plan alerts, send due alerts
    python -m app.scheduler --once     # one full pass and exit (development, CI, a cron job, or recovery after an outage)

Everything it needs is in PostgreSQL. Stop it, kill it or restart the machine: on the next start it re-derives every clock from the recorded cases, plans any alert
that is missing, hands work abandoned by a dead process back to the queue (lease expiry), and sends what became due while it was away - skipping alerts whose
subject has since finished. Run as many copies as you like; a claimed row is processed by exactly one of them. Logs carry identifiers and error codes only.
"""

import argparse
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.services import scheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Deadline and alert scheduler")
    parser.add_argument("--once", action="store_true", help="run one full pass and exit")
    parser.add_argument("--requeue-failed", action="store_true", help="give alerts that exhausted their retries another bounded set of attempts, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if args.requeue_failed:
        with SessionLocal() as db:
            print(f"Requeued {scheduler.requeue_failed(db)} alert(s) that had exhausted their retries.")
        return 0
    if args.once:
        with SessionLocal() as db:
            stats = scheduler.run_cycle(db, scheduler.worker_id(), settings)
        print("Scheduler pass complete: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        return 0
    scheduler.run_forever(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
