param(
    [switch]$ServerOnly,
    [string]$PythonEnvironmentRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    $resolvedEnvironmentRoot = $null
    if ($PythonEnvironmentRoot) {
        $candidateEnvironmentRoot = if ([IO.Path]::IsPathRooted($PythonEnvironmentRoot)) {
            $PythonEnvironmentRoot
        }
        else {
            Join-Path $repoRoot $PythonEnvironmentRoot
        }
        $resolvedEnvironmentRoot = [IO.Path]::GetFullPath($candidateEnvironmentRoot)
        New-Item -ItemType Directory -Force -Path $resolvedEnvironmentRoot | Out-Null
    }

    function Sync-UvProject {
        param(
            [AllowEmptyString()][string]$Project,
            [string]$EnvironmentName,
            [string]$Label
        )

        $savedProjectEnvironment = [Environment]::GetEnvironmentVariable(
            "UV_PROJECT_ENVIRONMENT",
            "Process"
        )
        try {
            if ($resolvedEnvironmentRoot) {
                $env:UV_PROJECT_ENVIRONMENT = Join-Path $resolvedEnvironmentRoot $EnvironmentName
            }
            else {
                Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
                $projectRoot = if ($Project) { Join-Path $repoRoot $Project } else { $repoRoot }
                $defaultEnvironment = Join-Path $projectRoot ".venv"
                $environmentItem = Get-Item -Force -LiteralPath $defaultEnvironment -ErrorAction SilentlyContinue
                if (
                    $null -ne $environmentItem -and
                    ($environmentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                    -not (Test-Path -LiteralPath $defaultEnvironment)
                ) {
                    throw "$Label has a broken .venv reparse point; use -PythonEnvironmentRoot to recreate environments without junctions"
                }
            }

            $projectArguments = if ($Project) { @("--project", $Project) } else { @() }
            & uv sync @projectArguments --frozen --python 3.14.7
            if ($LASTEXITCODE -ne 0) {
                throw "$Label uv sync failed"
            }
            $pythonVersion = (
                & uv run @projectArguments --frozen python -c "import platform; print(platform.python_version())"
            ).Trim()
            if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.14.7") {
                throw "$Label requires CPython 3.14.7; observed: $pythonVersion"
            }
        }
        finally {
            if ($null -eq $savedProjectEnvironment) {
                Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
            }
            else {
                $env:UV_PROJECT_ENVIRONMENT = $savedProjectEnvironment
            }
        }
    }

    $uvVersion = (& uv --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $uvVersion -notmatch "^uv 0\.12\.3(?: |$)") {
        throw "AutPlay requires uv 0.12.3; observed: $uvVersion"
    }

    & uv python install 3.14.7
    if ($LASTEXITCODE -ne 0) {
        throw "uv python install failed"
    }

    Sync-UvProject -Project "" -EnvironmentName "root" -Label "AutPlay contract tooling"
    Sync-UvProject -Project "server" -EnvironmentName "server" -Label "AutPlay server"

    if (-not $ServerOnly) {
        Sync-UvProject -Project "gpu" -EnvironmentName "gpu" -Label "AutPlay GPU worker"
        Sync-UvProject -Project "gpu/training" -EnvironmentName "training" -Label "AutPlay Sona training"
        Sync-UvProject -Project "tools/local_music_acquisition" -EnvironmentName "acquisition" -Label "AutPlay acquisition"

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

        $gradleArguments = @("--no-daemon", "--version")
        $gradleVersion = (& .\gradlew.bat @gradleArguments | Out-String)
        $normalizedJavaHome = $env:JAVA_HOME.TrimEnd([char[]]@(92, 47))
        $escapedJavaHome = [Regex]::Escape($normalizedJavaHome)
        if (
            $LASTEXITCODE -ne 0 -or
            $gradleVersion -notmatch '(?m)^Gradle 9\.3\.1\r?$' -or
            $gradleVersion -notmatch '(?m)^Launcher JVM:\s+17\.0\.20 \(Microsoft 17\.0\.20\+8-LTS\)\r?$' -or
            $gradleVersion -notmatch "(?m)^Daemon JVM:\s+$escapedJavaHome \(no Daemon JVM specified, using current Java home\)\r?$"
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
