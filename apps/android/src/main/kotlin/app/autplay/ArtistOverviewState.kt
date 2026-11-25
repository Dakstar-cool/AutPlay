package app.autplay

import android.content.Context
import android.util.LruCache
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import app.autplay.application.server.InternetMetadataDiscoveryCodec
import app.autplay.application.server.InternetMetadataDiscoveryPage
import app.autplay.application.server.catalogueUuid
import app.autplay.application.server.ServerFeatureHttpException
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.CatalogPage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

data class ArtistOverview(
    val baselineId: String,
    val tracks: InternetMetadataDiscoveryPage,
    val releases: InternetMetadataDiscoveryPage,
    val latest: InternetMetadataDiscoveryPage,
    val latestComplete: Boolean,
    val refreshing: Boolean,
    val libraryArtistId: String? = null,
)

internal fun decodeArtistOverview(artist: String, json: JsonObject, saved: ArtistOverview? = null): ArtistOverview {
    require(json["contract_version"]?.jsonPrimitive?.content == "artist-overview-v1")
    require(json["artist_mbid"]?.jsonPrimitive?.content == artist)
    val baseline = catalogueUuid(json.getValue("baseline_id").jsonPrimitive.content)
    val previous = saved?.takeIf { it.baselineId == baseline }
    fun page(field: String, entity: String): InternetMetadataDiscoveryPage =
        InternetMetadataDiscoveryCodec.decode(json.getValue(field).jsonObject).also {
            require(it.offset == 0 && it.limit == 25 && it.items.all { row -> row.entityType == entity })
        }
    val tracks = if ("tracks" in json) page("tracks", "recording") else requireNotNull(previous).tracks
    val releases = if ("releases" in json) page("releases", "release") else requireNotNull(previous).releases
    return ArtistOverview(baseline, tracks, releases, page("latest", "release"),
        json.getValue("latest_complete").jsonPrimitive.boolean, json.getValue("refreshing").jsonPrimitive.boolean,
        json["library_artist_id"]?.jsonPrimitive?.contentOrNull?.let(::catalogueUuid))
}

internal data class ArtistOverviewState(val page: ArtistOverview?, val loading: Boolean)

@Composable
internal fun rememberArtistOverview(context: Context, binding: ClientEventBinding?, artist: String?): ArtistOverviewState {
    val recent = remember(binding) { LruCache<String, Pair<Long, ArtistOverview>>(8) }
    var unsupportedAt by remember(binding) { mutableStateOf<Long?>(null) }
    val saved = remember(recent, artist) { artist?.let(recent::get) }
    var state by remember(recent, artist) {
        mutableStateOf(ArtistOverviewState(saved?.second, saved == null && binding != null && artist != null))
    }
    LaunchedEffect(binding, artist) {
        if (binding == null || artist == null) return@LaunchedEffect
        val profile = binding.serverProfileId.value
        val key = "${binding.userId.value}:ARTIST-OVERVIEW-1:$artist"
        val dao = AutPlayRuntime.database(context).catalogProjectionDao()
        try {
            val cached = saved ?: dao.catalogPage(profile, key)?.let { row ->
                withContext(Dispatchers.Default) {
                    runCatching { row.observedAtMs to decodeArtistOverview(artist, Json.parseToJsonElement(row.document).jsonObject) }.getOrNull()
                }
            }
            fun publish(time: Long, page: ArtistOverview) {
                recent.put(artist, time to page)
                state = ArtistOverviewState(page, false)
            }
            var retryDelay = 15_000L
            if (cached != null) {
                publish(cached.first, cached.second)
                val age = System.currentTimeMillis() - cached.first
                if (!cached.second.refreshing && age in 0 until 3_600_000L) return@LaunchedEffect
                if (cached.second.refreshing && age in 0 until retryDelay) delay(retryDelay - age)
            }
            if (unsupportedAt?.let { System.currentTimeMillis() - it in 0 until 3_600_000L } == true) {
                state = ArtistOverviewState(state.page, false)
                return@LaunchedEffect
            }
            val server = AutPlayRuntime.serverFeatures(context, binding)
            while (true) {
                val previous = state.page
                val (page, document) = withContext(Dispatchers.Default) {
                    val json = server.artistOverview(artist, previous?.baselineId)
                    val decoded = decodeArtistOverview(artist, json, previous)
                    val complete = JsonObject(json + mapOf("tracks" to decoded.tracks.rawPayload, "releases" to decoded.releases.rawPayload))
                    decoded to complete.toString()
                }
                val now = System.currentTimeMillis()
                publish(now, page)
                dao.putCatalogPage(CatalogPage(profile, key, document, now))
                dao.trimCatalogPages(profile)
                if (!page.refreshing) break
                delay(retryDelay)
                retryDelay = (retryDelay * 2).coerceAtMost(300_000L)
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failure: Exception) {
            if (failure is ServerFeatureHttpException && failure.status == 404 && failure.errorCode == null) {
                unsupportedAt = System.currentTimeMillis()
            }
            // Servers predating the overview endpoint still expose both paginated sections.
            state = ArtistOverviewState(state.page, false)
        }
    }
    return state
}
