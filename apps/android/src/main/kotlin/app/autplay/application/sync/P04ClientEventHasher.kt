package app.autplay.application.sync

import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

data class ClientEventBinding(
    val userId: UserId,
    val deviceId: DeviceId,
    val serverProfileId: ServerProfileId,
    /** Expected durable lineage epoch; it is request binding, not a hashed event member. */
    val journalEpoch: LocalId? = null,
)

data class P04ClientEventHashInput(
    val eventId: LocalId,
    val idempotencyKey: String,
    val binding: ClientEventBinding,
    val deviceSequence: Long,
    val eventType: String,
    val aggregateType: String,
    val aggregateLocalId: LocalId,
    val aggregateServerId: String?,
    val baseServerRowVersion: Long?,
    val occurredAt: String,
    val payloadJson: String,
)

/**
 * Builds the RFC 8785 canonical form for the fixed P04 client-event envelope.
 *
 * [payloadJson] must already be canonical JSON. P05 emits only a bounded object of strings; later
 * event families should use a general JCS implementation when their owning phase activates them.
 */
object P04ClientEventHasher {
    fun canonicalJsonWithoutHash(input: P04ClientEventHashInput): String {
        require(input.deviceSequence >= 1)
        require(input.idempotencyKey.isNotEmpty() && input.idempotencyKey.length <= 200)
        require(input.payloadJson.toByteArray(StandardCharsets.UTF_8).size <= MAX_PAYLOAD_BYTES)
        return buildString {
            append('{')
            field("aggregate_local_id", jsonString(input.aggregateLocalId.value))
            field("aggregate_server_id", nullableJsonString(input.aggregateServerId))
            field("aggregate_type", jsonString(input.aggregateType))
            field("base_server_row_version", input.baseServerRowVersion?.toString() ?: "null")
            field("device_id", jsonString(input.binding.deviceId.value))
            field("device_sequence", input.deviceSequence.toString())
            field("event_id", jsonString(input.eventId.value))
            field("event_type", jsonString(input.eventType))
            field("idempotency_key", jsonString(input.idempotencyKey))
            field("occurred_at", jsonString(input.occurredAt))
            field("payload", input.payloadJson)
            field("schema_version", "1")
            field("server_profile_id", jsonString(input.binding.serverProfileId.value))
            lastField("user_id", jsonString(input.binding.userId.value))
            append('}')
        }
    }

    fun sha256(input: P04ClientEventHashInput): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(
            canonicalJsonWithoutHash(input).toByteArray(StandardCharsets.UTF_8),
        )

    private fun StringBuilder.field(name: String, value: String) {
        append(jsonString(name)).append(':').append(value).append(',')
    }

    private fun StringBuilder.lastField(name: String, value: String) {
        append(jsonString(name)).append(':').append(value)
    }

    private fun nullableJsonString(value: String?): String = value?.let(::jsonString) ?: "null"

    private fun jsonString(value: String): String = buildString {
        append('"')
        for (character in value) {
            when (character) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\b' -> append("\\b")
                '\u000C' -> append("\\f")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (character.code < 0x20) {
                    append("\\u").append(character.code.toString(16).padStart(4, '0'))
                } else {
                    append(character)
                }
            }
        }
        append('"')
    }

    private const val MAX_PAYLOAD_BYTES = 262_144
}
