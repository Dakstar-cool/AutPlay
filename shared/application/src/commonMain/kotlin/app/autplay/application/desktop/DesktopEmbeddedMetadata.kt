package app.autplay.application.desktop

object DesktopEmbeddedMetadataLimits {
    const val MAX_METADATA_BYTES: Int = 8 * 1024 * 1024
    const val MAX_PARTS: Int = 1_024
    const val MAX_TEXT_UNITS: Int = 4_096
    const val MAX_IMAGE_BYTES: Int = 4 * 1024 * 1024
    const val MAX_IMAGE_DIMENSION: Int = 2_048
    const val MAX_IMAGE_PIXELS: Long = 4_194_304
    const val MAX_THUMBNAIL_DIMENSION: Int = 512
    const val MAX_THUMBNAIL_BYTES: Int = 1024 * 1024
    const val MAX_CACHE_ENTRIES: Int = 16
    const val MAX_CACHE_BYTES: Int = 16 * 1024 * 1024
}

/** Canonical bounded PNG only. Defensive copies keep published artwork immutable. */
class DesktopArtwork(val sha256: String, pngBytes: ByteArray, val width: Int, val height: Int) {
    private val encoded: ByteArray
    val byteSize: Int get() = encoded.size

    init {
        require(sha256.matches(Regex("[0-9a-f]{64}")))
        require(pngBytes.size in 1..DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES)
        require(width in 1..DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_DIMENSION)
        require(height in 1..DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_DIMENSION)
        encoded = pngBytes.copyOf()
    }

    fun copyPngBytes(): ByteArray = encoded.copyOf()
}

/** Display evidence only; format/status are not artist, album or decoder identities. */
data class DesktopEmbeddedMetadata(
    val status: String,
    val format: String = "UNKNOWN",
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    val artwork: DesktopArtwork? = null,
)

/** A user-approved label edit must still match all these stored fields inside its transaction. */
data class DesktopTrackTextBaseline(
    val trackId: String,
    val title: String,
    val artist: String,
    val sha256: String,
    val byteSize: Long,
)

/** Exact reader format only. Unknown display strings cannot authorize persisted source evidence. */
fun desktopEmbeddedTextFormat(format: String): DesktopEmbeddedTextFormat? = when (format) {
    "ID3V23" -> DesktopEmbeddedTextFormat.ID3V23
    "ID3V24" -> DesktopEmbeddedTextFormat.ID3V24
    "FLAC" -> DesktopEmbeddedTextFormat.FLAC
    "MP4" -> DesktopEmbeddedTextFormat.MP4
    else -> null
}

data class DesktopMetadataPreview(
    val id: String,
    val baseline: DesktopTrackTextBaseline,
    val embedded: DesktopEmbeddedMetadata,
    val proposedTitle: String,
    val proposedArtist: String,
    val provenance: DesktopTrackTextProvenance? = null,
) {
    /** Absent source fields stay null; fallback display text is never reclassified as embedded. */
    val application: DesktopEmbeddedTrackTextApplication? get() {
        val expected = provenance ?: return null
        val format = desktopEmbeddedTextFormat(embedded.format) ?: return null
        if (embedded.status != "AVAILABLE" || expected.baseline != baseline || expected.metadataRevision < 0 ||
            proposedTitle != (embedded.title ?: baseline.title) ||
            proposedArtist != (embedded.artist ?: baseline.artist)) return null
        return DesktopEmbeddedTrackTextApplication(expected, format, embedded.title, embedded.artist)
    }

    val canApply: Boolean get() = application != null &&
        validDesktopEmbeddedText(proposedTitle, allowBlank = false) &&
        validDesktopEmbeddedText(proposedArtist, allowBlank = true) &&
        (proposedTitle != baseline.title || proposedArtist != baseline.artist)

    val proposedTitleProvenance: DesktopTrackFieldProvenance? get() =
        proposedProvenance(baseline.title, proposedTitle, provenance?.title)
    val proposedArtistProvenance: DesktopTrackFieldProvenance? get() =
        proposedProvenance(baseline.artist, proposedArtist, provenance?.artist)

    private fun proposedProvenance(before: String, after: String,
        current: DesktopTrackFieldProvenance?): DesktopTrackFieldProvenance? {
        if (provenance?.baseline != baseline) return null
        // Matching source text is a no-op, not new evidence about the stored field.
        if (before == after) return current
        return application?.let { DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, it.format) }
    }
}

/** Host owns preview visibility, read/apply admission and the bounded process-local artwork cache. */
data class DesktopMetadataState(
    val requestId: String? = null,
    val trackId: String? = null,
    val busy: Boolean = false,
    val applying: Boolean = false,
    val preview: DesktopMetadataPreview? = null,
    val artworks: Map<String, DesktopArtwork> = emptyMap(),
    val errorCode: String? = null,
)

fun validDesktopEmbeddedText(value: String, allowBlank: Boolean): Boolean {
    if (value.length > DesktopEmbeddedMetadataLimits.MAX_TEXT_UNITS || (!allowBlank && value.isBlank())) return false
    var offset = 0
    while (offset < value.length) {
        val character = value[offset++]
        if (character == '\u0000') return false
        if (character.isHighSurrogate()) {
            if (offset >= value.length || !value[offset++].isLowSurrogate()) return false
        } else if (character.isLowSurrogate()) return false
    }
    return true
}
