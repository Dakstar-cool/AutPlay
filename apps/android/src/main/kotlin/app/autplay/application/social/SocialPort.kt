package app.autplay.application.social

import app.autplay.domain.ServerProfileId
import java.util.UUID
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject

/** Small authenticated boundary for S1C. Social state deliberately stays server-authoritative. */
interface SocialPort {
    suspend fun contactCard(profileId: ServerProfileId): SocialResult<ContactCard>
    suspend fun snapshot(profileId: ServerProfileId): SocialResult<SocialSnapshot>
    suspend fun friendshipCommand(profileId: ServerProfileId, command: FriendshipCommand): SocialResult<Unit>
    suspend fun updatePresence(profileId: ServerProfileId, operationId: String, settings: PresenceSettings): SocialResult<PresenceSettings>
    suspend fun profileStatisticsSettings(profileId: ServerProfileId): SocialResult<ProfileStatisticsSettings>
    suspend fun updateProfileStatisticsSettings(
        profileId: ServerProfileId,
        operationId: String,
        expectedRevision: Long,
        enabled: Boolean,
    ): SocialResult<ProfileStatisticsSettingsReceipt>
    suspend fun friendProfileStatistics(
        profileId: ServerProfileId,
        friendAccountId: String,
    ): SocialResult<SharedProfileStatistics>
    suspend fun heartbeat(profileId: ServerProfileId, operationId: String): SocialResult<Unit>
    suspend fun createRoomInvitation(profileId: ServerProfileId, roomId: String, targetAccountId: String, operationId: String): SocialResult<RoomInvitationSummary>
    suspend fun cancelRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): SocialResult<RoomInvitationSummary>
    suspend fun acceptRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): SocialResult<AcceptedRoomInvitation>
}

sealed interface SocialResult<out T> {
    data class Success<T>(val value: T) : SocialResult<T>
    data class Failure(val code: String) : SocialResult<Nothing>
}

data class ContactCard(
    val serverInstanceId: String,
    val accountId: String,
    val displayNameHint: String,
    val issuedAt: String,
    val expiresAt: String,
    /** Volatile share/import material. It is never written to Android persistence. */
    val signatureB64Url: String,
) {
    init { validateUuid(serverInstanceId); validateUuid(accountId); require(displayNameHint.length in 1..120); require(signatureB64Url.length in 16..512) }
    fun asJson(): JsonObject = kotlinx.serialization.json.buildJsonObject {
        put("server_instance_id", JsonPrimitive(serverInstanceId)); put("account_id", JsonPrimitive(accountId))
        put("display_name_hint", JsonPrimitive(displayNameHint))
        put("issued_at", JsonPrimitive(issuedAt)); put("expires_at", JsonPrimitive(expiresAt)); put("signature_b64url", JsonPrimitive(signatureB64Url))
    }
}

enum class FriendshipAction { SEND_REQUEST, ACCEPT_REQUEST, DECLINE_REQUEST, CANCEL_REQUEST, REMOVE_FRIEND, BLOCK_USER, UNBLOCK_USER }
data class FriendshipCommand(val operationId: String, val action: FriendshipAction, val targetAccountId: String? = null, val contactCard: JsonObject? = null) {
    init {
        validateUuid(operationId)
        if (action == FriendshipAction.SEND_REQUEST) require(contactCard != null && targetAccountId == null)
        else require(targetAccountId != null && contactCard == null).also { validateUuid(targetAccountId) }
    }
}

data class PresenceSettings(val friendsCanSeePresence: Boolean, val shareRoomActivity: Boolean, val availableToInvite: Boolean)
data class ProfileStatisticsSettings(val enabled: Boolean, val revision: Long) {
    init { require(revision >= 0) }
}
data class ProfileStatisticsSettingsReceipt(
    val operationId: String,
    val settings: ProfileStatisticsSettings,
) { init { validateUuid(operationId) } }

sealed interface ProfileStatisticsSettingsState {
    data object Loading : ProfileStatisticsSettingsState
    data class Confirmed(val enabled: Boolean, val revision: Long) : ProfileStatisticsSettingsState {
        init { require(revision >= 0) }
    }
    data class Updating(
        val confirmed: Confirmed,
        val requestedEnabled: Boolean,
    ) : ProfileStatisticsSettingsState
    data object Unavailable : ProfileStatisticsSettingsState
}

sealed interface SharedStatisticsWindowKind {
    val raw: String

    data object Last7CompleteDays : SharedStatisticsWindowKind {
        override val raw = "LAST_7_COMPLETE_DAYS"
    }
    data object Last30CompleteDays : SharedStatisticsWindowKind {
        override val raw = "LAST_30_COMPLETE_DAYS"
    }
    data object Last365CompleteDays : SharedStatisticsWindowKind {
        override val raw = "LAST_365_COMPLETE_DAYS"
    }
    data class Unknown(override val raw: String) : SharedStatisticsWindowKind {
        init { require(raw.isNotBlank() && raw.length <= 80) }
    }

    companion object {
        fun fromRaw(raw: String): SharedStatisticsWindowKind = when (raw) {
            Last7CompleteDays.raw -> Last7CompleteDays
            Last30CompleteDays.raw -> Last30CompleteDays
            Last365CompleteDays.raw -> Last365CompleteDays
            else -> Unknown(raw)
        }
    }
}

data class SharedProfileStatisticsWindow(
    val kind: SharedStatisticsWindowKind,
    val playSessionCount: Long,
    val listenedMs: Long,
    val uniqueTrackCount: Long,
) {
    init {
        require(playSessionCount >= 0)
        require(listenedMs >= 0)
        require(uniqueTrackCount >= 0)
    }
}

data class SharedProfileStatistics(
    val throughUtcDate: String,
    val windows: List<SharedProfileStatisticsWindow>,
) {
    init {
        require(throughUtcDate.length == 10)
        require(windows.size <= 8)
        require(windows.map { it.kind.raw }.distinct().size == windows.size)
    }

    fun knownWindows(): List<SharedProfileStatisticsWindow> = windows.filterNot {
        it.kind is SharedStatisticsWindowKind.Unknown
    }
}

sealed interface FriendProfileStatisticsState {
    data object Idle : FriendProfileStatisticsState
    data class Loading(val accountId: String) : FriendProfileStatisticsState
    data class Visible(val accountId: String, val statistics: SharedProfileStatistics) : FriendProfileStatisticsState
    data class Unavailable(val accountId: String) : FriendProfileStatisticsState
}

enum class AggregatePresence { OFFLINE, ONLINE, AVAILABLE_TO_INVITE, IN_ROOM }
enum class FriendshipStatus { PENDING_OUTBOUND, PENDING_INBOUND, FRIEND, BLOCKED }
enum class RoomInvitationStatus { PENDING, ACCEPTED, EXPIRED, CANCELLED, FULL, UNAVAILABLE }
data class FriendSummary(val accountId: String, val displayNameHint: String?, val status: FriendshipStatus, val presence: AggregatePresence = AggregatePresence.OFFLINE) { init { validateUuid(accountId) } }
data class RoomInvitationSummary(val invitationId: String, val roomId: String, val roomEpoch: String, val status: RoomInvitationStatus, val expiresAt: String) { init { validateUuid(invitationId); validateUuid(roomId); require(roomEpoch.length in 1..128) } }
data class SocialSnapshot(val friends: List<FriendSummary> = emptyList(), val sentInvitations: List<RoomInvitationSummary> = emptyList(), val receivedInvitations: List<RoomInvitationSummary> = emptyList(), val presence: PresenceSettings = PresenceSettings(false, false, false)) {
    init { require(friends.size <= 500); require(sentInvitations.size <= 100); require(receivedInvitations.size <= 100); require(friends.map { it.accountId }.distinct().size == friends.size) }
}
data class AcceptedRoomInvitation(val roomId: String) { init { validateUuid(roomId) } }

internal fun validateUuid(value: String) { UUID.fromString(value); require(value.length == 36) }
