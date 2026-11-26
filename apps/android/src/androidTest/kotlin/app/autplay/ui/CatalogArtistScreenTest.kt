package app.autplay.ui

import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.artist.*
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.application.server.InternetMetadataDiscoveryCodec
import kotlinx.serialization.json.*
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class CatalogArtistScreenTest {
    @get:Rule val compose = createComposeRule()

    @Test fun keygenChurchLightAt320dp() = verify("KEYGEN CHURCH", AutPlayThemeMode.Light)
    @Test fun jointArtistDarkAt320dp() = verify("SVRGN", AutPlayThemeMode.Dark)
    @Test fun keygenChurchDarkAt320dp() = verify("KEYGEN CHURCH", AutPlayThemeMode.Dark)
    @Test fun jointArtistLightAt320dp() = verify("RAGETRAIN", AutPlayThemeMode.Light)

    @Test fun popularTrackCreditsFitOneLineAndOpenTheTappedArtist() {
        val profile = ServerProfileId(UUID.randomUUID().toString())
        fun performer(name: String) = ArtistSummary(ArtistKey(profile, ArtistId(UUID.randomUUID().toString())),
            name, null, null, null, null, null)
        val apashe = performer("Apashe")
        val panther = performer("Panther")
        val recording = ArtistAppearance(null, "RECORDING", null, "Battle Royale (VIP)",
            ArtistLocalTarget.Track("track"), performers = listOf(apashe, panther))
        var opened: DetailTarget? = null
        compose.setContent {
            AutPlayTheme {
                Box(Modifier.width(320.dp)) {
                    CoreProductDetailScreen(CoreProductDetailUiState(DetailTarget(DetailKind.Artist, apashe.key.artistId.value),
                        artist = ArtistDetail(apashe, emptyList()), artistAppearances = listOf(recording)), onOpenDetail = { opened = it })
                }
            }
        }
        compose.onNodeWithTag("collection-details").performScrollToNode(hasTestTag("artist-track-preview"))
        val credit = compose.onNodeWithText("Apashe, Panther", useUnmergedTree = true)
        credit.assertIsDisplayed()
        credit.performTouchInput { click(percentOffset(.8f, .5f)) }
        compose.runOnIdle { assertEquals(DetailTarget(DetailKind.Artist, panther.key.artistId.value), opened) }
    }

    @Test fun libraryAndPublicPagesShareTheFirstTwelveAndPreserveGuestCredits() {
        val artist = UUID.randomUUID().toString()
        val localId = UUID.randomUUID().toString()
        val profile = ServerProfileId(UUID.randomUUID().toString())
        val summary = ArtistSummary(ArtistKey(profile, ArtistId(artist)), "Guest", null, null, null, null, null)
        fun mbid(i: Int) = UUID(0, i.toLong()).toString()
        fun page(indices: List<Int>, offset: Int) = InternetMetadataDiscoveryCodec.decode(buildJsonObject {
            put("contract_version", "music-discovery-v1"); put("source", "MusicBrainz")
            put("source_scope", "INTERNET"); put("availability", "METADATA_ONLY"); put("acquisition_allowed", false)
            put("capabilities", buildJsonObject { put("direct_acquisition", false); put("track_source_search", true) })
            put("limit", 25); put("offset", offset); put("total_count", 33); put("truncated", false)
            put("next_offset", if (offset == 0) JsonPrimitive(25) else JsonNull)
            put("items", buildJsonArray {
                indices.forEach { i -> add(buildJsonObject {
                    put("id", "musicbrainz:recording:${mbid(i)}"); put("entity_type", "recording")
                    put("entity_id", mbid(i)); put("recording_id", mbid(i))
                    put("title", if (i > 16) "Same title" else "Song $i"); put("artist", "Main & Guest")
                    put("guest_artist_ids", buildJsonArray { add(artist) })
                    put("source", "MusicBrainz"); put("availability", "METADATA_ONLY"); put("acquisition_allowed", false)
                    put("can_browse_tracks", false); put("download_search_query", "Main Song $i")
                }) }
            })
        }).items
        val cards = page((1..25).toList(), 0) + page((25..32).toList(), 25)
        val local = ArtistAppearance(null, "RECORDING", ServerId(localId), "Local title",
            ArtistLocalTarget.Track(localId))
        val state = CoreProductDetailUiState(DetailTarget(DetailKind.Artist, artist),
            artist = ArtistDetail(summary, emptyList()), artistMbid = artist, artistAppearances = listOf(local),
            publicRecordings = cards, libraryRecordings = mapOf(mbid(16) to localId),
            publicCounts = (1..16).associate { mbid(it) to it.toLong() })
        val expected = listOf(localId) + (15 downTo 1).map(::mbid) + (17..32).map(::mbid)
        assertEquals(expected, artistRecordings(state).map { it.displayKey })
        assertEquals(32, artistRecordings(state).count { it.guest })
        var target by mutableStateOf(state.target)
        compose.setContent { AutPlayTheme { CoreProductDetailScreen(state.copy(target = target), onOpenDetail = { target = it }) } }
        val details = compose.onNodeWithTag("collection-details")
        details.performScrollToNode(hasTestTag("artist-track-preview"))
        val preview = compose.onNodeWithTag("artist-track-preview")
        expected.take(12).chunked(3).forEachIndexed { col, keys ->
            preview.performScrollToIndex(col)
            val positions = keys.map { id ->
                compose.onNodeWithTag("artist-track-$id").fetchSemanticsNode().positionInRoot.y
            }
            assertEquals(positions.sorted(), positions)
            assertEquals(3, positions.distinct().size)
        }
        val header = InstrumentationRegistry.getInstrumentation().targetContext.getString(R.string.catalog_popular_tracks)
        details.performScrollToNode(hasText(header))
        compose.onNodeWithText(header).performClick()
        expected.take(12).forEachIndexed { index, id ->
            details.performScrollToIndex(index + 1)
            compose.onNodeWithTag("artist-track-$id").assertIsDisplayed()
        }
        details.performScrollToNode(hasTestTag("artist-tracks-more"))
        compose.onNodeWithTag("artist-tracks-more").performClick()
        details.performScrollToNode(hasTestTag("artist-track-${expected.last()}"))
        compose.onNodeWithTag("artist-track-${expected.last()}").assertIsDisplayed()
    }

    private fun verify(name: String, mode: AutPlayThemeMode) {
        val artist = UUID.randomUUID().toString()
        val key = ArtistKey(ServerProfileId(UUID.randomUUID().toString()), ArtistId(artist))
        val summary = ArtistSummary(key, name, name, "PERSON", null, null, "ACTIVE")
        val tracks = (1..16).map { index ->
            val id = UUID.randomUUID().toString()
            ArtistAppearance(null, "RECORDING", ServerId(id), if (index == 16) "BASS PESADÃO" else "Track $index",
                ArtistLocalTarget.Track(id), guest = index == 3)
        }
        val heading = InstrumentationRegistry.getInstrumentation().targetContext.getString(R.string.catalog_popular_tracks)
        var target by mutableStateOf(DetailTarget(DetailKind.Artist, artist))
        var played: String? = null
        compose.setContent {
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, 1.5f)) {
                AutPlayTheme(AutPlayAppearance(mode = mode)) {
                    Box(Modifier.width(320.dp)) {
                        CoreProductDetailScreen(CoreProductDetailUiState(target, artist = ArtistDetail(summary, emptyList()),
                            artistAppearances = tracks, ranking = tracks.mapIndexed { i, track -> track.displayKey to i.toLong() }.toMap()),
                            onOpenDetail = { target = it }, onPlayTrack = { played = it })
                    }
                }
            }
        }
        compose.onNode(hasText(name) and isHeading()).assertIsDisplayed()
        compose.onNodeWithTag("collection-details").performScrollToNode(hasText(heading))
        compose.onNodeWithText(heading).performClick()
        val top = tracks.last()
        compose.onNodeWithTag("artist-track-${top.displayKey}").performClick()
        compose.runOnIdle { assertEquals(top.displayKey, played); assertEquals(DetailKind.ArtistTracks, target.kind) }
        compose.onNodeWithTag("collection-details").performScrollToNode(hasTestTag("artist-track-${tracks.first().displayKey}"))
        compose.onNodeWithTag("artist-track-${tracks.first().displayKey}").assertIsDisplayed()
        compose.runOnIdle { target = DetailTarget(DetailKind.Artist, artist) }
        compose.onNodeWithText(heading).assertExists()
    }
}
