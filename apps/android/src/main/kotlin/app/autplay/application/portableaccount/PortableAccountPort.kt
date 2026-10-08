package app.autplay.application.portableaccount

import app.autplay.data.network.readCancellable
import app.autplay.data.network.withAutPlayRedirectPolicy
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.domain.ServerProfileId
import java.nio.ByteBuffer
import java.time.Duration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.erdtman.jcs.JsonCanonicalizer

class PortableAccountServerException(val status: Int, val code: String) : IllegalArgumentException(code)

/** Bounded transport. Trust and account correspondence are checked by the caller before signing. */
class PortableAccountPort(
    private val apiBaseUrl: String,
    credentials: CredentialStore,
    rotation: M5SessionRotationClient? = null,
    client: OkHttpClient = OkHttpClient(),
) {
    private val client = client.newBuilder().cache(null).callTimeout(Duration.ofSeconds(30)).build().withAutPlayRedirectPolicy()
    private val sessions = RefreshingSessionCredentials(apiBaseUrl, credentials, this.client, m5Rotation = rotation)

    suspend fun request(path: String, body: JsonObject?, profile: ServerProfileId? = null): JsonObject = withContext(Dispatchers.IO) {
        require(path.startsWith("/portable-accounts/") && path.length < 150)
        if (profile == null) return@withContext fetch(path, body, null).let { (code, result) -> checked(code, result) }
        var access = sessions.access(profile)
        try {
            var response = fetch(path, body, access.token)
            if (response.first == 401) {
                val generation = access.generation
                access.close()
                access = sessions.refreshAfterRejection(profile, generation)
                response = fetch(path, body, access.token)
            }
            checked(response.first, response.second)
        } finally { access.close() }
    }

    private fun checked(code: Int, result: JsonObject): JsonObject {
        if (code !in 200..299) {
            val error = result["error"] as? JsonObject
            val value = (error?.get("code") as? JsonPrimitive)?.content ?: "account_server_unavailable"
            throw PortableAccountServerException(code,
                value.takeIf { Regex("[a-z][a-z0-9_]{1,80}").matches(it) } ?: "account_server_unavailable")
        }
        return result
    }

    private suspend fun fetch(path: String, body: JsonObject?, token: ByteArray?): Pair<Int, JsonObject> {
        val request = Request.Builder().url(apiBaseUrl.trimEnd('/') + path)
            .header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
        token?.let { request.header("Authorization", "Bearer " + it.toString(Charsets.UTF_8)) }
        if (body == null) request.get() else {
            val bytes = canonicalAccountRequest(body)
            require(bytes.size <= 16_384)
            request.post(bytes.toRequestBody("application/json".toMediaType()))
        }
        return client.newCall(request.build()).readCancellable { response ->
            val source = response.body.source()
            require(!source.request(16_385)) { "ACCOUNT_RESPONSE_INVALID" }
            val bytes = source.readByteArray()
            try {
                require(bytes.size in 2..16_384)
                val text = Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString()
                JsonCanonicalizer(text)
                response.code to Json.parseToJsonElement(text).jsonObject
            } finally { bytes.fill(0) }
        }
    }
}

internal fun canonicalAccountRequest(body: JsonObject): ByteArray = JsonCanonicalizer(body.toString()).encodedString.toByteArray(Charsets.UTF_8)
