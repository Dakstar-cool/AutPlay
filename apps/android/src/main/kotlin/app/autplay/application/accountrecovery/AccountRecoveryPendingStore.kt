package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.text
import app.autplay.application.selfpairing.integer
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import kotlinx.serialization.json.JsonObject
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.withLock

internal object AccountRecoveryJournalGate {
    private val mutex = Mutex()
    suspend fun <T> serialized(block: suspend () -> T): T = mutex.withLock { block() }
}

enum class AccountRecoverySlot(val profile: ServerProfileId) {
    SOURCE(CredentialJournalSlots.recoverySource),
    RECIPIENT(CredentialJournalSlots.recoveryRecipient),
    DELETION_SOURCE(CredentialJournalSlots.deletionSource),
    DELETION_RECIPIENT(CredentialJournalSlots.deletionRecipient),
}

class AccountRecoveryPendingStore(private val credentials: CredentialStore) {
    internal suspend fun read(slot: AccountRecoverySlot): JsonObject? {
        val material = credentials.read(slot.profile) ?: return null
        return try {
            val envelope = SessionCredentialEnvelopeCodec.decode(material)
            require(envelope.accountRecoveryRole == slot.name)
            val raw = requireNotNull(envelope.accountRecoveryPending).toByteArray(Charsets.UTF_8)
            try { SelfPairingJson.parse(raw, 65_536) } finally { raw.fill(0) }
        } finally { material.fill(0) }
    }
    internal suspend fun write(slot: AccountRecoverySlot, document: JsonObject) {
        val raw = SelfPairingJson.canonical(document)
        try {
            require(raw.size <= 65_536)
            val envelope = SessionCredentialEnvelope("pending-account-recovery", null, 0, refreshPending = true,
                accountRecoveryRole = slot.name, accountRecoveryPending = raw.toString(Charsets.UTF_8))
            val encoded = SessionCredentialEnvelopeCodec.encode(envelope)
            try { credentials.write(slot.profile, encoded) } finally { encoded.fill(0) }
        } finally { raw.fill(0) }
    }
    internal suspend fun clear(slot: AccountRecoverySlot) = credentials.clear(slot.profile)
}

suspend fun CredentialStore.hasAccountRecoveryPendingRecipient(): Boolean =
    AccountRecoveryPendingStore(this).let {
        it.read(AccountRecoverySlot.RECIPIENT) != null || it.read(AccountRecoverySlot.DELETION_RECIPIENT) != null ||
            hasUnresolvedAccountDeletion()
    }

suspend fun CredentialStore.hasAccountDeletionPendingRecipient(): Boolean =
    AccountRecoveryPendingStore(this).read(AccountRecoverySlot.DELETION_RECIPIENT) != null

suspend fun CredentialStore.hasOrdinaryAccountRecoveryPendingRecipient(): Boolean =
    AccountRecoveryPendingStore(this).read(AccountRecoverySlot.RECIPIENT) != null

suspend fun CredentialStore.hasUnresolvedAccountDeletion(): Boolean {
    val pending = AccountRecoveryPendingStore(this).read(AccountRecoverySlot.DELETION_SOURCE) ?: return false
    require(pending.integer("schema_version") == 1L && pending.text("stage") in setOf("REQUEST_PENDING", "RECORDED"))
    return pending.text("stage") == "REQUEST_PENDING"
}

/** Unreadable private intent fails closed. A foreign binding remains usable. */
suspend fun CredentialStore.accountDeletionVetoes(profile: ServerProfileId, bindingCommitId: String?): Boolean =
    try {
        val pending = AccountRecoveryPendingStore(this).read(AccountRecoverySlot.DELETION_SOURCE)
        if (pending == null) false else {
        require(pending.integer("schema_version") == 1L && pending.text("stage") in setOf("REQUEST_PENDING", "RECORDED"))
        pending.text("expected_server_instance_id") == profile.value &&
            (bindingCommitId == null || pending.text("binding_commit_id") == bindingCommitId)
        }
    } catch (failure: CancellationException) { throw failure }
    catch (_: Exception) { true }
