package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.SelfPairingSourceContext
import app.autplay.application.selfpairing.hasSelfDevicePairingPendingRecipient
import app.autplay.application.selfpairing.text
import app.autplay.application.selfpairing.integer
import app.autplay.application.selfpairing.instant
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import java.time.Instant
import java.util.Base64
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Captured bearer is used solely for this exact deletion request and is never a UI value. */
class AccountDeletionBinding(val identity: SelfPairingIdentity, val accountId: String, val deviceId: String,
    val commitId: String, val keyAlias: String, val access: ByteArray) : AutoCloseable {
    override fun close() = access.fill(0)
}

interface AccountDeletionBindingPort {
    /** Holds the binding gate through capture, durable journal verification, and local detach. */
    suspend fun prepare(accountId: String, identity: SelfPairingIdentity, persist: suspend (AccountDeletionBinding) -> Unit)
    /** Detaches only the captured binding, including after death between journal write and detach. */
    suspend fun detach(pending: JsonObject)
}

sealed interface AccountDeletionSourceState {
    data object Idle : AccountDeletionSourceState
    data object Working : AccountDeletionSourceState
    data class Ready(val status: AccountDeletionStatus) : AccountDeletionSourceState
    data class Recorded(val receipt: AccountDeletionReceipt) : AccountDeletionSourceState
    data class NotAccepted(val resolution: AccountDeletionNotAccepted) : AccountDeletionSourceState
    data class Blocked(val code: String, val pending: Boolean) : AccountDeletionSourceState
}

/** A sent or possibly sent deletion is never silently forgotten or regenerated. */
class AccountDeletionSourceRuntime(
    private val credentials: CredentialStore, private val contexts: SelfPairingSourceContext,
    private val binding: AccountDeletionBindingPort, private val keys: M5DeviceKeyStore,
    private val transport: AccountDeletionTransport, private val deviceName: String, private val appVersion: String,
    private val now: () -> Instant = Instant::now, private val allowDevelopmentHttp: Boolean = false,
) {
    private val store = AccountRecoveryPendingStore(credentials)
    private val mutex = Mutex()
    private val mutable = MutableStateFlow<AccountDeletionSourceState>(AccountDeletionSourceState.Idle)
    val state: StateFlow<AccountDeletionSourceState> = mutable

    suspend fun load() = operation {
        val pending = store.read(SLOT)
        if (pending != null) { resumePending(pending); return@operation }
        val current = contexts.current() ?: run { mutable.value = AccountDeletionSourceState.Idle; return@operation }
        mutable.value = AccountDeletionSourceState.Working
        mutable.value = AccountDeletionSourceState.Ready(AccountDeletionStatus.parse(
            transport.status(current.identity, current.profile), current.userId))
    }

    suspend fun request(accountId: String, codeText: String) = operation {
        require(store.read(SLOT) == null) { "deletion_resume_required" }
        require(!credentials.hasAccountRecoveryPendingRecipient() && !credentials.hasSelfDevicePairingPendingRecipient()) { "deletion_resume_required" }
        val visible = mutable.value as? AccountDeletionSourceState.Ready ?: error("deletion_confirmation_required")
        require(visible.status.canRequest && visible.status.accountId == accountId) { "deletion_confirmation_required" }
        val current = requireNotNull(contexts.current()) { "deletion_binding_changed" }
        require(current.userId == accountId)
        val code = AccountRecoveryProof.normalize(codeText)
        try {
            mutable.value = AccountDeletionSourceState.Working
            val status = AccountDeletionStatus.parse(transport.status(current.identity, current.profile), accountId)
            require(status.canRequest) { status.reason ?: "account_deletion_unavailable" }
            AccountRecoveryJournalGate.serialized {
                require(store.read(SLOT) == null) { "deletion_resume_required" }
                binding.prepare(accountId, current.identity) { captured ->
                    val request = AccountDeletionProof.request("request", current.identity, accountId,
                        AccountRecoveryProof.device(keys, captured.keyAlias, deviceName, appVersion) + mapOf(
                            "confirmed_account_id" to JsonPrimitive(accountId),
                            "expected_code_generation" to JsonPrimitive(status.codeGeneration),
                            "expected_authority_generation" to JsonPrimitive(status.authorityGeneration)), now(), keys, captured.keyAlias)
                    try {
                        val pending = JsonObject(current.identity.fields() + mapOf(
                            "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("REQUEST_PENDING"),
                            "account_id" to JsonPrimitive(accountId), "device_id" to JsonPrimitive(captured.deviceId),
                            "binding_commit_id" to JsonPrimitive(captured.commitId), "key_alias" to JsonPrimitive(captured.keyAlias),
                            "access" to JsonPrimitive(captured.access.toString(Charsets.US_ASCII)),
                            "code" to JsonPrimitive(code.toString(Charsets.US_ASCII)), "request_b64" to JsonPrimitive(b64(request))))
                        store.write(SLOT, pending)
                        require(store.read(SLOT) == pending) { "deletion_journal_not_durable" }
                    } finally { request.fill(0) }
                }
            }
            resumePending(requireNotNull(store.read(SLOT)))
        } finally { code.fill(0) }
    }

    suspend fun resume() = operation { store.read(SLOT)?.let { resumePending(it) } }

    private suspend fun resumePending(pending: JsonObject) {
        val identity = SelfPairingIdentity.parse(pending, allowDevelopmentHttp)
        require(pending.integer("schema_version") == 1L && pending.text("stage") in setOf("REQUEST_PENDING", "RECORDED"))
        binding.detach(pending)
        mutable.value = AccountDeletionSourceState.Working
        val raw = Base64.getDecoder().decode(pending.text("request_b64"))
        val code = AccountRecoveryProof.normalize(pending.text("code"))
        val access = if (pending.text("stage") == "REQUEST_PENDING") pending.text("access").toByteArray(Charsets.US_ASCII) else null
        try {
            val request = SelfPairingJson.parse(raw)
            require(request.text("account_id") == pending.text("account_id") && SelfPairingIdentity.parse(request, allowDevelopmentHttp) == identity)
            // Resolve a possible prior acceptance before attempting any actor-authenticated action.
            var resolutionReply = false
            val result = try { transport.receipt(identity, code, raw) }
            catch (failure: CancellationException) { throw failure }
            catch (_: Exception) {
                if (access == null) AccountDeletionReceipt.fromJournal(pending)
                else if (now().isAfter(request.instant("requested_at").plusSeconds(120))) {
                    resolutionReply = true
                    transport.resolve(identity, code, raw)
                }
                else transport.request(identity, access, code, raw)
            }
            if (result.text("state") == "NOT_ACCEPTED") {
                require(resolutionReply && access != null) { "deletion_positive_receipt_conflict" }
                val resolution = AccountDeletionNotAccepted.parse(result, request)
                AccountRecoveryJournalGate.serialized {
                    if (store.read(SLOT) != pending) {
                        mutable.value = AccountDeletionSourceState.Idle
                        return@serialized
                    }
                    store.clear(SLOT)
                    require(store.read(SLOT) == null) { "deletion_journal_not_durable" }
                    mutable.value = AccountDeletionSourceState.NotAccepted(resolution)
                }
                return
            }
            val receipt = AccountDeletionReceipt.parse(result, pending.text("account_id"), request.text("operation_id"))
            // Retain the historical proof to observe cancellation performed on another phone.
            // A known receipt never resends the actor-authenticated deletion request.
            val recorded = JsonObject(identity.fields() + mapOf("schema_version" to JsonPrimitive(1),
                "stage" to JsonPrimitive("RECORDED"), "account_id" to pending.getValue("account_id"),
                "device_id" to pending.getValue("device_id"), "binding_commit_id" to pending.getValue("binding_commit_id"),
                "key_alias" to pending.getValue("key_alias"), "code" to pending.getValue("code"),
                "request_b64" to pending.getValue("request_b64"), "receipt_b64" to AccountDeletionReceipt.journalValue(result)))
            AccountRecoveryJournalGate.serialized {
                // Network replies cannot resurrect a journal cleared by durable cancellation.
                if (store.read(SLOT) != pending) {
                    mutable.value = AccountDeletionSourceState.Idle
                    return@serialized
                }
                if (receipt.state == "PENDING") {
                    store.write(SLOT, recorded)
                    require(store.read(SLOT) == recorded) { "deletion_journal_not_durable" }
                } else store.clear(SLOT)
                mutable.value = AccountDeletionSourceState.Recorded(receipt)
            }
        } finally { raw.fill(0); code.fill(0); access?.fill(0) }
    }

    private suspend fun operation(block: suspend () -> Unit) = mutex.withLock {
        try { block() } catch (failure: CancellationException) { throw failure }
        catch (failure: Exception) {
            val pending = runCatching { store.read(SLOT) != null }.getOrDefault(true)
            mutable.value = AccountDeletionSourceState.Blocked((failure as? AccountRecoveryRemoteFailure)?.code
                ?: "account_deletion_unavailable", pending)
        }
    }
    private companion object {
        val SLOT = AccountRecoverySlot.DELETION_SOURCE
        fun b64(value: ByteArray): String = Base64.getEncoder().encodeToString(value)
    }
}
