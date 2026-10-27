<#
.SYNOPSIS
  Back up the database and the private file storage. Windows / PowerShell version of ops/backup.sh.

.DESCRIPTION
  Writes, into -OutDir:  db-<stamp>.dump (pg_dump custom format), files-<stamp>.tar.gz (the private attachments) and SHA256SUMS-<stamp>.txt.
  The database and the files MUST be backed up together: the database refers to files by key.
  Connection settings come from the standard PG* environment variables (PGHOST, PGPORT, PGUSER, PGPASSWORD or PGPASSFILE) - nothing is read from, or written to, the application's settings.
  Backups contain personal data and private documents: store them encrypted, off the machine, with access limited to operators (docs/OPERATIONS.md).

.EXAMPLE
  $env:PGHOST="127.0.0.1"; $env:PGUSER="sih"; $env:PGPASSWORD=(Get-Content secrets\db_password); .\ops\backup.ps1 -Database sih_aicte -StorageDir backend\storage
#>
param(
  [string]$Database = $(if ($env:PGDATABASE) { $env:PGDATABASE } else { "sih_aicte" }),
  [string]$OutDir = "backups",
  [string]$StorageDir = "backend/storage",
  [string]$PgBin = ""
)
$ErrorActionPreference = "Stop"
function Tool([string]$name) { if ($PgBin) { Join-Path $PgBin "$name.exe" } else { $name } }

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$dump = Join-Path $OutDir "db-$stamp.dump"
$files = Join-Path $OutDir "files-$stamp.tar.gz"

& (Tool pg_dump) --format=custom --no-owner --file $dump $Database
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed (exit $LASTEXITCODE). No backup was produced." }

if (Test-Path $StorageDir) {
  & tar.exe -C $StorageDir -czf $files .
  if ($LASTEXITCODE -ne 0) { throw "archiving the storage directory failed (exit $LASTEXITCODE)." }
} else {
  Write-Warning "Storage directory '$StorageDir' does not exist: the file archive was not created."
}

$sums = Join-Path $OutDir "SHA256SUMS-$stamp.txt"
Get-ChildItem $OutDir -Filter "*-$stamp.*" | Where-Object { $_.Name -notlike "SHA256SUMS*" } | ForEach-Object {
  "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLower(), $_.Name
} | Set-Content -Path $sums -Encoding ascii

Write-Output "Backup written to $OutDir (stamp $stamp):"
Get-ChildItem $OutDir -Filter "*-$stamp.*" | ForEach-Object { "  {0}  {1:N0} bytes" -f $_.Name, $_.Length }
Write-Output "Next: copy it OFF this machine, encrypted, and prove it restores:  .\ops\restore.ps1 -Verify -Dump $dump"
