param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "status", "logs", "bootstrap-owner", "invite-browser", "fingerprint")]
    [string]$Action,
    [string]$StateDirectory = (Join-Path $env:LOCALAPPDATA "AutPlayServer"),
    [string]$DisplayName,
    [string]$DeviceName = $env:COMPUTERNAME,
    [string]$UserId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$bundleRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$stateRoot = [IO.Path]::GetFullPath($StateDirectory)
$envFile = Join-Path $stateRoot "server.env"
$projectFile = Join-Path $stateRoot "project-name.txt"
if (-not (Test-Path -LiteralPath $envFile) -or -not (Test-Path -LiteralPath $projectFile)) {
    throw "AutPlay server state is missing; run install-server.ps1 first"
}
$projectName = (Get-Content -Raw -LiteralPath $projectFile).Trim()
$manifest = Get-Content -Raw -LiteralPath (Join-Path $bundleRoot "server-installer-manifest.json") | ConvertFrom-Json
if ($projectName -notmatch "^[a-z0-9][a-z0-9_-]{2,62}$") {
    throw "STATE_PROJECT_INVALID"
}
function Get-StateValue([string]$Key) {
    $matches = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -like "$Key=*" })
    if ($matches.Count -ne 1) { throw "STATE_ENV_INVALID" }
    return $matches[0].Substring($Key.Length + 1)
}
function Test-PrivateIpv4([string]$Value) {
    $address = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$address) -or $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        return $false
    }
    $bytes = $address.GetAddressBytes()
    return $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
}
$stateImage = Get-StateValue "AUTPLAY_SERVER_IMAGE"
$stateReleaseTag = Get-StateValue "AUTPLAY_RELEASE_TAG"
$stateSourceCommit = Get-StateValue "AUTPLAY_SOURCE_COMMIT"
$stateBindHost = Get-StateValue "AUTPLAY_MOBILE_BIND_HOST"
if (
    $stateImage -ne [string]$manifest.image_tag -or
    $stateReleaseTag -ne [string]$manifest.release_tag -or
    $stateSourceCommit -ne [string]$manifest.source_commit
) {
    throw "STATE_VERSION_MISMATCH"
}
if (-not (Test-PrivateIpv4 $stateBindHost)) {
    throw "STATE_ORIGIN_MISMATCH"
}
$compose = @("compose", "--project-name", $projectName, "--env-file", $envFile)
foreach ($file in @("compose.yaml", "compose.runtime.yaml", "compose.admin-local.yaml", "compose.release.yaml")) {
    $compose += @("--file", (Join-Path $bundleRoot $file))
}
$compose += @("--profile", "runtime")

switch ($Action) {
    "start" { & docker @compose up --no-build --wait }
    "stop" { & docker @compose down --remove-orphans }
    "status" { & docker @compose ps }
    "logs" { & docker @compose logs --no-color --tail 200 }
    "bootstrap-owner" {
        if (-not $DisplayName) { throw "bootstrap-owner requires -DisplayName" }
        & docker @compose exec -T api autplay-admin bootstrap-owner `
            --display-name $DisplayName `
            --device-name $DeviceName `
            --platform OTHER `
            --app-version ([string]$manifest.release_version)
    }
    "invite-browser" {
        $parsed = [Guid]::Empty
        if (-not [Guid]::TryParse($UserId, [ref]$parsed)) { throw "invite-browser requires -UserId <owner UUID>" }
        & docker @compose exec -it api autplay-admin web-session-invite --user-id $parsed
    }
    "fingerprint" {
        $identityKey = Join-Path $stateRoot "secrets\profile-identity-p256.pem"
        if (-not (Test-Path -LiteralPath $identityKey)) { throw "Persistent server identity is missing" }
        $imageTag = [string]$manifest.image_tag
        & docker run --rm --network none --mount "type=bind,src=$identityKey,dst=/identity.pem,readonly" --entrypoint python $imageTag -c "from cryptography.hazmat.primitives import hashes,serialization; from cryptography.hazmat.primitives.asymmetric import ec; import hashlib; key=serialization.load_pem_private_key(open('/identity.pem','rb').read(),password=None); assert isinstance(key,ec.EllipticCurvePrivateKey) and isinstance(key.curve,ec.SECP256R1); print(hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest())"
    }
}
if ($LASTEXITCODE -ne 0) {
    throw "AutPlay server action failed: $Action"
}
