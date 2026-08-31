param(
    [ValidatePattern('^(auto|uuid:GPU-[A-Za-z0-9-]{8,100}|pci:(?:(?:[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}):)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]|index:[0-9]{1,3})$')]
    [string]$DeviceSelector = "auto"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$previousSelector = [Environment]::GetEnvironmentVariable("AUTPLAY_GPU_DEVICE_SELECTOR")

Push-Location $repoRoot
try {
    & uv lock --project gpu --check
    if ($LASTEXITCODE -ne 0) { throw "GPU uv lock freshness check failed" }

    & uv run --project gpu --frozen ruff check gpu/src gpu/tests
    if ($LASTEXITCODE -ne 0) { throw "GPU Ruff lint failed" }

    & uv run --project gpu --frozen ruff format --check gpu/src gpu/tests
    if ($LASTEXITCODE -ne 0) { throw "GPU Ruff format failed" }

    & uv run --project gpu --frozen mypy gpu/src gpu/tests
    if ($LASTEXITCODE -ne 0) { throw "GPU mypy failed" }

    & uv run --project gpu --frozen pytest gpu/tests
    if ($LASTEXITCODE -ne 0) { throw "GPU pytest failed" }

    [Environment]::SetEnvironmentVariable("AUTPLAY_GPU_DEVICE_SELECTOR", $DeviceSelector)
    & uv run --project gpu --frozen autplay-ml-gpu --list-devices
    if ($LASTEXITCODE -ne 0) {
        throw "No compatible NVIDIA accelerator is visible to the isolated GPU project"
    }

    & uv run --project gpu --frozen autplay-ml-gpu --select-device
    if ($LASTEXITCODE -ne 0) { throw "Configured GPU selector did not match a compatible device" }

    & uv run --project gpu --frozen autplay-ml-gpu --check-config
    if ($LASTEXITCODE -ne 0) { throw "GPU worker configuration check failed" }

    Write-Output "P12 GPU static/device gate passed for selector=$DeviceSelector"
    Write-Output "A reviewed model artifact and benchmark dataset are still required for metrics."
}
finally {
    [Environment]::SetEnvironmentVariable("AUTPLAY_GPU_DEVICE_SELECTOR", $previousSelector)
    Pop-Location
}
