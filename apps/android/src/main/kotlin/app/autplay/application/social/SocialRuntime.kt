package app.autplay.application.social

import app.autplay.domain.ServerProfileId
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Volatile social orchestration. It intentionally does not cache the social graph, contact-card
 * signature, presence, statistics policy, or friend aggregate in Room/DataStore.
 */
class SocialRuntime(
    private val profileId: ServerProfileId,
    private val port: SocialPort,
    private val scope: CoroutineScope,
    private val nowMs: () -> Long = System::currentTimeMillis,
    private val onAcceptedRoom: (String) -> Unit = {},
    initialPublicIdCandidate: String? = null,
) {
    private val mutableState = MutableStateFlow(SocialRuntimeState(
        publicIdRegistration = initialPublicIdCandidate?.let(::normalizeSocialPublicId)
            ?.let { PublicIdRegistrationState.Pending(it) } ?: PublicIdRegistrationState.Missing,
    ))
    val state: StateFlow<SocialRuntimeState> = mutableState.asStateFlow()
    private var lastHeartbeatMs = Long.MIN_VALUE
    private var friendStatisticsGeneration = 0L
    private var friendStatisticsJob: Job? = null
    private var lookupGeneration = 0L
    private var lookupJob: Job? = null

    fun load(): Job {
        loadPublicId()
        return launchSnapshot { port.snapshot(profileId) }
    }

    /** The server's existing name wins over any local candidate; a confirmed name is never renamed. */
    fun loadPublicId() {
        if (mutableState.value.publicIdLoading) return
        mutableState.update { it.copy(publicIdLoading = true, publicIdErrorCode = null) }
        scope.launch {
            when (val result = port.ownPublicId(profileId)) {
                is SocialResult.Success -> {
                    val registration = mutableState.value.publicIdRegistration
                    val candidate = registration.publicIdOrNull()
                    mutableState.update {
                        it.copy(
                            publicIdLoading = false,
                            publicIdRegistration = result.value?.let { confirmed -> PublicIdRegistrationState.Confirmed(confirmed) }
                                ?: if (registration is PublicIdRegistrationState.Conflict) registration
                                else candidate?.let { pending -> PublicIdRegistrationState.Pending(pending) } ?: PublicIdRegistrationState.Missing,
                            publicIdErrorCode = null,
                        )
                    }
                    if (result.value == null && candidate != null && registration !is PublicIdRegistrationState.Conflict) registerPublicId(candidate)
                }
                is SocialResult.Failure -> mutableState.update {
                    it.copy(publicIdLoading = false, publicIdErrorCode = result.code)
                }
            }
        }
    }

    /** The host first persists this candidate; a network failure preserves Pending, never Confirmed. */
    fun registerPublicId(input: String) {
        val publicId = normalizeSocialPublicId(input)
        if (publicId == null) {
            mutableState.update { it.copy(publicIdErrorCode = "public_id_invalid") }
            return
        }
        if (mutableState.value.publicIdLoading || mutableState.value.publicIdRegistration is PublicIdRegistrationState.Confirmed) return
        mutableState.update {
            it.copy(publicIdRegistration = PublicIdRegistrationState.Pending(publicId), publicIdLoading = true, publicIdErrorCode = null)
        }
        scope.launch {
            when (val result = port.registerPublicId(profileId, uuid(), publicId)) {
                is SocialResult.Success -> mutableState.update {
                    it.copy(
                        publicIdLoading = false,
                        publicIdRegistration = if (result.value.publicId == publicId) PublicIdRegistrationState.Confirmed(publicId) else it.publicIdRegistration,
                        publicIdErrorCode = if (result.value.publicId == publicId) null else "server_unavailable",
                    )
                }
                is SocialResult.Failure -> {
                    mutableState.update {
                        it.copy(
                            publicIdLoading = false,
                            publicIdRegistration = if (result.code == "public_id_taken") PublicIdRegistrationState.Conflict(publicId) else it.publicIdRegistration,
                            publicIdErrorCode = if (result.code == "public_id_taken") null else result.code,
                        )
                    }
                    if (result.code == "public_id_already_registered") loadPublicId()
                }
            }
        }
    }

    fun lookupPublicId(input: String) {
        val publicId = normalizeSocialPublicId(input) ?: return
        val generation = ++lookupGeneration
        lookupJob?.cancel()
        mutableState.update { it.copy(publicIdLookup = PublicIdLookupState.Loading(publicId)) }
        lookupJob = scope.launch {
            val result = port.lookupPublicId(profileId, publicId)
            if (generation == lookupGeneration) mutableState.update {
                it.copy(publicIdLookup = when (result) {
                    is SocialResult.Success -> PublicIdLookupState.Found(publicId, result.value)
                    is SocialResult.Failure -> PublicIdLookupState.Unavailable(publicId, result.code)
                })
            }
        }
    }

    fun clearPublicIdLookup() {
        lookupGeneration += 1
        lookupJob?.cancel()
        lookupJob = null
        mutableState.update { it.copy(publicIdLookup = PublicIdLookupState.Idle) }
    }

    fun sendFoundFriendRequest() {
        if (mutableState.value.loading) return
        val found = mutableState.value.publicIdLookup as? PublicIdLookupState.Found ?: return
        if (mutableState.value.snapshot.friends.any { it.accountId == found.contactCard.accountId }) return
        command(FriendshipCommand(uuid(), FriendshipAction.SEND_REQUEST, contactCard = found.contactCard.asJson()))
    }
    fun loadContactCard() = scope.launch {
        mutableState.value = mutableState.value.copy(loading = true, errorCode = null)
        when (val result = port.contactCard(profileId)) {
            is SocialResult.Success -> mutableState.value = mutableState.value.copy(loading = false, contactCard = result.value, errorCode = null)
            is SocialResult.Failure -> mutableState.value = mutableState.value.copy(loading = false, errorCode = result.code)
        }
    }
    fun importContactCard(value: String) {
        val card = parseSocialContactCardInput(value)
        if (card == null) { mutableState.value = mutableState.value.copy(errorCode = "friend_request_unavailable"); return }
        command(FriendshipCommand(uuid(), FriendshipAction.SEND_REQUEST, contactCard = card.asJson()))
    }
    fun command(command: FriendshipCommand) = launchMutation {
        if (command.action == FriendshipAction.REMOVE_FRIEND || command.action == FriendshipAction.BLOCK_USER) {
            command.targetAccountId?.let(::clearFriendProfileStatisticsFor)
        }
        if (command.action == FriendshipAction.BLOCK_USER) {
            val found = mutableState.value.publicIdLookup as? PublicIdLookupState.Found
            if (found?.contactCard?.accountId == command.targetAccountId) clearPublicIdLookup()
        }
        port.friendshipCommand(profileId, command)
    }
    fun acceptFriend(accountId: String) = command(FriendshipCommand(uuid(), FriendshipAction.ACCEPT_REQUEST, targetAccountId = accountId))
    fun declineFriend(accountId: String) = command(FriendshipCommand(uuid(), FriendshipAction.DECLINE_REQUEST, targetAccountId = accountId))
    fun cancelFriendRequest(accountId: String) = command(FriendshipCommand(uuid(), FriendshipAction.CANCEL_REQUEST, targetAccountId = accountId))
    fun removeFriend(accountId: String) = command(FriendshipCommand(uuid(), FriendshipAction.REMOVE_FRIEND, targetAccountId = accountId))
    fun block(accountId: String) = command(FriendshipCommand(uuid(), FriendshipAction.BLOCK_USER, targetAccountId = accountId))
    fun unblock(accountId: String) = command(FriendshipCommand(uuid(), FriendshipAction.UNBLOCK_USER, targetAccountId = accountId))
    fun setPresence(settings: PresenceSettings) = launchMutation { port.updatePresence(profileId, uuid(), settings) }
    fun loadProfileStatisticsSettings() = scope.launch {
        mutableState.value = mutableState.value.copy(
            statisticsSettings = ProfileStatisticsSettingsState.Loading,
            statisticsSettingsErrorCode = null,
        )
        when (val result = port.profileStatisticsSettings(profileId)) {
            is SocialResult.Success -> mutableState.value = mutableState.value.copy(
                statisticsSettings = result.value.confirmed(),
                statisticsSettingsErrorCode = null,
            )
            is SocialResult.Failure -> mutableState.value = mutableState.value.copy(
                statisticsSettings = ProfileStatisticsSettingsState.Unavailable,
                statisticsSettingsErrorCode = result.code,
            )
        }
    }
    fun setProfileStatisticsVisibility(enabled: Boolean) = scope.launch {
        val confirmed = when (val current = mutableState.value.statisticsSettings) {
            is ProfileStatisticsSettingsState.Confirmed -> current
            else -> return@launch
        }
        mutableState.value = mutableState.value.copy(
            statisticsSettings = ProfileStatisticsSettingsState.Updating(confirmed, enabled),
            statisticsSettingsErrorCode = null,
        )
        val operationId = uuid()
        when (val result = port.updateProfileStatisticsSettings(
            profileId = profileId,
            operationId = operationId,
            expectedRevision = confirmed.revision,
            enabled = enabled,
        )) {
            is SocialResult.Success -> mutableState.value = mutableState.value.copy(
                statisticsSettings = if (result.value.operationId == operationId) result.value.settings.confirmed() else confirmed,
                statisticsSettingsErrorCode = if (result.value.operationId == operationId) null else "server_unavailable",
            )
            is SocialResult.Failure -> mutableState.value = mutableState.value.copy(
                // Preserve the last server-confirmed value. In particular, a failed OFF stays ON.
                statisticsSettings = confirmed,
                statisticsSettingsErrorCode = result.code,
            )
        }
    }
    fun loadFriendProfileStatistics(accountId: String) {
        validateUuid(accountId)
        val generation = ++friendStatisticsGeneration
        friendStatisticsJob?.cancel()
        mutableState.value = mutableState.value.copy(
            friendStatistics = FriendProfileStatisticsState.Loading(accountId),
        )
        friendStatisticsJob = scope.launch {
            when (val result = port.friendProfileStatistics(profileId, accountId)) {
                is SocialResult.Success -> if (canAcceptFriendStatistics(generation, accountId)) {
                    mutableState.value = mutableState.value.copy(
                        friendStatistics = FriendProfileStatisticsState.Visible(accountId, result.value),
                    )
                }
                is SocialResult.Failure -> if (canAcceptFriendStatistics(generation, accountId)) {
                    // A denial and an absent/private/revoked target are deliberately indistinguishable.
                    mutableState.value = mutableState.value.copy(
                        friendStatistics = FriendProfileStatisticsState.Unavailable(accountId),
                    )
                }
            }
        }
    }
    fun clearFriendProfileStatistics() {
        friendStatisticsGeneration++
        friendStatisticsJob?.cancel()
        friendStatisticsJob = null
        mutableState.value = mutableState.value.copy(friendStatistics = FriendProfileStatisticsState.Idle)
    }
    /** A foreground host calls this while its authenticated M5 binding is active. */
    fun heartbeatWhileActive() {
        if (lastHeartbeatMs != Long.MIN_VALUE && nowMs() - lastHeartbeatMs < HEARTBEAT_MIN_INTERVAL_MS) return
        lastHeartbeatMs = nowMs()
        scope.launch { when (val result = port.heartbeat(profileId, uuid())) { is SocialResult.Failure -> mutableState.value = mutableState.value.copy(errorCode = result.code); is SocialResult.Success -> Unit } }
    }
    fun createRoomInvitation(roomId: String, targetAccountId: String) = launchMutation { port.createRoomInvitation(profileId, roomId, targetAccountId, uuid()) }
    fun cancelRoomInvitation(invitationId: String) = launchMutation { port.cancelRoomInvitation(profileId, invitationId, uuid()) }
    fun acceptRoomInvitation(invitationId: String) = scope.launch {
        mutableState.value = mutableState.value.copy(loading = true, errorCode = null)
        when (val result = port.acceptRoomInvitation(profileId, invitationId, uuid())) {
            is SocialResult.Success -> { mutableState.value = mutableState.value.copy(loading = false, acceptedRoomId = result.value.roomId); onAcceptedRoom(result.value.roomId); load() }
            is SocialResult.Failure -> mutableState.value = mutableState.value.copy(loading = false, errorCode = result.code)
        }
    }

    private fun launchSnapshot(call: suspend () -> SocialResult<SocialSnapshot>) = scope.launch {
        mutableState.value = mutableState.value.copy(loading = true, errorCode = null)
        when (val result = call()) {
            is SocialResult.Success -> {
                val current = mutableState.value
                val selectedAccountId = current.friendStatistics.accountIdOrNull()
                val friendStillPresent = selectedAccountId == null || result.value.friends.any {
                    it.accountId == selectedAccountId && it.status == FriendshipStatus.FRIEND
                }
                mutableState.value = current.copy(
                    loading = false,
                    snapshot = result.value,
                    snapshotLoaded = true,
                    friendStatistics = if (friendStillPresent) current.friendStatistics else FriendProfileStatisticsState.Idle,
                    errorCode = null,
                )
            }
            is SocialResult.Failure -> mutableState.value = mutableState.value.copy(loading = false, errorCode = result.code)
        }
    }
    private fun launchMutation(call: suspend () -> SocialResult<*>) = scope.launch {
        mutableState.value = mutableState.value.copy(loading = true, errorCode = null)
        when (val result = call()) {
            is SocialResult.Success -> { mutableState.value = mutableState.value.copy(loading = false); load() }
            is SocialResult.Failure -> mutableState.value = mutableState.value.copy(loading = false, errorCode = result.code)
        }
    }
    private fun clearFriendProfileStatisticsFor(accountId: String) {
        if (mutableState.value.friendStatistics.accountIdOrNull() == accountId) clearFriendProfileStatistics()
    }
    private fun canAcceptFriendStatistics(generation: Long, accountId: String): Boolean =
        generation == friendStatisticsGeneration &&
            mutableState.value.snapshot.friends.any { it.accountId == accountId && it.status == FriendshipStatus.FRIEND }
    private fun ProfileStatisticsSettings.confirmed() = ProfileStatisticsSettingsState.Confirmed(enabled, revision)
    private fun FriendProfileStatisticsState.accountIdOrNull(): String? = when (this) {
        FriendProfileStatisticsState.Idle -> null
        is FriendProfileStatisticsState.Loading -> accountId
        is FriendProfileStatisticsState.Visible -> accountId
        is FriendProfileStatisticsState.Unavailable -> accountId
    }
    private fun uuid() = UUID.randomUUID().toString()
    private companion object { const val HEARTBEAT_MIN_INTERVAL_MS = 30_000L }
}

data class SocialRuntimeState(
    val loading: Boolean = false,
    val snapshot: SocialSnapshot = SocialSnapshot(),
    val contactCard: ContactCard? = null,
    val acceptedRoomId: String? = null,
    val statisticsSettings: ProfileStatisticsSettingsState = ProfileStatisticsSettingsState.Unavailable,
    val statisticsSettingsErrorCode: String? = null,
    val friendStatistics: FriendProfileStatisticsState = FriendProfileStatisticsState.Idle,
    /** Stable, non-personal error code for UI copy. Prior server state remains intact on failure. */
    val errorCode: String? = null,
    /** An empty default is not a confirmed empty friend list. This flag is volatile as well. */
    val snapshotLoaded: Boolean = false,
    val publicIdRegistration: PublicIdRegistrationState = PublicIdRegistrationState.Missing,
    val publicIdLoading: Boolean = false,
    val publicIdErrorCode: String? = null,
    val publicIdLookup: PublicIdLookupState = PublicIdLookupState.Idle,
)
