package app.autplay.application.desktop.media

import app.autplay.application.desktop.account.AccountUuid
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation
import app.autplay.application.syncbinding.SyncBindingContractV2
import java.net.URI
import java.time.Duration
import java.time.Instant
import java.util.Collections

typealias MediaId = AccountUuid

object DesktopMediaLimits {
    const val MAX_INTEGER: Long = 9_007_199_254_740_991L
    const val MAX_OBJECT_BYTES: Long = 2_147_483_648L
    const val SNAPSHOT_BYTES: Int = 4_096
    const val CONTROL_MS: Long = 15_000L
    const val OBSERVATION_MS: Long = 10_000L
    const val PERMIT_MS: Long = 10_000L
    fun positive(value: Long): Unit { require(value in 1..MAX_INTEGER) { "PROTOCOL_ERROR" } }
    fun generation(value: Long): Unit { require(value in 0..MAX_INTEGER) { "PROTOCOL_ERROR" } }
    fun next(value: Long): Long {
        generation(value)
        require(value < MAX_INTEGER) { "CAPACITY_EXCEEDED" }
        return value + 1
    }
    fun digest(value: String): Unit { require(Regex("[0-9a-f]{64}").matches(value)) { "PROTOCOL_ERROR" } }
}

/** Intentionally noninterchangeable with the scoped D1/B1 revision. */
@JvmInline value class M5CapabilityRevision(val value: Long) {
    init { DesktopMediaLimits.positive(value) }
}
@JvmInline value class D1B1CapabilityRevision(val value: Long) {
    init { DesktopMediaLimits.generation(value) }
}

/** Process-only confirmed origin. Parsing establishes syntax, never confirmation or trust. */
data class ConfirmedMediaOrigin(val value: String) {
    init {
        require(value.length in 1..4_096 && value.all { it.code in 33..126 }) { "ORIGIN_REJECTED" }
        val uri = try { URI(value) } catch (_: Exception) { throw IllegalArgumentException("ORIGIN_REJECTED") }
        require(uri.scheme == "https" && uri.rawUserInfo == null && uri.rawQuery == null &&
            uri.rawFragment == null && uri.rawPath.isNullOrEmpty() && uri.host != null &&
            uri.host == uri.host.lowercase() && uri.port in -1..65535 && uri.port != 0 && uri.port != 443 &&
            '%' !in value && '\\' !in value) { "ORIGIN_REJECTED" }
        val host = uri.host
        if (host.startsWith("[")) require(host == canonicalIpv6(host)) { "ORIGIN_REJECTED" }
        else {
            require(host.length <= 253 && !host.endsWith('.') && host.split('.').all {
                Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?").matches(it)
            }) { "ORIGIN_REJECTED" }
            if (host.all { it.isDigit() || it == '.' }) require(host.split('.').let { parts ->
                parts.size == 4 && parts.all { Regex("0|[1-9][0-9]{0,2}").matches(it) && it.toInt() <= 255 }
            }) { "ORIGIN_REJECTED" }
        }
        require(uri.rawAuthority == host + if (uri.port == -1) "" else ":${uri.port}") { "ORIGIN_REJECTED" }
    }
    override fun toString(): String = "ConfirmedMediaOrigin(<redacted>)"
    companion object {
        private fun canonicalIpv6(host: String): String? {
            val text = host.removePrefix("[").removeSuffix("]")
            val halves = text.split("::")
            if (halves.size > 2 || '.' in text) return null
            val left = if (halves[0].isEmpty()) emptyList() else halves[0].split(':')
            val right = if (halves.size == 1 || halves[1].isEmpty()) emptyList() else halves[1].split(':')
            if ((left + right).any { !Regex("[0-9a-f]{1,4}").matches(it) }) return null
            val missing = 8 - left.size - right.size
            if (if (halves.size == 1) missing != 0 else missing < 1) return null
            val words = left.map { it.toInt(16) } + List(missing) { 0 } + right.map { it.toInt(16) }
            var bestStart = -1; var bestLength = 1; var index = 0
            while (index < 8) {
                if (words[index] != 0) { index++; continue }
                val start = index
                while (index < 8 && words[index] == 0) index++
                if (index - start > bestLength) { bestStart = start; bestLength = index - start }
            }
            val rendered = if (bestStart < 0) words.joinToString(":") { it.toString(16) }
            else words.take(bestStart).joinToString(":") { it.toString(16) } + "::" +
                words.drop(bestStart + bestLength).joinToString(":") { it.toString(16) }
            return "[$rendered]"
        }
    }
}

sealed interface MediaTransportPolicy {
    val origin: ConfirmedMediaOrigin
    val policyRevision: Long
    data class PlatformPki(override val origin: ConfirmedMediaOrigin, override val policyRevision: Long) : MediaTransportPolicy {
        init { DesktopMediaLimits.positive(policyRevision) }
        override fun toString(): String = "PlatformPki(<redacted>)"
    }
    data class ConfirmedSpki(override val origin: ConfirmedMediaOrigin, override val policyRevision: Long,
        val tlsSpkiSha256: String) : MediaTransportPolicy {
        init { DesktopMediaLimits.positive(policyRevision); DesktopMediaLimits.digest(tlsSpkiSha256) }
        override fun toString(): String = "ConfirmedSpki(<redacted>)"
    }
}

data class MediaSelectionFence(
    val bootId: MediaId, val operationId: MediaId, val selectionRevision: Long,
    val sourceRevision: Long, val desiredIntentRevision: Long, val queueId: MediaId?,
    val entryId: MediaId?, val listenId: MediaId?, val playbackGeneration: Long,
) {
    init {
        listOf(selectionRevision, sourceRevision, desiredIntentRevision, playbackGeneration).forEach(DesktopMediaLimits::positive)
        require((queueId == null) == (entryId == null) && (entryId == null) == (listenId == null)) { "PROTOCOL_ERROR" }
    }
    override fun toString(): String = "MediaSelectionFence(<redacted>)"
}

sealed interface LocalOwnerKey {
    data class Portable(val accountId: MediaId) : LocalOwnerKey {
        override fun toString(): String = "LocalOwnerKey.PORTABLE"
    }
    data class BoundLocal(val serverInstanceId: MediaId, val userId: MediaId,
        val ownerBindingCommitId: MediaId) : LocalOwnerKey {
        override fun toString(): String = "LocalOwnerKey.BOUND_LOCAL"
    }
}

enum class LocalUseState { AVAILABLE, LOCKED, FORGOTTEN, UNKNOWN }
enum class LocalMediaVeto { NONE, DEVICE_REVOKED, ACCOUNT_REVOKED, DELETION_PENDING, DELETED, UNKNOWN }
data class LocalGuardCapture(val ownerReference: MediaId, val revision: Long,
    val state: LocalUseState, val veto: LocalMediaVeto) {
    init { DesktopMediaLimits.positive(revision) }
    override fun toString(): String = "LocalGuardCapture(<redacted>)"
}
/**
 * Runtime comparison only, no K encoder/custody. Real owner must supply its exact <=512-byte LOCAL
 * guard, <=1024-byte transport policy and separate <=256-byte M5 high-water inside unchanged K
 * owner/value/installation budgets. Missing/unknown records cannot be initialized from these DTOs.
 */
data class AccountLocalCapture(
    val storeId: MediaId, val scopeId: MediaId, val activationRevision: Long, val leaseRevision: Long,
    val localOwner: LocalOwnerKey, val kOwnerReference: MediaId, val kValueRevision: Long,
    val publicationCommitId: MediaId, val kAuthorityRevision: Long, val localGuard: LocalGuardCapture,
    /** Explicitly null for an independent source copy; required for a downloaded copy. */
    val originLocalGuard: LocalGuardCapture?,
) {
    init {
        listOf(activationRevision, leaseRevision, kValueRevision, kAuthorityRevision).forEach(DesktopMediaLimits::positive)
        require(kOwnerReference == localGuard.ownerReference) { "OWNER_STALE" }
    }
    override fun toString(): String = "AccountLocalCapture(<redacted>)"
}

/** Accepted signed M5 profile-v1 document in bounded runtime memory, NEVER itself admission. */
class M5ProfileCapture(
    val documentId: MediaId, val serverInstanceId: MediaId, val identityEpoch: Long,
    val applicationKeyThumbprint: String, val userId: MediaId, val deviceId: MediaId,
    val m5CapabilityRevision: M5CapabilityRevision, val m5CapabilityRevisionHighWater: M5CapabilityRevision,
    val payloadSha256: String, val issuedAt: Instant, val expiresAt: Instant,
    acceptedProfileEnvelope: ByteArray, operations: Set<String>,
) {
    private val envelope: ByteArray = acceptedProfileEnvelope.also {
        require(it.size in 1..65_536) { "CAPABILITY_UNAVAILABLE" }
    }.copyOf()
    val operations: Set<String> = Collections.unmodifiableSet(operations.also {
        require(it.size <= 64) { "CAPABILITY_UNAVAILABLE" }
    }.toSet())
    init {
        DesktopMediaLimits.positive(identityEpoch)
        DesktopMediaLimits.digest(applicationKeyThumbprint); DesktopMediaLimits.digest(payloadSha256)
        require(envelope.size in 1..65_536 && operations.size <= 64) { "CAPABILITY_UNAVAILABLE" }
        require(expiresAt > issuedAt && Duration.between(issuedAt, expiresAt) <= Duration.ofHours(1)) { "CAPABILITY_UNAVAILABLE" }
        require(m5CapabilityRevision.value >= m5CapabilityRevisionHighWater.value) { "CAPABILITY_UNAVAILABLE" }
    }
    /** Verification owner compares the complete actual signed payload/operations/limits, including additions. */
    fun acceptedDocumentBytes(): ByteArray = envelope.copyOf()
    override fun toString(): String = "M5ProfileCapture(<redacted>)"
}

data class D1B1BindingGuardCapture(
    val protectedOwnerReference: MediaId, val trustRevision: Long,
    val globalCapabilityRevisionHighWater: D1B1CapabilityRevision,
    val d1ContentGuard: DeviceContractV2Negotiation.CapabilityObservation?,
    val b1Guard: SyncBindingContractV2.RevisionGuard?,
) {
    init { DesktopMediaLimits.positive(trustRevision) }
    override fun toString(): String = "D1B1BindingGuardCapture(<redacted>)"
}

data class JMediaProjectionCapture(
    val storeId: MediaId, val scopeId: MediaId, val localTrackRefId: MediaId,
    val userTrackRefId: MediaId, val projectionRevision: Long, val userTrackRefRowVersion: Long,
    val recordingId: MediaId, val pendingTargetMutation: Boolean, val conflict: Boolean, val tombstone: Boolean,
) {
    init { listOf(projectionRevision, userTrackRefRowVersion).forEach(DesktopMediaLimits::positive) }
    override fun toString(): String = "JMediaProjectionCapture(<redacted>)"
}

data class VaultNetworkCapture(
    val local: AccountLocalCapture, val profileId: MediaId, val userId: MediaId, val deviceId: MediaId,
    val deviceKeyThumbprint: String, val bindingCommitId: MediaId, val kAuthorityRevision: Long,
    val serverInstanceId: MediaId, val identityEpoch: Long, val applicationKeyThumbprint: String,
    val apiPolicy: MediaTransportPolicy, val streamPolicy: MediaTransportPolicy,
    val sessionId: MediaId, val sessionFamilyId: MediaId, val sessionGeneration: Long,
    val credentialGeneration: Long, val m5Profile: M5ProfileCapture,
    val d1b1Guards: D1B1BindingGuardCapture, val remoteVeto: LocalMediaVeto,
    val projection: JMediaProjectionCapture,
) {
    init {
        DesktopMediaLimits.digest(deviceKeyThumbprint); DesktopMediaLimits.digest(applicationKeyThumbprint)
        DesktopMediaLimits.positive(kAuthorityRevision); DesktopMediaLimits.positive(identityEpoch)
        DesktopMediaLimits.generation(sessionGeneration); DesktopMediaLimits.generation(credentialGeneration)
        require(projection.storeId == local.storeId && projection.scopeId == local.scopeId) { "OWNER_STALE" }
    }
    override fun toString(): String = "VaultNetworkCapture(<redacted>)"
}

sealed interface SelectionCapture {
    val fence: MediaSelectionFence
    data class StandaloneLocal(override val fence: MediaSelectionFence, val storeId: MediaId,
        val readOwnerId: MediaId, val storeSelectionRevision: Long) : SelectionCapture {
        init { DesktopMediaLimits.positive(storeSelectionRevision) }
        override fun toString(): String = "SelectionCapture.StandaloneLocal"
    }
    data class AccountLocal(override val fence: MediaSelectionFence, val account: AccountLocalCapture) : SelectionCapture {
        override fun toString(): String = "SelectionCapture.AccountLocal"
    }
    data class VaultNetwork(override val fence: MediaSelectionFence, val network: VaultNetworkCapture) : SelectionCapture {
        override fun toString(): String = "SelectionCapture.VaultNetwork"
    }
}
