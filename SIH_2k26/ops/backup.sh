#!/usr/bin/env sh
# Back up the database and the private file storage (POSIX shell). The PowerShell equivalent is ops/backup.ps1 (the one exercised on the development machine).
#
#   PGHOST=... PGUSER=sih PGPASSWORD=... ops/backup.sh [output_dir]
#
# Writes db-<stamp>.dump, files-<stamp>.tar.gz and SHA256SUMS-<stamp>.txt. The database and the files must be backed up TOGETHER (the database refers to files by key).
# Backups hold personal data and private documents: encrypt them, store them off this machine, limit access to operators (docs/OPERATIONS.md).
set -eu

OUT="${1:-backups}"
DB="${PGDATABASE:-sih_aicte}"
FILES="${STORAGE_DIR:-backend/storage}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$OUT"
pg_dump --format=custom --no-owner --file "$OUT/db-$STAMP.dump" "$DB"
if [ -d "$FILES" ]; then
  tar -C "$FILES" -czf "$OUT/files-$STAMP.tar.gz" .
else
  echo "warning: storage directory '$FILES' does not exist; no file archive created" >&2
fi
( cd "$OUT" && sha256sum db-"$STAMP".dump files-"$STAMP".tar.gz 2>/dev/null > "SHA256SUMS-$STAMP.txt" )
echo "Backup written to $OUT (stamp $STAMP)"
echo "Next: copy it off this machine, encrypted, and prove it restores (see docs/OPERATIONS.md)."
