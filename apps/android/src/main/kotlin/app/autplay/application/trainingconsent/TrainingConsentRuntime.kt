package app.autplay.application.trainingconsent

import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.text
import app.autplay.application.selfpairing.integer
import app.autplay.data.security.BindingAuthorityWriteGate
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Instant
import java.util.UUID

internal const val TRAINING_CONSENT_MAX_REVISION = 9_007_199_254_740_991L

data class TrainingConsentPolicy(val accountId: String, val decision: String, val revision: Long) {
    val granted: Boolean get() = decision == "GRANTED"
}
data class TrainingConsentState(val confirmed: TrainingConsentPolicy? = null,
    val busy: Boolean = false, val pending: Boolean = false, val error: Boolean = false)
interface TrainingConsentPort {
    suspend fun get(): JsonObject
    suspend fun decide(request: JsonObject): JsonObject
}
class TrainingConsentFailure(val code: String) : RuntimeException(code)

/** Account policy is server authority. The encrypted journal contains one exact user intent. */
class TrainingConsentRuntime(
    private val accountId: String,
    private val bindingKey: String,
    private val pendingStore: TrainingConsentJournalStore,
    private val port: TrainingConsentPort,
    private val currentBinding: suspend () -> Boolean,
) {
    private val mutableState = MutableStateFlow(TrainingConsentState())
    val state = mutableState.asStateFlow()
    private var readGeneration = 0L

    suspend fun load() {
        val generation = ++readGeneration
        mutableState.value = mutableState.value.copy(busy = true, error = false)
        try {
            val pending = pendingStore.read()
            if (pending != null) {
                validatePending(pending)
                if (pending.text("binding_key") == bindingKey) {
                    resolve(pending)
                    return
                }
                BindingAuthorityWriteGate.serialized {
                    if (generation == readGeneration && currentBinding() && pendingStore.read() == pending)
                        mutableState.value = mutableState.value.copy(pending = true)
                }
            }
            val result = parsePolicy(port.get())
            BindingAuthorityWriteGate.serialized {
                if (generation == readGeneration && currentBinding() &&
                    pendingStore.read() == pending) {
                    mutableState.value = TrainingConsentState(confirmed = result, pending = pending != null)
                }
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { publishReadFailure(generation) }
    }

    suspend fun choose(decision: String) {
        require(decision in setOf("GRANTED", "DENIED", "WITHDRAWN"))
        var generation = readGeneration
        try {
            val intent = BindingAuthorityWriteGate.serialized {
                require(currentBinding())
                val old = pendingStore.read()
                if (old != null) {
                    validatePending(old)
                    require(decision == "WITHDRAWN")
                }
                val confirmed = mutableState.value.confirmed
                require(confirmed != null || decision == "WITHDRAWN")
                require((confirmed?.revision ?: 0) < TRAINING_CONSENT_MAX_REVISION)
                val request = buildJsonObject {
                    put("schema_version", 1); put("binding_key", bindingKey)
                    put("operation_id", UUID.randomUUID().toString()); put("account_id", accountId)
                    put("expected_revision", confirmed?.revision ?: 0)
                    put("decision", decision); put("policy_version", 1)
                }
                pendingStore.write(request)
                require(pendingStore.read() == request)
                ++readGeneration
                generation = readGeneration
                mutableState.value = mutableState.value.copy(busy = true, pending = true, error = false)
                request
            }
            resolve(intent)
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { publishReadFailure(generation) }
    }

    private suspend fun resolve(pending: JsonObject) {
        validatePending(pending)
        require(pending.text("binding_key") == bindingKey)
        val generation = readGeneration
        val canSend = BindingAuthorityWriteGate.serialized {
            if (generation != readGeneration || !currentBinding() || pendingStore.read() != pending) false
            else {
                mutableState.value = mutableState.value.copy(busy = true, pending = true, error = false)
                true
            }
        }
        if (!canSend) return
        try {
            val request = JsonObject(pending.filterKeys { it !in setOf("schema_version", "binding_key") })
            val response = port.decide(request)
            require(response.text("operation_id") == pending.text("operation_id"))
            val applied = response.integer("applied_revision")
            val appliedDecision = response.text("applied_decision")
            require(applied > 0 && if (pending.text("decision") == "GRANTED") appliedDecision == "GRANTED"
                else appliedDecision in setOf("DENIED", "WITHDRAWN"))
            val policy = parsePolicy(JsonObject(response.filterKeys { it !in setOf("operation_id", "applied_revision", "applied_decision") }))
            require(policy.revision >= applied)
            BindingAuthorityWriteGate.serialized {
                if (currentBinding() && pendingStore.read() == pending) {
                    pendingStore.clear()
                    mutableState.value = TrainingConsentState(confirmed = policy)
                }
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (failure: TrainingConsentFailure) {
            // This purpose-specific response is authoritative nonacceptance: the server checks its
            // exact receipt before checking the current revision, while holding account/op locks.
            if (failure.code == "consent_revision_conflict") {
                BindingAuthorityWriteGate.serialized {
                    if (currentBinding() && pendingStore.read() == pending) {
                        pendingStore.clear()
                        mutableState.value = TrainingConsentState(error = true)
                    }
                }
                return
            }
            publishIntentFailure(pending, generation)
        } catch (_: Exception) {
            publishIntentFailure(pending, generation)
        }
    }

    private suspend fun publishReadFailure(generation: Long) = BindingAuthorityWriteGate.serialized {
        if (generation == readGeneration)
            mutableState.value = mutableState.value.copy(busy = false, error = true)
    }

    private suspend fun publishIntentFailure(pending: JsonObject, generation: Long) = BindingAuthorityWriteGate.serialized {
        if (generation == readGeneration) {
            val matches = try { currentBinding() && pendingStore.read() == pending }
                catch (cancelled: CancellationException) { throw cancelled }
                catch (_: Exception) { false }
            mutableState.value = mutableState.value.copy(busy = false,
                pending = matches || mutableState.value.pending, error = true)
        }
    }

    private fun validatePending(value: JsonObject) {
        require(value.keys == setOf("schema_version", "binding_key", "operation_id", "account_id",
            "expected_revision", "decision", "policy_version"))
        require(value.integer("schema_version") == 1L && value.integer("policy_version") == 1L)
        require(value.text("account_id") == accountId && value.integer("expected_revision") in 0 until TRAINING_CONSENT_MAX_REVISION)
        require(value.text("decision") in setOf("GRANTED", "DENIED", "WITHDRAWN"))
        require(UUID.fromString(value.text("operation_id")).toString() == value.text("operation_id"))
        require(value.text("binding_key").length in 1..1024)
    }

    private fun parsePolicy(value: JsonObject): TrainingConsentPolicy {
        require(value.keys == setOf("schema_version", "account_id", "decision", "revision", "policy_version", "changed_at"))
        require(value.integer("schema_version") == 1L && value.integer("policy_version") == 1L)
        require(value.text("account_id") == accountId)
        val decision = value.text("decision")
        require(decision in setOf("UNKNOWN", "GRANTED", "DENIED", "WITHDRAWN"))
        val revision = value.integer("revision")
        require(revision <= TRAINING_CONSENT_MAX_REVISION)
        require(revision != TRAINING_CONSENT_MAX_REVISION || decision in setOf("DENIED", "WITHDRAWN"))
        require(if (decision == "UNKNOWN") revision == 0L else revision > 0L)
        if (decision == "UNKNOWN") require(value["changed_at"] == kotlinx.serialization.json.JsonNull)
        else Instant.parse(value.text("changed_at"))
        return TrainingConsentPolicy(accountId, decision, revision)
    }
}
