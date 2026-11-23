package app.autplay

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import app.autplay.application.server.InternetMetadataDiscoveryCard
import app.autplay.application.server.InternetMetadataDiscoveryCodec
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.CatalogPage
import kotlinx.coroutines.CancellationException

internal data class ArtistCatalogPages(
    val items: List<InternetMetadataDiscoveryCard> = emptyList(),
    val incomplete: Boolean = false,
    val retry: () -> Unit = {},
    val loading: Boolean = false,
)

@Composable
internal fun rememberArtistCatalogPages(
    context: Context,
    binding: ClientEventBinding?,
    artistMbid: String?,
    editions: Boolean,
): ArtistCatalogPages {
    var snapshot by remember(binding, artistMbid, editions) { mutableStateOf(ArtistCatalogPages(loading = binding != null && artistMbid != null)) }
    var attempt by remember(binding, artistMbid, editions) { mutableStateOf(0) }
    LaunchedEffect(binding, artistMbid, editions, attempt) {
        if (binding == null || artistMbid == null) return@LaunchedEffect
        val profile = binding.serverProfileId.value
        val dao = AutPlayRuntime.database(context).catalogProjectionDao()
        val client = AutPlayRuntime.serverFeatures(context, binding)
        val results = linkedMapOf<String, InternetMetadataDiscoveryCard>()
        var offset = 0
        while (true) {
            val key = "${binding.userId.value}:ARTIST:$artistMbid:$editions:$offset"
            val saved = dao.catalogPage(profile, key)
            val cached = saved?.let { runCatching { InternetMetadataDiscoveryCodec.decode(it.document) }.getOrNull() }
            val page = if (cached != null && System.currentTimeMillis() - saved.observedAtMs in 0 until 3_600_000L) {
                cached
            } else {
                try {
                    val response = if (editions) client.discoveryArtistReleases(artistMbid, offset = offset)
                        else client.discoveryArtistTracks(artistMbid, offset = offset)
                    response.also {
                        check(it.offset == offset && it.limit == 25)
                        dao.putCatalogPage(CatalogPage(profile, key, it.rawPayload.toString(), System.currentTimeMillis()))
                        dao.trimCatalogPages(profile)
                    }
                } catch (error: CancellationException) {
                    throw error
                } catch (_: Exception) {
                    cached ?: run {
                        snapshot = ArtistCatalogPages(results.values.toList(), incomplete = true, retry = { attempt++ })
                        return@LaunchedEffect
                    }
                }
            }
            page.items.forEach { results.putIfAbsent(it.id, it) }
            val next = page.nextOffset
            if (next == null) {
                snapshot = ArtistCatalogPages(results.values.toList(), page.truncated, retry = { attempt++ })
                break
            }
            if (editions) snapshot = ArtistCatalogPages(results.values.toList())
            offset = next
        }
    }
    return snapshot
}
