param(
    [string]$AndroidHome = $env:ANDROID_HOME,
    [string]$DeviceSerial,
    [string]$JavaHome = $env:JAVA_HOME,
    [switch]$QaSideBySide
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$gradle = Join-Path $repoRoot "gradlew.bat"
if ([string]::IsNullOrWhiteSpace($AndroidHome)) { throw "AndroidHome is required" }
$adb = Join-Path $AndroidHome "platform-tools\adb.exe"
foreach ($requiredPath in @($gradle, $adb)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required L1 process-death input is missing: $requiredPath"
    }
}

$deviceRows = @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match "\sdevice$" })
if ($DeviceSerial) {
    $matchingRows = @($deviceRows | Where-Object { ($_ -split "\s+")[0] -eq $DeviceSerial })
    if ($matchingRows.Count -ne 1) { throw "Requested Android device is not connected and authorized" }
    $serial = $DeviceSerial
}
elseif ($deviceRows.Count -eq 1) {
    $serial = ($deviceRows[0] -split "\s+")[0]
}
else {
    throw "Exactly one connected and authorized Android device is required"
}

$originalAndroidSerial = $env:ANDROID_SERIAL
$originalJavaHome = $env:JAVA_HOME
$env:ANDROID_SERIAL = $serial
if (-not [string]::IsNullOrWhiteSpace($JavaHome)) { $env:JAVA_HOME = $JavaHome }

$testClass = "app.autplay.playback.PlaybackServiceProcessStageTest"
$applicationId = if ($QaSideBySide) { "app.autplay.qa" } else { "app.autplay" }
function Invoke-ProcessStage([string]$MethodName) {
    $filter = "-Pandroid.testInstrumentationRunnerArguments.class=$testClass#$MethodName"
    $arguments = @(
        "--no-daemon", "--console=plain", "--max-workers=1",
        ":apps:android:connectedDebugAndroidTest", $filter,
        "-Pandroid.testInstrumentationRunnerArguments.l1ProcessStage=true"
    )
    if ($QaSideBySide) { $arguments += "-Pautplay.qaSideBySide=true" }
    & $gradle @arguments
    if ($LASTEXITCODE -ne 0) { throw "L1 process-death stage failed: $MethodName" }
}

try {
    Invoke-ProcessStage "stage1_seedServiceAndWaitForPeriodicCheckpoint"
    $component = "$applicationId/app.autplay.MainActivity"
    & $adb -s $serial shell am start -W -n $component | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "AutPlay process restart failed after L1 stage1" }
    $runningPid = (& $adb -s $serial shell pidof $applicationId | Out-String).Trim()
    if (-not $runningPid) { throw "AutPlay process was not alive after L1 stage1" }

    & $adb -s $serial shell am force-stop $applicationId | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "AutPlay force-stop failed between L1 stages" }
    Start-Sleep -Milliseconds 500
    $stoppedPid = (& $adb -s $serial shell pidof $applicationId 2>$null | Out-String).Trim()
    if ($stoppedPid) { throw "AutPlay process survived the L1 force-stop boundary" }

    Invoke-ProcessStage "stage2_verifyServiceRestoresPersistedQueueAfterFreshConnection"
    Write-Output "L1 process-death queue restoration: PASS"
}
finally {
    $env:ANDROID_SERIAL = $originalAndroidSerial
    $env:JAVA_HOME = $originalJavaHome
}
