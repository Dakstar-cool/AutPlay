package app.autplay.application.trainingconsent

import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.domain.ServerProfileId
import app.autplay.data.network.readCancellable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class OkHttpTrainingConsentPort(
    private val apiBaseUrl: String, private val profileId: ServerProfileId,
    credentials: CredentialStore, client: OkHttpClient = OkHttpClient(),
    m5Rotation: M5SessionRotationClient? = null,
) : TrainingConsentPort {
    private val client = client.newBuilder().followRedirects(false).followSslRedirects(false)
        .cookieJar(CookieJar.NO_COOKIES).callTimeout(20, TimeUnit.SECONDS).build()
    private val sessions = RefreshingSessionCredentials(apiBaseUrl, credentials, this.client, m5Rotation = m5Rotation)
    override suspend fun get() = request(null)
    override suspend fun decide(request: JsonObject) = request(request)
    private suspend fun request(body: JsonObject?): JsonObject {
        var access = sessions.access(profileId)
        try {
            try { return send(access, body) }
            catch (failure: TrainingConsentFailure) {
                if (failure.code != "authentication_required") throw failure
                val generation = access.generation
                access.close()
                access = sessions.refreshAfterRejection(profileId, generation)
                return send(access, body)
            }
        } finally { access.close() }
    }
    private suspend fun send(access: SessionAccess, body: JsonObject?): JsonObject {
        val request = Request.Builder().url(apiBaseUrl.trimEnd('/') + "/privacy/shared-training")
            .header("Authorization", "Bearer " + access.token.toString(Charsets.US_ASCII))
            .header("Accept", "application/json").header("Cache-Control", "no-store")
            .header("Pragma", "no-cache")
        if (body != null) {
            val raw = SelfPairingJson.canonical(body)
            try { require(raw.size <= 1024); request.put(raw.toString(Charsets.UTF_8).toRequestBody("application/json".toMediaType())) }
            finally { raw.fill(0) }
        }
        return client.newCall(request.build()).readCancellable { response ->
            val source = response.body.source()
            require(!source.request(4097))
            val raw = source.readByteArray()
            try {
                if (!response.isSuccessful) {
                    val root = SelfPairingJson.parse(raw, 4096, maxDepth = 2)
                    val code = ((root["error"] as? JsonObject)?.get("code") as? JsonPrimitive)?.content
                    throw TrainingConsentFailure(if (response.code == 401) "authentication_required"
                        else if (response.code == 409 && code == "consent_revision_conflict") code
                        else "training_consent_unavailable")
                }
                require(response.header("Cache-Control").orEmpty().split(',').any { it.trim().equals("no-store", true) })
                SelfPairingJson.parse(raw, 4096)
            } finally { raw.fill(0) }
        }
    }
}
