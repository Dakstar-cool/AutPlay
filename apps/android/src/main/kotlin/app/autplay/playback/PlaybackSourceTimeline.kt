package app.autplay.playback

import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.source.ClippingMediaSource
import androidx.media3.exoplayer.source.ForwardingTimeline
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.WrappingMediaSource

internal data class PlaybackSourceTimeline(val durationMs: Long?, val seekable: Boolean)

internal const val CUT_DEFAULT_POSITION_MS = "transition_default_position_ms"

/** Reports the actual source timeline before clipping, never the duration from file tags. */
@UnstableApi
internal class PlaybackSourceTimelineFactory(
    private val delegate: MediaSource.Factory,
    private val onTimeline: (MediaItem, PlaybackSourceTimeline) -> Unit,
) : MediaSource.Factory by delegate {
    override fun createMediaSource(mediaItem: MediaItem): MediaSource {
        val plainItem = mediaItem.buildUpon().setClippingConfiguration(MediaItem.ClippingConfiguration.UNSET).build()
        val reporting = ReportingSource(delegate.createMediaSource(plainItem), mediaItem, onTimeline)
        return if (mediaItem.clippingConfiguration == MediaItem.ClippingConfiguration.UNSET) reporting
        else ClippingMediaSource.Builder(reporting)
            .setStartPositionMs(mediaItem.clippingConfiguration.startPositionMs)
            .setEndPositionMs(mediaItem.clippingConfiguration.endPositionMs)
            .build()
    }
}

@UnstableApi
private class ReportingSource(
    source: MediaSource,
    @Volatile private var item: MediaItem,
    private val onTimeline: (MediaItem, PlaybackSourceTimeline) -> Unit,
) : WrappingMediaSource(source) {
    override fun getMediaItem(): MediaItem = item

    override fun getInitialTimeline(): Timeline? = mediaSource.initialTimeline?.let(::describedTimeline)

    override fun canUpdateMediaItem(mediaItem: MediaItem): Boolean =
        item.clippingConfiguration == mediaItem.clippingConfiguration &&
            mediaSource.canUpdateMediaItem(plain(mediaItem))

    override fun updateMediaItem(mediaItem: MediaItem) {
        item = mediaItem
        mediaSource.updateMediaItem(plain(mediaItem))
    }

    override fun onChildSourceInfoRefreshed(newTimeline: Timeline) {
        val window = Timeline.Window()
        val info = if (newTimeline.windowCount == 1 && newTimeline.periodCount == 1) {
            newTimeline.getWindow(0, window)
            if (window.isPlaceholder) null else PlaybackSourceTimeline(
                durationMs = window.durationMs.takeIf { it != C.TIME_UNSET && it > 0 && !window.isLive },
                seekable = window.isSeekable && !window.isLive,
            )
        } else PlaybackSourceTimeline(null, false)
        // Re-preparing a clipped source starts with a placeholder; retain its learned bounds.
        info?.let { onTimeline(item, it) }
        refreshSourceInfo(describedTimeline(newTimeline))
    }

    private fun describedTimeline(timeline: Timeline): Timeline = object : ForwardingTimeline(timeline) {
        override fun getWindow(windowIndex: Int, window: Timeline.Window, defaultPositionProjectionUs: Long): Timeline.Window {
            super.getWindow(windowIndex, window, defaultPositionProjectionUs)
            val describedItem = item
            window.mediaItem = describedItem
            val defaultMs = describedItem.mediaMetadata.extras?.getLong(CUT_DEFAULT_POSITION_MS, 0) ?: 0
            if (defaultMs > 0 && timeline.windowCount == 1 && timeline.periodCount == 1 &&
                window.isSeekable && !window.isLive && !window.isPlaceholder &&
                window.durationMs > defaultMs * 2) {
                // Explicit initial/restore seeks still use their exact source position.
                // Auto transitions (including repeat-one) use this prepared head boundary.
                window.defaultPositionUs = defaultMs * 1_000
            }
            return window
        }
    }

    private fun plain(mediaItem: MediaItem): MediaItem =
        mediaItem.buildUpon().setClippingConfiguration(MediaItem.ClippingConfiguration.UNSET).build()
}
