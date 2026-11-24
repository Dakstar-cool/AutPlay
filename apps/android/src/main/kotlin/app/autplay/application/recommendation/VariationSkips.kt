package app.autplay.application.recommendation

import app.autplay.data.local.dao.LibraryDao
import java.security.MessageDigest

internal const val SKIP_WINDOW_MS = 30L * 86_400_000L

internal fun currentSkipHints(snapshotMs: Long, now: Long) = now - snapshotMs in 0L until 86_400_000L

internal fun variationSurface(origin: String, surface: String?, personalFallback: Boolean): String? = when {
    origin == "WAVE" && personalFallback -> "personal_wave"
    origin != "RECOMMENDED" -> null
    surface == "personal_wave" -> surface
    surface in setOf("home", "offline_pack") -> "home"
    else -> null
}

data class VariationSkips(val surface: String, val kind: String, val plays: Long, val skips: Long) {
    init {
        require(surface in setOf("home", "personal_wave") && kind in variationKinds && skips in 0..plays)
    }
    val frequent: Boolean get() = plays >= 5 && skips.toDouble() / plays >= 0.6
}

internal suspend fun LibraryDao.variationSkips(profile: String, through: Long): List<VariationSkips> {
    val since = through - SKIP_WINDOW_MS
    val counts = mutableMapOf<Pair<String, String>, VariationSkips>()
    var after = ""
    do {
        val page = variationHistoryRefs(profile, since, through, after)
        if (page.isEmpty()) break
        val byRef = page.associate { it.refId to waveVersion(it).kind }
        for (row in variationOutcomes(profile, since, through, byRef.keys.toList())) {
            val surface = row.surface?.takeIf { it == "home" || it == "personal_wave" } ?: continue
            val kind = (row.kind ?: byRef[row.refId])?.takeIf { it in variationKinds } ?: continue
            val key = surface to kind
            val previous = counts[key]
            counts[key] = VariationSkips(surface, kind, row.plays + (previous?.plays ?: 0), row.skips + (previous?.skips ?: 0))
        }
        after = page.last().refId
    } while (page.size == 200)
    return counts.values.toList()
}

/** Stable within a UTC day. Every recording remains eligible one day out of three. */
internal fun retainedVariation(id: String, now: Long): Boolean {
    val bucket = MessageDigest.getInstance("SHA-256").digest(id.toByteArray(Charsets.UTF_8))[0].toInt() and 255
    return (bucket + now / 86_400_000L) % 3L == 0L
}
