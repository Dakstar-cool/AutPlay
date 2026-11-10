package app.autplay.desktop.media

import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.*
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as ProfileV2
import app.autplay.desktop.account.AccountEndpoint
import app.autplay.desktop.account.DesktopAccountRuntime
import app.autplay.desktop.security.DesktopSessionRotation
import app.autplay.desktop.security.SecurePendingTicket
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Real K reads and exact durable rotation; never a raw token/J/settings fallback. */
internal class DesktopMediaProtectedCredentials(private val runtime: DesktopAccountRuntime) : DesktopMediaCredentialOwner {
    lateinit var renewals: DesktopMediaRenewalOwner
    private val rotation = Mutex()
    private class Publication(val parent: SelectionCapture.VaultNetwork, val next: SelectionCapture.VaultNetwork,
        val admissionGeneration: Long)
    private val publications = ConcurrentHashMap<MediaId, Publication>()
    private val parentPublications = ConcurrentHashMap<Triple<MediaId, MediaId, Long>, Publication>()
    private val rejected = ConcurrentHashMap.newKeySet<Pair<MediaId, Long>>()
    override fun forget(operation: MediaId): Unit {
        publications.remove(operation); rejected.removeIf { it.first == operation }
    }

    override fun rejectedReplayUsed(capture: SelectionCapture.VaultNetwork, executionAttempt: Long): Boolean =
        rejected.contains(capture.fence.operationId to executionAttempt)

    override suspend fun prepare(capture: SelectionCapture.VaultNetwork): SelectionCapture.VaultNetwork {
        val current = try { access(capture) } catch (failure: MediaSourceException) {
            if (failure.failure.code != MediaCode.AUTH_REQUIRED) throw failure
            return successor(capture, false, capture.fence.playbackGeneration).use { it.capture }
        }
        val expiring = try { current.accessExpiresAtMs <= System.currentTimeMillis() + 30_000 } finally { current.close() }
        return if (expiring) successor(capture, false, capture.fence.playbackGeneration).use { it.capture } else capture
    }

    override suspend fun access(capture: SelectionCapture.VaultNetwork): DesktopMediaCredential {
        val snapshot = runtime.secure.snapshot().mediaAwait()
        val stored = runtime.secure.read(capture.network.profileId.value, snapshot).mediaAwait()
            ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        try {
            requireCurrent(capture, snapshot, stored)
            val payload = stored.payload as SessionPayload
            val expiry = Math.multiplyExact(payload.accessExpiresAt, 1_000L)
            mediaCheck(expiry > System.currentTimeMillis() + 2_000, MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            val access = runtime.secure.access(snapshot, stored.owner).mediaAwait()
            try {
                val bytes = access.copy()
                try { return DesktopMediaCredential(capture, TransientMediaBearer(bytes), expiry) }
                finally { bytes.fill(0) }
            } finally { access.close() }
        } finally { stored.close() }
    }

    override suspend fun successor(capture: SelectionCapture.VaultNetwork, rejected: Boolean,
        executionAttempt: Long): DesktopMediaCredential = rotation.withLock {
        val operation = capture.fence.operationId
        if (rejected) mediaCheck(this.rejected.add(operation to executionAttempt), MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        val existing = publications[operation]
        if (existing != null && existing.parent.network.credentialGeneration == capture.network.credentialGeneration)
            return@withLock access(existing.next)
        val parentKey = Triple(capture.network.profileId, capture.network.sessionFamilyId, capture.network.credentialGeneration)
        parentPublications[parentKey]?.let { shared ->
            val n = capture.network; val published = shared.next.network
            mediaCheck(n.bindingCommitId == published.bindingCommitId && n.local.localOwner == published.local.localOwner &&
                n.apiPolicy == published.apiPolicy && n.streamPolicy == published.streamPolicy,
                MediaCode.OWNER_STALE, MediaStage.AUTH)
            val next = renewals.shared(capture)
            val credential = access(next)
            publications[operation] = Publication(capture, next, runtime.secure.admissionGeneration)
            return@withLock credential
        }
        val server = runtime.requireServer(D1.Family.SESSION)
        var snapshot = runtime.secure.snapshot().mediaAwait()
        val stored = runtime.secure.read(capture.network.profileId.value, snapshot).mediaAwait()
            ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        var resultCapture: SelectionCapture.VaultNetwork
        try {
            requireCurrent(capture, snapshot, stored)
            val owner = stored.owner
            mediaCheck(capture.network.m5Profile.operations.contains("rotateDeviceSession"),
                MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
            val session = stored.payload as SessionPayload
            renewals.before(capture, owner)
            val rotationOwner = DesktopSessionRotation(runtime.secure, runtime.signer, runtime.custody)
            // A pre-existing exact parent rotation is resumed using its original signed wire.
            var pending: SecurePendingTicket? = null
            for (reference in snapshot.selected) {
                val candidate = runtime.secure.read(reference.slot, snapshot).mediaAwait() ?: continue
                try {
                    val payload = candidate.payload
                    if (payload is PendingPayload && payload.operation == PendingOperation.ROTATION &&
                        candidate.owner.stage != SecureStage.TERMINAL && candidate.owner.profile == owner.profile &&
                        candidate.owner.family == owner.family && candidate.owner.session == owner.session &&
                        candidate.owner.sessionGeneration == owner.sessionGeneration) {
                        mediaCheck(pending == null, MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
                        val resumed = candidate.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision)
                        pending = rotationOwner.resume(snapshot, reference.slot, resumed,
                            rotationContext(snapshot, candidate, server.identity)).mediaAwait()
                        snapshot = requireNotNull(pending).control
                    }
                } finally { candidate.close() }
            }
            val ticket = pending ?: rotationOwner.prepare(snapshot, owner, server.identity,
                UUID.randomUUID().toString(), Instant.now()).mediaAwait()
            // dispatch persists possibly-sent before exposing material. One exact lost-response
            // reconciliation reuses ticket/wire/successor; it never signs another rotation.
            suspend fun dispatch(exact: SecurePendingTicket) = runtime.custody.dispatch(exact) { material ->
                val wire = material.request.copy()
                try { runtime.read(server.transport, AccountEndpoint.ROTATE, wire).thenApply { response ->
                    response.use { it.bodyCopy() }
                } } finally { wire.fill(0) }
            }.mediaAwait()
            val response = try { dispatch(ticket) } catch (_: Exception) {
                val current = runtime.secure.snapshot().mediaAwait()
                val pendingRead = runtime.secure.read(ticket.slot, current).mediaAwait()
                    ?: throw mediaFailure(MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
                val resumed = try { rotationOwner.resume(current, ticket.slot,
                    pendingRead.owner.copy(boot = runtime.secure.boot, authorityRevision = current.authorityRevision),
                    rotationContext(current, pendingRead, server.identity)).mediaAwait() }
                    finally { pendingRead.close() }
                dispatch(resumed)
            }
            val local = session.localGuardCopy(); val transport = session.transportPolicyCopy(); val high = session.m5GuardCopy()
            val committed = try { rotationOwner.commit(response, Instant.now(), "no-store", "no-cache",
                session.d1, session.m5, local, transport, high).mediaAwait() }
            finally { local.fill(0); transport.fill(0); high.fill(0) }
            val successor = runtime.secure.read(capture.network.profileId.value, committed).mediaAwait()
                ?: throw mediaFailure(MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
            try {
                val nextOwner = successor.owner
                mediaCheck(nextOwner.profile == owner.profile && nextOwner.account == owner.account && nextOwner.device == owner.device &&
                    nextOwner.keyThumbprint == owner.keyThumbprint && nextOwner.bindingCommit == owner.bindingCommit && nextOwner.family == owner.family &&
                    nextOwner.server == owner.server && nextOwner.identityEpoch == owner.identityEpoch &&
                    nextOwner.identityThumbprint == owner.identityThumbprint && nextOwner.apiOrigin == owner.apiOrigin && nextOwner.streamOrigin == owner.streamOrigin &&
                    nextOwner.sessionGeneration == requireNotNull(owner.sessionGeneration) + 1 && nextOwner.stage == SecureStage.ACTIVE &&
                    committed.activeBinding == owner.profile && !nextOwner.deletionVeto && !nextOwner.detached,
                    MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
                resultCapture = renewals.adopt(capture)
                requireCurrent(resultCapture, committed, successor)
                val publication = Publication(capture, resultCapture, runtime.secure.admissionGeneration)
                publications[operation] = publication
                parentPublications[parentKey] = publication
                if (parentPublications.size > 64) parentPublications.keys.firstOrNull { it != parentKey }?.let(parentPublications::remove)
            } finally { successor.close() }
        } catch (failure: Throwable) { renewals.abort(); throw failure }
        finally { stored.close() }
        access(resultCapture)
    }

    override fun publicationIsCurrent(old: SelectionCapture.VaultNetwork, successor: SelectionCapture.VaultNetwork): Boolean = try {
        val publication = publications[old.fence.operationId]
        publication != null && publication.parent === old && publication.next === successor &&
            runtime.secure.requireAdmission(publication.admissionGeneration).let { true }
    } catch (_: Exception) { false }

    private suspend fun rotationContext(control: SecureControl, stored: SecureRecord, identity: D1.TrustedM5Identity): ProfileV2.Context {
        val pending = stored.payload as? PendingPayload ?: throw mediaFailure(MediaCode.ACCESS_RENEWAL_FAILED, MediaStage.AUTH)
        mediaCheck(pending.wireKind == PendingWireKind.SESSION_ROTATE && stored.owner.server == identity.serverInstanceId &&
            stored.owner.identityEpoch == identity.identityEpoch && stored.owner.identityThumbprint == identity.thumbprintSha256 &&
            stored.owner.apiOrigin == identity.apiOrigin && stored.owner.streamOrigin == identity.streamOrigin,
            MediaCode.OWNER_STALE, MediaStage.AUTH)
        val wire = pending.requestCopy(); val successor = pending.successorCopy()
        val key = runtime.secure.publicKey(control, requireNotNull(stored.owner.keyTag)).mediaAwait()
        try {
            val document = SecureJson.parse(wire, 16_384, canonical = false)
            val refresh = document.string("current_refresh_token").toByteArray(Charsets.US_ASCII)
            try {
                val owner = stored.owner
                return ProfileV2.rotationContext(identity, D1.Platform.MACOS, key, requireNotNull(owner.account),
                    requireNotNull(owner.device), requireNotNull(owner.session), requireNotNull(owner.family),
                    requireNotNull(owner.bindingCommit), requireNotNull(owner.sessionGeneration), requireNotNull(owner.operation),
                    secureSha256(refresh), secureSha256(successor), Instant.ofEpochSecond(requireNotNull(owner.expiresAtEpochSeconds)))
            } finally { refresh.fill(0) }
        } finally { wire.fill(0); successor.fill(0); key.fill(0) }
    }

    private fun requireCurrent(capture: SelectionCapture.VaultNetwork, control: SecureControl, record: SecureRecord): Unit {
        val n = capture.network; val owner = record.owner
        val session = record.payload as? SessionPayload ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        val transportBytes = session.transportPolicyCopy(); val guardBytes = session.localGuardCopy()
        try {
            val policy = SecureMediaGuardCodec.readTransport(transportBytes)
            val local = SecureMediaGuardCodec.readLocal(guardBytes)
            mediaCheck(control.activeBinding == n.profileId.value && owner.profile == n.profileId.value && owner.boot == runtime.secure.boot &&
                owner.authorityRevision == control.authorityRevision && control.authorityRevision == n.kAuthorityRevision &&
                owner.server == n.serverInstanceId.value && owner.identityEpoch == n.identityEpoch && owner.identityThumbprint == n.applicationKeyThumbprint &&
                owner.account == n.userId.value && owner.device == n.deviceId.value && owner.keyThumbprint == n.deviceKeyThumbprint &&
                owner.bindingCommit == n.bindingCommitId.value && owner.session == n.sessionId.value && owner.family == n.sessionFamilyId.value &&
                owner.sessionGeneration == n.sessionGeneration && record.revision == n.credentialGeneration &&
                owner.stage == SecureStage.ACTIVE && !owner.detached && !owner.deletionVeto && local.available &&
                session.m5?.revision == n.m5Profile.m5CapabilityRevisionHighWater.value &&
                session.d1.revision == n.d1b1Guards.globalCapabilityRevisionHighWater.value &&
                owner.apiOrigin == n.apiPolicy.origin.value && owner.streamOrigin == n.streamPolicy.origin.value &&
                policy.revision == n.apiPolicy.policyRevision && policy.revision == n.streamPolicy.policyRevision,
                MediaCode.OWNER_STALE, MediaStage.AUTH)
            fun matches(mode: String, pin: String?, expected: MediaTransportPolicy): Boolean = when (expected) {
                is MediaTransportPolicy.PlatformPki -> mode == "PLATFORM_PKI" && pin == null
                is MediaTransportPolicy.ConfirmedSpki -> mode == "CONFIRMED_SPKI" && pin == expected.tlsSpkiSha256
            }
            mediaCheck(matches(policy.apiMode, policy.apiPin, n.apiPolicy) && matches(policy.streamMode, policy.streamPin, n.streamPolicy),
                MediaCode.TLS_UNTRUSTED, MediaStage.AUTH)
            n.d1b1Guards.d1ContentGuard?.let { guard ->
                val bytes = guard.effectivePayloadSha256
                try { mediaCheck(bytes.joinToString("") { "%02x".format(it.toInt() and 255) } == session.d1.contentSha256,
                    MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH) } finally { bytes.fill(0) }
            }
            runtime.secure.requireAdmission(runtime.secure.admissionGeneration)
        } finally { transportBytes.fill(0); guardBytes.fill(0) }
    }
}
