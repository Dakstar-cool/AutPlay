param()

$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($env:JAVA_HOME)) {
    throw 'JAVA_HOME must select the pinned Microsoft OpenJDK 17.0.20+8-LTS'
}
$taskJava = Join-Path $env:JAVA_HOME 'bin/java.exe'
$taskJavaVersion = (& $taskJava -version 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $taskJavaVersion -notmatch '17\.0\.20' -or $taskJavaVersion -notmatch 'Microsoft') {
    throw 'Desktop gate requires the pinned Microsoft OpenJDK 17.0.20+8-LTS'
}
Push-Location $taskProjectRoot
try {
    & .\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
        --max-workers=1 --dependency-verification=strict -Pautplay.desktopOnly=true `
        :shared:domain:jvmTest :shared:application:jvmTest :shared:ui:jvmTest `
        :apps:desktop:classes :apps:desktop:jar
    if ($LASTEXITCODE -ne 0) { throw 'Shared/desktop compilation and policy gate failed' }
} finally {
    Pop-Location
}
