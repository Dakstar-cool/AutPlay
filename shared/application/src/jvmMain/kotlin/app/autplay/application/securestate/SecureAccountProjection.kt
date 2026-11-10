package app.autplay.application.securestate

import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

/** J2/K2 comparison witness, charged to the existing owner header and full K value budgets. */
public object SecureAccountProjectionCodec {
    public fun encode(witness: AccountProtectedWitness): JsonObject {
        val plan = witness.preparation
        val value = buildJsonObject {
            put("format", 1); put("store_id", witness.storeId.value); put("scope_id", plan.scope.scopeId.value)
            put("portable_account_id", plan.scope.portableAccountId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("remote", plan.scope.remote?.let { remote -> buildJsonObject {
                put("server_instance_id", remote.serverInstanceId.value); put("user_id", remote.userId.value)
            } } ?: JsonNull)
            put("binding", plan.binding?.let { binding -> buildJsonObject {
                put("device_id", binding.deviceId.value); put("journal_epoch", binding.journalEpoch.value)
                put("identity_epoch", binding.identityEpoch); put("server_thumbprint", binding.serverThumbprint)
                put("device_key_thumbprint", binding.deviceKeyThumbprint)
            } } ?: JsonNull)
            put("profile_id", plan.profileId.value); put("binding_commit", plan.bindingCommit.value)
            put("activation_id", plan.activationId.value); put("intent_id", plan.intentId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("authority_revision", plan.expectedAuthorityRevision); put("prior_activation_revision", plan.expectedActivationRevision)
            put("current_authority_revision", witness.currentAuthorityRevision)
            put("stage", witness.stage.name); put("session_family", witness.sessionFamily?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("session_generation", witness.sessionGeneration)
            put("capability_revision", witness.capabilityRevision?.let(::JsonPrimitive) ?: JsonNull)
            put("lifecycle_allowed", witness.lifecycleAllowed); put("new_device_lineage", witness.permitsNewDeviceLineage)
        }
        val bytes = SecureJson.encode(value)
        try { secureRequire(bytes.size <= SecureStateLimits.HEADER) } finally { bytes.fill(0) }
        return value
    }

    public fun decode(value: JsonObject): AccountProtectedWitness {
        value.exact("format", "store_id", "scope_id", "portable_account_id", "remote", "binding", "profile_id",
            "binding_commit", "activation_id", "intent_id", "authority_revision", "prior_activation_revision", "stage",
            "session_family", "session_generation", "capability_revision", "lifecycle_allowed", "new_device_lineage", "current_authority_revision")
        secureRequire(value.number("format") == 1L)
        val remote = value.getValue("remote").takeUnless { it == JsonNull }?.let {
            val obj = it as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            obj.exact("server_instance_id", "user_id")
            AccountRemoteOwner(AccountUuid(obj.string("server_instance_id")), AccountUuid(obj.string("user_id")))
        }
        val binding = value.getValue("binding").takeUnless { it == JsonNull }?.let {
            val obj = it as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            obj.exact("device_id", "journal_epoch", "identity_epoch", "server_thumbprint", "device_key_thumbprint")
            AccountRemoteBinding(AccountUuid(obj.string("device_id")), AccountUuid(obj.string("journal_epoch")),
                obj.number("identity_epoch"), obj.string("server_thumbprint"), obj.string("device_key_thumbprint"))
        }
        val plan = AccountScopePreparation(AccountUuid(value.string("binding_commit")), AccountScopeIdentity(
            AccountUuid(value.string("scope_id")), value.nullableString("portable_account_id")?.let(::AccountUuid), remote),
            AccountUuid(value.string("profile_id")), binding, value.number("authority_revision"), value.number("prior_activation_revision"),
            AccountUuid(value.string("activation_id")), value.nullableString("intent_id")?.let(::AccountUuid))
        return AccountProtectedWitness(AccountUuid(value.string("store_id")), plan,
            AccountProtectedStage.valueOf(value.string("stage")), value.nullableString("session_family")?.let(::AccountUuid),
            value.number("session_generation"), value.nullableNumber("capability_revision"),
            value.boolean("lifecycle_allowed"), value.boolean("new_device_lineage"), value.number("current_authority_revision")).also { encode(it) }
    }

    public fun requireOwner(witness: AccountProtectedWitness, owner: SecureRecordOwner, payload: SecurePayload): Unit {
        val plan = witness.preparation
        secureRequire(owner.authorityRevision == witness.currentAuthorityRevision && !owner.detached && !owner.deletionVeto,
            SecureStateStatus.CONTEXT_CHANGED)
        when (payload) {
            is PortableRootPayload -> {
                secureRequire(plan.scope.remote == null && plan.binding == null && owner.role == SecureRole.LOCAL &&
                    owner.profile == null && owner.server == null && owner.identityEpoch == null && owner.identityThumbprint == null &&
                    owner.apiOrigin == null && owner.streamOrigin == null && owner.device == null && owner.keyTag == null && owner.keyThumbprint == null &&
                    owner.bindingCommit == null && owner.session == null && owner.family == null && owner.sessionGeneration == null &&
                    owner.expiresAtEpochSeconds == null && owner.stage == SecureStage.RECORDED &&
                    plan.scope.portableAccountId?.value == owner.account && witness.sessionFamily == null &&
                    witness.sessionGeneration == 0L && witness.capabilityRevision == null && !witness.permitsNewDeviceLineage,
                    SecureStateStatus.CONTEXT_CHANGED)
                val passport = payload.passportCopy()
                try { secureRequire(app.autplay.application.portableaccount.PortableAccountDocument.parse(passport).accountId == owner.account) }
                finally { passport.fill(0) }
            }
            is SessionPayload -> {
                owner.requireBinding()
                val remote = plan.scope.remote ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
                val binding = plan.binding ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
                secureRequire(plan.profileId.value == owner.profile && plan.bindingCommit.value == owner.bindingCommit &&
                    remote.serverInstanceId.value == owner.server && remote.userId.value == owner.account &&
                    binding.deviceId.value == owner.device && binding.identityEpoch == owner.identityEpoch &&
                    binding.serverThumbprint == owner.identityThumbprint && binding.deviceKeyThumbprint == owner.keyThumbprint &&
                    witness.sessionFamily?.value == owner.family && witness.sessionGeneration == owner.sessionGeneration &&
                    witness.capabilityRevision == payload.d1.revision &&
                    (plan.scope.portableAccountId == null || plan.scope.portableAccountId.value == owner.account), SecureStateStatus.CONTEXT_CHANGED)
            }
            else -> throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        }
    }
}

public enum class SecureJInstallationStage { RESERVED, READY }
public data class SecureJInstallation(public val storeId: String, public val initialOperationId: String,
    public val stage: SecureJInstallationStage) {
    init { canonicalUuid(storeId); canonicalUuid(initialOperationId) }
    override fun toString(): String = "SecureJInstallation"
}
public object SecureJInstallationCodec {
    public fun encode(value: SecureJInstallation): JsonObject = buildJsonObject {
        put("format", 1); put("schema", 1); put("store_id", value.storeId)
        put("initial_operation_id", value.initialOperationId); put("stage", value.stage.name)
    }.also { val bytes = SecureJson.encode(it); try { secureRequire(bytes.size <= 512) } finally { bytes.fill(0) } }
    public fun decode(value: JsonObject): SecureJInstallation {
        value.exact("format", "schema", "store_id", "initial_operation_id", "stage")
        secureRequire(value.number("format") == 1L && value.number("schema") == 1L)
        return SecureJInstallation(value.string("store_id"), value.string("initial_operation_id"),
            SecureJInstallationStage.valueOf(value.string("stage"))).also { encode(it) }
    }
}
