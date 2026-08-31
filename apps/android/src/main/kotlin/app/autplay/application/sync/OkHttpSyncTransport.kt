package app.autplay.application.sync

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.SessionAccess
import app.autplay.domain.ServerProfileId
import app.autplay.data.network.withAutPlayRedirectPolicy
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrl

/** Small typed P09 transport; it bounds page sizes and never logs bearer material or response payloads. */
class OkHttpSyncTransport(
    private val baseUrl: String,
    private val credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(java.time.Duration.ofSeconds(30)).build(),
    private val m5Rotation: M5SessionRotationClient? = null,
) : SyncTransport {
    private val client = client.withAutPlayRedirectPolicy()
    private val sessionCredentials = RefreshingSessionCredentials(baseUrl, credentials, client, m5Rotation = m5Rotation)

    override suspend fun push(binding: ClientEventBinding, events: List<app.autplay.data.local.entity.OfflineJournalEventEntity>): List<SyncAck> {
        require(events.size in 1..100)
        val body = buildString {
            append("{\"protocol_version\":1,\"device_id\":\"").append(binding.deviceId.value).append("\",\"server_profile_id\":\"").append(binding.serverProfileId.value).append("\",\"journal_epoch\":\"").append(binding.journalEpoch?.value ?: error("JOURNAL_EPOCH_REQUIRED")).append("\",\"events\":[")
            events.forEachIndexed { index, event -> if (index > 0) append(','); append(eventJson(event)) }; append("]}")
        }
        val root = execute(binding.serverProfileId, "/sync/push", body).jsonObject
        return root["acks"]!!.jsonArray.map { item -> item.jsonObject.let { value ->
            val error = value["error"]?.jsonObject
            SyncAck(value["event_id"]!!.jsonPrimitive.content, value["outcome"]!!.jsonPrimitive.content,
                error?.get("code")?.jsonPrimitive?.content, error?.get("retry_after_ms")?.jsonPrimitive?.longOrNull,
                error?.get("retryable")?.jsonPrimitive?.boolean ?: false, value["original_outcome"]?.jsonPrimitive?.contentOrNull,
                value["aggregate_type"]?.jsonPrimitive?.contentOrNull, value["aggregate_local_id"]?.jsonPrimitive?.contentOrNull,
                value["aggregate_server_id"]?.jsonPrimitive?.contentOrNull, value["server_row_version"]?.jsonPrimitive?.longOrNull,
                value["redirect"]?.jsonObject?.get("alias_server_id")?.jsonPrimitive?.contentOrNull,
                value["redirect"]?.jsonObject?.get("canonical_server_id")?.jsonPrimitive?.contentOrNull)
        } }
    }

    override suspend fun pull(binding: ClientEventBinding, cursor: String?): PullPage {
        val url = baseUrl.trimEnd('/').plus("/sync/pull").toHttpUrl().newBuilder()
            .addQueryParameter("protocol_version", "1").addQueryParameter("device_id", binding.deviceId.value)
            .addQueryParameter("server_profile_id", binding.serverProfileId.value).addQueryParameter("journal_epoch", binding.journalEpoch?.value ?: error("JOURNAL_EPOCH_REQUIRED"))
            .addQueryParameter("limit", "100")
            .addQueryParameter("catalog_projection_version", "1")
            .addQueryParameter("capabilities", CATALOG_ARTIST_ID_CAPABILITY)
            .apply { cursor?.let { addQueryParameter("cursor", it) } }.build()
        val root = execute(binding.serverProfileId, url.toString(), null).jsonObject
        return PullPage(root["next_cursor"]!!.jsonPrimitive.content, root["has_more"]!!.jsonPrimitive.boolean, events(root["events"]!!.jsonArray))
    }

    override suspend fun bootstrap(binding: ClientEventBinding, snapshotId: String?, pageToken: String?, pendingCount: Int): BootstrapPage {
        require(pendingCount in 0..100_000)
        val payload = "{\"protocol_version\":1,\"device_id\":\"${binding.deviceId.value}\",\"server_profile_id\":\"${binding.serverProfileId.value}\",\"journal_epoch\":\"${binding.journalEpoch?.value ?: error("JOURNAL_EPOCH_REQUIRED")}\",\"reason\":\"FIRST_SYNC\",\"snapshot_id\":${snapshotId?.let { "\"$it\"" } ?: "null"},\"page_token\":${pageToken?.let { "\"$it\"" } ?: "null"},\"pending_local_event_count\":$pendingCount,\"catalog_projection_version\":1,\"capabilities\":[\"$CATALOG_ARTIST_ID_CAPABILITY\"]}"
        val root = execute(binding.serverProfileId, "/sync/bootstrap", payload).jsonObject
        val snapshot = root["snapshot_id"]!!.jsonPrimitive.content
        val aggregates = root["aggregates"]?.jsonArray.orEmpty().map { item -> item.jsonObject.let { value ->
            val type = value["aggregate_type"]!!.jsonPrimitive.content
            RemoteEvent(value["aggregate_server_id"]!!.jsonPrimitive.content, 0, eventTypeFor(type), 1, value["payload"]!!.toString(), type, value["aggregate_server_id"]!!.jsonPrimitive.content, value["server_row_version"]!!.jsonPrimitive.long, "UPSERT")
        } }
        val tombstones = root["tombstones"]?.jsonArray.orEmpty().map { item -> item.jsonObject.let { value ->
            val type = value["aggregate_type"]!!.jsonPrimitive.content; val id = value["aggregate_server_id"]!!.jsonPrimitive.content
            RemoteEvent(value["tombstone_id"]!!.jsonPrimitive.content, 0, "AGGREGATE_DELETED", 1, "{}", type, id, null, "DELETE", value["tombstone_id"]!!.jsonPrimitive.content, instantMs(value["retain_until"]!!.jsonPrimitive.content))
        } }
        val redirects = root["redirects"]?.jsonArray.orEmpty().map { item -> item.jsonObject.let { value ->
            val type = value["aggregate_type"]!!.jsonPrimitive.content; val alias = value["alias_server_id"]!!.jsonPrimitive.content
            RemoteEvent(alias, 0, "AGGREGATE_REDIRECT", 1, "{}", type, alias, null, "REDIRECT", null, null, value["canonical_server_id"]!!.jsonPrimitive.content)
        } }
        val items = aggregates + tombstones + redirects
        return BootstrapPage(snapshot, root["next_page_token"]?.jsonPrimitive?.contentOrNull, root["snapshot_cursor"]?.jsonPrimitive?.contentOrNull, root["has_more"]!!.jsonPrimitive.boolean, items)
    }

    private suspend fun execute(profileId: ServerProfileId, path: String, body: String?) = withContext(Dispatchers.IO) {
        val url = if (path.startsWith("http")) path else baseUrl.trimEnd('/') + path
        var access = sessionCredentials.access(profileId)
        try {
            var result = executeOnce(url, body, access)
            if (result.first == 401) {
                val rejectedGeneration = access.generation
                access.close()
                access = sessionCredentials.refreshAfterRejection(profileId, rejectedGeneration)
                result = executeOnce(url, body, access)
            }
            val (status, text) = result
            if (status == 410 || (status == 409 && runCatching { Json.parseToJsonElement(text).jsonObject["code"]?.jsonPrimitive?.content }.getOrNull() in setOf("CURSOR_INVALID", "DEVICE_RESET_REQUIRED"))) throw InvalidCursorException()
            if (status !in 200..299) throw IllegalStateException("SYNC_HTTP_$status")
            Json.parseToJsonElement(text)
        } finally {
            access.close()
        }
    }

    private fun executeOnce(url: String, body: String?, access: SessionAccess): Pair<Int, String> {
        val builder = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer ${access.token.toString(StandardCharsets.UTF_8)}")
        if (body != null) builder.post(body.toRequestBody("application/json".toMediaType())) else builder.get()
        return client.newCall(builder.build()).execute().use { it.code to it.body.string() }
    }

    private fun events(items: JsonArray) = items.map { item -> item.jsonObject.let { value -> RemoteEvent(value["event_id"]!!.jsonPrimitive.content, value["server_sequence"]!!.jsonPrimitive.long, value["event_type"]!!.jsonPrimitive.content, value["schema_version"]!!.jsonPrimitive.int, value["payload"]!!.toString(), value["aggregate_type"]!!.jsonPrimitive.content, value["aggregate_server_id"]?.jsonPrimitive?.contentOrNull, value["server_row_version"]?.jsonPrimitive?.longOrNull, value["operation"]!!.jsonPrimitive.content, value["tombstone"]?.jsonObject?.get("tombstone_id")?.jsonPrimitive?.content, value["tombstone"]?.jsonObject?.get("retain_until")?.jsonPrimitive?.contentOrNull?.let(::instantMs), value["redirect"]?.jsonObject?.get("canonical_server_id")?.jsonPrimitive?.content) } }
    private fun eventTypeFor(type: String) = when (type) {
        "USER_TRACK_REF" -> "USER_TRACK_REF_CREATED"
        "LIBRARY_ENTRY" -> "LIBRARY_ENTRY_UPSERTED"
        "PLAYLIST" -> "PLAYLIST_CREATED"
        "PLAYLIST_ENTRY" -> "PLAYLIST_ENTRY_UPSERTED"
        "ARTIST" -> "CATALOG_ARTIST_UPSERTED"
        "ARTIST_CREDIT" -> "CATALOG_ARTIST_CREDIT_UPSERTED"
        "RECORDING_ARTIST_CREDIT" -> "CATALOG_RECORDING_CREDIT_LINK_UPSERTED"
        "RELEASE_ARTIST_CREDIT" -> "CATALOG_RELEASE_CREDIT_LINK_UPSERTED"
        else -> "BOOTSTRAP_UNKNOWN"
    }
    private fun instantMs(value: String): Long = java.time.Instant.parse(value).toEpochMilli()
    private fun eventJson(e: app.autplay.data.local.entity.OfflineJournalEventEntity) = "{\"event_id\":\"${e.eventId}\",\"idempotency_key\":\"${e.idempotencyKey}\",\"user_id\":\"${e.userId}\",\"device_id\":\"${e.deviceId}\",\"server_profile_id\":\"${e.serverProfileId}\",\"device_sequence\":${e.deviceSequence},\"event_type\":\"${e.eventType}\",\"schema_version\":${e.schemaVersion},\"aggregate_type\":\"${e.aggregateType}\",\"aggregate_local_id\":\"${e.aggregateLocalId}\",\"aggregate_server_id\":${e.aggregateServerId?.let { "\"$it\"" } ?: "null"},\"base_server_row_version\":${e.baseServerRowVersion ?: "null"},\"occurred_at\":\"${java.time.Instant.ofEpochMilli(e.occurredAtMs)}\",\"payload\":${e.payloadJson},\"request_hash\":\"${e.requestHash.joinToString("") { "%02x".format(it) }}\"}"

    private companion object {
        const val CATALOG_ARTIST_ID_CAPABILITY = "CATALOG_ARTIST_ID_V1"
    }
}
