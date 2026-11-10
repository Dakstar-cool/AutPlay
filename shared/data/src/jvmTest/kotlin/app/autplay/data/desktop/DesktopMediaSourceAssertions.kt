package app.autplay.data.desktop

import app.autplay.application.desktop.media.*
import org.junit.Assert.*

/**
 * FUTURE / UNCALLED / UNVERIFIED. No new test entry point and no composite was edited.
 * A later authorized fixture must supply real valid synthetic signed M5/current J/K readbacks,
 * server source tuples and actual bytes/digests. No crypto vector/hash is calculated at authoring.
 * Pure wire mutations below test syntax only, never claim signature/transport/ownership proof.
 * The required scenario driver must execute the real resolver and instrument its real injected
 * owners; returning scripted expected outcomes is not an implementation or a passing fixture.
 */
internal suspend fun desktopMediaSourceAssertions(fixture: DesktopMediaSourceFutureFixture): Unit {
    val raw = fixture.validSnapshotBytes()
    val parsed = MediaSourceSnapshotV1.decode(raw)
    val body = raw.toString(Charsets.UTF_8)
    val capture = fixture.capture
    val playback = fixture.playback
    val download = fixture.download
    fun bytes(value: String): ByteArray = value.toByteArray(Charsets.UTF_8)
    fun append(field: String): ByteArray = bytes(body.substringBeforeLast('}') + ",$field}")
    fun token(member: String, replacement: String): ByteArray {
        val pattern = Regex("\"$member\"\\s*:\\s*(?:\"[^\"]*\"|[0-9]+)")
        assertEquals(1, pattern.findAll(body).count())
        return bytes(pattern.replace(body) { "\"$member\":$replacement" })
    }
    fun rejects(rawBytes: ByteArray): Unit {
        try { MediaSourceSnapshotV1.decode(rawBytes); fail("Malformed wire admitted") }
        catch (failure: MediaSourceException) { assertEquals(MediaCode.PROTOCOL_ERROR, failure.failure.code) }
    }
    fun failure(code: MediaCode, action: () -> Unit): Unit {
        try { action(); fail("Stale source admitted") }
        catch (failure: MediaSourceException) { assertEquals(code, failure.failure.code) }
    }

    assertArrayEquals(raw, parsed.originalResponseBytes())
    val inputCopy = raw.copyOf()
    val immutable = MediaSourceSnapshotV1.decode(inputCopy)
    inputCopy.fill(0)
    val exportedCopy = immutable.originalResponseBytes()
    exportedCopy.fill(0)
    assertArrayEquals(raw, immutable.originalResponseBytes())
    assertFalse(immutable.toString().contains(parsed.sha256))
    val request = MediaSourceSnapshotRequestV1(parsed.userTrackRefId)
    assertEquals("GET", request.method)
    assertEquals("/api/v1/media-source/user-tracks/${parsed.userTrackRefId.value}/snapshot", request.path)
    assertNull(request.query); assertEquals(0, request.bodyBytes); assertFalse(request.followRedirects)

    rejects(byteArrayOf())
    rejects(byteArrayOf(0xc0.toByte(), 0xaf.toByte()))
    rejects(append("\"namespace\":\"autplay.media-source\""))
    rejects(append("\"\\u006eamespace\":\"autplay.media-source\""))
    rejects(append("\"unknown\":1"))
    rejects(token("codec", "{}"))
    rejects(token("container", "[]"))
    rejects(token("media_type", "null"))
    rejects(token("identity_epoch", "true"))
    rejects(token("identity_epoch", "\"1\""))
    for (number in listOf("0", "-1", "01", "1.0", "1e0", "9007199254740992", "9223372036854775808")) {
        rejects(token("user_track_ref_row_version", number))
    }
    rejects(token("wire_version", "2"))
    rejects(token("namespace", "\"autplay.remote-media\""))
    rejects(token("server_instance_id", "\"${parsed.serverInstanceId.value.replace('-', '_')}\""))
    rejects(token("sha256", "\"${parsed.sha256.dropLast(1)}G\""))
    rejects(token("codec", "\"\\uD800\""))
    rejects(token("codec", "\"\\uDC00\""))
    rejects(token("media_type", "\"${"\u00e9".repeat(101)}\""))
    rejects(token("codec", "\"${"\u00e9".repeat(51)}\""))
    rejects(token("container", "\"${"x".repeat(101)}\""))
    rejects(token("total_bytes", "2147483649"))
    assertEquals(DesktopMediaLimits.MAX_OBJECT_BYTES, MediaSourceSnapshotV1.decode(token("total_bytes", "2147483648")).totalBytes)
    assertEquals(DesktopMediaLimits.MAX_INTEGER,
        MediaSourceSnapshotV1.decode(token("audio_variant_row_version", "9007199254740991")).audioVariantRowVersion)
    assertEquals("\ud83c\udfb5", MediaSourceSnapshotV1.decode(token("codec", "\"\\uD83C\\uDFB5\"")).codec)
    assertTrue(raw.size < DesktopMediaLimits.SNAPSHOT_BYTES)
    val exactBound = raw + ByteArray(DesktopMediaLimits.SNAPSHOT_BYTES - raw.size) { 32 }
    assertArrayEquals(exactBound, MediaSourceSnapshotV1.decode(exactBound).originalResponseBytes())
    rejects(exactBound + byteArrayOf(32))

    val apiError = "{\"error\":{\"code\":\"media_source_unavailable\",\"message\":\"Unavailable\"," +
        "\"retryable\":true,\"request_id\":\"${parsed.userTrackRefId.value}\"}}"
    assertEquals(MediaSnapshotApiCode.MEDIA_SOURCE_UNAVAILABLE, MediaSnapshotApiError.decode(bytes(apiError)).code)
    assertTrue(MediaSnapshotApiError.decode(bytes(apiError)).retryable)
    for (malformed in listOf(apiError.replace("true", "1"), apiError.replace("\"Unavailable\"", "null"),
        apiError.replace("\"Unavailable\"", "\"${"x".repeat(129)}\""),
        apiError.replace("\"retryable\":true", "\"retryable\":true,\"retryable\":false"),
        apiError.replace("\"error\":{", "\"error\":{\"details\":\"forbidden\","),
        apiError.replace("media_source_unavailable", "unknown_code"))) {
        failure(MediaCode.PROTOCOL_ERROR) { MediaSnapshotApiError.decode(bytes(malformed)) }
    }

    val m5 = capture.network.m5Profile.m5CapabilityRevision
    val d1 = capture.network.d1b1Guards.globalCapabilityRevisionHighWater
    assertTrue(d1.value > 0 && d1.value != m5.value)
    assertEquals(m5, parsed.m5CapabilityRevision)
    DesktopMediaSourcePolicy.requireSnapshot(capture, playback, parsed)
    val d1Advanced = capture.copy(network = capture.network.copy(d1b1Guards = capture.network.d1b1Guards.copy(
        globalCapabilityRevisionHighWater = D1B1CapabilityRevision(DesktopMediaLimits.next(d1.value)))))
    // Pure M5 comparison does not take max(M5,D1). Actual D1 readback admission remains separate.
    DesktopMediaSourcePolicy.requireSnapshot(d1Advanced, playback, parsed)
    failure(MediaCode.CAPABILITY_UNAVAILABLE) {
        DesktopMediaSourcePolicy.requireSnapshot(capture, playback,
            MediaSourceSnapshotV1.decode(token("capability_revision", d1.value.toString())))
    }
    val reassigned = MediaSourceSnapshotV1.decode(fixture.validReassignedSnapshotBytes())
    assertEquals(parsed.userTrackRefId, reassigned.userTrackRefId)
    assertNotEquals(parsed.recordingId, reassigned.recordingId)
    assertNotEquals(parsed.audioVariantId, reassigned.audioVariantId)
    // R2/V2 is a fully valid wire200; no malformed digest/authority is used to cause this refusal.
    failure(MediaCode.RECONCILIATION_REQUIRED) { DesktopMediaSourcePolicy.requireSnapshot(capture, playback, reassigned) }
    failure(MediaCode.RECONCILIATION_REQUIRED) { DesktopMediaSourcePolicy.requireSnapshot(capture, download, reassigned) }
    assertFalse(parsed.sameAcquisitionTarget(reassigned))
    assertEquals(download.sourceEvidenceId, download.original.evidenceId)

    val rootOnly = fixture.rootOnlyCapture
    assertTrue(rootOnly.account.localOwner is LocalOwnerKey.Portable)
    DesktopMediaSourcePolicy.requireLocalGuard(rootOnly.account, downloaded = true)
    DesktopMediaSourcePolicy.requireIntent(rootOnly, SourceIntent.AccountCopy(fixture.originalCopyId))
    DesktopMediaSourcePolicy.requireIntent(SelectionCapture.AccountLocal(capture.fence, capture.network.local), playback)
    for (state in listOf(LocalUseState.LOCKED, LocalUseState.FORGOTTEN, LocalUseState.UNKNOWN)) {
        failure(MediaCode.OWNER_UNAVAILABLE) { DesktopMediaSourcePolicy.requireLocalGuard(rootOnly.account.copy(
            localGuard = rootOnly.account.localGuard.copy(state = state)), downloaded = true) }
    }
    for (veto in LocalMediaVeto.entries.filter { it != LocalMediaVeto.NONE }) {
        failure(MediaCode.AUTHORIZATION_DENIED) { DesktopMediaSourcePolicy.requireLocalGuard(rootOnly.account.copy(
            originLocalGuard = rootOnly.account.originLocalGuard!!.copy(veto = veto)), downloaded = true) }
    }
    failure(MediaCode.RECONCILIATION_REQUIRED) {
        DesktopMediaSourcePolicy.requireLocalGuard(rootOnly.account.copy(originLocalGuard = null), downloaded = true)
    }
    assertEquals(1L, DesktopMediaLimits.next(0))
    try { DesktopMediaLimits.next(DesktopMediaLimits.MAX_INTEGER); fail("Revision wrapped") }
    catch (failure: IllegalArgumentException) { assertEquals("CAPACITY_EXCEEDED", failure.message) }

    val transient = TransientMediaBearer(bytes("synthetic-access-sentinel"))
    assertEquals("TransientMediaBearer(<redacted>)", transient.toString())
    val escaped = transient.withBytes { it }
    assertTrue(escaped.all { it == 0.toByte() })
    transient.close()
    try { transient.withBytes { fail("Closed bearer exposed") }; fail("Closed bearer admitted") }
    catch (failure: IllegalStateException) { assertEquals("AUTH_REQUIRED", failure.message) }

    for (scenario in listOf(MediaFutureScenario.WRONG_OWNER, MediaFutureScenario.WRONG_SESSION,
        MediaFutureScenario.WRONG_GENERATION, MediaFutureScenario.WRONG_INTENT, MediaFutureScenario.ABA_AFTER_SNAPSHOT,
        MediaFutureScenario.STALE_AFTER_HEAD, MediaFutureScenario.STALE_AFTER_ACQUIRE, MediaFutureScenario.R1_TO_R2_VALID_200)) {
        val trace = fixture.runResolverScenario(scenario)
        assertFalse(trace.resolution is SourceResolution.Allow)
        assertEquals(0, trace.nativeSourceUses)
        assertEquals(0, trace.completedCopies)
        if (scenario == MediaFutureScenario.R1_TO_R2_VALID_200) {
            assertEquals(1, trace.calls.count { it == MediaFutureCall.SNAPSHOT })
            assertFalse(MediaFutureCall.HEAD in trace.calls)
            assertFalse(MediaFutureCall.ACQUIRE in trace.calls)
        }
    }
    for (scenario in listOf(MediaFutureScenario.ROOT_ONLY_OFFLINE_COPY, MediaFutureScenario.ORIGINAL_DETACHED_COPY)) {
        val trace = fixture.runResolverScenario(scenario)
        assertTrue((trace.resolution as? SourceResolution.Allow)?.handle is RetainedAccountHandle)
        assertEquals(fixture.originalCopyId, (trace.resolution as SourceResolution.Allow).handle.copyId)
        assertTrue(trace.originalProvenanceUnchanged)
        assertTrue(trace.calls.none { it in setOf(MediaFutureCall.SNAPSHOT, MediaFutureCall.HEAD,
            MediaFutureCall.ACQUIRE, MediaFutureCall.REFRESH) })
    }
    for (scenario in listOf(MediaFutureScenario.DETACHED_TRACK_SELECTION, MediaFutureScenario.LOCAL_LOCKED,
        MediaFutureScenario.LOCAL_FORGOTTEN, MediaFutureScenario.LOCAL_KNOWN_VETO,
        MediaFutureScenario.LOCAL_MISSING_ORIGIN_GUARD, MediaFutureScenario.LOCAL_ATTRIBUTE_ONLY)) {
        val trace = fixture.runResolverScenario(scenario)
        assertFalse(trace.resolution is SourceResolution.Allow)
        assertTrue(trace.bytesRetained)
        assertEquals(0, trace.nativeSourceUses)
    }
    for (scenario in listOf(MediaFutureScenario.CANCEL_HEAD_LATE_RESULT, MediaFutureScenario.CANCEL_ACTIVE_ACQUIRE,
        MediaFutureScenario.CANCEL_WAITING_PROMOTION, MediaFutureScenario.DRAIN_TIMEOUT)) {
        val trace = fixture.runResolverScenario(scenario)
        assertFalse(trace.resolution is SourceResolution.Allow)
        assertEquals(0, trace.replacementOwnersStarted)
        assertEquals(trace.actualStartedOwners, trace.confirmedSettledOwners + trace.failedRetainedOwners)
        assertTrue(trace.originalProvenanceUnchanged)
        if (scenario == MediaFutureScenario.DRAIN_TIMEOUT) assertTrue(trace.failedRetainedOwners > 0)
        else assertTrue(trace.cancellationPropagated)
    }
}

internal interface DesktopMediaSourceFutureFixture {
    val capture: SelectionCapture.VaultNetwork
    val playback: SourceIntent.VaultPlayback
    val download: SourceIntent.BoundVaultDownload
    val rootOnlyCapture: SelectionCapture.AccountLocal
    val originalCopyId: MediaId
    fun validSnapshotBytes(): ByteArray
    /** Same own actor/trust, actually valid R2/V2 bytes/HEAD/admission; J/intent deliberately still R1. */
    fun validReassignedSnapshotBytes(): ByteArray
    suspend fun runResolverScenario(scenario: MediaFutureScenario): MediaFutureTrace
}
internal enum class MediaFutureScenario {
    WRONG_OWNER, WRONG_SESSION, WRONG_GENERATION, WRONG_INTENT, ABA_AFTER_SNAPSHOT,
    STALE_AFTER_HEAD, STALE_AFTER_ACQUIRE, R1_TO_R2_VALID_200, ROOT_ONLY_OFFLINE_COPY,
    ORIGINAL_DETACHED_COPY, DETACHED_TRACK_SELECTION, LOCAL_LOCKED, LOCAL_FORGOTTEN,
    LOCAL_KNOWN_VETO, LOCAL_MISSING_ORIGIN_GUARD, LOCAL_ATTRIBUTE_ONLY,
    CANCEL_HEAD_LATE_RESULT, CANCEL_ACTIVE_ACQUIRE, CANCEL_WAITING_PROMOTION, DRAIN_TIMEOUT,
}
internal enum class MediaFutureCall { LOCAL_READ, SNAPSHOT, HEAD, ACQUIRE, REFRESH, NATIVE_USE, RETIRE, TERMINAL_RELEASE }
internal data class MediaFutureTrace(
    val resolution: SourceResolution?, val calls: List<MediaFutureCall>, val nativeSourceUses: Int,
    val completedCopies: Int, val bytesRetained: Boolean, val originalProvenanceUnchanged: Boolean,
    val actualStartedOwners: Int, val confirmedSettledOwners: Int, val failedRetainedOwners: Int,
    val replacementOwnersStarted: Int, val cancellationPropagated: Boolean,
)
