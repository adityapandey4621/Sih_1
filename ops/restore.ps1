<#
.SYNOPSIS
  Restore a backup, or prove that one restores.

.DESCRIPTION
  -Verify     restores the dump into a throw-away database (sih_verify_<stamp>), checks the schema revision and the row counts of the core tables, and drops it.
              Nothing real is touched. Run this on every backup you intend to rely on: an unverified backup is a hope, not a backup.
  -Target     restores into the named database (--clean --if-exists). Requires -IKnowThisOverwrites. Stop the application first.
  -Files      also unpacks the files archive into -StorageDir (only with -Target).
  The SHA256SUMS file next to the dump is checked first, if present.
#>
param(
  [Parameter(Mandatory = $true)][string]$Dump,
  [switch]$Verify,
  [string]$Target = "",
  [string]$Files = "",
  [string]$StorageDir = "backend/storage",
  [switch]$IKnowThisOverwrites,
  [string]$PgBin = "",
  [string]$MaintenanceDb = "postgres"
)
$ErrorActionPreference = "Stop"
function Tool([string]$name) { if ($PgBin) { Join-Path $PgBin "$name.exe" } else { $name } }
function Sql([string]$db, [string]$sql) { (& (Tool psql) -d $db -At -c $sql) }

if (-not (Test-Path $Dump)) { throw "Dump not found: $Dump" }
$dir = Split-Path -Parent (Resolve-Path $Dump)
$stamp = ([regex]::Match((Split-Path -Leaf $Dump), "db-(.+)\.dump")).Groups[1].Value
$sums = Join-Path $dir "SHA256SUMS-$stamp.txt"
if (Test-Path $sums) {
  foreach ($line in Get-Content $sums) {
    $hash, $name = $line -split "\s+", 2
    $path = Join-Path $dir $name.Trim()
    if (-not (Test-Path $path)) { throw "Listed file is missing: $name" }
    if ((Get-FileHash -Algorithm SHA256 $path).Hash.ToLower() -ne $hash) { throw "CHECKSUM MISMATCH for $name - the backup is damaged or was altered. Do not restore it." }
  }
  Write-Output "Checksums match."
} else {
  Write-Warning "No SHA256SUMS file next to the dump: its integrity cannot be checked."
}

if ($Verify) {
  $scratch = "sih_verify_" + (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
  Sql $MaintenanceDb "CREATE DATABASE $scratch" | Out-Null
  try {
    & (Tool pg_restore) --no-owner --exit-on-error -d $scratch $Dump
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed (exit $LASTEXITCODE): the dump does not restore." }
    $rev = Sql $scratch "SELECT version_num FROM alembic_version"
    Write-Output "Restored. Schema revision: $rev"
    foreach ($t in "users", "applications", "application_documents", "audit_events", "decision_cases", "obligations", "clocks", "scheduled_work", "external_syncs") {
      $n = Sql $scratch "SELECT count(*) FROM $t"
      Write-Output ("  {0,-24} {1} rows" -f $t, $n)
    }
    $trig = Sql $scratch "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'sih_%_append_only'"
    Write-Output "  append-only triggers present: $trig"
    Write-Output "VERIFIED: this backup restores into an empty database."
  } finally {
    Sql $MaintenanceDb "DROP DATABASE IF EXISTS $scratch" | Out-Null
  }
  return
}

if (-not $Target) { throw "Give -Verify, or -Target <database> -IKnowThisOverwrites." }
if (-not $IKnowThisOverwrites) { throw "Restoring overwrites '$Target'. Stop the application, then repeat with -IKnowThisOverwrites." }
& (Tool pg_restore) --clean --if-exists --no-owner --exit-on-error -d $Target $Dump
if ($LASTEXITCODE -ne 0) { throw "pg_restore failed (exit $LASTEXITCODE)." }
if ($Files) {
  New-Item -ItemType Directory -Force -Path $StorageDir | Out-Null
  & tar.exe -C $StorageDir -xzf $Files
  if ($LASTEXITCODE -ne 0) { throw "unpacking the files archive failed (exit $LASTEXITCODE)." }
}
Write-Output "Restored into $Target. Start the application, open /api/ready, and spot-check a few documents and decisions before announcing it."
