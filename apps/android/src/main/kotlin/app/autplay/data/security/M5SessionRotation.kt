package app.autplay.data.security

import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Duration
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.erdtman.jcs.JsonCanonicalizer

/** Non-secret local binding evidence needed to route a device-bound M5 rotation. */
data class M5RotationContext(
    val apiOrigin: String,
    val serverInstanceId: String,
    val identityEpoch: Long,
    val deviceId: DeviceId,
    val deviceKeyAlias: String,
)

interface M5RotationContextResolver {
    suspend fun resolve(profileId: ServerProfileId): M5RotationContext?
    suspend fun persistSuccessor(profileId: ServerProfileId, successor: SessionCredentialEnvelope)
}

/** Builds an exact replayable signed request, then performs one bounded rotation call. */
class M5SessionRotationClient(
    private val contexts: M5RotationContextResolver,
    private val keys: M5DeviceKeyStore,
    private val client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).build(),
) {
    suspend fun persistSuccessor(
        profileId: ServerProfileId,
        successor: SessionCredentialEnvelope,
    ) = contexts.persistSuccessor(profileId, successor)

    suspend fun prepare(profileId: ServerProfileId, current: SessionCredentialEnvelope): SessionCredentialEnvelope {
        val context = contexts.resolve(profileId) ?: throw SessionRequiredException()
        val refresh = current.refreshToken ?: throw SessionRequiredException()
        val successor = ByteArray(32).also(java.security.SecureRandom()::nextBytes)
        try {
            val successorToken = b64(successor)
            keys.ensure(context.deviceKeyAlias)
            val fields = linkedMapOf<String, Any>(
                "contract_version" to "v1", "schema_version" to 1,
                "rotation_id" to UUID.randomUUID().toString(),
                "expected_server_instance_id" to context.serverInstanceId,
                "expected_identity_epoch" to context.identityEpoch,
                "device_id" to context.deviceId.value,
                "parent_session_id" to requireNotNull(current.sessionId),
                "current_generation" to requireNotNull(current.sessionGeneration),
                "current_refresh_token" to refresh,
                "next_refresh_token_sha256" to sha256(successorToken.toByteArray(StandardCharsets.US_ASCII)),
            )
            val unsignedFields = fields + mapOf("signature_algorithm" to "ES256-P1363")
            val unsigned = JsonCanonicalizer(json(unsignedFields).toString()).encodedString.toByteArray(StandardCharsets.UTF_8)
            val signed = M5RequestSigner.sign(keys, context.deviceKeyAlias, DOMAIN, unsigned)
            val request = JsonCanonicalizer(json(unsignedFields + mapOf("request_sha256" to signed.requestSha256, "device_signature_b64url" to signed.signatureB64Url)).toString()).encodedString
            return current.copy(refreshPending = true, m5PendingRotationId = fields.getValue("rotation_id") as String, m5PendingRotationRequest = request, m5PendingSuccessorRefreshToken = successorToken)
        } finally { successor.fill(0) }
    }

    suspend fun execute(profileId: ServerProfileId, pending: SessionCredentialEnvelope): SessionCredentialEnvelope = withContext(Dispatchers.IO) {
        val context = contexts.resolve(profileId) ?: throw SessionRequiredException()
        val request = pending.m5PendingRotationRequest ?: throw SessionRequiredException()
        val successor = pending.m5PendingSuccessorRefreshToken ?: throw SessionRequiredException()
        try {
            val response = Request.Builder().url(context.apiOrigin.trimEnd('/') + "/api/v1/account/sessions/rotate")
                .header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
                .post(request.toRequestBody(JSON)).build()
            client.newCall(response).execute().use { http ->
                if (!http.header("Cache-Control").orEmpty().contains("no-store") || !http.header("Pragma").orEmpty().contains("no-cache")) throw SessionRequiredException()
                val source = http.body.source(); if (source.request(MAX_RESPONSE_BYTES + 1L)) throw SessionRequiredException()
                val root = Json.parseToJsonElement(source.readUtf8()).jsonObject
                if (!http.isSuccessful) throw SessionRequiredException()
                require(root.string("contract_version") == "v1" && root.int("schema_version") == 1)
                require(root.string("rotation_id") == pending.m5PendingRotationId)
                require(root.string("parent_session_id") == pending.sessionId)
                require(root.string("family_id") == pending.sessionFamilyId)
                val generation = root.long("generation")
                require(generation == requireNotNull(pending.sessionGeneration) + 1)
                val access = root.string("access_token"); require(access.length in 32..8192)
                pending.copy(accessToken = access, refreshToken = successor, generation = pending.generation + 1, refreshPending = false, sessionId = root.string("session_id"), sessionFamilyId = root.string("family_id"), sessionGeneration = generation, m5PendingRotationId = null, m5PendingRotationRequest = null, m5PendingSuccessorRefreshToken = null)
            }
        } finally { /* String secrets are limited to encrypted envelope/request scope. */ }
    }

    private fun json(fields: Map<String, Any>) = JsonObject(fields.mapValues { (_, value) -> when (value) { is String -> JsonPrimitive(value); is Int -> JsonPrimitive(value); is Long -> JsonPrimitive(value); else -> error("M5_ROTATION_FIELD_INVALID") } })
    private fun JsonObject.string(name: String) = requireNotNull(this[name]).jsonPrimitive.content
    private fun JsonObject.int(name: String) = requireNotNull(this[name]).jsonPrimitive.content.toInt()
    private fun JsonObject.long(name: String) = requireNotNull(this[name]).jsonPrimitive.long
    private fun b64(value: ByteArray) = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(value)
    private fun sha256(value: ByteArray) = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    private companion object { val JSON = "application/json".toMediaType(); const val DOMAIN = "AutPlay session rotation v1\n"; const val MAX_RESPONSE_BYTES = 16L * 1024 }
}
