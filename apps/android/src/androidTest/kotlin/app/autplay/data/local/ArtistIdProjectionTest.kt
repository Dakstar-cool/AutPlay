package app.autplay.data.local

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.application.artist.ArtistId
import app.autplay.application.artist.ArtistKey
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.artist.RoomArtistCatalogPort
import app.autplay.data.local.entity.ArtistCreditNameProjectionEntity
import app.autplay.data.local.entity.ArtistCreditProjectionEntity
import app.autplay.data.local.entity.ArtistProjectionEntity
import app.autplay.data.local.entity.CatalogArtistCreditLinkEntity
import app.autplay.data.local.entity.CatalogArtistCreditLinkOwnerEntity
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.ReleaseProjectionEntity
import app.autplay.data.local.entity.ReleaseTrackProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ArtistIdProjectionTest {
    @Test fun distinctNamesOrderedUnknownMembersEmptyAndLinksRemainProfileScoped() = runBlocking {
        val db = AutPlayDatabase.open(ApplicationProvider.getApplicationContext(), "artist-projection-${System.nanoTime()}.db")
        try {
            val dao = db.catalogProjectionDao()
            dao.upsertArtists(listOf(
                ArtistProjectionEntity("a1", "p1", "artist-1", "Same", null, "UNKNOWN_TYPE", null, null, "UNKNOWN_STATUS", 2, 2),
                ArtistProjectionEntity("a2", "p1", "artist-2", "Same", null, null, null, null, null, 1, 1),
            ))
            dao.upsertArtistCredits(listOf(ArtistCreditProjectionEntity("c1", "p1", "credit-1", "Same feat. Guest", 2, 2)))
            dao.upsertArtistCreditNames(listOf(
                ArtistCreditNameProjectionEntity("n2", "p1", "credit-1", "artist-2", 2, "Guest", "", "FUTURE_ROLE"),
                ArtistCreditNameProjectionEntity("n1", "p1", "credit-1", "artist-1", 1, "Same", " feat. ", "PRIMARY"),
            ))
            dao.upsertArtistCredits(listOf(ArtistCreditProjectionEntity("empty", "p1", "credit-empty", "Legacy", 1, 1)))
            val scope = "a".repeat(64)
            dao.upsertArtistCreditLinks(listOf(
                CatalogArtistCreditLinkEntity("l1", "p1", "RECORDING", "recording-1", "credit-1", scope, 1, 0, true, 2, 0, 2),
                CatalogArtistCreditLinkEntity("l2", "p1", "RELEASE", "release-1", "credit-1", scope, 1, 0, true, 2, 0, 2),
                CatalogArtistCreditLinkEntity("l3", "p2", "RECORDING", "recording-1", "credit-other", scope, 1, 0, true, 1, 0, 1),
            ))
            assertEquals(listOf(1, 2), dao.namesForCredit("p1", "credit-1", 10).map { it.position })
            assertEquals("FUTURE_ROLE", dao.namesForCredit("p1", "credit-1", 10)[1].role)
            assertEquals(0, dao.namesForCredit("p1", "credit-empty", 10).size)
            assertEquals("credit-1", dao.artistCreditLink("p1", "RECORDING", "recording-1")?.serverArtistCreditId)
            assertEquals("credit-1", dao.artistCreditLink("p1", "RELEASE", "release-1")?.serverArtistCreditId)
            assertNull(dao.artistCredit("p2", "credit-1"))
        } finally { db.close() }
    }

    @Test
    fun typedPortKeepsSameNamesDistinctAndBuildsOnlyOwnerVisibleLocalTargets() = runBlocking {
        val db = AutPlayDatabase.open(ApplicationProvider.getApplicationContext(), "artist-port-${System.nanoTime()}.db")
        try {
            val catalog = db.catalogProjectionDao()
            catalog.upsertRecordings(
                listOf(
                    recording("local-recording-1", RECORDING_ONE, "Owned recording"),
                    recording("local-recording-2", RECORDING_TWO, "Second recording"),
                    recording("local-recording-private", RECORDING_PRIVATE, "Private recording"),
                ),
            )
            catalog.upsertReleases(
                listOf(
                    ReleaseProjectionEntity(
                        "local-release-1",
                        RELEASE_ONE,
                        null,
                        "Owned release",
                        "Same",
                        "2026",
                        "ALBUM",
                        null,
                        1,
                        1,
                        false,
                    ),
                    ReleaseProjectionEntity(
                        "local-release-hidden",
                        RELEASE_HIDDEN,
                        null,
                        "Not owned release",
                        "Same",
                        "2026",
                        "ALBUM",
                        null,
                        1,
                        1,
                        false,
                    ),
                ),
            )
            catalog.upsertReleaseTracks(
                listOf(
                    ReleaseTrackProjectionEntity(
                        "local-release-track-1",
                        null,
                        "local-release-1",
                        "local-recording-1",
                        1,
                        1,
                        "1",
                        "Owned recording",
                        "Same",
                        180_000,
                    ),
                ),
            )
            db.libraryDao().upsertTrackRefs(
                listOf(
                    track("local-track-1", "local-recording-1", RECORDING_ONE, PROFILE_ONE),
                    track("local-track-1-new", "local-recording-1", RECORDING_ONE, PROFILE_ONE, updatedAt = 2),
                    track("local-track-2", "local-recording-2", RECORDING_TWO, PROFILE_ONE),
                    track("local-track-private", "local-recording-private", RECORDING_PRIVATE, PROFILE_TWO),
                ),
            )
            catalog.upsertArtists(
                listOf(
                    artist("local-artist-1", PROFILE_ONE, ARTIST_ONE, "Same", "First"),
                    artist("local-artist-2", PROFILE_ONE, ARTIST_TWO, "Same", "Second"),
                    artist("local-artist-private", PROFILE_TWO, ARTIST_PRIVATE, "Private", null),
                ),
            )
            catalog.upsertArtistCredits(
                listOf(
                    credit("local-credit-1", PROFILE_ONE, CREDIT_ONE, "Same"),
                    credit("local-credit-2", PROFILE_ONE, CREDIT_TWO, "Same"),
                    credit("local-credit-private", PROFILE_TWO, CREDIT_PRIVATE, "Private"),
                ),
            )
            catalog.upsertArtistCreditNames(
                listOf(
                    name("local-name-1", PROFILE_ONE, CREDIT_ONE, ARTIST_ONE, "Same"),
                    name("local-name-2", PROFILE_ONE, CREDIT_TWO, ARTIST_TWO, "Same"),
                    name("local-name-2-member", PROFILE_ONE, CREDIT_TWO, ARTIST_ONE, "Same", position = 2),
                    name("local-name-private", PROFILE_TWO, CREDIT_PRIVATE, ARTIST_PRIVATE, "Private"),
                ),
            )
            catalog.upsertArtistCreditLinks(
                listOf(
                    link("link-recording-1", PROFILE_ONE, "RECORDING", RECORDING_ONE, CREDIT_ONE, SCOPE_ONE),
                    link("link-release-1", PROFILE_ONE, "RELEASE", RELEASE_ONE, CREDIT_ONE, SCOPE_TWO),
                    link("link-release-hidden", PROFILE_ONE, "RELEASE", RELEASE_HIDDEN, CREDIT_ONE, SCOPE_HIDDEN),
                    link("link-recording-2", PROFILE_ONE, "RECORDING", RECORDING_TWO, CREDIT_TWO, SCOPE_THREE),
                    link("link-private", PROFILE_TWO, "RECORDING", RECORDING_PRIVATE, CREDIT_PRIVATE, SCOPE_PRIVATE),
                ),
            )
            catalog.upsertArtistCreditLinkOwners(
                listOf(
                    owner("owner-recording-1", PROFILE_ONE, "RECORDING", RECORDING_ONE, SCOPE_ONE, RECORDING_ONE),
                    owner("owner-release-1", PROFILE_ONE, "RELEASE", RELEASE_ONE, SCOPE_TWO, RECORDING_ONE),
                    owner("owner-release-hidden", PROFILE_ONE, "RELEASE", RELEASE_HIDDEN, SCOPE_HIDDEN, RECORDING_ONE),
                    owner("owner-recording-2", PROFILE_ONE, "RECORDING", RECORDING_TWO, SCOPE_THREE, RECORDING_TWO),
                    owner("owner-private", PROFILE_TWO, "RECORDING", RECORDING_PRIVATE, SCOPE_PRIVATE, RECORDING_PRIVATE),
                ),
            )

            val port = RoomArtistCatalogPort(db)
            val profileOne = ServerProfileId(PROFILE_ONE)
            val browse = port.observeBrowse(profileOne, 10).first()
            assertEquals(listOf(ARTIST_ONE, ARTIST_TWO), browse.map { it.key.artistId.value })
            assertEquals(listOf("Same", "Same"), browse.map { it.name })
            assertNull(port.detail(ArtistKey(ServerProfileId(PROFILE_TWO), ArtistId(ARTIST_ONE)), 10, 10))

            val appearances = port.appearances(ArtistKey(profileOne, ArtistId(ARTIST_ONE)), 10)
            assertEquals(
                setOf(
                    ArtistLocalTarget.Track("local-track-1-new"),
                    ArtistLocalTarget.Track("local-track-2"),
                    ArtistLocalTarget.Release("local-release-1"),
                ),
                appearances.mapNotNull { it.localTarget }.toSet(),
            )
            assertNull(appearances.single { it.subjectId.value == RELEASE_HIDDEN }.localTarget)
            assertEquals(
                CREDIT_ONE,
                port.subjectCredits(profileOne, "RECORDING", ServerId(RECORDING_ONE), 10).single().creditId.value,
            )
            assertTrue(
                port.subjectCredits(profileOne, "RECORDING", ServerId(RECORDING_PRIVATE), 10).isEmpty(),
            )
        } finally {
            db.close()
        }
    }

    private fun recording(localId: String, serverId: String, title: String) = RecordingProjectionEntity(
        localId, serverId, null, title, title.lowercase(), "Same", "same", "{}", 180_000,
        "SONG", null, 0, null, 1, 1,
    )

    private fun track(
        localId: String,
        localRecordingId: String,
        serverRecordingId: String,
        profileId: String,
        updatedAt: Long = 1,
    ) =
        UserTrackRefEntity(
            localId, null, localRecordingId, serverRecordingId, "RESOLVED", "Owned recording", "Same",
            null, 180_000, 1.0, "SYNCED", 1, 0, 1, updatedAt, null, profileId,
        )

    private fun artist(localId: String, profileId: String, serverId: String, name: String, disambiguation: String?) =
        ArtistProjectionEntity(localId, profileId, serverId, name, null, null, disambiguation, null, null, 1, 1)

    private fun credit(localId: String, profileId: String, serverId: String, displayName: String) =
        ArtistCreditProjectionEntity(localId, profileId, serverId, displayName, 1, 1)

    private fun name(
        localId: String,
        profileId: String,
        creditId: String,
        artistId: String,
        creditedName: String,
        position: Int = 1,
    ) = ArtistCreditNameProjectionEntity(
        localId, profileId, creditId, artistId, position, creditedName, "", "PRIMARY",
    )

    private fun link(
        localId: String,
        profileId: String,
        subjectType: String,
        subjectId: String,
        creditId: String,
        scopeId: String,
    ) = CatalogArtistCreditLinkEntity(
        localId, profileId, subjectType, subjectId, creditId, scopeId, 1, 0, true, 1, 0, 1,
    )

    private fun owner(
        localId: String,
        profileId: String,
        subjectType: String,
        subjectId: String,
        scopeId: String,
        ownerRecordingId: String,
    ) = CatalogArtistCreditLinkOwnerEntity(
        localId, profileId, subjectType, subjectId, scopeId, ownerRecordingId,
    )

    private companion object {
        const val PROFILE_ONE = "10000000-0000-4000-8000-000000000001"
        const val PROFILE_TWO = "10000000-0000-4000-8000-000000000002"
        const val ARTIST_ONE = "20000000-0000-4000-8000-000000000001"
        const val ARTIST_TWO = "20000000-0000-4000-8000-000000000002"
        const val ARTIST_PRIVATE = "20000000-0000-4000-8000-000000000003"
        const val CREDIT_ONE = "30000000-0000-4000-8000-000000000001"
        const val CREDIT_TWO = "30000000-0000-4000-8000-000000000002"
        const val CREDIT_PRIVATE = "30000000-0000-4000-8000-000000000003"
        const val RECORDING_ONE = "40000000-0000-4000-8000-000000000001"
        const val RECORDING_TWO = "40000000-0000-4000-8000-000000000002"
        const val RECORDING_PRIVATE = "40000000-0000-4000-8000-000000000003"
        const val RELEASE_ONE = "50000000-0000-4000-8000-000000000001"
        const val RELEASE_HIDDEN = "50000000-0000-4000-8000-000000000002"
        const val SCOPE_ONE = "1111111111111111111111111111111111111111111111111111111111111111"
        const val SCOPE_TWO = "2222222222222222222222222222222222222222222222222222222222222222"
        const val SCOPE_THREE = "3333333333333333333333333333333333333333333333333333333333333333"
        const val SCOPE_PRIVATE = "4444444444444444444444444444444444444444444444444444444444444444"
        const val SCOPE_HIDDEN = "5555555555555555555555555555555555555555555555555555555555555555"
    }
}
