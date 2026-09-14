param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][switch]$PersonalNoncommercial
)

$ErrorActionPreference = 'Stop'
if (-not $PersonalNoncommercial) { throw 'This frozen experiment is private and noncommercial.' }
$manifestPath = Join-Path $PSScriptRoot 'artifacts.lock.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
foreach ($artifact in $manifest.files) {
    if ([System.IO.Path]::GetFileName($artifact.name) -ne $artifact.name) { throw 'Invalid artifact name' }
    $uri = [uri]$artifact.url
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'essentia.upf.edu') { throw 'Invalid artifact origin' }
    $target = Join-Path $destinationRoot $artifact.name
    $candidate = $target
    if (-not (Test-Path -LiteralPath $target)) {
        $candidate = "$target.part"
        Invoke-WebRequest -Uri $uri -OutFile $candidate -ConnectionTimeoutSeconds 90 -OperationTimeoutSeconds 120
    }
    $info = Get-Item -LiteralPath $candidate
    $digest = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($info.Length -ne $artifact.size_bytes -or $digest -ne $artifact.sha256) {
        throw "Artifact integrity mismatch: $($artifact.name)"
    }
    if ($candidate -ne $target) { Move-Item -LiteralPath $candidate -Destination $target }
    Write-Output "Verified $($artifact.name)"
}
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $destinationRoot 'artifacts.json')
