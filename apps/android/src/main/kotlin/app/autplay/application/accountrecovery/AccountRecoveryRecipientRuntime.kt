package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.FirstBindCeremonyOwner
import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.profilepairing.PairingNetworkResult
import app.autplay.application.profilepairing.ProfilePairingPort
import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.SelfPairingProof
import app.autplay.data.security.isReservedCredentialProfile
import app.autplay.application.selfpairing.hasSelfDevicePairingPendingRecipient
import app.autplay.application.selfpairing.integer
import app.autplay.application.selfpairing.text
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.domain.ServerProfileId
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

sealed interface AccountRecoveryRecipientState {
    data object Idle : AccountRecoveryRecipientState
    data object Working : AccountRecoveryRecipientState
    data class ConfirmServer(val identity: SelfPairingIdentity, val accountId: String, val label: String) : AccountRecoveryRecipientState
    data class ConfirmAccount(val preview: AccountRecoveryPreview) : AccountRecoveryRecipientState
    data class Blocked(val code: String, val pending: Boolean, val commitPending: Boolean) : AccountRecoveryRecipientState
    data object Connected : AccountRecoveryRecipientState
}

data class AccountRecoveryBindingIntent(val identity: SelfPairingIdentity, val generationId: String,
    val bindingCommitId: String, val accountId: String, val keyAlias: String, val keyThumbprint: String)

interface AccountRecoveryBindingCommitter {
    suspend fun isDurable(intent: AccountRecoveryBindingIntent): Boolean
    suspend fun commit(intent: AccountRecoveryBindingIntent, result: AccountRecoveryResult, refresh: ByteArray, identitySpki: ByteArray): Boolean
}

/** No uncertain commit can be discarded; resume first recognizes an already durable binding. */
class AccountRecoveryRecipientRuntime(
    private val credentials: CredentialStore, private val keys: M5DeviceKeyStore,
    private val transport: AccountRecoveryTransport, private val discovery: ProfilePairingPort,
    private val binding: AccountRecoveryBindingCommitter, private val activeProfile: ActiveProfileGate,
    private val firstBind: FirstBindCeremonyGate, private val deviceName: String, private val appVersion: String,
    private val now: () -> Instant = Instant::now, private val allowDevelopmentHttp: Boolean = false,
    private val purpose: AccountRestorationPurpose = AccountRestorationPurpose.RECOVERY,
) {
    private val SLOT = if (purpose == AccountRestorationPurpose.RECOVERY) AccountRecoverySlot.RECIPIENT else AccountRecoverySlot.DELETION_RECIPIENT
    private val OWNER = if (purpose == AccountRestorationPurpose.RECOVERY) FirstBindCeremonyOwner.ACCOUNT_RECOVERY else FirstBindCeremonyOwner.ACCOUNT_DELETE_CANCEL
    private val store = AccountRecoveryPendingStore(credentials)
    private val mutex = Mutex()
    private val mutable = MutableStateFlow<AccountRecoveryRecipientState>(AccountRecoveryRecipientState.Idle)
    val state: StateFlow<AccountRecoveryRecipientState> = mutable
    private var proposal: AccountRecoveryDocument? = null

    /** Import only parses locally: a file cannot initiate contact with its origin. */
    suspend fun importDocument(raw: ByteArray) = operation {
        reserveNew()
        val document = AccountRecoveryDocument.parse(raw, allowDevelopmentHttp)
        try { requireUsableIdentity(document.identity) }
        catch (failure: Exception) { document.close(); throw failure }
        proposal?.close(); proposal = document
        mutable.value = AccountRecoveryRecipientState.ConfirmServer(document.identity, document.accountId, document.label)
    }

    /** The UI explicitly asks to inspect the user-entered server, without sending the code. */
    suspend fun inspectManual(origin: String, accountId: String, codeText: String) = operation {
        reserveNew()
        requireCanonicalUuid(accountId)
        val code = AccountRecoveryProof.normalize(codeText)
        try {
            val api = OriginNormalizer.normalize(origin, allowDevelopmentHttp)
            mutable.value = AccountRecoveryRecipientState.Working
            val found = discovery.discovery(api)
            require(found is PairingNetworkResult.Success) { "recovery_discovery_required" }
            val doc = found.value
            try {
                require(doc.apiOrigin == api && doc.expiresAt.isAfter(now()))
                val identity = SelfPairingIdentity(doc.identity.serverInstanceId, doc.identity.identityEpoch,
                    doc.identity.identityThumbprintSha256, doc.apiOrigin, doc.streamOrigin)
                requireUsableIdentity(identity)
                require(SelfPairingProof.hash(doc.identityPublicKeySpki) == identity.thumbprint)
                proposal?.close(); proposal = AccountRecoveryDocument(identity, accountId, accountId, code.copyOf())
                mutable.value = AccountRecoveryRecipientState.ConfirmServer(identity, accountId, doc.labelHint)
            } finally { doc.identityPublicKeySpki.fill(0) }
        } finally { code.fill(0) }
    }

    suspend fun confirmServer() = operation {
        val current = requireNotNull(proposal) { "recovery_server_confirmation_required" }
        require(firstBind.isReservedBy(OWNER)); requireUnbound()
        require(store.read(SLOT) == null) { "recovery_resume_required" }
        mutable.value = AccountRecoveryRecipientState.Working
        val found = discovery.discovery(current.identity.apiOrigin)
        require(found is PairingNetworkResult.Success) { "recovery_discovery_required" }
        val doc = found.value
        try {
            require(doc.identity.serverInstanceId == current.identity.serverInstanceId &&
                doc.identity.identityEpoch == current.identity.epoch && doc.identity.identityThumbprintSha256 == current.identity.thumbprint &&
                doc.apiOrigin == current.identity.apiOrigin && doc.streamOrigin == current.identity.streamOrigin && doc.expiresAt.isAfter(now())) { "recovery_identity_changed" }
            require(SelfPairingProof.hash(doc.identityPublicKeySpki) == current.identity.thumbprint)
            val generation = UUID.randomUUID().toString()
            val alias = "autplay.account.recovery.$generation"
            keys.ensure(alias)
            val pending = JsonObject(current.identity.fields() + mapOf(
                "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("PREVIEW_PENDING"),
                "account_id" to JsonPrimitive(current.accountId), "old_code" to JsonPrimitive(current.code.toString(Charsets.US_ASCII)),
                "generation_id" to JsonPrimitive(generation), "key_alias" to JsonPrimitive(alias),
                "key_thumbprint" to JsonPrimitive(keys.publicKeyThumbprintSha256(alias)),
                "identity_spki_b64" to JsonPrimitive(b64(doc.identityPublicKeySpki)),
            ))
            store.write(SLOT, pending)
            proposal = null; current.close()
            preview(pending)
        } finally { doc.identityPublicKeySpki.fill(0) }
    }

    suspend fun resume() = operation {
        val pending = store.read(SLOT) ?: return@operation
        require(firstBind.reserve(OWNER)) { "FIRST_BIND_CEREMONY_BUSY" }
        validatePending(pending)
        if (pending.text("stage") == "COMMIT_PENDING" && finishIfDurable(pending)) return@operation
        requireUnbound()
        if (pending.text("stage") == "COMMIT_PENDING") commit(pending) else preview(pending)
    }

    suspend fun confirmAccount(accountId: String) = operation {
        val visible = mutable.value as? AccountRecoveryRecipientState.ConfirmAccount ?: error("recovery_confirmation_required")
        require(accountId == visible.preview.accountId && firstBind.isReservedBy(OWNER))
        requireUnbound()
        val pending = requireNotNull(store.read(SLOT))
        validatePending(pending)
        require(pending.text("stage") == "PREVIEW_PENDING" && pending.text("account_id") == accountId)
        val identity = identity(pending)
        val nextCode = AccountRecoveryProof.code()
        val refresh = SelfPairingProof.secret()
        try {
            val deletion = visible.preview.deletion
            require((purpose == AccountRestorationPurpose.DELETE_CANCEL) == (deletion != null))
            val deletionFields = deletion?.let { mapOf("deletion_request_id" to JsonPrimitive(it.requestId),
                "expected_revision" to JsonPrimitive(it.revision)) } ?: emptyMap()
            val request = signedRequest("recover", identity, accountId,
                AccountRecoveryProof.device(keys, pending.text("key_alias"), deviceName, appVersion) + mapOf(
                    "expected_code_generation" to JsonPrimitive(visible.preview.generation),
                    "next_code_verifier_sha256" to JsonPrimitive(AccountRecoveryProof.verifier(identity, accountId, nextCode)),
                    "next_refresh_token_sha256" to JsonPrimitive(SelfPairingProof.hash(refresh)),
                    "binding_commit_id" to JsonPrimitive(UUID.randomUUID().toString()), "confirmed_account_id" to JsonPrimitive(accountId),
                ) + deletionFields, pending.text("key_alias"))
            val file = AccountRecoveryDocument(identity, accountId, visible.preview.label, nextCode.copyOf()).use { it.encode() }
            try {
                val updated = JsonObject(pending + mapOf("stage" to JsonPrimitive("COMMIT_PENDING"),
                    "request_b64" to JsonPrimitive(b64(request)), "next_refresh" to JsonPrimitive(refresh.toString(Charsets.US_ASCII)),
                    "next_document_b64" to JsonPrimitive(b64(file))))
                store.write(SLOT, updated)
                commit(updated)
            } finally { request.fill(0); file.fill(0) }
        } finally { nextCode.fill(0); refresh.fill(0) }
    }

    /** Preview is read-only. An uncertain commit remains resumable even after receipt expiry. */
    suspend fun cancelBeforeCommit() = operation {
        val pending = store.read(SLOT)
        require(pending == null || pending.text("stage") == "PREVIEW_PENDING") { "recovery_resume_required" }
        store.clear(SLOT)
        pending?.let { runCatching { keys.delete(it.text("key_alias")) } }
        proposal?.close(); proposal = null
        firstBind.release(OWNER)
        mutable.value = AccountRecoveryRecipientState.Idle
    }

    private suspend fun preview(pending: JsonObject) {
        mutable.value = AccountRecoveryRecipientState.Working
        val request = signedRequest("preview", identity(pending), pending.text("account_id"),
            AccountRecoveryProof.device(keys, pending.text("key_alias"), deviceName, appVersion), pending.text("key_alias"))
        val code = AccountRecoveryProof.normalize(pending.text("old_code"))
        try {
            val result = transport.recipient(identity(pending), "preview", code, request)
            val parsed = if (purpose == AccountRestorationPurpose.RECOVERY) AccountRecoveryPreview.parse(result, SelfPairingJson.parse(request))
                else AccountDeletionReceipt.preview(result, SelfPairingJson.parse(request), now())
            mutable.value = AccountRecoveryRecipientState.ConfirmAccount(parsed)
        } finally { request.fill(0); code.fill(0) }
    }
    private suspend fun commit(pending: JsonObject) {
        if (finishIfDurable(pending)) return
        mutable.value = AccountRecoveryRecipientState.Working
        val request = Base64.getDecoder().decode(pending.text("request_b64"))
        val code = AccountRecoveryProof.normalize(pending.text("old_code"))
        val refresh = pending.text("next_refresh").toByteArray(Charsets.US_ASCII)
        val spki = Base64.getDecoder().decode(pending.text("identity_spki_b64"))
        try {
            val currentIdentity = identity(pending)
            val value = try {
                transport.recipient(currentIdentity, "recover", code, request)
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
                try {
                    transport.outcome(currentIdentity, refresh, request)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    throw failure
                }
            }
            AccountRecoveryResult.parse(value, SelfPairingJson.parse(request), now()).use { result ->
                require(binding.commit(intent(pending), result, refresh, spki)) { "recovery_binding_pending" }
                require(finishIfDurable(pending)) { "recovery_binding_pending" }
            }
        } finally { request.fill(0); code.fill(0); refresh.fill(0); spki.fill(0) }
    }
    private suspend fun finishIfDurable(pending: JsonObject): Boolean = AccountRecoveryJournalGate.serialized {
        if (!binding.isDurable(intent(pending))) return@serialized false
        val request = decodedRequest(pending)
        val existing = store.read(AccountRecoverySlot.SOURCE)
        val newer = existing != null && existing.text("stage") == "SAVED" &&
            existing.text("account_id") == pending.text("account_id") && identity(existing) == identity(pending) &&
            existing.integer("code_generation") > request.integer("expected_code_generation") + 1
        if (!newer) store.write(AccountRecoverySlot.SOURCE, JsonObject(identity(pending).fields() + mapOf(
            "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("SAVED"),
            "account_id" to pending.getValue("account_id"), "document_b64" to pending.getValue("next_document_b64"),
            "code_generation" to JsonPrimitive(request.integer("expected_code_generation") + 1))))
        if (purpose == AccountRestorationPurpose.DELETE_CANCEL) {
            val source = store.read(AccountRecoverySlot.DELETION_SOURCE)
            if (source != null && source.text("stage") == "RECORDED" && source.text("account_id") == pending.text("account_id") &&
                identity(source) == identity(pending) && AccountDeletionReceipt.fromJournal(source).text("deletion_request_id") == request.text("deletion_request_id")) {
                store.clear(AccountRecoverySlot.DELETION_SOURCE)
            }
        }
        store.clear(SLOT)
        firstBind.release(OWNER)
        mutable.value = AccountRecoveryRecipientState.Connected
        true
    }
    private suspend fun reserveNew() {
        require(firstBind.reserve(OWNER)) { "FIRST_BIND_CEREMONY_BUSY" }
        require(store.read(SLOT) == null) { "recovery_resume_required" }
        requireUnbound()
    }
    private suspend fun requireUnbound() {
        val other = if (SLOT == AccountRecoverySlot.RECIPIENT) AccountRecoverySlot.DELETION_RECIPIENT else AccountRecoverySlot.RECIPIENT
        require(!activeProfile.hasActiveProfile() && !credentials.hasPublicAccessPendingRegistration() &&
            !credentials.hasSelfDevicePairingPendingRecipient() && store.read(other) == null &&
            !credentials.hasUnresolvedAccountDeletion()) { "recovery_active_profile_forbidden" }
    }
    private fun requireUsableIdentity(identity: SelfPairingIdentity) {
        SelfPairingIdentity.parse(JsonObject(identity.fields()), allowDevelopmentHttp)
        require(!ServerProfileId(identity.serverInstanceId).isReservedCredentialProfile()) { "RESERVED_CREDENTIAL_PROFILE" }
    }
    private fun validatePending(pending: JsonObject) {
        require(pending.integer("schema_version") == 1L && pending.text("stage") in setOf("PREVIEW_PENDING", "COMMIT_PENDING"))
        requireUsableIdentity(identity(pending)); requireCanonicalUuid(pending.text("account_id"))
        require(keys.publicKeyThumbprintSha256(pending.text("key_alias")) == pending.text("key_thumbprint"))
        val spki = Base64.getDecoder().decode(pending.text("identity_spki_b64"))
        try { require(SelfPairingProof.hash(spki) == identity(pending).thumbprint) } finally { spki.fill(0) }
        if (pending.text("stage") == "COMMIT_PENDING") {
            val request = decodedRequest(pending)
            require((purpose == AccountRestorationPurpose.DELETE_CANCEL) == request.containsKey("deletion_request_id"))
            require(SelfPairingIdentity.parse(request, allowDevelopmentHttp) == identity(pending) && request.text("confirmed_account_id") == pending.text("account_id"))
            require(request.text("device_key_thumbprint_sha256") == pending.text("key_thumbprint"))
            require(request.text("device_public_key_spki_b64") == b64(keys.publicKeySpki(pending.text("key_alias"))))
            val refresh = pending.text("next_refresh").toByteArray(Charsets.US_ASCII)
            try { SelfPairingProof.requireSecret(refresh); require(SelfPairingProof.hash(refresh) == request.text("next_refresh_token_sha256")) }
            finally { refresh.fill(0) }
            val file = Base64.getDecoder().decode(pending.text("next_document_b64"))
            try { AccountRecoveryDocument.parse(file, allowDevelopmentHttp).use {
                require(it.identity == identity(pending) && it.accountId == pending.text("account_id"))
                require(AccountRecoveryProof.verifier(it.identity, it.accountId, it.code) == request.text("next_code_verifier_sha256"))
            } } finally { file.fill(0) }
        }
    }
    private fun intent(pending: JsonObject): AccountRecoveryBindingIntent = AccountRecoveryBindingIntent(identity(pending),
        pending.text("generation_id"), decodedRequest(pending).text("binding_commit_id"), pending.text("account_id"), pending.text("key_alias"), pending.text("key_thumbprint"))
    private fun identity(pending: JsonObject) = SelfPairingIdentity.parse(pending, allowDevelopmentHttp)
    private fun decodedRequest(pending: JsonObject): JsonObject {
        val raw = Base64.getDecoder().decode(pending.text("request_b64"))
        return try { SelfPairingJson.parse(raw) } finally { raw.fill(0) }
    }
    private fun signedRequest(kind: String, identity: SelfPairingIdentity, accountId: String,
        fields: Map<String, kotlinx.serialization.json.JsonElement>, alias: String): ByteArray =
        if (purpose == AccountRestorationPurpose.RECOVERY) AccountRecoveryProof.request(kind, identity, accountId, fields, now(), keys, alias)
        else AccountDeletionProof.request(if (kind == "recover") "cancel" else kind, identity, accountId, fields, now(), keys, alias)
    private suspend fun operation(block: suspend () -> Unit) = mutex.withLock {
        try { block() } catch (failure: CancellationException) { throw failure }
        catch (failure: Exception) {
            val pending = runCatching { store.read(SLOT) }.getOrElse {
                mutable.value = AccountRecoveryRecipientState.Blocked("account_recovery_unavailable", true, true)
                return@withLock
            }
            if (pending == null && proposal == null) firstBind.release(OWNER)
            mutable.value = AccountRecoveryRecipientState.Blocked((failure as? AccountRecoveryRemoteFailure)?.code
                ?: "account_recovery_unavailable", pending != null, pending?.text("stage") == "COMMIT_PENDING")
        }
    }
    private companion object {
        fun b64(value: ByteArray): String = Base64.getEncoder().encodeToString(value)
    }
}
