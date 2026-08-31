param(
    [Parameter(Mandatory = $true)]
    [string]$BindHost,
    [string]$StateDirectory = (Join-Path $env:LOCALAPPDATA "AutPlayServer"),
    [string]$ProjectName = "autplay-personal",
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function New-SecretFile([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        return
    }
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        $value = [Convert]::ToBase64String($bytes)
        [IO.File]::WriteAllText($Path, "$value`n", [Text.UTF8Encoding]::new($false))
    }
    finally {
        $rng.Dispose()
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

if (-not (Test-PrivateIpv4 $BindHost)) {
    throw "BindHost must be a concrete RFC1918 IPv4 address (10/8, 172.16/12, or 192.168/16); never use 0.0.0.0"
}
if ($StateDirectory -match "[`r`n]") {
    throw "STATE_PATH_INVALID"
}
if ($ProjectName -notmatch "^[a-z0-9][a-z0-9_-]{2,62}$") {
    throw "ProjectName must contain 3-63 lowercase letters, digits, underscores, or hyphens"
}

$bundleRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$localAppDataRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd("\") + "\"
$stateRoot = [IO.Path]::GetFullPath($StateDirectory)
if (
    -not $stateRoot.StartsWith($localAppDataRoot, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFileName($stateRoot.TrimEnd("\")) -ne "AutPlayServer" -or
    $stateRoot.StartsWith($bundleRoot.TrimEnd("\") + "\", [StringComparison]::OrdinalIgnoreCase) -or
    $stateRoot -eq $bundleRoot
) {
    throw "STATE_PATH_UNSAFE"
}

function Set-PrivateDirectoryAcl([string]$Path) {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        [Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
    [IO.Directory]::SetAccessControl($Path, $acl)
}

function Test-PrivateDirectoryAcl([string]$Path) {
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = [IO.Directory]::GetAccessControl($Path)
    if (-not $acl.AreAccessRulesProtected) { return $false }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -eq 0) { return $false }
    foreach ($rule in $rules) {
        if (
            $rule.IdentityReference -ne $currentSid -or
            $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow
        ) {
            return $false
        }
    }
    return $true
}

$manifestPath = Join-Path $bundleRoot "server-installer-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "INSTALLER_MANIFEST_MISSING"
}
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$archiveName = [string]$manifest.server_archive.filename
$imageTag = [string]$manifest.image_tag
if (
    [IO.Path]::GetFileName($archiveName) -ne $archiveName -or
    $archiveName -notmatch '^autplay-server-v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?\.docker\.tar\.gz$' -or
    $imageTag -notmatch '^autplay-server:v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$'
) {
    throw "INSTALLER_MANIFEST_INVALID"
}
$archivePath = Join-Path $bundleRoot $archiveName
if (-not (Test-Path -LiteralPath $archivePath)) {
    throw "SERVER_ARCHIVE_MISSING"
}
$actualArchiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualArchiveHash -ne [string]$manifest.server_archive.sha256) {
    throw "ARCHIVE_HASH_MISMATCH"
}

& docker version --format "{{.Server.Version}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "DOCKER_ENGINE_UNAVAILABLE"
}
$composeVersionText = (& docker compose version --short | Out-String).Trim().TrimStart("v")
if ($LASTEXITCODE -ne 0 -or -not $composeVersionText) {
    throw "COMPOSE_TOO_OLD"
}
$composeVersion = $null
if (-not [Version]::TryParse($composeVersionText, [ref]$composeVersion) -or $composeVersion -lt [Version]"2.24.4") {
    throw "COMPOSE_TOO_OLD"
}

$stateMarker = Join-Path $stateRoot "autplay-state-v1.txt"
if (Test-Path -LiteralPath $stateRoot) {
    if (
        -not (Test-Path -LiteralPath $stateMarker) -or
        (Get-Content -Raw -LiteralPath $stateMarker).Trim() -ne "AUTPLAY_SERVER_STATE_V1" -or
        -not (Test-PrivateDirectoryAcl $stateRoot)
    ) {
        throw "STATE_DIRECTORY_NOT_PRIVATE"
    }
}
else {
    New-Item -ItemType Directory -Path $stateRoot | Out-Null
    Set-PrivateDirectoryAcl $stateRoot
    [IO.File]::WriteAllText($stateMarker, "AUTPLAY_SERVER_STATE_V1`n", [Text.UTF8Encoding]::new($false))
}
$existingProjectFile = Join-Path $stateRoot "project-name.txt"
if (Test-Path -LiteralPath $existingProjectFile) {
    $existingProjectName = (Get-Content -Raw -LiteralPath $existingProjectFile).Trim()
    if ($existingProjectName -ne $ProjectName) {
        throw "STATE_PROJECT_MISMATCH"
    }
}
$existingEnvironmentFile = Join-Path $stateRoot "server.env"
if (Test-Path -LiteralPath $existingEnvironmentFile) {
    $existingBindLine = Get-Content -LiteralPath $existingEnvironmentFile | Where-Object { $_ -like "AUTPLAY_MOBILE_BIND_HOST=*" } | Select-Object -First 1
    if ($existingBindLine -and $existingBindLine.Substring("AUTPLAY_MOBILE_BIND_HOST=".Length) -ne $BindHost) {
        throw "STATE_ORIGIN_MISMATCH"
    }
    $existingImageLine = Get-Content -LiteralPath $existingEnvironmentFile | Where-Object { $_ -like "AUTPLAY_SERVER_IMAGE=*" } | Select-Object -First 1
    if ($existingImageLine -and $existingImageLine.Substring("AUTPLAY_SERVER_IMAGE=".Length) -ne $imageTag) {
        throw "STATE_VERSION_MISMATCH"
    }
}
$secretRoot = Join-Path $stateRoot "secrets"
if (Test-Path -LiteralPath $secretRoot) {
    if (-not (Test-PrivateDirectoryAcl $secretRoot)) { throw "STATE_DIRECTORY_NOT_PRIVATE" }
}
else {
    New-Item -ItemType Directory -Path $secretRoot | Out-Null
    Set-PrivateDirectoryAcl $secretRoot
}

$authSecret = Join-Path $secretRoot "auth-signing.txt"
$adminSourceSecret = Join-Path $secretRoot "admin-source-hmac.txt"
$adminCsrfSecret = Join-Path $secretRoot "admin-csrf-hmac.txt"
$identityKey = Join-Path $secretRoot "profile-identity-p256.pem"
New-SecretFile $authSecret
New-SecretFile $adminSourceSecret
New-SecretFile $adminCsrfSecret

& docker load --input $archivePath | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "IMAGE_LOAD_FAILED"
}
$loadedImageJson = (& docker image inspect $imageTag | Out-String)
if ($LASTEXITCODE -ne 0 -or -not $loadedImageJson) {
    throw "IMAGE_IDENTITY_MISMATCH"
}
$loadedImage = $loadedImageJson | ConvertFrom-Json
if (
    $loadedImage.Os -ne "linux" -or
    $loadedImage.Architecture -ne "amd64" -or
    $loadedImage.Config.User -ne "autplay:autplay" -or
    $loadedImage.Config.Labels.'org.opencontainers.image.revision' -ne [string]$manifest.source_commit
) {
    throw "IMAGE_IDENTITY_MISMATCH"
}

if (-not (Test-Path -LiteralPath $identityKey)) {
    $identityPem = & docker run --rm --network none --entrypoint python $imageTag -c "from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import ec; print(ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'), end='')"
    if ($LASTEXITCODE -ne 0 -or -not $identityPem) {
        throw "IDENTITY_GENERATION_FAILED"
    }
    [IO.File]::WriteAllText($identityKey, (($identityPem -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
}
$secretValues = @($authSecret, $adminSourceSecret, $adminCsrfSecret) | ForEach-Object {
    (Get-Content -Raw -LiteralPath $_).Trim()
}
if (($secretValues | Where-Object { $_.Length -lt 32 }).Count -gt 0 -or ($secretValues | Sort-Object -Unique).Count -ne 3) {
    throw "SECRET_STATE_INVALID"
}
$identityFingerprint = (& docker run --rm --network none --mount "type=bind,src=$identityKey,dst=/identity.pem,readonly" --entrypoint python $imageTag -c "from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import ec; import hashlib; key=serialization.load_pem_private_key(open('/identity.pem','rb').read(),password=None); assert isinstance(key,ec.EllipticCurvePrivateKey) and isinstance(key.curve,ec.SECP256R1); print(hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest())" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $identityFingerprint -notmatch '^[0-9a-f]{64}$') {
    throw "IDENTITY_STATE_INVALID"
}

function To-ComposePath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).Replace("\", "/")
}

$envFile = Join-Path $stateRoot "server.env"
$environmentLines = @(
    "AUTPLAY_SERVER_IMAGE=$imageTag",
    "AUTPLAY_RELEASE_TAG=$([string]$manifest.release_tag)",
    "AUTPLAY_SOURCE_COMMIT=$([string]$manifest.source_commit)",
    "AUTPLAY_RUNTIME_AUTH_SECRET_FILE=$(To-ComposePath $authSecret)",
    "AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE=$(To-ComposePath $adminSourceSecret)",
    "AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE=$(To-ComposePath $adminCsrfSecret)",
    "AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE=$(To-ComposePath $identityKey)",
    "AUTPLAY_RUNTIME_BIND_HOST=127.0.0.1",
    "AUTPLAY_MOBILE_BIND_HOST=$BindHost",
    "AUTPLAY_API_PORT=8787",
    "AUTPLAY_MOBILE_API_PORT=18787",
    "AUTPLAY_MOBILE_STREAM_PORT=18788",
    "AUTPLAY_PROFILE_LABEL_HINT=AutPlay local server"
)
[IO.File]::WriteAllLines($envFile, $environmentLines, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $stateRoot "project-name.txt"), "$ProjectName`n", [Text.UTF8Encoding]::new($false))

$composeFiles = @(
    "compose.yaml",
    "compose.runtime.yaml",
    "compose.admin-local.yaml",
    "compose.release.yaml"
)
$composeArguments = @("compose", "--project-name", $ProjectName, "--env-file", $envFile)
foreach ($file in $composeFiles) {
    $path = Join-Path $bundleRoot $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "COMPOSE_INPUT_MISSING"
    }
    $composeArguments += @("--file", $path)
}
$composeArguments += @("--profile", "runtime")

& docker @composeArguments config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "COMPOSE_CONFIG_INVALID"
}
if (-not $NoStart) {
    & docker @composeArguments up --no-build --wait
    if ($LASTEXITCODE -ne 0) {
        throw "SERVER_HEALTH_FAILED"
    }
}

Write-Output "AutPlay server installer PASS"
Write-Output "Admin Web is available only on loopback port 8787. Mobile ports: 18787/18788."
Write-Output "Next: run server-control fingerprint and follow INSTALL_AND_PAIR.md."
