package app.autplay.desktop.presentation

import app.autplay.application.desktop.DesktopWaveRecommendation
import app.autplay.application.desktop.DesktopWaveTrack
import app.autplay.application.desktop.account.*
import app.autplay.data.local.dao.WaveRecordingRow
import app.autplay.ui.desktop.selectRankedWaveTracks
import kotlinx.serialization.json.*

internal suspend fun selectAccountWave(store: DesktopAccountStore, lease: AccountLease, ranked: List<DesktopWaveRecommendation>,
    preferred: Map<AccountUuid, AccountUuid>, offline: Boolean, confirmed: Set<String>, reduced: Set<String>,
    wave: Boolean): List<DesktopWaveTrack> {
    val domain = checkNotNull(store.domain)
    val catalog = checkNotNull(store.catalog)
    val media = checkNotNull(store.media)
    val now = System.currentTimeMillis()
    val liked = hashSetOf<String>()
    val blocked = hashSetOf<String>()
    var after: AccountUuid? = null
    do {
        val page = domain.readProjectionPage(lease, AccountProjectionKind.PREFERENCE, after, 100)
        for (row in page.filterNot { it.header.removed }) {
            val value = row.detail as? AccountLocalDomainDetail.Preference ?: continue
            if (value.rawPreference == "DISLIKED" || value.excludedFromTaste) blocked.add(value.trackId.value)
            else if (value.rawPreference == "LIKED") liked.add(value.trackId.value)
        }
        after = page.lastOrNull()?.header?.localId?.takeIf { page.size == 100 }
    } while (after != null)
    liked.removeAll(blocked)
    suspend fun eligible(id: AccountUuid): Boolean = id.value !in blocked && (!offline || media.readTrackCopies(lease, id, null, 1).isNotEmpty())
    val tracks = mutableListOf<DesktopWaveTrack>()
    for (item in ranked) {
        val recording = AccountUuid(item.recordingId)
        val first = preferred[recording] ?: continue
        var selected = first.takeIf { eligible(it) }
        var cursor: AccountUuid? = null
        while (selected == null) {
            val page = catalog.recordingRefs(lease, recording, cursor)
            selected = page.firstOrNull { it != first && eligible(it) }
            if (page.size < 100) break
            cursor = page.last()
        }
        if (selected != null) tracks.add(DesktopWaveTrack(selected.value, item))
    }
    if (tracks.isEmpty()) return emptyList()
    val history = catalog.waveListens(lease, now - 30L * 86_400_000L, now)
    val rows = mutableMapOf<String, WaveRecordingRow>()
    for (id in (tracks.map { it.trackId } + liked + history.map { it.track.value }).distinct()) {
        val key = AccountUuid(id)
        val record = domain.readExact(lease, AccountProjectionKind.TRACK, key) ?: continue
        val data = record.detail as? AccountLocalDomainDetail.Track ?: continue
        val fields = catalog.readMetadata(lease, key)?.fields
        fun text(field: String) = (fields?.get(field) as? JsonPrimitive)?.contentOrNull
        rows[id] = WaveRecordingRow(id, data.title, data.artist, text("recording_kind"), text("version_text"),
            text("recording_mbid"), text("work_mbid"), text("performance_role"), text("original_recording_mbid"),
            data.recordingId?.value, text("live_performance") == "true")
    }
    val byId = tracks.associateBy { it.trackId }
    return selectRankedWaveTracks(tracks.mapNotNull { rows[it.trackId] }, rows, history, liked, confirmed, reduced, wave, now)
        .mapNotNull(byId::get)
}
