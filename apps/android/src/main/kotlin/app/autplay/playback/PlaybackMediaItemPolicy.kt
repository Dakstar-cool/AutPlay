package app.autplay.playback

import androidx.media3.common.MediaItem

private val NON_PLAYABLE_SCHEMES = setOf("autplay-unavailable", "autplay-unresolved")

/** Internal queue markers are presentation state, never an audio source for Media3. */
internal fun MediaItem?.isResolvedPlaybackSource(): Boolean {
    val item = this ?: return false
    return isResolvedPlaybackSource(
        scheme = item.localConfiguration?.uri?.scheme,
        unavailableReason = item.mediaMetadata.extras?.getString("unavailable_reason"),
        selectedSource = item.mediaMetadata.extras?.getString("selected_source"),
    )
}

// A MediaController can receive metadata without the service's LocalConfiguration.
internal fun isResolvedPlaybackSource(scheme: String?, unavailableReason: String?, selectedSource: String? = null): Boolean =
    unavailableReason == null && scheme !in NON_PLAYABLE_SCHEMES &&
        (scheme != null || SelectedAudioSource.entries.any { it.name == selectedSource })
