[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 9222,

    [string]$ProfileDirectory = (Join-Path $env:LOCALAPPDATA 'AutPlay\HitmoCdpProfile')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
)
$edgePath = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $edgePath) {
    throw 'edge_not_found'
}

$resolvedProfile = [System.IO.Path]::GetFullPath($ProfileDirectory)
$defaultProfile = [System.IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data')
)
if ($resolvedProfile.StartsWith($defaultProfile, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'default_edge_profile_forbidden'
}

New-Item -ItemType Directory -Path $resolvedProfile -Force | Out-Null
Start-Process -FilePath $edgePath -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$resolvedProfile",
    '--no-first-run',
    '--new-window',
    'https://ru.hitmoz.org/'
)

Write-Output "CDP_EDGE_STARTED port=$Port profile=dedicated"
