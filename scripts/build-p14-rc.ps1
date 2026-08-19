param(
    [Parameter(Mandatory = $true)]
    [string]$JavaHome,
    [Parameter(Mandatory = $true)]
    [string]$AndroidHome,
    [string]$DebugKeystore = (Join-Path $env:USERPROFILE ".android\debug.keystore"),
    [string]$DeviceSerial,
    [switch]$AllowDisposableEmulatorReinstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$zipalign = Join-Path $AndroidHome "build-tools\36.1.0\zipalign.exe"
$apksigner = Join-Path $AndroidHome "build-tools\36.1.0\apksigner.bat"
$adb = Join-Path $AndroidHome "platform-tools\adb.exe"
$unsignedApk = Join-Path $repoRoot "apps\android\build\outputs\apk\release\android-release-unsigned.apk"
$artifactDirectory = Join-Path $repoRoot "docs\release\artifacts"
$alignedApk = Join-Path $artifactDirectory "autplay-rc1-dev-aligned.apk"
$signedApk = Join-Path $artifactDirectory "autplay-rc1-dev-signed.apk"
$evidencePath = Join-Path $repoRoot "docs\implementation\evidence\P14_RELEASE_BUILD.json"

foreach ($requiredPath in @($JavaHome, $AndroidHome, $zipalign, $apksigner, $adb, $DebugKeystore)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required local RC build input is missing: $requiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $artifactDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $evidencePath) | Out-Null

Push-Location $repoRoot
try {
    $env:JAVA_HOME = $JavaHome
    $gradleArguments = @(
        "--no-daemon",
        "--console=plain",
        ":apps:android:lintDebug",
        ":apps:android:testDebugUnitTest",
        ":apps:android:assembleDebug",
        ":apps:android:assembleRelease"
    )
    & .\gradlew.bat @gradleArguments
    if ($LASTEXITCODE -ne 0) { throw "Android RC build gate failed" }

    & docker build --file server/Dockerfile --tag autplay-server:rc1-local .
    if ($LASTEXITCODE -ne 0) { throw "CPU server RC image build failed" }

    if (-not (Test-Path -LiteralPath $unsignedApk)) {
        throw "Unsigned minified release APK is missing after a successful Gradle build"
    }
    & $zipalign -f -p 4 $unsignedApk $alignedApk
    if ($LASTEXITCODE -ne 0) { throw "RC APK zipalign failed" }

    $env:AUTPLAY_P14_DEBUG_KEY_PASSWORD = "android"
    try {
        & $apksigner sign `
            --ks $DebugKeystore `
            --ks-key-alias androiddebugkey `
            --ks-pass env:AUTPLAY_P14_DEBUG_KEY_PASSWORD `
            --key-pass env:AUTPLAY_P14_DEBUG_KEY_PASSWORD `
            --out $signedApk `
            $alignedApk
        if ($LASTEXITCODE -ne 0) { throw "RC APK debug signing failed" }
    }
    finally {
        Remove-Item Env:AUTPLAY_P14_DEBUG_KEY_PASSWORD -ErrorAction SilentlyContinue
    }
    & $apksigner verify --verbose --print-certs $signedApk
    if ($LASTEXITCODE -ne 0) { throw "RC APK signature verification failed" }

    $deviceRows = @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match "\sdevice$" })
    $deviceEvidence = [ordered]@{ status = "UNAVAILABLE" }
    $physicalA55 = $false
    if ($deviceRows.Count -gt 0) {
        if ($DeviceSerial) {
            $matchingRows = @($deviceRows | Where-Object { ($_ -split "\s+")[0] -eq $DeviceSerial })
            if ($matchingRows.Count -ne 1) { throw "Requested Android device is not connected" }
            $serial = $DeviceSerial
        }
        elseif ($deviceRows.Count -eq 1) {
            $serial = ($deviceRows[0] -split "\s+")[0]
        }
        else {
            throw "Multiple Android devices are connected; pass -DeviceSerial explicitly"
        }
        $manufacturer = (& $adb -s $serial shell getprop ro.product.manufacturer | Out-String).Trim()
        $model = (& $adb -s $serial shell getprop ro.product.model | Out-String).Trim()
        $sdk = (& $adb -s $serial shell getprop ro.build.version.sdk | Out-String).Trim()
        $qemu = (& $adb -s $serial shell getprop ro.kernel.qemu | Out-String).Trim()
        $savedErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $installOutput = (& $adb -s $serial install -r -t $signedApk 2>&1 | Out-String).Trim()
            $installExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        $destructiveDisposableReinstall = $false
        if (
            $installExitCode -ne 0 -and
            $installOutput -match "INSTALL_FAILED_UPDATE_INCOMPATIBLE" -and
            $AllowDisposableEmulatorReinstall -and
            $qemu -eq "1"
        ) {
            & $adb -s $serial uninstall app.autplay | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Disposable emulator package removal failed" }
            $destructiveDisposableReinstall = $true
            $savedErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $installOutput = (& $adb -s $serial install -t $signedApk 2>&1 | Out-String).Trim()
                $installExitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $savedErrorActionPreference
            }
        }
        if ($installExitCode -ne 0) {
            throw "Dev-signed RC APK installation failed: $installOutput"
        }
        & $adb -s $serial shell am start -W -n app.autplay/.MainActivity | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Dev-signed RC APK first launch failed" }
        $firstPid = (& $adb -s $serial shell pidof app.autplay | Out-String).Trim()
        if (-not $firstPid) { throw "Dev-signed RC process did not start" }
        & $adb -s $serial shell am start -W -a android.intent.action.MAIN -c android.intent.category.HOME | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Dev-signed RC background transition failed" }
        $activityState = (& $adb -s $serial shell dumpsys activity activities | Out-String)
        if ($activityState -match "mResumedActivity[^`r`n]*app\.autplay") {
            throw "Dev-signed RC remained resumed after background transition"
        }
        $packageState = (& $adb -s $serial shell dumpsys package app.autplay | Out-String)
        if ($packageState -match "android\.permission\.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS") {
            throw "Dev-signed RC requests a battery-optimization bypass"
        }
        & $adb -s $serial shell am force-stop app.autplay
        if ($LASTEXITCODE -ne 0) { throw "Dev-signed RC APK force-stop failed" }
        $savedErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $stoppedPid = (& $adb -s $serial shell pidof app.autplay 2>$null | Out-String).Trim()
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        if ($stoppedPid) { throw "Dev-signed RC process survived force-stop unexpectedly" }
        & $adb -s $serial shell am start -W -n app.autplay/.MainActivity | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Dev-signed RC APK restart launch failed" }
        $restartedPid = (& $adb -s $serial shell pidof app.autplay | Out-String).Trim()
        if (-not $restartedPid) { throw "Dev-signed RC process did not restart after force-stop" }
        $physicalA55 = (
            $manufacturer -ieq "samsung" -and
            $model -match "(?i)a55" -and
            $qemu -ne "1"
        )
        $serialHasher = [Security.Cryptography.SHA256]::Create()
        try {
            $serialHash = [BitConverter]::ToString(
                $serialHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($serial))
            ).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $serialHasher.Dispose()
        }
        $deviceEvidence = [ordered]@{
            status = $(if ($physicalA55) { "PASS" } else { "NON_A55_DEVICE_PASS" })
            serial_sha256 = $serialHash
            manufacturer = $manufacturer
            model = $model
            sdk = $sdk
            emulator = ($qemu -eq "1")
            install = "PASS"
            process_restart = "PASS"
            background_transition = "PASS"
            battery_optimization_bypass_requested = $false
            process_death_observed = "PASS"
            physical_samsung_a55 = $physicalA55
            disposable_emulator_package_reinstalled = $destructiveDisposableReinstall
        }
    }

    $imageId = (& docker image inspect autplay-server:rc1-local --format "{{.Id}}" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $imageId -notmatch "^sha256:[a-f0-9]{64}$") {
        throw "Local CPU RC image digest is unavailable"
    }
    $report = [ordered]@{
        schema_version = 1
        generated_at = [DateTimeOffset]::UtcNow.ToString("o")
        status = $(if ($physicalA55) { "PASS" } else { "PASS_WITH_A55_PENDING" })
        android = [ordered]@{
            unsigned_release_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $unsignedApk).Hash.ToLowerInvariant()
            dev_signed_rc_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $signedApk).Hash.ToLowerInvariant()
            signature = "Android debug development key; not a production key"
            lint_unit_r8_release = "PASS"
        }
        cpu_container = [ordered]@{
            tag = "autplay-server:rc1-local"
            image_id = $imageId
            pushed = $false
        }
        device = $deviceEvidence
        external_actions = [ordered]@{
            push = $false
            publication = $false
            deployment = $false
            production_signing = $false
        }
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $evidencePath -Encoding utf8
    Write-Output "P14 local RC build PASS: image=$imageId device=$($deviceEvidence.status)"
}
finally {
    Pop-Location
}
