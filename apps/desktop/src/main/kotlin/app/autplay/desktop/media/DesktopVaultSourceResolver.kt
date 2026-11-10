package app.autplay.desktop.media

import app.autplay.application.desktop.media.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext

/**
 * Independent SOURCE slice. All real authority/IO owners are required constructor arguments.
 * No HTTP adapter, refresh, publication, native coordinator or audio arbiter is installed here.
 * One resolution at a time (stricter than one per purpose); no retries or hidden fallback reads.
 */
class DesktopVaultSourceResolver(
    private val authority: DesktopMediaOwnerAdmissionPort,
    private val local: DesktopMediaLocalSourcePort,
    private val control: DesktopMediaAuthenticatedControlPort,
    private val journal: DesktopMediaJournalEvidencePort,
    private val heads: DesktopMediaHeadPort,
    private val resources: DesktopMediaResourceAdmissionPort,
    private val formats: DesktopMediaFormatAdmissionPort,
    private val settlement: DesktopMediaSettlementPort,
    private val clock: DesktopMediaResolutionClock,
) : DesktopMediaSourcePort {
    private val resolving: Mutex = Mutex()

    override suspend fun resolve(capture: SelectionCapture, intent: SourceIntent): SourceResolution {
        if (!resolving.tryLock()) return SourceResolution.Unknown(
            MediaFailure(MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE, MediaRetryAction.RETRY), LocalAvailability.UNKNOWN)
        val owned = ResolutionOwnership()
        var handedOff = false
        var availability = LocalAvailability.UNKNOWN
        try {
            DesktopMediaSourcePolicy.requireIntent(capture, intent)
            currentCoroutineContext().ensureActive()
            val localCapture = localCapture(capture)
            if (intent !is SourceIntent.BoundVaultDownload) {
                if (localCapture is SelectionCapture.AccountLocal) {
                    DesktopMediaSourcePolicy.requireLocalGuard(localCapture.account, downloaded = false)
                }
                val lookup = admitted(request(localCapture, intent, MediaAdmissionStep.LOCAL_LOOKUP)) { live ->
                    owned.start { local.startLocalRead(localCapture, intent, live) }
                }
                val result = owned.await(lookup) { value -> if (value is MediaLocalLookup.Found) owned.keep(value.lease) }
                when (result) {
                    is MediaLocalLookup.Found -> {
                        val lease = result.lease
                        return admitted(request(localCapture, intent, MediaAdmissionStep.LOCAL_RESULT, file = lease)) { live ->
                            requireLocalCopy(localCapture, intent, lease)
                            lease.verifyCurrentFileIdentityAndIntegrity()
                            val owner = GatedHandleOwner(localCapture, intent, lease.copyId, lease.representation,
                                null, null, lease, null, deadline(live, null), owned.leases.toList())
                            val handle = if (localCapture is SelectionCapture.StandaloneLocal) ManagedLocalHandle(owner)
                            else RetainedAccountHandle(owner, lease.ownership?.originalEvidence)
                            handedOff = true
                            SourceResolution.Allow(handle)
                        }
                    }
                    is MediaLocalLookup.Missing -> availability = result.availability
                }
            }
            if (intent is SourceIntent.AccountCopy || capture !is SelectionCapture.VaultNetwork) {
                return SourceResolution.Unknown(MediaFailure(MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER,
                    MediaRetryAction.RECONCILE), availability)
            }
            val started = now()
            val controlDeadline = addBounded(started, DesktopMediaLimits.CONTROL_MS)
            val networkRequest = request(capture, intent, MediaAdmissionStep.SNAPSHOT)
            val online = admitted(networkRequest) { it.networkAvailable }
            if (!online) return SourceResolution.Offline(availability)
            val observation = snapshot(capture, capture.network.projection.userTrackRefId, intent, owned, controlDeadline)
            val acceptance = admitted(request(capture, intent, MediaAdmissionStep.SNAPSHOT_RESULT, observation)) { live ->
                requireFresh(observation, controlDeadline)
                DesktopMediaSourcePolicy.requireSnapshot(capture, intent, observation.snapshot)
                owned.start { journal.startAcceptObservation(capture, intent, observation, live) }
            }
            val evidence = owned.await(acceptance)
            admitted(request(capture, intent, MediaAdmissionStep.SNAPSHOT_RESULT, observation, evidence)) {
                requireFresh(observation, controlDeadline)
                DesktopMediaSourcePolicy.requireEvidence(capture, intent, observation, evidence)
            }
            val format = formats.admit(evidence.snapshot.codec, evidence.snapshot.container, evidence.snapshot.mediaType)
            val representation = MediaRepresentation(evidence.snapshot.sha256, evidence.snapshot.totalBytes,
                evidence.snapshot.mediaType, format, MediaRepresentationKind.VAULT_CONTENT)
            requireFormatMime(representation)
            val head = admitted(request(capture, intent, MediaAdmissionStep.HEAD, observation, evidence)) { live ->
                requireFresh(observation, controlDeadline)
                owned.start { heads.startHead(capture, intent, evidence, live, controlDeadline) }
            }
            val inspection = owned.await(head) { owned.keep(it) }
            admitted(request(capture, intent, MediaAdmissionStep.HEAD_RESULT, observation, evidence)) {
                requireFresh(observation, controlDeadline)
                DesktopMediaSourcePolicy.requireEvidence(capture, intent, observation, evidence)
                requireHead(inspection.metadata, representation)
            }
            // INSPECT never becomes a long-lived credential holder while waiting for capacity.
            owned.release(inspection)
            currentCoroutineContext().ensureActive()
            val acquire = admitted(request(capture, intent, MediaAdmissionStep.ACQUIRE, observation, evidence)) { live ->
                requireFresh(observation, controlDeadline)
                owned.start { resources.startAcquire(capture, intent, evidence, representation, live, controlDeadline) }
            }
            val acquired = owned.await(acquire) { value ->
                when (value) {
                    is MediaResourceAcquisition.Active -> owned.keep(value.lease)
                    is MediaResourceAcquisition.Waiting -> owned.keep(value.pending)
                }
            }
            if (acquired is MediaResourceAcquisition.Waiting) {
                // This bounded resolver cannot own the future WAITING polling coordinator. Cleanup
                // requests exact cancellation and retains raced promotion/release until settled.
                mediaFail(MediaCode.RESOURCE_WAITING, MediaStage.ADMISSION)
            }
            val resource = (acquired as MediaResourceAcquisition.Active).lease
            val byteObservation = resource.admittedObservation ?: observation
            val byteCapture = byteObservation.capture
            requireCredentialSuccessor(capture, byteCapture)
            val byteDeadline = if (resource.admittedObservation != null)
                addBounded(byteObservation.dispatchedMonotonicMs, DesktopMediaLimits.CONTROL_MS) else controlDeadline
            return admitted(request(byteCapture, intent, MediaAdmissionStep.RESULT, byteObservation, evidence, resource = resource)) { live ->
                requireFresh(byteObservation, byteDeadline)
                DesktopMediaSourcePolicy.requireEvidence(byteCapture, intent, byteObservation, evidence)
                requireResource(byteCapture, intent, evidence, resource.fence)
                val owner = GatedHandleOwner(byteCapture, intent, resource.copyId, representation, byteObservation, evidence,
                    null, resource, deadline(live, resource), owned.leases.toList())
                val original = (intent as? SourceIntent.BoundVaultDownload)?.original
                handedOff = true
                SourceResolution.Allow(VaultPermit(owner, evidence, original, byteCapture, resource.fence))
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failure: MediaSourceException) {
            return when (failure.failure.code) {
                MediaCode.AUTHORIZATION_DENIED, MediaCode.CHECKSUM_MISMATCH, MediaCode.UNSUPPORTED_MEDIA,
                MediaCode.REPRESENTATION_CHANGED, MediaCode.REPRESENTATION_INVALID -> SourceResolution.Deny(failure.failure)
                MediaCode.OFFLINE -> SourceResolution.Offline(availability)
                else -> SourceResolution.Unknown(failure.failure, availability)
            }
        } catch (_: Exception) {
            return SourceResolution.Unknown(MediaFailure(MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE,
                MediaRetryAction.NONE), availability)
        } finally {
            try { if (!handedOff) owned.close() } finally { resolving.unlock() }
        }
    }

    override suspend fun readSourceSnapshot(capture: SelectionCapture.VaultNetwork, userTrackRefId: MediaId): MediaSourceObservation {
        if (!resolving.tryLock()) mediaFail(MediaCode.CAPACITY_EXCEEDED)
        val owned = ResolutionOwnership()
        try { return snapshot(capture, userTrackRefId, null, owned, addBounded(now(), DesktopMediaLimits.CONTROL_MS)) }
        finally { try { owned.close() } finally { resolving.unlock() } }
    }

    private suspend fun snapshot(capture: SelectionCapture.VaultNetwork, utr: MediaId, intent: SourceIntent?,
        owned: ResolutionOwnership, controlDeadline: Long): MediaSourceObservation {
        mediaRequire(utr == capture.network.projection.userTrackRefId, MediaCode.INTENT_STALE)
        val dispatched = now()
        val work = admitted(request(capture, intent, MediaAdmissionStep.SNAPSHOT)) { live ->
            requireBefore(controlDeadline)
            mediaRequire(live.networkAvailable, MediaCode.OFFLINE)
            owned.start { control.startSnapshot(capture, MediaSourceSnapshotRequestV1(utr), live, controlDeadline) }
        }
        val reply = owned.await(work)
        return admitted(request(capture, intent, MediaAdmissionStep.SNAPSHOT_RESULT)) {
            requireBefore(controlDeadline)
            val result = MediaSourceObservation(capture, decodeReply(reply), dispatched)
            requireFresh(result, controlDeadline)
            DesktopMediaSourcePolicy.requireSnapshot(capture, intent, result.snapshot)
            result
        }
    }

    private fun decodeReply(reply: MediaSnapshotHttpReply): MediaSourceSnapshotV1 {
        if (reply.status in 300..399) mediaFail(MediaCode.REDIRECT_REJECTED)
        if (reply.status == 403) mediaFail(MediaCode.AUTHORIZATION_DENIED)
        // An old/unmounted server can return HTML404. It never triggers the old UUID-only resolver.
        if (reply.status == 404 && reply.contentType != "application/json") mediaFail(MediaCode.CAPABILITY_UNAVAILABLE)
        mediaRequire(reply.relevantHeaderBytes in 0..16_384 && reply.largestRelevantHeaderBytes in 0..4_096 &&
            reply.contentType == "application/json" && reply.contentEncoding == null &&
            reply.cacheControl?.split(',')?.map { it.trim().lowercase() }?.sorted() == listOf("no-store", "private") &&
            reply.pragma?.lowercase() == "no-cache" && reply.vary?.lowercase() == "authorization", MediaCode.PROTOCOL_ERROR)
        if (reply.status == 200) return MediaSourceSnapshotV1.decode(reply.bodyBytes())
        val error = MediaSnapshotApiError.decode(reply.bodyBytes())
        val valid = when (reply.status) {
            401 -> error.code in setOf(MediaSnapshotApiCode.AUTHENTICATION_REQUIRED, MediaSnapshotApiCode.SESSION_REVOKED)
            404 -> error.code == MediaSnapshotApiCode.NOT_FOUND
            422 -> error.code == MediaSnapshotApiCode.REQUEST_VALIDATION_FAILED
            503 -> error.code == MediaSnapshotApiCode.MEDIA_SOURCE_UNAVAILABLE && error.retryable
            else -> false
        }
        mediaRequire(valid, MediaCode.PROTOCOL_ERROR)
        mediaFail(when (reply.status) {
            401 -> MediaCode.AUTH_REQUIRED
            404 -> MediaCode.SOURCE_UNKNOWN
            503 -> MediaCode.CAPABILITY_UNAVAILABLE
            else -> MediaCode.PROTOCOL_ERROR
        })
    }

    private fun requireCredentialSuccessor(previous: SelectionCapture.VaultNetwork, next: SelectionCapture.VaultNetwork): Unit {
        val a = previous.network; val b = next.network
        val local = a.local.copy(kValueRevision = if (a.local.kOwnerReference == a.profileId) b.local.kValueRevision else a.local.kValueRevision)
        mediaRequire(previous.fence == next.fence && local == b.local && b.credentialGeneration >= a.credentialGeneration &&
            b.sessionGeneration >= a.sessionGeneration && a.copy(local = local, sessionId = b.sessionId,
                sessionGeneration = b.sessionGeneration, credentialGeneration = b.credentialGeneration) == b,
            MediaCode.OWNER_STALE, MediaStage.AUTH)
    }

    private fun requireHead(head: MediaHeadMetadata, representation: MediaRepresentation): Unit {
        if (head.status == 401) mediaFail(MediaCode.AUTH_REQUIRED, MediaStage.HEAD)
        if (head.status == 403 || head.status == 404) mediaFail(MediaCode.AUTHORIZATION_DENIED, MediaStage.HEAD)
        mediaRequire(head.status == 200 && head.acceptRanges == "bytes" && head.bodyBytes == 0L && head.contentEncoding == null &&
            head.totalBytes == representation.totalBytes && head.strongEtag == representation.strongEtag &&
            head.mediaType == representation.mediaType && head.relevantHeaderBytes in 0..16_384 &&
            head.largestRelevantHeaderBytes in 0..4_096, MediaCode.REPRESENTATION_INVALID, MediaStage.HEAD)
    }

    private fun requireResource(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        evidence: MediaSourceEvidence, resource: MediaResourceFence): Unit {
        mediaRequire(resource.operationId == capture.fence.operationId, MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
        when (intent) {
            is SourceIntent.VaultPlayback -> mediaRequire(resource.type == MediaResourceType.PLAY_INSTANCE &&
                resource.resourceId == intent.playInstanceId && resource.attachmentRevision != null &&
                resource.recordingId == evidence.snapshot.recordingId && resource.nextRecordingId == null,
                MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            is SourceIntent.BoundVaultDownload -> mediaRequire(resource.type == MediaResourceType.DOWNLOAD_INTENT &&
                resource.resourceId == intent.intentId && resource.attachmentRevision == null &&
                resource.targetVariantId == intent.original.snapshot.audioVariantId &&
                evidence.snapshot.audioVariantId == intent.original.snapshot.audioVariantId,
                MediaCode.RESOURCE_STALE, MediaStage.ADMISSION)
            else -> mediaFail(MediaCode.INTENT_STALE)
        }
    }

    private fun requireLocalCopy(capture: SelectionCapture, intent: SourceIntent, lease: MediaLocalReadLease): Unit {
        mediaRequire(lease.leaseRevision in 1..DesktopMediaLimits.MAX_INTEGER, MediaCode.OWNER_STALE)
        requireFormatMime(lease.representation)
        if (capture is SelectionCapture.StandaloneLocal) {
            mediaRequire(intent is SourceIntent.StandaloneSelection && lease.ownership == null &&
                lease.representation.kind == MediaRepresentationKind.INDEPENDENT_LOCAL, MediaCode.OWNER_STALE)
            return
        }
        val account = (capture as? SelectionCapture.AccountLocal)?.account ?: mediaFail(MediaCode.OWNER_STALE)
        val proof = lease.ownership ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
        mediaRequire(proof.account == account && proof.copyId == lease.copyId && proof.owned && proof.committed && !proof.removed &&
            proof.completionRevision in 1..DesktopMediaLimits.MAX_INTEGER && proof.completionRevision == proof.ownershipCompletionRevision &&
            proof.publicationId == proof.ownershipPublicationId && proof.acquisitionId == proof.ownershipAcquisitionId,
            MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
        val downloaded = proof.acquisition == MediaCopyAcquisition.USER_DOWNLOAD
        DesktopMediaSourcePolicy.requireLocalGuard(account, downloaded)
        if (downloaded) {
            mediaRequire(proof.materializationRevision == null && lease.representation.kind == MediaRepresentationKind.VAULT_CONTENT,
                MediaCode.RECONCILIATION_REQUIRED)
            val original = proof.originalEvidence ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED)
            mediaRequire(original.kind == MediaObservationKind.ACQUISITION && original.intentId == proof.acquisitionId &&
                original.owner == account.localOwner && original.storeId == account.storeId && original.scopeId == proof.acquisitionScopeId &&
                original.snapshot.sha256 == lease.representation.digestSha256 &&
                original.snapshot.totalBytes == lease.representation.totalBytes && original.snapshot.mediaType == lease.representation.mediaType &&
                formats.admit(original.snapshot.codec, original.snapshot.container, original.snapshot.mediaType) == lease.representation.format,
                MediaCode.RECONCILIATION_REQUIRED)
        } else mediaRequire(proof.originalEvidence == null && lease.representation.kind == MediaRepresentationKind.INDEPENDENT_LOCAL &&
            proof.materializationRevision?.let { it in 1..DesktopMediaLimits.MAX_INTEGER } == true,
            MediaCode.RECONCILIATION_REQUIRED)
        when (intent) {
            is SourceIntent.AccountCopy -> mediaRequire(intent.copyId == proof.copyId &&
                (intent.acquisitionScopeId == null || intent.acquisitionScopeId == proof.acquisitionScopeId), MediaCode.INTENT_STALE)
            is SourceIntent.VaultPlayback -> {
                val attachment = proof.attachment ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED)
                mediaRequire(attachment == intent.projection && !attachment.pendingTargetMutation && !attachment.conflict &&
                    !attachment.tombstone, MediaCode.RECONCILIATION_REQUIRED)
                val original = proof.originalEvidence
                if (original != null) {
                    val sameTarget = original.localTrackRefId == attachment.localTrackRefId &&
                        original.snapshot.userTrackRefId == attachment.userTrackRefId && original.snapshot.recordingId == attachment.recordingId
                    if (!sameTarget) {
                        val redirect = proof.redirect ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED)
                        val fresh = redirect.evidence
                        mediaRequire(redirect.redirectProjectionRevision in 1..DesktopMediaLimits.MAX_INTEGER &&
                            redirect.aliasRecordingId == original.snapshot.recordingId && redirect.canonicalRecordingId == attachment.recordingId &&
                            fresh.kind == MediaObservationKind.ATTACHMENT && fresh.owner == original.owner && fresh.storeId == original.storeId &&
                            fresh.scopeId == original.scopeId && fresh.localTrackRefId == attachment.localTrackRefId &&
                            fresh.bindingCommitId == original.bindingCommitId && fresh.projectionRevision == attachment.projectionRevision &&
                            fresh.snapshot.serverInstanceId == original.snapshot.serverInstanceId && fresh.snapshot.userId == original.snapshot.userId &&
                            fresh.snapshot.identityEpoch == original.snapshot.identityEpoch &&
                            fresh.snapshot.identityThumbprintSha256 == original.snapshot.identityThumbprintSha256 &&
                            fresh.snapshot.userTrackRefId == attachment.userTrackRefId &&
                            fresh.snapshot.userTrackRefRowVersion == attachment.userTrackRefRowVersion &&
                            fresh.snapshot.recordingId == attachment.recordingId && fresh.snapshot.audioVariantId == original.snapshot.audioVariantId &&
                            fresh.snapshot.sameContent(original.snapshot), MediaCode.RECONCILIATION_REQUIRED)
                    }
                }
            }
            else -> mediaFail(MediaCode.INTENT_STALE)
        }
    }

    private fun requireFormatMime(value: MediaRepresentation): Unit {
        val allowed = when (value.format) {
            MediaFormat.MP3 -> setOf("audio/mpeg")
            MediaFormat.AAC -> setOf("audio/aac")
            MediaFormat.M4A -> setOf("audio/mp4", "audio/x-m4a")
            MediaFormat.FLAC -> setOf("audio/flac", "audio/x-flac")
            MediaFormat.OPUS -> setOf("audio/ogg", "audio/opus")
            MediaFormat.WAV -> setOf("audio/wav", "audio/x-wav", "audio/vnd.wave")
        }
        mediaRequire(value.mediaType in allowed, MediaCode.UNSUPPORTED_MEDIA)
    }
    private fun localCapture(capture: SelectionCapture): SelectionCapture = when (capture) {
        is SelectionCapture.VaultNetwork -> SelectionCapture.AccountLocal(capture.fence, capture.network.local)
        else -> capture
    }
    private fun request(capture: SelectionCapture, intent: SourceIntent?, step: MediaAdmissionStep,
        observation: MediaSourceObservation? = null, evidence: MediaSourceEvidence? = null,
        file: MediaLocalReadLease? = null, resource: MediaActiveResourceLease? = null): MediaAdmissionRequest =
        MediaAdmissionRequest(capture, intent, step, observation, evidence, file, resource)
    private suspend fun <T> admitted(request: MediaAdmissionRequest, action: (MediaLiveAdmission) -> T): T {
        currentCoroutineContext().ensureActive()
        return authority.withCurrent(request) { live ->
            mediaRequire(now() < live.authorityDeadlineMonotonicMs, MediaCode.OWNER_STALE)
            action(live)
        }
    }
    private fun now(): Long = clock.monotonicMs().also { mediaRequire(it >= 0, MediaCode.OWNER_STALE) }
    private fun addBounded(value: Long, delta: Long): Long {
        mediaRequire(value <= Long.MAX_VALUE - delta, MediaCode.CAPACITY_EXCEEDED)
        return value + delta
    }
    private fun requireBefore(deadline: Long): Unit = mediaRequire(now() < deadline, MediaCode.NETWORK_TIMEOUT)
    private fun requireFresh(observation: MediaSourceObservation, controlDeadline: Long): Unit {
        val time = now()
        mediaRequire(time >= observation.dispatchedMonotonicMs && time < controlDeadline &&
            time - observation.dispatchedMonotonicMs <= DesktopMediaLimits.OBSERVATION_MS, MediaCode.RECONCILIATION_REQUIRED)
    }
    private fun deadline(live: MediaLiveAdmission, resource: MediaActiveResourceLease?): Long =
        minOf(addBounded(now(), DesktopMediaLimits.PERMIT_MS), live.authorityDeadlineMonotonicMs,
            resource?.authorityDeadlineMonotonicMs ?: Long.MAX_VALUE).also { mediaRequire(it > now(), MediaCode.OWNER_STALE) }

    private inner class ResolutionOwnership {
        val leases: MutableList<MediaOwnedLease> = mutableListOf()
        fun <T> start(action: () -> MediaOwnedWork<T>): MediaOwnedWork<T> = action().also(settlement::retainWork)
        fun keep(lease: MediaOwnedLease): Unit {
            settlement.retainLease(lease)
            if (leases.none { it === lease }) leases.add(lease)
        }
        /** A cancelled waiter requests cancellation, then retrieves/retains its actual late result. */
        suspend fun <T> await(work: MediaOwnedWork<T>, accept: (T) -> Unit = {}): T {
            var terminal = false
            try {
                val value = work.awaitTerminal()
                terminal = true
                accept(value)
                currentCoroutineContext().ensureActive()
                return value
            } catch (cancelled: CancellationException) {
                try { work.requestCancellation() } catch (_: Exception) { /* Keep the actual owner. */ }
                if (!terminal) withContext(NonCancellable) {
                    try {
                        val value = work.awaitTerminal()
                        terminal = true
                        accept(value)
                    } catch (_: Exception) {
                        terminal = true // NonCancellable terminal failure is actual settlement.
                    }
                }
                throw cancelled
            } catch (failure: Exception) {
                // A non-cancellation exception from awaitTerminal is already actual terminal failure.
                terminal = true
                throw failure
            } finally {
                if (terminal) settlement.releaseWorkAfterTerminal(work)
            }
        }
        suspend fun release(lease: MediaOwnedLease): Unit = withContext(NonCancellable) {
            lease.retire()
            mediaRequire(lease.awaitReleased() == MediaHandleRelease.RELEASED, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
            settlement.releaseLeaseAfterConfirmedSettlement(lease)
            leases.removeAll { it === lease }
        }
        suspend fun close(): Unit = withContext(NonCancellable) {
            leases.forEach { try { it.retire() } catch (_: Exception) { /* Keep its actual owner. */ } }
            // FAILED_RETAINED remains referenced by the mandatory process-lifetime settlement owner.
            for (lease in leases.toList()) {
                try {
                    if (lease.awaitReleased() == MediaHandleRelease.RELEASED) {
                        settlement.releaseLeaseAfterConfirmedSettlement(lease)
                        leases.removeAll { it === lease }
                    }
                } catch (_: Exception) { /* Keep actual owner; never replace this with optimistic release. */ }
            }
        }
    }

    private inner class GatedHandleOwner(
        private val capture: SelectionCapture, private val intent: SourceIntent,
        override val copyId: MediaId, override val representation: MediaRepresentation,
        private val observation: MediaSourceObservation?, private val evidence: MediaSourceEvidence?,
        private val file: MediaLocalReadLease?, private val resource: MediaActiveResourceLease?,
        private val permitDeadline: Long, private val leases: List<MediaOwnedLease>,
    ) : MediaHandleOwner {
        override val permitRevision: Long = (file?.leaseRevision ?: resource?.permitRevision
            ?: mediaFail(MediaCode.OWNER_STALE)).also { mediaRequire(it in 1..DesktopMediaLimits.MAX_INTEGER, MediaCode.OWNER_STALE) }
        private val admittedResourceFence: MediaResourceFence? = resource?.fence
        private val admittedAtMonotonicMs: Long = now()
        private val gate: Any = Any()
        private var retired: Boolean = false
        override fun <T> withCurrentUse(action: (MediaRuntimeSource) -> T): T = synchronized(gate) {
            mediaRequire(!retired && now() >= admittedAtMonotonicMs && now() < permitDeadline, MediaCode.OWNER_STALE)
            authority.withCurrent(request(capture, intent, MediaAdmissionStep.USE, observation, evidence, file, resource)) { live ->
                mediaRequire(now() < live.authorityDeadlineMonotonicMs, MediaCode.OWNER_STALE)
                if (file != null) {
                    mediaRequire(file.leaseRevision == permitRevision && file.copyId == copyId && file.representation == representation,
                        MediaCode.OWNER_STALE)
                    requireLocalCopy(capture, intent, file)
                    file.verifyCurrentFileIdentityAndIntegrity()
                    file.withManagedFile(action)
                } else {
                    val network = capture as? SelectionCapture.VaultNetwork ?: mediaFail(MediaCode.OWNER_STALE)
                    val accepted = evidence ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED)
                    val currentObservation = observation ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED)
                    // Initial dispatch-to-byte window also gates every use of this short source handle.
                    requireFresh(currentObservation, permitDeadline)
                    DesktopMediaSourcePolicy.requireEvidence(network, intent, currentObservation, accepted)
                    val active = resource ?: mediaFail(MediaCode.RESOURCE_STALE)
                    mediaRequire(active.fence == admittedResourceFence && active.permitRevision == permitRevision && active.copyId == copyId,
                        MediaCode.RESOURCE_STALE)
                    requireResource(network, intent, accepted, active.fence)
                    mediaRequire(now() < active.authorityDeadlineMonotonicMs, MediaCode.RESOURCE_STALE)
                    active.withNetworkSource { source ->
                        mediaRequire(source.variantId == accepted.snapshot.audioVariantId && source.resource == active.fence &&
                            source.streamPolicy == network.network.streamPolicy &&
                            source.credentialGeneration == network.network.credentialGeneration &&
                            source.sessionGeneration == network.network.sessionGeneration, MediaCode.RESOURCE_STALE)
                        action(source)
                    }
                }
            }
        }
        override fun retire(): Unit = synchronized(gate) {
            retired = true
            leases.forEach { try { it.retire() } catch (_: Exception) { /* Actual owner remains retained. */ } }
        }
        override suspend fun awaitReleased(): MediaHandleRelease = withContext(NonCancellable) {
            retire()
            var result = MediaHandleRelease.RELEASED
            for (lease in leases) {
                try {
                    if (lease.awaitReleased() == MediaHandleRelease.RELEASED) settlement.releaseLeaseAfterConfirmedSettlement(lease)
                    else result = MediaHandleRelease.FAILED_RETAINED
                } catch (_: Exception) { result = MediaHandleRelease.FAILED_RETAINED }
            }
            result
        }
    }
}
