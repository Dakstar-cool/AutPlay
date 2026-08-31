param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$")]
    [string]$ReleaseTag,
    [Parameter(Mandatory = $true)]
    [string]$JavaHome,
    [Parameter(Mandatory = $true)]
    [string]$AndroidHome,
    [string]$DevelopmentKeystore = (Join-Path $env:USERPROFILE ".android\debug.keystore"),
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$releaseVersion = $ReleaseTag.Substring(1)
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot "dist\release"))
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $releaseRoot $ReleaseTag
}
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $outputRoot.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Release output must remain under $releaseRoot"
}
if (Test-Path -LiteralPath $outputRoot) {
    throw "Release output already exists: $outputRoot"
}

$zipalign = Join-Path $AndroidHome "build-tools\36.1.0\zipalign.exe"
$apksigner = Join-Path $AndroidHome "build-tools\36.1.0\apksigner.bat"
$aapt2 = Join-Path $AndroidHome "build-tools\36.1.0\aapt2.exe"
$keytool = Join-Path $JavaHome "bin\keytool.exe"
$gzipCandidates = @(
    (Join-Path $env:ProgramFiles "Git\usr\bin\gzip.exe"),
    (Join-Path $env:ProgramFiles "Git\mingw64\bin\gzip.exe")
)
$gzip = $gzipCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$unsignedSource = Join-Path $repoRoot "apps\android\build\outputs\apk\release\android-release-unsigned.apk"
$trustedLanSource = Join-Path $repoRoot "apps\android\build\outputs\apk\trustedLan\android-trustedLan.apk"
$gradle = Join-Path $repoRoot "gradlew.bat"
$checkScript = Join-Path $repoRoot "scripts\check.ps1"
$composeBase = Join-Path $repoRoot "deploy\compose\compose.yaml"
$composeRuntime = Join-Path $repoRoot "deploy\compose\compose.runtime.yaml"
$composeRelease = Join-Path $repoRoot "deploy\compose\compose.release.yaml"
$composeAdminLocal = Join-Path $repoRoot "deploy\compose\compose.admin-local.yaml"
$releaseNotes = Join-Path $repoRoot "docs\release\RELEASE_NOTES_$releaseVersion.md"
$installGuide = Join-Path $repoRoot "docs\operations\INSTALL_AND_PAIR.md"
$installerSource = Join-Path $repoRoot "deploy\installer"
$baselineSignedApk = Join-Path $repoRoot "docs\release\artifacts\autplay-rc1-dev-signed.apk"

foreach ($requiredPath in @(
    $JavaHome,
    $AndroidHome,
    $DevelopmentKeystore,
    $zipalign,
    $apksigner,
    $aapt2,
    $keytool,
    $gzip,
    $gradle,
    $checkScript,
    $composeBase,
    $composeRuntime,
    $composeRelease,
    $composeAdminLocal,
    $releaseNotes,
    $installGuide,
    $installerSource,
    $baselineSignedApk
)) {
    if (-not $requiredPath -or -not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required release input is missing: $requiredPath"
    }
}

Push-Location $repoRoot
try {
    $sourceCommit = (& git rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch "^[a-f0-9]{40}$") {
        throw "Unable to resolve the release source commit"
    }
    $tagCommit = (& git rev-list -n 1 $ReleaseTag | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $sourceCommit) {
        throw "Release tag $ReleaseTag must resolve to HEAD"
    }
    $worktreeStatus = (& git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $worktreeStatus) {
        throw "Release packaging requires a clean worktree"
    }

    $androidBuildFile = Get-Content -Raw -LiteralPath (Join-Path $repoRoot "apps\android\build.gradle.kts")
    $versionCodeMatch = [regex]::Match($androidBuildFile, 'versionCode\s*=\s*([0-9]+)')
    $versionNameMatch = [regex]::Match($androidBuildFile, 'versionName\s*=\s*"([^"]+)"')
    if (-not $versionCodeMatch.Success -or -not $versionNameMatch.Success) {
        throw "Unable to resolve the Android version from build.gradle.kts"
    }
    $androidVersionCode = [int]$versionCodeMatch.Groups[1].Value
    $androidVersionName = $versionNameMatch.Groups[1].Value
    if ($androidVersionName -ne $releaseVersion) {
        throw "Android versionName $androidVersionName does not match release tag $ReleaseTag"
    }

    $existingImageIds = @(& docker image ls --quiet --filter "reference=autplay-server:$ReleaseTag")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query the local Docker image store"
    }
    if ($existingImageIds.Count -gt 0) {
        throw "Refusing to replace existing local image autplay-server:$ReleaseTag"
    }

    New-Item -ItemType Directory -Path $outputRoot | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $outputRoot "sbom") | Out-Null

    $env:JAVA_HOME = $JavaHome
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $checkScript
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical release gate failed"
    }

    $gradleArguments = @(
        "--no-daemon",
        "--console=plain",
        "--max-workers=1",
        ":apps:android:assembleDebug",
        ":apps:android:assembleRelease",
        ":apps:android:assembleTrustedLan"
    )
    & $gradle @gradleArguments
    if (
        $LASTEXITCODE -ne 0 -or
        -not (Test-Path -LiteralPath $unsignedSource) -or
        -not (Test-Path -LiteralPath $trustedLanSource)
    ) {
        throw "Android release build failed"
    }
    $trustedLanBadging = (& $aapt2 dump badging $trustedLanSource | Out-String)
    $trustedLanManifest = (& $aapt2 dump xmltree $trustedLanSource --file AndroidManifest.xml | Out-String)
    $hardenedManifest = (& $aapt2 dump xmltree $unsignedSource --file AndroidManifest.xml | Out-String)
    if (
        $trustedLanBadging -notmatch "package: name='app\.autplay\.lan' versionCode='$androidVersionCode' versionName='$([regex]::Escape($releaseVersion))'" -or
        $trustedLanBadging -notmatch "application-label:'AutPlay LAN'" -or
        $trustedLanBadging -notmatch "application-label-ru:'AutPlay LAN'" -or
        $trustedLanManifest -notmatch 'debuggable\(0x[0-9a-f]+\)=true' -or
        $trustedLanManifest -notmatch 'usesCleartextTraffic\(0x[0-9a-f]+\)=true' -or
        $hardenedManifest -match 'debuggable\(0x[0-9a-f]+\)=true' -or
        $hardenedManifest -match 'usesCleartextTraffic\(0x[0-9a-f]+\)=true'
    ) {
        throw "Android hardened/trusted-LAN variant boundary is invalid"
    }

    $unsignedAsset = Join-Path $outputRoot "autplay-$releaseVersion-unsigned.apk"
    $alignedAsset = Join-Path $outputRoot "autplay-$releaseVersion-dev-aligned.apk"
    $signedAsset = Join-Path $outputRoot "autplay-$releaseVersion-dev-signed.apk"
    $certificateAsset = Join-Path $outputRoot "autplay-$releaseVersion-development-signing-cert.der"
    $trustedLanAsset = Join-Path $outputRoot "autplay-$releaseVersion-trusted-lan.apk"
    Copy-Item -LiteralPath $unsignedSource -Destination $unsignedAsset
    & $zipalign -f -p 4 $unsignedSource $alignedAsset
    if ($LASTEXITCODE -ne 0) {
        throw "Release APK zipalign failed"
    }

    $env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD = "android"
    try {
        & $apksigner sign `
            --ks $DevelopmentKeystore `
            --ks-key-alias androiddebugkey `
            --ks-pass env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD `
            --key-pass env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD `
            --out $signedAsset `
            $alignedAsset
        if ($LASTEXITCODE -ne 0) {
            throw "Development APK signing failed"
        }
        & $apksigner sign `
            --ks $DevelopmentKeystore `
            --ks-key-alias androiddebugkey `
            --ks-pass env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD `
            --key-pass env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD `
            --out $trustedLanAsset `
            $trustedLanSource
        if ($LASTEXITCODE -ne 0) {
            throw "Trusted-LAN APK development signing failed"
        }
        & $keytool -exportcert `
            -alias androiddebugkey `
            -keystore $DevelopmentKeystore `
            -storepass:env AUTPLAY_RELEASE_DEV_KEY_PASSWORD `
            -file $certificateAsset
        if ($LASTEXITCODE -ne 0) {
            throw "Development signing certificate export failed"
        }
    }
    finally {
        Remove-Item Env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $alignedAsset
    $signedVerification = (& $apksigner verify --verbose --print-certs $signedAsset | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Development-signed APK verification failed"
    }
    $trustedLanVerification = (& $apksigner verify --verbose --print-certs $trustedLanAsset | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Trusted-LAN APK signature verification failed"
    }
    $baselineVerification = (& $apksigner verify --verbose --print-certs $baselineSignedApk | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Baseline development signer verification failed"
    }
    $certificateDigestPattern = 'Signer #1 certificate SHA-256 digest: ([0-9a-f]+)'
    $signedDigest = [regex]::Match($signedVerification, $certificateDigestPattern).Groups[1].Value
    $trustedLanDigest = [regex]::Match($trustedLanVerification, $certificateDigestPattern).Groups[1].Value
    $baselineDigest = [regex]::Match($baselineVerification, $certificateDigestPattern).Groups[1].Value
    if (-not $signedDigest -or $signedDigest -ne $baselineDigest -or $trustedLanDigest -ne $baselineDigest) {
        throw "Development signer continuity check failed"
    }

    $imageTag = "autplay-server:$ReleaseTag"
    & docker build `
        --platform linux/amd64 `
        --file server/Dockerfile `
        --tag $imageTag `
        --label "org.opencontainers.image.version=$releaseVersion" `
        --label "org.opencontainers.image.revision=$sourceCommit" `
        --label "org.opencontainers.image.source=https://github.com/Dakstar-cool/AutPlay.git" `
        .
    if ($LASTEXITCODE -ne 0) {
        throw "CPU server image build failed"
    }
    $builtImage = (& docker image inspect $imageTag | ConvertFrom-Json)
    if (
        $builtImage.Os -ne "linux" -or
        $builtImage.Architecture -ne "amd64" -or
        $builtImage.Config.User -ne "autplay:autplay" -or
        $builtImage.Config.Labels.'org.opencontainers.image.revision' -ne $sourceCommit
    ) {
        throw "Built server image identity or runtime boundary is invalid"
    }
    $builtImageId = [string]$builtImage.Id

    $serverTar = Join-Path $outputRoot "autplay-server-$ReleaseTag.docker.tar"
    & docker save --output $serverTar $imageTag
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image archive creation failed"
    }
    & $gzip -9 -n $serverTar
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image archive compression failed"
    }
    $serverArchive = "$serverTar.gz"
    if (-not (Test-Path -LiteralPath $serverArchive)) {
        throw "Compressed Docker image archive is missing"
    }

    & docker image rm $imageTag | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to remove the generated image tag before archive verification"
    }
    & docker load --input $serverArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image archive reload failed"
    }
    $loadedImage = (& docker image inspect $imageTag | ConvertFrom-Json)
    if (
        [string]$loadedImage.Id -ne $builtImageId -or
        $loadedImage.Os -ne "linux" -or
        $loadedImage.Architecture -ne "amd64" -or
        $loadedImage.Config.Labels.'org.opencontainers.image.revision' -ne $sourceCommit
    ) {
        throw "Reloaded server image does not match the built image"
    }

    $installerStaging = Join-Path $outputRoot "autplay-server-$ReleaseTag-installer"
    New-Item -ItemType Directory -Path $installerStaging | Out-Null
    foreach ($composeInput in @($composeBase, $composeRuntime, $composeRelease, $composeAdminLocal)) {
        Copy-Item -LiteralPath $composeInput -Destination (Join-Path $installerStaging (Split-Path -Leaf $composeInput))
    }
    Get-ChildItem -LiteralPath $installerSource -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $installerStaging $_.Name)
    }
    Copy-Item -LiteralPath $installGuide -Destination (Join-Path $installerStaging "INSTALL_AND_PAIR.md")
    Copy-Item -LiteralPath $releaseNotes -Destination (Join-Path $installerStaging "RELEASE_NOTES.md")
    Copy-Item -LiteralPath $serverArchive -Destination (Join-Path $installerStaging (Split-Path -Leaf $serverArchive))
    $installerManifest = [ordered]@{
        schema_version = 1
        release_tag = $ReleaseTag
        release_version = $releaseVersion
        source_commit = $sourceCommit
        image_tag = $imageTag
        platform = "linux/amd64"
        distribution_class = "TRUSTED_LAN_DEVELOPMENT"
        server_archive = [ordered]@{
            filename = Split-Path -Leaf $serverArchive
            size_bytes = (Get-Item -LiteralPath $serverArchive).Length
            sha256 = (Get-FileHash -LiteralPath $serverArchive -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    [IO.File]::WriteAllText(
        (Join-Path $installerStaging "server-installer-manifest.json"),
        "$(($installerManifest | ConvertTo-Json -Depth 6))`n",
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllLines(
        (Join-Path $installerStaging "server-installer.env"),
        @(
            "RELEASE_TAG=$ReleaseTag",
            "RELEASE_VERSION=$releaseVersion",
            "SOURCE_COMMIT=$sourceCommit",
            "IMAGE_TAG=$imageTag",
            "SERVER_ARCHIVE=$(Split-Path -Leaf $serverArchive)",
            "SERVER_ARCHIVE_SHA256=$((Get-FileHash -LiteralPath $serverArchive -Algorithm SHA256).Hash.ToLowerInvariant())"
        ),
        [Text.UTF8Encoding]::new($false)
    )
    $installerChecksumPath = Join-Path $installerStaging "SHA256SUMS"
    $installerChecksumLines = Get-ChildItem -LiteralPath $installerStaging -File |
        Where-Object { $_.FullName -ne $installerChecksumPath } |
        Sort-Object Name |
        ForEach-Object {
            "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $($_.Name)"
        }
    [IO.File]::WriteAllLines($installerChecksumPath, $installerChecksumLines, [Text.UTF8Encoding]::new($false))
    $installerAsset = Join-Path $outputRoot "autplay-server-$ReleaseTag-installer.zip"
    Compress-Archive -Path (Join-Path $installerStaging "*") -DestinationPath $installerAsset -CompressionLevel NoCompression
    Remove-Item -LiteralPath $installerStaging -Recurse -Force
    if (-not (Test-Path -LiteralPath $installerAsset)) {
        throw "Server installer bundle creation failed"
    }

    $databaseUrl = "postgresql+psycopg://runtime:synthetic@127.0.0.1:1/autplay"
    $authSecret = "release-smoke-synthetic-secret-at-least-32-bytes"
    & docker run --rm --network none --read-only --tmpfs "/tmp:size=32m,mode=1777" `
        --env "AUTPLAY_DATABASE_URL=$databaseUrl" `
        --env "AUTPLAY_AUTH_SIGNING_SECRET=$authSecret" `
        --env "AUTPLAY_PROFILE=test" `
        $imageTag autplay-api --check-config
    if ($LASTEXITCODE -ne 0) {
        throw "Reloaded API configuration smoke failed"
    }
    & docker run --rm --network none --read-only --tmpfs "/tmp:size=32m,mode=1777" `
        --env "AUTPLAY_DATABASE_URL=$databaseUrl" `
        --env "AUTPLAY_PROFILE=test" `
        $imageTag autplay-worker-cpu --check-config
    if ($LASTEXITCODE -ne 0) {
        throw "Reloaded CPU worker configuration smoke failed"
    }
    & docker run --rm --network none --read-only --tmpfs "/tmp:size=32m,mode=1777" `
        --env "AUTPLAY_DATABASE_URL=$databaseUrl" `
        --env "AUTPLAY_AUTH_SIGNING_SECRET=$authSecret" `
        --env "AUTPLAY_PROFILE=test" `
        $imageTag autplay-stream --check-config
    if ($LASTEXITCODE -ne 0) {
        throw "Reloaded stream configuration smoke failed"
    }
    & docker run --rm --network none --read-only --tmpfs "/tmp:size=32m,mode=1777" `
        $imageTag python -m autplay.entrypoints.media_smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Reloaded media-tool smoke failed"
    }

    function Get-FreeTcpPorts([int]$Count) {
        $listeners = @()
        try {
            for ($index = 0; $index -lt $Count; $index++) {
                $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
                $listener.Start()
                $listeners += $listener
            }
            return @($listeners | ForEach-Object { ([Net.IPEndPoint]$_.LocalEndpoint).Port })
        }
        finally {
            $listeners | ForEach-Object { $_.Stop() }
        }
    }

    $runtimeProject = ("autplay-release-$releaseVersion-$PID" -replace "[^a-zA-Z0-9_-]", "-").ToLowerInvariant()
    $runtimeSecretDirectory = Join-Path ([IO.Path]::GetTempPath()) "autplay-release-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $runtimeSecretDirectory | Out-Null
    $runtimeSecretFile = Join-Path $runtimeSecretDirectory "auth.txt"
    $runtimeAdminSourceSecretFile = Join-Path $runtimeSecretDirectory "admin-source.txt"
    $runtimeAdminCsrfSecretFile = Join-Path $runtimeSecretDirectory "admin-csrf.txt"
    $runtimeIdentityFile = Join-Path $runtimeSecretDirectory "identity.pem"
    $runtimeComposeArguments = @(
        "compose",
        "--project-name", $runtimeProject,
        "--file", $composeBase,
        "--file", $composeRuntime,
        "--file", $composeAdminLocal,
        "--file", $composeRelease,
        "--profile", "runtime"
    )
    $runtimePorts = @(Get-FreeTcpPorts 3)
    $runtimeApiPort = $runtimePorts[0]
    $runtimeMobileApiPort = $runtimePorts[1]
    $runtimeMobileStreamPort = $runtimePorts[2]
    try {
        $runtimeSecret = "release-smoke-$([Guid]::NewGuid().ToString('N'))-$([Guid]::NewGuid().ToString('N'))"
        $runtimeAdminSourceSecret = "release-admin-source-$([Guid]::NewGuid().ToString('N'))-$([Guid]::NewGuid().ToString('N'))"
        $runtimeAdminCsrfSecret = "release-admin-csrf-$([Guid]::NewGuid().ToString('N'))-$([Guid]::NewGuid().ToString('N'))"
        [IO.File]::WriteAllText($runtimeSecretFile, "$runtimeSecret`n", [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($runtimeAdminSourceSecretFile, "$runtimeAdminSourceSecret`n", [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($runtimeAdminCsrfSecretFile, "$runtimeAdminCsrfSecret`n", [Text.UTF8Encoding]::new($false))
        $runtimeIdentityPem = & docker run --rm --network none --entrypoint python $imageTag -c "from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import ec; print(ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'), end='')"
        if ($LASTEXITCODE -ne 0 -or -not $runtimeIdentityPem) {
            throw "Release runtime identity generation failed"
        }
        [IO.File]::WriteAllText($runtimeIdentityFile, (($runtimeIdentityPem -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
        $env:AUTPLAY_SERVER_IMAGE = $imageTag
        $env:AUTPLAY_RUNTIME_AUTH_SECRET_FILE = $runtimeSecretFile
        $env:AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE = $runtimeAdminSourceSecretFile
        $env:AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE = $runtimeAdminCsrfSecretFile
        $env:AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE = $runtimeIdentityFile
        $env:AUTPLAY_RUNTIME_BIND_HOST = "127.0.0.1"
        $env:AUTPLAY_MOBILE_BIND_HOST = "127.0.0.1"
        $env:AUTPLAY_API_PORT = [string]$runtimeApiPort
        $env:AUTPLAY_MOBILE_API_PORT = [string]$runtimeMobileApiPort
        $env:AUTPLAY_MOBILE_STREAM_PORT = [string]$runtimeMobileStreamPort

        & docker @runtimeComposeArguments up --no-build --wait
        if ($LASTEXITCODE -ne 0) {
            & docker @runtimeComposeArguments logs --no-color --tail 200 `
                migrate api worker-cpu stream mobile-api admin-init
            throw "Reloaded release image Compose runtime gate failed"
        }
        $apiReady = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$runtimeApiPort/health/ready" `
            -TimeoutSec 5
        $adminLogin = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$runtimeApiPort/admin/login" `
            -TimeoutSec 5
        $mobileDiscovery = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$runtimeMobileApiPort/api/v1/pairing/discovery" `
            -TimeoutSec 5
        $streamLive = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$runtimeMobileStreamPort/health/live" `
            -TimeoutSec 5
        $mobileAdminStatus = $null
        try {
            $mobileAdmin = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri "http://127.0.0.1:$runtimeMobileApiPort/admin/login" `
                -TimeoutSec 5
            $mobileAdminStatus = $mobileAdmin.StatusCode
        }
        catch {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $mobileAdminStatus = [int]$_.Exception.Response.StatusCode
            }
            else {
                throw
            }
        }
        if (
            $apiReady.StatusCode -ne 200 -or
            $adminLogin.StatusCode -ne 200 -or
            $mobileDiscovery.StatusCode -ne 200 -or
            $streamLive.StatusCode -ne 200 -or
            $mobileAdminStatus -ne 404
        ) {
            throw "Reloaded release image runtime health endpoints failed"
        }
    }
    finally {
        & docker @runtimeComposeArguments down --volumes --remove-orphans | Out-Null
        Remove-Item -LiteralPath $runtimeSecretDirectory -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_SERVER_IMAGE -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_RUNTIME_AUTH_SECRET_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_RUNTIME_BIND_HOST -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_MOBILE_BIND_HOST -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_API_PORT -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_MOBILE_API_PORT -ErrorAction SilentlyContinue
        Remove-Item Env:AUTPLAY_MOBILE_STREAM_PORT -ErrorAction SilentlyContinue
    }

    & uv export --frozen --format cyclonedx1.5 `
        --output-file (Join-Path $outputRoot "sbom\python-root.cdx.json") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Root SBOM export failed"
    }
    & uv export --project server --frozen --format cyclonedx1.5 `
        --output-file (Join-Path $outputRoot "sbom\python-server.cdx.json") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Server SBOM export failed"
    }
    & uv export --project gpu --frozen --format cyclonedx1.5 `
        --output-file (Join-Path $outputRoot "sbom\python-gpu.cdx.json") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "GPU SBOM export failed"
    }
    $dependencyReport = Join-Path $outputRoot "ANDROID_RELEASE_DEPENDENCIES.txt"
    & $gradle --no-daemon --console=plain --max-workers=1 `
        :apps:android:dependencies --configuration releaseRuntimeClasspath |
        Out-File -LiteralPath $dependencyReport -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw "Android release dependency report failed"
    }
    Copy-Item -LiteralPath $releaseNotes `
        -Destination (Join-Path $outputRoot "RELEASE_NOTES.md")
    Copy-Item -LiteralPath $installGuide `
        -Destination (Join-Path $outputRoot "INSTALL_AND_PAIR.md")
    Copy-Item -LiteralPath docs/release/SECURITY_REVIEW.md `
        -Destination (Join-Path $outputRoot "SECURITY_REVIEW.md")

    function Get-ArtifactRecord([string]$Path) {
        $item = Get-Item -LiteralPath $Path
        return [ordered]@{
            filename = $item.Name
            size_bytes = $item.Length
            sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }

    $manifestPath = Join-Path $outputRoot "release-manifest.json"
    $manifest = [ordered]@{
        schema_version = 1
        generated_at = [DateTimeOffset]::UtcNow.ToString("o")
        release_tag = $ReleaseTag
        source_commit = $sourceCommit
        distribution_class = "DEVELOPMENT_RELEASE"
        production_signed = $false
        production_deployed = $false
        android = [ordered]@{
            application_id = "app.autplay"
            version_code = $androidVersionCode
            version_name = $releaseVersion
            unsigned = Get-ArtifactRecord $unsignedAsset
            development_signed = Get-ArtifactRecord $signedAsset
            trusted_lan = Get-ArtifactRecord $trustedLanAsset
            trusted_lan_application_id = "app.autplay.lan"
            trusted_lan_debuggable = $true
            trusted_lan_cleartext_rfc1918_only = $true
            development_signer_certificate = Get-ArtifactRecord $certificateAsset
        }
        server = [ordered]@{
            archive_format = "DOCKER_IMAGE_ARCHIVE_GZIP"
            image_tag = $imageTag
            image_id = $builtImageId
            platform = "linux/amd64"
            runtime = "CPU_ONLY"
            archive = Get-ArtifactRecord $serverArchive
            installer = Get-ArtifactRecord $installerAsset
            installer_topology = "TRUSTED_LAN_DEVELOPMENT"
            archive_reload = "PASS"
            configuration_smoke = "PASS"
            media_smoke = "PASS"
            disposable_compose_runtime = "PASS"
            registry_pushed = $false
        }
        verification = [ordered]@{
            canonical_host_gate = "PASS"
            android_signature = "PASS"
            artifact_hash_manifest = "SHA256SUMS"
        }
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText(
        $manifestPath,
        "$manifestJson`n",
        [Text.UTF8Encoding]::new($false)
    )

    $checksumPath = Join-Path $outputRoot "SHA256SUMS"
    $checksumLines = Get-ChildItem -LiteralPath $outputRoot -File -Recurse |
        Where-Object { $_.FullName -ne $checksumPath } |
        Sort-Object { $_.FullName.Substring($outputRoot.Length).TrimStart([char[]]"\/") } |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($outputRoot.Length).TrimStart([char[]]"\/").Replace("\", "/")
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relativePath"
        }
    [IO.File]::WriteAllLines($checksumPath, $checksumLines, [Text.UTF8Encoding]::new($false))

    Write-Output "Release bundle PASS: $outputRoot"
    Write-Output "Source commit: $sourceCommit"
    Write-Output "Server image: $builtImageId"
}
finally {
    Remove-Item Env:AUTPLAY_RELEASE_DEV_KEY_PASSWORD -ErrorAction SilentlyContinue
    Pop-Location
}
