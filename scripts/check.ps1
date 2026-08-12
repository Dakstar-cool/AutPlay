param(
    [switch]$ServerOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = "deploy/compose/compose.yaml"
$composeProject = "autplay-p01-smoke"

Push-Location $repoRoot

try {
    & "$PSScriptRoot\bootstrap.ps1" -ServerOnly:$ServerOnly

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

    & uv run --project server --frozen pytest -c server/pyproject.toml server/tests
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

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

    if (-not $ServerOnly) {
        $gradleJavaHomeArgument = "-Dorg.gradle.java.home=$env:JAVA_HOME"
        & .\gradlew.bat $gradleJavaHomeArgument --no-daemon --console=plain lintDebug testDebugUnitTest assembleDebug
        if ($LASTEXITCODE -ne 0) { throw "Android lint/unit/build smoke failed" }

        $existingContainers = (& docker ps -a --filter "label=com.docker.compose.project=$composeProject" --format "{{.ID}}" | Out-String).Trim()
        $existingVolumes = (& docker volume ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
        $existingNetworks = (& docker network ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
        if ($existingContainers -or $existingVolumes -or $existingNetworks) {
            throw "Refusing to reuse non-empty disposable Compose project $composeProject"
        }

        $composeTouched = $false
        try {
            & docker compose -p $composeProject -f $composeFile config --quiet
            if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration validation failed" }

            $composeTouched = $true
            & docker compose -p $composeProject -f $composeFile up --detach --wait
            if ($LASTEXITCODE -ne 0) { throw "Disposable PostgreSQL service did not become healthy" }

            & docker compose -p $composeProject -f $composeFile exec -T postgres psql -U autplay -d autplay -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
            if ($LASTEXITCODE -ne 0) { throw "pgvector extension creation failed" }

            $databaseVersions = (& docker compose -p $composeProject -f $composeFile exec -T postgres psql -U autplay -d autplay -tA -v ON_ERROR_STOP=1 -c "SELECT current_setting('server_version') || '|' || extversion FROM pg_extension WHERE extname = 'vector';" | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) { throw "PostgreSQL version query failed" }
            if ($databaseVersions -notmatch "^18\.4.*\|0\.8\.6$") {
                throw "Unexpected PostgreSQL/pgvector versions: $databaseVersions"
            }
            Write-Output "PostgreSQL|pgvector=$databaseVersions"
        }
        finally {
            if ($composeTouched) {
                & docker compose -p $composeProject -f $composeFile down --volumes --remove-orphans
                if ($LASTEXITCODE -ne 0) { throw "Disposable Compose cleanup failed" }
            }
        }

        $remainingContainers = (& docker ps -a --filter "label=com.docker.compose.project=$composeProject" --format "{{.ID}}" | Out-String).Trim()
        $remainingVolumes = (& docker volume ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
        $remainingNetworks = (& docker network ls --filter "label=com.docker.compose.project=$composeProject" --format "{{.Name}}" | Out-String).Trim()
        if ($remainingContainers -or $remainingVolumes -or $remainingNetworks) {
            throw "Disposable Compose resources remain after cleanup"
        }
    }
}
finally {
    Pop-Location
}
