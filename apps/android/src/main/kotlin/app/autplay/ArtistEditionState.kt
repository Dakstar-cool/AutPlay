package app.autplay

import android.content.Context
import android.os.SystemClock
import android.util.LruCache
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import app.autplay.application.server.InternetMetadataDiscoveryCard
import app.autplay.application.server.InternetMetadataDiscoveryCodec
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.CatalogPage
import app.autplay.application.server.ServerFeatureRepository
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

internal data class ArtistCatalogPages(
    val items: List<InternetMetadataDiscoveryCard> = emptyList(),
    val incomplete: Boolean = false,
    val retry: () -> Unit = {},
    val loading: Boolean = false,
    val hasMore: Boolean = false,
)

private data class ArtistCatalogProgress(
    val items: List<InternetMetadataDiscoveryCard>,
    val nextOffset: Int?,
    val savedAt: Long,
    val truncated: Boolean,
)

internal fun artistCatalogCacheKey(user: String, artist: String, editions: Boolean, offset: Int): String =
    "$user:ARTIST-CREDITS-2:$artist:$editions:$offset"

@Composable
internal fun rememberArtistCatalogPages(
    context: Context,
    binding: ClientEventBinding?,
    artistMbid: String?,
    editions: Boolean,
    firstPageOnly: Boolean = false,
): ArtistCatalogPages {
    val recent = remember(binding, editions) { LruCache<String, ArtistCatalogProgress>(8) }
    val previous = remember(recent, artistMbid, firstPageOnly) { artistMbid?.let(recent::get) }
    var snapshot by remember(recent, artistMbid, firstPageOnly) {
        mutableStateOf(ArtistCatalogPages(previous?.items.orEmpty(),
            loading = binding != null && artistMbid != null && (previous == null || (!firstPageOnly && previous.nextOffset != null)),
            incomplete = previous?.truncated == true, hasMore = previous?.nextOffset != null))
    }
    var attempt by remember(binding, artistMbid, editions, firstPageOnly) { mutableIntStateOf(0) }
    LaunchedEffect(binding, artistMbid, editions, firstPageOnly, attempt) {
        if (binding == null || artistMbid == null) return@LaunchedEffect
        val resume = recent.get(artistMbid)?.takeIf {
            attempt == 0 && SystemClock.elapsedRealtime() - it.savedAt < 60_000L && !it.truncated
        }
        if (resume != null && (firstPageOnly || resume.nextOffset == null)) return@LaunchedEffect

        val profile = binding.serverProfileId.value
        val dao = AutPlayRuntime.database(context).catalogProjectionDao()
        var client: ServerFeatureRepository? = null
        val results = resume?.items.orEmpty().associateByTo(linkedMapOf()) { it.id }
        var offset = resume?.nextOffset ?: 0
        var offlineFallback = false
        fun display(items: List<InternetMetadataDiscoveryCard>, loading: Boolean, incomplete: Boolean = false,
            hasMore: Boolean = snapshot.hasMore) {
            val visible = if ((loading || incomplete || hasMore) && snapshot.items.size > items.size) snapshot.items else items
            snapshot = ArtistCatalogPages(visible, incomplete = incomplete, loading = loading, hasMore = hasMore, retry = { attempt++ })
        }
        try {
            while (true) {
                val key = artistCatalogCacheKey(binding.userId.value, artistMbid, editions, offset)
                val saved = dao.catalogPage(profile, key)
                val cached = saved?.let { row -> withContext(Dispatchers.Default) {
                    runCatching { InternetMetadataDiscoveryCodec.decode(row.document) }.getOrNull()
                } }?.takeIf { it.offset == offset && it.limit == 25 }
                val fresh = saved != null && System.currentTimeMillis() - saved.observedAtMs in 0 until 3_600_000L
                val page = if (offlineFallback) {
                    cached ?: break
                } else if (cached != null && fresh && !(attempt > 0 && cached.truncated)) {
                    cached
                } else {
                    if (cached != null) {
                        val fallback = (results.values + cached.items).distinctBy { it.id }
                        display(fallback, loading = true)
                    }
                    try {
                        val server = client ?: AutPlayRuntime.serverFeatures(context, binding).also { client = it }
                        val response = if (editions) server.discoveryArtistReleases(artistMbid, offset = offset)
                            else server.discoveryArtistTracks(artistMbid, offset = offset)
                        check(response.offset == offset && response.limit == 25)
                        dao.putCatalogPage(CatalogPage(profile, key, response.rawPayload.toString(), System.currentTimeMillis()))
                        dao.trimCatalogPages(profile)
                        response
                    } catch (cancelled: CancellationException) {
                        throw cancelled
                    } catch (failure: Exception) {
                        offlineFallback = true
                        cached ?: throw failure
                    }
                }
                page.items.forEach { results.putIfAbsent(it.id, it) }
                val next = page.nextOffset
                val items = results.values.toList()
                val more = next != null && !firstPageOnly
                if (!offlineFallback && (next == null || items.size >= snapshot.items.size)) {
                    recent.put(artistMbid, ArtistCatalogProgress(items, next, SystemClock.elapsedRealtime(), page.truncated))
                }
                display(items, loading = more, incomplete = page.truncated || offlineFallback, hasMore = next != null)
                if (next == null || firstPageOnly) break
                offset = next
            }
            if (offlineFallback) display(snapshot.items, loading = false, incomplete = true)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            display(snapshot.items, loading = false, incomplete = true)
        }
    }
    return snapshot
}
