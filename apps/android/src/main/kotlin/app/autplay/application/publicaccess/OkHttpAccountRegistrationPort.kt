package app.autplay.application.publicaccess

import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.profilepairing.requireCanonicalUuid
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long

/** Separate unauthenticated PA2 route. Never attaches existing profile credentials. */
class OkHttpAccountRegistrationPort(
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).followRedirects(false).followSslRedirects(false).build(),
    private val allowUnsafeDevelopmentHttp: Boolean = false,
) : AccountRegistrationPort {
    private val client = client

    override suspend fun redeem(request: SignedAccountRegistrationRequest): Result<AccountRegistrationResponse> = withContext(Dispatchers.IO) {
        runCatching {
            val origin = OriginNormalizer.normalize(
                request.registration.invitation.apiOrigin,
                allowUnsafeDevelopmentHttp,
            )
            val body = request.canonicalJson.toRequestBody(JSON)
            val http = Request.Builder()
                .url(origin.trimEnd('/') + "/api/v1/public-access/account-invitations/redeem")
                .header("Accept", "application/json")
                .header("Cache-Control", "no-store")
                .header("Pragma", "no-cache")
                .post(body)
                .build()
            client.newCall(http).execute().use { response ->
                require(response.header("Cache-Control").orEmpty().contains("no-store")) { "ACCOUNT_REGISTRATION_CACHE_POLICY" }
                require(response.header("Pragma").orEmpty().contains("no-cache")) { "ACCOUNT_REGISTRATION_CACHE_POLICY" }
                val raw = response.body.bytes()
                try {
                    require(raw.size <= MAX_RESPONSE_BYTES) { "ACCOUNT_REGISTRATION_RESPONSE_TOO_LARGE" }
                    require(response.isSuccessful) { "ACCOUNT_REGISTRATION_REJECTED" }
                    val root = Json.parseToJsonElement(raw.toString(StandardCharsets.UTF_8)).jsonObject
                    require(root.keys == RESPONSE_MEMBERS) { "ACCOUNT_REGISTRATION_RESPONSE_MEMBERS" }
                    require(root["contract_version"]?.jsonPrimitive?.content == "v1")
                    require(root["schema_version"]?.jsonPrimitive?.int == 1)
                    require(root["registration_id"]?.jsonPrimitive?.content == request.registration.registrationId)
                    require(root["binding_commit_id"]?.jsonPrimitive?.content == request.registration.bindingCommitId)
                    require(root["server_instance_id"]?.jsonPrimitive?.content == request.registration.invitation.serverInstanceId)
                    requireCanonicalUuid(root["user_id"]?.jsonPrimitive?.content ?: error("missing user"))
                    require(root["account_display_name"]?.jsonPrimitive?.content == request.registration.invitation.accountDisplayName)
                    require(root["account_role"]?.jsonPrimitive?.content == "USER")
                    requireCanonicalUuid(root["device_id"]?.jsonPrimitive?.content ?: error("missing device"))
                    requireCanonicalUuid(root["session_id"]?.jsonPrimitive?.content ?: error("missing session"))
                    require(root["refresh_generation"]?.jsonPrimitive?.long == 0L)
                    val now = Instant.now()
                    val refreshExpires = Instant.parse(root["refresh_absolute_expires_at"]?.jsonPrimitive?.content ?: error("missing refresh expiry"))
                    val receiptExpires = Instant.parse(root["receipt_expires_at"]?.jsonPrimitive?.content ?: error("missing receipt expiry"))
                    val accessExpires = Instant.parse(root["access_expires_at"]?.jsonPrimitive?.content ?: error("missing access expiry"))
                    require(refreshExpires.isAfter(now) && receiptExpires.isAfter(now) && accessExpires.isAfter(now)) { "ACCOUNT_REGISTRATION_RESPONSE_EXPIRED" }
                    require(receiptExpires == refreshExpires.plusSeconds(300)) { "ACCOUNT_REGISTRATION_RECEIPT_EXPIRY_INVALID" }
                    root["replayed"]?.jsonPrimitive?.boolean ?: error("missing replay marker")
                    val access = root["access_token"]?.jsonPrimitive?.content ?: error("missing token")
                    require(access.length in 32..8192)
                    AccountRegistrationResponse(
                        root["registration_id"]!!.jsonPrimitive.content,
                        root["binding_commit_id"]!!.jsonPrimitive.content,
                        root["server_instance_id"]!!.jsonPrimitive.content,
                        root["user_id"]!!.jsonPrimitive.content,
                        root["account_display_name"]!!.jsonPrimitive.content,
                        root["device_id"]!!.jsonPrimitive.content,
                        root["session_id"]!!.jsonPrimitive.content,
                        refreshExpires.toEpochMilli(), receiptExpires.toEpochMilli(), accessExpires.toEpochMilli(),
                        access.toByteArray(StandardCharsets.UTF_8),
                    )
                } finally { raw.fill(0) }
            }
        }
    }

    private companion object {
        val JSON = "application/json".toMediaType()
        const val MAX_RESPONSE_BYTES = 16 * 1024
        val RESPONSE_MEMBERS = setOf("contract_version", "schema_version", "registration_id", "binding_commit_id", "server_instance_id", "user_id", "account_display_name", "account_role", "device_id", "session_id", "refresh_generation", "refresh_absolute_expires_at", "receipt_expires_at", "access_token", "access_expires_at", "replayed")
    }
}
