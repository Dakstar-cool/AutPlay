package app.autplay.application.publicaccess

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.readAccessToken
import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import java.net.URLEncoder
import java.time.Duration
import kotlinx.serialization.builtins.serializer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Authenticated OWNER client; server remains the sole authority for provisioning scope. */
class OkHttpOwnerProvisioningPort(
    private val origin: String,
    private val profileId: ServerProfileId,
    private val credentials: CredentialStore,
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(Duration.ofSeconds(20))
        .followRedirects(false)
        .followSslRedirects(false)
        .build(),
    private val allowUnsafeDevelopmentHttp: Boolean = false,
) : OwnerProvisioningPort {
    override suspend fun create(displayName: String, expiresInSeconds: Int, operationId: String) = callRaw("/public-access/account-invitations", "{\"contract_version\":\"v1\",\"schema_version\":1,\"operation_id\":\"$operationId\",\"account_display_name\":${Json.encodeToString(String.serializer(), displayName)},\"expires_in_seconds\":$expiresInSeconds}") { status, raw -> if (status == 201) AccountInvitationCreateResult.Created(AccountInvitationParser.parseDocument(AccountInvitationParser.MIME_TYPE, raw)) else AccountInvitationCreateResult.Replayed(view(Json.parseToJsonElement(raw.toString(StandardCharsets.UTF_8)).jsonObject)) }
    override suspend fun listInvitations(limit: Int, cursor: String?) = call("/public-access/account-invitations?limit=$limit" + (cursor?.let { "&cursor=${encodeQuery(it)}" } ?: ""), null) { root -> page(root) { view(it) } }
    override suspend fun cancelInvitation(invitationId: String, operationId: String, reasonCode: String) = lifecycle("/public-access/account-invitations/$invitationId/cancel", operationId, reasonCode)
    override suspend fun listAccounts(limit: Int, cursor: String?) = call("/public-access/accounts?limit=$limit" + (cursor?.let { "&cursor=${encodeQuery(it)}" } ?: ""), null) { root -> page(root) { account(it) } }
    override suspend fun disableAccount(userId: String, operationId: String, reasonCode: String) = lifecycle("/public-access/accounts/$userId/disable", operationId, reasonCode)
    private suspend fun lifecycle(path: String, operationId: String, reason: String) = call(path, "{\"contract_version\":\"v1\",\"schema_version\":1,\"operation_id\":\"$operationId\",\"reason_code\":\"$reason\"}") { Unit }
    private suspend fun <T> call(path: String, body: String?, parse: (JsonObject) -> T): Result<T> = callRaw(path, body) { _, raw -> parse(Json.parseToJsonElement(raw.toString(StandardCharsets.UTF_8)).jsonObject) }
    private suspend fun <T> callRaw(path: String, body: String?, parse: (Int, ByteArray) -> T): Result<T> = withContext(Dispatchers.IO) { runCatching {
        val token = credentials.readAccessToken(profileId) ?: error("OWNER_AUTH_REQUIRED")
        try { client.newCall(Request.Builder().url(app.autplay.application.profilepairing.OriginNormalizer.normalize(origin, allowUnsafeDevelopmentHttp).trimEnd('/') + "/api/v1" + path).header("Authorization", "Bearer ${token.toString(StandardCharsets.UTF_8)}").header("Cache-Control", "no-store").header("Pragma", "no-cache").apply { if (body == null) get() else post(body.toRequestBody(JSON)) }.build()).execute().use { response -> require(response.isSuccessful && response.header("Cache-Control").orEmpty().contains("no-store") && response.header("Pragma").orEmpty().contains("no-cache")); val raw = response.body.bytes(); try { require(raw.size <= 16 * 1024); parse(response.code, raw) } finally { raw.fill(0) } } } finally { token.fill(0) }
    } }
    private fun view(root: JsonObject): AccountInvitationView { exact(root, setOf("contract_version","schema_version","invitation_id","account_display_name","account_role","state","issued_at","expires_at"), setOf("terminal_at","invited_user_id")); return AccountInvitationView(s(root,"invitation_id"),s(root,"account_display_name"),s(root,"account_role"),s(root,"state"),s(root,"issued_at"),s(root,"expires_at"),nullable(root,"terminal_at"),nullable(root,"invited_user_id")) }
    private fun account(root: JsonObject): ProvisionedAccountView { exact(root, setOf("user_id","provisioning_invitation_id","display_name","role","status","created_at"), setOf("disabled_at")); return ProvisionedAccountView(s(root,"user_id"),s(root,"provisioning_invitation_id"),s(root,"display_name"),s(root,"role"),s(root,"status"),s(root,"created_at"),nullable(root,"disabled_at")) }
    private fun <T> page(root: JsonObject, parse: (JsonObject) -> T): List<T> { exact(root, setOf("contract_version","schema_version","items","next_cursor")); require(s(root,"contract_version") == "v1" && s(root,"schema_version") == "1"); return root["items"]!!.jsonArray.map { parse(it.jsonObject) }.also { require(it.size <= 100) } }
    private fun exact(root: JsonObject, required: Set<String>, optional: Set<String> = emptySet()) { require(required.all(root::containsKey) && root.keys.all { it in required || it in optional }) }
    private fun s(root: JsonObject, key: String) = root[key]!!.jsonPrimitive.content
    private fun nullable(root: JsonObject, key: String) = root[key]?.jsonPrimitive?.contentOrNull
    @Suppress("DEPRECATION") // String charset overload is required on Android API 26-32.
    private fun encodeQuery(value: String) = URLEncoder.encode(value, StandardCharsets.UTF_8.name())
    private companion object { val JSON = "application/json".toMediaType() }
}
