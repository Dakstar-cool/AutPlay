package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopTextSearchMatches
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.desktopTextSearchExpression
import kotlinx.coroutines.CancellationException

/** Only a visible query can allocate or query an index; route-local drafts remain retained. */
internal fun desktopActiveSearchQuery(section: DesktopSection, playlists: Boolean, query: String): String =
    if (section == (if (playlists) DesktopSection.Playlists else DesktopSection.Library)) query else ""

/** Compose keys compare by equality; a port's custom equals must not reuse a retired lifetime. */
internal class DesktopSearchOwnerKey(private val port: DesktopTextSearchPort?) {
    override fun equals(other: Any?): Boolean = other is DesktopSearchOwnerKey && port === other.port
    override fun hashCode(): Int = 0
}

/** No fallback: a missing, failed or malformed port yields a visible search failure only. */
internal suspend fun loadDesktopSearchMatches(
    port: DesktopTextSearchPort?,
    snapshot: DesktopLibrarySnapshot,
    query: String,
    checkpoint: () -> Unit = {},
): DesktopTextSearchMatches {
    fun failure(code: String): DesktopTextSearchMatches = DesktopTextSearchMatches(query, true, errorCode = code)
    checkpoint()
    if (query.length > 256) return failure("desktop_search_invalid")
    if (query.isBlank()) return DesktopTextSearchMatches(query, false)
    if (desktopTextSearchExpression(query) == null) return DesktopTextSearchMatches(query, true)
    if (port == null) return failure("desktop_search_unavailable")
    return try {
        val result = port.search(snapshot, query)
        checkpoint()
        if (result.query != query || !result.searched) return failure("desktop_search_failed")
        result.errorCode?.let { return failure(desktopSearchErrorCode(it)) }
        val trackIds = HashSet<String>()
        val playlistIds = HashSet<String>()
        snapshot.tracks.forEachIndexed { index, track ->
            if (index % 256 == 0) checkpoint()
            trackIds.add(track.id)
        }
        snapshot.playlists.forEachIndexed { index, playlist ->
            if (index % 256 == 0) checkpoint()
            playlistIds.add(playlist.id)
        }
        fun valid(ids: List<String>, known: Set<String>): Boolean {
            val seen = HashSet<String>()
            ids.forEachIndexed { index, id ->
                if (index % 256 == 0) checkpoint()
                if (id !in known || !seen.add(id)) return false
            }
            return true
        }
        if (!valid(result.trackIds, trackIds) || !valid(result.playlistIds, playlistIds)) failure("desktop_search_failed")
        else result
    } catch (failure: CancellationException) {
        throw failure
    } catch (_: Exception) {
        failure("desktop_search_failed")
    }
}

/** Construct outside composable lambda memoization: even equal replacement ports retire callbacks. */
internal fun desktopSearchOwnerGuard(
    captured: DesktopTextSearchPort?,
    latest: () -> DesktopTextSearchPort?,
    isOwnerCurrent: () -> Boolean,
): () -> Boolean = { captured === latest() && isOwnerCurrent() }

internal fun desktopSearchErrorCode(code: String): String = when (code) {
    "desktop_search_closed", "desktop_search_invalid", "desktop_search_unavailable" -> code
    else -> "desktop_search_failed"
}

/** Restrict the complete set before the existing filters/sort/pages and all-or-nothing admission. */
internal fun desktopSearchTracks(
    tracks: List<DesktopTrack>,
    result: DesktopTextSearchMatches,
    checkpoint: () -> Unit = {},
): List<DesktopTrack> {
    if (result.errorCode != null) return emptyList()
    if (!result.searched) return tracks
    val ids = desktopSearchIds(result.trackIds, checkpoint)
    val matches = ArrayList<DesktopTrack>()
    tracks.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        if (track.id in ids) matches.add(track)
    }
    checkpoint()
    return matches
}

/** Blank browsing explicitly supplies all choices, so even a hidden draft cannot use substring policy. */
internal fun desktopSearchPlaylistIds(
    playlists: List<DesktopPlaylist>,
    result: DesktopTextSearchMatches,
    checkpoint: () -> Unit = {},
): Set<String> {
    if (result.errorCode != null) return emptySet()
    if (result.searched) return desktopSearchIds(result.playlistIds, checkpoint)
    val ids = HashSet<String>()
    playlists.forEachIndexed { index, playlist ->
        if (index % 256 == 0) checkpoint()
        ids.add(playlist.id)
    }
    checkpoint()
    return ids
}

internal fun desktopSearchIds(ids: List<String>, checkpoint: () -> Unit = {}): Set<String> {
    val result = HashSet<String>()
    ids.forEachIndexed { index, id ->
        if (index % 256 == 0) checkpoint()
        result.add(id)
    }
    checkpoint()
    return result
}
