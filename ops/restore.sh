#!/usr/bin/env sh
# Restore a backup, or prove that one restores (POSIX shell; the PowerShell equivalent is ops/restore.ps1).
#
#   ops/restore.sh verify backups/db-<stamp>.dump            restore into a throw-away database, check it, drop it
#   ops/restore.sh restore backups/db-<stamp>.dump <database> [files.tar.gz]     OVERWRITES <database>; stop the application first
set -eu

MODE="${1:?usage: restore.sh verify|restore <dump> [database] [files.tar.gz]}"
DUMP="${2:?dump file required}"
DIR="$(dirname "$DUMP")"
STAMP="$(basename "$DUMP" .dump | sed 's/^db-//')"

if [ -f "$DIR/SHA256SUMS-$STAMP.txt" ]; then
  ( cd "$DIR" && sha256sum -c "SHA256SUMS-$STAMP.txt" ) || { echo "CHECKSUM MISMATCH: do not restore this backup." >&2; exit 2; }
else
  echo "warning: no SHA256SUMS file; integrity cannot be checked" >&2
fi

case "$MODE" in
  verify)
    SCRATCH="sih_verify_$(date -u +%Y%m%d%H%M%S)"
    psql -d "${MAINTENANCE_DB:-postgres}" -c "CREATE DATABASE $SCRATCH"
    trap 'psql -d "${MAINTENANCE_DB:-postgres}" -c "DROP DATABASE IF EXISTS $SCRATCH"' EXIT
    pg_restore --no-owner --exit-on-error -d "$SCRATCH" "$DUMP"
    echo "schema revision: $(psql -d "$SCRATCH" -At -c 'SELECT version_num FROM alembic_version')"
    for t in users applications application_documents audit_events decision_cases obligations clocks scheduled_work external_syncs; do
      echo "  $t: $(psql -d "$SCRATCH" -At -c "SELECT count(*) FROM $t") rows"
    done
    echo "VERIFIED: this backup restores into an empty database."
    ;;
  restore)
    TARGET="${3:?target database required}"
    pg_restore --clean --if-exists --no-owner --exit-on-error -d "$TARGET" "$DUMP"
    if [ -n "${4:-}" ]; then mkdir -p "${STORAGE_DIR:-backend/storage}" && tar -C "${STORAGE_DIR:-backend/storage}" -xzf "$4"; fi
    echo "Restored into $TARGET. Start the application, open /api/ready and spot-check before announcing it."
    ;;
  *) echo "unknown mode" >&2; exit 1 ;;
esac
