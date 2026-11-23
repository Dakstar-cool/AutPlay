package app.autplay.application.recommendation

import app.autplay.data.local.dao.LibraryDao
import app.autplay.data.local.dao.WaveRecordingRow

private const val WEEK_MS = 7L * 24 * 60 * 60 * 1000
internal const val LIKED_ORIGINAL_VERSION = "LIKED_ORIGINAL_VERSION"
private val variationRoles = setOf("REMIX", "COVER", "VERSION")

internal suspend fun LibraryDao.selectSongRefs(profile: String, ids: List<String>, now: Long, wave: Boolean,
    confirmedVariationRecordings: Set<String> = emptySet(), lessFrequentRecordings: Set<String> = emptySet()): List<String> {
    if (ids.isEmpty()) return emptyList()
    val rows = waveRecordings(profile, ids).associateBy { it.refId }
    val versions = rows.mapValues { waveVersion(it.value) }
    val surface = if (wave) "personal_wave" else "home"
    val counts = if (versions.values.any { it.kind != null }) variationSkips(profile, now)
        .filter { it.surface == surface }.associateBy { it.kind } else emptyMap()
    val pending = versions.values.filter { it.alternate }.mapNotNull { it.song }.toMutableSet()
    val unlocked = mutableSetOf<WaveOrigin>()
    val likedSources = mutableListOf<WaveRecordingRow>()
    var after = ""
    while (pending.isNotEmpty()) {
        val page = likedWaveRecordings(profile, after, 200)
        for (row in page) {
            val liked = waveVersion(row)
            if (liked.alternate) continue
            for (key in listOfNotNull(liked.song, liked.performance, row.recordingMbid?.let(::MusicBrainzRecording),
                liked.performance?.work?.let(::MusicBrainzWork))) {
                if (pending.remove(key)) {
                    unlocked.add(key)
                    likedSources.add(row)
                }
            }
        }
        if (page.size < 200) break
        after = page.last().refId
    }
    val history = mutableListOf<WaveRecordingRow>()
    after = ""
    do {
        val page = recentlyPlayedWaveRecordings(profile, now - WEEK_MS, now, after, 200)
        history.addAll(page)
        if (page.size < 200) break
        after = page.last().refId
    } while (true)
    val eligible = ids.distinct().mapNotNull { id ->
        val version = versions[id] ?: return@mapNotNull null
        val row = rows.getValue(id)
        val reduce = counts[version.kind]?.frequent == true || row.catalogRecordingId in lessFrequentRecordings
        if (reduce && !retainedVariation(row.catalogRecordingId ?: id, now)) return@mapNotNull null
        // A server-confirmed relationship can arrive in a pack before metadata sync reaches this phone.
        if (row.catalogRecordingId in confirmedVariationRecordings && row.performanceRole !in variationRoles) return@mapNotNull null
        row.takeIf { !version.alternate || version.song in unlocked }
    }
    return selectSongs(eligible, history, wave, likedSources)
}
