package app.autplay

import androidx.room3.withWriteTransaction
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.OkHttpSyncTransport
import app.autplay.application.sync.SyncCoordinator
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.io.File
import java.net.InetAddress
import java.net.URI
import java.time.Duration
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Explicit, debug-only local provisioning. A normal connected suite performs no mutation here. */
@RunWith(AndroidJUnit4::class)
class LocalServerProvisioningTest {
    @Test fun provisionTrustedLocalServerOnlyWhenExplicitlyEnabled() = runBlocking {
        val arguments = InstrumentationRegistry.getArguments()
        if (arguments.getString(ENABLED) != "true") return@runBlocking

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val provisioning = readAndDeleteProvisioning(File(context.filesDir, PROVISIONING_FILE))
        val serverBaseUrl = trustedLocalBaseUrl(provisioning.required(BASE_URL))
        val streamBaseUrl = trustedLocalBaseUrl(provisioning.required(STREAM_BASE_URL))
        val userId = normalizedUuid(provisioning.required(USER_ID))
        val deviceId = normalizedUuid(provisioning.required(DEVICE_ID))
        val profileId = normalizedUuid(provisioning.required(PROFILE_ID))
        val journalEpoch = normalizedUuid(provisioning.required(JOURNAL_EPOCH))
        val lineageId = normalizedUuid(provisioning.required(LINEAGE_ID))
        val accessToken = provisioning.required(ACCESS_TOKEN)
        val refreshToken = provisioning.required(REFRESH_TOKEN)
        require(accessToken.length <= MAX_TOKEN_CHARS && refreshToken.length <= MAX_TOKEN_CHARS)
        val binding = ClientEventBinding(
            UserId(userId),
            DeviceId(deviceId),
            ServerProfileId(profileId),
            LocalId(journalEpoch),
        )
        val apiBaseUrl = "$serverBaseUrl/api/v1"

        bindDevice(apiBaseUrl, accessToken, binding)

        val database = AutPlayRuntime.database(context)
        val nowMs = System.currentTimeMillis()
        database.withWriteTransaction {
            val existingLineage = database.journalDao().lineageByDeviceId(deviceId)
            val lineage = existingLineage ?: JournalLineageEntity(
                lineageId = lineageId,
                userId = userId,
                deviceId = deviceId,
                journalEpoch = journalEpoch,
                createdAtMs = nowMs,
            ).also { database.journalDao().insertLineage(it) }
            check(lineage.userId == userId && lineage.journalEpoch == journalEpoch) {
                "PROVISIONING_LINEAGE_MISMATCH"
            }
            val existingCursor = database.syncDao().cursor(profileId)
            check(
                existingCursor == null ||
                    (existingCursor.journalLineageId == lineage.lineageId &&
                        existingCursor.deviceId == deviceId &&
                        existingCursor.journalEpoch == journalEpoch),
            ) { "PROVISIONING_CURSOR_MISMATCH" }
            if (existingCursor == null) {
                database.syncDao().upsertCursor(
                    SyncCursorEntity(
                        serverProfileId = profileId,
                        journalLineageId = lineage.lineageId,
                        deviceId = deviceId,
                        journalEpoch = journalEpoch,
                        opaqueCursor = null,
                        lastPulledServerSequence = 0,
                        lastAckedDeviceSequence = 0,
                        bootstrapSnapshotId = null,
                        bootstrapState = "NOT_STARTED",
                        lastSyncAtMs = null,
                        updatedAtMs = nowMs,
                    ),
                )
            }
        }

        val credentialStore = AndroidKeystoreCredentialStore(context)
        credentialStore.write(
            binding.serverProfileId,
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(accessToken, refreshToken, 0),
            ),
        )
        assertTrue(SyncCoordinator(database, OkHttpSyncTransport(apiBaseUrl, credentialStore)).run(binding))
        assertEquals("READY", database.syncDao().cursor(profileId)?.bootstrapState)

        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = binding.serverProfileId,
                activeUserId = binding.userId,
                deviceId = binding.deviceId,
                serverBaseUrl = serverBaseUrl,
                streamBaseUrl = streamBaseUrl,
                syncOnMeteredNetwork = false,
            ),
        )
    }

    private fun bindDevice(baseUrl: String, accessToken: String, binding: ClientEventBinding) {
        val body = buildJsonObject {
            put("protocol_version", 1)
            put("user_id", binding.userId.value)
            put("device_id", binding.deviceId.value)
            put("server_profile_id", binding.serverProfileId.value)
            put("journal_epoch", requireNotNull(binding.journalEpoch).value)
            put("device_name", "Samsung A55")
            put("platform", "ANDROID")
            put("app_version", "0.1.0")
        }.toString()
        val request = Request.Builder()
            .url("$baseUrl/devices/bind")
            .header("Authorization", "Bearer $accessToken")
            .post(body.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        CLIENT.newCall(request).execute().use { response ->
            check(response.isSuccessful) { "DEVICE_BIND_HTTP_${response.code}" }
        }
    }

    private fun trustedLocalBaseUrl(raw: String): String {
        val value = raw.trimEnd('/')
        val uri = URI(value)
        require(uri.scheme == "http" && uri.userInfo == null && uri.query == null && uri.fragment == null)
        require(uri.port in 1..65535)
        val address = InetAddress.getByName(requireNotNull(uri.host))
        require(address.isSiteLocalAddress || address.isLoopbackAddress) { "PROVISIONING_HOST_NOT_LOCAL" }
        return value
    }

    private fun normalizedUuid(raw: String): String = UUID.fromString(raw).toString()

    private fun readAndDeleteProvisioning(file: File): JsonObject {
        require(file.isFile && file.length() in 1..MAX_PROVISIONING_BYTES)
        val bytes = file.readBytes()
        check(file.delete()) { "PROVISIONING_FILE_DELETE_FAILED" }
        return try {
            Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
        } finally {
            bytes.fill(0)
        }
    }

    private fun JsonObject.required(name: String): String =
        requireNotNull(this[name]?.jsonPrimitive?.content?.takeIf(String::isNotBlank)) {
            "PROVISIONING_ARGUMENT_MISSING_$name"
        }

    private companion object {
        const val ENABLED = "autplayProvisioningEnabled"
        const val PROVISIONING_FILE = "local-server-provisioning.json"
        const val BASE_URL = "autplayProvisioningBaseUrl"
        const val STREAM_BASE_URL = "autplayProvisioningStreamBaseUrl"
        const val USER_ID = "autplayProvisioningUserId"
        const val DEVICE_ID = "autplayProvisioningDeviceId"
        const val PROFILE_ID = "autplayProvisioningProfileId"
        const val JOURNAL_EPOCH = "autplayProvisioningJournalEpoch"
        const val LINEAGE_ID = "autplayProvisioningLineageId"
        const val ACCESS_TOKEN = "autplayProvisioningAccessToken"
        const val REFRESH_TOKEN = "autplayProvisioningRefreshToken"
        const val MAX_TOKEN_CHARS = 4_096
        const val MAX_PROVISIONING_BYTES = 16_384L
        val JSON_MEDIA_TYPE = "application/json".toMediaType()
        val CLIENT = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(30)).build()
    }
}
