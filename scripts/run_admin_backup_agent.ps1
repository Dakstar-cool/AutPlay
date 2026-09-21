[CmdletBinding()]
param(
    [string]$DestinationRoot = 'E:\AutPlayBackups',
    [string]$BaselineFile = 'E:\AutPlayBackups\admin-backup-baseline.json',
    [string]$SshTarget = 'user@100.111.141.119',
    [string]$RemoteControlRoot = '/srv/autplay/operator/backup-control',
    [string]$TargetId = 'windows-usb-e'
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repositoryRoot 'server\.venv\Scripts\python.exe'
$agent = Join-Path $PSScriptRoot 'admin_target_external_backup.py'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "AutPlay server virtual environment is missing: $python"
}
if (-not (Test-Path -LiteralPath $agent -PathType Leaf)) {
    throw "AutPlay backup agent is missing: $agent"
}

& $python $agent `
    --destination-root $DestinationRoot `
    --baseline-file $BaselineFile `
    --ssh-target $SshTarget `
    --remote-control-root $RemoteControlRoot `
    --target-id $TargetId `
    --run-requested
exit $LASTEXITCODE
