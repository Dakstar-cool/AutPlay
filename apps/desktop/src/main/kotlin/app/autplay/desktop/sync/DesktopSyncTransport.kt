package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.time.Instant
import kotlinx.serialization.json.*
import org.erdtman.jcs.JsonCanonicalizer

public enum class DesktopSyncFailure {
    UNAVAILABLE, AUTH_REQUIRED, TRUST_REQUIRED, CAPACITY, RETRY_LATER, CURSOR_INVALID,
    DEVICE_RESET_REQUIRED, BOOTSTRAP_SNAPSHOT_INVALID, CONFLICT, UNSUPPORTED, CONTEXT_CHANGED,
    INVALID_RESPONSE, DELIVERY_BLOCKED, OUTCOME_PENDING,
}
public class DesktopSyncException(public val failure: DesktopSyncFailure, public val retryAfterMs: Long = 0) :
    IllegalStateException("DESKTOP_SYNC_$failure")

/** Typed v1 data transport. Event bytes and their hash/UUID/sequence are never reconstructed. */
public class DesktopSyncTransport(internal val session: DesktopSyncSession) {
    internal var passDeadlineNanos: Long? = null
    internal fun deadlineMs(): Long {
        val remaining = passDeadlineNanos?.let { (it - System.nanoTime()) / 1_000_000 } ?: 20_000
        if (remaining < 100) throw DesktopSyncException(DesktopSyncFailure.RETRY_LATER, 1000)
        return minOf(20_000, remaining)
    }
    internal suspend fun push(claim: AccountDeliveryClaim, sync: AccountSyncState,
        ticket: (DomainIoTicket) -> Unit): DomainResponse {
        require(claim.events.size in 1..100 && claim.events.all { it.deviceId == sync.deviceId &&
            it.profileId == claim.lease.originalProfileId } && claim.events.zipWithNext().all { (a, b) -> b.sequence == a.sequence + 1 })
        val prefix = binding(claim.lease.originalProfileId, sync)
        val eventBytes = claim.events.map { it.canonicalBytes }
        val body = try {
            val fields = prefix.entries.joinToString(",") { (key, value) -> JsonPrimitive(key).toString() + ":" + JsonPrimitive(value) }
            ("{\"protocol_version\":1," + fields + ",\"events\":[" +
                eventBytes.joinToString(",") { it.toString(Charsets.UTF_8) } + "]}").toByteArray(Charsets.UTF_8)
        } finally { eventBytes.forEach { it.fill(0) } }
        try {
            require(body.size <= DesktopAccountLimits.PAGE_BYTES)
            return session.request(claim.lease.account, DomainRoute.Push(), body, ticket, deadlineMs())
        } finally { body.fill(0) }
    }
    internal suspend fun page(capture: AccountPageCapture, pullLimit: Int,
        ticket: (DomainIoTicket) -> Unit): DomainResponse {
        val state = capture.original
        return if (capture.kind == AccountPageKind.PULL) {
            val query = binding(capture.account.profileId, state) + mapOf("protocol_version" to "1", "limit" to pullLimit.toString(),
                "catalog_projection_version" to "1", "capabilities" to "CATALOG_ARTIST_ID_V1") +
                (state.cursor?.let { mapOf("cursor" to it) } ?: emptyMap())
            session.request(capture.account, DomainRoute.Pull(query), onTicket = ticket, deadlineMs = deadlineMs())
        } else {
            // Frozen bootstrap has no page-size field. Do not invent one to bypass capacity deferral.
            require(state.pendingEventCount in 0..100_000)
            val body = DomainJson.canonical(buildJsonObject {
                put("protocol_version", 1)
                binding(capture.account.profileId, state).forEach { (name, value) -> put(name, value) }
                put("reason", if (state.bootstrapState == "RESET_REQUIRED") "CURSOR_INVALID" else "FIRST_SYNC")
                put("snapshot_id", capture.requestSnapshotId?.value?.let(::JsonPrimitive) ?: JsonNull)
                put("page_token", capture.requestPageToken?.let(::JsonPrimitive) ?: JsonNull)
                put("pending_local_event_count", state.pendingEventCount)
                put("catalog_projection_version", 1); put("capabilities", JsonArray(listOf(JsonPrimitive("CATALOG_ARTIST_ID_V1"))))
            })
            try { session.request(capture.account, DomainRoute.Bootstrap(), body, ticket, deadlineMs()) } finally { body.fill(0) }
        }
    }
    internal suspend fun status(lease: AccountLease, sync: AccountSyncState): DomainResponse =
        session.request(lease, DomainRoute.Status(binding(lease.profileId, sync) + ("protocol_version" to "1")), deadlineMs = deadlineMs())

    internal fun requireSuccess(reply: DomainResponse, expected: Set<Int> = setOf(200)) {
        if (reply.status in expected) return
        var retryAfter = reply.retryAfterMs
        val safeCode = runCatching {
            val root = DomainJson.document(reply.bytes, 65_536)
            val error = root["error"] as? JsonObject ?: root
            val hint = (error["retry_after_ms"] as? JsonPrimitive)?.takeUnless { it.isString }?.longOrNull
            if (hint != null && hint in 0..86_400_000) retryAfter = maxOf(retryAfter, hint)
            (error["code"] as? JsonPrimitive)?.takeIf { it.isString }?.content
        }.getOrNull()
        val failure = when {
            safeCode in setOf("DEVICE_RESET_REQUIRED", "JOURNAL_RESET_REQUIRED", "device_reset_required", "journal_reset_required") -> DesktopSyncFailure.DEVICE_RESET_REQUIRED
            safeCode in setOf("CURSOR_INVALID", "cursor_invalid") -> DesktopSyncFailure.CURSOR_INVALID
            safeCode == "bootstrap_snapshot_invalid" -> DesktopSyncFailure.BOOTSTRAP_SNAPSHOT_INVALID
            reply.status in setOf(401, 403) -> DesktopSyncFailure.AUTH_REQUIRED
            reply.status in setOf(404, 405, 501) -> DesktopSyncFailure.UNSUPPORTED
            reply.status == 409 -> DesktopSyncFailure.CONFLICT
            reply.status == 413 -> DesktopSyncFailure.CAPACITY
            reply.status == 429 || reply.status >= 500 -> DesktopSyncFailure.RETRY_LATER
            else -> DesktopSyncFailure.INVALID_RESPONSE
        }
        throw DesktopSyncException(failure, retryAfter)
    }
    private fun binding(profile: AccountUuid, state: AccountSyncState): Map<String, String> = mapOf(
        "device_id" to state.deviceId.value, "server_profile_id" to profile.value, "journal_epoch" to state.journalEpoch.value)
}

/** Data-plane JSON accepts the existing finite decimal fields. K's integer-only grammar stays closed. */
internal object DomainJson {
    fun canonical(value: JsonElement): ByteArray = JsonCanonicalizer(value.toString()).encodedUTF8
    fun document(bytes: ByteArray, maximum: Int): JsonObject {
        require(bytes.size in 2..maximum)
        val raw = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        var depth = 0; var quoted = false; var escaped = false; var nodes = 0
        for (character in raw) {
            if (quoted) {
                if (escaped) escaped = false else if (character == '\\') escaped = true else if (character == '"') quoted = false
            } else when (character) {
                '"' -> { quoted = true; nodes++ }
                '{', '[' -> { depth++; nodes++; require(depth <= 20) }
                '}', ']' -> { depth--; require(depth >= 0) }
                ',' -> nodes++
            }
            require(nodes <= 100_000)
        }
        require(!quoted && depth == 0)
        // Strict JCS parsing rejects decoded duplicate keys, malformed Unicode and non-finite values.
        JsonCanonicalizer(raw)
        return Json.parseToJsonElement(raw) as? JsonObject ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
    }
    fun text(root: JsonObject, name: String, maximum: Int = 2000): String =
        (root[name] as? JsonPrimitive)?.takeIf { it.isString }?.content?.also {
            require(it.length in 1..maximum && it.none { character -> character.code < 32 || character.code == 127 })
        } ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
    fun number(root: JsonObject, name: String): Long = (root[name] as? JsonPrimitive)?.takeUnless { it.isString }
        ?.longOrNull?.also { require(it in 0..DesktopAccountLimits.MAX_REVISION) }
        ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
    fun timestamp(root: JsonObject, name: String): Instant = Instant.parse(text(root, name, 100))
}
