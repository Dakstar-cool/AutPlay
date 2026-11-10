package app.autplay.application.desktop.media

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

class MediaSourceSnapshotRequestV1(val userTrackRefId: MediaId) {
    val method: String get() = "GET"
    val path: String get() = "/api/v1/media-source/user-tracks/${userTrackRefId.value}/snapshot"
    val query: String? get() = null
    val bodyBytes: Int get() = 0
    val accept: String get() = "application/json"
    val followRedirects: Boolean get() = false
    override fun toString(): String = "MediaSourceSnapshotRequestV1(<redacted>)"
}

/** Original response bytes are private and defensively copied; no canonical rewrite or hash calculation. */
class MediaSourceSnapshotV1 private constructor(
    val serverInstanceId: MediaId, val identityEpoch: Long, val identityThumbprintSha256: String,
    val m5CapabilityRevision: M5CapabilityRevision, val userId: MediaId, val deviceId: MediaId,
    val userTrackRefId: MediaId, val userTrackRefRowVersion: Long, val recordingId: MediaId,
    val audioVariantId: MediaId, val audioVariantRowVersion: Long, val sha256: String,
    val totalBytes: Long, val mediaType: String, val codec: String, val container: String, raw: ByteArray,
) {
    private val document: ByteArray = raw.copyOf()
    fun originalResponseBytes(): ByteArray = document.copyOf()
    fun sameDocument(other: MediaSourceSnapshotV1): Boolean = document.contentEquals(other.document)
    fun sameContent(other: MediaSourceSnapshotV1): Boolean = sha256 == other.sha256 &&
        totalBytes == other.totalBytes && mediaType == other.mediaType && codec == other.codec && container == other.container
    /** Counters can advance only after J reconciles the fresh observation of this exact relationship. */
    fun sameAcquisitionTarget(other: MediaSourceSnapshotV1): Boolean = serverInstanceId == other.serverInstanceId &&
        identityEpoch == other.identityEpoch && identityThumbprintSha256 == other.identityThumbprintSha256 &&
        userId == other.userId && deviceId == other.deviceId && userTrackRefId == other.userTrackRefId &&
        recordingId == other.recordingId && audioVariantId == other.audioVariantId && sameContent(other)
    override fun toString(): String = "MediaSourceSnapshotV1(<redacted>)"
    companion object {
        private val members: Set<String> = setOf("namespace", "wire_version", "server_instance_id", "identity_epoch",
            "identity_thumbprint_sha256", "capability_revision", "user_id", "device_id", "user_track_ref_id",
            "user_track_ref_row_version", "recording_id", "audio_variant_id", "audio_variant_row_version", "sha256",
            "total_bytes", "media_type", "codec", "container")
        fun decode(raw: ByteArray): MediaSourceSnapshotV1 {
            val bytes = boundedCopy(raw)
            val root = MediaWireReader(bytes).read()
            wireRequire(root.keys == members)
            wireRequire(root.text("namespace") == "autplay.media-source" && root.number("wire_version") == 1L)
            val size = root.number("total_bytes")
            wireRequire(size in 1..DesktopMediaLimits.MAX_OBJECT_BYTES)
            return MediaSourceSnapshotV1(root.id("server_instance_id"), root.number("identity_epoch"),
                root.hash("identity_thumbprint_sha256"), M5CapabilityRevision(root.number("capability_revision")),
                root.id("user_id"), root.id("device_id"), root.id("user_track_ref_id"),
                root.number("user_track_ref_row_version"), root.id("recording_id"), root.id("audio_variant_id"),
                root.number("audio_variant_row_version"), root.hash("sha256"), size,
                root.text("media_type", 200), root.text("codec", 100), root.text("container", 100), bytes)
        }
    }
}

enum class MediaSnapshotApiCode(val wire: String) {
    REQUEST_VALIDATION_FAILED("request_validation_failed"), AUTHENTICATION_REQUIRED("authentication_required"),
    SESSION_REVOKED("session_revoked"), NOT_FOUND("not_found"), MEDIA_SOURCE_UNAVAILABLE("media_source_unavailable"),
}
/** Bounded existing nested ApiError; message is consumed/validated but never propagated or logged. */
class MediaSnapshotApiError private constructor(val code: MediaSnapshotApiCode, val retryable: Boolean) {
    override fun toString(): String = "MediaSnapshotApiError($code)"
    companion object {
        fun decode(raw: ByteArray): MediaSnapshotApiError {
            val root = MediaWireReader(boundedCopy(raw)).read()
            wireRequire(root.keys == setOf("error"))
            val error = (root["error"] as? WireValue.Object)?.members ?: wireReject()
            wireRequire(error.keys == setOf("code", "message", "retryable", "request_id"))
            val code = MediaSnapshotApiCode.entries.singleOrNull { it.wire == error.text("code") } ?: wireReject()
            error.text("message", 512, minimum = 0).also { wireRequire(it.codePointCount(0, it.length) <= 128) }
            error.id("request_id")
            val retryable = (error["retryable"] as? WireValue.Boolean)?.value ?: wireReject()
            return MediaSnapshotApiError(code, retryable)
        }
    }
}

enum class MediaObservationKind { ACQUISITION, CONTINUATION, ATTACHMENT }
/** Durable-safe non-secret observation. It cannot authorize requests, files or protected guard updates. */
class MediaSourceEvidence(
    val evidenceId: MediaId, val intentId: MediaId?, val kind: MediaObservationKind,
    val storeId: MediaId, val scopeId: MediaId, val owner: LocalOwnerKey,
    val bindingCommitId: MediaId, val localTrackRefId: MediaId, val projectionRevision: Long,
    val observedUtrServerRowVersion: Long, val observedRecordingId: MediaId,
    val snapshot: MediaSourceSnapshotV1,
) {
    init {
        DesktopMediaLimits.positive(projectionRevision); DesktopMediaLimits.positive(observedUtrServerRowVersion)
        require(observedUtrServerRowVersion == snapshot.userTrackRefRowVersion &&
            observedRecordingId == snapshot.recordingId) { "RECONCILIATION_REQUIRED" }
        val bound = owner as? LocalOwnerKey.BoundLocal
        require(bound == null || (bound.serverInstanceId == snapshot.serverInstanceId && bound.userId == snapshot.userId &&
            bound.ownerBindingCommitId == bindingCommitId)) { "RECONCILIATION_REQUIRED" }
    }
    override fun toString(): String = "MediaSourceEvidence(<redacted>)"
}
/** Dispatch time is process-only and never restored from J; snapshot alone remains historical evidence. */
class MediaSourceObservation(val capture: SelectionCapture.VaultNetwork, val snapshot: MediaSourceSnapshotV1,
    val dispatchedMonotonicMs: Long) {
    init { require(dispatchedMonotonicMs >= 0) { "PROTOCOL_ERROR" } }
    override fun toString(): String = "MediaSourceObservation(<redacted>)"
}

private fun boundedCopy(raw: ByteArray): ByteArray {
    wireRequire(raw.size in 1..DesktopMediaLimits.SNAPSHOT_BYTES)
    return raw.copyOf()
}
private fun wireRequire(value: Boolean) { if (!value) wireReject() }
private fun wireReject(): Nothing = throw MediaSourceException(
    MediaFailure(MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE, MediaRetryAction.NONE))
private sealed interface WireValue {
    class Text(val value: String) : WireValue
    class Integer(val value: Long) : WireValue
    class Boolean(val value: kotlin.Boolean) : WireValue
    class Object(val members: Map<String, WireValue>) : WireValue
}
private fun Map<String, WireValue>.text(key: String, max: Int = 4_096, minimum: Int = 1): String {
    val value = (this[key] as? WireValue.Text)?.value ?: wireReject()
    wireRequire(value.toByteArray(Charsets.UTF_8).size in minimum..max)
    return value
}
private fun Map<String, WireValue>.number(key: String): Long =
    (this[key] as? WireValue.Integer)?.value?.also { wireRequire(it in 1..DesktopMediaLimits.MAX_INTEGER) } ?: wireReject()
private fun Map<String, WireValue>.id(key: String): MediaId = try { MediaId(text(key)) }
    catch (_: IllegalArgumentException) { wireReject() }
private fun Map<String, WireValue>.hash(key: String): String = text(key).also {
    wireRequire(Regex("[0-9a-f]{64}").matches(it))
}

/** Small raw-token reader: no coercion, arrays/null, duplicate decoded keys or unbounded nesting. */
private class MediaWireReader(bytes: ByteArray) {
    private val text: String = try {
        Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
    } catch (_: Exception) { wireReject() }
    private var offset: Int = 0
    fun read(): Map<String, WireValue> {
        val result = obj(0)
        whitespace(); wireRequire(offset == text.length)
        return result
    }
    private fun obj(depth: Int): Map<String, WireValue> {
        wireRequire(depth <= 1); take('{'); whitespace()
        val result = linkedMapOf<String, WireValue>()
        if (peek() == '}') { offset++; return result }
        while (true) {
            val key = string(); wireRequire(!result.containsKey(key) && result.size < 18)
            take(':'); whitespace()
            val value = when (peek()) {
                '"' -> WireValue.Text(string())
                '{' -> WireValue.Object(obj(depth + 1))
                't' -> { literal("true"); WireValue.Boolean(true) }
                'f' -> { literal("false"); WireValue.Boolean(false) }
                in '0'..'9' -> WireValue.Integer(integer())
                else -> wireReject()
            }
            result[key] = value; whitespace()
            if (peek() == '}') { offset++; return result }
            take(',')
        }
    }
    private fun integer(): Long {
        val start = offset
        while (peek() in '0'..'9') offset++
        val token = text.substring(start, offset)
        wireRequire(token.length in 1..16 && (token.length == 1 || token[0] != '0'))
        val value = token.toLongOrNull() ?: wireReject()
        wireRequire(value <= DesktopMediaLimits.MAX_INTEGER)
        // A decimal/exponent/sign suffix is rejected by the following object delimiter check.
        return value
    }
    private fun string(): String {
        take('"')
        val value = StringBuilder()
        while (true) {
            wireRequire(offset < text.length)
            val char = text[offset++]
            if (char == '"') break
            wireRequire(char.code >= 32)
            if (char != '\\') value.append(char)
            else {
                wireRequire(offset < text.length)
                value.append(when (val escape = text[offset++]) {
                    '"', '\\', '/' -> escape
                    'b' -> '\b'; 'f' -> '\u000c'; 'n' -> '\n'; 'r' -> '\r'; 't' -> '\t'
                    'u' -> {
                        wireRequire(offset + 4 <= text.length)
                        val hex = text.substring(offset, offset + 4)
                        wireRequire(hex.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' })
                        offset += 4; hex.toInt(16).toChar()
                    }
                    else -> wireReject()
                })
            }
        }
        var index = 0
        while (index < value.length) {
            val char = value[index++]
            if (char.isHighSurrogate()) wireRequire(index < value.length && value[index++].isLowSurrogate())
            else wireRequire(!char.isLowSurrogate())
        }
        return value.toString()
    }
    private fun literal(value: String) {
        wireRequire(text.startsWith(value, offset)); offset += value.length
    }
    private fun peek(): Char = text.getOrNull(offset) ?: '\u0000'
    private fun whitespace() { while (peek() in " \t\r\n") offset++ }
    private fun take(char: Char) { whitespace(); wireRequire(peek() == char); offset++ }
}
