package app.autplay.application.recommendation

import app.autplay.data.local.dao.LibraryDao
import app.autplay.data.local.dao.WaveRecordingRow
import java.text.Normalizer
import java.util.Locale

private val bracketSuffix = Regex("""\s*[\[(]([^()\[\]]+)[\])]$""")
private val dashSuffix = Regex("""\s+[-–—]\s+([^-–—]+)$""")
private val variantLabel = Regex(
    """(?:(?:.+\s+)?(?:remix|cover|ремикс|кавер)|(?:cover|кавер)\s+.+|originally by\s+.+|""" +
        """live(?:\s+(?:at|in|from|version)\b.*)?|(?:acoustic|instrumental|karaoke|demo|slowed|sped up|""" +
        """концертная|акустическая|инструментал|караоке|демо|аранжировка|реаранжировка|arrangement|rearrangement)(?: version| версия)?|radio edit)""",
    RegexOption.IGNORE_CASE,
)
private val coverArtist = Regex("""(?:originally by|кавер на)\s+(.+)|(.+?)\s+(?:cover|кавер)""", RegexOption.IGNORE_CASE)
private val coverLabel = Regex("""\b(?:cover|кавер)\b|^originally by\b""", RegexOption.IGNORE_CASE)
private val alternateKinds = setOf("LIVE", "REMIX", "EDIT", "DEMO", "COVER", "INSTRUMENTAL", "ACOUSTIC", "KARAOKE")
private val originalLabel = Regex(
    """(?:uncensored|explicit|без цензуры)(?: version)?|original (?:version|mix)|album version|(?:оригинальная|альбомная) версия""",
    RegexOption.IGNORE_CASE,
)
private val cleanLabel = Regex("""(?:clean|censored)(?: edit| version)?|с цензурой|цензурная версия""", RegexOption.IGNORE_CASE)
private val presentationLabel = Regex("""official (?:audio|video|music video)|lyrics?|hd|4k""", RegexOption.IGNORE_CASE)

internal sealed interface WaveOrigin
internal data class WaveSongKey(val artist: String, val title: String) : WaveOrigin
internal data class WorkRecording(val work: String, val recording: String) : WaveOrigin
internal data class MusicBrainzRecording(val id: String) : WaveOrigin
internal data class MusicBrainzWork(val id: String) : WaveOrigin
private data class CatalogRecording(val id: String) : WaveOrigin
private data class TrackRef(val id: String) : WaveOrigin
internal data class WaveVersion(val alternate: Boolean, val song: WaveOrigin?, val performance: WorkRecording? = null,
    val kind: String? = null)

private fun normalized(text: String): String = Normalizer.normalize(text, Normalizer.Form.NFKC)
    .trim().lowercase(Locale.ROOT)

/** Confirmed covers use the work and recording IDs even when the displayed credits change. */
internal fun waveVersion(row: WaveRecordingRow): WaveVersion {
    if (row.performanceRole == "REMIX") {
        return WaveVersion(true, row.originalRecordingMbid?.takeUnless { it == row.recordingMbid }?.let(::MusicBrainzRecording), kind = "REMIX")
    }
    if (row.performanceRole == "COVER") {
        val origin = if (row.workMbid != null && row.originalRecordingMbid != null &&
            row.originalRecordingMbid != row.recordingMbid) WorkRecording(row.workMbid, row.originalRecordingMbid) else null
        return WaveVersion(true, origin, kind = "COVER")
    }
    var base = row.title.orEmpty().trim()
    val labels = mutableListOf<String>()
    row.versionText?.trim()?.let(labels::add)
    while (base.isNotEmpty()) {
        val suffix = bracketSuffix.find(base) ?: dashSuffix.find(base) ?: break
        val label = suffix.groupValues[1].trim()
        if (!variantLabel.matches(label) && !cleanLabel.matches(label) &&
            !originalLabel.matches(label) && !presentationLabel.matches(label)) break
        if (suffix.range.first == 0) break
        labels.add(label)
        base = base.take(suffix.range.first).trimEnd()
    }
    val alternate = row.recordingKind in alternateKinds || labels.any { variantLabel.matches(it) || cleanLabel.matches(it) }
    val cover = row.recordingKind == "COVER" || labels.any(coverLabel::containsMatchIn)
    val kind = when {
        row.performanceRole == "VERSION" && row.livePerformance -> "LIVE"
        row.recordingKind in variationKinds -> row.recordingKind
        labels.any { variantLabel.matches(it) && coverLabel.containsMatchIn(it) } -> "COVER"
        labels.any { variantLabel.matches(it) && (it.endsWith("remix", true) || it.endsWith("ремикс", true)) } -> "REMIX"
        labels.any { variantLabel.matches(it) && (it.startsWith("live", true) || it.startsWith("концертная", true)) } -> "LIVE"
        else -> null
    }
    if (row.performanceRole == "VERSION") return WaveVersion(true, row.workMbid?.let(::MusicBrainzWork), kind = kind)
    val performer = if (cover) labels.firstNotNullOfOrNull { value ->
        coverArtist.matchEntire(value)?.groupValues?.drop(1)?.firstOrNull { it.isNotBlank() }
    } else row.artist
    val key = if (performer.isNullOrBlank() || base.isBlank()) null
        else WaveSongKey(normalized(performer), normalized(base))
    val performance = if (!alternate && row.performanceRole == "PERFORMANCE" &&
        row.workMbid != null && row.recordingMbid != null) WorkRecording(row.workMbid, row.recordingMbid) else null
    return WaveVersion(alternate, key, performance, kind)
}

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

/** Match known works and source recordings across titles. Ambiguous text never joins two known works. */
internal fun selectSongs(ranked: List<WaveRecordingRow>, history: List<WaveRecordingRow>, wave: Boolean,
    sources: List<WaveRecordingRow> = emptyList()): List<String> {
    val rows = (ranked + history + sources).distinctBy { it.refId }
    val versions = rows.associate { it.refId to waveVersion(it) }
    val worksByTitle = rows.filter { it.performanceRole != null && it.workMbid != null }
        .groupBy { versions.getValue(it.refId).song }.mapValues { (_, same) -> same.mapNotNull { it.workMbid }.distinct() }
    fun keys(row: WaveRecordingRow): Set<WaveOrigin> = buildSet {
        add(TrackRef(row.refId))
        row.catalogRecordingId?.let { add(CatalogRecording(it)) }
        row.recordingMbid?.let { add(MusicBrainzRecording(it)) }
        if (row.performanceRole != null) {
            row.workMbid?.let { add(MusicBrainzWork(it)) }
            row.originalRecordingMbid?.let { add(MusicBrainzRecording(it)) }
        }
        val song = versions.getValue(row.refId).song
        if (song is WaveSongKey && worksByTitle[song].orEmpty().size <= 1) add(song)
    }
    // A source recording may supply the only bridge between a retitled remix and a live performance.
    val parents = mutableMapOf<WaveOrigin, WaveOrigin>()
    fun root(key: WaveOrigin): WaveOrigin {
        var current = key
        while (true) {
            val parent = parents[current] ?: break
            current = parent
        }
        if (current != key) parents[key] = current
        return current
    }
    for (row in rows) {
        val first = root(TrackRef(row.refId))
        for (key in keys(row)) {
            val other = root(key)
            if (other != first) parents[other] = first
        }
    }
    val seen = history.mapTo(mutableSetOf()) { root(TrackRef(it.refId)) }
    val selected = mutableListOf<String>()
    val deferred = ArrayDeque<Pair<String, WaveOrigin>>()
    var ordinarySinceVariant = 0
    fun addPendingVariant() {
        while (ordinarySinceVariant >= 19 && deferred.isNotEmpty()) {
            val (id, family) = deferred.removeFirst()
            if (seen.add(family)) {
                selected.add(id)
                ordinarySinceVariant = 0
            }
        }
    }
    for (row in ranked) {
        val family = root(TrackRef(row.refId))
        if (family in seen) continue
        if (wave && versions.getValue(row.refId).alternate) {
            deferred.addLast(row.refId to family)
        } else {
            selected.add(row.refId)
            seen.add(family)
            ordinarySinceVariant++
        }
        addPendingVariant()
    }
    return selected
}
