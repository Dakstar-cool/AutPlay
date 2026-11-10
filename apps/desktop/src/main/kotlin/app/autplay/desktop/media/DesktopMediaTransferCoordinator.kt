package app.autplay.desktop.media

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

internal data class DesktopTransferView(val state: String = "IDLE", val settledBytes: Long = 0,
    val totalBytes: Long? = null, val failure: MediaCode? = null)

/** Foreground logical transfer; a fifteen minute execution is never a whole-download limit. */
internal class DesktopMediaTransferCoordinator(
    private val journal: DesktopAccountMedia,
    private val evidence: DesktopMediaJournalAdapter,
    private val resolver: DesktopMediaSourcePort,
    private val native: DesktopMediaNativeOwner,
    private val files: DesktopDownloadOwner,
    private val resources: DesktopMediaResourceOwner,
    private val work: DesktopMediaWorkOwner,
    private val lease: (AccountLocalCapture) -> AccountLease,
    private val selectedForRequest: () -> AccountLease,
    private val capture: suspend (MediaId, MediaId, Long) -> SelectionCapture.VaultNetwork,
    private val clock: DesktopMediaResolutionClock,
) {
    private class Request(val track: MediaId, val storageClass: String, val id: MediaId,
        val explicitResume: Boolean) { override fun toString(): String = "DownloadRequest(<redacted>)" }
    private val requests = Channel<Request>(8)
    private val replayBudgets = ConcurrentHashMap<MediaId, Boolean>()
    private val stopped = AtomicBoolean(false)
    private val activeStop = AtomicBoolean(false)
    @Volatile private var stopReason: Wire.RetireReason? = null
    @Volatile private var journalStopReason: String = "ERROR"
    private val states = MutableStateFlow(DesktopTransferView())
    private val jobGate = Any()
    private var idle = kotlinx.coroutines.CompletableDeferred<Unit>().apply { complete(Unit) }
    private var currentRequest: Request? = null
    val state = states.asStateFlow()
    @Volatile private var activeNative: DesktopMediaNativeOwner.Source? = null

    init {
        work.scope.launch { files.progress.collect { progress ->
            if (progress != null && activeNative?.transfer?.transferId == progress.transferId && states.value.state == "DOWNLOADING")
                states.value = DesktopTransferView("DOWNLOADING", progress.settledBytes, progress.totalBytes)
        } }
        native.replay401UsedFor = { intent -> intent is SourceIntent.BoundVaultDownload && replayBudgets[intent.intentId] == true }
        work.scope.launch {
            for (request in requests) {
                val completion = synchronized(jobGate) {
                    if (stopped.get()) null else kotlinx.coroutines.CompletableDeferred<Unit>().also {
                        idle = it; activeStop.set(false); stopReason = null; journalStopReason = "ERROR"
                        currentRequest = request
                    }
                } ?: continue
                try { execute(request) }
                catch (failure: Exception) {
                    states.value = DesktopTransferView("PAUSED", states.value.settledBytes, states.value.totalBytes,
                        (failure as? MediaSourceException)?.failure?.code ?: MediaCode.RECONCILIATION_REQUIRED)
                }
                finally {
                    activeNative = null; replayBudgets.remove(request.id)
                    synchronized(jobGate) { currentRequest = null; completion.complete(Unit) }
                }
            }
        }
    }
    fun download(trackId: MediaId, storageClass: String): MediaId {
        mediaCheck(storageClass in setOf("PINNED", "USER_DOWNLOAD") && !stopped.get(), MediaCode.INTENT_STALE, MediaStage.WRITE)
        val intent = MediaId.random()
        mediaCheck(requests.trySend(Request(trackId, storageClass, intent, false)).isSuccess, MediaCode.CAPACITY_EXCEEDED, MediaStage.WRITE)
        return intent
    }
    fun pauseForLifecycle(reason: Wire.RetireReason = Wire.RetireReason.SLEEP, journalReason: String = "SLEEP"): Unit {
        require(journalReason in setOf("SLEEP", "QUIT", "OWNER"))
        synchronized(jobGate) { stopped.set(true); activeStop.set(true); stopReason = reason; journalStopReason = journalReason }
        activeNative?.let { native.retire(it, reason) }
    }
    fun resume(trackId: MediaId, intentId: MediaId): Unit {
        mediaCheck(!stopped.get() && requests.trySend(Request(trackId, "USER_DOWNLOAD", intentId, true)).isSuccess,
            MediaCode.CAPACITY_EXCEEDED, MediaStage.HANDOFF)
    }
    fun cancelCurrent(): Unit {
        stopReason = Wire.RetireReason.CANCEL; journalStopReason = "USER"; activeStop.set(true)
        activeNative?.let { native.retire(it, Wire.RetireReason.CANCEL) }
    }
    suspend fun awaitIdle(): Unit = withContext(NonCancellable) { synchronized(jobGate) { idle }.await() }
    fun ownsIntent(id: MediaId): Boolean = synchronized(jobGate) { currentRequest?.id == id && !idle.isCompleted }
    fun allowExplicitContinuation(): Unit = synchronized(jobGate) {
        mediaCheck(idle.isCompleted, MediaCode.DRAIN_UNCONFIRMED, MediaStage.HANDOFF); stopped.set(false)
    }

    suspend fun reconcileFailedSource(source: DesktopMediaNativeOwner.Source): Unit {
        mediaCheck(stopped.get() && synchronized(jobGate) { idle.isCompleted } && native.exceptionalSourceSettled(source),
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        files.reconcileFailedSource(source.key)
        resources.reconcileExceptionalSource(source)
        if (source.intent !is SourceIntent.BoundVaultDownload) return
        val captured = source.capture as? SelectionCapture.VaultNetwork ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.DRAIN)
        reconcileFailedOperation(captured, source.intent)
    }
    suspend fun reconcileFailedOperation(captured: SelectionCapture.VaultNetwork, sourceIntent: SourceIntent): Unit {
        mediaCheck(stopped.get() && synchronized(jobGate) { idle.isCompleted }, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        val intent = sourceIntent as? SourceIntent.BoundVaultDownload ?: return
        val selected = lease(captured.network.local)
        val descriptor = journal.readIntent(selected, intent.intentId) ?: throw mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.DRAIN)
        val attempt = journal.readAttempt(selected, intent.intentId, descriptor.currentAttempt)
            ?: throw mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.DRAIN)
        mediaCheck(attempt.operationId == captured.fence.operationId && attempt.sourceGeneration == captured.fence.playbackGeneration,
            MediaCode.INTENT_STALE, MediaStage.DRAIN)
        if (attempt.state in setOf("PREPARED", "WAITING", "RUNNING", "DRAINING"))
            journal.settleAttempt(selected, MediaId.random(), attempt.record.revision,
                attempt.copy(record = attempt.record.copy(revision = DesktopMediaLimits.next(attempt.record.revision)),
                    state = "UNCERTAIN", stopReason = "UNCERTAIN"))
    }

    private suspend fun execute(request: Request): Unit {
        var operation = MediaId.random()
        var selected = selectedForRequest()
        var intent = if (request.explicitResume) {
            val existing = journal.readIntent(selected, request.id) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.HANDOFF)
            mediaCheck(existing.localTrackRefId == request.track && existing.coarseState != "COMPLETED", MediaCode.INTENT_STALE, MediaStage.HANDOFF)
            journal.setDesiredState(selected, MediaId.random(), request.id, existing.record.revision, "DOWNLOAD")
            journal.readIntent(selected, request.id) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.HANDOFF)
        } else {
            journal.requestDownload(selected, MediaId.random(), request.id, request.track, request.storageClass)
        }
        native.start()
        try {
            while (!native.downloadNetworkAllowed()) {
                mediaCheck(!stopped.get() && !activeStop.get(), MediaCode.CANCELLED, MediaStage.RESOLVE)
                states.value = DesktopTransferView("WAITING_NETWORK")
                delay(250)
            }
        } finally {
            if (stopReason == Wire.RetireReason.CANCEL) {
                val latest = journal.readIntent(selected, request.id)
                if (latest != null && latest.coarseState != "COMPLETED")
                    journal.setDesiredState(selected, MediaId.random(), request.id, latest.record.revision, "CANCEL")
            }
        }
        mediaCheck(!stopped.get() && !activeStop.get(), MediaCode.CANCELLED, MediaStage.RESOLVE)
        var captured = capture(request.track, operation, 1)
        selected = lease(captured.network.local)
        if (intent.target == null) {
            val observation = resolver.readSourceSnapshot(captured, captured.network.projection.userTrackRefId)
            captured = observation.capture
            selected = lease(captured.network.local)
            intent = evidence.bindRequested(captured, intent, observation)
        }
        val original = journal.readEvidence(selected, requireNotNull(intent.target).sourceEvidenceId)
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        var previous: MediaAttempt? = if (request.explicitResume && intent.currentAttempt > 0)
            journal.readAttempt(selected, request.id, intent.currentAttempt) else null
        previous?.let { retained ->
            mediaCheck(resources.releaseOperation(retained.operationId) == MediaHandleRelease.RELEASED,
                MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
            resources.reconcileRetainedAttempt(captured, intent, retained)
        }
        val prefix = previous?.prefixId ?: MediaId.random()
        var explicit = request.explicitResume
        while (!stopped.get() && !activeStop.get()) {
            val number = (previous?.attempt ?: 0) + 1
            mediaCheck(number in 1..DesktopMediaLimits.MAX_INTEGER, MediaCode.CAPACITY_EXCEEDED, MediaStage.HANDOFF)
            if (previous != null) {
                intent = journal.readIntent(selected, request.id) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.HANDOFF)
                val wait = if (explicit) 0 else
                    (intent.nextEligibleAtMs ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.HANDOFF)) - System.currentTimeMillis()
                foregroundDelay(maxOf(1_000, wait))
                while (!native.downloadNetworkAllowed()) {
                    states.value = DesktopTransferView("WAITING_NETWORK", previous.confirmedPrefixBytes, previous.totalBytes)
                    foregroundDelay(250)
                }
                mediaCheck(!stopped.get() && !activeStop.get(), MediaCode.CANCELLED, MediaStage.HANDOFF)
                operation = MediaId.random()
                captured = capture(request.track, operation, number)
                selected = lease(captured.network.local)
                mediaCheck(captured.network.local.localOwner == original.owner && captured.network.local.scopeId == original.scopeId,
                    MediaCode.OWNER_STALE, MediaStage.HANDOFF)
            }
            val started = System.currentTimeMillis()
            var attempt = MediaAttempt(AccountMediaRecord(selected.storeId, selected.scopeId, 1), request.id, number,
                selected.bootId, captured.fence.playbackGeneration, intent.record.revision,
                DesktopMediaLimits.next(previous?.attemptLeaseRevision ?: 0), original.evidenceId, null, operation,
                null, null, null, null, null, prefix, previous?.confirmedPrefixBytes ?: 0, started, started + 900_000,
                0, "PREPARED", null, if (explicit) false else previous?.replay401Used ?: false,
                if (explicit) false else previous?.reconnectUsed ?: false,
                previous?.let { if (explicit || it.settledBytes > 0) 0 else it.consecutiveNoProgress + 1 } ?: 0)
            mediaCheck(attempt.consecutiveNoProgress <= 3, MediaCode.NETWORK_TIMEOUT, MediaStage.HANDOFF)
            attempt = journal.prepareAttempt(selected, operation, intent.record.revision, previous?.attemptLeaseRevision, attempt)
            explicit = false
            intent = journal.readIntent(selected, request.id) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.HANDOFF)
            replayBudgets[request.id] = attempt.replay401Used
            val sourceIntent = SourceIntent.BoundVaultDownload(request.id, intent.record.revision, original.evidenceId,
                original, MediaId.random(), captured.network.projection)
            var acquired: VaultPermit? = null
            val slot: DesktopMediaNativeOwner.Source
            try {
                val resolved = resolver.resolve(captured, sourceIntent)
                acquired = (resolved as? SourceResolution.Allow)?.handle as? VaultPermit ?: throw when (resolved) {
                    is SourceResolution.Deny -> MediaSourceException(resolved.failure)
                    is SourceResolution.Unknown -> MediaSourceException(resolved.failure)
                    else -> mediaFailure(MediaCode.OFFLINE, MediaStage.RESOLVE)
                }
                mediaCheck(!stopped.get() && !activeStop.get(), MediaCode.CANCELLED, MediaStage.RESOLVE)
                slot = native.findSource(acquired.copyId, captured) ?: throw mediaFailure(MediaCode.RESOURCE_STALE, MediaStage.RANGE)
            } catch (failure: Exception) {
                withContext(NonCancellable) {
                    acquired?.retire()
                    val nativeSettled = native.releaseOperation(operation) == MediaHandleRelease.RELEASED
                    val resourceSettled = resources.releaseOperation(operation) == MediaHandleRelease.RELEASED
                    val handleSettled = acquired?.awaitReleased() != MediaHandleRelease.FAILED_RETAINED
                    if (nativeSettled && resourceSettled && handleSettled) settleStopped(selected, request.id, number, attempt.confirmedPrefixBytes)
                }
                throw failure
            }
            val handle = requireNotNull(acquired)
            captured = handle.capture
            selected = lease(captured.network.local)
            activeNative = slot
            val transfer = RemoteMediaTransferCapture(slot.key, UUID.randomUUID(), attempt.sourceGeneration, intent.record.revision,
                attempt.attemptLeaseRevision, attempt.confirmedPrefixBytes)
            val beginPrefix = attempt.confirmedPrefixBytes
            var fileOpened = false
            var fullBytes = false
            var published = false
            try {
                attempt = attempt.copy(record = attempt.record.copy(revision = DesktopMediaLimits.next(attempt.record.revision)),
                    admissionEvidenceId = handle.evidence.evidenceId, resourceActivationId = handle.resource.activationId,
                    resourceGeneration = handle.resource.generation, etag = handle.representation.strongEtag,
                    digestSha256 = handle.representation.digestSha256, totalBytes = handle.representation.totalBytes, state = "RUNNING")
                journal.updateAttempt(selected, MediaId.random(), attempt.record.revision - 1, attempt)
                files.openAttempt(DesktopDownloadOwner.AttemptInput(captured, selected, intent, attempt, original, handle.representation, transfer))
                fileOpened = true
                states.value = DesktopTransferView("DOWNLOADING", beginPrefix, handle.representation.totalBytes)
                native.startTransfer(slot, transfer)
                // Leave one control/chunk deadline before the hard execution boundary. Timeout
                // cancels only this await; native/frame/file owners remain until real settlement.
                val end = withTimeoutOrNull(maxOf(1, attempt.deadlineAtMs - System.currentTimeMillis() - 15_000)) {
                    slot.transferred.await()
                }
                selected = lease((slot.capture as? SelectionCapture.VaultNetwork
                    ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.DRAIN)).network.local)
                fullBytes = end?.outcome == Wire.TransferOutcome.DELIVERED && end.nextOffset == handle.representation.totalBytes
                if (end != null && !fullBytes && !activeStop.get()) throw mediaFailure(
                    end.code?.let { MediaCode.valueOf(it.name) } ?: MediaCode.NETWORK_DROPPED, MediaStage.RANGE)
                native.retire(slot, if (fullBytes) Wire.RetireReason.SELECTION else Wire.RetireReason.ATTEMPT_BOUNDARY)
                mediaCheck(native.release(slot) == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                files.settle(transfer, fullBytes)
                attempt = journal.readAttempt(selected, request.id, number) ?: throw mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.DRAIN)
                attempt = update(selected, attempt, "DRAINING", if (fullBytes) "COMPLETE" else "ATTEMPT_BOUNDARY",
                    settled = attempt.confirmedPrefixBytes - beginPrefix, replay = slot.rejected401)
                if (fullBytes) {
                    resources.delivered(operation)
                    attempt = update(selected, attempt, "DELIVERED", "COMPLETE")
                    val local = (slot.capture as? SelectionCapture.VaultNetwork)?.network?.local ?: captured.network.local
                    val copy = files.publish(transfer, local)
                    published = copy.copy.publicationState == "COMMITTED"
                    states.value = DesktopTransferView("COMPLETED", copy.copy.totalBytes, copy.copy.totalBytes)
                    // A failed server release cannot revoke or destroy an already published LOCAL
                    // copy. Its original resource owner stays retained for later reconciliation.
                    handle.retire(); handle.awaitReleased()
                    return
                }
                mediaCheck(!stopped.get() && !activeStop.get(),
                    MediaCode.CANCELLED, MediaStage.HANDOFF)
                handle.retire()
                mediaCheck(handle.awaitReleased() == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.HANDOFF)
                files.closeAttempt(transfer); fileOpened = false
                if (attempt.settledBytes == 0L && attempt.consecutiveNoProgress >= 3) {
                    val paused = attempt.copy(record = attempt.record.copy(revision = DesktopMediaLimits.next(attempt.record.revision)),
                        state = "FAILED", stopReason = "NETWORK")
                    journal.settleAttempt(selected, MediaId.random(), attempt.record.revision, paused)
                    throw mediaFailure(MediaCode.NETWORK_TIMEOUT, MediaStage.HANDOFF)
                }
                attempt = update(selected, attempt, "YIELDED", "ATTEMPT_BOUNDARY")
                previous = attempt
                states.value = DesktopTransferView("QUEUED", attempt.confirmedPrefixBytes, attempt.totalBytes)
            } finally {
                withContext(NonCancellable) {
                    if (!published) {
                        native.retire(slot, stopReason ?: Wire.RetireReason.ERROR)
                        val drained = native.release(slot) == MediaHandleRelease.RELEASED
                        if (fileOpened && drained) {
                            try { files.settle(transfer, fullBytes); files.closeAttempt(transfer) }
                            catch (_: Exception) { /* Failed write/publication remains at the original owner. */ }
                        }
                        handle.retire()
                        val serverReleased = handle.awaitReleased() == MediaHandleRelease.RELEASED
                        if (drained && serverReleased && previous?.attempt != number) {
                            settleStopped(selected, request.id, number, beginPrefix)
                        }
                    }
                }
            }
        }
    }

    private suspend fun foregroundDelay(durationMs: Long): Unit {
        val until = clock.monotonicMs() + durationMs
        do {
            mediaCheck(!stopped.get() && !activeStop.get(), MediaCode.CANCELLED, MediaStage.HANDOFF)
            delay(minOf(250, maxOf(1, until - clock.monotonicMs())))
        } while (clock.monotonicMs() < until)
    }

    private suspend fun settleStopped(lease: AccountLease, intentId: MediaId, number: Long, beginPrefix: Long): Unit {
        try {
            val old = journal.readAttempt(lease, intentId, number) ?: return
            val canceled = stopReason == Wire.RetireReason.CANCEL
            if (old.state in setOf("PREPARED", "WAITING", "RUNNING", "DRAINING"))
                journal.settleAttempt(lease, MediaId.random(), old.record.revision,
                    old.copy(record = old.record.copy(revision = DesktopMediaLimits.next(old.record.revision)),
                        state = if (canceled) "CANCELED" else "FAILED", stopReason = journalStopReason,
                        settledBytes = maxOf(old.settledBytes, old.confirmedPrefixBytes - beginPrefix)))
            if (canceled) {
                val intent = journal.readIntent(lease, intentId) ?: return
                if (intent.coarseState != "COMPLETED") journal.setDesiredState(lease, MediaId.random(), intentId, intent.record.revision, "CANCEL")
            }
        } catch (_: Exception) { /* Actual J result is uncertain; retain the original attempt for reconciliation. */ }
    }

    private suspend fun update(lease: AccountLease, old: MediaAttempt, state: String, reason: String,
        settled: Long = old.settledBytes, replay: Boolean = old.replay401Used): MediaAttempt {
        val next = old.copy(record = old.record.copy(revision = DesktopMediaLimits.next(old.record.revision)),
            state = state, stopReason = reason, settledBytes = settled, replay401Used = replay)
        journal.updateAttempt(lease, MediaId.random(), old.record.revision, next)
        return journal.readAttempt(lease, old.intentId, old.attempt) ?: throw mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.WRITE)
    }
}
