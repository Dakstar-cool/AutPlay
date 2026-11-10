package app.autplay.application.releasewatch

import app.autplay.data.network.withAutPlayRedirectPolicy
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.data.security.SessionRequiredException
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Private, bounded release-watch transport. Neither URLs nor owner responses are logged. */
class OkHttpReleaseWatchPort(
    private val apiBaseUrl: String,
    credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient(),
    m5Rotation: M5SessionRotationClient? = null,
) : ReleaseWatchPort {
    private val client = client.withAutPlayRedirectPolicy()
    private val session = RefreshingSessionCredentials(apiBaseUrl.trimEnd('/'), credentials, this.client, m5Rotation = m5Rotation)

    override suspend fun artists(profileId: ServerProfileId, afterArtistId: String?): WatchPage<WatchedArtist> =
        request(profileId, "GET", pagePath("/release-watch/artists", afterArtistId)).let { root ->
            WatchPage(root.getValue("artists").jsonArray.map { item ->
                val value = item.jsonObject
                WatchedArtist(
                    artistId = uuid(value.getValue("artist_id").jsonPrimitive.content),
                    reason = WatchReason.valueOf(value.getValue("reason").jsonPrimitive.content),
                    scoreMilli = value["score_milli"]?.jsonPrimitive?.int,
                    favoriteGeneration = value["favorite_generation"]?.jsonPrimitive?.longOrNull,
                    autoDownload = value["auto_download"]?.takeUnless { it is JsonNull }?.jsonObject?.let(::artistAutoDownload),
                )
            }.also { require(it.size <= PAGE_SIZE) })
        }

    override suspend fun exclusions(profileId: ServerProfileId, afterArtistId: String?): WatchPage<String> =
        request(profileId, "GET", pagePath("/release-watch/exclusions", afterArtistId)).let { root ->
            WatchPage(root.getValue("artist_ids").jsonArray.map { uuid(it.jsonPrimitive.content) }
                .also { require(it.size <= PAGE_SIZE) })
        }

    override suspend fun setFollow(profileId: ServerProfileId, artistId: String, follow: Boolean) {
        val id = uuid(artistId)
        val root = request(profileId, if (follow) "PUT" else "DELETE", "/release-watch/artists/$id")
        require(root.getValue("artist_id").jsonPrimitive.content == id)
        require(root.getValue("revision").jsonPrimitive.longOrNull != null)
    }

    override suspend fun autoDownloadSettings(profileId: ServerProfileId): OwnerAutoDownloadSettings =
        ownerAutoDownload(request(profileId, "GET", "/release-watch/auto-download"))

    override suspend fun setAutoDownload(
        profileId: ServerProfileId,
        enabled: Boolean,
        expectedRevision: Long,
        consequenceConfirmation: String?,
    ) {
        require(expectedRevision >= 0)
        require(!enabled || !consequenceConfirmation.isNullOrBlank())
        request(profileId, "PUT", "/release-watch/auto-download", buildJsonObject {
            put("enabled", enabled)
            put("expected_revision", expectedRevision)
            if (enabled) put("consequence_confirmation", consequenceConfirmation)
        })
    }

    private fun ownerAutoDownload(root: JsonObject): OwnerAutoDownloadSettings = OwnerAutoDownloadSettings(
        enabled = root.getValue("enabled").jsonPrimitive.boolean,
        revision = requireNotNull(root.getValue("revision").jsonPrimitive.longOrNull).also { require(it >= 0) },
        executionEnabled = root.getValue("execution_enabled").jsonPrimitive.boolean,
        consequenceConfirmation = root["consequence_confirmation"]?.jsonPrimitive?.contentOrNull,
    )

    private fun artistAutoDownload(root: JsonObject): ArtistAutoDownloadStatus = ArtistAutoDownloadStatus(
        watchPolicyId = root["watch_policy_id"]?.jsonPrimitive?.contentOrNull?.let(::uuid),
        policyRevision = root["policy_revision"]?.jsonPrimitive?.longOrNull,
        policyImportMode = root["policy_import_mode"]?.jsonPrimitive?.contentOrNull,
        effectiveOptIn = root.getValue("effective_opt_in").jsonPrimitive.boolean,
        executionEnabled = root.getValue("execution_enabled").jsonPrimitive.boolean,
        reason = ArtistAutoDownloadReason.valueOf(root.getValue("reason").jsonPrimitive.content),
    )

    private fun pagePath(path: String, afterArtistId: String?): String =
        "$path?limit=$PAGE_SIZE" + (afterArtistId?.let { "&after_artist_id=${uuid(it)}" } ?: "")

    private suspend fun request(profileId: ServerProfileId, method: String, path: String, body: JsonObject? = null): JsonObject {
        try {
            var access = session.access(profileId)
            try {
                var response = withContext(Dispatchers.IO) { execute(method, path, body, access) }
                if (response.first == 401) {
                    val generation = access.generation
                    access.close()
                    access = session.refreshAfterRejection(profileId, generation)
                    response = withContext(Dispatchers.IO) { execute(method, path, body, access) }
                }
                if (response.first !in 200..299) throw ReleaseWatchException(when (response.first) {
                    401, 403 -> WatchFailure.SESSION
                    404 -> WatchFailure.UNAVAILABLE
                    else -> WatchFailure.SERVER
                })
                return Json.parseToJsonElement(response.second).jsonObject.also { root ->
                    require(root.getValue("contract_version").jsonPrimitive.content == "release-watch-v1")
                    require(root.getValue("schema_version").jsonPrimitive.int == 1)
                }
            } finally {
                access.close()
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: SessionRequiredException) {
            throw ReleaseWatchException(WatchFailure.SESSION)
        } catch (_: IOException) {
            throw ReleaseWatchException(WatchFailure.OFFLINE)
        } catch (error: ReleaseWatchException) {
            throw error
        } catch (_: Exception) {
            throw ReleaseWatchException(WatchFailure.SERVER)
        }
    }

    private fun execute(method: String, path: String, body: JsonObject?, access: SessionAccess): Pair<Int, String> {
        val request = Request.Builder()
            .url(apiBaseUrl.trimEnd('/') + path)
            .header("Authorization", "Bearer " + access.token.toString(StandardCharsets.UTF_8))
            .header("Accept", "application/json")
            .header("Cache-Control", "no-store")
            .apply {
                when (method) {
                    "GET" -> get()
                    "PUT" -> put(if (body == null) ByteArray(0).toRequestBody() else body.toString().toRequestBody(JSON))
                    "DELETE" -> delete()
                    else -> error("Unsupported release-watch method")
                }
            }.build()
        return client.newCall(request).execute().use { response ->
            val raw = response.peekBody((MAX_RESPONSE_CHARS + 1).toLong()).bytes()
            require(raw.size <= MAX_RESPONSE_CHARS)
            response.code to raw.toString(StandardCharsets.UTF_8)
        }
    }

    private fun uuid(value: String): String = UUID.fromString(value).toString()

    private companion object {
        const val PAGE_SIZE = 100
        const val MAX_RESPONSE_CHARS = 100_000
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
