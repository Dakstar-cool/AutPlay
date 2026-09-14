param(
    [string]$AndroidHome = $env:ANDROID_HOME,
    [string]$DeviceSerial = "emulator-5554",
    [string]$JavaHome = $env:JAVA_HOME,
    [switch]$QaSideBySide
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$originalAndroidHome = $env:ANDROID_HOME
$originalJavaHome = $env:JAVA_HOME
try {
    $env:ANDROID_HOME = $AndroidHome
    $env:JAVA_HOME = $JavaHome
    $arguments = @("--no-daemon", "--console=plain", "--max-workers=1",
        ":apps:android:assembleDebug", ":apps:android:assembleDebugAndroidTest")
    if ($QaSideBySide) { $arguments += "-Pautplay.qaSideBySide=true" }
    & (Join-Path $repoRoot "gradlew.bat") @arguments
    if ($LASTEXITCODE -ne 0) { throw "Process-death fixture build failed" }
    $runnerArguments = @((Join-Path $PSScriptRoot "test_l1_process_death.py"), "--serial", $DeviceSerial)
    if ($QaSideBySide) { $runnerArguments += "--qa-side-by-side" }
    & python @runnerArguments
    if ($LASTEXITCODE -ne 0) { throw "Process-death fixture failed; inspect build/l1-process-death" }
}
finally {
    $env:ANDROID_HOME = $originalAndroidHome
    $env:JAVA_HOME = $originalJavaHome
}
