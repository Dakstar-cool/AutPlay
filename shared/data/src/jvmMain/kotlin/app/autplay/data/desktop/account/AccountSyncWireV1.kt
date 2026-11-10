package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.time.Instant
import kotlinx.serialization.json.*
import org.erdtman.jcs.JsonCanonicalizer

/** Exact v1 response members. Original safe envelope bytes live in the page result, not a new wire. */
internal data class AccountWireAck(
    val event: AccountUuid, val sequence: Long, val type: String, val local: AccountUuid,
    val server: AccountUuid?, val outcome: String, val effective: String, val version: Long?,
    val retryable: Boolean, val retryAfter: Long, val error: String?, val conflict: JsonObject?,
    val redirect: AccountWireRedirect?, val original: JsonObject,
)
internal data class AccountWireAckPage(val through: Long, val acks: List<AccountWireAck>)
internal data class AccountWireRedirect(val type: String, val alias: AccountUuid, val canonical: AccountUuid)
internal data class AccountWireTombstone(
    val id: AccountUuid, val type: String, val server: AccountUuid, val deletedBy: AccountUuid,
    val deletedAt: String, val retainUntil: String,
)
internal data class AccountWireChange(
    val event: AccountUuid?, val sequence: Long?, val eventType: String?, val schema: Long,
    val type: String, val server: AccountUuid, val version: Long?, val operation: String,
    val payload: JsonObject, val original: JsonObject, val createdAt: String?,
    val tombstone: AccountWireTombstone?, val redirect: AccountWireRedirect?,
)
internal data class AccountWirePage(
    val changes: List<AccountWireChange>, val next: String?, val more: Boolean,
    val snapshot: AccountUuid?, val highWater: Long?, val finalCursor: String?,
)

internal object AccountSyncWireV1 {
    private val json = Json { isLenient = false; allowSpecialFloatingPointValues = false }
    private val name = Regex("[a-z][a-z0-9_]{0,99}")
    private val forbidden = Regex("(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|raw_model_features|model_features|feature_vector|debug_text|personal_debug|raw_audio|audio_bytes)(_|$)")
    private val envelopeTokens = setOf("next_page_token")

    fun boundedCopy(bytes: ByteArray): ByteArray {
        if (bytes.size !in 1..DesktopAccountLimits.PAGE_BYTES.toInt()) invalid()
        return bytes.copyOf()
    }

    fun document(bytes: ByteArray): JsonObject = try {
        require(bytes.size in 1..DesktopAccountLimits.PAGE_BYTES.toInt())
        val source = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        Scan(source).document()
        val root = json.parseToJsonElement(source) as? JsonObject ?: invalid()
        safe(root, 0)
        root
    } catch (_: Exception) { invalid() }

    fun binding(root: JsonObject, device: AccountUuid, profile: AccountUuid, epoch: AccountUuid): Unit {
        if (root.number("protocol_version", 1, 1) != 1L || root.id("device_id") != device ||
            root.id("server_profile_id") != profile || root.id("journal_epoch") != epoch) {
            fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        }
        root.time("server_time")
    }

    fun acks(root: JsonObject, device: AccountUuid, profile: AccountUuid, epoch: AccountUuid): AccountWireAckPage {
        binding(root, device, profile, epoch)
        val values = root.array("acks", 100).map { item ->
            val value = item as? JsonObject ?: invalid()
            val outcome = value.string("outcome", 200)
            val effective = if (outcome == "DUPLICATE") value.string("original_outcome", 200) else outcome
            if (outcome != "DUPLICATE" && "original_outcome" in value) invalid()
            if (effective == "APPLIED" && ("conflict" in value || "error" in value)) invalid()
            if (effective == "CONFLICT" && "error" in value) invalid()
            if (effective == "REJECTED" && "conflict" in value) invalid()
            val server = value.nullId("aggregate_server_id")
            val version = value.optionalNumber("server_row_version", 1)
            var retryable = false
            var retryAfter = 0L
            var error: String? = null
            var conflict: JsonObject? = null
            when (effective) {
                "APPLIED" -> if (server == null || version == null) invalid()
                "CONFLICT" -> {
                    if (server == null) invalid()
                    conflict = value.obj("conflict")
                    conflict.string("conflict_kind", 100)
                    conflict.string("resolution_state", 100)
                    conflict.optionalId("server_event_id")
                    if ("server_snapshot" in conflict) conflict.obj("server_snapshot")
                }
                "REJECTED" -> {
                    val problem = value.obj("error")
                    error = problem.string("code", 100)
                    problem.string("message", 500)
                    problem.id("request_id")
                    retryable = problem.bool("retryable")
                    retryAfter = problem.optionalNumber("retry_after_ms", 0, 86_400_000) ?: 0
                }
                else -> Unit // Retained and retried; unknown classifications never become terminal.
            }
            val type = value.string("aggregate_type", 100)
            AccountWireAck(value.id("event_id"), value.number("device_sequence", 1), type,
                value.id("aggregate_local_id"), server, outcome, effective, version, retryable, retryAfter,
                error, conflict, if ("redirect" in value) redirect(value.obj("redirect"), type) else null, value)
        }
        return AccountWireAckPage(root.number("acknowledged_through_device_sequence", 0), values)
    }

    fun pull(root: JsonObject, capture: AccountPageCapture): AccountWirePage {
        binding(root, capture.original.deviceId, capture.account.profileId, capture.original.journalEpoch)
        if (root.nullCursor("from_cursor") != capture.original.cursor) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        val events = root.array("events", 100).map { item ->
            val value = item as? JsonObject ?: invalid()
            val type = value.string("aggregate_type", 100)
            val server = value.id("aggregate_server_id")
            value.nullId("origin_device_id")
            AccountWireChange(value.id("event_id"), value.number("server_sequence", 1),
                value.string("event_type", 200), value.number("schema_version", 1), type, server,
                value.nullNumber("server_row_version", 1), value.string("operation", 100),
                payload(value), value, value.time("created_at"),
                if ("tombstone" in value) tombstone(value.obj("tombstone"), type, server) else null,
                if ("redirect" in value) redirect(value.obj("redirect"), type) else null)
        }
        var prior = 0L
        val ids = HashSet<AccountUuid>()
        events.forEach { change ->
            val sequence = checkNotNull(change.sequence)
            if (sequence <= prior || !ids.add(checkNotNull(change.event))) invalid()
            if (change.tombstone != null && change.tombstone.deletedBy != change.event) invalid()
            prior = sequence
        }
        return AccountWirePage(events, root.cursor("next_cursor"), root.bool("has_more"), null, null, null)
    }

    fun bootstrap(root: JsonObject, capture: AccountPageCapture): AccountWirePage {
        binding(root, capture.original.deviceId, capture.account.profileId, capture.original.journalEpoch)
        if (root.string("pending_event_directive", 100) != "PRESERVE_REBASE_RETRY") invalid()
        val snapshot = root.id("snapshot_id")
        val high = root.number("snapshot_high_water_server_sequence", 0)
        val continuing = capture.original.bootstrapState == "BOOTSTRAPPING"
        if (continuing && (capture.original.snapshotId != snapshot || capture.original.snapshotHighWater != high ||
                capture.original.finalCursor != root.nullCursor("snapshot_cursor"))) invalid()
        val more = root.bool("has_more")
        val next = root.nullCursor("next_page_token")
        val final = root.nullCursor("snapshot_cursor")
        if (more != (next != null) || (!more && final == null) ||
            (more && continuing && next == capture.original.pageToken)) invalid()
        val changes = root.array("aggregates", 500).map { item ->
            val value = item as? JsonObject ?: invalid()
            AccountWireChange(null, null, null, 1, value.string("aggregate_type", 100),
                value.id("aggregate_server_id"), value.number("server_row_version", 1), "UPSERT",
                payload(value), value, null, null, null)
        }.toMutableList()
        root.array("tombstones", 500).forEach { item ->
            val value = item as? JsonObject ?: invalid()
            val type = value.string("aggregate_type", 100)
            val server = value.id("aggregate_server_id")
            changes.add(AccountWireChange(null, null, null, 1, type, server, null, "DELETE",
                JsonObject(emptyMap()), value, null, tombstone(value, type, server), null))
        }
        root.array("redirects", 500).forEach { item ->
            val value = item as? JsonObject ?: invalid()
            val type = value.string("aggregate_type", 100)
            val redirect = redirect(value, type)
            changes.add(AccountWireChange(null, null, null, 1, type, redirect.alias, null, "REDIRECT",
                JsonObject(emptyMap()), value, null, null, redirect))
        }
        if (changes.size > 500 || changes.map {
                listOf(it.operation, it.type, it.server.value,
                    if (it.type in setOf("RECORDING_ARTIST_CREDIT", "RELEASE_ARTIST_CREDIT"))
                        it.payload.number("owner_recording_page", 0).toString() else "")
            }.toSet().size != changes.size) invalid()
        return AccountWirePage(changes, next, more, snapshot, high, final)
    }

    private fun redirect(value: JsonObject, type: String): AccountWireRedirect =
        AccountWireRedirect(type, value.id("alias_server_id"), value.id("canonical_server_id")).also {
            if (it.alias == it.canonical) invalid()
        }
    private fun tombstone(value: JsonObject, type: String, server: AccountUuid): AccountWireTombstone =
        AccountWireTombstone(value.id("tombstone_id"), type, server, value.id("deleted_by_event_id"),
            value.time("deleted_at"), value.time("retain_until")).also {
            if (Instant.parse(it.retainUntil).isBefore(Instant.parse(it.deletedAt))) invalid()
        }

    private fun payload(value: JsonObject): JsonObject = value.obj("payload").also {
        safe(it, 0, 32)
        if (JsonCanonicalizer(it.toString()).encodedUTF8.size > DesktopAccountLimits.PAYLOAD_BYTES) invalid()
    }

    private fun safe(value: JsonElement, depth: Int, maximum: Int = 36): Unit {
        if (depth > maximum) invalid()
        when (value) {
            is JsonObject -> value.forEach { (key, child) ->
                if (!name.matches(key) || (forbidden.containsMatchIn(key) && !(depth == 0 && key in envelopeTokens))) invalid()
                safe(child, depth + 1, maximum)
            }
            is JsonArray -> value.forEach { safe(it, depth + 1, maximum) }
            is JsonPrimitive -> if (value.isString) unicode(value.content)
        }
    }
    private fun unicode(value: String): Unit {
        var at = 0
        while (at < value.length) {
            val char = value[at++]
            if (char.isHighSurrogate()) {
                if (at == value.length || !value[at++].isLowSurrogate()) invalid()
            } else if (char.isLowSurrogate()) invalid()
        }
    }

    /** Pre-parse depth/duplicate-key scan. Parser then enforces JSON grammar; no canonical rewrite. */
    private class Scan(private val text: String) {
        private var at = 0
        fun document(): Unit { value(0); space(); if (at != text.length) invalid() }
        private fun space(): Unit { while (at < text.length && text[at] in " \r\n\t") at++ }
        private fun take(char: Char): Boolean { space(); return if (at < text.length && text[at] == char) { at++; true } else false }
        private fun string(): String {
            space()
            val start = at
            if (!take('"')) invalid()
            var escaped = false
            while (at < text.length) {
                val char = text[at++]
                if (escaped) escaped = false
                else if (char == '\\') escaped = true
                else if (char == '"') return (json.parseToJsonElement(text.substring(start, at)) as JsonPrimitive).content.also(::unicode)
            }
            invalid()
        }
        private fun value(depth: Int): Unit {
            if (depth > 36) invalid()
            space()
            if (at >= text.length) invalid()
            when (text[at]) {
                '{' -> {
                    at++
                    val keys = HashSet<String>()
                    if (take('}')) return
                    do {
                        if (!keys.add(string()) || !take(':')) invalid()
                        value(depth + 1)
                        if (take('}')) return
                    } while (take(','))
                    invalid()
                }
                '[' -> {
                    at++
                    if (take(']')) return
                    do { value(depth + 1); if (take(']')) return } while (take(','))
                    invalid()
                }
                '"' -> string()
                else -> {
                    val start = at
                    while (at < text.length && text[at] !in ",]} \r\n\t") at++
                    if (start == at) invalid()
                }
            }
        }
    }
    private fun invalid(): Nothing = fail(AccountFailure.ACCOUNT_INPUT_INVALID)
}

internal fun JsonObject.string(key: String, max: Int, min: Int = 1): String {
    val value = this[key] as? JsonPrimitive ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
    if (!value.isString || value.content.length !in min..max) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
    return value.content
}
internal fun JsonObject.id(key: String): AccountUuid = try { AccountUuid(string(key, 36)) }
    catch (_: IllegalArgumentException) { fail(AccountFailure.ACCOUNT_INPUT_INVALID) }
internal fun JsonObject.nullId(key: String): AccountUuid? = if (this[key] == JsonNull) null else id(key)
internal fun JsonObject.optionalId(key: String): AccountUuid? = if (key !in this) null else nullId(key)
internal fun JsonObject.number(key: String, min: Long, max: Long = DesktopAccountLimits.MAX_REVISION): Long {
    val value = this[key] as? JsonPrimitive ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
    return value.longOrNull?.takeIf { !value.isString && it in min..max } ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
}
internal fun JsonObject.nullNumber(key: String, min: Long, max: Long = DesktopAccountLimits.MAX_REVISION): Long? =
    if (this[key] == JsonNull) null else number(key, min, max)
internal fun JsonObject.optionalNumber(key: String, min: Long, max: Long = DesktopAccountLimits.MAX_REVISION): Long? =
    if (key !in this || this[key] == JsonNull) null else number(key, min, max)
internal fun JsonObject.bool(key: String): Boolean {
    val value = this[key] as? JsonPrimitive ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
    return value.booleanOrNull?.takeIf { !value.isString } ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
}
internal fun JsonObject.obj(key: String): JsonObject = this[key] as? JsonObject ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
internal fun JsonObject.array(key: String, max: Int): JsonArray = (this[key] as? JsonArray)?.takeIf { it.size <= max }
    ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
internal fun JsonObject.time(key: String): String = try { string(key, 100).also { Instant.parse(it) } }
    catch (_: Exception) { fail(AccountFailure.ACCOUNT_INPUT_INVALID) }
internal fun JsonObject.cursor(key: String): String = string(key, 2048, 16).also {
    if (it.toByteArray(Charsets.UTF_8).size > DesktopAccountLimits.CURSOR_BYTES) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
}
internal fun JsonObject.nullCursor(key: String): String? = if (this[key] == JsonNull) null else cursor(key)
