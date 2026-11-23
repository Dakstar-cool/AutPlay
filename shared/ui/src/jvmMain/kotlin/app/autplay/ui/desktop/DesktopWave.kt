package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.recommendation.selectSongs
import app.autplay.application.recommendation.waveVersion
import app.autplay.data.local.dao.WaveRecordingRow

public fun selectDesktopWaveTracks(library: DesktopLibrarySnapshot): List<String> {
    val active = library.tracks.filter { it.collectionState == "ACTIVE" && it.preference != "DISLIKED" }
    val rows = active.associate { it.id to WaveRecordingRow(it.id, it.title, it.artist, null, null) }
    val originals = active.filter { it.preference == "LIKED" }.mapNotNull { rows[it.id] }
        .filterNot { waveVersion(it).alternate }
    val unlocked = originals.mapNotNull { waveVersion(it).song }.toSet()
    val recent = library.history.filter { it.finalized && it.startedAtMs >= System.currentTimeMillis() - 7L * 86_400_000 &&
        it.listenTasteState == "INCLUDED" && it.sessionTasteState == "INCLUDED" }.mapNotNull { rows[it.trackId] }
    val candidates = rows.values.filter { row ->
        val version = waveVersion(row)
        !version.alternate || version.song in unlocked
    }.shuffled()
    return selectSongs(candidates, recent, wave = true, sources = originals).take(200)
}

internal actual fun desktopWaveTracks(library: DesktopLibrarySnapshot): List<String> = selectDesktopWaveTracks(library)
