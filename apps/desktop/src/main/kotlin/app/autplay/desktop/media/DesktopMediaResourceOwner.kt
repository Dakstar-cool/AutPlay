package app.autplay.desktop.media

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.*
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.time.Instant
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

/** Ordinary resource-v1 execution, with exact terminal release before any successor operation. */
internal class DesktopMediaResourceOwner(
    private val journal: DesktopAccountMedia,
    private val domain: DesktopAccountDomain,
    private val lease: (AccountLocalCapture) -> AccountLease,
    private val http: DesktopMediaHttp,
    private val native: DesktopMediaNativeOwner,
    private val credentials: DesktopMediaCredentialOwner,
    private val authority: DesktopMediaAccountAdmission,
    private val work: DesktopMediaWorkOwner,
    private val clock: DesktopMediaResolutionClock,
) : DesktopMediaResourceAdmissionPort {
    private val owners = java.util.concurrent.ConcurrentHashMap<MediaId, Resource>()
    private val renewalFenced = AtomicBoolean(false)
    suspend fun quiesceForRenewal(invokingOperation: MediaId): Unit {
        renewalFenced.set(true)
        owners.values.toList().filter { it.operation != invokingOperation }.forEach { it.protocol.withLock { } }
    }
    fun resumeAfterRenewal(): Unit { renewalFenced.set(false) }
    suspend fun adoptCredential(source: DesktopMediaNativeOwner.Source, capture: SelectionCapture.VaultNetwork): Unit {
        val owner = owners[capture.fence.operationId] ?: return
        mediaCheck(owner.nativeSlot === source && !owner.retired.get() && source.accessWait,
            MediaCode.OWNER_STALE, MediaStage.AUTH)
        val credential = credentials.access(capture)
        owner.protocol.withLock {
            owner.bearer?.close(); owner.bearer = credential; owner.capture = capture
        }
    }

    /** Existing operation only; a missing/ambiguous response is retained, never synthesized RELEASED. */
    suspend fun reconcileRetainedAttempt(capture: SelectionCapture.VaultNetwork, intent: MediaDownloadIntent,
        attempt: MediaAttempt): Unit {
        mediaCheck(attempt.intentId == intent.intentId && attempt.sourceEvidenceId == intent.target?.sourceEvidenceId &&
            native.releaseOperation(attempt.operationId) == MediaHandleRelease.RELEASED,
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        fun exact(value: Status): Unit = mediaCheck(value.operation == attempt.operationId && value.resource == intent.intentId &&
            value.type == MediaResourceType.DOWNLOAD_INTENT && value.target == intent.target?.variantId &&
            (attempt.resourceActivationId == null || value.activation == attempt.resourceActivationId && value.generation == attempt.resourceGeneration),
            MediaCode.RESOURCE_STALE, MediaStage.DRAIN)
        var actual = request(capture, MediaControlRoute.Resource(attempt.operationId, "poll"), retained = true).also(::exact)
        val digest = actual.digest
        if (actual.state != "RELEASED") {
            actual = request(capture, MediaControlRoute.Resource(attempt.operationId, "release"), envelope {
                if (actual.state == "WAITING") put("operation_sha256", actual.digest)
                else { put("activation_id", requireNotNull(actual.activation).value); put("generation", requireNotNull(actual.generation)) }
                put("reason", "CANCEL")
            }, retained = true).also(::exact)
        }
        repeat(3) {
            if (actual.state == "RELEASED") return@repeat
            delay(250)
            actual = request(capture, MediaControlRoute.Resource(attempt.operationId, "poll"), retained = true).also(::exact)
        }
        mediaCheck(actual.state == "RELEASED" && actual.digest == digest, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
    }
    fun delivered(operation: MediaId): Unit {
        val owner = owners[operation] ?: throw mediaFailure(MediaCode.RESOURCE_STALE, MediaStage.DRAIN)
        mediaCheck(owner.type == MediaResourceType.DOWNLOAD_INTENT && !owner.retired.get(),
            MediaCode.RESOURCE_STALE, MediaStage.DRAIN)
        owner.completed.set(true)
    }
    suspend fun releaseOperation(operation: MediaId): MediaHandleRelease {
        val owner = owners[operation] ?: return MediaHandleRelease.RELEASED
        owner.retire()
        return owner.awaitReleased()
    }
    suspend fun reconcileExceptionalSource(source: DesktopMediaNativeOwner.Source): Unit {
        val owner = owners[source.capture.fence.operationId] ?: return
        mediaCheck(owner.nativeSlot === source && native.exceptionalSourceSettled(source),
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        owner.retire()
        owner.terminal.await() // Original failure remains a failure; no concurrent retry.
        owner.releaseServer()
        owner.bearer?.close(); owners.remove(owner.operation, owner)
        work.releaseLeaseAfterConfirmedSettlement(owner)
    }
    suspend fun reconcileExceptionalOperations(transfers: DesktopMediaTransferCoordinator): Unit {
        for (owner in owners.values.toList()) {
            owner.nativeSlot?.let { mediaCheck(native.exceptionalSourceSettled(it), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN) }
            owner.retire(); owner.terminal.await()
            owner.releaseServer()
            transfers.reconcileFailedOperation(owner.capture, owner.intent)
            owner.bearer?.close(); owners.remove(owner.operation, owner)
            work.releaseLeaseAfterConfirmedSettlement(owner)
        }
    }
    private class Status(val operation: MediaId, val resource: MediaId, val type: MediaResourceType,
        val target: MediaId?, val digest: String, val state: String, val activation: MediaId?,
        val generation: Long?, val attachment: Long, val recording: MediaId?,
        val claimUntil: Instant?, val leaseUntil: Instant?, val retrySeconds: Long)

    override fun startAcquire(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        evidence: MediaSourceEvidence, representation: MediaRepresentation, admission: MediaLiveAdmission,
        deadlineMonotonicMs: Long): MediaOwnedWork<MediaResourceAcquisition> = work.start { cancelled ->
        mediaCheck(!renewalFenced.get(), MediaCode.OWNER_STALE, MediaStage.AUTH)
        val selected = lease(capture.network.local)
        val operation: MediaId
        val resourceId: MediaId
        val type: MediaResourceType
        val target: MediaId?
        var attempt = capture.fence.playbackGeneration
        if (intent is SourceIntent.BoundVaultDownload) {
            val descriptor = journal.readIntent(selected, intent.intentId) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.ADMISSION)
            val persisted = journal.readAttempt(selected, intent.intentId, descriptor.currentAttempt)
                ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.ADMISSION)
            mediaCheck(persisted.operationId == capture.fence.operationId && persisted.sourceEvidenceId == intent.sourceEvidenceId,
                MediaCode.INTENT_STALE, MediaStage.ADMISSION)
            operation = persisted.operationId; resourceId = intent.intentId; target = evidence.snapshot.audioVariantId
            type = MediaResourceType.DOWNLOAD_INTENT; attempt = persisted.sourceGeneration
        } else {
            val playback = intent as? SourceIntent.VaultPlayback ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.ADMISSION)
            val queue = domain.readQueue(selected, requireNotNull(capture.fence.queueId))
                ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.ADMISSION)
            mediaCheck(queue.sessionId == playback.playInstanceId && queue.currentEntryId == capture.fence.entryId &&
                queue.entries.any { it.entryId == capture.fence.entryId && it.trackId == playback.projection.localTrackRefId },
                MediaCode.INTENT_STALE, MediaStage.ADMISSION)
            operation = capture.fence.operationId; resourceId = playback.playInstanceId; target = null; type = MediaResourceType.PLAY_INSTANCE
            // Existing approved J queue command retains the operation + actual logical play instance
            // before acquisition. No server queue wire or standalone Room row is introduced.
            domain.saveQueue(selected, operation, queue.revision, queue.copy(revision = DesktopMediaLimits.next(queue.revision)))
        }
        val owned = Resource(capture, intent, evidence, representation, operation, resourceId, type, target, attempt)
        mediaCheck(owners.putIfAbsent(operation, owned) == null && owners.size <= 2, MediaCode.CAPACITY_EXCEEDED, MediaStage.ADMISSION)
        work.retainLease(owned)
        try {
            owned.status = request(capture, MediaControlRoute.Resource(null, "acquire"), envelope {
                put("operation_id", operation.value); put("kind", if (type == MediaResourceType.PLAY_INSTANCE) "PLAYBACK" else "TRANSFER")
                put("resource_type", type.name); put("resource_id", resourceId.value)
                target?.let { put("target_id", it.value) }
            })
            owned.requireStatus(requireNotNull(owned.status))
            val waitingStarted = clock.monotonicMs()
            while (owned.status?.state == "WAITING") {
                mediaCheck(!cancelled.get() && !owned.retired.get() && clock.monotonicMs() - waitingStarted < 45_000,
                    MediaCode.RESOURCE_WAITING, MediaStage.ADMISSION)
                delay(requireNotNull(owned.status).retrySeconds * 1_000)
                owned.status = request(capture, MediaControlRoute.Resource(operation, "poll")).also(owned::requireStatus)
            }
            var status = requireNotNull(owned.status)
            mediaCheck(status.state == "ACTIVE" && status.claimUntil?.isAfter(Instant.now()) == true,
                MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            // First renew claims the exact server fence before claim_until.
            status = request(capture, MediaControlRoute.Resource(operation, "renew"), fence(status)).also(owned::requireStatus)
            if (type == MediaResourceType.PLAY_INSTANCE) status = request(capture, MediaControlRoute.Resource(operation, "attachments"), envelope {
                put("activation_id", requireNotNull(status.activation).value); put("generation", requireNotNull(status.generation))
                put("expected_attachment_revision", status.attachment); put("current_recording_id", evidence.snapshot.recordingId.value)
                put("next_recording_id", JsonNull)
            }).also(owned::requireStatus)
            mediaCheck(status.state == "ACTIVE" && status.leaseUntil?.isAfter(Instant.now()) == true &&
                (type != MediaResourceType.PLAY_INSTANCE || status.recording == evidence.snapshot.recordingId),
                MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            owned.status = status
            val snapshotReply = beforeHelper(owned, MediaControlRoute.Snapshot(evidence.snapshot.userTrackRefId))
            val dispatched = snapshotReply.first
            snapshotReply.second.use { response ->
                mediaCheck(response.status == 200, if (response.status == 401) MediaCode.AUTH_REQUIRED else MediaCode.SOURCE_UNKNOWN, MediaStage.RESOLVE)
                val fresh = MediaSourceSnapshotV1.decode(response.bytes)
                DesktopMediaSourcePolicy.requireSnapshot(owned.capture, intent, fresh)
                mediaCheck(fresh.sameDocument(evidence.snapshot), MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
                owned.observation = MediaSourceObservation(owned.capture, fresh, dispatched)
            }
            val credential = credentials.access(owned.capture)
            owned.bearer = credential
            val slot = native.installNetwork(owned.capture, intent, evidence, attempt, owned.copyId)
            owned.nativeSlot = slot
            native.authorize(slot, representation, owned)
            mediaCheck(!cancelled.get() && !owned.retired.get() && clock.monotonicMs() - dispatched <= 10_000,
                MediaCode.OWNER_STALE, MediaStage.ADMISSION)
            owned.startRenewal()
            MediaResourceAcquisition.Active(owned)
        } catch (failure: Exception) {
            withContext(NonCancellable) { owned.retire(); owned.awaitReleased() }
            throw failure
        }
    }

    private inner class Resource(@Volatile var capture: SelectionCapture.VaultNetwork, val intent: SourceIntent,
        val evidence: MediaSourceEvidence, val representation: MediaRepresentation, val operation: MediaId,
        val resourceId: MediaId, val type: MediaResourceType, val target: MediaId?, val attempt: Long) : MediaActiveResourceLease {
        val retired = AtomicBoolean(false)
        val protocol = Mutex()
        val terminal = CompletableDeferred<MediaHandleRelease>()
        var status: Status? = null
        var nativeSlot: DesktopMediaNativeOwner.Source? = null
        var bearer: DesktopMediaCredential? = null
        var observation: MediaSourceObservation? = null
        val completed = AtomicBoolean(false)
        fun requireStatus(value: Status): Unit {
            val previous = status
            mediaCheck(value.operation == operation && value.resource == resourceId && value.type == type && value.target == target &&
                (previous == null || previous.digest == value.digest) &&
                (value.state !in setOf("ACTIVE", "DRAINING") ||
                    (value.activation != null && value.generation != null && value.generation > 0)) &&
                (previous?.activation == null || value.activation == previous.activation && value.generation == previous.generation),
                MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
        }
        override val admittedObservation: MediaSourceObservation? get() = observation
        override val copyId = native.copyFor(capture, intent)
        override val permitRevision: Long get() = nativeSlot?.revision ?: 1
        override val authorityDeadlineMonotonicMs: Long get() = minOf(
            capture.network.m5Profile.expiresAt.toEpochMilli() - System.currentTimeMillis() + clock.monotonicMs(),
            (status?.leaseUntil?.toEpochMilli() ?: 0) - System.currentTimeMillis() + clock.monotonicMs())
        override val fence: MediaResourceFence get() {
            val current = status ?: throw mediaFailure(MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            return MediaResourceFence(type, resourceId, operation, requireNotNull(current.activation), requireNotNull(current.generation),
                if (type == MediaResourceType.PLAY_INSTANCE) current.attachment else null, target,
                if (type == MediaResourceType.PLAY_INSTANCE) evidence.snapshot.recordingId else null, null)
        }
        override fun <T> withNetworkSource(action: (MediaRuntimeSource.Network) -> T): T {
            mediaCheck(!retired.get(), MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            val token = bearer ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            return action(MediaRuntimeSource.Network(capture.network.streamPolicy, evidence.snapshot.audioVariantId, token.bearer,
                fence, token.credentialGeneration, token.sessionGeneration, token.accessExpiresAtMs))
        }
        fun startRenewal(): Unit { work.scope.launch {
            try {
                var tick = 0
                while (!retired.get()) {
                    delay(5_000)
                    protocol.withLock {
                        if (!retired.get() && !renewalFenced.get() && nativeSlot?.accessWait != true) {
                            val currentCapture = nativeSlot?.capture as? SelectionCapture.VaultNetwork ?: capture
                            if (++tick % 2 == 0) status = request(currentCapture, MediaControlRoute.Resource(operation, "renew"), fence(requireNotNull(status)))
                                .also(::requireStatus)
                            nativeSlot?.let { native.renew(it) }
                        }
                    }
                }
            } catch (_: Exception) { retire() }
        } }
        override fun retire(): Unit {
            if (!retired.compareAndSet(false, true)) return
            nativeSlot?.let { native.retire(it, Wire.RetireReason.AUTHORITY) }
            work.scope.launch {
                var result = MediaHandleRelease.FAILED_RETAINED
                try {
                    if (nativeSlot?.let { native.release(it) } == MediaHandleRelease.FAILED_RETAINED) return@launch
                    releaseServer()
                    bearer?.close(); owners.remove(operation)
                    authority.forget(operation); credentials.forget(operation)
                    result = MediaHandleRelease.RELEASED
                    work.releaseLeaseAfterConfirmedSettlement(this@Resource)
                } catch (_: Exception) { /* Original operation, credentials and release evidence stay owned. */ }
                finally { terminal.complete(result) }
            }
        }
        suspend fun releaseServer(): Unit = protocol.withLock {
            val currentCapture = nativeSlot?.capture as? SelectionCapture.VaultNetwork ?: capture
            var current = status ?: request(currentCapture, MediaControlRoute.Resource(operation, "poll")).also(::requireStatus)
            if (current.state == "WAITING") {
                current = request(currentCapture, MediaControlRoute.Resource(operation, "release"), envelope {
                    put("operation_sha256", current.digest); put("reason", "CANCEL")
                }).also(::requireStatus)
                if (current.state != "RELEASED") current = request(currentCapture, MediaControlRoute.Resource(operation, "poll")).also(::requireStatus)
            }
            if (current.state in setOf("ACTIVE", "DRAINING")) current = request(currentCapture,
                MediaControlRoute.Resource(operation, "release"), envelope {
                    put("activation_id", requireNotNull(current.activation).value); put("generation", requireNotNull(current.generation))
                    put("reason", if (completed.get()) "COMPLETE" else "CANCEL")
                }).also(::requireStatus)
            mediaCheck(current.state == "RELEASED", MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
            status = current
        }
        override suspend fun awaitReleased(): MediaHandleRelease { retire(); return terminal.await() }
    }

    /** No helper slot exists here. Only this exact terminated control request may be replayed. */
    private suspend fun beforeHelper(owner: Resource, route: MediaControlRoute,
        body: ByteArray = byteArrayOf()): Pair<Long, DesktopMediaHttp.Reply> {
        if (owner.nativeSlot != null || owner.retired.get()) {
            val actual = owner.nativeSlot?.capture as? SelectionCapture.VaultNetwork ?: owner.capture
            val dispatched = clock.monotonicMs()
            return dispatched to http.execute(actual, route, body)
        }
        authority.refresh(owner.capture, owner.intent, established = true)
        owner.capture = credentials.prepare(owner.capture)
        var dispatched = clock.monotonicMs()
        var reply = http.execute(owner.capture, route, body)
        if (reply.status == 401) {
            reply.close()
            // HTTP has settled, and the old INSPECT owner was drained before acquire. This
            // uses the same attempt-wide counter which seeds a later native source.
            val successor = credentials.successor(owner.capture, true, owner.attempt)
            try { owner.capture = successor.capture } finally { successor.close() }
            authority.refresh(owner.capture, owner.intent, established = true)
            dispatched = clock.monotonicMs()
            reply = http.execute(owner.capture, route, body)
        }
        return dispatched to reply
    }

    private suspend fun request(capture: SelectionCapture.VaultNetwork, route: MediaControlRoute.Resource,
        body: ByteArray = byteArrayOf(), retained: Boolean = false): Status = try {
        val response = if (retained) http.execute(capture, route, body) else {
            val owner = owners[capture.fence.operationId] ?: throw mediaFailure(MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            beforeHelper(owner, route, body).second
        }
        response.use { reply ->
            mediaCheck(reply.status == 200, when (reply.status) {
                401 -> MediaCode.AUTH_REQUIRED; 403 -> MediaCode.AUTHORIZATION_DENIED; 409 -> MediaCode.RESOURCE_STALE
                429, 503 -> MediaCode.RESOURCE_WAITING; else -> MediaCode.CAPABILITY_UNAVAILABLE
            }, MediaStage.ADMISSION)
            val document = SecureJson.parse(reply.bytes, 4096, canonical = false)
            document.exact("contract_version", "schema_version", "operation_id", "kind", "resource_type", "resource_id", "target_id",
                "operation_sha256", "state", "activation_id", "generation", "waiting_until", "claim_until", "lease_until",
                "attachment_revision", "current_recording_id", "next_recording_id", "account_limit", "device_limit", "server_limit",
                "account_usage", "device_usage", "server_usage", "waiting_reason", "retry_after_seconds")
            mediaCheck(document.string("contract_version") == "v1" && document.number("schema_version") == 1L,
                MediaCode.PROTOCOL_ERROR, MediaStage.ADMISSION)
            fun id(key: String): MediaId? = document.nullableString(key)?.let(::MediaId)
            fun number(key: String): Long? = if (document[key] == JsonNull) null else document.number(key)
            fun time(key: String): Instant? = document.nullableString(key)?.let(Instant::parse)
            val type = MediaResourceType.valueOf(document.string("resource_type"))
            val state = document.string("state")
            val retry = document.number("retry_after_seconds")
            mediaCheck(state in setOf("WAITING", "ACTIVE", "DRAINING", "RELEASED") &&
                (state != "WAITING" || retry in 1..15) && document["next_recording_id"] == JsonNull &&
                document.string("kind") == (if (type == MediaResourceType.PLAY_INSTANCE) "PLAYBACK" else "TRANSFER"),
                MediaCode.PROTOCOL_ERROR, MediaStage.ADMISSION)
            Status(MediaId(document.string("operation_id")), MediaId(document.string("resource_id")), type, id("target_id"),
                document.string("operation_sha256").also(DesktopMediaLimits::digest), state, id("activation_id"), number("generation"),
                document.number("attachment_revision"), id("current_recording_id"), time("claim_until"), time("lease_until"), retry)
        }
    } finally { body.fill(0) }
    private fun fence(status: Status): ByteArray = envelope {
        put("activation_id", requireNotNull(status.activation).value); put("generation", requireNotNull(status.generation))
    }
    private fun envelope(body: JsonObjectBuilder.() -> Unit): ByteArray = SecureJson.encode(buildJsonObject {
        put("contract_version", "v1"); put("schema_version", 1); body()
    })
}
