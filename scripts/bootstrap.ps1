param(
    [switch]$ServerOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    $uvVersion = (& uv --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $uvVersion -notmatch "^uv 0\.12\.3(?: |$)") {
        throw "AutPlay requires uv 0.12.3; observed: $uvVersion"
    }

    & uv python install 3.14.7
    if ($LASTEXITCODE -ne 0) {
        throw "uv python install failed"
    }

    & uv sync --project server --frozen --python 3.14.7
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed"
    }

    $pythonVersion = (& uv run --project server --frozen python -c "import platform; print(platform.python_version())").Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.14.7") {
        throw "AutPlay server environment requires CPython 3.14.7; observed: $pythonVersion"
    }

    if (-not $ServerOnly) {
        if (-not $env:JAVA_HOME) {
            throw "JAVA_HOME must point to the pinned JDK 17"
        }
        if (-not $env:ANDROID_HOME) {
            throw "ANDROID_HOME must point to an SDK with platform 36.1 and Build Tools 36.1.0"
        }

        $javaExecutable = Join-Path $env:JAVA_HOME "bin\java.exe"
        if (-not (Test-Path -LiteralPath $javaExecutable)) {
            throw "JAVA_HOME does not contain bin\java.exe"
        }
        $savedErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $javaVersion = (& $javaExecutable -version 2>&1 | Out-String)
        $javaExitCode = $LASTEXITCODE
        $ErrorActionPreference = $savedErrorActionPreference
        if ($javaExitCode -ne 0 -or $javaVersion -notmatch 'openjdk version "17\.0\.20"' -or $javaVersion -notmatch 'Microsoft-\d+ \(build 17\.0\.20\+8-LTS\)') {
            throw "AutPlay requires Microsoft OpenJDK 17.0.20+8-LTS"
        }

        $androidPlatform = Join-Path $env:ANDROID_HOME "platforms\android-36.1\android.jar"
        $androidBuildTool = Join-Path $env:ANDROID_HOME "build-tools\36.1.0\aapt2.exe"
        if (-not (Test-Path -LiteralPath $androidPlatform) -or -not (Test-Path -LiteralPath $androidBuildTool)) {
            throw "ANDROID_HOME lacks platform 36.1 or Build Tools 36.1.0"
        }

        $gradleJavaHomeArgument = "-Dorg.gradle.java.home=$env:JAVA_HOME"
        $gradleVersion = (& .\gradlew.bat $gradleJavaHomeArgument --no-daemon --version | Out-String)
        if (
            $LASTEXITCODE -ne 0 -or
            $gradleVersion -notmatch '(?m)^Gradle 9\.3\.1\r?$' -or
            $gradleVersion -notmatch '(?m)^Launcher JVM:\s+17\.0\.20 \(Microsoft 17\.0\.20\+8-LTS\)\r?$' -or
            $gradleVersion -notmatch '(?m)^Daemon JVM:.*\(from org\.gradle\.java\.home\)\r?$'
        ) {
            throw "Gradle wrapper or pinned JDK resolution failed"
        }

        & docker compose -f deploy/compose/compose.yaml config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose configuration validation failed"
        }
    }
}
finally {
    Pop-Location
}
