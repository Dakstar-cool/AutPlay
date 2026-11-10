package app.autplay.desktop.security

import app.autplay.application.devicecompatibility.ProfileDeviceContractV2
import app.autplay.application.devicecompatibility.SelfDeviceContractV2
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2
import app.autplay.application.securestate.*
import java.util.concurrent.CompletableFuture

/** Closed typed D1 entry points. ROOT proofs continue through UnlockedPortableAccount. */
public class DesktopDeviceSigner(private val secure: DesktopSecureStateOwner) {
    public fun sign(snapshot: SecureControl, owner: SecureRecordOwner,
        prepared: ProfileDeviceContractV2.PreparedRequest): CompletableFuture<ProfileDeviceContractV2.SignedCommand> {
        SecureJournalRestoration.requireScope(prepared, owner)
        secureRequire(prepared.platform.name == "MACOS" && prepared.keyThumbprintSha256 == owner.keyThumbprint, SecureStateStatus.CONTEXT_CHANGED)
        val operation = when (prepared.operation) {
            ProfileDeviceContractV2.Operation.ENROLLMENT_EXCHANGE -> DeviceProofOperation.ENROLLMENT_EXCHANGE
            ProfileDeviceContractV2.Operation.ADMISSION_SUBMIT -> DeviceProofOperation.ADMISSION_SUBMIT
            ProfileDeviceContractV2.Operation.ADMISSION_POLL -> DeviceProofOperation.ADMISSION_POLL
            ProfileDeviceContractV2.Operation.ADMISSION_RECOVER -> DeviceProofOperation.ADMISSION_RECOVER
            ProfileDeviceContractV2.Operation.ADMISSION_EXCHANGE -> DeviceProofOperation.ADMISSION_EXCHANGE
            ProfileDeviceContractV2.Operation.TRUSTED_CHALLENGE -> DeviceProofOperation.TRUSTED_CHALLENGE
            ProfileDeviceContractV2.Operation.TRUSTED_EXCHANGE -> DeviceProofOperation.TRUSTED_EXCHANGE
            ProfileDeviceContractV2.Operation.SESSION_ROTATE -> DeviceProofOperation.SESSION_ROTATE
        }
        val digest = prepared.requestDigestCopy()
        return try { secure.sign(snapshot, owner, operation, digest).thenApply { proof ->
            try { ProfileDeviceContractV2.complete(prepared, proof) } finally { proof.fill(0) }
        } } finally { digest.fill(0) }
    }
    public fun sign(snapshot: SecureControl, owner: SecureRecordOwner,
        prepared: SelfDeviceContractV2.PreparedRequest): CompletableFuture<SelfDeviceContractV2.WireRequest> {
        SecureJournalRestoration.requireScope(prepared, owner)
        secureRequire(prepared.platform.name == "MACOS" && prepared.keyThumbprintSha256 == owner.keyThumbprint, SecureStateStatus.CONTEXT_CHANGED)
        val operation = when (prepared.operation) {
            SelfDeviceContractV2.Operation.CLAIM -> DeviceProofOperation.SELF_CLAIM
            SelfDeviceContractV2.Operation.POLL -> DeviceProofOperation.SELF_POLL
            SelfDeviceContractV2.Operation.EXCHANGE -> DeviceProofOperation.SELF_EXCHANGE
            else -> throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        }
        val digest = prepared.requestDigestCopy()
        return try { secure.sign(snapshot, owner, operation, digest).thenApply { proof ->
            try { SelfDeviceContractV2.complete(prepared, proof) } finally { proof.fill(0) }
        } } finally { digest.fill(0) }
    }
    public fun sign(snapshot: SecureControl, owner: SecureRecordOwner,
        prepared: RecoveryDeletionDeviceContractV2.PreparedRequest): CompletableFuture<RecoveryDeletionDeviceContractV2.SignedCommand> {
        SecureJournalRestoration.requireScope(prepared, owner)
        secureRequire(prepared.platform.name == "MACOS" && prepared.keyThumbprintSha256 == owner.keyThumbprint, SecureStateStatus.CONTEXT_CHANGED)
        val operation = when (prepared.bodyKind) {
            RecoveryDeletionDeviceContractV2.BodyKind.RECOVERY_PREVIEW -> DeviceProofOperation.RECOVERY_PREVIEW
            RecoveryDeletionDeviceContractV2.BodyKind.RECOVERY_COMMIT -> DeviceProofOperation.RECOVERY_COMMIT
            RecoveryDeletionDeviceContractV2.BodyKind.DELETION_REQUEST -> DeviceProofOperation.DELETION_REQUEST
            RecoveryDeletionDeviceContractV2.BodyKind.DELETION_PREVIEW -> DeviceProofOperation.DELETION_PREVIEW
            RecoveryDeletionDeviceContractV2.BodyKind.DELETION_CANCEL -> DeviceProofOperation.DELETION_CANCEL
        }
        val digest = prepared.requestDigestCopy()
        return try { secure.sign(snapshot, owner, operation, digest).thenApply { proof ->
            try { RecoveryDeletionDeviceContractV2.complete(prepared, proof) } finally { proof.fill(0) }
        } } finally { digest.fill(0) }
    }
}
