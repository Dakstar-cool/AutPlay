package app.autplay.desktop.media

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.SecureRecordOwner
import app.autplay.desktop.account.DesktopAccountRuntime
import app.autplay.desktop.security.DesktopProtectedMediaState
import app.autplay.desktop.security.MacSecureStateIdentity
import java.nio.file.Path
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext

/** Allocate and retain before open. Every acquired lifetime remains reachable on factory failure. */
internal class DesktopMediaOpenAttempt {
    internal var work: DesktopMediaWorkOwner? = null
    internal var admission: DesktopMediaAccountAdmission? = null
    internal var native: DesktopMediaNativeOwner? = null
    internal var files: DesktopDownloadOwner? = null
    internal var persistence: DesktopMediaQueueOwner? = null
    internal var playback: DesktopMediaOwner? = null
    internal var downloads: DesktopMediaTransferCoordinator? = null
    internal var runtime: DesktopMediaRuntime? = null
    internal var channel: java.nio.channels.FileChannel? = null
    internal var lock: java.nio.channels.FileLock? = null
    private var released = false

    suspend fun close(): MediaHandleRelease = withContext(NonCancellable) {
        if (released) return@withContext MediaHandleRelease.RELEASED
        try {
            val complete = runtime
            if (complete != null) complete.close() else {
                admission?.retire()
                downloads?.pauseForLifecycle(RemoteMediaProtocol.RetireReason.ERROR, "QUIT")
                downloads?.awaitIdle()
                val owner = playback
                if (owner != null) owner.close() else {
                    persistence?.close()
                    native?.close()?.let { actual -> mediaCheck(actual.kind == RemoteMediaReleaseKind.DRAINED,
                        MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN) }
                }
                work?.let { mediaCheck(it.close() == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN) }
                val fileOwner = files
                if (fileOwner != null) mediaCheck(fileOwner.close() == MediaHandleRelease.RELEASED,
                    MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                else { lock?.let { if (it.isValid) it.release() }; channel?.close() }
                work?.finish()
            }
            released = true
            MediaHandleRelease.RELEASED
        } catch (_: Exception) { MediaHandleRelease.FAILED_RETAINED }
    }
}

/**
 * Concrete M1 graph. Main owns process lifetime, K/J bootstrap and the common admission barrier.
 * Account mode stays unavailable until the actual protected J bridge/readback fence is supplied;
 * this graph does not manufacture that missing authority from an AccountLease or cached SQL.
 */
internal class DesktopMediaRuntime private constructor(
    val playback: DesktopMediaOwner,
    val downloads: DesktopMediaTransferCoordinator,
    val admission: DesktopMediaAccountAdmission,
    val copies: DesktopDownloadOwner,
    val library: DesktopMediaLibraryOwner,
    private val native: DesktopMediaNativeOwner,
    private val resources: DesktopMediaResourceOwner,
    private val work: DesktopMediaWorkOwner,
    private val profiles: DesktopMediaProfileOwner,
    private val renewals: DesktopMediaRenewalOwner,
) {
    val network get() = native.networkState
    suspend fun setMeteredDownloads(enabled: Boolean): Unit = native.setMeteredDownloads(enabled)
    fun acceptsRenewal(owner: SecureRecordOwner): Boolean = renewals.matches(owner)
    suspend fun selectAccount(lease: AccountLease, queueId: MediaId? = null): Unit {
        retireAdmissions()
        playback.awaitQuiescent(); downloads.awaitIdle(); playback.awaitPersistence()
        admission.adoptLease(lease)
        library.select(lease, queueId)
        playback.allowExplicitContinuation(); downloads.allowExplicitContinuation()
    }
    suspend fun selectStandalone(): Unit {
        retireAdmissions()
        playback.awaitQuiescent(); downloads.awaitIdle(); playback.awaitPersistence()
        admission.selectStandalone(); library.standalone()
        playback.allowExplicitContinuation()
    }
    /** Call synchronously BEFORE the shared K/J publication gate admits a lifecycle mutation. */
    fun retireAdmissions(reason: RemoteMediaProtocol.RetireReason = RemoteMediaProtocol.RetireReason.AUTHORITY,
        journalReason: String = "OWNER"): Unit {
        renewals.retire(); copies.vetoAllPublication(); admission.retire(); playback.lifecycleVeto(); downloads.pauseForLifecycle(reason, journalReason); profiles.retire()
    }
    suspend fun close(): Unit = withContext(NonCancellable) {
        retireAdmissions(RemoteMediaProtocol.RetireReason.ERROR, "QUIT")
        downloads.awaitIdle()
        playback.close()
        mediaCheck(work.close() == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        mediaCheck(copies.close() == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        work.finish()
    }
    /** Explicit failure action. Completion permits constructing a new graph; this one never reopens. */
    suspend fun reconcileFailedForExplicitNewAttempt(): Unit = withContext(NonCancellable) {
        retireAdmissions(RemoteMediaProtocol.RetireReason.ERROR, "QUIT")
        downloads.awaitIdle()
        try { playback.close() } catch (failure: MediaSourceException) {
            if (failure.failure.code != MediaCode.DRAIN_UNCONFIRMED) throw failure
        }
        native.reconcileFailedForExplicitAttempt(downloads, playback, resources)
        mediaCheck(work.close() == MediaHandleRelease.RELEASED && copies.close() == MediaHandleRelease.RELEASED,
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        work.finish()
    }

    companion object {
        fun open(attempt: DesktopMediaOpenAttempt, canonicalBundle: Path, identity: MacSecureStateIdentity, standalone: DesktopLibraryStore,
            account: DesktopAccountStore, secureRuntime: DesktopAccountRuntime,
            protectedMedia: DesktopProtectedMediaState, initialLocal: DesktopAudioEngine?,
            createLocal: () -> DesktopAudioEngine, allowMeteredDownloads: Boolean,
            readCurrentLease: suspend () -> AccountLease, publishLease: (AccountLease?) -> Unit): DesktopMediaRuntime {
            val journal = account.media ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            val domain = account.domain ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            val catalog = account.catalog ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            mediaCheck(attempt.work == null, MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
            val work = DesktopMediaWorkOwner().also { attempt.work = it }
            val clock = object : DesktopMediaResolutionClock {
                private val start = System.nanoTime()
                override fun monotonicMs(): Long = (System.nanoTime() - start).let {
                    mediaCheck(it >= 0, MediaCode.OWNER_STALE, MediaStage.RESOLVE); it / 1_000_000
                }
            }
            val profiles = DesktopMediaProfileOwner(secureRuntime, protectedMedia)
            val admission = DesktopMediaAccountAdmission(secureRuntime, account, profiles, clock).also { attempt.admission = it }
            val credentials = DesktopMediaProtectedCredentials(secureRuntime)
            val native = DesktopMediaNativeOwner(canonicalBundle, MacRemoteMediaPackageIdentity(identity), admission,
                work, credentials, clock, allowMeteredDownloads).also { attempt.native = it }
            val files = DesktopDownloadOwner.open(journal, work, admission, attempt).also { attempt.files = it }
            files.selectedLease = admission::lease
            files.currentCapture = native::currentCapture
            native.files = files
            val local = DesktopStandaloneMediaSource(standalone, work)
            val http = DesktopMediaHttp(credentials, admission, clock, work)
            val evidence = DesktopMediaJournalAdapter(journal, work, admission::lease, admission)
            val resources = DesktopMediaResourceOwner(journal, domain, admission::lease, http, native,
                credentials, admission, work, clock)
            native.adoptCredential = resources::adoptCredential
            val rawResolver = DesktopVaultSourceResolver(admission, files, http, evidence, native, resources,
                DesktopMediaFormats, work, clock)
            val resolver = DesktopMediaResolvingSource(rawResolver, credentials, admission, native, resources)
            val sources = object : DesktopMediaActorSourcePort {
                override suspend fun capture(target: DesktopMediaActorTarget): DesktopMediaActorSource {
                    admission.actorTarget(target)
                    return if (admission.hasSelectedLease()) admission.capture(target) else local.capture(target)
                }
                override suspend fun network(local: SelectionCapture.AccountLocal,
                    intent: SourceIntent.VaultPlayback): DesktopMediaActorSource.Account = admission.network(local, intent)
            }
            val persistence = DesktopMediaQueueOwner(standalone, domain, admission, work).also { attempt.persistence = it }
            val library = DesktopMediaLibraryOwner(standalone, domain, catalog, work)
            persistence.onCommitted = library::committed
            val owner = DesktopMediaOwner(sources, resolver, native, work, persistence, initialLocal, createLocal).also { attempt.playback = it }
            val downloads = DesktopMediaTransferCoordinator(journal, evidence, resolver, native, files, resources, work,
                admission::lease, admission::selectedForRequest, admission::downloadCapture, clock).also { attempt.downloads = it }
            lateinit var graph: DesktopMediaRuntime
            val renewals = DesktopMediaRenewalOwner(admission, native, http, resources, files, persistence, library, owner,
                readCurrentLease, publishLease, { graph.retireAdmissions() })
            credentials.renewals = renewals
            graph = DesktopMediaRuntime(owner, downloads, admission, files, library, native, resources, work, profiles, renewals)
            attempt.runtime = graph
            return graph
        }
    }
}
