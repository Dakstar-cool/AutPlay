package app.autplay.playback

import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.TransferListener
import androidx.media3.exoplayer.source.BaseMediaSource
import androidx.media3.exoplayer.source.MediaPeriod
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.upstream.Allocator

/** Pending queue entries must never be opened as HTTP while Media3 buffers the current item. */
@UnstableApi
internal class ResolvingMediaSourceFactory(private val delegate: MediaSource.Factory) : MediaSource.Factory by delegate {
    override fun createMediaSource(mediaItem: MediaItem): MediaSource =
        if (mediaItem.localConfiguration?.uri?.scheme in setOf("autplay-unresolved", "autplay-unavailable")) {
            PendingMediaSource(mediaItem)
        } else delegate.createMediaSource(mediaItem)
}

/** Media3's masking source waits for timeline publication until the service replaces this item. */
@UnstableApi
private class PendingMediaSource(private val item: MediaItem) : BaseMediaSource() {
    override fun getMediaItem(): MediaItem = item
    override fun prepareSourceInternal(mediaTransferListener: TransferListener?) = Unit
    override fun maybeThrowSourceInfoRefreshError() = Unit
    override fun releaseSourceInternal() = Unit
    override fun releasePeriod(mediaPeriod: MediaPeriod) = Unit
    override fun createPeriod(id: MediaSource.MediaPeriodId, allocator: Allocator, startPositionUs: Long): MediaPeriod =
        error("UNRESOLVED_SOURCE_HAS_NO_PERIOD")
}
