import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "build-pa3-signed-release.ps1"
EVIDENCE_INDEX_PATH = REPOSITORY_ROOT / "docs" / "release" / "PA3_ANDROID_SIGNING_EVIDENCE.json"


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_plaintext_keystore_directory_is_owner_only_before_decryption() -> None:
    script = _script()

    protect_directory = script.index("Set-OwnerOnlyDirectoryAcl -LiteralPath $temporaryDirectory")
    decrypt = script.index("$decrypt = Invoke-NativeCapture $agePath")
    protect_plaintext = script.index("Set-OwnerOnlyFileAcl -LiteralPath $temporaryKeystore")
    inspect_plaintext = script.index("$keyListing = Invoke-NativeCapture $keytool")

    assert "SetAccessRuleProtection($true, $false)" in script
    assert protect_directory < decrypt < protect_plaintext < inspect_plaintext
    assert "SIGNING_TEMP_ACL_NOT_OWNER_ONLY" in script


def test_pass_receipt_is_atomic_and_follows_verified_cleanup() -> None:
    script = _script()

    cleanup = script.index("$cleanupVerified = $true")
    receipt = script.index("$receiptPath = Join-Path $evidenceDirectory")
    atomic_write = script.index("Write-Utf8FileAtomically `\n    -LiteralPath $receiptPath")
    success = script.index('Write-Output "SIGNED_RELEASE_BUILD_PASS"')

    assert cleanup < receipt < atomic_write < success
    assert 'plaintext_keystore_cleanup = "PASS"' in script
    assert "[IO.File]::Move($temporaryPath, $fullPath)" in script


def test_dirty_source_provenance_covers_untracked_content_and_build_drift() -> None:
    script = _script()

    pre_build = script.index(
        "$sourceProvenance = Get-StableSourceProvenance -Git $git -RepoRoot $repoRoot"
    )
    build = script.index("Invoke-GradleRelease $gradle")
    post_build = script.index("$postBuildSourceProvenance = Get-StableSourceProvenance")

    assert '"ls-files", "--others", "--exclude-standard", "-z"' in script
    assert "sha256 = Get-Sha256Hex $fullPath" in script
    assert pre_build < build < post_build
    assert "SOURCE_PROVENANCE_DRIFT_DETECTED" in script
    assert "source_untracked_manifest = $sourceProvenance.UntrackedManifest" in script
    assert 'source_stability = "PRE_AND_POST_BUILD_MATCH"' in script


def test_signing_evidence_index_scopes_update_proof_to_exact_apk_hashes() -> None:
    evidence = json.loads(EVIDENCE_INDEX_PATH.read_text(encoding="utf-8"))

    update_proof = evidence["update_proof_generation"]
    latest_build = evidence["latest_interactive_build_generation"]
    claims = evidence["claims"]
    expected_update_artifacts = [
        {
            "filename": "autplay-0.3.0-pa3.1-signed.apk",
            "version_code": 3,
            "sha256": ("f69cca1e3ba77e89265939cd7c42455f95dd036de3220fcac4178c7c9e7407f5"),
        },
        {
            "filename": "autplay-0.3.0-pa3.2-signed.apk",
            "version_code": 4,
            "sha256": ("0c174e8bfd3618c0d4550913f2ad992bb8b8cafd686c1dcc3988e8898ea0e4d2"),
        },
    ]
    expected_latest_artifacts = [
        {
            "filename": "autplay-0.3.0-pa3.1-signed.apk",
            "version_code": 3,
            "sha256": ("52ba5492b73502d94f36ec95548a95429876eebe71bedb83dd8d6c3a8171b1a7"),
        },
        {
            "filename": "autplay-0.3.0-pa3.2-signed.apk",
            "version_code": 4,
            "sha256": ("45d8a185fe51ef0b79770953b9864e83574d4a4fa169fe1e45ad662d4b89661e"),
        },
    ]

    assert update_proof["evidence_run"] == "20260902T065415Z"
    assert update_proof["status"] == "PASS"
    assert update_proof["update_receipt_sha256"] == (
        "cc2075943db025d1d421b5fea1b3416687961bd29906028d1c2e8477b5131c8b"
    )
    assert update_proof["artifacts"] == expected_update_artifacts
    assert latest_build["evidence_run"] == "20260902T084853Z"
    assert latest_build["artifacts"] == expected_latest_artifacts
    assert latest_build["update_install"] == "NOT_REPEATED"
    assert claims["exact_update_proof_artifact_set"] == update_proof["evidence_run"]
    assert claims["latest_interactive_build_artifact_set"] == latest_build["evidence_run"]
    assert claims["update_proof_transferred_to_different_apk_hashes"] is False
    assert {artifact["sha256"] for artifact in update_proof["artifacts"]}.isdisjoint(
        {artifact["sha256"] for artifact in latest_build["artifacts"]}
    )
    assert evidence["current_signing_workflow"]["interactive_build_status"] == (
        "NOT_RUN_AFTER_FINAL_HARDENING"
    )
