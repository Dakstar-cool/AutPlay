package app.autplay.application.selfpairing

import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.data.network.readCancellable
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.domain.ServerProfileId
import java.time.Duration
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Never includes response bodies, origins, headers or bearer material in exception text. */
class SelfPairingRemoteFailure(val code: String, val retryAfterSeconds: Int? = null) :
    IllegalStateException(code)

interface SelfPairingTransport {
    suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, kind: String, ceremonyId: String, request: ByteArray? = null): JsonObject
    suspend fun recipient(identity: SelfPairingIdentity, kind: String, ceremonyId: String, secret: ByteArray, request: ByteArray): JsonObject
}

/** Every call has bounded I/O; redirects and public-route cookies are disabled. */
class OkHttpSelfPairingTransport(
    private val sourceCredentials: (SelfPairingIdentity, ServerProfileId) -> RefreshingSessionCredentials,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).build(),
    private val allowDevelopmentHttp: Boolean = false,
) : SelfPairingTransport {
    private val client = client.newBuilder().followRedirects(false).followSslRedirects(false)
        .cookieJar(okhttp3.CookieJar.NO_COOKIES).build()

    override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, kind: String, ceremonyId: String, request: ByteArray?): JsonObject {
        require(kind in setOf("start", "status", "decision"))
        val path = when (kind) {
            "start" -> "/account/device-pairings"
            "status" -> "/account/device-pairings/$ceremonyId"
            else -> "/account/device-pairings/$ceremonyId/decision"
        }
        val access = sourceCredentials(identity, profile)
        val first = access.access(profile)
        return try { send(identity, ceremonyId, path, request, access = first) }
        catch (failure: SelfPairingRemoteFailure) {
            if (failure.code != "authentication_required") throw failure
            access.refreshAfterRejection(profile, first.generation).use { fresh ->
                send(identity, ceremonyId, path, request, access = fresh)
            }
        } finally { first.close() }
    }

    override suspend fun recipient(identity: SelfPairingIdentity, kind: String, ceremonyId: String, secret: ByteArray, request: ByteArray): JsonObject {
        require(kind in setOf("claim", "poll", "exchange"))
        SelfPairingProof.requireSecret(secret)
        return send(identity, ceremonyId, "/pairing/self-service/$ceremonyId/$kind", request, secret = secret)
    }

    private suspend fun send(identity: SelfPairingIdentity, ceremonyId: String, path: String, body: ByteArray?, access: SessionAccess? = null, secret: ByteArray? = null): JsonObject {
        requireCanonicalUuid(ceremonyId)
        require(body == null || body.size in 2..8192)
        require(identity.apiOrigin == OriginNormalizer.normalize(identity.apiOrigin, allowDevelopmentHttp))
        val request = Request.Builder().url(identity.apiOrigin + "/api/v1" + path)
            .header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
        if (body != null) request.post(body.toRequestBody("application/json".toMediaType()))
        if (access != null) request.header("Authorization", "Bearer " + access.token.toString(Charsets.US_ASCII))
        if (secret != null) request.header("X-AutPlay-Pairing-Secret", secret.toString(Charsets.US_ASCII))
        return client.newCall(request.build()).readCancellable { response ->
            if (response.code == 401) throw SelfPairingRemoteFailure("authentication_required")
            if (response.code == 429) throw SelfPairingRemoteFailure("self_pairing_rate_limited", 15)
            if (!response.isSuccessful) {
                var code = when (response.code) {
                    409 -> "self_pairing_conflict"
                    503 -> "capability_missing"
                    else -> "self_pairing_unavailable"
                }
                if (response.code == 409) {
                    val source = response.body.source()
                    if (!source.request(4097)) {
                        val raw = source.readByteArray()
                        try {
                            val document = runCatching {
                                SelfPairingJson.parse(raw, 4096, maxDepth = 2)
                            }.getOrNull()
                            val candidate = (document?.get("error") as? JsonObject)
                                ?.let { runCatching { it.text("code") }.getOrNull() }
                                ?: document?.let {
                                    runCatching { it.text("error_code") }.getOrNull()
                                }
                            if (candidate in setOf("self_pairing_revision_conflict", "operation_conflict", "account_device_limit_reached", "self_pairing_key_already_bound")) code = requireNotNull(candidate)
                        } finally { raw.fill(0) }
                    }
                }
                throw SelfPairingRemoteFailure(code)
            }
            require(response.header("Cache-Control").orEmpty().split(',').any { it.trim().equals("no-store", true) })
            val source = response.body.source()
            require(!source.request(16_385)) { "SELF_PAIRING_RESPONSE_TOO_LARGE" }
            val raw = source.readByteArray()
            try { SelfPairingJson.parse(raw, 16_384) } finally { raw.fill(0) }
        }
    }
}
