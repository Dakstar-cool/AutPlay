package app.autplay.application.wave

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.domain.ServerProfileId
import app.autplay.domain.wave.WaveAvailability
import app.autplay.domain.wave.WaveCommand
import java.nio.charset.StandardCharsets
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Auth header is installed per request/socket; credentials are never retained, logged, or put in URLs. */
class OkHttpWaveTransport(
    private val baseUrl: String,
    private val profileId: ServerProfileId,
    private val credentials: CredentialStore,
    private val client: OkHttpClient = OkHttpClient(),
    private val authBaseUrl: String = baseUrl.trimEnd('/').removeSuffix("/api") + "/api/v1",
) : WaveTransport {
    private val sessionCredentials = RefreshingSessionCredentials(authBaseUrl, credentials, client)

    override suspend fun create(allowUserIds: List<String>): WaveSnapshot {
        require(allowUserIds.size <= 7) { "WAVE_ALLOWLIST_TOO_LARGE" }
        return decodeSnapshot(
            postJson(
                "/v1/wave/rooms",
                buildJsonObject {
                    put(
                        "allow_user_ids",
                        buildJsonArray {
                            allowUserIds.forEach {
                                add(JsonPrimitive(UUID.fromString(it).toString()))
                            }
                        },
                    )
                },
            ),
        )
    }

    override suspend fun snapshot(roomId: String): WaveSnapshot {
        val normalizedRoomId = UUID.fromString(roomId).toString()
        val (status, payload) = executeAuthorized("${baseUrl.trimEnd('/')}/v1/wave/rooms/$normalizedRoomId/snapshot") { url, bearer ->
            Request.Builder().url(url).header("Authorization", bearer).build()
        }
        check(status in 200..299) { if (status == 401 || status == 403) "WAVE_AUTH_REQUIRED" else "WAVE_SNAPSHOT_FAILED" }
        check(payload.length <= MAX_SNAPSHOT_CHARS) { "WAVE_SNAPSHOT_TOO_LARGE" }
        return decodeSnapshot(Json.parseToJsonElement(payload).jsonObject)
    }
    override fun connect(
        roomId: String,
        afterSequence: Long,
        roomEpoch: String,
        onEvent: (WaveEvent) -> Unit,
        onFailure: () -> Unit,
    ): AutoCloseable {
        val normalizedRoomId = UUID.fromString(roomId).toString()
        val websocketUrl = baseUrl.trimEnd('/').replaceFirst("http", "ws") + "/v1/wave/rooms/$normalizedRoomId/events"
        val socket = client.newWebSocket(webSocketRequest(websocketUrl), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(
                    buildJsonObject {
                        put("type", "hello")
                        put("after_sequence", afterSequence)
                        put("room_epoch", roomEpoch)
                    }.toString(),
                )
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull()?.let { value ->
                    when (value["type"]?.jsonPrimitive?.contentOrNull) {
                        "snapshot_required", "invalidate" -> {
                            onFailure()
                            return@let
                        }
                        "event" -> Unit
                        else -> return@let
                    }
                    val epoch = value["epoch"]?.jsonPrimitive?.contentOrNull ?: roomEpoch
                    val sequence = value["sequence"]?.jsonPrimitive?.long ?: return@let
                    val kind = value["kind"]?.jsonPrimitive?.contentOrNull ?: return@let
                    val payload = value["payload"]?.let { element ->
                        runCatching { element.jsonPrimitive.content }.getOrElse { element.toString() }
                    }.orEmpty()
                    onEvent(WaveEvent(epoch, WaveCommand(sequence, kind, payload)))
                }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { onFailure() }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { onFailure() }
        })
        return AutoCloseable { socket.close(1000, "client-close") }
    }

    override suspend fun joinByCode(code: String): WaveSnapshot = decodeSnapshot(
        postJson(
            "/v1/wave/rooms/join",
            buildJsonObject { put("room_code", code.uppercase()) },
        ),
    )

    override suspend fun leave(roomId: String) {
        postJson("/v1/wave/rooms/${UUID.fromString(roomId)}/leave", buildJsonObject {})
    }

    override suspend fun close(roomId: String) {
        postJson("/v1/wave/rooms/${UUID.fromString(roomId)}/close", buildJsonObject {})
    }

    override suspend fun transferHost(roomId: String, targetDeviceId: String) {
        postJson(
            "/v1/wave/rooms/${UUID.fromString(roomId)}/host-transfer",
            buildJsonObject {
                put("target_device_id", UUID.fromString(targetDeviceId).toString())
            },
        )
    }

    override suspend fun hostCommand(roomId: String, command: WaveCommand, queueVersion: Long) {
        val normalized = UUID.fromString(roomId)
        val expectedSequence = command.sequence - 1
        if (command.kind == "PLAY") {
            val payload = Json.parseToJsonElement(command.payload).jsonObject
            postJson(
                "/v1/wave/rooms/$normalized/start",
                buildJsonObject {
                    put("queue_entry_id", payload.getValue("queue_entry_id").jsonPrimitive.content)
                    put("recording_id", payload.getValue("recording_id").jsonPrimitive.content)
                    put("queue_version", queueVersion)
                    put("expected_sequence", expectedSequence)
                },
            )
            return
        }
        postJson(
            "/v1/wave/rooms/$normalized/commands",
            buildJsonObject {
                put("kind", command.kind)
                put("idempotency_key", "android-${command.kind.lowercase()}-${command.sequence}")
                put("base_version", queueVersion)
                put("expected_sequence", expectedSequence)
                command.payload.takeIf(String::isNotBlank)?.let { put("recording_id", it) }
            },
        )
    }

    override suspend fun clock(): WaveClockSample {
        val clientSendMs = System.currentTimeMillis()
        val payload = postJson("/v1/wave/clock", buildJsonObject {})
        val clientReceiveMs = System.currentTimeMillis()
        val serverReceiveMs = payload.getValue("server_receive_epoch_ms").jsonPrimitive.long
        val serverSendMs = payload.getValue("server_send_epoch_ms").jsonPrimitive.long
        return WaveClockSample(clientSendMs, serverReceiveMs, serverSendMs, clientReceiveMs)
    }

    override suspend fun start(
        roomId: String,
        queueEntryId: String,
        recordingId: String,
        queueVersion: Long,
        expectedSequence: Long,
    ): Boolean {
        val payload = postJson(
            "/v1/wave/rooms/${UUID.fromString(roomId)}/start",
            buildJsonObject {
                put("queue_entry_id", UUID.fromString(queueEntryId).toString())
                put("recording_id", UUID.fromString(recordingId).toString())
                put("queue_version", queueVersion)
                put("expected_sequence", expectedSequence)
            },
        )
        return payload["started"]?.jsonPrimitive?.booleanOrNull == true
    }

    override suspend fun preflight(roomId: String, reports: List<WavePreflightReport>) {
        val normalized = UUID.fromString(roomId)
        require(reports.size <= 4) { "WAVE_PREFLIGHT_TOO_LARGE" }
        reports.forEach { report ->
            postJson(
                "/v1/wave/rooms/$normalized/availability",
                buildJsonObject {
                    put("queue_entry_id", report.queueEntryId)
                    put("recording_id", report.recordingId)
                    put("queue_version", report.queueVersion)
                    put(
                        "availability",
                        if (report.availability == WaveAvailability.LOCAL_READABLE) {
                            "LOCAL"
                        } else {
                            report.availability.name
                        },
                    )
                    put("final_ready", report.finalReady)
                },
            )
        }
    }

    override suspend fun timing(roomId: String, report: WaveTimingReport) {
        postJson(
            "/v1/wave/rooms/${UUID.fromString(roomId)}/timing",
            buildJsonObject {
                put("command_sequence", report.commandSequence)
                put("rtt_ms", report.rttMs)
                put("offset_ms", report.offsetMs)
                put("uncertainty_ms", report.uncertaintyMs)
                report.commandLagMs?.let { put("command_lag_ms", it) }
                report.startSkewMs?.let { put("start_skew_ms", it) }
                report.driftMs?.let { put("drift_ms", it) }
            },
        )
    }

    private suspend fun postJson(path: String, value: JsonObject): JsonObject {
        val body = value.toString().toRequestBody(JSON_MEDIA_TYPE)
        val (status, payload) = executeAuthorized(baseUrl.trimEnd('/') + path) { url, bearer ->
            Request.Builder().url(url).header("Authorization", bearer).post(body).build()
        }
        if (status == 401 || status == 403) throw SecurityException("WAVE_AUTH_REQUIRED")
        check(status in 200..299) { "WAVE_REQUEST_FAILED" }
        check(payload.length <= MAX_SNAPSHOT_CHARS) { "WAVE_RESPONSE_TOO_LARGE" }
        return if (payload.isBlank()) buildJsonObject {} else Json.parseToJsonElement(payload).jsonObject
    }

    private suspend fun executeAuthorized(
        url: String,
        request: (String, String) -> Request,
    ): Pair<Int, String> {
        var access = sessionCredentials.access(profileId)
        try {
            var result = executeOnce(request(url, access.bearer()))
            if (result.first == 401) {
                val rejectedGeneration = access.generation
                access.close()
                access = sessionCredentials.refreshAfterRejection(profileId, rejectedGeneration)
                result = executeOnce(request(url, access.bearer()))
            }
            return result
        } finally {
            access.close()
        }
    }

    private fun executeOnce(request: Request): Pair<Int, String> =
        client.newCall(request).execute().use { it.code to it.body.string() }

    private fun webSocketRequest(url: String): Request {
        val access = runBlocking { sessionCredentials.access(profileId) }
        return try {
            Request.Builder().url(url).header("Authorization", access.bearer()).build()
        } finally {
            access.close()
        }
    }

    private fun SessionAccess.bearer(): String =
        "Bearer " + token.toString(StandardCharsets.UTF_8)
    private fun decodeSnapshot(value: JsonObject): WaveSnapshot {
        val entries = (value["entries"] ?: value["queue"])?.jsonArray?.map { element ->
            val entry = element.jsonObject
            WaveSnapshotEntry(
                entry.getValue("queue_entry_id").jsonPrimitive.content,
                entry.getValue("recording_id").jsonPrimitive.content,
                entry.getValue("position").jsonPrimitive.long,
                entry["local_track_ref_id"]?.jsonPrimitive?.contentOrNull,
                entry["ready"]?.jsonPrimitive?.booleanOrNull ?: false,
            )
        }.orEmpty()
        val preflight = value["preflight"]?.jsonObject?.mapValues { (_, element) ->
            runCatching { WaveAvailability.valueOf(element.jsonPrimitive.content) }
                .getOrDefault(WaveAvailability.UNAVAILABLE)
        }.orEmpty()
        return WaveSnapshot(
            value.getValue("room_id").jsonPrimitive.content,
            profileId.value,
            value["room_epoch"]?.jsonPrimitive?.contentOrNull ?: "1",
            value["queue_version"]?.jsonPrimitive?.longOrNull
                ?: value["version"]?.jsonPrimitive?.longOrNull
                ?: 1,
            value["role"]?.jsonPrimitive?.contentOrNull ?: "MEMBER",
            value["state"]?.jsonPrimitive?.contentOrNull ?: "OPEN",
            value["sequence"]?.jsonPrimitive?.longOrNull
                ?: value["command_sequence"]?.jsonPrimitive?.longOrNull
                ?: 0,
            entries,
            preflight,
            value["room_code"]?.jsonPrimitive?.contentOrNull?.ifBlank { null },
        )
    }

    private companion object {
        const val MAX_SNAPSHOT_CHARS = 262_144
        val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}
