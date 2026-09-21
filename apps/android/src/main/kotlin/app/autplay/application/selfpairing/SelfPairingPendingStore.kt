package app.autplay.application.selfpairing

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import kotlinx.serialization.json.JsonObject

/** Stable private slots in the existing encrypted store, never active server profile IDs. */
enum class SelfPairingRole(val slot: ServerProfileId) {
    SOURCE(CredentialJournalSlots.selfPairingSource),
    RECIPIENT(CredentialJournalSlots.selfPairingRecipient),
}

/** The runtime serializes journal writes and network effects through a single-flight mutex. */
class SelfPairingPendingStore(private val credentials: CredentialStore) {
    internal suspend fun read(role: SelfPairingRole): JsonObject? {
        val encrypted = credentials.read(role.slot) ?: return null
        return try {
            val envelope = SessionCredentialEnvelopeCodec.decode(encrypted)
            require(envelope.selfDevicePairingRole == role.name) { "SELF_PAIRING_PENDING_INVALID" }
            val raw = requireNotNull(envelope.selfDevicePairingPending).toByteArray(Charsets.UTF_8)
            try { SelfPairingJson.parse(raw, 65_536) } finally { raw.fill(0) }
        } finally { encrypted.fill(0) }
    }

    internal suspend fun write(role: SelfPairingRole, document: JsonObject) {
        val raw = SelfPairingJson.canonical(document)
        try {
            require(raw.size <= 65_536)
            val envelope = SessionCredentialEnvelope(
                accessToken = "pending-self-device-pairing", refreshToken = null,
                generation = 0, refreshPending = true,
                selfDevicePairingRole = role.name, selfDevicePairingPending = raw.toString(Charsets.UTF_8),
            )
            val encoded = SessionCredentialEnvelopeCodec.encode(envelope)
            try { credentials.write(role.slot, encoded) } finally { encoded.fill(0) }
        } finally { raw.fill(0) }
    }

    suspend fun clear(role: SelfPairingRole) = credentials.clear(role.slot)
}

/** Decode failure is propagated so a competing first-bind flow fails closed after restart. */
suspend fun CredentialStore.hasSelfDevicePairingPendingRecipient(): Boolean =
    SelfPairingPendingStore(this).read(SelfPairingRole.RECIPIENT) != null
