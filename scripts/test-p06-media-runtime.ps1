Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$image = "autplay-server:p06-local"

Push-Location $repoRoot
try {
    & docker build --file server/Dockerfile --tag $image .
    if ($LASTEXITCODE -ne 0) { throw "P06 CPU runtime image build failed" }

    $result = (& docker run --rm --network none --read-only --tmpfs /tmp:size=32m,mode=1777 $image python -m autplay.entrypoints.media_smoke | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "P06 real media-tool smoke failed" }
    $document = $result | ConvertFrom-Json
    if (
        $document.status -ne "ok" -or
        $document.codec -ne "flac" -or
        $document.rejected -ne 2 -or
        $document.quarantined -ne 2 -or
        $document.streamed_bytes -lt 1 -or
        $document.fingerprint_bytes -lt 1
    ) {
        throw "P06 media-tool evidence was incomplete"
    }
    Write-Output $result
}
finally {
    Pop-Location
}
