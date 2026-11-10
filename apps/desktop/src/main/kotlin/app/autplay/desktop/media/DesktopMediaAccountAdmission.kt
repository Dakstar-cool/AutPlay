package app.autplay.desktop.media

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.*
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.syncbinding.SyncBindingContractV2 as B1
import app.autplay.desktop.account.AccountEndpoint
import app.autplay.desktop.account.DesktopAccountRuntime
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * Concrete protected/J readbacks and the process media-selection fence. The monitor below is
 * NOT the shared K/J readback-to-dispatch barrier. Account media must remain unmounted until
 * that actual common barrier and protected J association are connected by the host. Lifecycle
 * publication must retire these admissions before mutation; late reads cannot undo retirement.
 */
internal class DesktopMediaAccountAdmission(
    private val runtime: DesktopAccountRuntime,
    private val account: DesktopAccountStore,
    private val profiles: DesktopMediaProfileOwner,
    private val clock: DesktopMediaResolutionClock,
) : DesktopMediaOwnerAdmissionPort, DesktopMediaActorSourcePort {
    private val gate = Any()
    private val revision = AtomicLong(1)
    private val transferGeneration = AtomicLong(0)
    private var retired = false
    private var renewalLease: AccountLease? = null
    private val selected = AtomicReference<AccountLease?>(null)
    private val actor = AtomicReference<DesktopMediaActorTarget?>(null)
    private class Readback(val capture: SelectionCapture, val lease: AccountLease, val intent: SourceIntent?,
        val authorityGeneration: Long, val revision: Long, val deadline: Long, val cleanupOnly: Boolean)
    private val readbacks = ConcurrentHashMap<MediaId, Readback>()
    private val acceptedM5 = ConcurrentHashMap<MediaId, M5ProfileCapture>()
    private val d1 = ConcurrentHashMap<MediaId, D1.CapabilityObservation>()
    private class KindDeadlines(val d1: Instant, val b1: Instant?)
    private val kindDeadlines = ConcurrentHashMap<MediaId, KindDeadlines>()
    private val media: DesktopAccountMedia get() = account.media ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
    fun hasSelectedLease(): Boolean = selected.get() != null
    fun selectedLeaseOrNull(): AccountLease? = selected.get()
    fun selectedForRequest(): AccountLease = synchronized(gate) {
        mediaCheck(!retired, MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        selected.get() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
    }
    fun selectStandalone(): Unit = synchronized(gate) {
        nextRevision(); retired = true; selected.set(null); readbacks.clear(); acceptedM5.clear(); d1.clear(); kindDeadlines.clear(); actor.set(null)
    }
    fun isRetired(): Boolean = synchronized(gate) { retired }
    fun beginRenewal(expected: AccountLease): Unit = synchronized(gate) {
        mediaCheck(!retired && selected.get() == expected && renewalLease == null, MediaCode.OWNER_STALE, MediaStage.AUTH)
        renewalLease = expected
        nextRevision(); readbacks.clear()
    }
    suspend fun adoptRenewal(expected: AccountLease, fresh: AccountLease): Unit {
        account.readTracks(fresh, null, 1)
        synchronized(gate) {
            mediaCheck(!retired && renewalLease == expected && selected.get() == expected &&
                fresh.storeId == expected.storeId && fresh.scopeId == expected.scopeId && fresh.profileId == expected.profileId &&
                fresh.bindingCommit == expected.bindingCommit && fresh.sessionFamily == expected.sessionFamily &&
                fresh.activationRevision > expected.activationRevision, MediaCode.OWNER_STALE, MediaStage.AUTH)
            selected.set(fresh); renewalLease = null; nextRevision(); readbacks.clear(); kindDeadlines.clear()
        }
    }
    suspend fun recaptureRenewal(old: SelectionCapture.VaultNetwork): SelectionCapture.VaultNetwork {
        val next = network(old.fence, old.network.projection.localTrackRefId)
        val a = old.network; val b = next.network
        mediaCheck(a.local.localOwner == b.local.localOwner && a.bindingCommitId == b.bindingCommitId &&
            a.userId == b.userId && a.deviceId == b.deviceId && a.deviceKeyThumbprint == b.deviceKeyThumbprint &&
            a.serverInstanceId == b.serverInstanceId && a.identityEpoch == b.identityEpoch && a.applicationKeyThumbprint == b.applicationKeyThumbprint &&
            a.apiPolicy == b.apiPolicy && a.streamPolicy == b.streamPolicy && a.sessionFamilyId == b.sessionFamilyId &&
            b.sessionGeneration == DesktopMediaLimits.next(a.sessionGeneration) && a.projection == b.projection &&
            a.local.localGuard == b.local.localGuard && a.local.originLocalGuard == b.local.originLocalGuard &&
            a.m5Profile.m5CapabilityRevisionHighWater == b.m5Profile.m5CapabilityRevisionHighWater &&
            a.d1b1Guards.globalCapabilityRevisionHighWater == b.d1b1Guards.globalCapabilityRevisionHighWater,
            MediaCode.OWNER_STALE, MediaStage.AUTH)
        return next
    }
    fun forget(operationId: MediaId): Unit { readbacks.remove(operationId); kindDeadlines.remove(operationId) }

    /** Actual J lease is checked by the real repository before it can become selected. */
    suspend fun adoptLease(lease: AccountLease): Unit {
        account.readTracks(lease, null, 1)
        synchronized(gate) { nextRevision(); retired = false; selected.set(lease); readbacks.clear(); acceptedM5.clear(); d1.clear(); kindDeadlines.clear() }
    }
    fun actorTarget(target: DesktopMediaActorTarget?): Unit { actor.set(target) }
    fun intentFor(capture: SelectionCapture): SourceIntent? = readbacks[capture.fence.operationId]?.intent
    fun accessTransitionDeadline(capture: SelectionCapture.VaultNetwork, resource: MediaActiveResourceLease?): Long = synchronized(gate) {
        val readback = readbacks[capture.fence.operationId] ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.AUTH)
        mediaCheck(!retired && readback.revision == revision.get() && selected.get() == readback.lease &&
            sameAuthority(readback.capture, capture),
            MediaCode.OWNER_STALE, MediaStage.AUTH)
        runtime.secure.requireAdmission(readback.authorityGeneration)
        val kinds = kindDeadlines[capture.fence.operationId] ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
        val now = clock.monotonicMs()
        val wall = System.currentTimeMillis()
        minOf(now + 30_000, now + capture.network.m5Profile.expiresAt.toEpochMilli() - wall,
            now + kinds.d1.toEpochMilli() - wall, kinds.b1?.let { now + it.toEpochMilli() - wall } ?: Long.MAX_VALUE,
            resource?.authorityDeadlineMonotonicMs ?: Long.MAX_VALUE)
    }
    fun retire(): Unit = synchronized(gate) { nextRevision(); retired = true; renewalLease = null; readbacks.clear(); kindDeadlines.clear(); actor.set(null) }
    fun lease(local: AccountLocalCapture): AccountLease {
        val actual = selected.get() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        mediaCheck(actual.storeId == local.storeId && actual.scopeId == local.scopeId && actual.activationRevision == local.activationRevision &&
            actual.bindingCommit == local.publicationCommitId && actual.authorityRevision == local.kAuthorityRevision,
            MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        return actual
    }

    override suspend fun capture(target: DesktopMediaActorTarget): DesktopMediaActorSource {
        actorTarget(target)
        val selected = selected.get() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        target.copySelection?.let { copy ->
            val fence = MediaSelectionFence(selected.bootId, MediaId.random(), target.selectionRevision,
                target.selectionRevision, target.desiredIntentRevision, MediaId(target.queueId), MediaId(target.entryId),
                target.listenId?.let(::MediaId), target.generation)
            return captureCopy(fence, MediaId(copy.copyId), MediaId(copy.acquisitionScopeId), MediaId(target.trackId))
        }
        val queue = account.domain?.readQueue(selected, MediaId(target.queueId))
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        mediaCheck(queue.currentEntryId?.value == target.entryId && queue.sessionId?.value == target.listenId &&
            queue.entries.any { it.entryId.value == target.entryId && it.trackId.value == target.trackId },
            MediaCode.INTENT_STALE, MediaStage.RESOLVE)
        val projection = media.projection(selected, MediaId(target.trackId))
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        // J scans through ineligible raw pages; only real exhaustion yields no origin guard.
        val retained = media.readTrackCopies(selected, projection.localTrackRefId, null, 1).firstOrNull()
        val origin = retained?.ownership?.originLocalVetoRef?.let { originGuard(it) }
        val local = local(selected, origin)
        val fence = MediaSelectionFence(selected.bootId, MediaId.random(), target.selectionRevision,
            target.selectionRevision, target.desiredIntentRevision, MediaId(target.queueId), MediaId(target.entryId),
            target.listenId?.let(::MediaId), target.generation)
        val capture = SelectionCapture.AccountLocal(fence, local)
        val intent = SourceIntent.VaultPlayback(projection, MediaId(requireNotNull(target.listenId)))
        refresh(capture, intent)
        return DesktopMediaActorSource.Account(capture, intent)
    }

    suspend fun captureCopy(fence: MediaSelectionFence, copyId: MediaId, acquisitionScope: MediaId,
        localTrackId: MediaId? = null): DesktopMediaActorSource.Account {
        val lease = selected.get() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        val copy = media.readCopy(lease, copyId, acquisitionScope) ?: throw mediaFailure(MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
        mediaCheck(localTrackId == null || copy.copy.localTrackRefId == localTrackId, MediaCode.INTENT_STALE, MediaStage.LOCAL_OWNER)
        val origin = copy.ownership.originLocalVetoRef?.let { originGuard(it) }
        val local = local(lease, origin)
        mediaCheck(local.localOwner == copy.ownership.localOwner && local.kOwnerReference == copy.ownership.kOwnerRef,
            MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        val capture = SelectionCapture.AccountLocal(fence, local)
        val intent = SourceIntent.AccountCopy(copyId, acquisitionScope)
        refresh(capture, intent)
        return DesktopMediaActorSource.Account(capture, intent)
    }
    private suspend fun originGuard(reference: MediaId): LocalGuardCapture {
        val snapshot = runtime.secure.snapshot().mediaAwait()
        val record = runtime.secure.read(reference.value, snapshot).mediaAwait()
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
        try {
            val guard = readGuard(record.payload)
            return LocalGuardCapture(reference, guard.revision, state(guard.state), veto(guard.veto))
        } finally { record.close() }
    }
    suspend fun recaptureLocal(expected: AccountLocalCapture): AccountLocalCapture {
        val selected = lease(expected)
        val origin = expected.originLocalGuard?.let { originGuard(it.ownerReference) }
        val current = local(selected, origin)
        mediaCheck(current.localOwner == expected.localOwner && current.kOwnerReference == expected.kOwnerReference &&
            current.localGuard == expected.localGuard && current.originLocalGuard == expected.originLocalGuard,
            MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        return current
    }

    override suspend fun network(local: SelectionCapture.AccountLocal, intent: SourceIntent.VaultPlayback): DesktopMediaActorSource.Account {
        val capture = network(local.fence, intent.projection.localTrackRefId)
        refresh(capture, intent)
        return DesktopMediaActorSource.Account(capture, intent)
    }

    suspend fun downloadCapture(track: MediaId, operation: MediaId, attempt: Long): SelectionCapture.VaultNetwork {
        val selected = selected.get() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        val generation = transferGeneration.updateAndGet { DesktopMediaLimits.next(it) }
        val capture = network(MediaSelectionFence(selected.bootId, operation, generation, generation, generation,
            null, null, null, generation), track)
        refresh(capture, null)
        return capture
    }

    private suspend fun network(fence: MediaSelectionFence, track: MediaId): SelectionCapture.VaultNetwork {
        val lease = selected.get() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        val expectedRevision = revision.get()
        val profile = acceptedM5[lease.profileId]?.takeIf { it.expiresAt > Instant.now() }
            ?: profiles.refresh(lease.profileId).also { acceptedM5[lease.profileId] = it }
        val snapshot = runtime.secure.snapshot().mediaAwait()
        val record = runtime.secure.read(lease.profileId.value, snapshot).mediaAwait()
            ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        try {
            val owner = record.owner
            val payload = record.payload as? SessionPayload ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            val server = runtime.selected ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
            val access = runtime.secure.access(snapshot, owner).mediaAwait()
            val bytes = access.copy()
            val response = try { runtime.read(server.transport, AccountEndpoint.D1_CAPABILITIES, access = bytes).mediaAwait() }
                finally { bytes.fill(0); access.close() }
            val raw = try { response.bodyCopy() } finally { response.close() }
            val transition = try { (D1.capabilities(raw, server.identity,
                D1.CapabilityScope(requireNotNull(owner.account), requireNotNull(owner.device), D1.Platform.MACOS),
                d1[lease.profileId], Instant.now()) as? D1.Result.Verified)?.value
                    ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH) } finally { raw.fill(0) }
            val digest = transition.nextObservation.effectivePayloadSha256
            try { mediaCheck(transition.nextObservation.revision == payload.d1.revision &&
                digest.joinToString("") { "%02x".format(it.toInt() and 255) } == payload.d1.contentSha256,
                MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH) } finally { digest.fill(0) }
            d1[lease.profileId] = transition.nextObservation
            val binding = bindingGuard(fence, lease, owner, payload, snapshot, transition)
            val transportBytes = payload.transportPolicyCopy()
            val transport = try { SecureMediaGuardCodec.readTransport(transportBytes) } finally { transportBytes.fill(0) }
            fun policy(origin: String, mode: String, pin: String?): MediaTransportPolicy = when (mode) {
                "PLATFORM_PKI" -> MediaTransportPolicy.PlatformPki(ConfirmedMediaOrigin(origin), transport.revision)
                "CONFIRMED_SPKI" -> MediaTransportPolicy.ConfirmedSpki(ConfirmedMediaOrigin(origin), transport.revision, requireNotNull(pin))
                else -> throw mediaFailure(MediaCode.TLS_UNTRUSTED, MediaStage.AUTH)
            }
            val projection = media.projection(lease, track) ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
            val local = local(lease, LocalGuardCapture(lease.profileId, readGuard(payload).revision,
                state(readGuard(payload).state), veto(readGuard(payload).veto)))
            mediaCheck(revision.get() == expectedRevision && profile.m5CapabilityRevisionHighWater.value == payload.m5?.revision,
                MediaCode.OWNER_STALE, MediaStage.AUTH)
            return SelectionCapture.VaultNetwork(fence, VaultNetworkCapture(local, lease.profileId,
                MediaId(requireNotNull(owner.account)), MediaId(requireNotNull(owner.device)), requireNotNull(owner.keyThumbprint),
                MediaId(requireNotNull(owner.bindingCommit)), snapshot.authorityRevision, MediaId(requireNotNull(owner.server)),
                requireNotNull(owner.identityEpoch), requireNotNull(owner.identityThumbprint),
                policy(requireNotNull(owner.apiOrigin), transport.apiMode, transport.apiPin),
                policy(requireNotNull(owner.streamOrigin), transport.streamMode, transport.streamPin),
                MediaId(requireNotNull(owner.session)), MediaId(requireNotNull(owner.family)), requireNotNull(owner.sessionGeneration),
                record.revision, profile, binding, LocalMediaVeto.NONE, projection))
        } finally { record.close() }
    }

    private suspend fun bindingGuard(fence: MediaSelectionFence, lease: AccountLease, owner: SecureRecordOwner,
        payload: SessionPayload, snapshot: SecureControl, transition: D1.CapabilityTransition): D1B1BindingGuardCapture {
        var b1: B1.RevisionGuard? = null
        var b1Expiry: Instant? = null
        val protectedHash = payload.d1.b1ContentSha256
        if (protectedHash != null) {
            // This read revalidates an ALREADY applicable B1 guard. M1 never performs bind,
            // enables a family, or substitutes B1 content for D1/M5 content.
            val server = runtime.selected ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
            val context = B1.OwnerCapture(server.identity, requireNotNull(owner.account), requireNotNull(owner.device),
                D1.Platform.MACOS, runtime.secure.admissionGeneration)
            val guard = (B1.revisionGuard(context, payload.d1.revision, null, transition.nextObservation) as? B1.Result.Verified)?.value
                ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
            val secret = runtime.secure.access(snapshot, owner).mediaAwait()
            val access = secret.copy()
            val response = try { runtime.read(server.transport, AccountEndpoint.B1_CAPABILITIES, access = access).mediaAwait() }
                finally { access.fill(0); secret.close() }
            val raw = try { response.bodyCopy() } finally { response.close() }
            val accepted = try { (B1.capabilities(raw, context, guard, Instant.now()) as? B1.Result.Verified)?.value
                ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH) } finally { raw.fill(0) }
            val digest = requireNotNull(accepted.next.previousB1).effectivePayloadSha256
            try { mediaCheck(accepted.next.globalHighWater == payload.d1.revision &&
                digest.joinToString("") { "%02x".format(it.toInt() and 255) } == protectedHash,
                MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH) } finally { digest.fill(0) }
            b1 = accepted.next; b1Expiry = accepted.snapshot.expiresAt
        }
        mediaCheck(kindDeadlines.size < 16 || kindDeadlines.containsKey(fence.operationId), MediaCode.CAPACITY_EXCEEDED, MediaStage.AUTH)
        kindDeadlines[fence.operationId] = KindDeadlines(transition.snapshot.expiresAt, b1Expiry)
        return D1B1BindingGuardCapture(lease.profileId, snapshot.authorityRevision,
            D1B1CapabilityRevision(payload.d1.revision), transition.nextObservation, b1)
    }

    private suspend fun local(lease: AccountLease, origin: LocalGuardCapture?): AccountLocalCapture {
        val snapshot = runtime.secure.snapshot().mediaAwait()
        val reconciliation = account.reconciliationSnapshot()
        val scope = reconciliation.scopes.singleOrNull { it.identity.scopeId == lease.scopeId }?.identity
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
        mediaCheck(reconciliation.selectedScope == lease.scopeId && reconciliation.activationRevision == lease.activationRevision &&
            reconciliation.rawActivationState == "ACTIVE" && snapshot.authorityRevision == lease.authorityRevision,
            MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        val portable = scope.portableAccountId
        val slot = if (portable != null) requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved) else lease.profileId.value
        val record = runtime.secure.read(slot, snapshot).mediaAwait() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        try {
            val owner = if (portable != null) {
                mediaCheck(record.payload is PortableRootPayload && record.owner.account == portable.value,
                    MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
                LocalOwnerKey.Portable(portable)
            } else {
                val remote = scope.remote ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
                mediaCheck(record.owner.server == remote.serverInstanceId.value && record.owner.account == remote.userId.value &&
                    record.owner.bindingCommit == lease.bindingCommit.value, MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
                LocalOwnerKey.BoundLocal(remote.serverInstanceId, remote.userId, lease.bindingCommit)
            }
            val reference = MediaId(slot)
            val guard = readGuard(record.payload)
            mediaCheck(guard.available && !record.owner.deletionVeto, MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            return AccountLocalCapture(lease.storeId, lease.scopeId, lease.activationRevision, lease.activationRevision, owner,
                reference, record.revision, lease.bindingCommit, snapshot.authorityRevision,
                LocalGuardCapture(reference, guard.revision, state(guard.state), veto(guard.veto)), origin)
        } finally { record.close() }
    }

    /** Called outside actor/bridge locks before each native read; completion keeps its actual owner. */
    suspend fun refresh(capture: SelectionCapture, intent: SourceIntent?, established: Boolean = false, cleanup: Boolean = false): Unit {
        val capturedRevision = revision.get()
        synchronized(gate) { mediaCheck(renewalLease == null && (!retired || cleanup), MediaCode.OWNER_STALE, MediaStage.RESOLVE) }
        val local = when (capture) {
            is SelectionCapture.AccountLocal -> capture.account
            is SelectionCapture.VaultNetwork -> capture.network.local
            else -> throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        }
        val lease = lease(local)
        account.readTracks(lease, null, 1)
        val origin = local.originLocalGuard?.let { expected ->
            val snapshot = runtime.secure.snapshot().mediaAwait()
            val stored = runtime.secure.read(expected.ownerReference.value, snapshot).mediaAwait()
                ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            try { val guard = readGuard(stored.payload)
                LocalGuardCapture(expected.ownerReference, guard.revision, state(guard.state), veto(guard.veto)) }
            finally { stored.close() }
        }
        mediaCheck(local(lease, origin) == local, MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        var deadline = clock.monotonicMs() + 10_000
        if (capture is SelectionCapture.VaultNetwork) {
            if (!cleanup) profiles.requireCurrent(capture.network.m5Profile)
            if (!cleanup) {
                val kinds = kindDeadlines[capture.fence.operationId] ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                mediaCheck(kinds.d1 > Instant.now() && (kinds.b1 == null || kinds.b1 > Instant.now()),
                    MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                deadline = minOf(deadline, clock.monotonicMs() + kinds.d1.toEpochMilli() - System.currentTimeMillis(),
                    kinds.b1?.let { clock.monotonicMs() + it.toEpochMilli() - System.currentTimeMillis() } ?: Long.MAX_VALUE)
            }
            val snapshot = runtime.secure.snapshot().mediaAwait()
            val stored = runtime.secure.read(capture.network.profileId.value, snapshot).mediaAwait()
                ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            try {
                val payload = stored.payload as? SessionPayload ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
                mediaCheck(snapshot.activeBinding == capture.network.profileId.value && stored.revision == capture.network.credentialGeneration &&
                    stored.owner.session == capture.network.sessionId.value && stored.owner.family == capture.network.sessionFamilyId.value &&
                    stored.owner.sessionGeneration == capture.network.sessionGeneration && !stored.owner.deletionVeto && !stored.owner.detached &&
                    payload.m5?.revision == capture.network.m5Profile.m5CapabilityRevisionHighWater.value &&
                    payload.d1.revision == capture.network.d1b1Guards.globalCapabilityRevisionHighWater.value,
                    MediaCode.OWNER_STALE, MediaStage.AUTH)
                deadline = minOf(deadline, clock.monotonicMs() + Math.multiplyExact(payload.accessExpiresAt, 1_000) - System.currentTimeMillis())
                if (!cleanup) deadline = minOf(deadline,
                    clock.monotonicMs() + capture.network.m5Profile.expiresAt.toEpochMilli() - System.currentTimeMillis())
            } finally { stored.close() }
            if (!cleanup && (!established || intent !is SourceIntent.BoundVaultDownload)) {
                val projection = media.projection(lease, capture.network.projection.localTrackRefId)
                mediaCheck(projection == capture.network.projection, MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
            }
            if (!cleanup && intent is SourceIntent.BoundVaultDownload) {
                val current = media.readIntent(lease, intent.intentId) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.RESOLVE)
                mediaCheck(current.record.revision == intent.intentRevision && current.desiredState == "DOWNLOAD" &&
                    current.target?.sourceEvidenceId == intent.sourceEvidenceId && current.target?.variantId == intent.original.snapshot.audioVariantId,
                    MediaCode.INTENT_STALE, MediaStage.RESOLVE)
            }
        }
        synchronized(gate) {
            mediaCheck(renewalLease == null && revision.get() == capturedRevision && (!retired || cleanup), MediaCode.OWNER_STALE, MediaStage.RESOLVE)
            readbacks.entries.removeIf { it.value.deadline <= clock.monotonicMs() }
            mediaCheck(readbacks.size < 8 || readbacks.containsKey(capture.fence.operationId), MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE)
            readbacks[capture.fence.operationId] = Readback(capture, lease, intent, runtime.secure.admissionGeneration, capturedRevision, deadline, cleanup)
        }
    }

    override fun <T> withCurrent(request: MediaAdmissionRequest, action: (MediaLiveAdmission) -> T): T = synchronized(gate) {
        val readback = readbacks[request.capture.fence.operationId] ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.RESOLVE)
        mediaCheck(renewalLease == null && ((!retired && !readback.cleanupOnly) || request.step == MediaAdmissionStep.DRAIN),
            MediaCode.OWNER_STALE, MediaStage.RESOLVE)
        mediaCheck(readback.revision == revision.get() && readback.lease == selected.get() &&
            sameAuthority(readback.capture, request.capture) && clock.monotonicMs() < readback.deadline,
            MediaCode.OWNER_STALE, MediaStage.RESOLVE)
        runtime.secure.requireAdmission(readback.authorityGeneration)
        request.capture.fence.queueId?.takeIf { request.step != MediaAdmissionStep.DRAIN }?.let {
            val actor = actor.get() ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.RESOLVE)
            mediaCheck(actor.queueId == it.value && actor.entryId == request.capture.fence.entryId?.value &&
                actor.listenId == request.capture.fence.listenId?.value && actor.generation == request.capture.fence.playbackGeneration &&
                actor.selectionRevision == request.capture.fence.selectionRevision, MediaCode.INTENT_STALE, MediaStage.RESOLVE)
        }
        val deadline = minOf(readback.deadline, request.resourceLease?.authorityDeadlineMonotonicMs ?: Long.MAX_VALUE)
        action(object : MediaLiveAdmission {
            override val authorityDeadlineMonotonicMs: Long = deadline
            override val networkAvailable: Boolean = request.capture is SelectionCapture.VaultNetwork
        })
    }
    private fun sameAuthority(a: SelectionCapture, b: SelectionCapture): Boolean = a == b ||
        (a is SelectionCapture.VaultNetwork && b is SelectionCapture.AccountLocal && a.fence == b.fence && a.network.local == b.account)
    private fun nextRevision(): Unit { val previous = revision.get(); mediaCheck(previous < Long.MAX_VALUE, MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE); revision.set(previous + 1) }
    private fun readGuard(payload: SecurePayload): LocalUseGuard {
        val bytes = when (payload) {
            is SessionPayload -> payload.localGuardCopy(); is PortableRootPayload -> payload.localGuardCopy()
            else -> throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        }
        return try { SecureMediaGuardCodec.readLocal(bytes) } finally { bytes.fill(0) }
    }
    private fun state(value: String): LocalUseState = LocalUseState.entries.firstOrNull { it.name == value } ?: LocalUseState.UNKNOWN
    private fun veto(value: String): LocalMediaVeto = LocalMediaVeto.entries.firstOrNull { it.name == value } ?: LocalMediaVeto.UNKNOWN
}
