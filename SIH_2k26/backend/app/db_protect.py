"""Database-level protection of the records that must never change: the audit trail, recorded decisions, evidence, reviews, history and the integration log.

The application already refuses to change them (app/guards.py). This adds a second, independent barrier inside PostgreSQL: a trigger that rejects UPDATE,
DELETE and TRUNCATE on these tables, so a bug, a raw SQL console or a compromised application account cannot rewrite history through ordinary statements.

Honest limits: a database superuser or the table owner can drop a trigger. Protect those credentials, ship the audit log elsewhere (docs/OPERATIONS.md), and
treat a missing trigger as a finding (`python -m app.db_protect --check`). SQLite (used only by the fast tests) has no equivalent here.
"""

import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

APPEND_ONLY_TABLES = (
    "audit_events", "ec_decisions", "bureau_observations", "appeal_documents", "obligation_submissions", "obligation_documents", "obligation_reviews", "obligation_events",
    "clock_events", "integration_logs",
)

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION sih_forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only: % is not permitted', TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""


def install(conn: Connection) -> None:
    conn.execute(text(FUNCTION_SQL))
    for t in APPEND_ONLY_TABLES:
        conn.execute(text(f"DROP TRIGGER IF EXISTS sih_{t}_append_only ON {t}"))
        conn.execute(text(f"CREATE TRIGGER sih_{t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION sih_forbid_change()"))
        conn.execute(text(f"DROP TRIGGER IF EXISTS sih_{t}_no_truncate ON {t}"))
        conn.execute(text(f"CREATE TRIGGER sih_{t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION sih_forbid_change()"))


def uninstall(conn: Connection) -> None:
    for t in APPEND_ONLY_TABLES:
        conn.execute(text(f"DROP TRIGGER IF EXISTS sih_{t}_append_only ON {t}"))
        conn.execute(text(f"DROP TRIGGER IF EXISTS sih_{t}_no_truncate ON {t}"))
    conn.execute(text("DROP FUNCTION IF EXISTS sih_forbid_change()"))


def missing(conn: Connection) -> list[str]:
    """Tables whose protection is absent. Empty means every protected table has both triggers."""
    present = {r[0] for r in conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
    return [t for t in APPEND_ONLY_TABLES if f"sih_{t}_append_only" not in present or f"sih_{t}_no_truncate" not in present]


def main() -> int:  # pragma: no cover - an operator command
    from app.db import engine

    if engine.dialect.name != "postgresql":
        print("Append-only triggers apply to PostgreSQL only.")
        return 0
    with engine.connect() as conn:
        gone = missing(conn)
    if gone:
        print("MISSING protection on: " + ", ".join(gone))
        return 1
    print("All append-only tables are protected by triggers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
