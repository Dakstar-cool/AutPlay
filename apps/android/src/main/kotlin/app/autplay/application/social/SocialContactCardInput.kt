package app.autplay.application.social

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Bounded input parsing for a preview. The authenticated server still verifies the signed card. */
fun parseSocialContactCardInput(value: String): ContactCard? = runCatching {
    require(value.length in 1..4_096)
    val root = Json.parseToJsonElement(value).jsonObject
    require(root.keys == CONTACT_CARD_FIELDS)
    ContactCard(
        serverInstanceId = root.requiredCardString("server_instance_id"),
        accountId = root.requiredCardString("account_id"),
        displayNameHint = root.requiredCardString("display_name_hint"),
        issuedAt = root.requiredCardString("issued_at"),
        expiresAt = root.requiredCardString("expires_at"),
        signatureB64Url = root.requiredCardString("signature_b64url"),
    )
}.getOrNull()

private fun JsonObject.requiredCardString(name: String): String {
    val value = requireNotNull(this[name]).jsonPrimitive
    require(value.isString)
    return value.content
}

private val CONTACT_CARD_FIELDS = setOf(
    "server_instance_id", "account_id", "display_name_hint",
    "issued_at", "expires_at", "signature_b64url",
)
