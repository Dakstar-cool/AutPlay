package app.autplay.application.recommendation

import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.SessionAccess
import app.autplay.data.network.withAutPlayRedirectPolicy
import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets
import java.time.Duration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Authenticated bounded P11 transport. It never logs or persists bearer/response material. */
class OkHttpRecommendationPackTransport(
    private val baseUrl: String,
    private val credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(30)).build(),
    private val m5Rotation: M5SessionRotationClient? = null,
) : RecommendationPackTransport {
    private val client = client.withAutPlayRedirectPolicy()
    private val sessionCredentials = RefreshingSessionCredentials(baseUrl, credentials, client, m5Rotation = m5Rotation)

    override suspend fun fetch(
        binding: ClientEventBinding,
        request: RecommendationPackFetchRequest,
    ): DownloadedRecommendationPack = withContext(Dispatchers.IO) {
        var access = sessionCredentials.access(binding.serverProfileId)
        try {
            val body = buildJsonObject {
                put("context", request.context)
                put("exploration", request.exploration)
                put("limit", request.limit)
                put("pipeline_key", request.pipelineKey)
                put("pipeline_version", request.pipelineVersion?.let(::JsonPrimitive) ?: JsonNull)
                put("seed", request.seed)
                put("shadow", request.shadow)
                put("ttl_days", request.ttlDays)
            }.toString()
            var result = executeOnce(body, access)
            if (result.first == 401) {
                val rejectedGeneration = access.generation
                access.close()
                access = sessionCredentials.refreshAfterRejection(binding.serverProfileId, rejectedGeneration)
                result = executeOnce(body, access)
            }
            if (result.first !in 200..299) error("RECOMMENDATION_PACK_HTTP_${result.first}")
            decodeResponse(result.second)
        } finally {
            access.close()
        }
    }

    private fun executeOnce(body: String, access: SessionAccess): Pair<Int, String> {
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + "/recommendation-packs")
            .header("Authorization", "Bearer ${access.token.toString(StandardCharsets.UTF_8)}")
            .header("Cache-Control", "no-store")
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        return client.newCall(request).execute().use { response ->
            response.code to response.body.readBoundedUtf8(MAX_RESPONSE_BYTES)
        }
    }

    private fun decodeResponse(text: String): DownloadedRecommendationPack {
        val root = try {
            Json.parseToJsonElement(text) as? JsonObject
        } catch (_: Exception) {
            null
        } ?: error("RECOMMENDATION_PACK_RESPONSE_INVALID")
        return DownloadedRecommendationPack(
            offlinePackId = root.requiredString("offline_pack_id", 36),
            recommendationRequestId = root.requiredString("recommendation_request_id", 36),
            payloadVersion = root.requiredInt("payload_version", 1, 1_000),
            payloadEncoding = root.requiredString("payload_encoding", 32),
            payloadBase64 = root.requiredString("payload_base64", MAX_BASE64_CHARS),
            payloadSha256 = root.requiredString("payload_sha256", 64),
            createdAtMs = root.requiredLong("created_at_ms"),
            expiresAtMs = root.requiredLong("expires_at_ms"),
        )
    }

    private fun JsonObject.requiredString(name: String, maxLength: Int): String {
        val primitive = this[name] as? JsonPrimitive
        if (primitive == null || !primitive.isString || primitive.content.isEmpty() || primitive.content.length > maxLength) {
            error("RECOMMENDATION_PACK_RESPONSE_INVALID")
        }
        return primitive.content
    }

    private fun JsonObject.requiredLong(name: String): Long {
        val primitive = this[name] as? JsonPrimitive
        val value = primitive?.takeUnless(JsonPrimitive::isString)?.longOrNull
            ?: error("RECOMMENDATION_PACK_RESPONSE_INVALID")
        if (value < 0) error("RECOMMENDATION_PACK_RESPONSE_INVALID")
        return value
    }

    private fun JsonObject.requiredInt(name: String, minimum: Int, maximum: Int): Int {
        val primitive = this[name] as? JsonPrimitive
        val value = primitive?.takeUnless(JsonPrimitive::isString)?.intOrNull
            ?: error("RECOMMENDATION_PACK_RESPONSE_INVALID")
        if (value !in minimum..maximum) error("RECOMMENDATION_PACK_RESPONSE_INVALID")
        return value
    }

    private fun okhttp3.ResponseBody.readBoundedUtf8(maxBytes: Int): String {
        val length = contentLength()
        if (length > maxBytes) error("RECOMMENDATION_PACK_RESPONSE_TOO_LARGE")
        val output = ByteArrayOutputStream(minOf(maxBytes, if (length > 0) length.toInt() else 8_192))
        byteStream().use { input ->
            val buffer = ByteArray(8_192)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                if (total > maxBytes) error("RECOMMENDATION_PACK_RESPONSE_TOO_LARGE")
                output.write(buffer, 0, count)
            }
        }
        return output.toByteArray().toString(StandardCharsets.UTF_8)
    }

    private companion object {
        const val MAX_BASE64_CHARS = 699_052
        const val MAX_RESPONSE_BYTES = 704_000
    }
}
