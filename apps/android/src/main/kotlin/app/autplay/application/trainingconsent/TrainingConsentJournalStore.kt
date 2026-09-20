package app.autplay.application.trainingconsent

import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.isReservedCredentialProfile
import app.autplay.domain.ServerProfileId
import kotlinx.serialization.json.JsonObject

/** One account/server journal; a different binding may only explicitly supersede it privately. */
class TrainingConsentJournalStore(private val credentials: CredentialStore, private val profile: ServerProfileId) {
    init { require(profile.isReservedCredentialProfile() && profile.value.startsWith("ad5c0e5e-")) }
    internal suspend fun read(): JsonObject? {
        val material = credentials.read(profile) ?: return null
        return try {
            val envelope = SessionCredentialEnvelopeCodec.decode(material)
            require(envelope.accountRecoveryRole == "TRAINING_CONSENT")
            val raw = requireNotNull(envelope.accountRecoveryPending).toByteArray(Charsets.UTF_8)
            try { SelfPairingJson.parse(raw, 4096) } finally { raw.fill(0) }
        } finally { material.fill(0) }
    }
    internal suspend fun write(document: JsonObject) {
        val raw = SelfPairingJson.canonical(document)
        try {
            require(raw.size <= 4096)
            val envelope = SessionCredentialEnvelope("pending-training-consent", null, 0, refreshPending = true,
                accountRecoveryRole = "TRAINING_CONSENT", accountRecoveryPending = raw.toString(Charsets.UTF_8))
            val encoded = SessionCredentialEnvelopeCodec.encode(envelope)
            try { credentials.write(profile, encoded) } finally { encoded.fill(0) }
        } finally { raw.fill(0) }
    }
    internal suspend fun clear() = credentials.clear(profile)
}
