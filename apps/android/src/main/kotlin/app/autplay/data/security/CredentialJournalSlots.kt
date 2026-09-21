package app.autplay.data.security

import app.autplay.domain.ServerProfileId

/** Private journal addresses must never be used as ordinary server credential profiles. */
object CredentialJournalSlots {
    val selfPairingSource = ServerProfileId("a89a7ef3-fb5a-46aa-a827-1bf120d55dab")
    val selfPairingRecipient = ServerProfileId("14c2edb0-f9ef-43d0-af74-63dc06373bfb")
    val recoverySource = ServerProfileId("f5419a71-6b38-4cb1-a28d-f20d4d8ba780")
    val recoveryRecipient = ServerProfileId("b623bd90-e84b-4621-9c83-a3bd69ba8db1")
    val deletionSource = ServerProfileId("3ca064af-046e-4fd7-af6d-b47ec651a7c7")
    val deletionRecipient = ServerProfileId("bf7d0f66-35dd-409b-8330-49d582507daf")
    val all: Set<ServerProfileId> = setOf(selfPairingSource, selfPairingRecipient, recoverySource,
        recoveryRecipient, deletionSource, deletionRecipient)

    /** UUIDv8 ad5c0e5e namespace is reserved for account-scoped encrypted consent journals. */
    fun trainingConsentProfile(server: ServerProfileId, account: String, origin: String): ServerProfileId {
        val raw = "autplay:training-consent-journal:v1\n${server.value}\n$account\n$origin".toByteArray(Charsets.UTF_8)
        val digest = try { java.security.MessageDigest.getInstance("SHA-256").digest(raw) }
            finally { raw.fill(0) }
        try {
            digest[0] = 0xad.toByte(); digest[1] = 0x5c; digest[2] = 0x0e; digest[3] = 0x5e
            digest[6] = ((digest[6].toInt() and 0x0f) or 0x80).toByte()
            digest[8] = ((digest[8].toInt() and 0x3f) or 0x80).toByte()
            val buffer = java.nio.ByteBuffer.wrap(digest)
            return ServerProfileId(java.util.UUID(buffer.long, buffer.long).toString())
        } finally { digest.fill(0) }
    }
}

fun ServerProfileId.isReservedCredentialProfile(): Boolean = this in CredentialJournalSlots.all ||
    value.startsWith("ad5c0e5e-") && java.util.UUID.fromString(value).version() == 8
