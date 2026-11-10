package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopTrack

internal const val DESKTOP_TRACK_TEXT_LIMIT: Int = 4096

/** Storage-compatible UTF-16 validation; accepted text is never trimmed or normalized. */
internal fun validDesktopTrackText(value: String, title: Boolean): Boolean {
    if (value.length > DESKTOP_TRACK_TEXT_LIMIT || (title && value.isBlank())) return false
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

/** Reference identity invalidates old form closures after edits, cancellation or reopening. */
internal class DesktopTrackTextDraft(
    val trackId: String,
    val baselineTitle: String,
    val baselineArtist: String,
    val title: String = baselineTitle,
    val artist: String = baselineArtist,
) {
    fun matches(track: DesktopTrack?): Boolean = track != null && track.id == trackId &&
        track.title == baselineTitle && track.artist == baselineArtist

    fun canSubmit(track: DesktopTrack?): Boolean = matches(track) &&
        (title != baselineTitle || artist != baselineArtist) &&
        validDesktopTrackText(title, title = true) && validDesktopTrackText(artist, title = false)
}
