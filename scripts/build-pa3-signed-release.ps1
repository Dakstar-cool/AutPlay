param(
    [switch]$Preflight,
    [string]$EncryptedKeystore = (Join-Path $env:LOCALAPPDATA "AutPlay\signing\autplay-production.p12.age"),
    [string]$SignerReceipt = (Join-Path $env:LOCALAPPDATA "AutPlay\signing\android-signer-receipt.json"),
    [string]$AndroidHome = $env:ANDROID_HOME,
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$AgeDirectory = "D:\AutPlayBackup\tools\age-v1.3.2\age",
    [string]$EvidenceRoot = "D:\AutPlay-PA3-Evidence\android-signer-update"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-PlainText {
    param([Security.SecureString]$Value)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Get-Sha256Hex {
    param([string]$LiteralPath)

    $stream = [IO.File]::OpenRead($LiteralPath)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Get-TextSha256Hex {
    param([string]$Value)

    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha256.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Assert-OwnerOnlyAcl {
    param(
        [string]$LiteralPath,
        [Security.Principal.SecurityIdentifier]$OwnerSid,
        [switch]$Directory
    )

    $acl = Get-Acl -LiteralPath $LiteralPath
    $actualOwner = $acl.GetOwner([Security.Principal.SecurityIdentifier])
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    ))
    if (
        -not $acl.AreAccessRulesProtected -or
        $actualOwner.Value -cne $OwnerSid.Value -or
        $rules.Count -ne 1
    ) {
        throw "SIGNING_TEMP_ACL_NOT_OWNER_ONLY: $LiteralPath"
    }

    $rule = $rules[0]
    if (
        $rule.IsInherited -or
        $rule.IdentityReference.Value -cne $OwnerSid.Value -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
            [Security.AccessControl.FileSystemRights]::FullControl
    ) {
        throw "SIGNING_TEMP_ACL_NOT_OWNER_ONLY: $LiteralPath"
    }

    if ($Directory) {
        $expectedInheritance =
            [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
        if (
            $rule.InheritanceFlags -ne $expectedInheritance -or
            $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None
        ) {
            throw "SIGNING_TEMP_ACL_NOT_OWNER_ONLY: $LiteralPath"
        }
    }
    elseif ($rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None) {
        throw "SIGNING_TEMP_ACL_NOT_OWNER_ONLY: $LiteralPath"
    }
}

function Set-OwnerOnlyDirectoryAcl {
    param([string]$LiteralPath)

    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not $ownerSid) {
        throw "SIGNING_TEMP_OWNER_SID_UNAVAILABLE"
    }

    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($ownerSid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $ownerSid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $LiteralPath -AclObject $acl
    Assert-OwnerOnlyAcl -LiteralPath $LiteralPath -OwnerSid $ownerSid -Directory
}

function Set-OwnerOnlyFileAcl {
    param([string]$LiteralPath)

    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not $ownerSid) {
        throw "SIGNING_TEMP_OWNER_SID_UNAVAILABLE"
    }

    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($ownerSid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $ownerSid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $LiteralPath -AclObject $acl
    Assert-OwnerOnlyAcl -LiteralPath $LiteralPath -OwnerSid $ownerSid
}

function Write-Utf8FileAtomically {
    param(
        [string]$LiteralPath,
        [string]$Value
    )

    $fullPath = [IO.Path]::GetFullPath($LiteralPath)
    $directory = Split-Path -Parent $fullPath
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw "ATOMIC_WRITE_DIRECTORY_MISSING: $directory"
    }
    if (Test-Path -LiteralPath $fullPath) {
        throw "ATOMIC_WRITE_TARGET_ALREADY_EXISTS: $fullPath"
    }

    $temporaryPath = Join-Path $directory (
        ".{0}.{1}.tmp" -f (Split-Path -Leaf $fullPath), [guid]::NewGuid().ToString("N")
    )
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        $stream = [IO.FileStream]::new(
            $temporaryPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        try {
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        }
        finally {
            $stream.Dispose()
        }
        [IO.File]::Move($temporaryPath, $fullPath)
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            [IO.File]::Delete($temporaryPath)
        }
    }
}

function Invoke-NativeCapture {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = (& $FilePath @Arguments 2>&1 | Out-String)
        return [pscustomobject]@{ ExitCode = [int]$LASTEXITCODE; Output = $output }
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Get-UntrackedSourceManifest {
    param(
        [string]$Git,
        [string]$RepoRoot
    )

    $capture = Invoke-NativeCapture $Git @(
        "-C", $RepoRoot, "ls-files", "--others", "--exclude-standard", "-z"
    )
    if ($capture.ExitCode -ne 0) {
        throw "SOURCE_UNTRACKED_MANIFEST_CAPTURE_FAILED"
    }

    $lastTerminator = $capture.Output.LastIndexOf([char]0)
    if ($lastTerminator -lt 0) {
        if ([string]::IsNullOrWhiteSpace($capture.Output)) {
            return @()
        }
        throw "SOURCE_UNTRACKED_MANIFEST_FORMAT_INVALID"
    }
    if (-not [string]::IsNullOrWhiteSpace($capture.Output.Substring($lastTerminator + 1))) {
        throw "SOURCE_UNTRACKED_MANIFEST_FORMAT_INVALID"
    }

    $payload = $capture.Output.Substring(0, $lastTerminator)
    [string[]]$paths = if ($payload.Length -eq 0) {
        @()
    }
    else {
        $payload.Split([char]0, [StringSplitOptions]::RemoveEmptyEntries)
    }
    [Array]::Sort($paths, [StringComparer]::Ordinal)

    $rootPrefix = $RepoRoot.TrimEnd([char[]]"\/") + [IO.Path]::DirectorySeparatorChar
    $records = @()
    foreach ($path in $paths) {
        $fullPath = [IO.Path]::GetFullPath((Join-Path $RepoRoot $path))
        if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "SOURCE_UNTRACKED_PATH_OUTSIDE_REPOSITORY"
        }
        $item = Get-Item -LiteralPath $fullPath -Force
        if (
            $item -isnot [IO.FileInfo] -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
        ) {
            throw "SOURCE_UNTRACKED_INPUT_NOT_REGULAR_FILE: $path"
        }
        $records += [ordered]@{
            path = $path.Replace("\", "/")
            bytes = $item.Length
            sha256 = Get-Sha256Hex $fullPath
        }
    }
    return $records
}

function Get-SourceProvenance {
    param(
        [string]$Git,
        [string]$RepoRoot
    )

    $sourceCommit = Invoke-NativeCapture $Git @("-C", $RepoRoot, "rev-parse", "HEAD")
    $sourceStatus = Invoke-NativeCapture $Git @(
        "-C", $RepoRoot, "status", "--porcelain=v1", "--untracked-files=all"
    )
    $sourceDiff = Invoke-NativeCapture $Git @(
        "-C", $RepoRoot, "diff", "--binary", "--no-ext-diff", "HEAD", "--"
    )
    if ($sourceCommit.ExitCode -ne 0 -or $sourceStatus.ExitCode -ne 0 -or $sourceDiff.ExitCode -ne 0) {
        throw "SOURCE_PROVENANCE_CAPTURE_FAILED"
    }

    $commit = $sourceCommit.Output.Trim()
    if ($commit -notmatch "^[0-9a-f]{40,64}$") {
        throw "SOURCE_COMMIT_INVALID"
    }
    $untrackedManifest = @(Get-UntrackedSourceManifest -Git $Git -RepoRoot $RepoRoot)
    $untrackedManifestJson = ConvertTo-Json -InputObject $untrackedManifest -Depth 4 -Compress
    $statusSha256 = Get-TextSha256Hex $sourceStatus.Output
    $trackedDiffSha256 = Get-TextSha256Hex $sourceDiff.Output
    $untrackedManifestSha256 = Get-TextSha256Hex $untrackedManifestJson
    $fingerprintInput = [ordered]@{
        commit = $commit
        status_sha256 = $statusSha256
        tracked_diff_sha256 = $trackedDiffSha256
        untracked_manifest_sha256 = $untrackedManifestSha256
    } | ConvertTo-Json -Compress

    return [pscustomobject]@{
        Commit = $commit
        Worktree = if ([string]::IsNullOrWhiteSpace($sourceStatus.Output)) { "CLEAN" } else { "DIRTY" }
        StatusSha256 = $statusSha256
        TrackedDiffSha256 = $trackedDiffSha256
        UntrackedManifest = $untrackedManifest
        UntrackedManifestSha256 = $untrackedManifestSha256
        Fingerprint = Get-TextSha256Hex $fingerprintInput
    }
}

function Get-StableSourceProvenance {
    param(
        [string]$Git,
        [string]$RepoRoot
    )

    $first = Get-SourceProvenance -Git $Git -RepoRoot $RepoRoot
    $second = Get-SourceProvenance -Git $Git -RepoRoot $RepoRoot
    if ($first.Fingerprint -cne $second.Fingerprint) {
        throw "SOURCE_PROVENANCE_UNSTABLE"
    }
    return $second
}

function Invoke-GradleRelease {
    param(
        [string]$Gradle,
        [int]$VersionCode,
        [string]$VersionName
    )

    $arguments = @(
        "--no-daemon",
        "--no-configuration-cache",
        "--console=plain",
        "--max-workers=1",
        "--rerun-tasks",
        "-Pautplay.versionCode=$VersionCode",
        "-Pautplay.versionName=$VersionName",
        ":apps:android:assembleRelease"
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Gradle @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "SIGNED_RELEASE_BUILD_FAILED_$VersionCode"
        }
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$gradle = Join-Path $repoRoot "gradlew.bat"
$releaseApk = Join-Path $repoRoot "apps\android\build\outputs\apk\release\android-release.apk"
$git = (Get-Command "git.exe" -ErrorAction Stop).Source

if (-not $AndroidHome) {
    throw "ANDROID_HOME_NOT_SET"
}
if (-not $JavaHome) {
    throw "JAVA_HOME_NOT_SET"
}
$keytool = Join-Path $JavaHome "bin\keytool.exe"
$apksigner = Join-Path $AndroidHome "build-tools\36.1.0\apksigner.bat"
$aapt2 = Join-Path $AndroidHome "build-tools\36.1.0\aapt2.exe"
$ageDirectory = [IO.Path]::GetFullPath($AgeDirectory)
$agePath = Join-Path $ageDirectory "age.exe"
$batchpass = Join-Path $ageDirectory "age-plugin-batchpass.exe"
$expectedAgeSha256 = "2821a4ed191da07372acd302e5f6feae7a7985e285e1417765ebe74025af45f0"
$expectedBatchpassSha256 = "cfc08ce451552ba12de0a7ec350c4cfbdc3dc7114dbf8ea564562cac770dc5d8"

foreach ($requiredPath in @(
    $gradle,
    $EncryptedKeystore,
    $SignerReceipt,
    $keytool,
    $apksigner,
    $aapt2,
    $agePath,
    $batchpass
)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "REQUIRED_SIGNING_INPUT_MISSING: $requiredPath"
    }
}
if (
    (Get-Sha256Hex $agePath) -cne $expectedAgeSha256 -or
    (Get-Sha256Hex $batchpass) -cne $expectedBatchpassSha256
) {
    throw "PINNED_AGE_TOOL_HASH_MISMATCH"
}

$receipt = Get-Content -Raw -LiteralPath $SignerReceipt | ConvertFrom-Json
$expectedEncryptedHash = [string]$receipt.artifact.encrypted_sha256
$expectedFingerprint = [string]$receipt.certificate.sha256_fingerprint
if (
    $receipt.status -ne "PASS" -or
    $receipt.application_id -ne "app.autplay" -or
    $expectedEncryptedHash -notmatch "^[a-f0-9]{64}$" -or
    $expectedFingerprint -notmatch "^[a-f0-9]{64}$" -or
    (Get-Sha256Hex $EncryptedKeystore) -cne $expectedEncryptedHash
) {
    throw "SIGNER_RECEIPT_OR_CIPHERTEXT_INVALID"
}

if ($Preflight) {
    [pscustomobject]@{
        Result = "PASS"
        ApplicationId = "app.autplay"
        EncryptedSigner = "PASS"
        SignerReceipt = "PASS"
        AndroidTools = "PASS"
        AgeBatchpass = "PASS"
        Secrets = "NOT_REQUESTED"
    } | ConvertTo-Json -Compress
    exit 0
}

$sourceProvenance = Get-StableSourceProvenance -Git $git -RepoRoot $repoRoot

$temporaryRoot = "D:\AutPlay-PA3-Temporary"
$temporaryDirectory = Join-Path $temporaryRoot ([guid]::NewGuid().ToString("N"))
$temporaryKeystore = Join-Path $temporaryDirectory "autplay-production.p12"
$evidenceDirectory = Join-Path $EvidenceRoot ([DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
$previousPath = $env:PATH
$keystoreSecure = $null
$recoverySecure = $null
$keystorePassword = $null
$recoveryPassphrase = $null
$cleanupVerified = $false
$artifactRecords = $null

try {
    Write-Host "Enter the two secrets already saved in your password manager."
    Write-Host "Do not paste either secret into chat. Input below is hidden."

    $keystoreSecure = Read-Host "Enter the Android keystore password" -AsSecureString
    $recoverySecure = Read-Host "Enter the recovery-envelope passphrase" -AsSecureString
    $keystorePassword = ConvertTo-PlainText $keystoreSecure
    $recoveryPassphrase = ConvertTo-PlainText $recoverySecure

    if ($keystorePassword.Length -lt 20 -or $recoveryPassphrase.Length -lt 20) {
        throw "SIGNING_SECRET_TOO_SHORT"
    }
    if ($keystorePassword -ceq $recoveryPassphrase) {
        throw "SIGNING_SECRETS_MUST_BE_DISTINCT"
    }
    $temporaryRootPrefix = [IO.Path]::GetFullPath($temporaryRoot).TrimEnd([char[]]"\/") +
        [IO.Path]::DirectorySeparatorChar
    if (-not ([IO.Path]::GetFullPath($temporaryDirectory)).StartsWith(
        $temporaryRootPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "TEMPORARY_PATH_INVALID"
    }
    if (Test-Path -LiteralPath $evidenceDirectory) {
        throw "EVIDENCE_DIRECTORY_ALREADY_EXISTS"
    }

    if (Test-Path -LiteralPath $temporaryDirectory) {
        throw "TEMPORARY_DIRECTORY_ALREADY_EXISTS"
    }
    [void][IO.Directory]::CreateDirectory($temporaryDirectory)
    Set-OwnerOnlyDirectoryAcl -LiteralPath $temporaryDirectory
    $env:PATH = $ageDirectory + [IO.Path]::PathSeparator + $previousPath
    $env:AGE_PASSPHRASE = $recoveryPassphrase
    try {
        $decrypt = Invoke-NativeCapture $agePath @(
            "--decrypt", "-j", "batchpass", "--output", $temporaryKeystore, $EncryptedKeystore
        )
    }
    finally {
        Remove-Item Env:AGE_PASSPHRASE -ErrorAction SilentlyContinue
        $env:PATH = $previousPath
    }
    if ($decrypt.ExitCode -ne 0 -and $decrypt.Output -match "(?i)incorrect passphrase") {
        throw "RECOVERY_PASSPHRASE_INCORRECT"
    }
    if ($decrypt.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $temporaryKeystore)) {
        throw "SIGNER_DECRYPTION_FAILED"
    }
    Set-OwnerOnlyFileAcl -LiteralPath $temporaryKeystore
    Assert-OwnerOnlyAcl `
        -LiteralPath $temporaryDirectory `
        -OwnerSid ([Security.Principal.WindowsIdentity]::GetCurrent().User) `
        -Directory

    $env:AUTPLAY_ANDROID_KEYSTORE_PASSWORD = $keystorePassword
    $keyListing = Invoke-NativeCapture $keytool @(
        "-J-Duser.language=en",
        "-J-Duser.country=US",
        "-list",
        "-v",
        "-keystore", $temporaryKeystore,
        "-storetype", "PKCS12",
        "-storepass:env", "AUTPLAY_ANDROID_KEYSTORE_PASSWORD"
    )
    if ($keyListing.ExitCode -ne 0) {
        throw "KEYSTORE_PASSWORD_OR_CONTENT_INVALID"
    }
    $aliasMatches = [regex]::Matches($keyListing.Output, "(?m)^Alias name:\s*(.+?)\s*$")
    if ($aliasMatches.Count -ne 1) {
        throw "KEYSTORE_MUST_CONTAIN_EXACTLY_ONE_ALIAS"
    }
    $keyAlias = $aliasMatches[0].Groups[1].Value

    $env:AUTPLAY_ANDROID_KEYSTORE_PATH = $temporaryKeystore
    $env:AUTPLAY_ANDROID_KEY_ALIAS = $keyAlias
    $env:AUTPLAY_ANDROID_KEY_PASSWORD = $keystorePassword
    New-Item -ItemType Directory -Path $evidenceDirectory -Force | Out-Null

    $builds = @(
        [pscustomobject]@{ VersionCode = 3; VersionName = "0.3.0-pa3.1" },
        [pscustomobject]@{ VersionCode = 4; VersionName = "0.3.0-pa3.2" }
    )
    $artifactRecords = @()
    Push-Location $repoRoot
    try {
        foreach ($build in $builds) {
            Invoke-GradleRelease $gradle $build.VersionCode $build.VersionName
            if (-not (Test-Path -LiteralPath $releaseApk)) {
                throw "SIGNED_RELEASE_APK_MISSING_$($build.VersionCode)"
            }

            $artifactName = "autplay-$($build.VersionName)-signed.apk"
            $artifactPath = Join-Path $evidenceDirectory $artifactName
            Copy-Item -LiteralPath $releaseApk -Destination $artifactPath

            $signature = Invoke-NativeCapture $apksigner @("verify", "--verbose", "--print-certs", $artifactPath)
            if ($signature.ExitCode -ne 0) {
                throw "APK_SIGNATURE_VERIFICATION_FAILED_$($build.VersionCode)"
            }
            $fingerprintMatch = [regex]::Match(
                $signature.Output,
                "Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]+)"
            )
            $fingerprint = $fingerprintMatch.Groups[1].Value.ToLowerInvariant()
            if ($fingerprint -cne $expectedFingerprint) {
                throw "APK_SIGNER_FINGERPRINT_MISMATCH_$($build.VersionCode)"
            }

            $badging = Invoke-NativeCapture $aapt2 @("dump", "badging", $artifactPath)
            $versionPattern = "package: name='app\.autplay' versionCode='$($build.VersionCode)' versionName='$([regex]::Escape($build.VersionName))'"
            if ($badging.ExitCode -ne 0 -or $badging.Output -notmatch $versionPattern) {
                throw "APK_VERSION_IDENTITY_MISMATCH_$($build.VersionCode)"
            }

            $artifactRecords += [ordered]@{
                filename = $artifactName
                version_code = $build.VersionCode
                version_name = $build.VersionName
                bytes = (Get-Item -LiteralPath $artifactPath).Length
                sha256 = Get-Sha256Hex $artifactPath
                certificate_sha256 = $fingerprint
                apksigner = "PASS"
            }
        }
    }
    finally {
        Pop-Location
    }

    $postBuildSourceProvenance = Get-StableSourceProvenance -Git $git -RepoRoot $repoRoot
    if ($sourceProvenance.Fingerprint -cne $postBuildSourceProvenance.Fingerprint) {
        throw "SOURCE_PROVENANCE_DRIFT_DETECTED"
    }
}
finally {
    $env:PATH = $previousPath
    Remove-Item Env:AGE_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:AUTPLAY_ANDROID_KEYSTORE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:AUTPLAY_ANDROID_KEYSTORE_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:AUTPLAY_ANDROID_KEY_ALIAS -ErrorAction SilentlyContinue
    Remove-Item Env:AUTPLAY_ANDROID_KEY_PASSWORD -ErrorAction SilentlyContinue
    $keystorePassword = $null
    $recoveryPassphrase = $null
    if ($keystoreSecure) {
        $keystoreSecure.Dispose()
    }
    if ($recoverySecure) {
        $recoverySecure.Dispose()
    }
    if (Test-Path -LiteralPath $temporaryKeystore -PathType Leaf) {
        try {
            [IO.File]::Delete($temporaryKeystore)
        }
        catch {
            throw "PLAINTEXT_KEYSTORE_CLEANUP_FAILED: $temporaryKeystore"
        }
    }
    if (Test-Path -LiteralPath $temporaryKeystore) {
        throw "PLAINTEXT_KEYSTORE_CLEANUP_FAILED: $temporaryKeystore"
    }
    if (Test-Path -LiteralPath $temporaryDirectory) {
        try {
            [IO.Directory]::Delete($temporaryDirectory, $true)
        }
        catch {
            throw "SIGNING_TEMP_DIRECTORY_CLEANUP_FAILED: $temporaryDirectory"
        }
    }
    if (Test-Path -LiteralPath $temporaryDirectory) {
        throw "SIGNING_TEMP_DIRECTORY_CLEANUP_FAILED: $temporaryDirectory"
    }
    $cleanupVerified = $true
}

if (-not $cleanupVerified) {
    throw "SIGNING_CLEANUP_NOT_VERIFIED"
}
$buildReceipt = [ordered]@{
    schema_version = 2
    status = "PASS"
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    source_commit = $sourceProvenance.Commit
    source_worktree = $sourceProvenance.Worktree
    source_status_sha256 = $sourceProvenance.StatusSha256
    source_tracked_diff_sha256 = $sourceProvenance.TrackedDiffSha256
    source_untracked_manifest = $sourceProvenance.UntrackedManifest
    source_untracked_manifest_sha256 = $sourceProvenance.UntrackedManifestSha256
    source_provenance_fingerprint = $sourceProvenance.Fingerprint
    source_stability = "PRE_AND_POST_BUILD_MATCH"
    temporary_acl = "OWNER_ONLY"
    plaintext_keystore_cleanup = "PASS"
    application_id = "app.autplay"
    signer_certificate_sha256 = $expectedFingerprint
    artifacts = $artifactRecords
    update_install = "PENDING_TEST_DEVICE"
    secrets = "NOT_RECORDED"
}
$receiptPath = Join-Path $evidenceDirectory "signed-build-receipt.json"
Write-Utf8FileAtomically `
    -LiteralPath $receiptPath `
    -Value "$(ConvertTo-Json $buildReceipt -Depth 8)`n"
Write-Output "SIGNED_RELEASE_BUILD_PASS"
Write-Output "Evidence: $evidenceDirectory"
