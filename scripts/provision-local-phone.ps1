param(
    [Parameter(Mandatory = $true)]
    [string]$JavaHome,
    [Parameter(Mandatory = $true)]
    [string]$AndroidHome,
    [Parameter(Mandatory = $true)]
    [string]$ServerBaseUrl,
    [string]$StreamBaseUrl,
    [string]$SessionFile = (Join-Path $env:LOCALAPPDATA "AutPlay\dev\phone-owner-session.json"),
    [string]$DeviceSerial,
    [ValidateSet("app.autplay", "app.autplay.qa")]
    [string]$ApplicationId = "app.autplay",
    [string]$JournalEpoch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$adb = Join-Path $AndroidHome "platform-tools\adb.exe"
$apksigner = Join-Path $AndroidHome "build-tools\36.1.0\apksigner.bat"
$applicationApk = Join-Path $repoRoot "apps\android\build\outputs\apk\debug\android-debug.apk"
$testApk = Join-Path $repoRoot "apps\android\build\outputs\apk\androidTest\debug\android-debug-androidTest.apk"
$provisioningFileName = "local-server-provisioning.json"

foreach ($requiredPath in @($JavaHome, $AndroidHome, $adb, $apksigner, $applicationApk, $testApk, $SessionFile)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required local provisioning input is missing: $requiredPath"
    }
}

if ([string]::IsNullOrWhiteSpace($StreamBaseUrl)) { $StreamBaseUrl = $ServerBaseUrl }
foreach ($serviceOrigin in @($ServerBaseUrl, $StreamBaseUrl)) {
    $uri = [Uri]$serviceOrigin
    if ($uri.Scheme -ne "http" -or -not $uri.IsAbsoluteUri -or $uri.Port -lt 1 -or $uri.AbsolutePath -ne "/") {
        throw "Local provisioning requires absolute HTTP service origins with explicit ports"
    }
    $address = [Net.IPAddress]::Parse($uri.Host)
    $bytes = $address.GetAddressBytes()
    $privateAddress = $address.Equals([Net.IPAddress]::Loopback) -or
        $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
    if (-not $privateAddress) { throw "Local provisioning host is not a private IPv4 address" }
}

$deviceRows = @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match "\sdevice$" })
if ($DeviceSerial) {
    $matchingRows = @($deviceRows | Where-Object { ($_ -split "\s+")[0] -eq $DeviceSerial })
    if ($matchingRows.Count -ne 1) { throw "Requested Android device is not connected" }
    $serial = $DeviceSerial
}
elseif ($deviceRows.Count -eq 1) {
    $serial = ($deviceRows[0] -split "\s+")[0]
}
else {
    throw "Exactly one authorized Android device is required"
}

function Get-ApkCertificate([string]$Path) {
    $line = (& $apksigner verify --print-certs $Path | Select-String "Signer #1 certificate SHA-256 digest:" | Select-Object -First 1).Line
    if (-not $line) { throw "APK certificate inspection failed" }
    return ($line -replace ".*digest:\s*", "").Trim()
}

$testApplicationId = "$ApplicationId.test"
$installedPath = ((& $adb -s $serial shell pm path $ApplicationId | Out-String).Trim() -replace "^package:", "")
if (-not $installedPath) { throw "AutPlay is not installed on the selected device" }
$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) "autplay-phone-provisioning"
New-Item -ItemType Directory -Force -Path $temporaryDirectory | Out-Null
$installedApk = Join-Path $temporaryDirectory "installed-base.apk"
& $adb -s $serial pull $installedPath $installedApk 1>$null
if ($LASTEXITCODE -ne 0) { throw "Installed APK inspection failed" }
$installedCertificate = Get-ApkCertificate $installedApk
if ($installedCertificate -ne (Get-ApkCertificate $applicationApk) -or
    $installedCertificate -ne (Get-ApkCertificate $testApk)) {
    throw "Refusing a non-matching APK signature update"
}

$session = Get-Content -LiteralPath $SessionFile -Raw | ConvertFrom-Json
foreach ($name in @("access_token", "refresh_token", "user_id", "device_id")) {
    if (-not $session.$name) { throw "Owner session file is missing $name" }
}
if (-not $session.access_expires_at) {
    throw "Owner session access token is stale; provide a freshly issued local admin session. The provisioning script never replays a copied refresh credential."
}
$accessExpiresAt = if ($session.access_expires_at -is [DateTime]) {
    [DateTimeOffset]$session.access_expires_at
}
else {
    [DateTimeOffset]::Parse([string]$session.access_expires_at, [Globalization.CultureInfo]::InvariantCulture)
}
if ($accessExpiresAt -le [DateTimeOffset]::UtcNow.AddMinutes(2)) {
    throw "Owner session access token is stale; provide a freshly issued local admin session. The provisioning script never replays a copied refresh credential."
}
$provisioning = [ordered]@{
    autplayProvisioningBaseUrl = $ServerBaseUrl.TrimEnd("/")
    autplayProvisioningStreamBaseUrl = $StreamBaseUrl.TrimEnd("/")
    autplayProvisioningUserId = $session.user_id
    autplayProvisioningDeviceId = $session.device_id
    autplayProvisioningProfileId = [guid]::NewGuid().ToString()
    autplayProvisioningJournalEpoch = if ($JournalEpoch) { ([guid]$JournalEpoch).ToString() } else { [guid]::NewGuid().ToString() }
    autplayProvisioningLineageId = [guid]::NewGuid().ToString()
    autplayProvisioningAccessToken = $session.access_token
    autplayProvisioningRefreshToken = $session.refresh_token
}
$provisioningJson = $provisioning | ConvertTo-Json -Compress

$beforeInstall = ((& $adb -s $serial shell dumpsys package $ApplicationId | Select-String "firstInstallTime=" | Select-Object -First 1).Line -replace ".*firstInstallTime=", "").Trim()
& $adb -s $serial install -r -t $applicationApk 1>$null
if ($LASTEXITCODE -ne 0) { throw "Non-destructive application APK update failed" }
& $adb -s $serial install -r -t $testApk 1>$null
if ($LASTEXITCODE -ne 0) { throw "Test APK update failed" }
$afterInstall = ((& $adb -s $serial shell dumpsys package $ApplicationId | Select-String "firstInstallTime=" | Select-Object -First 1).Line -replace ".*firstInstallTime=", "").Trim()
if ($beforeInstall -ne $afterInstall) { throw "Application first-install identity changed unexpectedly" }

$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $adb
$appPrivateDirectory = "/data/user/0/$ApplicationId/files"
$appPrivateProvisioning = "$appPrivateDirectory/$provisioningFileName"
& $adb -s $serial shell run-as $ApplicationId mkdir -p $appPrivateDirectory
if ($LASTEXITCODE -ne 0) { throw "App-private files directory is unavailable" }
$startInfo.Arguments = "-s `"$serial`" shell run-as $ApplicationId dd of=$appPrivateProvisioning status=none"
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$process = [Diagnostics.Process]::new()
$process.StartInfo = $startInfo
if (-not $process.Start()) { throw "App-private provisioning transfer could not start" }
$process.StandardInput.Write($provisioningJson)
$process.StandardInput.Close()
$process.WaitForExit()
$transferError = $process.StandardError.ReadToEnd().Trim()
if ($process.ExitCode -ne 0) { throw "App-private provisioning transfer failed: $transferError" }
& $adb -s $serial shell run-as $ApplicationId chmod 600 $appPrivateProvisioning
if ($LASTEXITCODE -ne 0) { throw "App-private provisioning permission hardening failed" }
$provisioningJson = $null
$provisioning = $null
$session = $null

$instrumentation = (& $adb -s $serial shell am instrument -w -r `
    -e class app.autplay.LocalServerProvisioningTest `
    -e autplayProvisioningEnabled true `
    $testApplicationId/androidx.test.runner.AndroidJUnitRunner 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $instrumentation -notmatch "OK \(1 test\)") {
    throw "Local server provisioning instrumentation failed: $instrumentation"
}

& $adb -s $serial shell run-as $ApplicationId test ! -e $appPrivateProvisioning
if ($LASTEXITCODE -ne 0) { throw "Provisioning input was not removed from app-private storage" }
& $adb -s $serial shell am force-stop $ApplicationId 1>$null
& $adb -s $serial shell am start -n $ApplicationId/app.autplay.MainActivity 1>$null
if ($LASTEXITCODE -ne 0) { throw "AutPlay did not start after provisioning" }

[pscustomobject]@{
    status = "PASS"
    first_install_preserved = $true
    app_private_input_removed = $true
    app_started = $true
} | ConvertTo-Json
