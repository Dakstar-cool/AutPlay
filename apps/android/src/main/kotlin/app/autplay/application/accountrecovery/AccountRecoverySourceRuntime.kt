package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.SelfPairingProof
import app.autplay.application.selfpairing.SelfPairingSourceBinding
import app.autplay.application.selfpairing.SelfPairingSourceContext
import app.autplay.application.selfpairing.integer
import app.autplay.application.selfpairing.text
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import java.time.Instant
import java.util.Base64
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

sealed interface AccountRecoverySourceState {
    data object Idle : AccountRecoverySourceState
    data object Working : AccountRecoverySourceState
    data class Ready(val configured: Boolean, val canExport: Boolean, val generation: Long, val exportConfirmed: Boolean = false) : AccountRecoverySourceState
    data class Blocked(val code: String, val pending: Boolean) : AccountRecoverySourceState
}

/** A successful configure keeps its code encrypted until explicit document export/rotation. */
class AccountRecoverySourceRuntime(
    private val credentials: CredentialStore, private val contexts: SelfPairingSourceContext,
    private val transport: AccountRecoveryTransport, private val now: () -> Instant = Instant::now,
    private val allowDevelopmentHttp: Boolean = false,
    private val settings: NonSecretSettingsStore? = null,
) {
    private val store = AccountRecoveryPendingStore(credentials)
    private val mutex = Mutex()
    private val mutable = MutableStateFlow<AccountRecoverySourceState>(AccountRecoverySourceState.Idle)
    val state: StateFlow<AccountRecoverySourceState> = mutable

    suspend fun loadLocal() = operation {
        val binding = contexts.current() ?: run { mutable.value = AccountRecoverySourceState.Idle; return@operation }
        val pending = store.read(SLOT)
        if (pending != null && pending.text("stage") == "SAVED" && matches(pending, binding)) {
            mutable.value = ready(binding, pending)
        } else {
            mutable.value = AccountRecoverySourceState.Idle
        }
    }

    suspend fun load() = operation {
        val binding = contexts.current() ?: run { mutable.value = AccountRecoverySourceState.Idle; return@operation }
        val pending = store.read(SLOT)
        mutable.value = AccountRecoverySourceState.Working
        if (pending != null && pending.text("stage") == "CONFIGURE_PENDING") {
            configurePending(binding, pending)
        } else {
            if (pending != null && matches(pending, binding)) {
                mutable.value = ready(binding, pending)
            }
            val status = transport.source(binding.identity, binding.profile)
            validateStatus(status, binding)
            val canExport = pending != null && matches(pending, binding) && pending.integer("code_generation") == status.integer("code_generation") && status.recoveryBoolean("configured")
            mutable.value = if (canExport) ready(binding, requireNotNull(pending)) else
                AccountRecoverySourceState.Ready(status.recoveryBoolean("configured"), false, status.integer("code_generation"))
        }
    }

    /** Explicit create/rotate action; never generated as a hidden server-side setup step. */
    suspend fun configure(replacePending: Boolean = false) = operation {
        require(!credentials.hasAccountRecoveryPendingRecipient()) { "recovery_resume_required" }
        val binding = requireNotNull(contexts.current()) { "recovery_active_account_required" }
        val old = store.read(SLOT)
        require(old == null || old.text("stage") == "SAVED" || replacePending) { "recovery_resume_required" }
        mutable.value = AccountRecoverySourceState.Working
        val status = transport.source(binding.identity, binding.profile)
        validateStatus(status, binding)
        val code = AccountRecoveryProof.code()
        val document = AccountRecoveryDocument(binding.identity, binding.userId, status.text("account_label"), code)
        try {
            val request = AccountRecoveryProof.request("configure", binding.identity, binding.userId, mapOf(
                "expected_code_generation" to JsonPrimitive(status.integer("code_generation")),
                "next_code_verifier_sha256" to JsonPrimitive(AccountRecoveryProof.verifier(binding.identity, binding.userId, code)),
            ), now())
            val file = document.encode()
            try {
                val pending = JsonObject(binding.identity.fields() + mapOf(
                    "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("CONFIGURE_PENDING"),
                    "account_id" to JsonPrimitive(binding.userId), "actor_device_id" to JsonPrimitive(binding.deviceId),
                    "actor_family_id" to JsonPrimitive(binding.familyId), "request_b64" to JsonPrimitive(b64(request)),
                    "document_b64" to JsonPrimitive(b64(file)),
                ))
                store.write(SLOT, pending)
                configurePending(binding, pending)
            } finally { request.fill(0); file.fill(0) }
        } finally { document.close() }
    }

    /** Raw local export does not acknowledge setup. Caller owns and must erase the returned bytes. */
    suspend fun export(): ByteArray = mutex.withLock { AccountRecoveryJournalGate.serialized {
        val binding = requireNotNull(contexts.current())
        val saved = requireNotNull(store.read(SLOT))
        require(saved.text("stage") == "SAVED" && matches(saved, binding))
        // Exporting already saved local material must also work without the optional server.
        document(saved, binding)
    } }

    /** Captures exactly the current account and document before an external picker can suspend. */
    suspend fun prepareExport(): AccountRecoveryExportTicket = exportGuard {
        exportMaterial().use { it.ticket }
    }

    /**
     * The writer returns only after destination close and bounded exact read-back verification.
     * No local lock is held during provider I/O. Its bytes remain ours and are erased on every exit.
     */
    suspend fun saveExport(ticket: AccountRecoveryExportTicket, destinationWriter: suspend (ByteArray) -> Unit) {
        val material = exportGuard { exportMaterial(ticket) }
        material.use {
            destinationWriter(it.bytes)
            currentCoroutineContext().ensureActive()
            require(SelfPairingProof.hash(it.bytes) == ticket.documentSha256) { "recovery_export_changed" }
            exportGuard {
                exportMaterial(ticket).use {
                    val settingsStore = requireNotNull(settings) { "recovery_export_context_unavailable" }
                    settingsStore.mutate { current ->
                        require(matches(ticket, current)) { "recovery_authority_changed" }
                        val checkpoint = current.accountRecoverySetup
                        if (checkpoint != null && checkpoint.matches(current)) {
                            current.copy(accountRecoverySetup = checkpoint.copy(
                                savedCodeGeneration = ticket.codeGeneration,
                                savedDocumentSha256 = ticket.documentSha256,
                            ))
                        } else current
                    }
                    val current = settingsStore.settings.first()
                    require(matches(ticket, current)) { "recovery_authority_changed" }
                    mutable.value = AccountRecoverySourceState.Ready(true, true, ticket.codeGeneration,
                        confirmed(ticket, current))
                }
            }
        }
    }

    /** Caller holds journal then binding gates; do not call contexts.current() under this lock. */
    private suspend fun exportMaterial(expected: AccountRecoveryExportTicket? = null): ExportMaterial {
        val settingsStore = requireNotNull(settings) { "recovery_export_context_unavailable" }
        require(!credentials.hasAccountRecoveryPendingRecipient()) { "recovery_resume_required" }
        val current = settingsStore.settings.first()
        val checkpoint = requireNotNull(current.m5Binding) { "recovery_active_account_required" }
        val profile = requireNotNull(current.activeServerProfileId) { "recovery_active_account_required" }
        val user = requireNotNull(current.activeUserId) { "recovery_active_account_required" }
        val device = requireNotNull(current.deviceId) { "recovery_active_account_required" }
        val binding = SelfPairingSourceBinding(profile, user.value, device.value, checkpoint.sessionFamilyId,
            SelfPairingIdentity(checkpoint.serverInstanceId, checkpoint.identityEpoch,
                checkpoint.identityThumbprintSha256, requireNotNull(current.serverBaseUrl), requireNotNull(current.streamBaseUrl)))
        val credential = requireNotNull(credentials.read(profile)) { "recovery_authority_changed" }
        try {
            val envelope = SessionCredentialEnvelopeCodec.decode(credential)
            require(envelope.bindingCommitId == checkpoint.bindingCommitId && envelope.sessionId == checkpoint.sessionId &&
                envelope.sessionFamilyId == checkpoint.sessionFamilyId && envelope.sessionGeneration == checkpoint.sessionGeneration &&
                envelope.refreshToken != null) { "recovery_authority_changed" }
        } finally { credential.fill(0) }
        val saved = requireNotNull(store.read(SLOT)) { "recovery_export_unavailable" }
        require(saved.text("stage") == "SAVED" && matches(saved, binding)) { "recovery_export_changed" }
        val raw = document(saved, binding)
        try {
            val ticket = AccountRecoveryExportTicket(profile, user.value, checkpoint.bindingCommitId, binding.identity,
                saved.integer("code_generation"), SelfPairingProof.hash(raw))
            require(ticket.codeGeneration > 0) { "recovery_export_changed" }
            if (expected != null) require(sameDocument(expected, ticket)) { "recovery_export_changed" }
            return ExportMaterial(ticket, raw)
        } catch (failure: Exception) { raw.fill(0); throw failure }
    }

    private suspend fun <T> exportGuard(block: suspend () -> T): T = mutex.withLock {
        AccountRecoveryJournalGate.serialized { BindingAuthorityWriteGate.serialized(block) }
    }

    private suspend fun ready(binding: SelfPairingSourceBinding, saved: JsonObject): AccountRecoverySourceState.Ready {
        val generation = saved.integer("code_generation")
        val settingsStore = settings ?: return AccountRecoverySourceState.Ready(true, true, generation)
        val current = settingsStore.settings.first()
        val checkpoint = current.accountRecoverySetup
        if (checkpoint == null || !checkpoint.matches(current)) return AccountRecoverySourceState.Ready(true, true, generation)
        val raw = document(saved, binding)
        return try {
            val ticket = AccountRecoveryExportTicket(binding.profile, binding.userId, checkpoint.bindingCommitId,
                binding.identity, generation, SelfPairingProof.hash(raw))
            AccountRecoverySourceState.Ready(true, true, generation, confirmed(ticket, current))
        } finally { raw.fill(0) }
    }

    private fun confirmed(ticket: AccountRecoveryExportTicket, current: NonSecretSettings): Boolean {
        val checkpoint = current.accountRecoverySetup ?: return false
        return matches(ticket, current) && checkpoint.matches(current) &&
            checkpoint.savedCodeGeneration == ticket.codeGeneration && checkpoint.savedDocumentSha256 == ticket.documentSha256
    }

    private fun matches(ticket: AccountRecoveryExportTicket, current: NonSecretSettings): Boolean {
        val binding = current.m5Binding ?: return false
        return current.activeServerProfileId == ticket.serverProfileId && current.activeUserId?.value == ticket.accountId &&
            current.deviceId != null && binding.bindingCommitId == ticket.bindingCommitId &&
            binding.serverInstanceId == ticket.identity.serverInstanceId && binding.identityEpoch == ticket.identity.epoch &&
            binding.identityThumbprintSha256 == ticket.identity.thumbprint &&
            current.serverBaseUrl == ticket.identity.apiOrigin && current.streamBaseUrl == ticket.identity.streamOrigin
    }

    private fun sameDocument(left: AccountRecoveryExportTicket, right: AccountRecoveryExportTicket): Boolean =
        left.serverProfileId == right.serverProfileId && left.accountId == right.accountId &&
            left.bindingCommitId == right.bindingCommitId && left.identity == right.identity &&
            left.codeGeneration == right.codeGeneration && left.documentSha256 == right.documentSha256

    private fun document(saved: JsonObject, binding: SelfPairingSourceBinding): ByteArray {
        val raw = Base64.getDecoder().decode(saved.text("document_b64"))
        try {
            AccountRecoveryDocument.parse(raw, allowDevelopmentHttp).use {
                require(it.identity == binding.identity && it.accountId == binding.userId) { "recovery_export_changed" }
            }
            return raw
        } catch (failure: Exception) { raw.fill(0); throw failure }
    }

    private class ExportMaterial(val ticket: AccountRecoveryExportTicket, val bytes: ByteArray) : AutoCloseable {
        override fun close() { bytes.fill(0) }
    }

    private suspend fun configurePending(binding: SelfPairingSourceBinding, pending: JsonObject) {
        require(matches(pending, binding) && pending.text("actor_device_id") == binding.deviceId && pending.text("actor_family_id") == binding.familyId) { "recovery_authority_changed" }
        mutable.value = AccountRecoverySourceState.Working
        val request = Base64.getDecoder().decode(pending.text("request_b64"))
        try {
            val parsed = SelfPairingJson.parse(request)
            val result = transport.source(binding.identity, binding.profile, request)
            require(result.keys == setOf("contract_version", "schema_version", "operation_id", "account_id", "code_generation", "configured", "replayed"))
            require(result.text("contract_version") == "v1" && result.integer("schema_version") == 1L)
            require(result.text("operation_id") == parsed.text("operation_id") && result.text("account_id") == binding.userId)
            require(result.recoveryBoolean("configured") && result.integer("code_generation") == parsed.integer("expected_code_generation") + 1)
            result.recoveryBoolean("replayed")
            val saved = JsonObject((pending - setOf("request_b64", "actor_device_id", "actor_family_id")) + mapOf(
                "stage" to JsonPrimitive("SAVED"), "code_generation" to result.getValue("code_generation")))
            store.write(SLOT, saved)
            mutable.value = ready(binding, saved)
        } finally { request.fill(0) }
    }
    private fun matches(value: JsonObject, binding: SelfPairingSourceBinding): Boolean =
        value.integer("schema_version") == 1L && value.text("account_id") == binding.userId && SelfPairingIdentity.parse(value, allowDevelopmentHttp) == binding.identity
    private fun validateStatus(value: JsonObject, binding: SelfPairingSourceBinding) {
        require(value.keys == setOf("contract_version", "schema_version", "account_id", "account_label", "configured", "code_generation"))
        require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L && value.text("account_id") == binding.userId)
        require(value.integer("code_generation") >= 0)
        value.recoveryBoolean("configured")
    }
    private suspend fun operation(block: suspend () -> Unit) = mutex.withLock { AccountRecoveryJournalGate.serialized {
        try { block() } catch (failure: CancellationException) { throw failure }
        catch (failure: Exception) {
            if (mutable.value is AccountRecoverySourceState.Ready) return@serialized
            mutable.value = AccountRecoverySourceState.Blocked(
                (failure as? AccountRecoveryRemoteFailure)?.code ?: "account_recovery_unavailable",
                runCatching { store.read(SLOT)?.text("stage") == "CONFIGURE_PENDING" }.getOrDefault(true))
        }
    } }
    private companion object {
        val SLOT = AccountRecoverySlot.SOURCE
        fun b64(value: ByteArray): String = Base64.getEncoder().encodeToString(value)
    }
}
