package app.autplay.application.desktop.account

import app.autplay.application.securestate.*
import kotlinx.serialization.json.*

/** Nonsecret first-bind comparison only. Unknown response identities remain explicit null. */
data class AccountBindingIntent(
    val intentId: AccountUuid,
    val operationId: AccountUuid,
    val pendingSlot: AccountUuid,
    val authorityRevision: Long,
    val priorActivationRevision: Long,
    val serverInstanceId: AccountUuid,
    val identityEpoch: Long,
    val serverThumbprint: String,
    val deviceKeyThumbprint: String,
    val profileId: AccountUuid,
    val portableAccountId: AccountUuid?,
    val requestedJournalEpoch: AccountUuid,
    val knownUserId: AccountUuid?,
    val knownDeviceId: AccountUuid?,
    val wireKind: PendingWireKind,
    val localDataChoice: AccountBindingLocalChoice = AccountBindingLocalChoice.KEEP_LOCAL,
    val selectedChangeIds: List<AccountUuid> = emptyList(),
) {
    init {
        require(authorityRevision in 1..DesktopAccountLimits.MAX_REVISION)
        require(priorActivationRevision in 0 until DesktopAccountLimits.MAX_REVISION)
        require(identityEpoch in 1..DesktopAccountLimits.MAX_REVISION)
        require(serverThumbprint.matches(Regex("[0-9a-f]{64}")))
        require(deviceKeyThumbprint.matches(Regex("[0-9a-f]{64}")))
        require(wireKind in AccountBindingIntentCodec.GRANTS)
        require(selectedChangeIds.size <= 100 && selectedChangeIds.distinct().size == selectedChangeIds.size)
        require((localDataChoice == AccountBindingLocalChoice.KEEP_LOCAL) == selectedChangeIds.isEmpty())
    }
    override fun toString(): String = "AccountBindingIntent"
}

enum class AccountBindingLocalChoice { KEEP_LOCAL, REVIEW_SELECTED }

/** Closed immutable document; no extensible command, response, origin, proof or credential field. */
object AccountBindingIntentCodec {
    val GRANTS: Set<PendingWireKind> = setOf(PendingWireKind.ENROLLMENT_EXCHANGE,
        PendingWireKind.PORTABLE_REGISTER, PendingWireKind.PORTABLE_LOGIN, PendingWireKind.TRUSTED_EXCHANGE,
        PendingWireKind.ADMISSION_EXCHANGE, PendingWireKind.SELF_EXCHANGE, PendingWireKind.RECOVERY_COMMIT,
        PendingWireKind.DELETION_CANCEL)

    fun encode(value: AccountBindingIntent): ByteArray = SecureJson.encode(buildJsonObject {
        put("format", 1); put("intent_id", value.intentId.value); put("operation_id", value.operationId.value)
        put("pending_slot", value.pendingSlot.value); put("authority_revision", value.authorityRevision)
        put("prior_activation_revision", value.priorActivationRevision)
        put("server_instance_id", value.serverInstanceId.value); put("identity_epoch", value.identityEpoch)
        put("server_thumbprint", value.serverThumbprint); put("device_key_thumbprint", value.deviceKeyThumbprint)
        put("profile_id", value.profileId.value)
        put("portable_account_id", value.portableAccountId?.value?.let(::JsonPrimitive) ?: JsonNull)
        put("requested_journal_epoch", value.requestedJournalEpoch.value)
        put("known_user_id", value.knownUserId?.value?.let(::JsonPrimitive) ?: JsonNull)
        put("known_device_id", value.knownDeviceId?.value?.let(::JsonPrimitive) ?: JsonNull)
        put("wire_kind", value.wireKind.name); put("local_data_choice", value.localDataChoice.name)
        put("selected_change_ids", JsonArray(value.selectedChangeIds.map { JsonPrimitive(it.value) }))
    }).also { if (it.size > 4096) throw AccountStoreException(AccountFailure.ACCOUNT_STORAGE_FULL) }

    fun decode(bytes: ByteArray): AccountBindingIntent {
        val value = SecureJson.parse(bytes, 4096)
        value.exact("format", "intent_id", "operation_id", "pending_slot", "authority_revision",
            "prior_activation_revision", "server_instance_id", "identity_epoch", "server_thumbprint",
            "device_key_thumbprint", "profile_id", "portable_account_id", "requested_journal_epoch",
            "known_user_id", "known_device_id", "wire_kind", "local_data_choice", "selected_change_ids")
        require(value.number("format") == 1L)
        return AccountBindingIntent(AccountUuid(value.string("intent_id")), AccountUuid(value.string("operation_id")),
            AccountUuid(value.string("pending_slot")), value.number("authority_revision"), value.number("prior_activation_revision"),
            AccountUuid(value.string("server_instance_id")), value.number("identity_epoch"), value.string("server_thumbprint"),
            value.string("device_key_thumbprint"), AccountUuid(value.string("profile_id")),
            value.nullableString("portable_account_id")?.let(::AccountUuid), AccountUuid(value.string("requested_journal_epoch")),
            value.nullableString("known_user_id")?.let(::AccountUuid), value.nullableString("known_device_id")?.let(::AccountUuid),
            PendingWireKind.valueOf(value.string("wire_kind")), AccountBindingLocalChoice.valueOf(value.string("local_data_choice")),
            java.util.Collections.unmodifiableList(value.array("selected_change_ids").map {
                AccountUuid((it as JsonPrimitive).also { item -> require(item.isString) }.content)
            }))
    }
}
