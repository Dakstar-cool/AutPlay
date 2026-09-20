package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.data.network.readCancellable
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.domain.ServerProfileId
import java.time.Duration
import kotlinx.serialization.json.JsonObject
import okhttp3.CookieJar
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

interface AccountDeletionTransport {
    suspend fun status(identity: SelfPairingIdentity, profile: ServerProfileId): JsonObject
    suspend fun request(identity: SelfPairingIdentity, access: ByteArray, code: ByteArray, request: ByteArray): JsonObject
    suspend fun receipt(identity: SelfPairingIdentity, code: ByteArray, request: ByteArray): JsonObject
    suspend fun resolve(identity: SelfPairingIdentity, code: ByteArray, request: ByteArray): JsonObject
    suspend fun cancel(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject
    suspend fun cancelOutcome(identity: SelfPairingIdentity, refresh: ByteArray, request: ByteArray): JsonObject =
        throw AccountRecoveryRemoteFailure("account_deletion_outcome_unavailable")
}

/** Cancellation shares binding persistence, while all wire paths retain the deletion purpose. */
class AccountDeletionCancellationTransport(private val deletion: AccountDeletionTransport) : AccountRecoveryTransport {
    override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, request: ByteArray?): JsonObject =
        error("deletion_source_transport_forbidden")
    override suspend fun recipient(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject =
        deletion.cancel(identity, if (kind == "recover") "cancel" else kind, code, request)
    override suspend fun outcome(identity: SelfPairingIdentity, refresh: ByteArray, request: ByteArray): JsonObject =
        deletion.cancelOutcome(identity, refresh, request)
}

class OkHttpAccountDeletionTransport(
    private val credentials: (SelfPairingIdentity, ServerProfileId) -> RefreshingSessionCredentials,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).build(),
    private val allowDevelopmentHttp: Boolean = false,
) : AccountDeletionTransport {
    private val client = client.newBuilder().followRedirects(false).followSslRedirects(false)
        .cookieJar(CookieJar.NO_COOKIES).build()
    override suspend fun status(identity: SelfPairingIdentity, profile: ServerProfileId): JsonObject {
        val provider = credentials(identity, profile)
        val first = provider.access(profile)
        return try { send(identity, "/account/deletion", access = first.token) }
        catch (failure: AccountRecoveryRemoteFailure) {
            if (failure.code != "authentication_required") throw failure
            provider.refreshAfterRejection(profile, first.generation).use { send(identity, "/account/deletion", access = it.token) }
        } finally { first.close() }
    }
    override suspend fun request(identity: SelfPairingIdentity, access: ByteArray, code: ByteArray, request: ByteArray): JsonObject =
        send(identity, "/account/deletion", request, access, code)
    override suspend fun receipt(identity: SelfPairingIdentity, code: ByteArray, request: ByteArray): JsonObject =
        send(identity, "/deletion/request-receipt", request, code = code)
    override suspend fun resolve(identity: SelfPairingIdentity, code: ByteArray, request: ByteArray): JsonObject =
        send(identity, "/deletion/request-resolve", request, code = code)
    override suspend fun cancel(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject {
        require(kind in setOf("preview", "cancel"))
        return send(identity, if (kind == "preview") "/deletion/cancel/preview" else "/deletion/cancel/commit", request, code = code)
    }
    override suspend fun cancelOutcome(identity: SelfPairingIdentity, refresh: ByteArray, request: ByteArray): JsonObject {
        require(refresh.size in 32..128 && refresh.all { it.toInt() in 33..126 })
        return send(identity, "/deletion/cancel/outcome", request, refresh = refresh)
    }
    private suspend fun send(identity: SelfPairingIdentity, path: String, body: ByteArray? = null,
        access: ByteArray? = null, code: ByteArray? = null, refresh: ByteArray? = null): JsonObject {
        require(identity.apiOrigin == OriginNormalizer.normalize(identity.apiOrigin, allowDevelopmentHttp))
        require(body == null || body.size in 2..8192)
        require(code == null || code.size == 32 && code.all { it.toInt().toChar() in AccountRecoveryProof.ALPHABET })
        require(refresh == null || refresh.size in 32..128 && refresh.all { it.toInt() in 33..126 })
        require(access == null || access.size in 1..4096 && access.all { it.toInt() in 33..126 })
        val request = Request.Builder().url(identity.apiOrigin + "/api/v1" + path)
            .header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
        if (body != null) request.post(body.toRequestBody("application/json".toMediaType()))
        if (access != null) request.header("Authorization", "Bearer " + access.toString(Charsets.US_ASCII))
        if (code != null) request.header("X-AutPlay-Recovery-Code", code.toString(Charsets.US_ASCII))
        if (refresh != null) request.header("X-AutPlay-Recovery-Refresh", refresh.toString(Charsets.US_ASCII))
        return client.newCall(request.build()).readCancellable { response ->
            if (!response.isSuccessful) {
                // Read only a bounded allowlisted code; personal server messages never reach UI/logs.
                val source = response.body.source()
                val raw = if (!source.request(4097)) source.readByteArray() else null
                val codeValue = try { raw?.let { SelfPairingJson.parse(it, 4096, maxDepth = 2)["error"] as? JsonObject }
                    ?.get("code")?.let { it as? kotlinx.serialization.json.JsonPrimitive }?.content }
                    catch (_: Exception) { null } finally { raw?.fill(0) }
                throw AccountRecoveryRemoteFailure(if (codeValue == "last_owner_required") codeValue else when (response.code) {
                    401 -> "authentication_required"
                    429 -> "account_recovery_rate_limited"
                    409 -> "deletion_conflict"
                    503 -> "capability_missing"
                    else -> "account_deletion_unavailable"
                })
            }
            require(response.header("Cache-Control").orEmpty().split(',').any { it.trim().equals("no-store", true) })
            val source = response.body.source()
            require(!source.request(16_385))
            val raw = source.readByteArray()
            try { SelfPairingJson.parse(raw, 16_384) } finally { raw.fill(0) }
        }
    }
}
