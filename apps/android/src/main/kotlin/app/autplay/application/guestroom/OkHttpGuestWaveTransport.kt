package app.autplay.application.guestroom

import app.autplay.application.wave.WaveEvent
import app.autplay.application.wave.WaveClockSample
import app.autplay.application.wave.WavePreflightReport
import app.autplay.application.wave.WaveSnapshot
import app.autplay.application.wave.WaveSnapshotEntry
import app.autplay.application.wave.WaveTimingReport
import app.autplay.application.wave.WaveTransport
import app.autplay.domain.wave.WaveAvailability
import app.autplay.domain.wave.WaveCommand
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.CacheControl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

class GuestRoomTransportException(val code: String) : IllegalStateException(code)

/** Dedicated S1D transport: it sends a guest bearer only to the exact guest API family. */
class OkHttpGuestWaveTransport private constructor(
    serverOrigin: String,
    private val scopedRoomId: String,
    private val snapshotProfileId: String,
    private val sessionBearer: ByteArray,
    private val client: OkHttpClient,
    private val onAuthorityLost: (String) -> Unit,
) : WaveTransport, AutoCloseable {
    private val guestBase = serverOrigin.trimEnd('/') + "/api/v1/wave/guest"

    override suspend fun snapshot(roomId: String): WaveSnapshot {
        requireRoom(roomId)
        return decodeSnapshot(get("/rooms/$roomId/snapshot"))
    }

    override fun connect(
        roomId: String,
        afterSequence: Long,
        roomEpoch: String,
        onEvent: (WaveEvent) -> Unit,
        onFailure: () -> Unit,
    ): AutoCloseable {
        requireRoom(roomId)
        val socketUrl = guestBase.replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://") + "/rooms/$roomId/events"
        val request = Request.Builder()
            .url(socketUrl)
            .header(GUEST_HEADER, bearer())
            .cacheControl(CacheControl.FORCE_NETWORK)
            .build()
        val socket = client.newWebSocket(request, object : WebSocketListener() {
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
                val value = runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull()
                    ?: return
                when (value["type"]?.jsonPrimitive?.contentOrNull) {
                    "snapshot_required", "invalidate", "guest_access_changed" -> {
                        onFailure()
                        return
                    }
                    "event" -> Unit
                    else -> return
                }
                val sequence = value["sequence"]?.jsonPrimitive?.longOrNull ?: return
                val kind = value["kind"]?.jsonPrimitive?.contentOrNull ?: return
                val payload = value["payload"]?.toString().orEmpty()
                onEvent(
                    WaveEvent(
                        value["epoch"]?.jsonPrimitive?.contentOrNull ?: roomEpoch,
                        WaveCommand(sequence, kind, payload),
                    ),
                )
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (response?.code in TERMINAL_HTTP_CODES) onAuthorityLost("guest_unavailable")
                onFailure()
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (code == GUEST_AUTH_CLOSE_CODE) onAuthorityLost("guest_revoked")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (code == GUEST_AUTH_CLOSE_CODE) onAuthorityLost("guest_revoked")
                if (code != 1000) onFailure()
            }
        })
        return AutoCloseable { socket.close(1000, "client-close") }
    }

    override suspend fun leave(roomId: String) {
        requireRoom(roomId)
        post(
            "/rooms/$roomId/leave",
            buildJsonObject { put("operation_id", UUID.randomUUID().toString()) },
        )
    }

    override suspend fun preflight(roomId: String, reports: List<WavePreflightReport>) {
        requireRoom(roomId)
        require(reports.size <= 4) { "GUEST_PREFLIGHT_TOO_LARGE" }
        reports.forEach { report ->
            post(
                "/rooms/$roomId/preflight",
                buildJsonObject {
                    put("queue_entry_id", canonicalUuid(report.queueEntryId))
                    put("recording_id", canonicalUuid(report.recordingId))
                    put("queue_version", report.queueVersion)
                    put("availability", report.availability.serverValue())
                    put("final_ready", report.finalReady)
                },
                expectBody = false,
            )
        }
    }

    override suspend fun timing(roomId: String, report: WaveTimingReport) {
        requireRoom(roomId)
        post(
            "/rooms/$roomId/timing",
            buildJsonObject {
                put("command_sequence", report.commandSequence)
                put("rtt_ms", report.rttMs)
                put("offset_ms", report.offsetMs)
                put("uncertainty_ms", report.uncertaintyMs)
                report.startSkewMs?.let { put("start_skew_ms", it) }
                report.driftMs?.let { put("drift_ms", it) }
            },
            expectBody = false,
        )
    }

    override suspend fun clock(): WaveClockSample {
        val sent = System.currentTimeMillis()
        val value = post(
            "/clock",
            buildJsonObject { put("room_id", scopedRoomId) },
        )
        val received = System.currentTimeMillis()
        return WaveClockSample(
            clientSentMs = sent,
            serverReceivedMs = value.getValue("server_receive_epoch_ms").jsonPrimitive.long,
            serverSentMs = value.getValue("server_send_epoch_ms").jsonPrimitive.long,
            clientReceivedMs = received,
        )
    }

    override fun close() {
        sessionBearer.fill(0)
    }

    private fun get(path: String): JsonObject {
        val request = Request.Builder()
            .url(guestBase + path)
            .header(GUEST_HEADER, bearer())
            .cacheControl(CacheControl.FORCE_NETWORK)
            .get()
            .build()
        return execute(request, expectBody = true)
    }

    private fun post(path: String, body: JsonObject, expectBody: Boolean = true): JsonObject {
        val request = Request.Builder()
            .url(guestBase + path)
            .header(GUEST_HEADER, bearer())
            .header("Cache-Control", "no-store")
            .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
            .build()
        return execute(request, expectBody)
    }

    private fun execute(request: Request, expectBody: Boolean): JsonObject =
        client.newCall(request).execute().use { response ->
            val payload = response.body.string()
            check(payload.length <= MAX_RESPONSE_CHARS) { "GUEST_RESPONSE_TOO_LARGE" }
            if (!response.isSuccessful) {
                val code = errorCode(payload)
                if (response.code in TERMINAL_HTTP_CODES || code in TERMINAL_ERROR_CODES) {
                    onAuthorityLost(code)
                }
                throw GuestRoomTransportException(code)
            }
            if (!expectBody || payload.isBlank()) buildJsonObject {}
            else Json.parseToJsonElement(payload).jsonObject
        }

    private fun decodeSnapshot(value: JsonObject): WaveSnapshot {
        val entries = value["queue"]?.jsonArray?.map { element ->
            val entry = element.jsonObject
            WaveSnapshotEntry(
                canonicalUuid(entry.getValue("queue_entry_id").jsonPrimitive.content),
                canonicalUuid(entry.getValue("recording_id").jsonPrimitive.content),
                entry.getValue("position").jsonPrimitive.long,
                localTrackRefId = null,
                ready = false,
            )
        }.orEmpty()
        val preflight = value["preflight"]?.jsonObject?.mapValues { (_, element) ->
            element.jsonPrimitive.content.toWaveAvailability()
        }.orEmpty()
        return WaveSnapshot(
            roomId = canonicalUuid(value.getValue("room_id").jsonPrimitive.content),
            profileId = snapshotProfileId,
            roomEpoch = value.getValue("room_epoch").jsonPrimitive.content,
            queueVersion = value.getValue("queue_version").jsonPrimitive.long,
            role = "GUEST",
            state = value.getValue("state").jsonPrimitive.content,
            sequence = value["sequence"]?.jsonPrimitive?.longOrNull ?: 0,
            entries = entries,
            preflight = preflight,
        )
    }

    private fun requireRoom(roomId: String) {
        require(canonicalUuid(roomId) == scopedRoomId) { "GUEST_ROOM_SCOPE_DENIED" }
    }

    private fun bearer(): String =
        Base64.getUrlEncoder().withoutPadding().encodeToString(sessionBearer)

    companion object {
        suspend fun redeem(
            document: GuestRoomDocument,
            displayName: String,
            client: OkHttpClient = OkHttpClient(),
            localMediaProfileId: String? = null,
            onAuthorityLost: (String) -> Unit = {},
        ): Pair<RedeemedGuestCapability, OkHttpGuestWaveTransport> {
            val normalizedName = displayName.trim().replace(Regex("\\s+"), " ")
            require(normalizedName.length in 1..40) { "GUEST_NAME_INVALID" }
            val sessionBearer = GuestRoomDocumentCodec.generateBearer()
            try {
                val request = Request.Builder()
                    .url(document.serverOrigin.trimEnd('/') + "/api/v1/wave/guest/redeem")
                    .header("Cache-Control", "no-store")
                    .post(
                        buildJsonObject {
                            put("operation_id", UUID.randomUUID().toString())
                            put("invitation_id", document.invitationId)
                            put("room_id", document.roomId)
                            put("document_bearer", document.bearer())
                            put(
                                "session_bearer",
                                Base64.getUrlEncoder().withoutPadding().encodeToString(sessionBearer),
                            )
                            put("display_name", normalizedName)
                        }.toString().toRequestBody(JSON_MEDIA_TYPE),
                    )
                    .build()
                val root = client.newCall(request).execute().use { response ->
                    val payload = response.body.string()
                    check(payload.length <= MAX_RESPONSE_CHARS) { "GUEST_RESPONSE_TOO_LARGE" }
                    if (!response.isSuccessful) {
                        throw GuestRoomTransportException(errorCode(payload))
                    }
                    Json.parseToJsonElement(payload).jsonObject
                }
                val capability = RedeemedGuestCapability(
                    guestSessionId = canonicalUuid(root.getValue("guest_session_id").jsonPrimitive.content),
                    invitationId = canonicalUuid(root.getValue("invitation_id").jsonPrimitive.content),
                    roomId = canonicalUuid(root.getValue("room_id").jsonPrimitive.content),
                    roomEpoch = root.getValue("room_epoch").jsonPrimitive.content,
                    displayName = root.getValue("display_name").jsonPrimitive.content,
                    expiresAt = java.time.Instant.parse(root.getValue("expires_at").jsonPrimitive.content),
                )
                require(
                    capability.invitationId == document.invitationId &&
                        capability.roomId == document.roomId
                ) { "GUEST_REDEMPTION_SCOPE_INVALID" }
                val profileId = localMediaProfileId
                    ?: "guest:${document.serverInstanceId}:${document.identityEpoch}"
                return capability to OkHttpGuestWaveTransport(
                    serverOrigin = document.serverOrigin,
                    scopedRoomId = document.roomId,
                    snapshotProfileId = profileId,
                    sessionBearer = sessionBearer,
                    client = client,
                    onAuthorityLost = onAuthorityLost,
                )
            } catch (error: Throwable) {
                sessionBearer.fill(0)
                throw error
            }
        }

        private fun errorCode(payload: String): String = runCatching {
            val root = Json.parseToJsonElement(payload).jsonObject
            root["error"]?.jsonObject?.get("code")?.jsonPrimitive?.contentOrNull
                ?: root["code"]?.jsonPrimitive?.contentOrNull
        }.getOrNull() ?: "guest_unavailable"

        private fun canonicalUuid(value: String): String = UUID.fromString(value).toString().also {
            require(it == value.lowercase()) { "GUEST_UUID_INVALID" }
        }

        private fun String.toWaveAvailability(): WaveAvailability = when (this) {
            "LOCAL" -> WaveAvailability.LOCAL_READABLE
            "DOWNLOADED" -> WaveAvailability.DOWNLOADED
            "VAULT_STREAMABLE" -> WaveAvailability.VAULT_STREAMABLE
            else -> WaveAvailability.UNAVAILABLE
        }

        private fun WaveAvailability.serverValue(): String = when (this) {
            WaveAvailability.LOCAL_READABLE -> "LOCAL"
            else -> name
        }

        private const val GUEST_HEADER = "X-AutPlay-Guest-Capability"
        private const val GUEST_AUTH_CLOSE_CODE = 4401
        private const val MAX_RESPONSE_CHARS = 262_144
        private val TERMINAL_HTTP_CODES = setOf(401, 403, 404, 410)
        private val TERMINAL_ERROR_CODES = setOf(
            "guest_expired",
            "guest_revoked",
            "guest_unavailable",
            "room_changed",
        )
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}
