package app.autplay.application.securestate

import kotlinx.serialization.json.*

/** Unknown persisted state/veto is retained and blocks LOCAL. No session/capability dependency. */
public data class LocalUseGuard(public val revision: Long, public val state: String, public val veto: String) {
    init { secureRequire(revision in 1..SecureStateLimits.INTEGER); boundedTag(state); boundedTag(veto) }
    public val available: Boolean get() = state == "AVAILABLE" && veto == "NONE"
    override fun toString(): String = "LocalUseGuard(<redacted>)"
}
public data class ProtectedTransportPolicy(public val revision: Long,
    public val apiMode: String, public val apiPin: String?, public val streamMode: String,
    public val streamPin: String?, public val confirmedAtMs: Long) {
    init {
        secureRequire(revision in 1..SecureStateLimits.INTEGER && confirmedAtMs in 0..SecureStateLimits.INTEGER)
        boundedTag(apiMode); boundedTag(streamMode); apiPin?.let(::sha256Text); streamPin?.let(::sha256Text)
        listOf(apiMode to apiPin, streamMode to streamPin).forEach { (mode, pin) ->
            if (mode == "PLATFORM_PKI") secureRequire(pin == null)
            if (mode == "CONFIRMED_SPKI") secureRequire(pin != null)
        }
    }
    public val known: Boolean get() = apiMode in setOf("PLATFORM_PKI", "CONFIRMED_SPKI") && streamMode in setOf("PLATFORM_PKI", "CONFIRMED_SPKI")
    override fun toString(): String = "ProtectedTransportPolicy(<redacted>)"
}
/** M5 is ONLY its own positive singleton revision. No equal-revision content-hash rule. */
public data class M5CapabilityGuard(public val revision: Long) {
    init { secureRequire(revision in 1..SecureStateLimits.INTEGER) }
    public fun accepts(next: M5CapabilityGuard): Boolean = next.revision >= revision
    override fun toString(): String = "M5CapabilityGuard(<redacted>)"
}
private fun boundedTag(value: String): Unit = secureRequire(value.length in 1..64 && value.all { it in 'A'..'Z' || it in '0'..'9' || it == '_' })

public object SecureMediaGuardCodec {
    public fun local(value: LocalUseGuard): ByteArray = bounded(SecureJson.encode(buildJsonObject {
        put("local_use_format", 1); put("localUseRevision", value.revision); put("localUseState", value.state); put("localVeto", value.veto)
    }), 512)
    public fun readLocal(bytes: ByteArray): LocalUseGuard {
        val value = SecureJson.parse(bytes, 512)
        value.exact("local_use_format", "localUseRevision", "localUseState", "localVeto")
        secureRequire(value.number("local_use_format") == 1L)
        return LocalUseGuard(value.number("localUseRevision"), value.string("localUseState"), value.string("localVeto"))
    }
    public fun transport(value: ProtectedTransportPolicy): ByteArray = bounded(SecureJson.encode(buildJsonObject {
        put("transport_format", 1); put("transportPolicyRevision", value.revision)
        put("apiMode", value.apiMode); put("apiTlsSpkiSha256", value.apiPin?.let(::JsonPrimitive) ?: JsonNull)
        put("streamMode", value.streamMode); put("streamTlsSpkiSha256", value.streamPin?.let(::JsonPrimitive) ?: JsonNull)
        put("confirmedAtMs", value.confirmedAtMs)
    }), 1024)
    public fun readTransport(bytes: ByteArray): ProtectedTransportPolicy {
        val value = SecureJson.parse(bytes, 1024)
        value.exact("transport_format", "transportPolicyRevision", "apiMode", "apiTlsSpkiSha256", "streamMode", "streamTlsSpkiSha256", "confirmedAtMs")
        secureRequire(value.number("transport_format") == 1L)
        return ProtectedTransportPolicy(value.number("transportPolicyRevision"), value.string("apiMode"), value.nullableString("apiTlsSpkiSha256"), value.string("streamMode"), value.nullableString("streamTlsSpkiSha256"), value.number("confirmedAtMs"))
    }
    public fun m5(value: M5CapabilityGuard): ByteArray = bounded(SecureJson.encode(buildJsonObject {
        put("m5_capability_guard_format", 1); put("m5CapabilityRevisionHighWater", value.revision)
    }), 256)
    public fun readM5(bytes: ByteArray): M5CapabilityGuard {
        val value = SecureJson.parse(bytes, 256)
        value.exact("m5_capability_guard_format", "m5CapabilityRevisionHighWater")
        secureRequire(value.number("m5_capability_guard_format") == 1L)
        return M5CapabilityGuard(value.number("m5CapabilityRevisionHighWater"))
    }
    private fun bounded(bytes: ByteArray, maximum: Int): ByteArray {
        if (bytes.size > maximum) { bytes.fill(0); throw SecureStateFailure(SecureStateStatus.CAPACITY_REQUIRED) }; return bytes
    }
}
