param(
    [switch]$ServerOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = "deploy/compose/compose.yaml"
$composeTestFile = "deploy/compose/compose.test.yaml"
$composeRuntimeFile = "deploy/compose/compose.runtime.yaml"
$composeProject = "autplay-p06-$PID"
$composeTouched = $false
$previousTestDatabaseUrl = [Environment]::GetEnvironmentVariable("AUTPLAY_TEST_DATABASE_URL")
$previousRuntimeSecretFile = [Environment]::GetEnvironmentVariable("AUTPLAY_RUNTIME_AUTH_SECRET_FILE")
$previousPublicAccessSourceSecretFile = [Environment]::GetEnvironmentVariable(
    "AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE"
)

Push-Location $repoRoot

try {
    & "$PSScriptRoot\bootstrap.ps1" -ServerOnly:$ServerOnly

    & uv lock --check
    if ($LASTEXITCODE -ne 0) { throw "AutPlay contract tooling uv lock freshness check failed" }

    & uv run --frozen ruff check tests/contract tests/release
    if ($LASTEXITCODE -ne 0) { throw "Root test Ruff lint failed" }

    & uv run --frozen ruff format --check tests/contract tests/release
    if ($LASTEXITCODE -ne 0) { throw "Root test Ruff format check failed" }

    & uv run --frozen mypy tests/contract tests/release
    if ($LASTEXITCODE -ne 0) { throw "Root test mypy failed" }

    & uv run --frozen pytest tests/contract tests/release
    if ($LASTEXITCODE -ne 0) { throw "Root contract/release validation failed" }

    & uv lock --project server --check
    if ($LASTEXITCODE -ne 0) { throw "uv lock freshness check failed" }

    & uv run --project server --frozen python -c "import autplay"
    if ($LASTEXITCODE -ne 0) { throw "AutPlay package import failed" }

    & uv run --project server --frozen ruff check --config server/pyproject.toml server
    if ($LASTEXITCODE -ne 0) { throw "Ruff lint failed" }

    & uv run --project server --frozen ruff format --check --config server/pyproject.toml server
    if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed" }

    & uv run --project server --frozen mypy --config-file server/pyproject.toml server/src server/tests
    if ($LASTEXITCODE -ne 0) { throw "mypy failed" }

    $dependencyTreeJson = (& uv tree --project server --frozen --universal --format json --preview-features json-output | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "uv dependency tree audit failed" }
    $dependencyTree = $dependencyTreeJson | ConvertFrom-Json
    $dependencyNames = @(
        $dependencyTree.resolution.PSObject.Properties.Value | ForEach-Object {
            if ($null -ne $_.PSObject.Properties["name"]) { $_.name }
        }
    )
    $prohibitedPattern = '^(cupy|jax|jaxlib|tensorflow|torch|torchvision|torchaudio|nvidia($|-)|cuda($|-)|onnxruntime($|-)|transformers$|scikit-learn$)'
    $prohibitedPackages = @($dependencyNames | Where-Object { $_ -match $prohibitedPattern } | Sort-Object -Unique)
    if ($prohibitedPackages.Count -gt 0) {
        throw "CPU dependency graph contains prohibited GPU or ML packages: $($prohibitedPackages -join ', ')"
    }

    [Environment]::SetEnvironmentVariable(
        "AUTPLAY_RUNTIME_AUTH_SECRET_FILE",
        (Join-Path $repoRoot "server\pyproject.toml")
    )
    [Environment]::SetEnvironmentVariable(
        "AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE",
        (Join-Path $repoRoot "pyproject.toml")
    )
    & docker compose -p $composeProject -f $composeFile -f $composeRuntimeFile --profile runtime config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Runtime Docker Compose configuration validation failed" }

    if (-not $ServerOnly) {
        $gradleArguments = @(
            "--no-daemon",
            "--console=plain",
            "lintDebug",
            "testDebugUnitTest",
            "assembleDebug",
            "assembleRelease"
        )
        & .\gradlew.bat @gradleArguments
        if ($LASTEXITCODE -ne 0) { throw "Android lint/unit/debug/release-R8 gate failed" }
    }

    $existingContainers = (& docker ps -a --filter "label=com.docker.compose.project=$composeProject" --format "{{.ID}}" | Out-String).Trim()
    $existingVolumes = (& docker volume ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
    $existingNetworks = (& docker network ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
    if ($existingContainers -or $existingVolumes -or $existingNetworks) {
        throw "Refusing to reuse non-empty disposable Compose project $composeProject"
    }

    & docker compose -p $composeProject -f $composeFile -f $composeTestFile config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration validation failed" }

    $composeTouched = $true
    & docker compose -p $composeProject -f $composeFile -f $composeTestFile up --detach --wait
    if ($LASTEXITCODE -ne 0) { throw "Disposable PostgreSQL service did not become healthy" }

    $databaseReady = $false
    $databaseVersions = ""
    $lastDatabaseError = "PostgreSQL final server did not become ready"
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $postgresLogs = (& docker compose -p $composeProject -f $composeFile -f $composeTestFile logs --no-color postgres 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and $postgresLogs.Contains("PostgreSQL init process complete; ready for start up.")) {
            $extensionOutput = (& docker compose -p $composeProject -f $composeFile -f $composeTestFile exec -T postgres psql -U autplay -d autplay -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0) {
                $databaseVersions = (& docker compose -p $composeProject -f $composeFile -f $composeTestFile exec -T postgres psql -U autplay -d autplay -tA -v ON_ERROR_STOP=1 -c "SELECT current_setting('server_version') || '|' || extversion FROM pg_extension WHERE extname = 'vector';" 2>&1 | Out-String).Trim()
                if ($LASTEXITCODE -eq 0 -and $databaseVersions -match "^18\.4.*\|0\.8\.6$") {
                    $databaseReady = $true
                    break
                }
                $lastDatabaseError = "Unexpected PostgreSQL/pgvector versions: $databaseVersions"
            }
            else {
                $lastDatabaseError = "pgvector extension creation failed: $extensionOutput"
            }
        }
        if ($attempt -lt 30) { Start-Sleep -Seconds 1 }
    }
    if (-not $databaseReady) {
        throw $lastDatabaseError
    }
    Write-Output "PostgreSQL|pgvector=$databaseVersions"

    $publishedEndpoint = (& docker compose -p $composeProject -f $composeFile -f $composeTestFile port postgres 5432 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $publishedEndpoint -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "PostgreSQL test port is not a dynamic loopback endpoint"
    }
    $publishedPort = [int]$Matches[1]
    if ($publishedPort -lt 1 -or $publishedPort -gt 65535) {
        throw "PostgreSQL test port is outside the valid range"
    }
    [Environment]::SetEnvironmentVariable(
        "AUTPLAY_TEST_DATABASE_URL",
        "postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:$publishedPort/autplay"
    )

    & uv run --project server --frozen pytest -c server/pyproject.toml server/tests
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}
finally {
    [Environment]::SetEnvironmentVariable("AUTPLAY_TEST_DATABASE_URL", $previousTestDatabaseUrl)
    [Environment]::SetEnvironmentVariable("AUTPLAY_RUNTIME_AUTH_SECRET_FILE", $previousRuntimeSecretFile)
    [Environment]::SetEnvironmentVariable(
        "AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE",
        $previousPublicAccessSourceSecretFile
    )
    if ($composeTouched) {
        & docker compose -p $composeProject -f $composeFile -f $composeTestFile down --volumes --remove-orphans
        if ($LASTEXITCODE -ne 0) { throw "Disposable Compose cleanup failed" }
    }
    $remainingContainers = (& docker ps -a --filter "label=com.docker.compose.project=$composeProject" --format "{{.ID}}" | Out-String).Trim()
    $remainingVolumes = (& docker volume ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
    $remainingNetworks = (& docker network ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
    if ($remainingContainers -or $remainingVolumes -or $remainingNetworks) {
        throw "Disposable Compose resources remain after cleanup"
    }
    Pop-Location
}
