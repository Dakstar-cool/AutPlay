package app.autplay.domain

import java.util.Locale
import java.util.UUID

/** A stable device-generated UUID used as the identity of a local aggregate. */
@JvmInline
value class LocalId(val value: String) {
    init {
        requireCanonicalUuid(value)
    }

    override fun toString(): String = value

    companion object {
        fun random(): LocalId = LocalId(UUID.randomUUID().toString())
    }
}

/** A stable canonical UUID assigned by the personal server. */
@JvmInline
value class ServerId(val value: String) {
    init {
        requireCanonicalUuid(value)
    }

    override fun toString(): String = value
}

/** Stable owner UUID returned by the authenticated personal server. */
@JvmInline
value class UserId(val value: String) {
    init {
        requireCanonicalUuid(value)
    }

    override fun toString(): String = value
}

/** Stable UUID of this installation's device identity. */
@JvmInline
value class DeviceId(val value: String) {
    init {
        requireCanonicalUuid(value)
    }

    override fun toString(): String = value

    companion object {
        fun random(): DeviceId = DeviceId(UUID.randomUUID().toString())
    }
}

/** Stable server-side profile UUID used to scope settings and credentials. */
@JvmInline
value class ServerProfileId(val value: String) {
    init {
        requireCanonicalUuid(value)
    }

    override fun toString(): String = value
}

/**
 * Lossless representation of a persisted string which may be newer than this client.
 *
 * Persistence adapters store [rawValue] and domain consumers must retain [Unknown] rather than
 * replacing it with a guessed default.
 */
sealed interface PersistedStringValue<out T> {
    val rawValue: String

    data class Known<T>(
        val value: T,
        override val rawValue: String,
    ) : PersistedStringValue<T>

    data class Unknown(
        override val rawValue: String,
    ) : PersistedStringValue<Nothing>

    companion object {
        /** Decodes a stored value without discarding an unrecognised raw string. */
        fun <T> decode(rawValue: String, decoder: (String) -> T?): PersistedStringValue<T> =
            decoder(rawValue)?.let { Known(value = it, rawValue = rawValue) } ?: Unknown(rawValue)
    }
}

private fun requireCanonicalUuid(value: String) {
    val parsed = runCatching { UUID.fromString(value) }
        .getOrElse { throw IllegalArgumentException("Identifier must be a canonical UUID.") }
    require(parsed.toString() == value.lowercase(Locale.ROOT) && value == value.lowercase(Locale.ROOT)) {
        "Identifier must be a lowercase canonical UUID."
    }
}
