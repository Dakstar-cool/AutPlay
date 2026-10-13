package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopTrack
import java.util.Locale

/** Small-collection fallback. Results are bounded; this is not an indexed or paged DB query. */
fun searchDesktopTracks(snapshot: DesktopLibrarySnapshot, query: String, limit: Int = 200): List<DesktopTrack> {
    if (query.length > 256 || limit !in 1..1_000) throw DesktopStorageException("desktop_search_invalid")
    val needle = query.trim().lowercase(Locale.ROOT)
    return snapshot.tracks.asSequence().filter { track ->
        needle.isEmpty() || track.title.lowercase(Locale.ROOT).contains(needle) ||
            track.artist.lowercase(Locale.ROOT).contains(needle)
    }.take(limit).toList()
}
