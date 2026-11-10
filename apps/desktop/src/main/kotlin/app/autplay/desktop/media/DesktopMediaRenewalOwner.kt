package app.autplay.desktop.media

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.SecureRecordOwner
import java.util.concurrent.atomic.AtomicReference

/** Exact-parent continuation after real transport/frame settlement; never waits on its invoking actor. */
internal class DesktopMediaRenewalOwner(
    private val admission: DesktopMediaAccountAdmission,
    private val native: DesktopMediaNativeOwner,
    private val http: DesktopMediaHttp,
    private val resources: DesktopMediaResourceOwner,
    private val files: DesktopDownloadOwner,
    private val persistence: DesktopMediaQueueOwner,
    private val library: DesktopMediaLibraryOwner,
    private val playback: DesktopMediaOwner,
    private val readCurrentLease: suspend () -> AccountLease,
    private val publishLease: (AccountLease?) -> Unit,
    private val failAdmission: () -> Unit,
) {
    private class Pending(val owner: SecureRecordOwner, val lease: AccountLease)
    private val pending = AtomicReference<Pending?>(null)

    suspend fun before(capture: SelectionCapture.VaultNetwork, owner: SecureRecordOwner): Unit {
        val lease = admission.lease(capture.network.local)
        val original = Pending(owner, lease)
        mediaCheck(pending.get() == null, MediaCode.OWNER_STALE, MediaStage.AUTH)
        try {
            persistence.fenceForRenewal(lease)
            resources.quiesceForRenewal(capture.fence.operationId)
            http.quiesceForRenewal()
            native.quiesceForRenewal(capture)
            playback.quiesceLocalForRenewal()
            admission.beginRenewal(lease)
            mediaCheck(pending.compareAndSet(null, original), MediaCode.OWNER_STALE, MediaStage.AUTH)
        } catch (failure: Throwable) { abort(); throw failure }
    }

    /** Called only by J2's actual SESSION_ROTATE dispatch with its protected pending owner. */
    fun matches(owner: SecureRecordOwner): Boolean {
        val p = pending.get() ?: return false
        val old = p.owner
        return !admission.isRetired() && admission.selectedLeaseOrNull() == p.lease &&
            old.boot == owner.boot && old.authorityRevision == owner.authorityRevision && old.profile == owner.profile &&
            old.account == owner.account && old.device == owner.device && old.keyThumbprint == owner.keyThumbprint &&
            old.bindingCommit == owner.bindingCommit && old.family == owner.family && old.session == owner.session &&
            old.sessionGeneration == owner.sessionGeneration && old.server == owner.server && old.identityEpoch == owner.identityEpoch &&
            old.identityThumbprint == owner.identityThumbprint && old.apiOrigin == owner.apiOrigin && old.streamOrigin == owner.streamOrigin &&
            !owner.detached && !owner.deletionVeto
    }

    suspend fun adopt(capture: SelectionCapture.VaultNetwork): SelectionCapture.VaultNetwork {
        val p = pending.get() ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.AUTH)
        try {
            val fresh = readCurrentLease()
            admission.adoptRenewal(p.lease, fresh)
            files.adoptRenewal(p.lease, fresh)
            library.adoptRenewal(p.lease, fresh)
            val result = admission.recaptureRenewal(capture)
            mediaCheck(pending.compareAndSet(p, null), MediaCode.OWNER_STALE, MediaStage.AUTH)
            persistence.completeRenewal(p.lease, fresh)
            publishLease(fresh)
            http.resumeAfterRenewal(); resources.resumeAfterRenewal()
            native.continueQuiescedRenewals()
            playback.continueAfterRenewal()
            return result
        } catch (failure: Throwable) { abort(); throw failure }
    }

    suspend fun shared(capture: SelectionCapture.VaultNetwork): SelectionCapture.VaultNetwork = admission.recaptureRenewal(capture)

    fun retire(): Unit {
        if (pending.getAndSet(null) != null) { persistence.abortRenewal(); publishLease(null) }
    }

    fun abort(): Unit {
        pending.set(null); persistence.abortRenewal(); publishLease(null); failAdmission()
    }
}
