package app.autplay.application.social

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.data.security.SessionRequiredException
import app.autplay.data.network.withAutPlayRedirectPolicy
import app.autplay.data.network.readCancellable
import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import java.time.Duration
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Bounded social transport: no graph, card, or response body is logged or persisted. */
class OkHttpSocialPort(
    private val apiBaseUrl: String,
    private val credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient(),
    private val m5Rotation: M5SessionRotationClient? = null,
) : SocialPort {
    override suspend fun ownPublicId(profileId: ServerProfileId) = request(
        profileId, "GET", "/social/public-id", null, MAX_SETTINGS_RESPONSE_CHARS,
    ) { root ->
        when (root.requiredString("status")) {
            "unregistered" -> { require(root["public_id"] == JsonNull); null }
            "confirmed" -> root.requiredString("public_id").also(::requireCanonicalPublicId)
            else -> error("unsupported public ID state")
        }
    }
    override suspend fun registerPublicId(
        profileId: ServerProfileId,
        operationId: String,
        publicId: String,
    ): SocialResult<PublicIdRegistrationReceipt> {
        validateUuid(operationId)
        requireCanonicalPublicId(publicId)
        return request(profileId, "PUT", "/social/public-id", buildJsonObject {
            put("operation_id", operationId); put("public_id", publicId)
        }, MAX_SETTINGS_RESPONSE_CHARS) { root ->
            require(root.requiredString("status") == "confirmed")
            PublicIdRegistrationReceipt(root.requiredString("public_id")).also { require(it.publicId == publicId) }
        }
    }
    override suspend fun lookupPublicId(profileId: ServerProfileId, publicId: String): SocialResult<ContactCard> {
        requireCanonicalPublicId(publicId)
        return request(profileId, "GET", "/social/accounts/by-public-id/$publicId", null, MAX_SETTINGS_RESPONSE_CHARS, ::card)
    }
    private val client = client.newBuilder().cache(null).callTimeout(Duration.ofSeconds(30)).build().withAutPlayRedirectPolicy()
    private val sessionCredentials = RefreshingSessionCredentials(apiBaseUrl.trimEnd('/'), credentials, this.client, m5Rotation = m5Rotation)

    override suspend fun contactCard(profileId: ServerProfileId) = request(profileId, "GET", "/social/contact-card", null) { card(it) }
    override suspend fun snapshot(profileId: ServerProfileId) = request(profileId, "GET", "/social/snapshot", null) { snapshot(it) }
    override suspend fun friendshipCommand(profileId: ServerProfileId, command: FriendshipCommand) = request(profileId, "POST", "/social/friendships/commands", buildJsonObject {
        put("operation_id", command.operationId); put("action", command.action.name); command.targetAccountId?.let { put("target_account_id", it) }; command.contactCard?.let { put("contact_card", it) }
    }) { Unit }
    override suspend fun updatePresence(profileId: ServerProfileId, operationId: String, settings: PresenceSettings) = request(profileId, "PUT", "/social/presence/settings", buildJsonObject {
        put("operation_id", operationId); put("friend_presence_visibility_enabled", settings.friendsCanSeePresence); put("room_activity_sharing_enabled", settings.shareRoomActivity); put("invite_availability_enabled", settings.availableToInvite)
    }) { presence(it) }
    override suspend fun profileStatisticsSettings(profileId: ServerProfileId) = request(
        profileId,
        "GET",
        "/social/profile-statistics/settings",
        null,
        MAX_SETTINGS_RESPONSE_CHARS,
    ) { profileStatisticsSettings(it) }
    override suspend fun updateProfileStatisticsSettings(
        profileId: ServerProfileId,
        operationId: String,
        expectedRevision: Long,
        enabled: Boolean,
    ) = request(
        profileId,
        "PUT",
        "/social/profile-statistics/settings",
        buildJsonObject {
            put("operation_id", operationId)
            put("expected_revision", expectedRevision)
            put("friends_can_view_statistics", enabled)
        },
        MAX_SETTINGS_RESPONSE_CHARS,
    ) { profileStatisticsSettingsReceipt(it, operationId) }
    override suspend fun friendProfileStatistics(
        profileId: ServerProfileId,
        friendAccountId: String,
    ): SocialResult<SharedProfileStatistics> {
        validateUuid(friendAccountId)
        return request(
            profileId,
            "GET",
            "/social/friends/$friendAccountId/profile-statistics",
            null,
            MAX_PROFILE_STATISTICS_RESPONSE_CHARS,
        ) { sharedProfileStatistics(it) }
    }
    override suspend fun heartbeat(profileId: ServerProfileId, operationId: String) = request(profileId, "POST", "/social/presence/heartbeat", buildJsonObject { put("operation_id", operationId) }) { Unit }
    override suspend fun createRoomInvitation(profileId: ServerProfileId, roomId: String, targetAccountId: String, operationId: String) = request(profileId, "POST", "/social/room-invitations", buildJsonObject { put("operation_id", operationId); put("room_id", roomId); put("target_account_id", targetAccountId) }) { invitation(it) }
    override suspend fun cancelRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String) = request(profileId, "POST", "/social/room-invitations/$invitationId/cancel", buildJsonObject { put("operation_id", operationId) }) { invitation(it) }
    override suspend fun acceptRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String) = request(profileId, "POST", "/social/room-invitations/$invitationId/accept", buildJsonObject { put("operation_id", operationId) }) { root -> AcceptedRoomInvitation(root.requiredString("room_id")) }

    private suspend fun <T> request(
        profileId: ServerProfileId,
        method: String,
        path: String,
        body: JsonObject?,
        maxResponseChars: Int = MAX_RESPONSE_CHARS,
        decode: (JsonObject) -> T,
    ): SocialResult<T> = withContext(Dispatchers.IO) { try {
        var access = sessionCredentials.access(profileId)
        try {
            var result = execute(method, path, body, access, maxResponseChars)
            if (result.first == 401) { val generation = access.generation; access.close(); access = sessionCredentials.refreshAfterRejection(profileId, generation); result = execute(method, path, body, access, maxResponseChars) }
            val root = result.second
            if (result.first !in 200..299) return@withContext SocialResult.Failure(errorCode(root))
            SocialResult.Success(decode(root))
        } finally { access.close() }
    } catch (_: SessionRequiredException) { SocialResult.Failure("auth_attention_required") }
    catch (cancelled: CancellationException) { throw cancelled }
    catch (_: Exception) { SocialResult.Failure("server_unavailable") } }

    private suspend fun execute(
        method: String,
        path: String,
        value: JsonObject?,
        access: SessionAccess,
        maxResponseChars: Int,
    ): Pair<Int, JsonObject> {
        val url = apiBaseUrl.trimEnd('/') + path
        val builder = Request.Builder().url(url).header("Authorization", "Bearer " + access.token.toString(StandardCharsets.UTF_8)).header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
        when (method) { "GET" -> builder.get(); "PUT" -> builder.put(requireNotNull(value).toString().toRequestBody(JSON)); else -> builder.post(requireNotNull(value).toString().toRequestBody(JSON)) }
        return client.newCall(builder.build()).readCancellable { response ->
            val source = response.body.source()
            require(!source.request(maxResponseChars.toLong() + 1))
            val raw = source.readUtf8()
            response.code to (if (raw.isBlank()) buildJsonObject {} else Json.parseToJsonElement(raw).jsonObject)
        }
    }

    private fun card(root: JsonObject) = ContactCard(root.requiredString("server_instance_id"), root.requiredString("account_id"), root.requiredString("display_name_hint"), root.requiredString("issued_at"), root.requiredString("expires_at"), root.requiredString("signature_b64url"))
    private fun snapshot(root: JsonObject): SocialSnapshot {
        val friends = ArrayList<FriendSummary>()
        fun people(name: String, status: FriendshipStatus) { root[name]?.jsonArray?.take(500)?.forEach { entry -> entry.jsonObject.let { friends += FriendSummary(it.requiredString("account_id"), it["display_name_hint"]?.jsonPrimitive?.contentOrNull?.take(80), status, it["presence"]?.jsonPrimitive?.contentOrNull?.let(AggregatePresence::valueOf) ?: AggregatePresence.OFFLINE) } } }
        people("friends", FriendshipStatus.FRIEND); people("incoming_requests", FriendshipStatus.PENDING_INBOUND); people("outgoing_requests", FriendshipStatus.PENDING_OUTBOUND); people("blocked", FriendshipStatus.BLOCKED)
        return SocialSnapshot(friends, invitations(root, "sent_room_invitations"), invitations(root, "received_room_invitations"), presence(root["presence_settings"]?.jsonObject ?: buildJsonObject {}))
    }
    private fun presence(root: JsonObject): PresenceSettings = PresenceSettings(root["friend_presence_visibility_enabled"]?.jsonPrimitive?.booleanOrNull ?: false, root["room_activity_sharing_enabled"]?.jsonPrimitive?.booleanOrNull ?: false, root["invite_availability_enabled"]?.jsonPrimitive?.booleanOrNull ?: false)
    private fun profileStatisticsSettings(root: JsonObject): ProfileStatisticsSettings {
        requireSchemaV1(root)
        return ProfileStatisticsSettings(
            enabled = requireNotNull(root["friends_can_view_statistics"]?.jsonPrimitive?.booleanOrNull),
            revision = requireNotNull(root["revision"]?.jsonPrimitive?.longOrNull),
        )
    }
    private fun profileStatisticsSettingsReceipt(
        root: JsonObject,
        expectedOperationId: String,
    ): ProfileStatisticsSettingsReceipt {
        requireSchemaV1(root)
        val operationId = root.requiredString("operation_id")
        require(operationId == expectedOperationId) { "mismatched operation receipt" }
        return ProfileStatisticsSettingsReceipt(operationId, profileStatisticsSettings(root))
    }
    private fun sharedProfileStatistics(root: JsonObject): SharedProfileStatistics {
        requireSchemaV1(root)
        val windows = requireNotNull(root["windows"]) { "missing windows" }.jsonArray.map { value ->
            val window = value.jsonObject
            SharedProfileStatisticsWindow(
                kind = SharedStatisticsWindowKind.fromRaw(window.requiredString("window")),
                playSessionCount = window.requiredLong("play_session_count"),
                listenedMs = window.requiredLong("listened_ms"),
                uniqueTrackCount = window.requiredLong("unique_track_count"),
            )
        }
        return SharedProfileStatistics(root.requiredString("through_utc_date"), windows)
    }
    private fun invitations(root: JsonObject, name: String) = root[name]?.jsonArray?.take(100)?.map { invitation(it.jsonObject) }.orEmpty()
    private fun invitation(root: JsonObject) = RoomInvitationSummary(root.requiredString("invitation_id"), root.requiredString("room_id"), root.requiredString("room_epoch"), RoomInvitationStatus.valueOf(root.requiredString("state")), root.requiredString("expires_at"))
    private fun errorCode(root: JsonObject): String = root["error"]?.jsonObject?.get("code")?.jsonPrimitive?.contentOrNull ?: root["error_code"]?.jsonPrimitive?.contentOrNull ?: "server_unavailable"
    private fun requireSchemaV1(root: JsonObject) {
        require(root["schema_version"]?.jsonPrimitive?.longOrNull == 1L) { "unsupported schema" }
    }
    private fun JsonObject.requiredString(name: String): String = requireNotNull(this[name]) { "missing $name" }.jsonPrimitive.content
    private fun JsonObject.requiredLong(name: String): Long = requireNotNull(
        requireNotNull(this[name]) { "missing $name" }.jsonPrimitive.longOrNull,
    ) { "invalid $name" }
    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
        const val MAX_RESPONSE_CHARS = 131_072
        const val MAX_SETTINGS_RESPONSE_CHARS = 4_096
        const val MAX_PROFILE_STATISTICS_RESPONSE_CHARS = 2_048
    }
}
