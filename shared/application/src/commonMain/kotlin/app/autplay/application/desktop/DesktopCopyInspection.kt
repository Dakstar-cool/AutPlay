package app.autplay.application.desktop

object DesktopCopyInspectionLimits {
    const val MAX_REQUEST_TRACKS: Int = 1_000
    const val MAX_CACHED_RESULTS: Int = 1_000
}

/** Attribute-only observation of a managed copy, never a capability or decoder/hash proof. */
data class DesktopCopyInspection(
    val trackId: String,
    val sha256: String,
    val byteSize: Long,
    /** PRESENT or UNAVAILABLE. Unknown future values remain display-only unknown evidence. */
    val status: String,
)

/** Process-local last observations. Rendering and playback must never trigger a scan. */
data class DesktopCopyInspectionState(
    val requestId: String? = null,
    val busy: Boolean = false,
    val requested: Int = 0,
    val completed: Int = 0,
    val results: Map<String, DesktopCopyInspection> = emptyMap(),
    val errorCode: String? = null,
)

fun DesktopCopyInspection.matches(track: DesktopTrack): Boolean =
    trackId == track.id && sha256 == track.sha256 && byteSize == track.byteSize
