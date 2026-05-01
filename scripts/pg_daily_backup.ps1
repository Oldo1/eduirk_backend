param(
  [string]$Container = $env:POSTGRES_CONTAINER,
  [string]$Database = $env:POSTGRES_DB,
  [string]$User = $env:POSTGRES_USER,
  [string]$BackupDir = ".\backups",
  [int]$KeepDays = 14
)

if (-not $Container) { $Container = "mky-postgres" }
if (-not $Database) { $Database = "eduirk_db" }
if (-not $User) { $User = "postgres" }

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$file = Join-Path $BackupDir "$Database-$stamp.dump"
$containerFile = "/tmp/$Database-$stamp.dump"

docker exec $Container sh -c "pg_dump -U $User -d $Database -Fc -f $containerFile"
docker cp "${Container}:${containerFile}" $file
docker exec $Container rm -f $containerFile | Out-Null

Get-ChildItem -Path $BackupDir -Filter "$Database-*.dump" |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
  Remove-Item -Force

Write-Host "Backup created: $file"
Write-Host "Restore check command:"
Write-Host "docker cp $file ${Container}:/tmp/restore-check.dump"
Write-Host "docker exec $Container pg_restore -U $User -d ${Database}_restore --clean --if-exists /tmp/restore-check.dump"
