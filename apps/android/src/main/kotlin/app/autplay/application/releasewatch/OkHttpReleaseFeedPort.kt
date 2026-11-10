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
import java.time.OffsetDateTime
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/** Read-only, owner-scoped Home feed transport. Never sends impression or training events. */
class OkHttpReleaseFeedPort(
    private val apiBaseUrl: String,
    credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient(),
    m5Rotation: M5SessionRotationClient? = null,
) : ReleaseFeedPort {
    private val client = client.withAutPlayRedirectPolicy()
    private val session = RefreshingSessionCredentials(apiBaseUrl.trimEnd('/'), credentials, this.client, m5Rotation = m5Rotation)

    override suspend fun page(profileId: ServerProfileId, cursor: String?): ReleaseFeedPage {
        require(cursor == null || cursor.length in 1..512)
        val url = (apiBaseUrl.trimEnd('/') + "/release-watch/feed").toHttpUrl().newBuilder()
            .addQueryParameter("limit", PAGE_SIZE.toString())
            .apply { cursor?.let { addQueryParameter("cursor", it) } }
            .build().toString()
        val root = request(profileId, url)
        require(root.getValue("contract_version").jsonPrimitive.content == "release-feed-v1")
        require(root.getValue("schema_version").jsonPrimitive.int == 1)
        require(root.getValue("ranking_version").jsonPrimitive.content == "owner-local-v1")
        val items = root.getValue("items").jsonArray.map { parseItem(it.jsonObject) }
        require(items.size <= PAGE_SIZE && items.distinctBy { it.candidateId }.size == items.size)
        val next = root["next_cursor"]?.takeUnless { it is JsonNull }?.jsonPrimitive?.content
        require(next == null || next.length in 1..512)
        return ReleaseFeedPage(items, next)
    }

    private fun parseItem(value: JsonObject): ReleaseFeedItem {
        val state = ReleaseFeedState.valueOf(value.getValue("state").jsonPrimitive.content)
        val action = ReleaseFeedAction.valueOf(value.getValue("available_action").jsonPrimitive.content)
        val ownerRef = value.optionalUuid("user_track_ref_id")
        require((state == ReleaseFeedState.READY) == (ownerRef != null))
        require(action == when (state) {
            ReleaseFeedState.READY -> ReleaseFeedAction.PLAY
            ReleaseFeedState.REVIEW_REQUIRED -> ReleaseFeedAction.REVIEW
            ReleaseFeedState.DISCOVERED -> ReleaseFeedAction.OPEN_DISCOVERY
            else -> ReleaseFeedAction.NONE
        })
        val releasedAt = value.getValue("released_at").jsonPrimitive.content
        OffsetDateTime.parse(releasedAt)
        val score = value.getValue("score_milli").jsonPrimitive.int
        require(score in 0..1_000_000)
        return ReleaseFeedItem(
            candidateId = value.uuid("candidate_id"),
            canonicalArtistId = value.uuid("canonical_artist_id"),
            artistName = value.getValue("artist_name").jsonPrimitive.content.take(MAX_LABEL_CHARS),
            title = value.getValue("title").jsonPrimitive.content.take(MAX_LABEL_CHARS),
            releasedAt = releasedAt,
            reason = WatchReason.valueOf(value.getValue("reason").jsonPrimitive.content),
            state = state,
            scoreMilli = score,
            releaseId = value.optionalUuid("release_id"),
            recordingId = value.optionalUuid("recording_id"),
            userTrackRefId = ownerRef,
            providerId = value.uuid("provider_id"),
            evidenceRunId = value.uuid("evidence_run_id"),
            evidenceAdapterVersion = value.getValue("evidence_adapter_version").jsonPrimitive.content.take(80),
            availableAction = action,
        )
    }

    private fun JsonObject.uuid(key: String): String = UUID.fromString(getValue(key).jsonPrimitive.content).toString()
    private fun JsonObject.optionalUuid(key: String): String? = getValue(key).takeUnless { it is JsonNull }
        ?.jsonPrimitive?.content?.let { UUID.fromString(it).toString() }

    private suspend fun request(profileId: ServerProfileId, url: String): JsonObject {
        try {
            var access = session.access(profileId)
            try {
                var response = withContext(Dispatchers.IO) { execute(url, access) }
                if (response.first == 401) {
                    val generation = access.generation
                    access.close()
                    access = session.refreshAfterRejection(profileId, generation)
                    response = withContext(Dispatchers.IO) { execute(url, access) }
                }
                if (response.first !in 200..299) throw ReleaseFeedException(when (response.first) {
                    401, 403 -> ReleaseFeedFailure.SESSION
                    404 -> ReleaseFeedFailure.UNAVAILABLE
                    else -> ReleaseFeedFailure.SERVER
                })
                return Json.parseToJsonElement(response.second).jsonObject
            } finally { access.close() }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: SessionRequiredException) {
            throw ReleaseFeedException(ReleaseFeedFailure.SESSION)
        } catch (_: IOException) {
            throw ReleaseFeedException(ReleaseFeedFailure.OFFLINE)
        } catch (error: ReleaseFeedException) {
            throw error
        } catch (_: Exception) {
            throw ReleaseFeedException(ReleaseFeedFailure.SERVER)
        }
    }

    private fun execute(url: String, access: SessionAccess): Pair<Int, String> {
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer " + access.token.toString(StandardCharsets.UTF_8))
            .header("Accept", "application/json")
            .header("Cache-Control", "no-store")
            .get().build()
        return client.newCall(request).execute().use { response ->
            val raw = response.peekBody((MAX_RESPONSE_BYTES + 1).toLong()).bytes()
            require(raw.size <= MAX_RESPONSE_BYTES)
            response.code to raw.toString(StandardCharsets.UTF_8)
        }
    }

    private companion object {
        const val PAGE_SIZE = 20
        const val MAX_LABEL_CHARS = 200
        const val MAX_RESPONSE_BYTES = 100_000
    }
}
