package app.autplay.playback

import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.source.ForwardingTimeline
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.WrappingMediaSource

internal data class PlaybackSourceTimeline(val durationMs: Long?, val seekable: Boolean)

/** Reports the actual source timeline, never the duration from file tags. */
@UnstableApi
internal class PlaybackSourceTimelineFactory(
    private val delegate: MediaSource.Factory,
    private val onTimeline: (MediaItem, PlaybackSourceTimeline) -> Unit,
) : MediaSource.Factory by delegate {
    override fun createMediaSource(mediaItem: MediaItem): MediaSource {
        return ReportingSource(delegate.createMediaSource(mediaItem), mediaItem, onTimeline)
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
        mediaSource.canUpdateMediaItem(mediaItem)

    override fun updateMediaItem(mediaItem: MediaItem) {
        item = mediaItem
        mediaSource.updateMediaItem(mediaItem)
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
        // A re-prepared source starts with a placeholder; retain its learned duration.
        info?.let { onTimeline(item, it) }
        refreshSourceInfo(describedTimeline(newTimeline))
    }

    private fun describedTimeline(timeline: Timeline): Timeline = object : ForwardingTimeline(timeline) {
        override fun getWindow(windowIndex: Int, window: Timeline.Window, defaultPositionProjectionUs: Long): Timeline.Window {
            super.getWindow(windowIndex, window, defaultPositionProjectionUs)
            val describedItem = item
            window.mediaItem = describedItem
            return window
        }
    }

}
