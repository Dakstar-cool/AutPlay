package app.autplay.ui.player

import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.ui.face.FacePlaybackMode
import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackFaceMappingTest {
    @Test
    fun playbackFactsMapToHonestFaceModes() {
        assertEquals(FacePlaybackMode.Buffering, facePlaybackMode(PlaybackStatus.Buffering, false))
        assertEquals(FacePlaybackMode.Playing, facePlaybackMode(PlaybackStatus.Ready, true))
        assertEquals(FacePlaybackMode.Paused, facePlaybackMode(PlaybackStatus.Ready, false))
        assertEquals(FacePlaybackMode.Idle, facePlaybackMode(PlaybackStatus.Idle, false))
        assertEquals(FacePlaybackMode.Idle, facePlaybackMode(PlaybackStatus.Ended, false))
        assertEquals(FacePlaybackMode.Idle, facePlaybackMode(PlaybackStatus.Ended, true))
    }
}
