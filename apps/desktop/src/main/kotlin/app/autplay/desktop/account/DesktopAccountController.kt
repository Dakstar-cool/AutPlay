package app.autplay.desktop.account

import app.autplay.desktop.security.*
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean

/** Host entry point. The projection MUST be the actual credential-correlated J binding adapter. */
public class DesktopAccountController private constructor(
    public val accounts: DesktopAccountRuntime,
    public val profiles: DesktopProfileController,
    public val invitations: DesktopEnrollmentInvitationController,
    public val admission: DesktopAdmissionController,
    public val selfPairing: DesktopSelfPairingController,
    public val serverRecovery: DesktopServerRecoveryController,
    public val portableRecovery: DesktopPortableRecoveryController,
    public val recoveryFiles: DesktopRecoveryFiles,
) : AutoCloseable {
    private val closed = AtomicBoolean(false)
    /** Reopens the exact protected action; confirmations and secret reveals remain explicit UI actions. */
    public fun resume(handle: DesktopPendingHandle): CompletionStage<Unit> = accounts.requireCurrent(handle).thenCompose {
        when (handle.purpose) {
            DesktopPendingPurpose.BINDING -> accounts.restoreBinding(requireNotNull(handle.profile))
            DesktopPendingPurpose.SESSION_GRANT -> accounts.resumePending(handle.slot)
            DesktopPendingPurpose.ROOT_ROTATION -> portableRecovery.resumeRotation()
            DesktopPendingPurpose.ADMISSION, DesktopPendingPurpose.TRUSTED -> admission.restoreProtectedRequest(handle.slot)
            DesktopPendingPurpose.SELF_SOURCE -> selfPairing.restoreProtectedCeremony(source = true)
            DesktopPendingPurpose.SELF_RECIPIENT -> selfPairing.restoreProtectedCeremony(source = false)
            DesktopPendingPurpose.RECOVERY_SOURCE -> serverRecovery.resumeSourceConfiguration()
            DesktopPendingPurpose.RECOVERY_PREVIEW -> serverRecovery.restoreRecipientPreview()
            DesktopPendingPurpose.DELETION_SOURCE -> accounts.resumePending(handle.slot)
            DesktopPendingPurpose.DELETION_PREVIEW -> serverRecovery.restoreRecipientPreview(deletionCancellation = true)
            DesktopPendingPurpose.PROFILE_COMMAND -> profiles.resumeProtectedCommand(handle.slot)
            DesktopPendingPurpose.UNSUPPORTED -> CompletableFuture.failedFuture(AccountControlException(AccountControlFailure.UNSUPPORTED))
        }
    }
    /** Sleep/switch needs a new qualified owner on resume; this never resurrects the retired one. */
    public fun retire() { accounts.retire(); selfPairing.close(); admission.close(); serverRecovery.close(); recoveryFiles.close() }
    override fun close() { if (closed.compareAndSet(false, true)) { retire(); accounts.close() } }
    /** Owned command/file/HTTP drain after close. Then host closes J; only then drains the shared K owner. */
    public fun drain(): CompletionStage<Unit> = CompletableFuture.allOf(
        accounts.drain().toCompletableFuture(), recoveryFiles.drain().toCompletableFuture()).thenApply { Unit }

    public companion object {
        /** Projection must use actual J prepareScope/activateScope under its real K authority lifetime. */
        public fun create(secure: DesktopSecureStateOwner, projection: SecureBindingProjection,
            deviceName: String, applicationVersion: String): DesktopAccountController {
            val custody = DesktopSecureCustody(secure, projection)
            val runtime = DesktopAccountRuntime(secure, custody, DesktopPortableRootStore(secure),
                DesktopDeviceSigner(secure), DesktopControlTransport(), deviceName, applicationVersion, projection)
            val profiles = DesktopProfileController(runtime)
            return DesktopAccountController(runtime, profiles, DesktopEnrollmentInvitationController(runtime, profiles), DesktopAdmissionController(runtime),
                DesktopSelfPairingController(runtime, profiles), DesktopServerRecoveryController(runtime, profiles),
                DesktopPortableRecoveryController(runtime), DesktopRecoveryFiles(runtime))
        }
    }
}
