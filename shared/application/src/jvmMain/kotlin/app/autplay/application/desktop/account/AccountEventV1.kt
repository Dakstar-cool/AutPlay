package app.autplay.application.desktop.account

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.security.MessageDigest
import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull
import org.erdtman.jcs.JsonCanonicalizer

/**
 * Exact existing contracts/events/v1/client-event.schema.json, P04/P07 JCS/hash recipe.
 * This is integrity evidence, never authorization. No vectors are calculated during source authoring.
 * Original canonical bytes, including safe additive members and unknown discriminators, are retained.
 * The typed create functions author supported v1 families; decoding never applies a mutation.
 */
class AccountEventV1 private constructor(
    val eventId: AccountUuid,
    val userId: AccountUuid,
    val deviceId: AccountUuid,
    val profileId: AccountUuid,
    val sequence: Long,
    val idempotencyKey: String,
    val eventType: String,
    val aggregateType: String,
    val aggregateLocalId: AccountUuid,
    val aggregateServerId: AccountUuid?,
    val baseServerRowVersion: Long?,
    val occurredAt: String,
    bytes: ByteArray,
    hash: ByteArray,
) {
    private val original: ByteArray = bytes.copyOf()
    private val digest: ByteArray = hash.copyOf()
    val canonicalBytes: ByteArray get() = original.copyOf()
    val requestHash: ByteArray get() = digest.copyOf()
    val byteSize: Int get() = original.size
    override fun toString(): String = "AccountEventV1"

    companion object {
        private val json: Json = Json { isLenient = false; allowSpecialFloatingPointValues = false }
        private val name: Regex = Regex("[a-z][a-z0-9_]{0,99}")
        private val forbidden: Regex = Regex(
            "(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|" +
                "base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|" +
                "raw_model_features|model_features|feature_vector|debug_text|personal_debug|raw_audio|audio_bytes)(_|$)",
        )

        /** Validates only immutable wire integrity/shape. Caller supplies trusted ownership separately. */
        fun fromCanonical(bytes: ByteArray): AccountEventV1 = try {
            require(bytes.size in 1..DesktopAccountLimits.PAGE_BYTES.toInt())
            val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
            // Canonical byte equality rejects duplicate keys, alternative escapes/ordering and whitespace.
            // Depth is bounded BEFORE recursive parser/canonicalizer entry, including adversarial input.
            requireBoundedNesting(text)
            val document = json.parseToJsonElement(text) as? JsonObject ?: invalid()
            safe(document, 0)
            require(JsonCanonicalizer(text).encodedUTF8.contentEquals(bytes))
            fun string(key: String, maximum: Int): String {
                val value = document[key] as? JsonPrimitive ?: invalid()
                require(value.isString && value.content.length in 1..maximum)
                return value.content
            }
            fun id(key: String): AccountUuid = AccountUuid(string(key, 36))
            fun number(key: String): Long {
                val value = document[key] as? JsonPrimitive ?: invalid()
                require(!value.isString)
                return (value.longOrNull ?: invalid()).also { require(it in 1..DesktopAccountLimits.MAX_REVISION) }
            }
            fun nullableId(key: String): AccountUuid? = when (document[key]) {
                JsonNull -> null
                null -> invalid()
                else -> id(key)
            }
            fun nullableNumber(key: String): Long? = when (document[key]) {
                JsonNull -> null
                null -> invalid()
                else -> number(key)
            }
            require(number("schema_version") == 1L)
            val payload = document["payload"] as? JsonObject ?: invalid()
            require(JsonCanonicalizer(payload.toString()).encodedUTF8.size <= DesktopAccountLimits.PAYLOAD_BYTES)
            val hashText = string("request_hash", 64)
            require(hashText.matches(Regex("[0-9a-f]{64}")))
            val hash = ByteArray(32) { hashText.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
            val withoutHash = JsonObject(document - "request_hash")
            val calculated = sha(JsonCanonicalizer(withoutHash.toString()).encodedUTF8)
            require(MessageDigest.isEqual(hash, calculated))
            val occurredAt = string("occurred_at", 100)
            Instant.parse(occurredAt)
            AccountEventV1(
                id("event_id"), id("user_id"), id("device_id"), id("server_profile_id"),
                number("device_sequence"), string("idempotency_key", 200), string("event_type", 200),
                string("aggregate_type", 100), id("aggregate_local_id"), nullableId("aggregate_server_id"),
                nullableNumber("base_server_row_version"), occurredAt, bytes, hash,
            )
        } catch (_: Exception) { invalid() }

        fun trackPayload(change: AccountCreateTrack): ByteArray = canonical(JsonObject(mapOf(
            "artist" to JsonPrimitive(change.artist),
            "library_entry_local_id" to JsonPrimitive(change.libraryEntryId.value),
            "title" to JsonPrimitive(change.title),
        ))).also { require(it.size <= DesktopAccountLimits.PAYLOAD_BYTES) }

        fun createTrack(
            change: AccountCreateTrack,
            remote: AccountRemoteOwner,
            binding: AccountRemoteBinding,
            profileId: AccountUuid,
            sequence: Long,
        ): AccountEventV1 {
            // Writers must fit the actual sync.py datetime.fromisoformat receiver. Decoding retained
            // immutable bytes deliberately keeps its existing wider Instant range.
            require(change.occurredAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS)
            require(sequence in 1..DesktopAccountLimits.MAX_REVISION)
            val payload = json.parseToJsonElement(trackPayload(change).toString(Charsets.UTF_8))
            val fields = mapOf(
                "event_id" to JsonPrimitive(change.operationId.value),
                "idempotency_key" to JsonPrimitive(change.operationId.value),
                "user_id" to JsonPrimitive(remote.userId.value),
                "device_id" to JsonPrimitive(binding.deviceId.value),
                "server_profile_id" to JsonPrimitive(profileId.value),
                "device_sequence" to JsonPrimitive(sequence),
                "event_type" to JsonPrimitive("USER_TRACK_REF_CREATED"),
                "schema_version" to JsonPrimitive(1),
                "aggregate_type" to JsonPrimitive("USER_TRACK_REF"),
                "aggregate_local_id" to JsonPrimitive(change.trackRefId.value),
                "aggregate_server_id" to JsonNull,
                "base_server_row_version" to JsonNull,
                "occurred_at" to JsonPrimitive(Instant.ofEpochMilli(change.occurredAtMs).toString()),
                "payload" to payload,
            )
            val digest = sha(canonical(JsonObject(fields)))
            val hex = digest.joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }
            return fromCanonical(canonical(JsonObject(fields + ("request_hash" to JsonPrimitive(hex)))))
        }

        fun createDomain(change: AccountDomainChange, remote: AccountRemoteOwner, binding: AccountRemoteBinding,
            profileId: AccountUuid, sequence: Long, serverId: AccountUuid?, baseVersion: Long?,references:AccountDomainReferences=AccountDomainReferences()): AccountEventV1 {
            require(change.occurredAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS && sequence in 1..DesktopAccountLimits.MAX_REVISION)
            val (eventType, aggregateType) = AccountDomainWire.type(change.command)
            if (change.command is AccountDomainCommand.Listen || change.command is AccountDomainCommand.Interaction)
                require(change.operationId == change.command.localId && baseVersion == null)
            val payload = AccountDomainWire.payload(change.command,references)
            require(canonical(payload).size <= DesktopAccountLimits.PAYLOAD_BYTES)
            val fields = JsonObject(mapOf(
                "event_id" to JsonPrimitive(change.operationId.value), "idempotency_key" to JsonPrimitive(change.operationId.value),
                "user_id" to JsonPrimitive(remote.userId.value), "device_id" to JsonPrimitive(binding.deviceId.value),
                "server_profile_id" to JsonPrimitive(profileId.value), "device_sequence" to JsonPrimitive(sequence),
                "event_type" to JsonPrimitive(eventType), "schema_version" to JsonPrimitive(1), "aggregate_type" to JsonPrimitive(aggregateType),
                "aggregate_local_id" to JsonPrimitive(change.command.localId.value),
                "aggregate_server_id" to (serverId?.let { JsonPrimitive(it.value) } ?: JsonNull),
                "base_server_row_version" to (baseVersion?.let(::JsonPrimitive) ?: JsonNull),
                "occurred_at" to JsonPrimitive(Instant.ofEpochMilli(change.occurredAtMs).toString()), "payload" to payload))
            val hash = sha(canonical(fields)).joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }
            return fromCanonical(canonical(JsonObject(fields + ("request_hash" to JsonPrimitive(hash)))))
        }

        private fun canonical(value: JsonObject): ByteArray = AccountPortableV1.canonical(value)
        private fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
        private fun invalid(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)

        private fun requireBoundedNesting(text: String): Unit {
            var depth = 0
            var quoted = false
            var escaped = false
            for (character in text) {
                if (quoted) {
                    if (escaped) escaped = false
                    else if (character == '\\') escaped = true
                    else if (character == '"') quoted = false
                } else when (character) {
                    '"' -> quoted = true
                    '{', '[' -> { depth++; require(depth <= 34) }
                    '}', ']' -> { depth--; require(depth >= 0) }
                }
            }
            require(depth == 0 && !quoted)
        }

        private fun safe(value: JsonElement, depth: Int): Unit {
            require(depth <= 33) // Envelope plus P07's payload depth 32.
            when (value) {
                is JsonObject -> value.forEach { (key, child) ->
                    require(name.matches(key) && !forbidden.containsMatchIn(key))
                    safe(child, depth + 1)
                }
                is JsonArray -> value.forEach { safe(it, depth + 1) }
                is JsonPrimitive -> if (value.isString) {
                    var index = 0
                    while (index < value.content.length) {
                        val character = value.content[index++]
                        if (character.isHighSurrogate()) {
                            require(index < value.content.length && value.content[index++].isLowSurrogate())
                        } else require(!character.isLowSurrogate())
                    }
                }
            }
        }
    }
}
