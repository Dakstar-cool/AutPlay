package app.autplay.application.desktop

/** Display evidence only; never a recording, artist, source grant or authenticated identity. */
enum class DesktopTrackTextOrigin { UNKNOWN, USER, EMBEDDED }

enum class DesktopEmbeddedTextFormat { ID3V23, ID3V24, FLAC, MP4 }

data class DesktopTrackFieldProvenance(
    val origin: DesktopTrackTextOrigin = DesktopTrackTextOrigin.UNKNOWN,
    val format: DesktopEmbeddedTextFormat? = null,
) {
    init { require((origin == DesktopTrackTextOrigin.EMBEDDED) == (format != null)) }
}

/** One transactional read of exact labels, byte identity and their local evidence.
 * metadataRevision counts metadata facts, including legacy and native-duration facts.
 * UNKNOWN includes legacy, unsupported, ambiguous or over-budget history; reads never repair it.
 */
data class DesktopTrackTextProvenance(
    val baseline: DesktopTrackTextBaseline,
    val metadataRevision: Long,
    val title: DesktopTrackFieldProvenance = DesktopTrackFieldProvenance(),
    val artist: DesktopTrackFieldProvenance = DesktopTrackFieldProvenance(),
)

/** Freeze this with the preview. Null means retain the field, including its provenance.
 * Non-null is the exact selected embedded source value, including an explicitly empty artist.
 * The caller supplies a known format only after its bounded reader established that format.
 * Exact no-ops retain prior semantics and create no provenance or metadata facts.
 */
data class DesktopEmbeddedTrackTextApplication(
    val expected: DesktopTrackTextProvenance,
    val format: DesktopEmbeddedTextFormat,
    val title: String? = null,
    val artist: String? = null,
)
