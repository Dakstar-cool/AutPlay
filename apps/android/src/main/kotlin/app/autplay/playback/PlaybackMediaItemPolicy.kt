package app.autplay.playback

import androidx.media3.common.MediaItem

private val NON_PLAYABLE_SCHEMES = setOf("autplay-unavailable", "autplay-unresolved")

/** Internal queue markers are presentation state, never an audio source for Media3. */
internal fun MediaItem?.isResolvedPlaybackSource(): Boolean {
    val item = this ?: return false
    return isResolvedPlaybackSource(
        scheme = item.localConfiguration?.uri?.scheme,
        unavailableReason = item.mediaMetadata.extras?.getString("unavailable_reason"),
    )
}

internal fun isResolvedPlaybackSource(scheme: String?, unavailableReason: String?): Boolean =
    unavailableReason == null && scheme != null && scheme !in NON_PLAYABLE_SCHEMES
