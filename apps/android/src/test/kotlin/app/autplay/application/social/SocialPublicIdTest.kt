package app.autplay.application.social

import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SocialPublicIdTest {
    @Test fun `names are canonical ASCII and cannot be account or device UUIDs`() {
        assertEquals("ptica_1", normalizeSocialPublicId(" @PTICA_1 "))
        assertEquals("a".repeat(24), normalizeSocialPublicId("A".repeat(24)))
        listOf("", "ab", "a".repeat(25), "@@alice", "ali-ce", "alice/name", "птица", "Kel", "alİce", ACCOUNT_ID).forEach {
            assertNull(it, normalizeSocialPublicId(it))
        }
    }

    @Test fun `a name chosen offline remains pending until a server acknowledgement`() {
        val port = PublicIdTestPort()
        val runtime = runtime(port, "@PTICA_1")
        assertEquals(PublicIdRegistrationState.Pending("ptica_1"), runtime.state.value.publicIdRegistration)
        runtime.load()
        assertEquals(PublicIdRegistrationState.Pending("ptica_1"), runtime.state.value.publicIdRegistration)
        assertEquals(0, port.registrations)
        port.own = SocialResult.Success(null)
        port.registered = SocialResult.Success(PublicIdRegistrationReceipt("ptica_1"))
        runtime.load()
        assertEquals(PublicIdRegistrationState.Confirmed("ptica_1"), runtime.state.value.publicIdRegistration)
        assertEquals(1, port.registrations)
    }

    @Test fun `a confirmed name on the server wins over a stale local candidate`() {
        val port = PublicIdTestPort().apply { own = SocialResult.Success("existing_user") }
        val runtime = runtime(port, "different_user")
        runtime.load()
        assertEquals(PublicIdRegistrationState.Confirmed("existing_user"), runtime.state.value.publicIdRegistration)
        assertEquals(0, port.registrations)
        runtime.registerPublicId("rename_attempt")
        assertEquals(0, port.registrations)
    }

    @Test fun `taken ID allows correction and refresh does not silently retry it`() {
        val port = PublicIdTestPort().apply {
            own = SocialResult.Success(null)
            registered = SocialResult.Failure("public_id_taken")
        }
        val runtime = runtime(port, "taken_name")
        runtime.load()
        assertEquals(PublicIdRegistrationState.Conflict("taken_name"), runtime.state.value.publicIdRegistration)
        runtime.load()
        assertEquals(1, port.registrations)
        assertEquals(PublicIdRegistrationState.Conflict("taken_name"), runtime.state.value.publicIdRegistration)
        port.registered = SocialResult.Success(PublicIdRegistrationReceipt("available_name"))
        runtime.registerPublicId("available_name")
        assertEquals(PublicIdRegistrationState.Confirmed("available_name"), runtime.state.value.publicIdRegistration)
    }

    @Test fun `registration that fails or returns another name never confirms the local candidate`() {
        val port = PublicIdTestPort()
        val runtime = runtime(port)
        runtime.registerPublicId("local_name")
        assertEquals(PublicIdRegistrationState.Pending("local_name"), runtime.state.value.publicIdRegistration)
        port.registered = SocialResult.Success(PublicIdRegistrationReceipt("different_name"))
        runtime.registerPublicId("local_name")
        assertEquals(PublicIdRegistrationState.Pending("local_name"), runtime.state.value.publicIdRegistration)
        assertEquals("server_unavailable", runtime.state.value.publicIdErrorCode)
    }

    @Test fun `another device registering first is recovered through the owner view`() {
        val port = PublicIdTestPort().apply {
            own = SocialResult.Success("original_name")
            registered = SocialResult.Failure("public_id_already_registered")
        }
        val runtime = runtime(port, "local_name")
        runtime.registerPublicId("local_name")
        assertEquals(PublicIdRegistrationState.Confirmed("original_name"), runtime.state.value.publicIdRegistration)
    }

    @Test fun `lookup cannot create a friendship or send a request until explicitly instructed`() {
        val port = PublicIdTestPort().apply { found = SocialResult.Success(card) }
        val runtime = runtime(port)
        runtime.lookupPublicId("@ALICE")
        assertEquals("alice", port.lastLookup)
        assertEquals(PublicIdLookupState.Found("alice", card), runtime.state.value.publicIdLookup)
        assertTrue(port.commands.isEmpty())
        runtime.sendFoundFriendRequest()
        val command = port.commands.single()
        assertEquals(FriendshipAction.SEND_REQUEST, command.action)
        assertEquals(card.asJson(), command.contactCard)
        assertNull(command.targetAccountId)
        assertFalse(runtime.state.value.snapshot.friends.any { it.status == FriendshipStatus.FRIEND })
    }

    @Test fun `lookup of an incoming request does not auto accept or send an opposite request`() {
        val port = PublicIdTestPort().apply {
            found = SocialResult.Success(card)
            snapshotResult = SocialResult.Success(SocialSnapshot(friends = listOf(FriendSummary(ACCOUNT_ID, "Alice", FriendshipStatus.PENDING_INBOUND))))
        }
        val runtime = runtime(port)
        runtime.load()
        runtime.lookupPublicId("alice")
        runtime.sendFoundFriendRequest()
        assertTrue(port.commands.isEmpty())
        runtime.acceptFriend(ACCOUNT_ID)
        assertEquals(FriendshipAction.ACCEPT_REQUEST, port.commands.single().action)
    }

    @Test fun `clearing a lookup prevents a late result from restoring a signed card`() = runBlocking {
        val deferred = CompletableDeferred<SocialResult<ContactCard>>()
        val port = PublicIdTestPort().apply { lookupCall = { deferred.await() } }
        val runtime = runtime(port)
        runtime.lookupPublicId("alice")
        runtime.clearPublicIdLookup()
        deferred.complete(SocialResult.Success(card))
        assertEquals(PublicIdLookupState.Idle, runtime.state.value.publicIdLookup)
    }

    @Test fun `crossed pending requests remain distinct and never appear as confirmed friends`() {
        val people = listOf(
            FriendSummary(ACCOUNT_ID, "Alice", FriendshipStatus.PENDING_INBOUND),
            FriendSummary(ACCOUNT_ID, "Alice", FriendshipStatus.PENDING_OUTBOUND),
        )
        val snapshot = SocialSnapshot(friends = people)
        assertEquals(2, snapshot.friends.size)
        assertFalse(snapshot.friends.any { it.status == FriendshipStatus.FRIEND })
        assertTrue(runCatching { SocialSnapshot(friends = people + people.first()) }.isFailure)
    }

    @Test fun `parsing signed card input preserves escaped display names exactly`() {
        val original = card.copy(displayNameHint = "Alice \"A\" \\ studio")
        assertEquals(original, parseSocialContactCardInput(original.asJson().toString()))
        assertNull(parseSocialContactCardInput("x".repeat(4_097)))
        assertNull(parseSocialContactCardInput("{}"))
        assertNull(parseSocialContactCardInput(original.asJson().toString().replace("studio", "studio".repeat(40))))
    }

    private fun runtime(port: SocialPort, candidate: String? = null) = SocialRuntime(
        ServerProfileId("22222222-2222-4222-8222-222222222222"), port, CoroutineScope(Dispatchers.Unconfined),
        initialPublicIdCandidate = candidate,
    )

    private companion object {
        const val ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
        val card = ContactCard("33333333-3333-4333-8333-333333333333", ACCOUNT_ID, "Alice", "2026-10-06T12:00:00Z", "2026-11-05T12:00:00Z", "a".repeat(86))
    }
}

internal class PublicIdTestPort : SocialPort {
    var own: SocialResult<String?> = SocialResult.Failure("server_unavailable")
    var registered: SocialResult<PublicIdRegistrationReceipt> = SocialResult.Failure("server_unavailable")
    var found: SocialResult<ContactCard> = SocialResult.Failure("public_id_not_found")
    var snapshotResult: SocialResult<SocialSnapshot> = SocialResult.Success(SocialSnapshot())
    var lookupCall: (suspend () -> SocialResult<ContactCard>)? = null
    var registrations = 0
    var lastLookup: String? = null
    val commands = mutableListOf<FriendshipCommand>()
    override suspend fun ownPublicId(profileId: ServerProfileId) = own
    override suspend fun registerPublicId(profileId: ServerProfileId, operationId: String, publicId: String): SocialResult<PublicIdRegistrationReceipt> { registrations += 1; return registered }
    override suspend fun lookupPublicId(profileId: ServerProfileId, publicId: String): SocialResult<ContactCard> { lastLookup = publicId; return lookupCall?.invoke() ?: found }
    override suspend fun snapshot(profileId: ServerProfileId) = snapshotResult
    override suspend fun friendshipCommand(profileId: ServerProfileId, command: FriendshipCommand): SocialResult<Unit> { commands += command; return SocialResult.Success(Unit) }
    override suspend fun contactCard(profileId: ServerProfileId): SocialResult<ContactCard> = SocialResult.Failure("server_unavailable")
    override suspend fun updatePresence(profileId: ServerProfileId, operationId: String, settings: PresenceSettings): SocialResult<PresenceSettings> = SocialResult.Failure("server_unavailable")
    override suspend fun profileStatisticsSettings(profileId: ServerProfileId): SocialResult<ProfileStatisticsSettings> = SocialResult.Failure("server_unavailable")
    override suspend fun updateProfileStatisticsSettings(profileId: ServerProfileId, operationId: String, expectedRevision: Long, enabled: Boolean): SocialResult<ProfileStatisticsSettingsReceipt> = SocialResult.Failure("server_unavailable")
    override suspend fun friendProfileStatistics(profileId: ServerProfileId, friendAccountId: String): SocialResult<SharedProfileStatistics> = SocialResult.Failure("server_unavailable")
    override suspend fun heartbeat(profileId: ServerProfileId, operationId: String): SocialResult<Unit> = SocialResult.Success(Unit)
    override suspend fun createRoomInvitation(profileId: ServerProfileId, roomId: String, targetAccountId: String, operationId: String): SocialResult<RoomInvitationSummary> = SocialResult.Failure("server_unavailable")
    override suspend fun cancelRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): SocialResult<RoomInvitationSummary> = SocialResult.Failure("server_unavailable")
    override suspend fun acceptRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): SocialResult<AcceptedRoomInvitation> = SocialResult.Failure("server_unavailable")
}
