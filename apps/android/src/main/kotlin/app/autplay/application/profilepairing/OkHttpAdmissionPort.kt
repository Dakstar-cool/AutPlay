package app.autplay.application.profilepairing

import java.nio.charset.StandardCharsets
import java.time.Duration
import app.autplay.data.network.withAutPlayRedirectPolicy
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** S1B no-store adapter. Poll authority is accepted only as a header and never interpolated into a URL. */
class OkHttpAdmissionPort(
    private val originForProfile: (app.autplay.domain.ServerProfileId) -> String?,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).build(),
    private val allowUnsafeDevelopmentHttp: Boolean = false,
) : AdmissionPort {
    private val client = client.withAutPlayRedirectPolicy()
    override suspend fun request(request: AdmissionRequest) = call(request.checkpoint, "/social/admission-requests", request.wireJson) { root ->
        AdmissionCreated(root.string("review_locator"), root.string("poll_bearer").encodeToByteArray(), "")
    }
    override suspend fun recover(request: AdmissionRequest) = call(request.checkpoint, "/social/admission-requests/${request.checkpoint.requestId}/recover", request.wireJson) { root ->
        AdmissionRecovery(root.string("review_locator"), root.string("poll_bearer").encodeToByteArray(), "")
    }
    override suspend fun poll(request: AdmissionRequest, pollBearer: ByteArray): PairingNetworkResult<AdmissionPoll> = call(request.checkpoint, "/social/admission-requests/${request.checkpoint.requestId}/poll", request.wireJson, pollBearer) { root ->
        when (root.string("state")) { "PENDING" -> AdmissionPoll.Pending; "APPROVED", "TRUSTED" -> AdmissionPoll.Approved(AdmissionAccount(app.autplay.domain.UserId(root.string("approved_account_id")), root.string("approved_account_label"))); "REJECTED" -> AdmissionPoll.Rejected; "BLOCKED" -> AdmissionPoll.Blocked; "EXPIRED" -> AdmissionPoll.Expired; else -> AdmissionPoll.Unavailable }
    }
    override suspend fun exchange(command: AdmissionExchangeCommand) = call(command.checkpoint, "/social/admission-requests/${command.checkpoint.requestId}/exchange", command.wireJson, command.pollBearer) { root ->
        require(root.string("binding_commit_id") == command.bindingCommitId)
        EnrollmentSession(
            app.autplay.domain.DeviceId(root.string("device_id")), root.string("session_id"), root.string("session_id"), 0,
            root.string("access_token").encodeToByteArray(), command.nextRefreshToken.copyOf(),
        )
    }
    override suspend fun trustedReenrollmentChallenge(request: AdmissionRequest) = call(request.checkpoint, "/social/trusted-keys/re-enrollment/challenge", request.wireJson) { root ->
        TrustedReenrollmentChallenge(root.string("challenge_id"), root.string("challenge").encodeToByteArray())
    }
    override suspend fun trustedReenrollmentExchange(command: TrustedReenrollmentCommand) = call(command.checkpoint, "/social/trusted-keys/re-enrollment/exchange", command.wireJson) { root ->
        require(root.string("binding_commit_id") == command.bindingCommitId)
        EnrollmentSession(app.autplay.domain.DeviceId(root.string("device_id")), root.string("session_id"), root.string("session_id"), 0, root.string("access_token").encodeToByteArray(), command.nextRefreshToken.copyOf())
    }

    private suspend fun <T> call(checkpoint: AdmissionCheckpoint, path: String, body: String?, bearer: ByteArray? = null, parse: (kotlinx.serialization.json.JsonObject) -> T): PairingNetworkResult<T> = withContext(Dispatchers.IO) {
        val rawOrigin = originForProfile(checkpoint.serverProfileId) ?: return@withContext PairingNetworkResult.Failure("admission_request_unavailable")
        try {
            val origin = OriginNormalizer.normalize(rawOrigin, allowUnsafeDevelopmentHttp)
            val builder = Request.Builder().url(origin.trimEnd('/') + "/api/v1" + path).header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
            if (bearer != null) builder.header("X-AutPlay-Admission-Poll", bearer.toString(StandardCharsets.US_ASCII))
            if (body == null) builder.get() else builder.post(body.toRequestBody(JSON))
            client.newCall(builder.build()).execute().use { response ->
                if (!response.header("Cache-Control").orEmpty().contains("no-store")) return@use PairingNetworkResult.Failure("admission_request_unavailable")
                val raw = response.body.string().take(16384); val root = Json.parseToJsonElement(raw).jsonObject
                if (!response.isSuccessful) PairingNetworkResult.Failure(root["error"]?.jsonObject?.get("code")?.jsonPrimitive?.content ?: "admission_request_unavailable") else PairingNetworkResult.Success(parse(root))
            }
        } catch (_: Exception) { PairingNetworkResult.Failure("admission_request_unavailable") }
        finally { bearer?.fill(0) }
    }
    private fun kotlinx.serialization.json.JsonObject.string(key: String) = requireNotNull(this[key]).jsonPrimitive.content
    private companion object { val JSON = "application/json; charset=utf-8".toMediaType() }
}
