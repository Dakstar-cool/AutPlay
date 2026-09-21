package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.data.network.readCancellable
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.domain.ServerProfileId
import java.time.Duration
import kotlinx.serialization.json.JsonObject
import okhttp3.CookieJar
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class AccountRecoveryRemoteFailure(val code: String) : IllegalStateException(code)

interface AccountRecoveryTransport {
    suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, request: ByteArray? = null): JsonObject
    suspend fun recipient(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject
    suspend fun outcome(identity: SelfPairingIdentity, refresh: ByteArray, request: ByteArray): JsonObject =
        throw AccountRecoveryRemoteFailure("account_recovery_outcome_unavailable")
}

class OkHttpAccountRecoveryTransport(
    private val credentials: (SelfPairingIdentity, ServerProfileId) -> RefreshingSessionCredentials,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).build(),
    private val allowDevelopmentHttp: Boolean = false,
) : AccountRecoveryTransport {
    private val client = client.newBuilder().followRedirects(false).followSslRedirects(false)
        .cookieJar(CookieJar.NO_COOKIES).build()

    override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, request: ByteArray?): JsonObject {
        val provider = credentials(identity, profile)
        val first = provider.access(profile)
        return try { send(identity, "/account/recovery", request, access = first) }
        catch (failure: AccountRecoveryRemoteFailure) {
            if (failure.code != "authentication_required") throw failure
            provider.refreshAfterRejection(profile, first.generation).use { fresh ->
                send(identity, "/account/recovery", request, access = fresh)
            }
        } finally { first.close() }
    }

    override suspend fun recipient(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject {
        require(kind in setOf("preview", "recover"))
        require(code.size == 32 && code.all { it.toInt().toChar() in AccountRecoveryProof.ALPHABET })
        return send(identity, if (kind == "preview") "/recovery/preview" else "/recovery/commit", request, code = code)
    }
    override suspend fun outcome(identity: SelfPairingIdentity, refresh: ByteArray, request: ByteArray): JsonObject {
        require(refresh.size in 32..128 && refresh.all { it.toInt() in 33..126 })
        return send(identity, "/recovery/outcome", request, refresh = refresh)
    }

    private suspend fun send(identity: SelfPairingIdentity, path: String, body: ByteArray?, access: SessionAccess? = null,
        code: ByteArray? = null, refresh: ByteArray? = null): JsonObject {
        require(identity.apiOrigin == OriginNormalizer.normalize(identity.apiOrigin, allowDevelopmentHttp))
        require(body == null || body.size in 2..8192)
        require(refresh == null || refresh.size in 32..128 && refresh.all { it.toInt() in 33..126 })
        val request = Request.Builder().url(identity.apiOrigin + "/api/v1" + path)
            .header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
        if (body != null) request.post(body.toRequestBody("application/json".toMediaType()))
        if (access != null) request.header("Authorization", "Bearer " + access.token.toString(Charsets.US_ASCII))
        if (code != null) request.header("X-AutPlay-Recovery-Code", code.toString(Charsets.US_ASCII))
        if (refresh != null) request.header("X-AutPlay-Recovery-Refresh", refresh.toString(Charsets.US_ASCII))
        return client.newCall(request.build()).readCancellable { response ->
            if (!response.isSuccessful) throw AccountRecoveryRemoteFailure(when (response.code) {
                401 -> "authentication_required"
                429 -> "account_recovery_rate_limited"
                409 -> "recovery_conflict"
                503 -> "capability_missing"
                else -> "account_recovery_unavailable"
            })
            require(response.header("Cache-Control").orEmpty().split(',').any { it.trim().equals("no-store", true) })
            val source = response.body.source()
            require(!source.request(16_385))
            val raw = source.readByteArray()
            try { SelfPairingJson.parse(raw, 16_384) } finally { raw.fill(0) }
        }
    }
}
