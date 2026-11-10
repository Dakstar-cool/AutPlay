package app.autplay.desktop.acceptance

import app.autplay.application.desktop.media.*
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation
import app.autplay.application.syncbinding.SyncBindingContractV2
import app.autplay.desktop.media.*
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*

/**
 * FUTURE / UNCALLED / UNVERIFIED. No composite hook or new test entry point.
 * The zero-argument overload creates accepted SYNTHETIC signed M5/D1/B1 readbacks and actual
 * in-memory WAV/snapshot bytes only when invoked in a later explicitly authorized phase.
 * Inputs are data, never a scripted scenario driver. No real file or network is touched.
 * All required resolver ports below are deterministic in-memory owners; the resolver and public
 * application policy/decoder are real. These assertions do not qualify the production adapters.
 */
internal suspend fun assertFutureDesktopMediaResolverHost(): Unit =
    assertFutureDesktopMediaResolverHost(futureDesktopMediaResolverHostInputs())

internal suspend fun assertFutureDesktopMediaResolverHost(inputs: DesktopMediaResolverHostInputs): Unit {
    inputs.requireValidControls()
    assertSuccessfulVaultPlayback(inputs)
    assertSuccessfulBoundDownload(inputs)
    assertGuardChangesBeforeUse(inputs)
    assertLocalGuardsBeforeUse(inputs)
    assertCanonicalAttachmentReadbacks(inputs)
    assertLateAcquireCancellation(inputs, releaseConfirmed = true)
    assertLateAcquireCancellation(inputs, releaseConfirmed = false)
}

/**
 * Generate/verify these inputs only in a later authorized qualification phase. The capture is
 * the accepted current synthetic owner, including session/family/generations and separate guards.
 * Original: U/r1/R1/V1. Continuation: U/r2/R1/V1, identical content, advanced real row versions.
 * Canonical: U/r3/R2/V1, identical content, authenticated J alias R1 -> R2 supplied at the seam.
 * Reassigned: U/r3/R2/V2, otherwise valid own-actor content. No malformed proof drives a denial.
 * Every snapshot uses the valid fixture format pcm_s16le/wav/audio/wav. This helper consumes
 * accepted cryptographic readbacks; it does not verify signed M5/D1/B1.
 */
internal class DesktopMediaResolverHostInputs(
    val capture: SelectionCapture.VaultNetwork,
    val rootOnlyCapture: SelectionCapture.AccountLocal,
    val rootOnlyRepresentation: MediaRepresentation,
    val acceptedAt: Instant,
    val accessExpiresAt: Instant,
    val sessionExpiresAt: Instant,
    originalSnapshotBytes: ByteArray,
    continuationSnapshotBytes: ByteArray,
    canonicalSnapshotBytes: ByteArray,
    reassignedSnapshotBytes: ByteArray,
    originalContentBytes: ByteArray,
    reassignedContentBytes: ByteArray,
) {
    private val originalBytes: ByteArray = originalSnapshotBytes.copyOf()
    private val originalContent: ByteArray = originalContentBytes.copyOf()
    private val reassignedContent: ByteArray = reassignedContentBytes.copyOf()
    val original: MediaSourceSnapshotV1 = MediaSourceSnapshotV1.decode(originalBytes)
    val continuation: MediaSourceSnapshotV1 = MediaSourceSnapshotV1.decode(continuationSnapshotBytes)
    val canonical: MediaSourceSnapshotV1 = MediaSourceSnapshotV1.decode(canonicalSnapshotBytes)
    val reassigned: MediaSourceSnapshotV1 = MediaSourceSnapshotV1.decode(reassignedSnapshotBytes)

    fun originalContentBytes(): ByteArray = originalContent.copyOf()
    fun reassignedContentBytes(): ByteArray = reassignedContent.copyOf()

    fun requireValidControls(): Unit {
        val n = capture.network
        val a = n.local
        val m = n.m5Profile
        val g = n.d1b1Guards
        val d1 = checkNotNull(g.d1ContentGuard)
        val b1 = checkNotNull(g.b1Guard)
        val b1Content = checkNotNull(b1.previousB1)
        assertNotNull(capture.fence.queueId)
        assertNotNull(capture.fence.entryId)
        assertNotNull(capture.fence.listenId)
        assertTrue(a.localOwner is LocalOwnerKey.BoundLocal)
        assertEquals(n.bindingCommitId, (a.localOwner as LocalOwnerKey.BoundLocal).ownerBindingCommitId)
        assertEquals(n.serverInstanceId, (a.localOwner as LocalOwnerKey.BoundLocal).serverInstanceId)
        assertEquals(n.userId, (a.localOwner as LocalOwnerKey.BoundLocal).userId)
        assertEquals(n.kAuthorityRevision, a.kAuthorityRevision)
        assertEquals(a.kOwnerReference, g.protectedOwnerReference)
        assertEquals(a.kOwnerReference, checkNotNull(a.originLocalGuard).ownerReference)
        assertNotEquals(n.apiPolicy.origin, n.streamPolicy.origin)
        assertEquals(a.storeId, n.projection.storeId)
        assertEquals(a.scopeId, n.projection.scopeId)
        assertEquals(LocalMediaVeto.NONE, n.remoteVeto)
        DesktopMediaSourcePolicy.requireLocalGuard(a, downloaded = true)
        assertEquals(g.globalCapabilityRevisionHighWater.value, b1.globalHighWater)
        assertTrue(d1.revision <= b1.globalHighWater && b1Content.revision <= b1.globalHighWater)
        assertTrue(g.globalCapabilityRevisionHighWater.value > 0)
        assertNotEquals(m.m5CapabilityRevision.value, g.globalCapabilityRevisionHighWater.value)
        assertEquals(32, d1.effectivePayloadSha256.size)
        assertEquals(32, b1Content.effectivePayloadSha256.size)
        for (identity in listOf(d1.identity, b1.owner.identity, b1Content.owner.identity)) {
            assertEquals(n.serverInstanceId.value, identity.serverInstanceId)
            assertEquals(n.identityEpoch, identity.identityEpoch)
            assertEquals(n.applicationKeyThumbprint, identity.thumbprintSha256)
            assertEquals(n.apiPolicy.origin.value, identity.apiOrigin)
            assertEquals(n.streamPolicy.origin.value, identity.streamOrigin)
            assertArrayEquals(d1.identity.publicKeySpki, identity.publicKeySpki)
        }
        assertEquals(n.userId.value, d1.scope.userId)
        assertEquals(n.deviceId.value, d1.scope.deviceId)
        assertEquals(DeviceContractV2Negotiation.Platform.MACOS, d1.scope.platform)
        for (owner in listOf(b1.owner, b1Content.owner)) {
            assertEquals(n.userId.value, owner.userId)
            assertEquals(n.deviceId.value, owner.deviceId)
            assertEquals(DeviceContractV2Negotiation.Platform.MACOS, owner.platform)
            assertEquals(b1.owner.lifecycleGeneration, owner.lifecycleGeneration)
        }
        assertTrue(m.issuedAt <= acceptedAt && m.expiresAt > acceptedAt.plusSeconds(60))
        assertTrue(m.m5CapabilityRevision.value >= m.m5CapabilityRevisionHighWater.value)
        assertEquals(n.serverInstanceId, m.serverInstanceId)
        assertEquals(n.identityEpoch, m.identityEpoch)
        assertEquals(n.applicationKeyThumbprint, m.applicationKeyThumbprint)
        assertEquals(n.userId, m.userId)
        assertEquals(n.deviceId, m.deviceId)
        assertTrue(accessExpiresAt > acceptedAt.plusSeconds(60) && sessionExpiresAt > acceptedAt.plusSeconds(60))
        assertTrue(m.acceptedDocumentBytes().isNotEmpty())
        assertFalse(m.operations.any { operation ->
            listOf("vault", "media-source", "snapshot").any { it in operation.lowercase() }
        })
        assertNotEquals(n.sessionId, n.sessionFamilyId)
        assertTrue(n.sessionGeneration > 0 && n.credentialGeneration > 0)
        assertNotEquals(n.sessionGeneration, n.credentialGeneration)
        assertNotEquals(capture.fence.operationId, resolverHostId(1))
        assertArrayEquals(originalBytes, original.originalResponseBytes())
        DesktopMediaSourcePolicy.requireIntent(capture, playback())
        DesktopMediaSourcePolicy.requireSnapshot(capture, playback(), original)
        assertTrue(original.sameAcquisitionTarget(continuation))
        assertTrue(continuation.userTrackRefRowVersion > original.userTrackRefRowVersion)
        assertTrue(continuation.audioVariantRowVersion > original.audioVariantRowVersion)
        assertEquals(original.userTrackRefId, canonical.userTrackRefId)
        assertNotEquals(original.recordingId, canonical.recordingId)
        assertEquals(original.audioVariantId, canonical.audioVariantId)
        assertTrue(original.sameContent(canonical))
        assertTrue(canonical.userTrackRefRowVersion > continuation.userTrackRefRowVersion)
        assertTrue(canonical.audioVariantRowVersion > continuation.audioVariantRowVersion)
        assertEquals(canonical.userTrackRefRowVersion, reassigned.userTrackRefRowVersion)
        assertEquals(canonical.recordingId, reassigned.recordingId)
        assertNotEquals(original.audioVariantId, reassigned.audioVariantId)
        assertFalse(original.sameContent(reassigned))
        // Validate every alternate as a positive own-actor snapshot under its matching J projection.
        for (snapshot in listOf(continuation, canonical, reassigned)) {
            val projected = captureFor(snapshot)
            DesktopMediaSourcePolicy.requireSnapshot(projected,
                SourceIntent.VaultPlayback(projected.network.projection, resolverHostId(1)), snapshot)
        }
        for (snapshot in listOf(original, continuation, canonical, reassigned)) {
            assertEquals("pcm_s16le", snapshot.codec)
            assertEquals("wav", snapshot.container)
            assertEquals("audio/wav", snapshot.mediaType)
            val content = if (snapshot === reassigned) reassignedContent else originalContent
            requireFixtureWave(content)
            assertEquals(content.size.toLong(), snapshot.totalBytes)
            // Future invocation only: exact content proof, never a committed invented digest.
            val digest = MessageDigest.getInstance("SHA-256").digest(content)
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            assertEquals(digest, snapshot.sha256)
        }
    }

    private fun requireFixtureWave(content: ByteArray): Unit {
        assertEquals(16_044, content.size)
        val wave = ByteBuffer.wrap(content).order(ByteOrder.LITTLE_ENDIAN)
        fun ascii(size: Int): String = ByteArray(size).also { wave.get(it) }.toString(Charsets.US_ASCII)
        assertEquals("RIFF", ascii(4)); assertEquals(content.size - 8, wave.int)
        assertEquals("WAVEfmt ", ascii(8)); assertEquals(16, wave.int)
        assertEquals(1, wave.short.toInt()); assertEquals(1, wave.short.toInt())
        assertEquals(8_000, wave.int); assertEquals(16_000, wave.int)
        assertEquals(2, wave.short.toInt()); assertEquals(16, wave.short.toInt())
        assertEquals("data", ascii(4)); assertEquals(16_000, wave.int)
        assertEquals(16_000, wave.remaining())
    }

    fun playback(): SourceIntent.VaultPlayback = SourceIntent.VaultPlayback(capture.network.projection, resolverHostId(1))
    fun captureFor(snapshot: MediaSourceSnapshotV1): SelectionCapture.VaultNetwork = capture.copy(
        network = capture.network.copy(projection = capture.network.projection.copy(
            projectionRevision = DesktopMediaLimits.next(capture.network.projection.projectionRevision),
            userTrackRefRowVersion = snapshot.userTrackRefRowVersion, recordingId = snapshot.recordingId)))

    fun evidence(snapshot: MediaSourceSnapshotV1, projection: JMediaProjectionCapture, id: MediaId,
        intentId: MediaId?, kind: MediaObservationKind): MediaSourceEvidence = MediaSourceEvidence(
        id, intentId, kind, capture.network.local.storeId, capture.network.local.scopeId,
        capture.network.local.localOwner, capture.network.bindingCommitId, projection.localTrackRefId,
        projection.projectionRevision, snapshot.userTrackRefRowVersion, snapshot.recordingId, snapshot)

    fun originalAcquisition(): MediaSourceEvidence = evidence(original, capture.network.projection,
        resolverHostId(4), resolverHostId(3), MediaObservationKind.ACQUISITION)

    override fun toString(): String = "DesktopMediaResolverHostInputs(<redacted>)"
}

private suspend fun assertSuccessfulVaultPlayback(input: DesktopMediaResolverHostInputs): Unit {
    val fixture = ResolverHostFixture(input, input.capture, input.playback(), input.original)
    val handle = fixture.resolveVault()
    assertNull(handle.originalEvidence)
    assertSame(fixture.capture, handle.capture)
    assertEquals(MediaResourceType.PLAY_INSTANCE, handle.resource.type)
    assertEquals(input.playback().playInstanceId, handle.resource.resourceId)
    assertEquals(fixture.capture.fence.operationId, handle.resource.operationId)
    assertNotEquals(handle.resource.resourceId, handle.resource.operationId)
    assertEquals(input.original.recordingId, handle.resource.recordingId)
    assertNull(handle.resource.targetVariantId)
    assertNull(handle.resource.nextRecordingId)
    fixture.assertNetworkOrder(localFirst = true)
    val resource = checkNotNull(fixture.resource)
    assertEquals(0, resource.physicalReleases)
    assertEquals(0, resource.retireEffects)
    assertTrue(fixture.leases.any { it === resource })
    val accepted = handle.evidence
    handle.withUse { source ->
        assertSame(resource.source, source)
        assertSame(handle.resource, (source as MediaRuntimeSource.Network).resource)
        assertSame(fixture.capture.network.streamPolicy, source.streamPolicy)
        assertEquals(input.original.audioVariantId, source.variantId)
        assertEquals(fixture.capture.network.credentialGeneration, source.credentialGeneration)
        assertEquals(fixture.capture.network.sessionGeneration, source.sessionGeneration)
    }
    handle.withUse { assertSame(resource.source, it) }
    assertEquals(2, resource.sourceUses)
    assertSame(accepted, handle.evidence)
    assertEquals(handle.resource.operationId, resource.fence.operationId) // No per-read UUID replacement.
    assertArrayEquals(input.original.originalResponseBytes(), accepted.snapshot.originalResponseBytes())
    assertEquals(0, resource.physicalReleases)
    handle.retire()
    handle.retire() // Multiple fences may converge; actual close/release is still once.
    assertEquals(0, resource.physicalReleases)
    assertUseRejected(handle, MediaCode.OWNER_STALE)
    assertEquals(MediaHandleRelease.RELEASED, handle.awaitReleased())
    fixture.assertSettled(resource)
    assertEquals(1, resource.retireEffects)
    assertEquals(1, resource.releaseWaits)
}

private suspend fun assertSuccessfulBoundDownload(input: DesktopMediaResolverHostInputs): Unit {
    val original = input.originalAcquisition()
    val originalBytes = original.snapshot.originalResponseBytes()
    val capture = input.captureFor(input.continuation).copy(fence = input.capture.fence.copy(
        operationId = resolverHostId(6), queueId = null, entryId = null, listenId = null))
    val intent = SourceIntent.BoundVaultDownload(resolverHostId(3), 2, original.evidenceId,
        original, resolverHostId(5), capture.network.projection)
    val fixture = ResolverHostFixture(input, capture, intent, input.continuation)
    val handle = fixture.resolveVault()
    fixture.assertNetworkOrder(localFirst = false)
    assertSame(original, handle.originalEvidence)
    assertNotSame(original, handle.evidence)
    assertEquals(MediaObservationKind.CONTINUATION, handle.evidence.kind)
    assertEquals(intent.admissionEvidenceId, handle.evidence.evidenceId)
    assertEquals(intent.intentId, handle.evidence.intentId)
    assertEquals(MediaResourceType.DOWNLOAD_INTENT, handle.resource.type)
    assertEquals(intent.intentId, handle.resource.resourceId)
    assertEquals(capture.fence.operationId, handle.resource.operationId)
    assertNotEquals(handle.resource.resourceId, handle.resource.operationId)
    assertEquals(original.snapshot.audioVariantId, handle.resource.targetVariantId)
    assertNull(handle.resource.recordingId)
    assertNull(handle.resource.attachmentRevision)
    handle.withUse { assertEquals(MediaResourceType.DOWNLOAD_INTENT, (it as MediaRuntimeSource.Network).resource.type) }
    assertTrue(original.snapshot.userTrackRefRowVersion < handle.evidence.snapshot.userTrackRefRowVersion)
    assertArrayEquals(originalBytes, original.snapshot.originalResponseBytes())
    assertEquals(input.original.recordingId, original.observedRecordingId)
    assertEquals(MediaHandleRelease.RELEASED, handle.awaitReleased())
    fixture.assertSettled(checkNotNull(fixture.resource))
}

private enum class ResolverGuardChange { OWNER, VETO, PROJECTION, J_LEASE, M5, D1, B1, RESOURCE }

private suspend fun assertGuardChangesBeforeUse(input: DesktopMediaResolverHostInputs): Unit {
    for (change in ResolverGuardChange.entries) {
        val fixture = ResolverHostFixture(input, input.capture, input.playback(), input.original)
        val handle = fixture.resolveVault() // Valid positive path must finish BEFORE mutating readbacks.
        val resource = checkNotNull(fixture.resource)
        val ownerBefore = handle.capture.network.local.localOwner
        when (change) {
            ResolverGuardChange.OWNER -> fixture.currentAccount = fixture.currentAccount.copy(
                localOwner = LocalOwnerKey.Portable(resolverHostId(30)))
            ResolverGuardChange.VETO -> fixture.currentAccount = fixture.currentAccount.copy(
                localGuard = fixture.currentAccount.localGuard.copy(
                    revision = DesktopMediaLimits.next(fixture.currentAccount.localGuard.revision),
                    veto = LocalMediaVeto.ACCOUNT_REVOKED))
            ResolverGuardChange.PROJECTION -> fixture.currentProjection = checkNotNull(fixture.currentProjection).copy(
                projectionRevision = DesktopMediaLimits.next(checkNotNull(fixture.currentProjection).projectionRevision),
                recordingId = input.reassigned.recordingId,
                userTrackRefRowVersion = input.reassigned.userTrackRefRowVersion)
            ResolverGuardChange.J_LEASE -> fixture.currentAccount = fixture.currentAccount.copy(
                leaseRevision = DesktopMediaLimits.next(fixture.currentAccount.leaseRevision))
            ResolverGuardChange.M5 -> fixture.m5Readback = null
            ResolverGuardChange.D1 -> fixture.d1Readback = null
            ResolverGuardChange.B1 -> fixture.b1Readback = null
            ResolverGuardChange.RESOURCE -> resource.fence = resource.fence.copy(
                generation = DesktopMediaLimits.next(resource.fence.generation))
        }
        val expected = when (change) {
            ResolverGuardChange.VETO -> MediaCode.AUTHORIZATION_DENIED
            ResolverGuardChange.PROJECTION -> MediaCode.RECONCILIATION_REQUIRED
            ResolverGuardChange.M5, ResolverGuardChange.D1, ResolverGuardChange.B1 -> MediaCode.CAPABILITY_UNAVAILABLE
            ResolverGuardChange.RESOURCE -> MediaCode.RESOURCE_STALE
            else -> MediaCode.OWNER_STALE
        }
        assertUseRejected(handle, expected)
        assertEquals(0, resource.sourceUses)
        assertEquals(0, resource.retireEffects)
        assertEquals(0, resource.physicalReleases)
        assertTrue(fixture.leases.any { it === resource })
        assertEquals(ownerBefore, handle.capture.network.local.localOwner)
        assertSame(fixture.acceptedEvidence, handle.evidence)
        // One unconfirmed owner deliberately survives the handle's drain result.
        resource.releaseConfirmed = change != ResolverGuardChange.OWNER
        val result = handle.awaitReleased()
        if (change == ResolverGuardChange.OWNER) {
            assertEquals(MediaHandleRelease.FAILED_RETAINED, result)
            assertEquals(0, resource.physicalReleases)
            assertEquals(0, fixture.leaseReleases.count { it === resource })
            assertTrue(fixture.leases.any { it === resource })
            // The original process owner later settles THIS exact lease; no new-account adoption.
            resource.releaseConfirmed = true
            assertEquals(MediaHandleRelease.RELEASED, resource.awaitReleased())
            fixture.settlement.releaseLeaseAfterConfirmedSettlement(resource)
        } else assertEquals(MediaHandleRelease.RELEASED, result)
        fixture.assertSettled(resource)
        assertEquals(1, resource.retireEffects)
        assertEquals(0, resource.sourceUses)
    }
}

private suspend fun assertLocalGuardsBeforeUse(input: DesktopMediaResolverHostInputs): Unit {
    for (change in listOf("none", "owner", "veto", "lease", "file-identity", "read-lease")) {
        val intent = SourceIntent.AccountCopy(resolverHostId(2))
        val fixture = ResolverHostFixture(input, input.capture, intent, input.original)
        fixture.online = false
        fixture.remoteReadbacksAvailable = false
        val file = fixture.installCopy(input.originalAcquisition(), null, null)
        val selected = SelectionCapture.AccountLocal(input.capture.fence, input.capture.network.local)
        val handle = (fixture.resolver.resolve(selected, intent) as SourceResolution.Allow).handle
        assertEquals(1, file.identityChecks)
        when (change) {
            "owner" -> fixture.currentAccount = fixture.currentAccount.copy(localOwner = LocalOwnerKey.Portable(resolverHostId(30)))
            "veto" -> fixture.currentAccount = fixture.currentAccount.copy(localGuard = fixture.currentAccount.localGuard.copy(
                revision = DesktopMediaLimits.next(fixture.currentAccount.localGuard.revision), veto = LocalMediaVeto.DELETION_PENDING))
            "lease" -> fixture.currentAccount = fixture.currentAccount.copy(leaseRevision = DesktopMediaLimits.next(fixture.currentAccount.leaseRevision))
            "file-identity" -> file.currentFileIdentity = "1:3"
            "read-lease" -> file.readLeaseCurrent = false
        }
        if (change == "none") {
            handle.withUse { assertSame(file.managed, it) }
            assertEquals(1, file.fileUses)
            assertEquals(2, file.identityChecks)
        } else {
            assertUseRejected(handle, when (change) {
                "veto" -> MediaCode.AUTHORIZATION_DENIED
                "file-identity", "read-lease" -> MediaCode.SOURCE_UNKNOWN
                else -> MediaCode.OWNER_STALE
            })
            assertEquals(0, file.fileUses)
        }
        assertEquals(0, file.physicalReleases)
        assertTrue(file.descriptorHeld)
        assertTrue(fixture.leases.any { it === file })
        assertEquals(0, fixture.networkReadbackChecks)
        assertEquals(listOf(ResolverHostCall.LOCAL), fixture.starts.map { it.kind })
        assertEquals(MediaHandleRelease.RELEASED, handle.awaitReleased())
        fixture.assertSettled(file)
    }

    // ROOT-only has an independent LOCAL capture and reviewed source-copy readback, no remote tuple.
    val rootOnly = input.rootOnlyCapture
    val intent = SourceIntent.AccountCopy(resolverHostId(2))
    val fixture = ResolverHostFixture(input, rootOnly, intent, null)
    fixture.online = false
    fixture.remoteReadbacksAvailable = false
    assertTrue(rootOnly.account.localOwner is LocalOwnerKey.Portable)
    assertNull(rootOnly.account.originLocalGuard)
    assertNull(fixture.currentProjection)
    assertNull(fixture.m5Readback)
    assertNull(fixture.d1Readback)
    assertNull(fixture.b1Readback)
    val file = fixture.installExplicitSourceCopy(input.rootOnlyRepresentation)
    val handle = (fixture.resolver.resolve(rootOnly, intent) as SourceResolution.Allow).handle
    try {
        assertTrue(handle is RetainedAccountHandle)
        assertNull((handle as RetainedAccountHandle).evidence)
        assertEquals(MediaCopyAcquisition.EXPLICIT_SOURCE_COPY, file.ownership.acquisition)
        assertNotNull(file.ownership.materializationRevision)
        assertNull(file.ownership.originalEvidence)
        assertNull(file.ownership.attachment)
        assertNull(file.ownership.redirect)
        assertEquals(rootOnly.account, file.ownership.account)
        assertEquals(MediaRepresentationKind.INDEPENDENT_LOCAL, handle.representation.kind)
        handle.withUse { assertSame(file.managed, it) }
        assertEquals(2, file.identityChecks)
        assertEquals(1, file.fileUses)
        assertEquals(0, file.physicalReleases)
        assertTrue(file.descriptorHeld)
        assertTrue(fixture.leases.any { it === file })
        assertEquals(listOf(ResolverHostCall.LOCAL), fixture.starts.map { it.kind })
        assertTrue(fixture.starts.all { it.capture === rootOnly })
        assertEquals(0, fixture.networkReadbackChecks)
        assertEquals(0, fixture.startedResources)
        assertNull(fixture.acceptedEvidence)
        assertNull(fixture.inspection)
        assertNull(fixture.resource)
    } finally {
        assertEquals(MediaHandleRelease.RELEASED, handle.awaitReleased())
    }
    fixture.assertSettled(file)
}

private suspend fun assertCanonicalAttachmentReadbacks(input: DesktopMediaResolverHostInputs): Unit {
    // Ordinary network reassignment has valid wire/format/HEAD-capable content, but stale J R1.
    val reassigned = ResolverHostFixture(input, input.capture, input.playback(), input.reassigned)
    assertUnknown(reassigned.resolver.resolve(reassigned.capture, reassigned.intent), MediaCode.RECONCILIATION_REQUIRED)
    assertEquals(listOf(ResolverHostCall.LOCAL, ResolverHostCall.SNAPSHOT), reassigned.starts.map { it.kind })
    assertNull(reassigned.inspection)
    assertNull(reassigned.resource)
    assertTrue(reassigned.work.all { it.terminal && reassigned.workReleases.count { released -> released === it } == 1 })
    assertTrue(reassigned.leases.isEmpty())

    // R2/V2 really reaches matching HEAD/acquire/use after ordinary J repair and a new selection.
    // This positive control rules out a bad R2 wire/format/authority fixture causing the refusal.
    val repairedCapture = input.captureFor(input.reassigned).copy(fence = input.capture.fence.copy(
        operationId = resolverHostId(14), selectionRevision = DesktopMediaLimits.next(input.capture.fence.selectionRevision),
        sourceRevision = DesktopMediaLimits.next(input.capture.fence.sourceRevision)))
    val repairedIntent = SourceIntent.VaultPlayback(repairedCapture.network.projection, resolverHostId(13))
    val repaired = ResolverHostFixture(input, repairedCapture, repairedIntent, input.reassigned)
    val repairedHandle = repaired.resolveVault()
    repaired.assertNetworkOrder(localFirst = true)
    repairedHandle.withUse { assertEquals(input.reassigned.audioVariantId, (it as MediaRuntimeSource.Network).variantId) }
    assertEquals(MediaHandleRelease.RELEASED, repairedHandle.awaitReleased())
    repaired.assertSettled(checkNotNull(repaired.resource))

    val original = input.originalAcquisition()
    val bytes = original.snapshot.originalResponseBytes()
    // CURRENT J and a valid R2/V2 snapshot cannot retarget this original R1/V1 download.
    // Removing sameAcquisitionTarget must reach forbidden HEAD/acquire and fail this control.
    val downloadCapture = repairedCapture.copy(fence = repairedCapture.fence.copy(
        operationId = resolverHostId(15), queueId = null, entryId = null, listenId = null))
    val downloadIntent = SourceIntent.BoundVaultDownload(resolverHostId(3), 2, original.evidenceId,
        original, resolverHostId(16), downloadCapture.network.projection)
    DesktopMediaSourcePolicy.requireIntent(downloadCapture, downloadIntent)
    val reassignedDownload = ResolverHostFixture(input, downloadCapture, downloadIntent, input.reassigned)
    assertEquals(downloadIntent.currentProjection, reassignedDownload.currentProjection)
    assertUnknown(reassignedDownload.resolver.resolve(downloadCapture, downloadIntent), MediaCode.RECONCILIATION_REQUIRED)
    assertEquals(listOf(ResolverHostCall.SNAPSHOT), reassignedDownload.starts.map { it.kind })
    assertNull(reassignedDownload.acceptedEvidence)
    assertNull(reassignedDownload.inspection)
    assertNull(reassignedDownload.resource)
    assertEquals(0, reassignedDownload.startedResources)
    assertTrue(reassignedDownload.works.isEmpty())
    assertTrue(reassignedDownload.work.all { ticket ->
        ticket.terminal && reassignedDownload.workReleases.count { it === ticket } == 1
    })
    assertTrue(reassignedDownload.leases.isEmpty())
    assertSame(original, downloadIntent.original)
    assertEquals(original.evidenceId, downloadIntent.sourceEvidenceId)
    assertArrayEquals(bytes, original.snapshot.originalResponseBytes())
    assertEquals(input.original.userTrackRefRowVersion, original.observedUtrServerRowVersion)
    assertEquals(input.original.recordingId, original.observedRecordingId)
    assertEquals(input.original.recordingId, original.snapshot.recordingId)
    assertEquals(input.original.audioVariantId, original.snapshot.audioVariantId)

    val canonicalCapture = input.captureFor(input.canonical)
    val projection = canonicalCapture.network.projection
    val attachmentEvidence = input.evidence(input.canonical, projection, resolverHostId(10),
        original.intentId, MediaObservationKind.ATTACHMENT)
    val redirect = MediaCanonicalAttachmentReadback(input.original.recordingId, input.canonical.recordingId,
        11, attachmentEvidence)
    val localCapture = SelectionCapture.AccountLocal(canonicalCapture.fence, canonicalCapture.network.local)
    val trackIntent = SourceIntent.VaultPlayback(projection, resolverHostId(1))
    for (case in listOf("canonical", "missing", "wrong-alias", "changed-variant", "changed-content", "undone", "detached-track")) {
        val fixture = ResolverHostFixture(input, canonicalCapture, trackIntent, input.canonical)
        fixture.remoteReadbacksAvailable = false
        fixture.online = false
        val actualRedirect = when (case) {
            "missing", "detached-track" -> null
            "wrong-alias" -> MediaCanonicalAttachmentReadback(resolverHostId(31), input.canonical.recordingId, 11, attachmentEvidence)
            "changed-variant" -> MediaCanonicalAttachmentReadback(input.original.recordingId, input.canonical.recordingId, 11,
                input.evidence(input.reassigned, projection, resolverHostId(11), original.intentId, MediaObservationKind.ATTACHMENT))
            "changed-content" -> {
                // Deliberately contradictory content observation; keep the actual retained copy
                // and all authority facts valid so the canonical SAME-content check is reached.
                val raw = input.canonical.originalResponseBytes().toString(Charsets.UTF_8)
                val token = Regex("\"total_bytes\"\\s*:\\s*[0-9]+")
                assertEquals(1, token.findAll(raw).count())
                val length = if (input.canonical.totalBytes == 1L) 2L else input.canonical.totalBytes - 1L
                val changed = MediaSourceSnapshotV1.decode(token.replace(raw) { "\"total_bytes\":$length" }.toByteArray(Charsets.UTF_8))
                assertEquals(original.snapshot.audioVariantId, changed.audioVariantId)
                assertFalse(changed.sameContent(original.snapshot))
                MediaCanonicalAttachmentReadback(input.original.recordingId, input.canonical.recordingId, 11,
                    input.evidence(changed, projection, resolverHostId(12), original.intentId, MediaObservationKind.ATTACHMENT))
            }
            else -> redirect
        }
        val attachment = if (case == "detached-track") null else projection
        val file = fixture.installCopy(original, attachment, actualRedirect)
        if (case == "undone") fixture.redirectReadbackRevision = null
        val resolution = fixture.resolver.resolve(localCapture, trackIntent)
        if (case == "canonical") {
            val handle = (resolution as SourceResolution.Allow).handle
            assertTrue(handle is RetainedAccountHandle)
            assertSame(original, handle.evidence)
            handle.withUse { assertSame(file.managed, it) }
            assertEquals(2, file.identityChecks)
            assertEquals(1, file.fileUses)
            assertSame(redirect, file.ownership!!.redirect)
            assertSame(attachmentEvidence, file.ownership!!.redirect!!.evidence)
            assertEquals(input.original.audioVariantId, attachmentEvidence.snapshot.audioVariantId)
            assertTrue(attachmentEvidence.snapshot.sameContent(original.snapshot))
            assertEquals(0, file.physicalReleases)
            assertEquals(MediaHandleRelease.RELEASED, handle.awaitReleased())
        } else {
            assertUnknown(resolution, MediaCode.RECONCILIATION_REQUIRED)
            assertEquals(0, file.fileUses)
        }
        fixture.assertSettled(file)
        assertEquals(listOf(ResolverHostCall.LOCAL), fixture.starts.map { it.kind })
        assertEquals(0, fixture.networkReadbackChecks)
        assertArrayEquals(bytes, original.snapshot.originalResponseBytes())
        assertEquals(input.original.recordingId, original.observedRecordingId)
    }

    // A DETACHED copy can be opened by its original owner with no remote capture at all.
    val detached = ResolverHostFixture(input, canonicalCapture, SourceIntent.AccountCopy(resolverHostId(2)), input.canonical)
    detached.remoteReadbacksAvailable = false
    detached.online = false
    val file = detached.installCopy(original, null, null)
    val handle = (detached.resolver.resolve(localCapture, detached.intent) as SourceResolution.Allow).handle
    assertTrue(handle is RetainedAccountHandle)
    assertNull(file.ownership!!.attachment)
    assertNull(file.ownership!!.redirect)
    handle.withUse { assertSame(file.managed, it) }
    assertSame(original, handle.evidence)
    assertEquals(listOf(ResolverHostCall.LOCAL), detached.starts.map { it.kind })
    assertEquals(0, detached.networkReadbackChecks)
    assertEquals(MediaHandleRelease.RELEASED, handle.awaitReleased())
    detached.assertSettled(file)
    assertArrayEquals(bytes, original.snapshot.originalResponseBytes())
    // OPEN M1-J-CANONICAL-TRANSACTION: these are authenticated-owner seam inputs, not a J
    // redirect producer/transaction, attachment invalidation, HTTP redirect or publication proof.
}

private suspend fun assertLateAcquireCancellation(input: DesktopMediaResolverHostInputs,
    releaseConfirmed: Boolean): Unit {
    val fixture = ResolverHostFixture(input, input.capture, input.playback(), input.original)
    fixture.holdAcquire = true
    // This owned test job is independent: a cleanup timeout must fail, not enter an implicit
    // coroutineScope child join that waits forever for a NonCancellable resolver settlement.
    val owner = SupervisorJob()
    val result = CoroutineScope(currentCoroutineContext() + owner).async(start = CoroutineStart.UNDISPATCHED) {
        fixture.resolver.resolve(fixture.capture, fixture.intent)
    }
    try {
        val pending = checkNotNull(fixture.acquireWork)
        val lease = checkNotNull(fixture.resource)
        lease.releaseConfirmed = releaseConfirmed
        assertFalse(pending.terminal)
        assertFalse(result.isCompleted)
        assertTrue(fixture.works.any { it === pending })
        assertFalse(fixture.leases.any { it === lease }) // Still owned by the start port's work result.
        assertEquals(0, lease.physicalReleases)
        result.cancel()
        withTimeout(5_000) { pending.cancellationRequested.await() }
        assertFalse(result.isCompleted) // Cancellation has not discarded the late ACTIVE result.
        assertEquals(1, pending.cancellationRequests)
        assertEquals(0, fixture.workReleases.count { it === pending })
        assertEquals(0, lease.physicalReleases)
        fixture.currentAccount = fixture.currentAccount.copy(localOwner = LocalOwnerKey.Portable(resolverHostId(30)))
        pending.complete(MediaResourceAcquisition.Active(lease))
        withTimeout(5_000) { result.join() }
        try { result.await(); fail("Cancellation was swallowed") } catch (_: CancellationException) { /* Expected. */ }
        assertTrue(pending.terminal)
        assertEquals(2, pending.awaits)
        assertEquals(1, fixture.workReleases.count { it === pending })
        assertEquals(1, lease.retireEffects)
        assertEquals(0, lease.sourceUses)
        assertEquals(input.capture.network.local.localOwner, lease.admittedOwner)
        assertNotEquals(fixture.currentAccount.localOwner, lease.admittedOwner)
        if (!releaseConfirmed) {
            assertEquals(0, lease.physicalReleases)
            assertTrue(fixture.leases.any { it === lease })
            assertEquals(0, fixture.leaseReleases.count { it === lease })
            lease.releaseConfirmed = true
            assertEquals(MediaHandleRelease.RELEASED, lease.awaitReleased())
            fixture.settlement.releaseLeaseAfterConfirmedSettlement(lease)
        }
        fixture.assertSettled(lease)
        assertEquals(1, fixture.startedResources)
        assertArrayEquals(input.original.originalResponseBytes(), checkNotNull(fixture.acceptedEvidence).snapshot.originalResponseBytes())
    } finally {
        result.cancel()
        try {
            fixture.acquireWork?.completeIfPending(MediaResourceAcquisition.Active(checkNotNull(fixture.resource)))
            withContext(NonCancellable) {
                withTimeout(5_000) {
                    result.join() // Actual completion, never cancellation alone or an ignored timeout.
                    assertTrue(result.isCompleted)
                    assertTrue(fixture.work.all { ticket ->
                        ticket.terminal && fixture.workReleases.count { it === ticket } == 1
                    })
                    fixture.resource?.let { lease ->
                        lease.releaseConfirmed = true
                        lease.retire()
                        assertEquals(MediaHandleRelease.RELEASED, lease.awaitReleased())
                        if (fixture.leases.any { it === lease })
                            fixture.settlement.releaseLeaseAfterConfirmedSettlement(lease)
                        fixture.assertSettled(lease)
                    }
                    assertTrue(fixture.works.isEmpty())
                    assertTrue(fixture.leases.isEmpty())
                }
            }
        } finally { owner.cancel() }
    }
}

private fun assertUseRejected(handle: MediaSourceHandle, code: MediaCode): Unit {
    var delivered = 0
    try { handle.withUse { delivered++; Unit }; fail("Stale handle admitted a resource") }
    catch (failure: MediaSourceException) { assertEquals(code, failure.failure.code) }
    assertEquals(0, delivered)
}

private fun assertUnknown(resolution: SourceResolution, code: MediaCode): Unit {
    assertTrue(resolution is SourceResolution.Unknown)
    assertEquals(code, (resolution as SourceResolution.Unknown).failure.code)
}

private fun resolverHostId(value: Int): MediaId = MediaId("71000000-0000-4000-8000-${value.toString().padStart(12, '0')}")

private enum class ResolverHostCall { LOCAL, SNAPSHOT, JOURNAL, HEAD, ACQUIRE }
private class ResolverStart(val kind: ResolverHostCall, val capture: SelectionCapture,
    val intent: SourceIntent?, val evidence: MediaSourceEvidence?, val admission: MediaLiveAdmission)

/** Synthetic K/J owner readbacks. No production default-success adapter, SQL or Keychain calls. */
private class ResolverHostFixture<C : SelectionCapture>(val input: DesktopMediaResolverHostInputs,
    val capture: C, val intent: SourceIntent, val snapshot: MediaSourceSnapshotV1?) {
    private val networkCapture: VaultNetworkCapture? = (capture as? SelectionCapture.VaultNetwork)?.network
    var now: Long = 1_000
    var online: Boolean = true
    var currentAccount: AccountLocalCapture = when (capture) {
        is SelectionCapture.AccountLocal -> capture.account
        is SelectionCapture.VaultNetwork -> capture.network.local
        else -> error("Unexpected standalone fixture")
    }
    var currentProjection: JMediaProjectionCapture? = networkCapture?.projection
    var m5Readback: M5ProfileCapture? = networkCapture?.m5Profile
    var d1Readback: DeviceContractV2Negotiation.CapabilityObservation? = networkCapture?.d1b1Guards?.d1ContentGuard
    var b1Readback: SyncBindingContractV2.RevisionGuard? = networkCapture?.d1b1Guards?.b1Guard
    var remoteReadbacksAvailable: Boolean = true
    var redirectReadbackRevision: Long? = 11
    var networkReadbackChecks: Int = 0
    val starts: MutableList<ResolverStart> = mutableListOf()
    val chronology: MutableList<String> = mutableListOf()
    val work: MutableList<ResolverWork<*>> = mutableListOf()
    val works: MutableList<MediaOwnedWork<*>> = mutableListOf()
    val workReleases: MutableList<MediaOwnedWork<*>> = mutableListOf()
    val leases: MutableList<MediaOwnedLease> = mutableListOf()
    val leaseReleases: MutableList<MediaOwnedLease> = mutableListOf()
    var acceptedEvidence: MediaSourceEvidence? = null
    var inspection: ResolverInspection? = null
    var resource: ResolverResource? = null
    var file: ResolverFile? = null
    var holdAcquire: Boolean = false
    var acquireWork: ResolverWork<MediaResourceAcquisition>? = null
    var startedResources: Int = 0
    private val commonGate: Any = Any()
    private var currentAdmission: ResolverAdmission? = null

    private class ResolverAdmission(val request: MediaAdmissionRequest, override val networkAvailable: Boolean) : MediaLiveAdmission {
        override val authorityDeadlineMonotonicMs: Long = 61_000
    }

    private val authority: DesktopMediaOwnerAdmissionPort = object : DesktopMediaOwnerAdmissionPort {
        override fun <T> withCurrent(request: MediaAdmissionRequest, action: (MediaLiveAdmission) -> T): T = synchronized(commonGate) {
            check(currentAdmission == null)
            val selected = request.capture
            val requestedIntent = request.intent
            val local = when (selected) {
                is SelectionCapture.AccountLocal -> selected.account
                is SelectionCapture.VaultNetwork -> selected.network.local
                else -> error("Unexpected standalone authority")
            }
            mediaRequire(currentAccount.localGuard.veto == LocalMediaVeto.NONE, MediaCode.AUTHORIZATION_DENIED)
            mediaRequire(local == currentAccount && request.capture.fence == capture.fence, MediaCode.OWNER_STALE)
            DesktopMediaSourcePolicy.requireLocalGuard(currentAccount, downloaded = request.localLease?.ownership?.acquisition ==
                MediaCopyAcquisition.USER_DOWNLOAD)
            if (requestedIntent is SourceIntent.VaultPlayback) mediaRequire(requestedIntent.projection == currentProjection,
                MediaCode.RECONCILIATION_REQUIRED)
            val redirect = request.localLease?.ownership?.redirect
            if (redirect != null) mediaRequire(redirectReadbackRevision == redirect.redirectProjectionRevision,
                MediaCode.RECONCILIATION_REQUIRED)
            if (selected is SelectionCapture.VaultNetwork) {
                networkReadbackChecks++
                val actual = selected.network
                mediaRequire(remoteReadbacksAvailable && actual == networkCapture, MediaCode.OWNER_STALE)
                mediaRequire(actual.projection == currentProjection, MediaCode.RECONCILIATION_REQUIRED)
                mediaRequire(m5Readback === actual.m5Profile && d1Readback === actual.d1b1Guards.d1ContentGuard &&
                    b1Readback === actual.d1b1Guards.b1Guard, MediaCode.CAPABILITY_UNAVAILABLE)
                val wallNow = input.acceptedAt.plusMillis(now - 1_000)
                mediaRequire(wallNow >= actual.m5Profile.issuedAt && wallNow < actual.m5Profile.expiresAt,
                    MediaCode.CAPABILITY_UNAVAILABLE)
                mediaRequire(wallNow < input.accessExpiresAt && wallNow < input.sessionExpiresAt, MediaCode.AUTH_REQUIRED)
                mediaRequire(actual.remoteVeto == LocalMediaVeto.NONE, MediaCode.AUTHORIZATION_DENIED)
            }
            val live = ResolverAdmission(request, online)
            currentAdmission = live
            try { action(live) } finally { currentAdmission = null }
        }
    }

    val settlement: DesktopMediaSettlementPort = object : DesktopMediaSettlementPort {
        override fun retainWork(work: MediaOwnedWork<*>): Unit {
            assertFalse(works.any { it === work }); works.add(work)
        }
        override fun retainLease(lease: MediaOwnedLease): Unit {
            assertFalse(leases.any { it === lease }); leases.add(lease)
        }
        override fun releaseWorkAfterTerminal(work: MediaOwnedWork<*>): Unit {
            assertTrue((work as ResolverWork<*>).terminal)
            assertTrue(works.remove(work)); assertFalse(workReleases.any { it === work }); workReleases.add(work)
        }
        override fun releaseLeaseAfterConfirmedSettlement(lease: MediaOwnedLease): Unit {
            assertEquals(1, (lease as ResolverLease).physicalReleases)
            assertTrue(leases.remove(lease)); assertFalse(leaseReleases.any { it === lease }); leaseReleases.add(lease)
        }
    }

    private fun start(kind: ResolverHostCall, selected: SelectionCapture, sourceIntent: SourceIntent?,
        evidence: MediaSourceEvidence?, live: MediaLiveAdmission, step: MediaAdmissionStep): Unit {
        val held = checkNotNull(currentAdmission)
        assertSame(held, live)
        assertSame(held.request.capture, selected)
        assertSame(held.request.intent, sourceIntent)
        assertEquals(step, held.request.step)
        assertTrue(starts.none { it.admission === live })
        starts.add(ResolverStart(kind, selected, sourceIntent, evidence, live))
        chronology.add("start-$kind")
    }
    private fun <T> completed(value: T): ResolverWork<T> = ResolverWork<T>().also { it.complete(value); work.add(it) }

    val resolver: DesktopVaultSourceResolver = DesktopVaultSourceResolver(authority,
        object : DesktopMediaLocalSourcePort {
            override fun startLocalRead(capture: SelectionCapture, intent: SourceIntent,
                admission: MediaLiveAdmission): MediaOwnedWork<MediaLocalLookup> {
                start(ResolverHostCall.LOCAL, capture, intent, null, admission, MediaAdmissionStep.LOCAL_LOOKUP)
                return completed(file?.let { MediaLocalLookup.Found(it) } ?: MediaLocalLookup.Missing(LocalAvailability.ABSENT))
            }
        },
        object : DesktopMediaAuthenticatedControlPort {
            override fun startSnapshot(capture: SelectionCapture.VaultNetwork, request: MediaSourceSnapshotRequestV1,
                admission: MediaLiveAdmission, deadlineMonotonicMs: Long): MediaOwnedWork<MediaSnapshotHttpReply> {
                start(ResolverHostCall.SNAPSHOT, capture, currentAdmission!!.request.intent, null, admission, MediaAdmissionStep.SNAPSHOT)
                val expected = checkNotNull(snapshot)
                assertEquals(expected.userTrackRefId, request.userTrackRefId)
                assertEquals("GET", request.method)
                assertEquals("/api/v1/media-source/user-tracks/${expected.userTrackRefId.value}/snapshot", request.path)
                assertNull(request.query); assertEquals(0, request.bodyBytes); assertFalse(request.followRedirects)
                assertEquals("application/json", request.accept)
                assertTrue(deadlineMonotonicMs > now && deadlineMonotonicMs - now <= DesktopMediaLimits.CONTROL_MS)
                return completed(MediaSnapshotHttpReply(200, "application/json", null, "private, no-store", "no-cache",
                    "Authorization", 512, 128, expected.originalResponseBytes()))
            }
        },
        object : DesktopMediaJournalEvidencePort {
            override fun startAcceptObservation(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
                observation: MediaSourceObservation, admission: MediaLiveAdmission): MediaOwnedWork<MediaSourceEvidence> {
                start(ResolverHostCall.JOURNAL, capture, intent, null, admission, MediaAdmissionStep.SNAPSHOT_RESULT)
                assertSame(capture, observation.capture)
                assertEquals(now, observation.dispatchedMonotonicMs)
                assertArrayEquals(checkNotNull(snapshot).originalResponseBytes(), observation.snapshot.originalResponseBytes())
                DesktopMediaSourcePolicy.requireSnapshot(capture, intent, observation.snapshot)
                val download = intent as? SourceIntent.BoundVaultDownload
                val evidence = input.evidence(observation.snapshot, checkNotNull(currentProjection),
                    download?.admissionEvidenceId ?: resolverHostId(7), download?.intentId,
                    if (download == null) MediaObservationKind.ACQUISITION else MediaObservationKind.CONTINUATION)
                DesktopMediaSourcePolicy.requireEvidence(capture, intent, observation, evidence)
                acceptedEvidence = evidence
                return completed(evidence)
            }
        },
        object : DesktopMediaHeadPort {
            override fun startHead(capture: SelectionCapture.VaultNetwork, intent: SourceIntent, evidence: MediaSourceEvidence,
                admission: MediaLiveAdmission, deadlineMonotonicMs: Long): MediaOwnedWork<MediaHeadInspection> {
                start(ResolverHostCall.HEAD, capture, intent, evidence, admission, MediaAdmissionStep.HEAD)
                assertSame(acceptedEvidence, evidence)
                assertTrue(deadlineMonotonicMs > now)
                val expected = checkNotNull(snapshot)
                val value = ResolverInspection(MediaHeadMetadata(200, "bytes", expected.totalBytes,
                    "\"sha256-${expected.sha256}\"", expected.mediaType, null, 0, 512, 128), chronology)
                inspection = value
                return completed(value)
            }
        },
        object : DesktopMediaResourceAdmissionPort {
            override fun startAcquire(capture: SelectionCapture.VaultNetwork, intent: SourceIntent, evidence: MediaSourceEvidence,
                representation: MediaRepresentation, admission: MediaLiveAdmission,
                deadlineMonotonicMs: Long): MediaOwnedWork<MediaResourceAcquisition> {
                start(ResolverHostCall.ACQUIRE, capture, intent, evidence, admission, MediaAdmissionStep.ACQUIRE)
                assertSame(acceptedEvidence, evidence)
                assertEquals(1, checkNotNull(inspection).physicalReleases)
                assertEquals(1, leaseReleases.count { it === inspection })
                assertTrue(deadlineMonotonicMs > now)
                val download = intent as? SourceIntent.BoundVaultDownload
                val fence = if (download != null) MediaResourceFence(MediaResourceType.DOWNLOAD_INTENT,
                    download.intentId, capture.fence.operationId, resolverHostId(8), 1, null,
                    download.original.snapshot.audioVariantId, null, null)
                else MediaResourceFence(MediaResourceType.PLAY_INSTANCE, (intent as SourceIntent.VaultPlayback).playInstanceId,
                    capture.fence.operationId, resolverHostId(8), 1, 0, null, checkNotNull(snapshot).recordingId, null)
                val lease = ResolverResource(fence, representation, evidence.snapshot.audioVariantId, capture,
                    input.accessExpiresAt.toEpochMilli(), chronology) { held ->
                    val gate = checkNotNull(currentAdmission)
                    assertEquals(MediaAdmissionStep.USE, gate.request.step)
                    assertSame(held, gate.request.resourceLease)
                    assertSame(evidence, gate.request.evidence)
                }
                startedResources++
                resource = lease
                val ticket = ResolverWork<MediaResourceAcquisition>()
                acquireWork = ticket
                work.add(ticket)
                if (!holdAcquire) ticket.complete(MediaResourceAcquisition.Active(lease))
                return ticket
            }
        },
        object : DesktopMediaFormatAdmissionPort {
            override fun admit(codec: String, container: String, mediaType: String): MediaFormat {
                // A single required valid fixture format; this is no claim about native decoding.
                mediaRequire(codec == "pcm_s16le" && container == "wav" && mediaType == "audio/wav", MediaCode.UNSUPPORTED_MEDIA)
                return MediaFormat.WAV
            }
        }, settlement, object : DesktopMediaResolutionClock { override fun monotonicMs(): Long = now })

    fun installCopy(original: MediaSourceEvidence, attachment: JMediaProjectionCapture?,
        redirect: MediaCanonicalAttachmentReadback?): ResolverFile {
        val publication = resolverHostId(9)
        val acquisition = checkNotNull(original.intentId)
        val proof = MediaOwnedCopyReadback(resolverHostId(2), currentAccount, MediaCopyAcquisition.USER_DOWNLOAD,
            acquisition, acquisition, null, 1, 1, publication, publication,
            true, true, false, original, attachment, redirect)
        return ResolverFile(MediaRepresentation(original.snapshot.sha256, original.snapshot.totalBytes,
            original.snapshot.mediaType, MediaFormat.WAV, MediaRepresentationKind.VAULT_CONTENT), proof, chronology) { held ->
                val gate = checkNotNull(currentAdmission)
                assertTrue(gate.request.step in setOf(MediaAdmissionStep.LOCAL_RESULT, MediaAdmissionStep.USE))
                assertSame(held, gate.request.localLease)
            }.also { file = it }
    }

    fun installExplicitSourceCopy(representation: MediaRepresentation): ResolverFile {
        assertTrue(currentAccount.localOwner is LocalOwnerKey.Portable)
        assertNull(currentAccount.originLocalGuard)
        assertEquals(MediaRepresentationKind.INDEPENDENT_LOCAL, representation.kind)
        val acquisition = resolverHostId(17)
        val publication = resolverHostId(18)
        val proof = MediaOwnedCopyReadback(resolverHostId(2), currentAccount, MediaCopyAcquisition.EXPLICIT_SOURCE_COPY,
            acquisition, acquisition, 1, 1, 1, publication, publication, true, true, false, null, null, null)
        return ResolverFile(representation, proof, chronology) { held ->
            val gate = checkNotNull(currentAdmission)
            assertTrue(gate.request.step in setOf(MediaAdmissionStep.LOCAL_RESULT, MediaAdmissionStep.USE))
            assertSame(held, gate.request.localLease)
        }.also { file = it }
    }

    suspend fun resolveVault(): VaultPermit {
        val resolution = resolver.resolve(capture, intent)
        assertTrue(resolution is SourceResolution.Allow)
        val handle = (resolution as SourceResolution.Allow).handle
        assertTrue(handle is VaultPermit)
        assertSame(acceptedEvidence, handle.evidence)
        assertEquals(checkNotNull(snapshot).sha256, handle.representation.digestSha256)
        assertTrue(works.isEmpty())
        return handle as VaultPermit
    }

    fun assertNetworkOrder(localFirst: Boolean): Unit {
        assertEquals((if (localFirst) listOf(ResolverHostCall.LOCAL) else emptyList()) + listOf(
            ResolverHostCall.SNAPSHOT, ResolverHostCall.JOURNAL, ResolverHostCall.HEAD, ResolverHostCall.ACQUIRE), starts.map { it.kind })
        for (call in starts.filter { it.kind != ResolverHostCall.LOCAL }) {
            assertSame(capture, call.capture)
            assertSame(intent, call.intent)
        }
        assertSame(starts.single { it.kind == ResolverHostCall.HEAD }.evidence,
            starts.single { it.kind == ResolverHostCall.ACQUIRE }.evidence)
        assertTrue(chronology.indexOf("release-head") < chronology.indexOf("start-ACQUIRE"))
        assertEquals(1, checkNotNull(inspection).retireEffects)
        assertEquals(1, checkNotNull(inspection).releaseWaits)
        assertEquals(1, leaseReleases.count { it === inspection })
        assertEquals(work.size, workReleases.size)
    }

    fun assertSettled(lease: ResolverLease): Unit {
        assertEquals(1, lease.physicalReleases)
        assertEquals(1, leaseReleases.count { it === lease })
        assertTrue(works.isEmpty())
        assertTrue(leases.isEmpty())
        assertTrue(work.all { ticket -> ticket.terminal && workReleases.count { it === ticket } == 1 })
    }
}

private class ResolverWork<T> : MediaOwnedWork<T> {
    private val result: CompletableDeferred<T> = CompletableDeferred()
    val cancellationRequested: CompletableDeferred<Unit> = CompletableDeferred()
    var cancellationRequests: Int = 0
    var awaits: Int = 0
    var terminal: Boolean = false
    fun complete(value: T): Unit { check(result.complete(value)) }
    fun completeIfPending(value: T): Boolean = result.complete(value)
    override fun requestCancellation(): Unit { cancellationRequests++; cancellationRequested.complete(Unit) }
    override suspend fun awaitTerminal(): T {
        awaits++
        return result.await().also { terminal = true }
    }
}

private abstract class ResolverLease(private val label: String, private val chronology: MutableList<String>) : MediaOwnedLease {
    var releaseConfirmed: Boolean = true
    var retireEffects: Int = 0
    var physicalReleases: Int = 0
    var releaseWaits: Int = 0
    override fun retire(): Unit {
        if (retireEffects == 0) { retireEffects++; chronology.add("retire-$label") }
    }
    override suspend fun awaitReleased(): MediaHandleRelease {
        releaseWaits++
        check(retireEffects == 1)
        if (!releaseConfirmed) return MediaHandleRelease.FAILED_RETAINED
        if (physicalReleases == 0) { onSettled(); physicalReleases++; chronology.add("release-$label") }
        return MediaHandleRelease.RELEASED
    }
    protected open fun onSettled(): Unit = Unit
}

private class ResolverInspection(override val metadata: MediaHeadMetadata, chronology: MutableList<String>) :
    ResolverLease("head", chronology), MediaHeadInspection

private class ResolverResource(override var fence: MediaResourceFence, val representation: MediaRepresentation,
    variantId: MediaId, capture: SelectionCapture.VaultNetwork, accessExpiresAtMs: Long, chronology: MutableList<String>,
    private val requireUseGate: (MediaActiveResourceLease) -> Unit) : ResolverLease("resource", chronology), MediaActiveResourceLease {
    override val copyId: MediaId = resolverHostId(2)
    val admittedOwner: LocalOwnerKey = capture.network.local.localOwner
    override val permitRevision: Long = 1
    override val authorityDeadlineMonotonicMs: Long = 61_000
    // Opaque non-credential sentinel only, never sent to any transport or printed.
    private val bearer: TransientMediaBearer = TransientMediaBearer("synthetic-never-a-token".toByteArray(Charsets.US_ASCII))
    val source: MediaRuntimeSource.Network = MediaRuntimeSource.Network(capture.network.streamPolicy,
        variantId, bearer, fence, capture.network.credentialGeneration, capture.network.sessionGeneration, accessExpiresAtMs)
    var sourceUses: Int = 0
    override fun <T> withNetworkSource(action: (MediaRuntimeSource.Network) -> T): T {
        requireUseGate(this)
        check(retireEffects == 0 && physicalReleases == 0)
        sourceUses++
        return action(source)
    }
    override fun onSettled(): Unit = bearer.close()
}

private class ResolverFile(override val representation: MediaRepresentation,
    override val ownership: MediaOwnedCopyReadback, chronology: MutableList<String>,
    private val requireReadGate: (MediaLocalReadLease) -> Unit) : ResolverLease("file", chronology), MediaLocalReadLease {
    override val copyId: MediaId = ownership.copyId
    override val leaseRevision: Long = 1
    val managed: MediaRuntimeSource.ManagedFile = MediaRuntimeSource.ManagedFile("/synthetic/remote-media-v1/retained/fixture.wav", "1:2")
    var currentFileIdentity: String = managed.fileIdentity
    var readLeaseCurrent: Boolean = true
    var descriptorHeld: Boolean = true
    private val verifiedRepresentation: MediaRepresentation = representation
    var identityChecks: Int = 0
    var fileUses: Int = 0
    override fun verifyCurrentFileIdentityAndIntegrity(): Unit {
        requireReadGate(this)
        mediaRequire(readLeaseCurrent && descriptorHeld && currentFileIdentity == managed.fileIdentity && representation == verifiedRepresentation,
            MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
        check(retireEffects == 0 && physicalReleases == 0)
        identityChecks++ // Synthetic file owner witness only: no IO/hash or metadata-as-integrity claim.
    }
    override fun <T> withManagedFile(action: (MediaRuntimeSource.ManagedFile) -> T): T {
        requireReadGate(this)
        check(identityChecks >= 2 && retireEffects == 0 && physicalReleases == 0)
        fileUses++
        return action(managed)
    }
    override fun onSettled(): Unit { descriptorHeld = false }
}

/*
 * OPEN / UNVERIFIED: M1-J-CANONICAL-TRANSACTION, real J media schema/migration and authenticated
 * redirect producer/undo/attachment invalidation, actual single-snapshot HTTP/PG/final-time and
 * HEAD/Range redirects, K custody/guard capacity/publication, native read decisions/drain/audio,
 * short-handle expiry and renewal orchestration, credential/attempt handoff, real file integrity/
 * publication, interop/codecs/seek/TLS/cost/quota, privacy and identified signed-package release.
 * No continuous-transfer proof, executable qualification, matrix point or release gate is earned.
 */
