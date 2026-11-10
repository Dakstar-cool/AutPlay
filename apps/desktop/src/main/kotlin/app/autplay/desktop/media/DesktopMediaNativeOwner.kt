package app.autplay.desktop.media

import app.autplay.application.desktop.media.*
import app.autplay.desktop.audio.DesktopAudioOwnerArbiter
import app.autplay.desktop.audio.DesktopAudioOwnerLease
import app.autplay.desktop.audio.DesktopAudioOwnerPhase
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.nio.file.Path
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** The actual bridge authority. It never makes a queue, listen, EOF or timer decision. */
internal class DesktopMediaNativeOwner(
    bundle: Path, identity: RemoteMediaPackageIdentity,
    private val admission: DesktopMediaAccountAdmission,
    private val work: DesktopMediaWorkOwner,
    private val credentials: DesktopMediaCredentialOwner,
    private val clock: DesktopMediaResolutionClock,
    allowMetered: Boolean,
) : RemoteMediaHostAuthority, DesktopMediaHeadPort {
    private val gate = Any()
    private val commands = Mutex()
    private val installations = Mutex()
    @Volatile private var renewalFenced = false
    private val closed = AtomicBoolean(false)
    private val recovering = AtomicBoolean(false)
    @Volatile private var processRelease: RemoteMediaRelease? = null
    private var recoveryAuthority: Pair<UUID, RemoteMediaRelease>? = null
    private val slots = mutableMapOf<Wire.SourceKey, Source>()
    private var latestIntent: Wire.UpdateIntent? = null
    private var appliedIntent: Wire.IntentFence? = null
    private var network = MutableStateFlow<Wire.Network?>(null)
    val networkState: StateFlow<Wire.Network?> get() = network
    private val events = Channel<Wire.Event>(32)
    val actorEvents = Channel<Wire.Playback>(32)
    @Volatile private var startRequested = false
    private val bridge: MacRemoteMediaBridge by lazy {
        MacRemoteMediaBridge.create(bundle, identity, this, networkPolicyRevision, allowMeteredDownloads, UUID.randomUUID())
    }
    lateinit var files: DesktopDownloadOwner
    private var eventJob: Job? = null
    var onAccessRenewed: () -> Unit = {}
    lateinit var adoptCredential: suspend (Source, SelectionCapture.VaultNetwork) -> Unit

    class Source(val key: Wire.SourceKey, @Volatile var capture: SelectionCapture, val intent: SourceIntent,
        val evidence: MediaSourceEvidence?, val executionAttempt: Long, val copyId: MediaId,
        @Volatile var revision: Long, var representation: MediaRepresentation?, var resource: MediaActiveResourceLease?) {
        @Volatile var retiring = false
        @Volatile var accessWait = false
        @Volatile var pendingRevision: Long? = null
        @Volatile var credentialGeneration: Long? = null
        @Volatile var sessionGeneration: Long? = null
        var policyRevision: Long? = null
        var authorityDeadline = 0L
        var audio: DesktopAudioOwnerLease? = null
        var transfer: RemoteMediaTransferCapture? = null
        val inspected = CompletableDeferred<Wire.Inspected>()
        val transferred = CompletableDeferred<Wire.TransferEnd>()
        var release: CompletionStage<RemoteMediaSourceRelease>? = null
        var renewalId = 0L
        @Volatile var renewalDeadline = 0L
        var rejected401 = false
        var accessQuiesced = CompletableDeferred<Wire.AccessState>()
        var accessApplied = CompletableDeferred<Wire.AccessState>()
        var replacement: Wire.ReplaceAccess? = null
        var renewal: Job? = null
        var proactive: Job? = null
        var inspectionLease: MediaHeadInspection? = null
        override fun toString(): String = "NativeMediaSource(<redacted>)"
    }

    init {
        eventJob = work.scope.launch {
            for (event in events) when (event) {
                is Wire.Inspected -> source(event.source)?.inspected?.complete(event)
                is Wire.Playback -> if (!actorEvents.trySend(event).isSuccess) closeAdmission(Wire.Code.CAPACITY_EXCEEDED)
                is Wire.TransferEnd -> source(event.source)?.transferred?.complete(event)
                is Wire.AccessState -> source(event.source)?.let { slot ->
                    if (event.applied) slot.accessApplied.complete(event) else slot.accessQuiesced.complete(event)
                }
                is Wire.AuthNeeded -> source(event.source)?.let { slot ->
                    if (slot.retiring) return@let
                    if (slot.rejected401 && slot.renewalId != event.renewalId) retire(slot, Wire.RetireReason.AUTHORITY)
                    else {
                        slot.rejected401 = true
                        slot.accessWait = true
                        if (slot.renewal?.isActive != true) slot.renewal = work.scope.launch { renewAccess(slot, true, event.renewalId) }
                    }
                }
                is Wire.SourceFailure -> source(event.source)?.let { slot ->
                    slot.inspected.completeExceptionally(mediaFailure(MediaCode.valueOf(event.code.name), MediaStage.HEAD))
                    slot.transferred.completeExceptionally(mediaFailure(MediaCode.valueOf(event.code.name), MediaStage.RANGE))
                }
                is Wire.Network -> network.value = event
                else -> Unit
            }
        }
    }

    suspend fun start(): Unit { startRequested = true; bridge.start().mediaAwait() }

    fun findSource(copyId: MediaId, capture: SelectionCapture): Source? = synchronized(gate) {
        slots.values.singleOrNull { it.copyId == copyId && it.capture.fence == capture.fence && !it.retiring && it.resource != null }
    }
    suspend fun releaseOperation(operation: MediaId): MediaHandleRelease = withContext(NonCancellable) {
        val owned = synchronized(gate) { slots.values.filter { it.capture.fence.operationId == operation } }
        owned.forEach { retire(it, Wire.RetireReason.ERROR) }
        var result = MediaHandleRelease.RELEASED
        for (slot in owned) if (release(slot) != MediaHandleRelease.RELEASED) result = MediaHandleRelease.FAILED_RETAINED
        result
    }
    var replay401UsedFor: (SourceIntent) -> Boolean = { false }
    private val copyIds = mutableMapOf<Boolean, Pair<MediaId, MediaId>>()
    fun copyFor(capture: SelectionCapture, intent: SourceIntent): MediaId = synchronized(gate) {
        val purpose = intent is SourceIntent.BoundVaultDownload
        val operation = capture.fence.operationId
        val old = copyIds[purpose]
        if (old?.first == operation) old.second else MediaId.random().also { copyIds[purpose] = operation to it }
    }

    private fun source(key: Wire.SourceKey): Source? = synchronized(gate) { slots[key] }
    fun currentCapture(transfer: RemoteMediaTransferCapture): SelectionCapture.VaultNetwork {
        val slot = source(transfer.source) ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.WRITE)
        mediaCheck(slot.transfer == transfer, MediaCode.INTENT_STALE, MediaStage.WRITE)
        return slot.capture as? SelectionCapture.VaultNetwork ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.WRITE)
    }
    private fun request(slot: Source): MediaAdmissionRequest = MediaAdmissionRequest(slot.capture, slot.intent,
        MediaAdmissionStep.USE, null, slot.evidence, null, slot.resource)

    private fun <T> current(slot: Source, action: (MediaLiveAdmission) -> T): T {
        mediaCheck(!closed.get() && !slot.retiring, MediaCode.OWNER_STALE, MediaStage.RESOLVE)
        return admission.withCurrent(request(slot)) { live ->
            mediaCheck(clock.monotonicMs() < live.authorityDeadlineMonotonicMs, MediaCode.OWNER_STALE, MediaStage.RESOLVE)
            action(live)
        }
    }

    private fun lifetime(slot: Source): Wire.Lifetime = current(slot) { live ->
        val remaining = minOf(10_000L, live.authorityDeadlineMonotonicMs - clock.monotonicMs())
        mediaCheck(remaining > 1_000, MediaCode.OWNER_STALE, MediaStage.RESOLVE)
        slot.authorityDeadline = clock.monotonicMs() + remaining
        Wire.Lifetime(Math.addExact(System.currentTimeMillis(), remaining), remaining)
    }
    private fun readLifetime(slot: Source): Wire.Lifetime = current(slot) { live ->
        val remaining = minOf(slot.authorityDeadline, live.authorityDeadlineMonotonicMs) - clock.monotonicMs()
        mediaCheck(remaining > 1_000, MediaCode.OWNER_STALE, MediaStage.RANGE)
        Wire.Lifetime(System.currentTimeMillis() + remaining, remaining)
    }

    override fun startHead(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        evidence: MediaSourceEvidence, admission: MediaLiveAdmission,
        deadlineMonotonicMs: Long): MediaOwnedWork<MediaHeadInspection> = work.start { cancelled ->
        val slot = installNetwork(capture, intent, evidence, capture.fence.playbackGeneration, copyFor(capture, intent))
        try {
            val inspected = slot.inspected.await()
            mediaCheck(!cancelled.get() && clock.monotonicMs() < deadlineMonotonicMs,
                MediaCode.CANCELLED, MediaStage.HEAD)
            object : MediaHeadInspection {
                override val metadata = MediaHeadMetadata(200, "bytes", inspected.representation.totalBytes,
                    inspected.representation.etag, inspected.representation.mediaType, null, 0, 0, 0)
                override fun retire(): Unit { this@DesktopMediaNativeOwner.retire(slot, Wire.RetireReason.SELECTION) }
                override suspend fun awaitReleased(): MediaHandleRelease = release(slot)
            }.also { slot.inspectionLease = it; work.retainLease(it) }
        } catch (failure: Exception) {
            withContext(NonCancellable) { retire(slot, Wire.RetireReason.ERROR); release(slot) }
            throw failure
        }
    }

    suspend fun installNetwork(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        evidence: MediaSourceEvidence, executionAttempt: Long, copyId: MediaId): Source = installations.withLock {
        mediaCheck(!renewalFenced, MediaCode.OWNER_STALE, MediaStage.AUTH)
        start()
        val source = Source(Wire.SourceKey(bridge.runId, UUID.randomUUID(), capture.fence.playbackGeneration),
            capture, intent, evidence, executionAttempt, copyId, 1, null, null)
        source.rejected401 = replay401UsedFor(intent) || credentials.rejectedReplayUsed(capture, executionAttempt)
        synchronized(gate) {
            mediaCheck(slots.size < 2 && slots.values.none { !it.retiring &&
                (it.intent is SourceIntent.BoundVaultDownload) == (intent is SourceIntent.BoundVaultDownload) },
                MediaCode.CAPACITY_EXCEEDED, MediaStage.HEAD)
            slots[source.key] = source
        }
        try {
            val credential = credentials.access(capture)
            try {
                mediaCheck(credential.credentialGeneration == capture.network.credentialGeneration &&
                    credential.sessionGeneration == capture.network.sessionGeneration &&
                    credential.streamPolicy == capture.network.streamPolicy, MediaCode.OWNER_STALE, MediaStage.AUTH)
                source.credentialGeneration = credential.credentialGeneration
                source.sessionGeneration = credential.sessionGeneration
                source.policyRevision = credential.streamPolicy.policyRevision
                val policy = credential.streamPolicy
                val access = credential.bearer.withBytes { Wire.AccessBytes.takeOwnership(it.copyOf()) }
                val command = Wire.InstallSource(source.key,
                    if (intent is SourceIntent.BoundVaultDownload) Wire.Purpose.INSPECT_DOWNLOAD else Wire.Purpose.INSPECT_PLAYBACK,
                    executionAttempt, UUID.fromString(copyId.value), UUID.fromString(evidence.snapshot.audioVariantId.value),
                    policy.origin.value, policy.origin.value + "/api/v1/stream/audio-variants/${evidence.snapshot.audioVariantId.value}",
                    if (policy is MediaTransportPolicy.ConfirmedSpki) Wire.TransportMode.CONFIRMED_SPKI else Wire.TransportMode.PLATFORM_PKI,
                    policy.policyRevision, (policy as? MediaTransportPolicy.ConfirmedSpki)?.tlsSpkiSha256, access,
                    credential.credentialGeneration, credential.sessionGeneration, credential.accessExpiresAtMs,
                    source.revision, lifetime(source))
                send(command)
                scheduleAccess(source, credential.accessExpiresAtMs)
            } finally { credential.bearer.close() }
            return@withLock source
        } catch (failure: Exception) {
            // A possibly dispatched install keeps its original source owner until actual exit/drain.
            source.retiring = true
            source.inspected.completeExceptionally(failure)
            throw failure
        }
    }

    suspend fun authorize(slot: Source, representation: MediaRepresentation,
        resource: MediaActiveResourceLease): Unit {
        val inspected = slot.inspected.await()
        mediaCheck(inspected.representation == wire(representation), MediaCode.REPRESENTATION_CHANGED, MediaStage.HEAD)
        slot.resource = resource; slot.representation = representation
        val fence = resource.fence
        val next = DesktopMediaLimits.next(slot.revision)
        slot.pendingRevision = next
        send(Wire.AuthorizeBytes(slot.key, next, wire(representation), Wire.ResourceType.valueOf(fence.type.name),
            UUID.fromString(fence.operationId.value), UUID.fromString(fence.activationId.value), fence.generation,
            fence.attachmentRevision, lifetime(slot)))
        slot.revision = next; slot.pendingRevision = null
    }

    suspend fun installLocal(capture: SelectionCapture.AccountLocal, intent: SourceIntent,
        handle: MediaSourceHandle): Source = installations.withLock {
        mediaCheck(!renewalFenced, MediaCode.OWNER_STALE, MediaStage.AUTH)
        start()
        val slot = Source(Wire.SourceKey(bridge.runId, UUID.randomUUID(), capture.fence.playbackGeneration),
            capture, intent, handle.evidence, 1, handle.copyId, 1, handle.representation, null)
        synchronized(gate) { mediaCheck(slots.size < 2, MediaCode.CAPACITY_EXCEEDED, MediaStage.LOCAL_OWNER); slots[slot.key] = slot }
        val command = handle.withUse { file ->
            val managed = file as? MediaRuntimeSource.ManagedFile ?: throw mediaFailure(MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
            Wire.InstallRetained(slot.key, UUID.fromString(handle.copyId.value), managed.managedPath, managed.fileIdentity,
                wire(handle.representation), handle.permitRevision, 1, lifetime(slot))
        }
        send(command)
        slot
    }

    /** Synchronous actor fence, independent of ordinary command/activation/renewal ACKs. */
    fun captureIntent(fence: Wire.IntentFence, wantsPlay: Boolean): Unit = synchronized(gate) {
        if ((latestIntent?.fence?.desiredIntentRevision ?: 0) < fence.desiredIntentRevision) {
            latestIntent = Wire.UpdateIntent(fence, wantsPlay)
            if (!wantsPlay) appliedIntent = null
        }
    }

    suspend fun updateIntent(fence: Wire.IntentFence, wantsPlay: Boolean): Unit {
        val update = Wire.UpdateIntent(fence, wantsPlay)
        synchronized(gate) {
            if ((latestIntent?.fence?.desiredIntentRevision ?: 0) > fence.desiredIntentRevision || appliedIntent == fence) return
            latestIntent = update
            if (!wantsPlay) appliedIntent = null
        }
        send(update)
        synchronized(gate) { if (latestIntent === update) appliedIntent = fence }
    }

    suspend fun activatePaused(slot: Source, positionMs: Long, fence: Wire.IntentFence): Unit {
        val lease = DesktopAudioOwnerArbiter.acquireRemote(slot.key)
            ?: throw mediaFailure(MediaCode.DRAIN_UNCONFIRMED, MediaStage.HANDOFF)
        slot.audio = lease
        send(Wire.ActivateAudio(slot.key, fence, lease.id, positionMs))
    }
    suspend fun play(slot: Source, fence: Wire.IntentFence): Unit = sendAudio(slot, fence, true)
    suspend fun pause(slot: Source, fence: Wire.IntentFence): Unit = sendAudio(slot, fence, false)
    private suspend fun sendAudio(slot: Source, fence: Wire.IntentFence, play: Boolean): Unit {
        val audio = slot.audio ?: return
        send(if (play) Wire.Play(slot.key, fence, audio.id) else Wire.Pause(slot.key, fence, audio.id))
    }
    suspend fun seek(slot: Source, fence: Wire.IntentFence, seekId: Long, positionMs: Long): Unit {
        val audio = slot.audio ?: return
        send(Wire.Seek(slot.key, fence, audio.id, seekId, positionMs))
    }
    suspend fun renew(slot: Source): Unit {
        if (slot.accessWait || slot.renewal?.isActive == true) return
        admission.refresh(slot.capture, slot.intent, established = true)
        val next = DesktopMediaLimits.next(slot.revision)
        slot.pendingRevision = next
        send(Wire.RenewPermit(slot.key, slot.revision, next, lifetime(slot)))
        slot.revision = next; slot.pendingRevision = null
    }

    private fun scheduleAccess(slot: Source, expiresAtMs: Long): Unit {
        slot.proactive?.cancel()
        val remaining = expiresAtMs - System.currentTimeMillis()
        val lead = minOf(30_000L, maxOf(2_000L, remaining / 4))
        mediaCheck(remaining - lead > 1_000, MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        slot.proactive = work.scope.launch {
            delay(remaining - lead)
            if (!slot.retiring && !closed.get() && slot.renewal?.isActive != true)
                slot.renewal = work.scope.launch { renewAccess(slot, false, null) }
        }
    }

    /** ACCESS_WAIT owns old tasks/frames; a newer actor Pause never changes the renewal target. */
    suspend fun quiesceForRenewal(parent: SelectionCapture.VaultNetwork): Unit {
        val retained = installations.withLock {
            synchronized(gate) { renewalFenced = true; slots.values.toList() }
        }
        for (slot in retained) {
            val old = slot.capture as? SelectionCapture.VaultNetwork ?: continue
            mediaCheck(old.network.profileId == parent.network.profileId && old.network.sessionFamilyId == parent.network.sessionFamilyId &&
                old.network.credentialGeneration == parent.network.credentialGeneration, MediaCode.OWNER_STALE, MediaStage.AUTH)
            if (slot.retiring || slot.resource == null) {
                retire(slot, Wire.RetireReason.AUTHORITY)
                mediaCheck(release(slot) == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.AUTH)
            } else {
                beginAccessWait(slot, old, slot.rejected401, null)
                val actual = slot.accessQuiesced.await()
                mediaCheck(actual.renewalId == slot.renewalId && actual.permitRevision == slot.revision &&
                    actual.credentialGeneration == slot.credentialGeneration && actual.sessionGeneration == slot.sessionGeneration &&
                    !slot.retiring, MediaCode.DRAIN_UNCONFIRMED, MediaStage.AUTH)
            }
        }
    }

    fun continueQuiescedRenewals(): Unit {
        renewalFenced = false
        synchronized(gate) { slots.values.toList() }.forEach { slot ->
            if (slot.accessWait && !slot.retiring && slot.renewal?.isActive != true)
                slot.renewal = work.scope.launch { renewAccess(slot, slot.rejected401, null) }
        }
    }

    private suspend fun beginAccessWait(slot: Source, old: SelectionCapture.VaultNetwork,
        rejected: Boolean, nativeId: Long?): Pair<Long, Long> = commands.withLock {
        mediaCheck(!closed.get() && !slot.retiring, MediaCode.OWNER_STALE, MediaStage.AUTH)
        if (slot.accessWait && slot.renewalDeadline > 0) return@withLock slot.revision to slot.renewalId
        slot.accessWait = true
        val previousRevision = slot.revision
        val renewalId = nativeId ?: DesktopMediaLimits.next(slot.renewalId)
        slot.renewalId = renewalId
        slot.renewalDeadline = admission.accessTransitionDeadline(old, slot.resource)
        val deadline = slot.renewalDeadline - clock.monotonicMs()
        mediaCheck(deadline > 0, MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        val begin = Wire.BeginAccessRenewal(slot.key, renewalId,
            if (rejected) Wire.RenewalReason.REJECTED_401 else Wire.RenewalReason.PREEXPIRY,
            previousRevision, requireNotNull(slot.credentialGeneration), requireNotNull(slot.sessionGeneration),
            System.currentTimeMillis() + deadline, deadline)
        requireAck(bridge.submit(begin).mediaAwait(), begin)
        previousRevision to renewalId
    }

    private suspend fun renewAccess(slot: Source, rejected: Boolean, nativeId: Long?): Unit {
        try {
            val old = slot.capture as? SelectionCapture.VaultNetwork ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            val capturedTransition = beginAccessWait(slot, old, rejected, nativeId)
            val (previousRevision, renewalId) = capturedTransition
            val quiesced = slot.accessQuiesced.await()
            mediaCheck(!quiesced.applied && quiesced.renewalId == renewalId && quiesced.permitRevision == previousRevision &&
                quiesced.credentialGeneration == slot.credentialGeneration && quiesced.sessionGeneration == slot.sessionGeneration,
                MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
            val successor = credentials.successor(old, quiesced.renewalReason == Wire.RenewalReason.REJECTED_401, slot.executionAttempt)
            try {
                mediaCheck(credentials.publicationIsCurrent(old, successor.capture) && !slot.retiring,
                    MediaCode.OWNER_STALE, MediaStage.AUTH)
                val next = DesktopMediaLimits.next(previousRevision)
                val reason = quiesced.renewalReason
                admission.refresh(successor.capture, slot.intent, established = true)
                adoptCredential(slot, successor.capture)
                slot.capture = successor.capture
                val replacement = Wire.ReplaceAccess(slot.key, renewalId, reason, previousRevision, next,
                    requireNotNull(slot.credentialGeneration), successor.credentialGeneration,
                    requireNotNull(slot.sessionGeneration), successor.sessionGeneration,
                    successor.bearer.withBytes { Wire.AccessBytes.takeOwnership(it.copyOf()) }, successor.accessExpiresAtMs, lifetime(slot))
                slot.replacement = replacement
                send(replacement)
                val applied = slot.accessApplied.await()
                mediaCheck(applied.applied && applied.renewalId == renewalId && applied.permitRevision == next &&
                    applied.credentialGeneration == successor.credentialGeneration && applied.sessionGeneration == successor.sessionGeneration &&
                    credentials.publicationIsCurrent(old, successor.capture), MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
                slot.revision = next; slot.credentialGeneration = successor.credentialGeneration
                slot.sessionGeneration = successor.sessionGeneration; slot.accessWait = false; slot.replacement = null
                slot.accessQuiesced = CompletableDeferred(); slot.accessApplied = CompletableDeferred()
                scheduleAccess(slot, successor.accessExpiresAtMs)
                onAccessRenewed()
                // Read/play admission uses the latest actor tuple; no captured autoplay survives.
            } finally { successor.close() }
        } catch (_: Exception) {
            retire(slot, Wire.RetireReason.AUTHORITY)
            slot.transferred.completeExceptionally(mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH))
            slot.inspected.completeExceptionally(mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH))
        }
    }

    suspend fun startTransfer(slot: Source, capture: RemoteMediaTransferCapture): Unit {
        mediaCheck(capture.source == slot.key && slot.intent is SourceIntent.BoundVaultDownload,
            MediaCode.INTENT_STALE, MediaStage.WRITE)
        slot.transfer = capture
        send(Wire.StartTransfer(slot.key, capture.transferId, capture.attempt, capture.intentRevision,
            capture.attemptLeaseRevision, capture.initialOffset))
        bridge.grantInitialCredit(capture.transferId, Wire.MAX_DATA)
    }

    fun retire(slot: Source, reason: Wire.RetireReason): Unit = synchronized(gate) {
        if (reason in setOf(Wire.RetireReason.CANCEL, Wire.RetireReason.SLEEP))
            slot.transfer?.let { if (::files.isInitialized) files.vetoPublication(it) }
        if (slot.release == null) {
            slot.retiring = true
            slot.proactive?.cancel()
            slot.transfer?.let { if (::files.isInitialized) files.retire(it) }
            slot.audio?.let(DesktopAudioOwnerArbiter::beginDrain)
            slot.release = bridge.retire(slot.key, reason)
        }
    }
    suspend fun release(slot: Source): MediaHandleRelease = withContext(NonCancellable) {
        retire(slot, Wire.RetireReason.SELECTION)
        val actual = requireNotNull(slot.release).mediaAwait()
        slot.audio?.let { DesktopAudioOwnerArbiter.recordRemoteRelease(it, slot.key, actual) }
        if (actual.kind == RemoteMediaReleaseKind.DRAINED && actual.nativeDrained &&
            actual.sourcePipeWritesSettled && actual.hostCallbacksSettled) {
            synchronized(gate) { slots.remove(slot.key) }
            if (slot.resource == null && slot.inspectionLease == null) {
                admission.forget(slot.capture.fence.operationId); credentials.forget(slot.capture.fence.operationId)
            }
            MediaHandleRelease.RELEASED
        } else MediaHandleRelease.FAILED_RETAINED
    }

    private suspend fun send(command: Wire.Command): Unit {
        // Intent/replies retain their independent bridge lane while an ordinary ACK is pending.
        if (command is Wire.UpdateIntent) requireAck(bridge.submit(command).mediaAwait(), command)
        else commands.withLock { requireAck(bridge.submit(command).mediaAwait(), command) }
    }
    private fun requireAck(ack: Wire.Ack, command: Wire.Command): Unit = mediaCheck(
        ack.outcome == when (command) {
            is Wire.UpdateIntent, is Wire.RenewPermit, is Wire.AuthorizeBytes, is Wire.InstallRetained, is Wire.SetNetworkPolicy -> Wire.AckOutcome.APPLIED
            is Wire.RetireSource, is Wire.Close -> Wire.AckOutcome.DRAINED
            else -> Wire.AckOutcome.ADMITTED
        }, ack.code?.let { MediaCode.valueOf(it.name) } ?: MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)

    override fun commandIsCurrent(command: Wire.Command): Boolean {
        if (command is Wire.Close || command is Wire.RetireSource || command is Wire.Pause) return true
        if (command is Wire.SetNetworkPolicy) return !closed.get()
        if (command is Wire.UpdateIntent) return intentIsCurrent(command.fence, command.wantsPlay)
        val slot = command.source?.let(::source) ?: return false
        if (command is Wire.BeginAccessRenewal) return !closed.get() && !slot.retiring && slot.accessWait &&
            slot.renewalId == command.renewalId && clock.monotonicMs() < slot.renewalDeadline &&
            slot.revision == command.expectedPermitRevision && slot.credentialGeneration == command.expectedCredentialGeneration &&
            slot.sessionGeneration == command.expectedSessionGeneration
        return try { current(slot) { !slot.accessWait || command is Wire.BeginAccessRenewal || command is Wire.ReplaceAccess } }
        catch (_: Exception) { false }
    }
    override fun accessPublicationIsCurrent(command: Wire.ReplaceAccess, quiesced: Wire.AccessState): Boolean {
        val slot = source(command.source) ?: return false
        return slot.replacement === command && slot.renewalId == command.renewalId &&
            quiesced.renewalId == command.renewalId && !quiesced.applied && commandIsCurrent(command)
    }
    override fun audioLeaseIsCurrent(command: Wire.ActivateAudio): Boolean {
        val slot = source(command.source) ?: return false
        val lease = slot.audio ?: return false
        return command.audioLeaseId == lease.id && DesktopAudioOwnerArbiter.isCurrent(lease, slot.key) && commandIsCurrent(command)
    }
    override fun intentIsCurrent(fence: Wire.IntentFence, wantsPlay: Boolean): Boolean = synchronized(gate) {
        !closed.get() && latestIntent?.let { it.fence == fence && it.wantsPlay == wantsPlay } == true
    }
    override fun decideRead(request: Wire.ReadAdmission): CompletionStage<Wire.ReadResponse> {
        val future = CompletableFuture<Wire.ReadResponse>()
        work.scope.launch {
            try {
                val slot = source(request.source) ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.RANGE)
                admission.refresh(slot.capture, slot.intent, established = true)
                future.complete(decideCurrentRead(request))
            } catch (failure: Exception) {
                val code = (failure as? MediaSourceException)?.failure?.code ?: MediaCode.OWNER_UNAVAILABLE
                future.complete(Wire.ReadResponse(request.source, request.readId, request.permitRevision,
                    request.credentialGeneration, request.transportPolicyRevision, Wire.ReadDecision.DENY, null, Wire.Code.valueOf(code.name)))
            }
        }
        return future.minimalCompletionStage()
    }
    private fun decideCurrentRead(request: Wire.ReadAdmission): Wire.ReadResponse {
        val response = try {
            val slot = source(request.source) ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.RANGE)
            mediaCheck(!slot.accessWait && (request.permitRevision == slot.revision || request.permitRevision == slot.pendingRevision) &&
                request.credentialGeneration == slot.credentialGeneration && request.transportPolicyRevision == slot.policyRevision,
                MediaCode.OWNER_STALE, MediaStage.RANGE)
            current(slot) { live ->
                if (request.kind != Wire.ReadKind.FILE) mediaCheck(live.networkAvailable, MediaCode.OFFLINE, MediaStage.RANGE)
                if (slot.intent is SourceIntent.BoundVaultDownload) mediaCheck(downloadNetworkAllowed(), MediaCode.OFFLINE, MediaStage.RANGE)
            }
            Wire.ReadResponse(request.source, request.readId, request.permitRevision, request.credentialGeneration,
                request.transportPolicyRevision, Wire.ReadDecision.ALLOW, readLifetime(slot), null)
        } catch (failure: Exception) {
            val code = (failure as? MediaSourceException)?.failure?.code ?: MediaCode.OWNER_STALE
            Wire.ReadResponse(request.source, request.readId, request.permitRevision, request.credentialGeneration,
                request.transportPolicyRevision, Wire.ReadDecision.DENY, null, Wire.Code.valueOf(code.name))
        }
        return response
    }
    override fun readDecisionIsCurrent(request: Wire.ReadAdmission, response: Wire.ReadResponse): Boolean {
        val slot = source(request.source) ?: return false
        return response.decision == Wire.ReadDecision.ALLOW &&
            (response.permitRevision == slot.revision || response.permitRevision == slot.pendingRevision) &&
            request.credentialGeneration == slot.credentialGeneration && !slot.accessWait &&
            try { current(slot) { request.kind == Wire.ReadKind.FILE || it.networkAvailable } } catch (_: Exception) { false }
    }
    override fun write(frame: Wire.DataFrame, transfer: RemoteMediaTransferCapture): CompletionStage<RemoteMediaWriteWitness> = files.write(frame, transfer)
    override fun creditIsCurrent(transfer: RemoteMediaTransferCapture): Boolean {
        val slot = source(transfer.source) ?: return false
        // Credit admits one bounded file frame. The native path policy and fresh read
        // decision independently gate network tasks; a cost wait must retain this owner.
        return !slot.accessWait && files.creditIsCurrent(transfer) &&
            try { current(slot) { it.networkAvailable } } catch (_: Exception) { false }
    }
    private var allowMeteredDownloads: Boolean = allowMetered
    private var networkPolicyRevision: Long = 1
    fun downloadNetworkAllowed(): Boolean = network.value?.let {
        it.status == Wire.NetworkStatus.SATISFIED && it.constrained == false &&
            (it.expensive == false || (it.expensive == true && allowMeteredDownloads))
    } == true
    suspend fun setMeteredDownloads(enabled: Boolean): Unit {
        allowMeteredDownloads = enabled
        networkPolicyRevision = DesktopMediaLimits.next(networkPolicyRevision)
        if (startRequested) send(Wire.SetNetworkPolicy(networkPolicyRevision, enabled))
    }
    override fun closeAdmission(code: Wire.Code): Unit {
        closed.set(true)
        synchronized(gate) { slots.values.forEach { slot -> slot.retiring = true; slot.transfer?.let {
            if (::files.isInitialized) files.retire(it)
        } } }
    }
    override fun event(event: Wire.Event): Unit {
        // ACCESS_APPLIED must publish this nonblocking readback before the bridge can send its
        // withheld START. Enqueueing a later coroutine is not an access/credit publication.
        if (event is Wire.AccessState && event.applied) {
            val slot = source(event.source)
            val replacement = slot?.replacement
            if (slot == null || replacement == null || event.renewalId != replacement.renewalId ||
                event.permitRevision != replacement.permitRevision || event.credentialGeneration != replacement.credentialGeneration ||
                event.sessionGeneration != replacement.sessionGeneration) {
                closeAdmission(Wire.Code.ACCESS_RENEWAL_FAILED)
                return
            }
            slot.revision = event.permitRevision; slot.credentialGeneration = event.credentialGeneration
            slot.sessionGeneration = event.sessionGeneration; slot.accessWait = false
        }
        if (!events.trySend(event).isSuccess) closeAdmission(Wire.Code.CAPACITY_EXCEEDED)
    }
    override fun failed(code: Wire.Code): Unit { closeAdmission(code) }
    override fun exceptionalRecoveryIsCurrent(runId: UUID, release: RemoteMediaRelease): Boolean = synchronized(gate) {
        closed.get() && recovering.get() && recoveryAuthority?.let { it.first == runId && it.second === release } == true &&
            processRelease === release && release.kind == RemoteMediaReleaseKind.FAILED_RETAINED && release.processExitConfirmed &&
            release.pipeReaderSettled && release.pipeWriterSettled && release.hostCallbacksSettled
    }
    fun exceptionalSourceSettled(source: Source): Boolean {
        val process = processRelease ?: return false
        if (!closed.get() || process.kind != RemoteMediaReleaseKind.FAILED_RETAINED || !process.processExitConfirmed ||
            !process.pipeReaderSettled || !process.pipeWriterSettled || !process.hostCallbacksSettled || source.key.runId != bridge.runId) return false
        return try {
            retire(source, Wire.RetireReason.ERROR)
            val actual = source.release?.toCompletableFuture()?.getNow(null)
            actual != null && (actual.nativeDrained || actual.exceptionalProcessExitConfirmed) &&
                actual.sourcePipeWritesSettled && actual.hostCallbacksSettled
        } catch (_: Exception) { false }
    }

    /** Explicit UI retry only. This old graph stays closed; a fresh graph is opened by Main. */
    suspend fun reconcileFailedForExplicitAttempt(transfers: DesktopMediaTransferCoordinator, playback: DesktopMediaOwner,
        resources: DesktopMediaResourceOwner): Unit = withContext(NonCancellable) {
        mediaCheck(recovering.compareAndSet(false, true), MediaCode.CAPACITY_EXCEEDED, MediaStage.DRAIN)
        try {
            val actual = close()
            mediaCheck(actual.kind == RemoteMediaReleaseKind.FAILED_RETAINED && actual.processExitConfirmed &&
                actual.pipeReaderSettled && actual.pipeWriterSettled && actual.hostCallbacksSettled,
                MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
            // This closes new work and waits actual tickets. Retained leases are reconciled
            // individually below; their original FAILED_RETAINED results are kept intact.
            work.close()
            val retained = synchronized(gate) { slots.values.toList() }
            for (slot in retained) {
                // Read the original ticket so the shared arbiter records the actual failed
                // source outcome before its separate explicit-failure release is considered.
                release(slot)
                mediaCheck(exceptionalSourceSettled(slot), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                transfers.reconcileFailedSource(slot)
                slot.inspectionLease?.let(work::releaseLeaseAfterConfirmedSettlement)
            }
            resources.reconcileExceptionalOperations(transfers)
            playback.releaseRetainedHandlesForExplicitReconciliation()
            synchronized(gate) { recoveryAuthority = bridge.runId to actual }
            for (slot in retained) slot.audio?.let { audio ->
                mediaCheck(DesktopAudioOwnerArbiter.state.value.phase == DesktopAudioOwnerPhase.NONE ||
                    DesktopAudioOwnerArbiter.releaseFailedForExplicitAttempt(audio) {
                    recovering.get() && exceptionalSourceSettled(slot)
                }, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
            }
            mediaCheck(bridge.releaseFailedProcessForExplicitNewAttempt(), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
            synchronized(gate) { slots.clear() }
        } finally { synchronized(gate) { recoveryAuthority = null }; recovering.set(false) }
    }
    suspend fun close(): RemoteMediaRelease = withContext(NonCancellable) {
        closeAdmission(Wire.Code.CANCELLED)
        val actual = if (startRequested) bridge.close().mediaAwait() else
            RemoteMediaRelease(RemoteMediaReleaseKind.DRAINED, null, true, true, true, true, RemoteMediaFailureDelivery.NOT_REQUIRED)
        processRelease = actual
        val retained = synchronized(gate) { slots.values.toList() }
        retained.forEach { it.proactive?.cancel(); it.renewal?.join() }
        events.close(); eventJob?.join(); actorEvents.close()
        actual
    }
    companion object {
        fun wire(value: MediaRepresentation): Wire.Representation = Wire.Representation(value.strongEtag,
            value.totalBytes, value.mediaType, value.format.suffix)
    }
}
