package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.recommendation.*
import app.autplay.application.desktop.account.AccountWaveListen
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


public fun selectRankedWaveTracks(ranked: List<WaveRecordingRow>, rows: Map<String, WaveRecordingRow>,
    listens: List<AccountWaveListen>, liked: Set<String>, confirmed: Set<String>, reduced: Set<String>,
    wave: Boolean, now: Long): List<String> {
    val originals = liked.mapNotNull(rows::get).filterNot { waveVersion(it).alternate }
    val unlocked = originals.flatMap { row ->
        val v = waveVersion(row)
        listOfNotNull(v.song, v.performance, row.recordingMbid?.let(::MusicBrainzRecording), v.performance?.work?.let(::MusicBrainzWork))
    }.toSet()
    val recent = listens.filter { it.playedMs > 0 && now - it.startedAtMs in 0 until 7 * 86_400_000L }
        .mapNotNull { rows[it.track.value] }
    val surfaces = if (wave) setOf("personal_wave") else setOf("home", "offline_pack")
    val frequent = listens.filter { it.surface in surfaces }.groupBy { rows[it.track.value]?.let(::waveVersion)?.kind }
        .filter { (kind, events) -> kind != null && events.size >= 5 &&
            events.count { it.playedMs < 30_000 || it.ratio?.let { ratio -> ratio <= 0.2 } == true }.toDouble() / events.size >= 0.6 }.keys
    val eligible = ranked.filter { row ->
        val v = waveVersion(row)
        val reduce = v.kind in frequent || row.catalogRecordingId in reduced
        val retain = if (!reduce) true else {
            val bytes = (row.catalogRecordingId ?: row.refId).encodeToByteArray()
            val bucket = java.security.MessageDigest.getInstance("SHA-256").digest(bytes)[0].toInt() and 255
            (bucket + now / 86_400_000L) % 3L == 0L
        }
        retain && (row.catalogRecordingId !in confirmed || row.performanceRole in setOf("COVER", "REMIX", "VERSION")) &&
            (!v.alternate || v.song in unlocked)
    }
    return selectSongs(eligible, recent, wave, originals)
}
